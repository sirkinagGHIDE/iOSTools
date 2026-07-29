# Employment Verification Tool

Upload a CSV of contacts and find out which ones are **confirmed** to no
longer work at their listed company. If the evidence is unclear or
ambiguous, the contact is skipped — this tool only reports confident
departures, never guesses.

## How it works

For each contact, the script searches the web (via Google's Custom Search
API) for their name + company, alongside departure language ("no longer
works at", "former ... at", "left the company", etc.). A contact is only
flagged as having left if a search result explicitly contains that kind of
language near a mention of their listed company. Everything else is left
as "unknown" and excluded from the flagged output.

This is a heuristic based on public search snippets, not a guarantee —
always check the `evidence_url` for a flagged contact before acting on it.

## 1. Set up Google Custom Search API access

1. Go to console.cloud.google.com, create/select a project, and enable the
   "Custom Search API". Create an API key under "Credentials" — this is
   your `GOOGLE_API_KEY`.
2. Go to programmablesearchengine.google.com and create a new search
   engine. In its settings, turn on "Search the entire web". Copy its
   "Search engine ID" — this is your `GOOGLE_CSE_ID`.
3. Note the free tier is 100 queries/day; beyond that it's billed per
   Google's pricing. This tool uses 1 query per contact by default.

## 2. Install

```bash
cd employment-verification
pip install -r requirements.txt
export GOOGLE_API_KEY=your_key_here
export GOOGLE_CSE_ID=your_cse_id_here
```

## 3. CSV format

The input CSV needs a name column and a company column. These are
detected automatically from common headers (`name`, `full_name`, `company`,
`employer`, `organization`, etc.), case-insensitive. Other columns (job
title, email, etc.) are preserved in the output. See `sample_contacts.csv`
for an example.

## 4. Run

```bash
# Preview the search queries without using any API quota
python verify_employment.py sample_contacts.csv --dry-run

# Real run
python verify_employment.py sample_contacts.csv -o flagged.csv
```

Progress is printed to stderr as each contact is checked. Two files are
written:

- `flagged.csv` — only contacts confirmed to no longer work at their
  listed company, with an `evidence_snippet` and `evidence_url` column.
- `flagged.csv.full.csv` — every contact with a `status` column
  (`left`, `unknown`, `skipped`, or `error`), for auditing.

## Options

| Flag | Purpose |
|---|---|
| `--name-column`, `--company-column` | Override auto-detected columns |
| `--num-results` | Search results inspected per contact (default 5) |
| `--delay` | Seconds between live API calls (default 1.2) |
| `--cache-file` | Cache raw search responses so re-running doesn't burn quota (default `.search_cache.json`) |
| `--full-log` | Custom path for the full audit CSV |
| `--dry-run` | Print queries only, no API calls |

Re-running the script reuses the cache file, so interrupted runs (e.g. if
you hit the daily quota) can be resumed later without re-spending queries
on contacts already checked.
