"""
Remote Job Discovery & Smart Matching Engine
=============================================
Aggregates jobs from Remotive, Adzuna, JSearch/RapidAPI.
Normalises to a common schema, scores relevance, and provides
AI-assisted matching and proposal generation.

Profile: Mayank Gaur — 17+ yrs Java Full-Stack, targeting remote/
contract/Europe-friendly roles to fund travel & relocation to Europe.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.db import get_conn
from app.services.platform_ai import call as ai_call

logger = logging.getLogger("secretaryai.job_discovery")

# ─── Candidate Profile ────────────────────────────────────────────────────────

MY_PROFILE = {
    "name": "Mayank Gaur",
    "experience_years": 17,
    "title": "Java Full-Stack Developer / Technical Lead",
    "core_skills": [
        "Java", "Spring Boot", "Spring Framework", "Microservices",
        "REST APIs", "SQL", "PostgreSQL", "MySQL", "React", "Angular",
        "AWS", "Azure", "Docker", "Kubernetes", "Jenkins", "Git",
        "System Design", "Kafka", "RabbitMQ", "Redis",
    ],
    "nice_to_have": ["GraphQL", "Node.js", "TypeScript", "Terraform", "Go"],
    "job_types": ["contract", "freelance", "consulting", "part-time", "remote"],
    "target_salary_eur_annual": {"min": 80000, "max": 130000},
    "target_hourly_eur": {"min": 50, "max": 120},
    "europe_preference": True,
    "goal": "Fund travel and relocation to Europe via remote/contract income",
}

_PROFILE_BLOB = (
    f"Candidate: {MY_PROFILE['name']}, {MY_PROFILE['experience_years']}+ years experience. "
    f"Title: {MY_PROFILE['title']}. "
    f"Core skills: {', '.join(MY_PROFILE['core_skills'])}. "
    f"Preferred job types: {', '.join(MY_PROFILE['job_types'])}. "
    f"Salary target: €{MY_PROFILE['target_salary_eur_annual']['min']:,}–"
    f"€{MY_PROFILE['target_salary_eur_annual']['max']:,}/yr or "
    f"€{MY_PROFILE['target_hourly_eur']['min']}–€{MY_PROFILE['target_hourly_eur']['max']}/hr. "
    "Goal: remote contract roles to support Europe travel/relocation."
)

# ─── Scoring constants ────────────────────────────────────────────────────────

_JAVA_KEYWORDS = {"java", "spring", "spring boot", "spring framework", "jvm",
                  "jakarta", "kotlin", "hibernate", "jpa"}
_BACKEND_KEYWORDS = {"microservices", "rest api", "backend", "api", "cloud",
                     "distributed", "kafka", "rabbitmq", "redis"}
_CLOUD_KEYWORDS = {"aws", "azure", "gcp", "kubernetes", "k8s", "docker",
                   "terraform", "devops", "sre"}
_FRONTEND_KEYWORDS = {"react", "angular", "typescript", "frontend", "full stack",
                      "full-stack"}
_LEVEL_KEYWORDS = {"senior", "lead", "principal", "architect", "head of",
                   "staff engineer", "tech lead"}
_CONTRACT_KEYWORDS = {"contract", "freelance", "consulting", "contractor",
                      "freelancer", "part-time", "part time", "gig", "fixed-term"}
_EUROPE_LOCATIONS = {
    "europe", "germany", "berlin", "munich", "frankfurt", "hamburg",
    "netherlands", "amsterdam", "uk", "london", "ireland", "dublin",
    "france", "paris", "spain", "barcelona", "madrid", "sweden",
    "stockholm", "denmark", "copenhagen", "remote", "worldwide", "global",
    "anywhere", "international",
}

# ─── Salary parsing ───────────────────────────────────────────────────────────

def _parse_salary_text(text: str) -> tuple[int | None, int | None, str]:
    """Parse salary strings like '€80k–100k', '$120/hr', '80,000 - 100,000 EUR'."""
    if not text:
        return None, None, "EUR"
    text = re.sub(r"<[^>]+>", "", text)
    currency = "USD"
    if any(c in text for c in ("€", "EUR")):
        currency = "EUR"
    elif any(c in text for c in ("£", "GBP")):
        currency = "GBP"
    is_hourly = bool(re.search(r"/hr|per.hour|hourly", text, re.I))
    nums_raw = re.findall(r"[\d]+(?:[,\.][\d]{3})*(?:k)?", text, re.I)
    nums: list[int] = []
    for n in nums_raw:
        n_clean = n.replace(",", "").lower()
        if n_clean.endswith("k"):
            nums.append(int(n_clean[:-1]) * 1000)
        else:
            try:
                nums.append(int(n_clean))
            except ValueError:
                pass
    if not nums:
        return None, None, currency
    nums = sorted(nums[:2])
    lo = nums[0] if nums else None
    hi = nums[-1] if nums else lo
    # Convert hourly to approximate annual (40hr/wk × 48 weeks)
    if is_hourly and lo and lo < 2000:
        lo = lo * 40 * 48
        hi = (hi or lo) * 40 * 48
    return lo, hi, currency


# ─── Quick heuristic scoring (0–100, no AI) ──────────────────────────────────

def _quick_score(job: dict) -> int:
    """Score job relevance against MY_PROFILE without calling AI. Returns 0-100."""
    blob = " ".join(filter(None, [
        job.get("title", ""),
        job.get("description", ""),
        job.get("tags", ""),
        job.get("job_type", ""),
        job.get("location", ""),
    ])).lower()

    score = 35  # base

    # Java / JVM stack (most important)
    java_hits = sum(1 for k in _JAVA_KEYWORDS if k in blob)
    score += min(java_hits * 6, 20)

    # Backend / cloud keywords
    backend_hits = sum(1 for k in _BACKEND_KEYWORDS if k in blob)
    score += min(backend_hits * 3, 9)

    # Cloud / DevOps
    cloud_hits = sum(1 for k in _CLOUD_KEYWORDS if k in blob)
    score += min(cloud_hits * 2, 6)

    # Frontend skills (bonus for full-stack)
    if any(k in blob for k in _FRONTEND_KEYWORDS):
        score += 4

    # Seniority level
    if any(k in blob for k in _LEVEL_KEYWORDS):
        score += 5

    # Contract / freelance preference
    type_hits = sum(1 for k in _CONTRACT_KEYWORDS if k in blob)
    score += min(type_hits * 4, 10)

    # Europe / Remote location
    if any(k in blob for k in _EUROPE_LOCATIONS):
        score += 7

    # Salary quality
    s_max = job.get("salary_max") or 0
    if s_max >= 100000:
        score += 6
    elif s_max >= 80000:
        score += 4

    return min(score, 100)


def _is_europe_friendly(job: dict) -> bool:
    loc = (job.get("location") or "").lower()
    return any(k in loc for k in _EUROPE_LOCATIONS)


def _travel_fund_score(job: dict) -> int:
    """Score 0-100: how useful is this job for funding Europe travel/relocation?"""
    score = 0
    blob = (job.get("title", "") + " " + job.get("description", "") +
            " " + job.get("job_type", "")).lower()
    # Contract/freelance preferred (faster pay, flexible hours)
    if any(k in blob for k in ("contract", "freelance", "consulting", "contractor")):
        score += 35
    elif "part-time" in blob or "part time" in blob:
        score += 20
    else:
        score += 10
    # Remote is mandatory
    if "remote" in blob or job.get("remote_type") == "remote":
        score += 20
    # High hourly rate = direct travel funding
    h_max = job.get("hourly_rate_max") or 0
    s_max = job.get("salary_max") or 0
    if h_max >= 80:
        score += 30
    elif h_max >= 50:
        score += 20
    elif s_max >= 100000:
        score += 20
    elif s_max >= 80000:
        score += 15
    else:
        score += 5
    # Java skills = can start quickly without re-skilling
    if any(k in blob for k in _JAVA_KEYWORDS):
        score += 15
    return min(score, 100)


def _estimated_monthly_eur(job: dict) -> int | None:
    """Estimate monthly EUR earning potential."""
    h_min = job.get("hourly_rate_min")
    h_max = job.get("hourly_rate_max")
    if h_max:
        return int((h_min or h_max) * 160)  # 160 hrs/month
    s_min = job.get("salary_min")
    s_max = job.get("salary_max")
    if s_max:
        cur = job.get("salary_currency", "EUR")
        monthly = int((s_min or s_max) / 12)
        if cur == "GBP":
            monthly = int(monthly * 1.17)
        elif cur == "USD":
            monthly = int(monthly * 0.92)
        return monthly
    return None


# ─── Source fetchers ──────────────────────────────────────────────────────────

def _fetch_remotive(client: httpx.Client) -> list[dict]:
    """
    Remotive public API — free, no auth required.
    https://remotive.com/api/remote-jobs?category=software-dev&search=java&limit=100
    """
    params = {
        "category": "software-dev",
        "search": "java spring backend",
        "limit": 100,
    }
    try:
        resp = client.get("https://remotive.com/api/remote-jobs", params=params, timeout=20)
        resp.raise_for_status()
        jobs = resp.json().get("jobs", [])
        logger.info("Remotive: fetched %d jobs", len(jobs))
    except Exception as exc:
        logger.error("Remotive fetch failed: %s", exc)
        return []

    results = []
    for j in jobs:
        salary_text = j.get("salary") or ""
        s_min, s_max, s_cur = _parse_salary_text(salary_text)
        # Determine hourly
        h_min = h_max = None
        if s_max and s_max < 5000:  # likely hourly
            h_min, h_max = s_min, s_max
            s_min = s_max = None
        tags_raw = j.get("tags") or []
        tags = json.dumps(tags_raw) if isinstance(tags_raw, list) else str(tags_raw)
        loc = j.get("candidate_required_location") or "Worldwide"
        results.append({
            "external_id": str(j.get("id", "")),
            "source": "remotive",
            "source_url": j.get("url") or j.get("company_logo_url"),
            "title": (j.get("title") or "").strip(),
            "company": (j.get("company_name") or "Unknown").strip(),
            "location": loc,
            "country": _infer_country(loc),
            "remote_type": "remote",
            "job_type": _normalise_job_type(j.get("job_type") or ""),
            "salary_min": s_min,
            "salary_max": s_max,
            "salary_currency": s_cur,
            "hourly_rate_min": h_min,
            "hourly_rate_max": h_max,
            "description": (j.get("description") or "")[:2000],
            "tags": tags,
            "posted_at": j.get("publication_date"),
        })
    return results


def _fetch_adzuna(client: httpx.Client) -> list[dict]:
    """
    Adzuna Developer API — requires ADZUNA_APP_ID + ADZUNA_APP_KEY.
    Searches GB + DE for Java/Spring remote jobs.
    """
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        logger.debug("Adzuna skipped: ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
        return []

    results: list[dict] = []
    for country_code in ("gb", "de", "nl", "ie"):
        url = f"https://api.adzuna.com/v1/api/jobs/{country_code}/search/1"
        params = {
            "app_id": app_id,
            "app_key": app_key,
            "results_per_page": 50,
            "what": "java spring backend developer",
            "what_or": "remote contract freelance",
            "content-type": "application/json",
            "sort_by": "date",
        }
        try:
            resp = client.get(url, params=params, timeout=20)
            resp.raise_for_status()
            raw_results = resp.json().get("results", [])
            logger.info("Adzuna [%s]: fetched %d jobs", country_code, len(raw_results))
        except Exception as exc:
            logger.error("Adzuna [%s] fetch failed: %s", country_code, exc)
            continue

        for j in raw_results:
            loc = (j.get("location") or {}).get("display_name") or country_code.upper()
            contract_type = j.get("contract_type") or ""
            contract_time = j.get("contract_time") or "full_time"
            results.append({
                "external_id": str(j.get("id", "")),
                "source": "adzuna",
                "source_url": j.get("redirect_url"),
                "title": (j.get("title") or "").strip(),
                "company": (j.get("company", {}).get("display_name") or "Unknown").strip(),
                "location": loc,
                "country": _infer_country(loc),
                "remote_type": "remote" if "remote" in (j.get("title") or "").lower() else "hybrid",
                "job_type": _normalise_job_type(contract_type or contract_time),
                "salary_min": j.get("salary_min"),
                "salary_max": j.get("salary_max"),
                "salary_currency": "GBP" if country_code == "gb" else "EUR",
                "description": (j.get("description") or "")[:2000],
                "tags": json.dumps([]),
                "posted_at": j.get("created"),
            })
    return results


def _fetch_jsearch(client: httpx.Client) -> list[dict]:
    """
    JSearch via RapidAPI — requires RAPIDAPI_KEY.
    Returns remote Java/Spring contract jobs globally.
    """
    api_key = os.getenv("RAPIDAPI_KEY")
    if not api_key:
        logger.debug("JSearch skipped: RAPIDAPI_KEY not set")
        return []

    queries = [
        "java spring boot remote contract",
        "java microservices freelance remote",
        "backend java developer remote europe",
    ]
    results: list[dict] = []
    headers = {
        "X-RapidAPI-Key": api_key,
        "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
    }
    for query in queries:
        params = {
            "query": query,
            "page": "1",
            "num_pages": "1",
            "date_posted": "week",
            "remote_jobs_only": "true",
        }
        try:
            resp = client.get(
                "https://jsearch.p.rapidapi.com/search",
                params=params,
                headers=headers,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            logger.info("JSearch [%s]: fetched %d jobs", query, len(data))
        except Exception as exc:
            logger.error("JSearch [%s] failed: %s", query, exc)
            continue

        for j in data:
            # Handle hourly vs annual salary
            s_min = j.get("job_min_salary")
            s_max = j.get("job_max_salary")
            period = (j.get("job_salary_period") or "").upper()
            h_min = h_max = None
            s_cur = j.get("job_salary_currency") or "USD"
            if s_max and period == "HOUR":
                h_min, h_max = s_min, s_max
                s_min = s_max = None
            elif s_max and period == "MONTH" and s_max < 50000:
                s_min = int(s_min * 12) if s_min else None
                s_max = int(s_max * 12)
            loc = j.get("job_city") or j.get("job_country") or "Remote"
            results.append({
                "external_id": str(j.get("job_id", "")),
                "source": "jsearch",
                "source_url": j.get("job_apply_link"),
                "title": (j.get("job_title") or "").strip(),
                "company": (j.get("employer_name") or "Unknown").strip(),
                "location": loc,
                "country": j.get("job_country") or _infer_country(loc),
                "remote_type": "remote" if j.get("job_is_remote") else "onsite",
                "job_type": _normalise_job_type(j.get("job_employment_type") or ""),
                "salary_min": s_min,
                "salary_max": s_max,
                "salary_currency": s_cur,
                "hourly_rate_min": h_min,
                "hourly_rate_max": h_max,
                "description": (j.get("job_description") or "")[:2000],
                "tags": json.dumps(j.get("job_required_skills") or []),
                "posted_at": j.get("job_posted_at_datetime_utc"),
            })
    return results


# ─── Normalisation helpers ────────────────────────────────────────────────────

def _infer_country(location: str) -> str | None:
    loc = (location or "").lower()
    country_map = {
        "germany": "Germany", "berlin": "Germany", "munich": "Germany",
        "frankfurt": "Germany", "hamburg": "Germany",
        "uk": "UK", "united kingdom": "UK", "london": "UK", "england": "UK",
        "ireland": "Ireland", "dublin": "Ireland",
        "netherlands": "Netherlands", "amsterdam": "Netherlands",
        "france": "France", "paris": "France",
        "spain": "Spain", "barcelona": "Spain", "madrid": "Spain",
        "sweden": "Sweden", "stockholm": "Sweden",
        "denmark": "Denmark", "copenhagen": "Denmark",
        "usa": "USA", "united states": "USA", "us": "USA",
        "canada": "Canada",
        "australia": "Australia",
    }
    for key, val in country_map.items():
        if key in loc:
            return val
    if any(k in loc for k in ("remote", "worldwide", "global", "anywhere")):
        return "Remote/Global"
    return None


def _normalise_job_type(raw: str) -> str:
    raw = (raw or "").lower().strip()
    if any(k in raw for k in ("contract", "contractor", "fixed")):
        return "contract"
    if any(k in raw for k in ("freelance", "gig")):
        return "freelance"
    if any(k in raw for k in ("part", "parttime")):
        return "parttime"
    if any(k in raw for k in ("consult",)):
        return "consulting"
    return "fulltime"


# ─── DB persistence ───────────────────────────────────────────────────────────

def _upsert_job(job: dict) -> tuple[bool, int]:
    """Insert or update a remote job. Returns (is_new, job_id)."""
    quick = _quick_score(job)
    europe = 1 if _is_europe_friendly(job) else 0
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM remote_jobs WHERE source=? AND external_id=?",
            (job["source"], job["external_id"]),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE remote_jobs SET
                    title=?, company=?, location=?, country=?, remote_type=?,
                    job_type=?, salary_min=COALESCE(salary_min,?),
                    salary_max=COALESCE(salary_max,?),
                    salary_currency=COALESCE(salary_currency,?),
                    hourly_rate_min=COALESCE(hourly_rate_min,?),
                    hourly_rate_max=COALESCE(hourly_rate_max,?),
                    description=?, tags=?, posted_at=?,
                    is_europe_friendly=?, quick_score=?,
                    updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
                (
                    job["title"], job["company"], job.get("location"),
                    job.get("country"), job.get("remote_type", "remote"),
                    job.get("job_type", "fulltime"),
                    job.get("salary_min"), job.get("salary_max"),
                    job.get("salary_currency", "EUR"),
                    job.get("hourly_rate_min"), job.get("hourly_rate_max"),
                    job.get("description"), job.get("tags"),
                    job.get("posted_at"), europe, quick,
                    existing["id"],
                ),
            )
            return False, existing["id"]
        conn.execute(
            """INSERT INTO remote_jobs (
                external_id, source, source_url, title, company, location, country,
                remote_type, job_type, salary_min, salary_max, salary_currency,
                hourly_rate_min, hourly_rate_max, description, tags, posted_at,
                is_europe_friendly, quick_score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job["external_id"], job["source"], job.get("source_url"),
                job["title"], job["company"], job.get("location"),
                job.get("country"), job.get("remote_type", "remote"),
                job.get("job_type", "fulltime"),
                job.get("salary_min"), job.get("salary_max"),
                job.get("salary_currency", "EUR"),
                job.get("hourly_rate_min"), job.get("hourly_rate_max"),
                job.get("description"), job.get("tags"), job.get("posted_at"),
                europe, quick,
            ),
        )
        new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        return True, new_id


