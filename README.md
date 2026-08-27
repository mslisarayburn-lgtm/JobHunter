# Job Search Automation (Adzuna feed)

Part of El's Job Hunter pipeline. This repo exists purely to make one outbound
API call GitHub Actions' runners can make that Claude's cloud workspace
cannot (Claude's sandbox blocks arbitrary outbound API calls; GitHub Actions
runners have normal internet access).

## What it does

Every morning at 20:00 UTC (~6am Sydney, see DST note below), a scheduled
GitHub Action:

1. Queries the Adzuna API (`api.adzuna.com`) for a fixed set of target
   product-management titles across Australia.
2. Applies a heuristic fit-score (title match + industry tier + capability
   keyword bonus - technical/digital-specific penalties) mirroring the
   scoring already used in `Application Tracker Database v2.xlsx`.
3. Dedupes against `seen_urls.json` (committed to this repo) so the same
   listing is never surfaced twice.
4. Commits the day's new candidates to `latest_results.json`.

Claude's own daily scheduled task (in Cowork) reads `latest_results.json`
from this repo about an hour later, dedupes again against the live tracker,
appends new rows, and sends a push notification digest.

## Setup

Secrets required (Settings -> Secrets and variables -> Actions):

- `ADZUNA_APP_ID`
- `ADZUNA_APP_KEY`

Get these free at https://developer.adzuna.com/signup.

## Daylight saving note

Sydney is UTC+10 (AEST) most of the year but UTC+11 (AEDT) from early
October to early April. The cron in `daily-job-search.yml` is set for AEST.
When DST changes, ask Claude to shift the cron by one hour (20:00 UTC <->
19:00 UTC), and update the paired Cowork scheduled task the same way.

## Manual run

Use the "Run workflow" button on the Actions tab (workflow_dispatch) to
trigger a fetch on demand, e.g. to test after changing target titles.

## Adjusting target titles or scoring

Edit `scripts/fetch_jobs.py` - `TARGET_TITLES`, `INDUSTRY_RULES`, and
`TITLE_RULES` are all plain Python at the top of the file.
