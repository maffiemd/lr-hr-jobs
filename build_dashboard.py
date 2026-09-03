#!/usr/bin/env python3
"""
Regenerates index.html from the "Active Jobs" tab of the tracking sheet.

This is the GitHub Actions copy of the local build_dashboard.py -- it runs
inside the lr-hr-jobs repo itself (see .github/workflows/refresh.yml), reading
dashboard_template.html and writing index.html, both relative to this file,
since the Actions runner only has this repo checked out.

Usage: python3 build_dashboard.py
"""

import csv
import io
import json
import os
import sys
import urllib.request
from datetime import date, datetime

DIR = os.path.dirname(os.path.abspath(__file__))
SHEET_ID = "1BAfOqeVES2_yehz__bAKgacBxXdgM7Ba7Z2NKOHNeDs"
ACTIVE_JOBS_GID = "368372525"
IR_PROGRAMS_GID = "187135351"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={ACTIVE_JOBS_GID}"
IR_PROGRAMS_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={IR_PROGRAMS_GID}"
TEMPLATE_PATH = f"{DIR}/dashboard_template.html"
OUTPUT_PATH = f"{DIR}/index.html"
CARNEGIE_PATH = f"{DIR}/carnegie-classification.json"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

# Render-time-only fallback (added 2026-09-02) for a row whose Teaching load
# cell is genuinely blank in the Sheet -- never written back into the Sheet
# itself, unlike teaching-load-lookup.json's institution-specific figures.
# See carnegie-classification.json's docstring for why these three buckets
# and not a finer scale. Kept in sync with the local build_dashboard.py by
# hand -- this copy runs standalone inside the lr-hr-jobs repo (see module
# docstring), so it can't just import the local one.
CARNEGIE_BUCKET_LOAD = {"R1": "2-2", "R2": "2-3", "Teaching": "3-3"}

DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"]


def fetch_csv(url=CSV_URL):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


def build_institution_signals(ir_programs_csv_text):
    """Institution -> [signal tags] lookup, sourced from the IR-programs
    sheet's own Signals column (added 2026-07-14) rather than a hand-
    maintained dict, so a school only needs to be tagged once regardless of
    how many future postings it has. Only tags that are true of the whole
    institution belong here (e.g. "ER-Friendly B-School", "Inclusive
    Journal List") -- posting-specific tags like "IR/ER in Ad" stay on the
    Active Jobs row itself, see build_job()."""
    reader = csv.DictReader(io.StringIO(ir_programs_csv_text))
    out = {}
    for row in reader:
        institution = (row.get("Institution") or "").strip()
        signals_raw = (row.get("Signals") or "").strip()
        if not institution or not signals_raw:
            continue
        tags = [s.strip() for s in signals_raw.split(";") if s.strip()]
        out.setdefault(institution, [])
        for t in tags:
            if t not in out[institution]:
                out[institution].append(t)
    return out


def lookup_institution_signals(university, institution_signals):
    """Exact match first (the common case). Falls back to a prefix check
    (either name starting with the other, case-insensitive) since the
    Active Jobs "University" column and the IR-programs "Institution"
    column aren't guaranteed to use identical strings -- e.g. "Boise State"
    vs "Boise State University". Small, curated datasets on both sides, so
    this stays safe rather than needing full fuzzy matching."""
    if university in institution_signals:
        return institution_signals[university]
    u_norm = university.strip().lower()
    for inst, tags in institution_signals.items():
        i_norm = inst.strip().lower()
        if u_norm.startswith(i_norm) or i_norm.startswith(u_norm):
            return tags
    return []


def load_carnegie_classifications():
    with open(CARNEGIE_PATH, encoding="utf-8") as f:
        return json.load(f).get("entries", {})


def lookup_carnegie_bucket(university, classifications):
    """Same exact-match-then-prefix-match approach as
    lookup_institution_signals() -- small curated dataset, no need for full
    fuzzy matching."""
    if university in classifications:
        return classifications[university]
    u_norm = university.strip().lower()
    for inst, bucket in classifications.items():
        i_norm = inst.strip().lower()
        if u_norm.startswith(i_norm) or i_norm.startswith(u_norm):
            return bucket
    return None


def parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def format_rank(raw):
    raw = (raw or "").strip()
    if not raw:
        return "Open rank"
    if raw.lower() == "open":
        return "Open rank"
    if raw.lower() in ("assistant", "associate", "full") and "professor" not in raw.lower():
        return f"{raw} Professor"
    return raw