# ─── Main sync entry point ────────────────────────────────────────────────────

def sync_all_sources() -> dict:
    """
    Fetch jobs from all configured sources, normalise, dedup, and persist.
    Returns a summary dict.
    """
    logger.info("Remote job sync starting …")
    total_new = total_updated = total_errors = 0
    source_stats: list[dict] = []

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        sources = [
            ("remotive", _fetch_remotive),
            ("adzuna", _fetch_adzuna),
            ("jsearch", _fetch_jsearch),
        ]
        for source_name, fetcher in sources:
            try:
                jobs = fetcher(client)
                src_new = src_updated = 0
                for job in jobs:
                    if not job.get("title") or not job.get("external_id"):
                        continue
                    is_new, _ = _upsert_job(job)
                    if is_new:
                        src_new += 1
                    else:
                        src_updated += 1
                source_stats.append({
                    "source": source_name,
                    "fetched": len(jobs),
                    "new": src_new,
                    "updated": src_updated,
                })
                total_new += src_new
                total_updated += src_updated
                logger.info(
                    "Source [%s]: fetched=%d new=%d updated=%d",
                    source_name, len(jobs), src_new, src_updated,
                )
            except Exception as exc:
                logger.error("Source [%s] sync error: %s", source_name, exc)
                source_stats.append({"source": source_name, "error": str(exc)})
                total_errors += 1

    result = {
        "total_new": total_new,
        "total_updated": total_updated,
        "total_errors": total_errors,
        "sources": source_stats,
        "synced_at": datetime.utcnow().isoformat(),
    }
    _log_discovery_run("remote_job_sync", "success", json.dumps(result))
    logger.info("Remote job sync complete: new=%d updated=%d errors=%d",
                total_new, total_updated, total_errors)
    return result


