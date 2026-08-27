#!/usr/bin/env python3
"""
Daily job-search fetch for El (Lisa) Ray Burn's Job Hunter pipeline.

Queries the Adzuna API for a fixed set of target titles across Australia,
applies a heuristic fit-score (derived from the existing Application Tracker
Database v2.xlsx scoring pattern: title fit + industry tier + capability
keyword bonus - technical/digital-specific penalties), dedupes against a
"seen" ledger committed to this repo, and writes the day's new candidates to
latest_results.json for the Claude Cowork daily trigger to pick up and file
into the tracker.

Runs in GitHub Actions, which has unrestricted internet access (unlike the
sandbox this pipeline was designed from), so this is the one piece of the
pipeline that talks to the real Adzuna API directly.
"""
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

APP_ID = os.environ.get("ADZUNA_APP_ID", "").strip()
APP_KEY = os.environ.get("ADZUNA_APP_KEY", "").strip()

if not APP_ID or not APP_KEY:
    print("ERROR: ADZUNA_APP_ID / ADZUNA_APP_KEY not set in environment.", file=sys.stderr)
    sys.exit(1)

TARGET_TITLES = [
    "senior product manager",
    "product manager",
    "product owner",
    "senior product owner",
    "product lead",
    "technical product manager",
    "product delivery manager",
]

RESULTS_PER_PAGE = 50
MAX_DAYS_OLD = 3  # daily run, so only need very recent postings
SEEN_FILE = "seen_urls.json"
OUTPUT_FILE = "latest_results.json"

# ---------- Fit-scoring rubric ----------
# Derived from patterns already established in Application Tracker Database v2.xlsx.
# This is a triage heuristic, not a verified JD match -- rows land as
# "JD Verified: No" until Lisa confirms.

INDUSTRY_RULES = [
    # (regex on title+company+description, tier label, base score)
    (r"\b(bank|banking|lending|lender|credit union|mortgage|fintech|neobank)\b", "Fintech", 35),
    (r"\b(credit bureau|credit report|buy now pay later|bnpl)\b", "Fintech", 35),
    (r"\b(transport for nsw|tfnsw|department of|nsw government|state government|public sector|nsw health|ehealth|iworkfor)\b", "Government", 32),
    (r"\b(health|hospital|medical|clinical)\b", "Healthcare", 30),
    (r"\b(insurance|superannuation|\bsuper\b|wealth management)\b", "Insurance/Super", 18),
    (r"\b(education|edtech|university|school)\b", "Education", 22),
]
DEFAULT_INDUSTRY = ("Other", 15)

TITLE_RULES = [
    (r"\b(technical product (manager|lead|owner)|data product (manager|owner)|platform product)\b", 35),
    (r"\b(principal product|group product|head of product|director of product)\b", 33),
    (r"\b(senior product (manager|owner|lead)|product lead|product delivery manager)\b", 33),
    (r"\bdigital product (manager|owner|lead)\b", 15),  # deprioritized - digital-specific
    (r"\bproduct (manager|owner)\b", 28),
]
DEFAULT_TITLE_SCORE = 10

CAPABILITY_KEYWORDS = [
    "data platform", "api", "regulatory", "compliance", "credit", "lending",
    "data governance", "multi-provider", "b2b", "roadmap", "data product",
    "data feed", "integration", "data quality",
]

TECH_INFRA_PENALTY_PATTERN = re.compile(
    r"\b(devops|kubernetes|site reliability|\bsre\b|cloud engineer|infrastructure engineer|"
    r"sovereign compute|network engineer|systems administrator)\b",
    re.IGNORECASE,
)
DIGITAL_SPECIFIC_PATTERN = re.compile(r"\bdigital product (manager|owner|lead)\b", re.IGNORECASE)
ED_SAAS_PATTERN = re.compile(r"\b(learning management|assessment platform|ed-?tech saas)\b", re.IGNORECASE)


def score_listing(title: str, company: str, description: str):
    text = f"{title} {company} {description}".lower()

    industry_label, industry_score = DEFAULT_INDUSTRY
    for pattern, label, score in INDUSTRY_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            industry_label, industry_score = label, score
            break

    title_score = DEFAULT_TITLE_SCORE
    for pattern, score in TITLE_RULES:
        if re.search(pattern, title, re.IGNORECASE):
            title_score = max(title_score, score)

    bonus = sum(3 for kw in CAPABILITY_KEYWORDS if kw in text)
    bonus = min(bonus, 15)

    notes = []
    penalty = 0
    if TECH_INFRA_PENALTY_PATTERN.search(text):
        penalty += 20
        notes.append("Deprioritized - highly technical infra")
    if DIGITAL_SPECIFIC_PATTERN.search(title):
        penalty += 8
        notes.append("Deprioritized - digital-specific")
    if industry_label == "Education" and ED_SAAS_PATTERN.search(text):
        penalty += 8
        notes.append("Mild penalty - SaaS/ed-product")

    fit_score = max(0, min(100, title_score + industry_score + bonus - penalty))
    product_type_fit = "Fit" if not notes else "; ".join(notes)
    return fit_score, industry_label, product_type_fit


def adzuna_search(query: str, page: int = 1):
    base = f"https://api.adzuna.com/v1/api/jobs/au/search/{page}"
    params = {
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "what": query,
        "results_per_page": RESULTS_PER_PAGE,
        "max_days_old": MAX_DAYS_OLD,
        "content-type": "application/json",
    }
    url = base + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "job-hunter-automation/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(sorted(seen), f, indent=2)


def main():
    seen = load_seen()
    candidates = {}

    for query in TARGET_TITLES:
        try:
            data = adzuna_search(query)
        except Exception as exc:
            print(f"WARN: query '{query}' failed: {exc}", file=sys.stderr)
            continue
        for job in data.get("results", []):
            url = job.get("redirect_url") or job.get("id")
            if not url or url in seen:
                continue
            title = job.get("title", "").strip()
            company = (job.get("company") or {}).get("display_name", "").strip()
            location = (job.get("location") or {}).get("display_name", "").strip()
            description = job.get("description", "").strip()
            created = job.get("created", "")

            fit_score, industry_tier, product_type_fit = score_listing(title, company, description)

            candidates[url] = {
                "title": title,
                "company": company,
                "location": location,
                "url": url,
                "description_snippet": description[:500],
                "created": created,
                "source": "Adzuna",
                "fit_score": fit_score,
                "industry_tier": industry_tier,
                "product_type_fit": product_type_fit,
                "jd_verified": "No",
            }
        time.sleep(1)  # be polite to the free-tier rate limit

    for url in candidates:
        seen.add(url)
    save_seen(seen)

    result_list = sorted(candidates.values(), key=lambda c: c["fit_score"], reverse=True)
    output = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(result_list),
        "candidates": result_list,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Wrote {len(result_list)} new candidates to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