def build_job(row, today, institution_signals, carnegie_classifications):
    due = parse_date(row.get("Due Date"))
    posted = parse_date(row.get("Post Date"))
    expired_flag = (row.get("Expired?") or "").strip().lower() == "yes"
    if expired_flag:
        return None
    if due is not None and due < today:
        return None  # past its stated deadline -- don't show a stale posting

    area = [a.strip() for a in (row.get("Area") or "").split("/") if a.strip()]
    link = (row.get("Link") or "").strip()
    university = (row.get("University") or "").strip()
    if not university or not link:
        return None  # incomplete row, skip rather than show a broken card

    # Institution-wide tags (from the IR-programs sheet) plus this row's own
    # posting-specific tags (from Active Jobs' own Signals column) -- see
    # build_institution_signals()'s docstring for why these two stay separate.
    own_signals = [s.strip() for s in (row.get("Signals") or "").split(";") if s.strip()]
    signals = list(lookup_institution_signals(university, institution_signals))
    for s in own_signals:
        if s not in signals:
            signals.append(s)

    # Teaching load: use the Sheet's own value if it states one (ad-stated,
    # or a previously-composed '~institution-lookup' figure -- both count as
    # "the Sheet has an answer"). Only when the cell is genuinely blank do we
    # fall back to a live, render-time Carnegie-bucket inference -- this
    # never gets written back into the Sheet, so the Sheet itself stays a
    # faithful record of what was actually stated. See carnegie-
    # classification.json's docstring.
    #
    # PostDoc rows are an exception (added 2026-09-02, at the user's request):
    # a Carnegie-bucket figure describes a department's standing faculty
    # teaching load, not a postdoc's -- most postdocs teach nothing or teach
    # occasionally, so bucketing them by R1/R2/Teaching like a TT line is
    # actively misleading. Only trust a real number for a postdoc when the
    # Sheet's own cell states one directly (that's the ad itself giving a
    # clear signal the postdoc must teach); a genuinely blank cell means
    # "N/A", not an inferred bucket.
    is_postdoc = (row.get("TT-NTT-PostDoc") or "").strip().lower() == "postdoc"
    teaching = (row.get("Teaching load") or "").strip()
    teaching_inferred = False
    if not teaching:
        if is_postdoc:
            teaching = "N/A"
        else:
            bucket = lookup_carnegie_bucket(university, carnegie_classifications)
            if bucket:
                teaching = CARNEGIE_BUCKET_LOAD[bucket]
                teaching_inferred = True

    # Collapse any "N/A (...)" variant -- including ones typed directly into
    # the Sheet's own Teaching load cell -- down to a bare "N/A". A
    # parenthetical explanation is useful context but blows out the
    # dashboard's Teaching load column width; the reasoning belongs in the
    # Sheet cell for the record, not in the rendered card. Kept in sync with
    # the local build_dashboard.py by hand -- see this file's module docstring.
    if teaching.strip().lower().startswith("n/a"):
        teaching = "N/A"

    return {
        "university": university,
        "rank": format_rank(row.get("Rank")),
        "tt": (row.get("TT-NTT-PostDoc") or "").strip() or "TT",
        "area": area,
        "location": (row.get("Location") or "").strip(),
        "region": (row.get("Region") or "").strip(),
        "salary": (row.get("Salary") or "").strip(),
        "teaching": teaching,
        "teachingInferred": teaching_inferred,
        "posted": posted.isoformat() if posted else "",
        "due": due.isoformat() if due else "",
        "link": link,
        "signals": signals,
    }


def main():
    today = date.today()
    try:
        csv_text = fetch_csv()
    except Exception as e:
        print(f"ERROR fetching sheet CSV: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        ir_programs_csv_text = fetch_csv(IR_PROGRAMS_CSV_URL)
        institution_signals = build_institution_signals(ir_programs_csv_text)
    except Exception as e:
        # Non-fatal: dashboard still builds, just without institution-level
        # signal tags for this run (posting-specific ones from Active Jobs'
        # own Signals column still work).
        print(f"ERROR fetching IR-programs CSV: {e}", file=sys.stderr)
        institution_signals = {}

    try:
        carnegie_classifications = load_carnegie_classifications()
    except Exception as e:
        # Non-fatal: dashboard still builds, just without the inferred-load
        # fallback for this run (rows with a blank Teaching load stay blank
        # instead of getting a bucketed estimate).
        print(f"ERROR loading carnegie-classification.json: {e}", file=sys.stderr)
        carnegie_classifications = {}

    reader = csv.DictReader(io.StringIO(csv_text))
    jobs = []
    for row in reader:
        job = build_job(row, today, institution_signals, carnegie_classifications)
        if job:
            jobs.append(job)

    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()

    # Boards-monitored count is a hand-maintained literal here (unlike the
    # local build_dashboard.py, which imports run.py directly to compute
    # this dynamically -- see that file's count_boards_monitored()) --
    # this copy runs standalone inside the lr-hr-jobs repo (see module
    # docstring), which never has run.py checked out at all, so there's
    # nothing to import. Update by hand when run.py's SITES count changes
    # (len(SITES) + 2 for the two Browser-tool-only sites, UW-Madison and
    # HigherEdJobs -- 176 + 2 = 178 as of 2026-09-03).
    BOARDS_MONITORED = 178

    html = template.replace("__TODAY_ISO__", today.isoformat())
    html = html.replace("__GENERATED_STR__", today.strftime("%-d %b %Y"))
    html = html.replace("__JOBS_JSON__", json.dumps(jobs, ensure_ascii=False))
    html = html.replace("__BOARDS_MONITORED__", str(BOARDS_MONITORED))

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