def _log_discovery_run(run_type: str, status: str, details: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO automation_runs (run_type, status, details) VALUES (?,?,?)",
            (run_type, status, details),
        )


# ─── AI Matching ──────────────────────────────────────────────────────────────

_AI_MATCH_SYSTEM = """You are a career intelligence engine. Given a job posting and a candidate profile,
return a JSON object (no markdown) with exactly these keys:
{
  "match_score": <integer 0-100>,
  "match_reasons": [<string>, ...],
  "missing_skills": [<string>, ...],
  "salary_assessment": "<below_market|at_market|above_market|unknown>",
  "europe_score": <integer 0-100>,
  "travel_fund_score": <integer 0-100>,
  "estimated_monthly_eur": <integer or null>,
  "match_explanation": "<2-3 sentence human-readable summary>"
}
Be precise. travel_fund_score reflects how useful this role is for funding travel to Europe."""


def ai_match_job(job_id: int) -> dict:
    """Run full AI match scoring for a remote job. Stores result in remote_job_matches."""
    job = get_remote_job(job_id)
    if not job:
        raise ValueError(f"Remote job {job_id} not found")

    salary_info = ""
    if job.get("salary_max"):
        salary_info = f"Salary: {job.get('salary_currency','EUR')} {job.get('salary_min',0):,}–{job.get('salary_max',0):,}/yr."
    elif job.get("hourly_rate_max"):
        salary_info = f"Rate: €{job.get('hourly_rate_min',0)}–€{job.get('hourly_rate_max',0)}/hr."

    prompt = (
        f"CANDIDATE PROFILE:\n{_PROFILE_BLOB}\n\n"
        f"JOB POSTING:\n"
        f"Title: {job['title']}\n"
        f"Company: {job['company']}\n"
        f"Location: {job.get('location','')}\n"
        f"Type: {job.get('job_type','')} / {job.get('remote_type','')}\n"
        f"{salary_info}\n"
        f"Tags: {job.get('tags','')}\n"
        f"Description (first 800 chars): {(job.get('description') or '')[:800]}\n\n"
        "Return the JSON match analysis."
    )
    try:
        raw = ai_call(prompt, system=_AI_MATCH_SYSTEM, max_tokens=600)
        # Extract JSON from response (handle markdown fences)
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            raise ValueError("No JSON found in AI response")
        result = json.loads(json_match.group())
    except Exception as exc:
        logger.error("AI match failed for job %d: %s", job_id, exc)
        # Fall back to quick score
        result = {
            "match_score": job.get("quick_score", 50),
            "match_reasons": ["Score based on keyword matching"],
            "missing_skills": [],
            "salary_assessment": "unknown",
            "europe_score": 70 if job.get("is_europe_friendly") else 30,
            "travel_fund_score": _travel_fund_score(job),
            "estimated_monthly_eur": _estimated_monthly_eur(job),
            "match_explanation": "AI match unavailable; score based on keyword analysis.",
        }

    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO remote_job_matches
               (remote_job_id, match_score, match_reasons, missing_skills,
                salary_assessment, europe_score, travel_fund_score,
                estimated_monthly_eur, match_explanation, matched_at)
               VALUES (?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
            (
                job_id,
                result.get("match_score", 50),
                json.dumps(result.get("match_reasons", [])),
                json.dumps(result.get("missing_skills", [])),
                result.get("salary_assessment", "unknown"),
                result.get("europe_score", 50),
                result.get("travel_fund_score", 50),
                result.get("estimated_monthly_eur"),
                result.get("match_explanation", ""),
            ),
        )
    return result


