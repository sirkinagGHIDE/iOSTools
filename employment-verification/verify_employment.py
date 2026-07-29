#!/usr/bin/env python3
"""Flag contacts in a CSV who are confirmed to no longer work at their listed company.

Uses the Google Custom Search JSON API to search the web for each contact,
then looks for explicit departure language ("no longer works at", "former
X at", "left the company", etc.) near a mention of the listed company. If
no such evidence is found, the contact's status is left as "unknown" and
they are skipped from the flagged output, per design: unknown/ambiguous
means move on, don't guess.
"""

import argparse
import csv
import json
import os
import re
import sys
import time

import requests

GOOGLE_ENDPOINT = "https://www.googleapis.com/customsearch/v1"

NAME_COLUMN_CANDIDATES = ["name", "full_name", "contact_name", "full name", "contact name"]
COMPANY_COLUMN_CANDIDATES = [
    "company", "employer", "organization", "company_name", "company name",
    "current_company", "current company",
]

DEPARTURE_PATTERNS = [
    re.compile(p, re.I)
    for p in [
        r"no longer (?:works|working|employed|associated)\s+(?:at|with|for)",
        r"is no longer (?:with|at)",
        r"formerly (?:at|with|of)",
        r"former\s+[\w &.'-]{0,40}?\s+at",
        r"used to work (?:at|for)",
        r"previously (?:at|with|worked at)",
        r"\bleft\b[\w\s]{0,30}?(?:in|on|after|to|last|earlier|\d{4})",
        r"departed (?:from)?",
        r"stepped down (?:as|from)",
        r"ex-[\w-]*\s*employee",
        r"\bformer employee\b",
        r"is no longer (?:employed|associated)",
    ]
]

COMPANY_STOPWORDS = {
    "inc", "inc.", "llc", "l.l.c.", "ltd", "ltd.", "corp", "corp.", "corporation",
    "co", "co.", "company", "the", "group", "holdings", "plc",
}


