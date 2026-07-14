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
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={ACTIVE_JOBS_GID}"
TEMPLATE_PATH = f"{DIR}/dashboard_template.html"
OUTPUT_PATH = f"{DIR}/index.html"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"

DATE_FORMATS = ["%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y", "%b %d, %Y"]


def fetch_csv():
    req = urllib.request.Request(CSV_URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8")


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


def build_job(row, today):
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

    return {
        "university": university,
        "rank": format_rank(row.get("Rank")),
        "tt": (row.get("TT-NTT-PostDoc") or "").strip() or "TT",
        "area": area,
        "location": (row.get("Location") or "").strip(),
        "region": (row.get("Region") or "").strip(),
        "salary": (row.get("Salary") or "").strip(),
        "teaching": (row.get("Teaching load") or "").strip(),
        "posted": posted.isoformat() if posted else "",
        "due": due.isoformat() if due else "",
        "link": link,
    }


def main():
    today = date.today()
    try:
        csv_text = fetch_csv()
    except Exception as e:
        print(f"ERROR fetching sheet CSV: {e}", file=sys.stderr)
        sys.exit(1)

    reader = csv.DictReader(io.StringIO(csv_text))
    jobs = []
    for row in reader:
        job = build_job(row, today)
        if job:
            jobs.append(job)

    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        template = f.read()

    html = template.replace("__TODAY_ISO__", today.isoformat())
    html = html.replace("__GENERATED_STR__", today.strftime("%-d %b %Y"))
    html = html.replace("__JOBS_JSON__", json.dumps(jobs, ensure_ascii=False))

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