# ─── Proposal generation ──────────────────────────────────────────────────────

# Mayank's proven achievements bank — injected into every proposal for credibility
_ACHIEVEMENTS_BANK = """
CANDIDATE ACHIEVEMENTS (select 2-3 most relevant to this job):
• Reduced API response latency by 40% via Redis caching layer + async processing refactor
• Led migration from Java monolith to 12-service Spring Boot microservices architecture
• System served 1.2M+ daily active users with 99.9% uptime on AWS ECS / Kubernetes
• Managed cross-functional team of 8 engineers across frontend (React/Angular) and backend (Java)
• Reduced CI/CD pipeline duration from 45 min to 8 min via Jenkins + Docker optimisation
• Designed event-driven architecture using Apache Kafka handling 100K+ events/day
• Azure cost reduction of 30% through containerisation and auto-scaling on AKS
• Delivered 3 consecutive major product releases ahead of schedule over a 17-month engagement
• Built REST API platform now consumed by 6 internal teams and 2 external partners
• Implemented OAuth2/JWT auth layer for enterprise SaaS serving 50,000+ users
"""

_PROPOSAL_PROMPTS = {
    "cover_letter": (
        "Write a high-impact cover letter. Requirements:\n"
        "• Para 1 — HOOK: Open with a specific achievement that directly relates to this job. "
        "  Example: 'I built a Spring Boot microservices platform serving 1.2M daily users — "
        "  when I saw this role at {company}, the architecture challenge immediately resonated.'\n"
        "• Para 2 — PROOF: Mention 2-3 measurable achievements from the bank above that match "
        "  the job requirements. Be specific — numbers beat adjectives.\n"
        "• Para 3 — FIT + CTA: Explain why this specific company / role excites you. "
        "  Close with: 'I'm available for a 20-minute discovery call this week — happy to jump on a "
        "  call at short notice.' Include remote readiness.\n"
        "Keep to 3 tight paragraphs. No filler phrases. No 'I am writing to apply'."
    ),
    "proposal": (
        "Write a freelance/contract project proposal. Requirements:\n"
        "• Line 1 — HOOK: 1-sentence credibility opener with a metric. "
        "  E.g. 'I've architected Spring Boot microservices handling 100K+ Kafka events/day — "
        "  I can bring that same rigour to {company}.'\n"
        "• Section: Relevant Experience (3 bullet points with numbers)\n"
        "• Section: My Approach (2-3 sentences on how you'd tackle this project)\n"
        "• Section: Rate & Availability — state €70–100/hr depending on scope, "
        "  available within 5 business days, remote-first.\n"
        "• Close with strong CTA: 'Let's schedule a 15-min scoping call — I can start "
        "  as soon as next week.'\n"
        "Max 280 words. Confident, not arrogant."
    ),
    "outreach": (
        "Write a LinkedIn DM / recruiter outreach message. Requirements:\n"
        "• Max 100 words.\n"
        "• Open with something specific about the job or company — not generic.\n"
        "• Mention one concrete achievement relevant to the role.\n"
        "• State remote availability and contract/consulting preference.\n"
        "• End with a specific ask: 'Worth a quick chat?'\n"
        "No fluff. Sound like a senior professional who knows their value."
    ),
    "pitch": (
        "Write a 2-3 line elevator pitch for this role. This is shown next to the apply link.\n"
        "Format:\n"
        "Line 1: Your strongest matching skill / achievement for this specific role.\n"
        "Line 2: Why you're a uniquely good fit (specific tech + experience match).\n"
        "Line 3: Availability + CTA.\n"
        "Be punchy. Each line should be one sentence max."
    ),
    "comparison": (
        "Compare this role against an ideal Java Lead contract in Europe. "
        "Output a concise table or bullet list covering: match score rationale, "
        "salary vs market (€80k–130k/yr or €60–100/hr), Europe alignment, "
        "income speed (contract vs fulltime), and a clear verdict: "
        "APPLY NOW / APPLY THIS WEEK / SKIP. Give one sentence of reasoning for the verdict."
    ),
}