def normalize(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def company_tokens(company):
    words = re.findall(r"[A-Za-z0-9]+", company.lower())
    significant = [w for w in words if w not in COMPANY_STOPWORDS and len(w) > 1]
    return significant or words


def mentions_company(text, company):
    if not company:
        return False
    if normalize(company) in normalize(text):
        return True
    toks = company_tokens(company)
    if not toks:
        return False
    text_l = text.lower()
    hits = sum(1 for t in toks if t in text_l)
    return hits >= max(1, len(toks) - 1)


def find_departure_evidence(results, company):
    for r in results:
        blob = f"{r.get('title', '')}. {r.get('snippet', '')}"
        if not mentions_company(blob, company):
            continue
        for pat in DEPARTURE_PATTERNS:
            m = pat.search(blob)
            if m:
                return blob.strip(), r.get("link", ""), m.group(0)
    return None


def build_query(name, company):
    return (
        f'"{name}" "{company}" '
        f'(former OR formerly OR "no longer" OR left OR "ex-employee" OR previously OR departed)'
    )


def normalize_header(h):
    return h.strip().lower() if h else h


def pick_column(fieldnames, candidates, explicit):
    if explicit:
        key = normalize_header(explicit)
        if key in fieldnames:
            return key
        raise SystemExit(f"Column '{explicit}' not found. Available columns: {', '.join(fieldnames)}")
    for c in candidates:
        if c in fieldnames:
            return c
    return None


def load_cache(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(path, cache):
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cache, f)


def search(session, api_key, cx, query, num_results):
    params = {"key": api_key, "cx": cx, "q": query, "num": min(max(num_results, 1), 10)}
    last_exc = None
    for attempt in range(4):
        resp = session.get(GOOGLE_ENDPOINT, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 502, 503):
            time.sleep(2 ** attempt)
            last_exc = requests.HTTPError(f"{resp.status_code}: {resp.text[:200]}")
            continue
        resp.raise_for_status()
    if last_exc:
        raise last_exc
    return {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Path to the input contacts CSV")
    parser.add_argument("-o", "--output", default="flagged_contacts.csv",
                         help="Where to write contacts confirmed to have left (default: flagged_contacts.csv)")
    parser.add_argument("--full-log", default=None,
                         help="Where to write every contact with its status/evidence (default: <output>.full.csv)")
    parser.add_argument("--api-key", default=os.environ.get("GOOGLE_API_KEY"),
                         help="Google API key (or set GOOGLE_API_KEY env var)")
    parser.add_argument("--cse-id", default=os.environ.get("GOOGLE_CSE_ID"),
                         help="Google Programmable Search Engine ID (or set GOOGLE_CSE_ID env var)")
    parser.add_argument("--name-column", help="Override which CSV column holds the contact's name")
    parser.add_argument("--company-column", help="Override which CSV column holds the company name")
    parser.add_argument("--num-results", type=int, default=5, help="Search results to inspect per contact (default: 5)")
    parser.add_argument("--delay", type=float, default=1.2, help="Seconds to wait between live API calls (default: 1.2)")
    parser.add_argument("--cache-file", default=".search_cache.json",
                         help="Cache file for raw search responses, so re-runs don't burn API quota (default: .search_cache.json)")
    parser.add_argument("--dry-run", action="store_true", help="Print the queries that would be run, without calling the API")
    args = parser.parse_args()

    with open(args.input, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = [normalize_header(h) for h in (reader.fieldnames or [])]
        reader.fieldnames = fieldnames
        rows = list(reader)

    name_col = pick_column(fieldnames, NAME_COLUMN_CANDIDATES, args.name_column)
    company_col = pick_column(fieldnames, COMPANY_COLUMN_CANDIDATES, args.company_column)
    if not name_col or not company_col:
        raise SystemExit(
            "Could not find name/company columns automatically. "
            f"Available columns: {', '.join(fieldnames)}. "
            "Use --name-column and --company-column to specify them."
        )

    if not args.dry_run and (not args.api_key or not args.cse_id):
        raise SystemExit(
            "Missing Google API credentials. Pass --api-key/--cse-id or set "
            "GOOGLE_API_KEY and GOOGLE_CSE_ID environment variables. See README.md for setup."
        )

    cache = load_cache(args.cache_file)
    session = requests.Session()

    full_rows = []
    flagged_rows = []
    total = len(rows)

    for i, row in enumerate(rows, start=1):
        name = (row.get(name_col) or "").strip()
        company = (row.get(company_col) or "").strip()
        out_row = dict(row)

        if not name or not company:
            print(f"[{i}/{total}] skipping row with missing name/company", file=sys.stderr)
            out_row.update({"status": "skipped", "evidence_phrase": "", "evidence_snippet": "", "evidence_url": "", "query_used": ""})
            full_rows.append(out_row)
            continue

        query = build_query(name, company)

        if args.dry_run:
            print(f"[{i}/{total}] {name} @ {company} -> {query}")
            continue

        if query in cache:
            data = cache[query]
        else:
            try:
                data = search(session, args.api_key, args.cse_id, query, args.num_results)
            except requests.HTTPError as e:
                print(f"[{i}/{total}] {name} @ {company} -> search error: {e}", file=sys.stderr)
                out_row.update({"status": "error", "evidence_phrase": "", "evidence_snippet": str(e), "evidence_url": "", "query_used": query})
                full_rows.append(out_row)
                continue
            cache[query] = data
            save_cache(args.cache_file, cache)
            time.sleep(args.delay)

        items = data.get("items", [])[: args.num_results]
        results = [{"title": it.get("title", ""), "snippet": it.get("snippet", ""), "link": it.get("link", "")} for it in items]
        evidence = find_departure_evidence(results, company)

        if evidence:
            blob, link, phrase = evidence
            status = "left"
            flagged_rows.append({**out_row, "status": status, "evidence_phrase": phrase, "evidence_snippet": blob, "evidence_url": link, "query_used": query})
            print(f"[{i}/{total}] {name} @ {company} -> LEFT ({phrase!r})", file=sys.stderr)
        else:
            status = "unknown"
            blob = link = phrase = ""
            print(f"[{i}/{total}] {name} @ {company} -> unknown, skipping", file=sys.stderr)

        out_row.update({"status": status, "evidence_phrase": phrase, "evidence_snippet": blob, "evidence_url": link, "query_used": query})
        full_rows.append(out_row)

    if args.dry_run:
        return

    full_log_path = args.full_log or f"{args.output}.full.csv"
    extra_fields = ["status", "evidence_phrase", "evidence_snippet", "evidence_url", "query_used"]
    out_fieldnames = list(rows[0].keys()) if rows else fieldnames
    out_fieldnames = out_fieldnames + [f for f in extra_fields if f not in out_fieldnames]

    with open(full_log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        writer.writerows(full_rows)

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames)
        writer.writeheader()
        writer.writerows(flagged_rows)

    print(f"\nDone. {len(flagged_rows)} confirmed no longer employed, {total - len(flagged_rows)} unknown/skipped.", file=sys.stderr)
    print(f"Flagged contacts: {args.output}", file=sys.stderr)
    print(f"Full log (all contacts + evidence): {full_log_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