_PROPOSAL_SYSTEM = (
    "You are an elite tech career strategist specialising in senior Java developer placements. "
    "Your proposals consistently achieve 40%+ response rates. "
    "Write with confidence, specificity, and urgency. "
    "Output ONLY the requested document — no meta-commentary, no 'here is your letter', "
    "no markdown headers unless they add value. Plain professional text."
)


def generate_proposal(job_id: int, proposal_type: str = "cover_letter") -> dict:
    """
    Generate an AI-written proposal with optimised hooks, measurable achievements,
    and apply-history feedback context.
    """
    job = get_remote_job(job_id)
    if not job:
        raise ValueError(f"Remote job {job_id} not found")

    instruction = _PROPOSAL_PROMPTS.get(proposal_type, _PROPOSAL_PROMPTS["cover_letter"])
    # Substitute {company} placeholder
    instruction = instruction.replace("{company}", job.get("company", "your company"))

    salary_info = ""
    if job.get("salary_max"):
        salary_info = f"Salary: {job.get('salary_currency','EUR')} {job.get('salary_min',0):,}–{job.get('salary_max',0):,}/yr."
    elif job.get("hourly_rate_max"):
        salary_info = f"Rate: €{job.get('hourly_rate_min',0)}–€{job.get('hourly_rate_max',0)}/hr."

    # Pull apply-history learning context
    feedback_context = ""
    try:
        from app.services.apply_engine import get_learning_insights
        insights = get_learning_insights()
        if insights.get("prompt_context"):
            feedback_context = f"\nPREVIOUS APPLY HISTORY CONTEXT:\n{insights['prompt_context']}\n"
    except Exception:
        pass

    prompt = (
        f"CANDIDATE PROFILE:\n{_PROFILE_BLOB}\n"
        f"{_ACHIEVEMENTS_BANK}\n"
        f"{feedback_context}\n"
        f"JOB POSTING:\n"
        f"Title: {job['title']} at {job['company']}\n"
        f"Location: {job.get('location','')}\n"
        f"Type: {job.get('job_type','')} / {job.get('remote_type','')}\n"
        f"{salary_info}\n"
        f"Tags: {job.get('tags','')}\n"
        f"Description: {(job.get('description') or '')[:800]}\n\n"
        f"TASK: {instruction}"
    )

    try:
        content = ai_call(prompt, system=_PROPOSAL_SYSTEM, max_tokens=900)
    except Exception as exc:
        logger.error("Proposal generation failed for job %d: %s", job_id, exc)
        content = f"[Proposal generation failed: {exc}]"

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO remote_proposals (remote_job_id, proposal_type, content) VALUES (?,?,?)",
            (job_id, proposal_type, content),
        )
        # Mark apply_kit_ready
        conn.execute(
            "UPDATE remote_jobs SET apply_kit_ready=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        )

    return {"job_id": job_id, "proposal_type": proposal_type, "content": content}


# ─── CRUD operations ──────────────────────────────────────────────────────────

def list_remote_jobs(
    page: int = 1,
    per_page: int = 30,
    source: str | None = None,
    job_type: str | None = None,
    remote_type: str | None = None,
    europe_only: bool = False,
    min_score: int = 0,
    status: str | None = None,
    saved_only: bool = False,
    search: str | None = None,
    sort_by: str = "relevance",
) -> dict:
    """Paginated job list with filters, joined with AI match data."""
    conditions: list[str] = ["rj.is_hidden = 0"]
    params: list[Any] = []

    if source:
        conditions.append("rj.source = ?")
        params.append(source)
    if job_type:
        conditions.append("rj.job_type = ?")
        params.append(job_type)
    if remote_type:
        conditions.append("rj.remote_type = ?")
        params.append(remote_type)
    if europe_only:
        conditions.append("rj.is_europe_friendly = 1")
    if min_score > 0:
        conditions.append("rj.quick_score >= ?")
        params.append(min_score)
    if status:
        if status == "saved":
            conditions.append("rj.is_saved = 1")
        else:
            conditions.append("rj.application_status = ?")
            params.append(status)
    if saved_only:
        conditions.append("rj.is_saved = 1")
    if search:
        conditions.append(
            "(rj.title LIKE ? OR rj.company LIKE ? OR rj.description LIKE ? OR rj.tags LIKE ?)"
        )
        like = f"%{search}%"
        params.extend([like, like, like, like])

    where = " AND ".join(conditions)
    sort_sql = {
        "relevance": "COALESCE(m.match_score, rj.quick_score) DESC",
        "newest": "rj.posted_at DESC, rj.created_at DESC",
        "salary": "COALESCE(rj.salary_max, rj.hourly_rate_max*1920) DESC",
        "europe": "COALESCE(m.europe_score, rj.is_europe_friendly*60) DESC",
        "travel_fund": "COALESCE(m.travel_fund_score, 0) DESC",
    }.get(sort_by, "COALESCE(m.match_score, rj.quick_score) DESC")

    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) FROM remote_jobs rj WHERE {where}", params
        ).fetchone()[0]

        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""SELECT rj.*,
                    m.match_score, m.match_reasons, m.missing_skills,
                    m.salary_assessment, m.europe_score, m.travel_fund_score,
                    m.estimated_monthly_eur, m.match_explanation, m.matched_at
                FROM remote_jobs rj
                LEFT JOIN remote_job_matches m ON m.remote_job_id = rj.id
                WHERE {where}
                ORDER BY {sort_sql}
                LIMIT ? OFFSET ?""",
            params + [per_page, offset],
        ).fetchall()

    jobs = []
    for row in rows:
        j = dict(row)
        j["match_score"] = j.get("match_score") or j.get("quick_score") or 0
        # Parse JSON fields
        for field in ("match_reasons", "missing_skills"):
            raw = j.get(field)
            j[field] = json.loads(raw) if raw and raw.startswith("[") else []
        # Parse tags
        tags_raw = j.get("tags") or "[]"
        try:
            j["tags_list"] = json.loads(tags_raw) if tags_raw.startswith("[") else [tags_raw]
        except Exception:
            j["tags_list"] = []
        jobs.append(j)

    return {
        "jobs": jobs,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


def get_remote_job(job_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT rj.*,
                   m.match_score, m.match_reasons, m.missing_skills,
                   m.salary_assessment, m.europe_score, m.travel_fund_score,
                   m.estimated_monthly_eur, m.match_explanation, m.matched_at
               FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id = rj.id
               WHERE rj.id = ?""",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    j = dict(row)
    j["match_score"] = j.get("match_score") or j.get("quick_score") or 0
    for field in ("match_reasons", "missing_skills"):
        raw = j.get(field)
        j[field] = json.loads(raw) if raw and isinstance(raw, str) and raw.startswith("[") else []
    tags_raw = j.get("tags") or "[]"
    try:
        j["tags_list"] = json.loads(tags_raw) if tags_raw.startswith("[") else []
    except Exception:
        j["tags_list"] = []
    return j


def save_job(job_id: int) -> dict:
    """Toggle saved state. Returns new state."""
    with get_conn() as conn:
        current = conn.execute(
            "SELECT is_saved FROM remote_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not current:
            raise ValueError(f"Job {job_id} not found")
        new_state = 0 if current["is_saved"] else 1
        conn.execute(
            "UPDATE remote_jobs SET is_saved=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (new_state, job_id),
        )
    return {"job_id": job_id, "is_saved": bool(new_state)}


def hide_job(job_id: int) -> dict:
    """Hide a job (removes from default listing)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE remote_jobs SET is_hidden=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (job_id,),
        )
    return {"job_id": job_id, "is_hidden": True}


def update_job_status(job_id: int, status: str) -> dict:
    """Update application_status. Valid: new/saved/applied/interviewing/rejected/closed/offer."""
    valid = {"new", "saved", "applied", "interviewing", "rejected", "closed", "offer"}
    if status not in valid:
        raise ValueError(f"Invalid status '{status}'. Must be one of: {valid}")
    with get_conn() as conn:
        applied_at_sql = ", applied_at=CURRENT_TIMESTAMP" if status == "applied" else ""
        conn.execute(
            f"UPDATE remote_jobs SET application_status=?{applied_at_sql}, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, job_id),
        )
    return {"job_id": job_id, "application_status": status}


def update_job_tracker(
    job_id: int,
    notes: str | None = None,
    follow_up_date: str | None = None,
    resume_used: str | None = None,
    contact_person: str | None = None,
    salary_discussed: int | None = None,
) -> dict:
    """Update tracker fields for a remote job application."""
    fields = []
    params = []
    if notes is not None:
        fields.append("notes=?")
        params.append(notes)
    if follow_up_date is not None:
        fields.append("follow_up_date=?")
        params.append(follow_up_date)
    if resume_used is not None:
        fields.append("resume_used=?")
        params.append(resume_used)
    if contact_person is not None:
        fields.append("contact_person=?")
        params.append(contact_person)
    if salary_discussed is not None:
        fields.append("salary_discussed=?")
        params.append(salary_discussed)
    if not fields:
        return {"job_id": job_id, "updated": False}
    fields.append("updated_at=CURRENT_TIMESTAMP")
    params.append(job_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE remote_jobs SET {', '.join(fields)} WHERE id=?", params
        )
    return {"job_id": job_id, "updated": True}


# ─── Travel Fund Widget ───────────────────────────────────────────────────────

def get_travel_widget(top_n: int = 6) -> list[dict]:
    """
    Return top jobs ranked by travel_fund_score or computed travel value.
    These are the roles most likely to generate fast income for Europe travel.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT rj.id, rj.title, rj.company, rj.location, rj.job_type,
                      rj.salary_min, rj.salary_max, rj.salary_currency,
                      rj.hourly_rate_min, rj.hourly_rate_max,
                      rj.source_url, rj.application_status, rj.is_saved,
                      COALESCE(m.travel_fund_score, rj.quick_score) AS tfs,
                      COALESCE(m.estimated_monthly_eur, 0) AS monthly_eur,
                      m.match_score
               FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id = rj.id
               WHERE rj.is_hidden=0 AND rj.application_status NOT IN ('rejected','closed')
               ORDER BY tfs DESC, monthly_eur DESC
               LIMIT ?""",
            (top_n,),
        ).fetchall()

    result = []
    for row in rows:
        j = dict(row)
        # Compute estimated monthly if not AI-provided
        if not j["monthly_eur"]:
            j["monthly_eur"] = _estimated_monthly_eur(j) or 0
        result.append(j)
    return result


# ─── Stats ────────────────────────────────────────────────────────────────────

def get_discovery_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute(
            "SELECT COUNT(*) FROM remote_jobs WHERE is_hidden=0"
        ).fetchone()[0]
        saved = conn.execute(
            "SELECT COUNT(*) FROM remote_jobs WHERE is_saved=1 AND is_hidden=0"
        ).fetchone()[0]
        applied = conn.execute(
            "SELECT COUNT(*) FROM remote_jobs WHERE application_status='applied'"
        ).fetchone()[0]
        high_match = conn.execute(
            """SELECT COUNT(*) FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id=rj.id
               WHERE rj.is_hidden=0
               AND COALESCE(m.match_score, rj.quick_score) >= 70"""
        ).fetchone()[0]
        europe_count = conn.execute(
            "SELECT COUNT(*) FROM remote_jobs WHERE is_europe_friendly=1 AND is_hidden=0"
        ).fetchone()[0]
        last_sync_row = conn.execute(
            """SELECT created_at FROM automation_runs
               WHERE run_type='remote_job_sync' AND status='success'
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
    return {
        "total": total,
        "saved": saved,
        "applied": applied,
        "high_match": high_match,
        "europe_friendly": europe_count,
        "last_sync": last_sync_row["created_at"] if last_sync_row else None,
    }


def get_proposals_for_job(job_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM remote_proposals WHERE remote_job_id=? ORDER BY generated_at DESC",
            (job_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_applied_jobs(limit: int = 50) -> list[dict]:
    """Return jobs in applied/interviewing/offer stage for tracker view."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT rj.*, COALESCE(m.match_score, rj.quick_score) AS match_score
               FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id=rj.id
               WHERE rj.application_status IN ('applied','interviewing','offer','rejected','closed')
               ORDER BY rj.applied_at DESC, rj.updated_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
