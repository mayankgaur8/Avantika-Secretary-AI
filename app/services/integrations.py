"""Live provider integrations for jobs and travel."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

from app.db import get_conn
from app.services.jobs import compute_priority

logger = logging.getLogger("secretaryai.integrations")

DEFAULT_JOB_FEED_URLS = [
    "https://www.arbeitnow.com/api/job-board-api",
    "https://himalayas.app/jobs/api?limit=20",
]
ARBEITNOW_SOURCE = "arbeitnow.com"

# Strict: definitely Java/JVM
ROLE_KEYWORDS_STRICT = (
    "java", "spring", "spring boot", "kotlin", "jvm", "jakarta",
    "microservices", "technical lead", "software architect",
)
# Broad: any senior backend/software role (used as fallback when strict returns 0)
ROLE_KEYWORDS_BROAD = (
    "java", "spring", "kotlin", "jvm", "backend", "fullstack", "full-stack",
    "software engineer", "software developer", "senior engineer", "lead engineer",
    "engineering lead", "platform engineer", "cloud engineer", "api developer",
    "devops", "sre", "architect", "principal engineer", "tech lead",
)
ROLE_KEYWORDS = ROLE_KEYWORDS_STRICT  # default, overridden by two-stage logic

# Locations we target — includes "remote" and "worldwide" to avoid filtering too hard
LOCATION_KEYWORDS = (
    "germany", "berlin", "munich", "frankfurt", "hamburg", "stuttgart",
    "cologne", "düsseldorf", "dusseldorf", "remote", "worldwide", "anywhere",
    "europe", "dubai", "uae", "abu dhabi", "global", "international",
)

# ─── Salary Benchmarks ────────────────────────────────────────────────────────
_SALARY_GERMANY = {
    "principal": (105000, 135000),
    "architect": (100000, 130000),
    "head of": (110000, 140000),
    "manager": (95000, 120000),
    "lead": (90000, 115000),
    "senior": (80000, 100000),
    "engineer": (70000, 90000),
}
_SALARY_UAE = {  # AED annual (÷12 for monthly)
    "principal": (420000, 540000),
    "architect": (390000, 510000),
    "lead": (330000, 420000),
    "senior": (280000, 360000),
    "engineer": (240000, 300000),
}


def suggest_salary(role_title: str, country: str) -> tuple[int, int, str]:
    """Return (salary_min, salary_max, currency) based on role + country."""
    role_lower = (role_title or "").lower()
    country_lower = (country or "").lower()
    if "uae" in country_lower or "dubai" in country_lower or "emirates" in country_lower:
        guide = _SALARY_UAE
        currency = "AED"
    else:
        guide = _SALARY_GERMANY
        currency = "EUR"
    for keyword, (lo, hi) in guide.items():
        if keyword in role_lower:
            return lo, hi, currency
    return guide["senior"][0], guide["senior"][1], currency


def _log_run(run_type: str, status: str, details: str, target_id: int | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO automation_runs (run_type, target_id, status, details)
            VALUES (?, ?, ?, ?)
            """,
            (run_type, target_id, status, details),
        )


def _summarize_details(raw: str) -> str:
    try:
        payload = json.loads(raw)
    except Exception:
        compact = " ".join(raw.split())
        return compact[:260] + ("..." if len(compact) > 260 else "")

    if payload.get("user_message"):
        summary = f"status: {payload.get('status')} | user_message: {payload.get('user_message')}"
        if payload.get("total_items") is not None:
            summary += f" | total_items: {payload.get('total_items')}"
        if payload.get("live_offers") is not None:
            summary += f" | live_offers: {payload.get('live_offers')}"
        return summary[:260] + ("..." if len(summary) > 260 else "")

    parts = []
    for key in ("status", "user_message", "reason", "total_items", "live_offers", "url", "payload_path"):
        value = payload.get(key)
        if value not in (None, "", []):
            parts.append(f"{key}: {value}")
    if "sources" in payload and payload["sources"]:
        parts.append(f"sources: {len(payload['sources'])}")
    summary = " | ".join(parts) if parts else str(payload)
    return summary[:260] + ("..." if len(summary) > 260 else "")


def _extract_company(title: str) -> str:
    if " at " in title:
        return title.split(" at ", 1)[1].strip()
    if " - " in title:
        return title.split(" - ", 1)[0].strip()
    return "Unknown Company"


def _extract_role(title: str) -> str:
    if " at " in title:
        return title.split(" at ", 1)[0].strip()
    if " - " in title:
        return title.split(" - ", 1)[0].strip()
    return title.strip() or "Software Engineer"


def _location_country(location: str) -> tuple[str | None, str | None]:
    normalized = (location or "").strip()
    if not normalized:
        return None, None
    lowered = normalized.lower()
    if "germany" in lowered or any(city in lowered for city in ("berlin", "munich", "frankfurt", "hamburg", "stuttgart")):
        return "Germany", normalized
    if any(token in lowered for token in ("dubai", "uae", "united arab emirates", "abu dhabi")):
        return "UAE", normalized
    return None, normalized


def _insert_or_update_job(item: dict) -> bool:
    """Insert or update a job lead. Returns True if a brand-new row was inserted."""
    with get_conn() as conn:
        exists = conn.execute(
            """
            SELECT id FROM job_leads
            WHERE company = ? AND role_title = ? AND COALESCE(apply_url, '') = COALESCE(?, '')
            """,
            (item["company"], item["role_title"], item.get("apply_url")),
        ).fetchone()

        # Fill in salary from benchmark if not provided by feed
        s_min = item.get("salary_min")
        s_max = item.get("salary_max")
        s_cur = item.get("salary_currency")
        if not s_max:
            s_min, s_max, s_cur = suggest_salary(item.get("role_title", ""), item.get("country", ""))

        priority = compute_priority(item.get("match_score", 6.5), item.get("visa_support"), s_max)

        if exists:
            conn.execute(
                """
                UPDATE job_leads
                SET country = ?, city = ?, source = ?, salary_min = COALESCE(salary_min, ?),
                    salary_max = COALESCE(salary_max, ?), salary_currency = COALESCE(salary_currency, ?),
                    visa_support = ?, match_score = ?, credibility_score = ?,
                    priority_score = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    item.get("country"),
                    item.get("city"),
                    item.get("source"),
                    s_min, s_max, s_cur,
                    item.get("visa_support"),
                    item.get("match_score", 6.5),
                    item.get("credibility_score", 7.0),
                    priority,
                    item.get("notes"),
                    exists["id"],
                ),
            )
            return False
        else:
            conn.execute(
                """
                INSERT INTO job_leads (
                    company, role_title, country, city, source, apply_url,
                    salary_min, salary_max, salary_currency, visa_support,
                    match_score, credibility_score, priority_score, notes, review_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_review')
                """,
                (
                    item["company"],
                    item["role_title"],
                    item.get("country"),
                    item.get("city"),
                    item.get("source"),
                    item.get("apply_url"),
                    s_min, s_max, s_cur,
                    item.get("visa_support"),
                    item.get("match_score", 6.5),
                    item.get("credibility_score", 7.0),
                    priority,
                    item.get("notes"),
                ),
            )
            return True


def _job_dedup_key(item: dict) -> str:
    """Stable hash for deduplication across sources."""
    raw = f"{(item.get('company') or '').lower().strip()}|{(item.get('role_title') or '').lower().strip()}|{(item.get('apply_url') or '').strip()}"
    return hashlib.md5(raw.encode()).hexdigest()


def _score_job(item: dict) -> float:
    """Score 0–10 against Mayank's profile (Java Lead, Germany/UAE target)."""
    score = 5.0
    role_blob = f"{item.get('role_title','')} {item.get('notes','')} {item.get('company','')}".lower()
    location_blob = f"{item.get('country','')} {item.get('city','')}".lower()

    # Role relevance
    if any(k in role_blob for k in ("java", "spring")):
        score += 2.0
    if any(k in role_blob for k in ("lead", "principal", "architect", "head of")):
        score += 1.0
    if any(k in role_blob for k in ("microservices", "kafka", "aws", "kubernetes", "k8s")):
        score += 0.5
    # Location
    if any(k in location_blob for k in ("germany", "berlin", "munich", "frankfurt", "hamburg")):
        score += 1.5
    elif any(k in location_blob for k in ("dubai", "uae")):
        score += 1.0
    elif "remote" in location_blob:
        score += 0.5
    # Salary (if posted above target)
    salary_max = item.get("salary_max") or 0
    if salary_max >= 90000:
        score += 0.5
    return min(round(score, 1), 10.0)


def _allow_job_item(item: dict, strict: bool = True) -> bool:
    role_blob = " ".join(
        filter(None, [item.get("role_title"), item.get("notes"), item.get("company")])
    ).lower()
    location_blob = " ".join(filter(None, [item.get("country"), item.get("city")])).lower()
    keywords = ROLE_KEYWORDS_STRICT if strict else ROLE_KEYWORDS_BROAD
    role_match = any(keyword in role_blob for keyword in keywords)
    location_match = any(keyword in location_blob for keyword in LOCATION_KEYWORDS)
    return role_match and location_match


def _filter_two_stage(items: list[dict], source: str) -> list[dict]:
    """
    Stage 1: strict Java/JVM keyword filter.
    Stage 2: if strict returns 0, broaden to all senior backend roles and log.
    Sets match_score via _score_job on all passing items.
    """
    strict_pass = [i for i in items if _allow_job_item(i, strict=True)]
    if strict_pass:
        logger.info("Filter [%s] strict=%d/%d", source, len(strict_pass), len(items))
        for item in strict_pass:
            item["match_score"] = _score_job(item)
        return strict_pass

    broad_pass = [i for i in items if _allow_job_item(i, strict=False)]
    logger.warning(
        "Filter [%s] strict=0/%d — falling back to broad filter → %d results",
        source, len(items), len(broad_pass),
    )
    for item in broad_pass:
        item["match_score"] = max(5.0, _score_job(item) - 1.0)  # slight penalty for broad match
    return broad_pass


def _parse_arbeitnow(payload: dict, source: str) -> list[dict]:
    """Parse Arbeitnow job board API response (Germany-focused)."""
    records = payload.get("data", [])
    logger.info("Arbeitnow raw records: %d", len(records))
    raw = []
    for record in records[:100]:
        title = (record.get("title") or "").strip()
        company = (record.get("company_name") or "Unknown Company").strip()
        location = (record.get("location") or "Germany").strip()
        url = record.get("url")
        tags = record.get("tags") or []
        description = (record.get("description") or "")[:500]
        remote = record.get("remote", False)

        country = "Germany"
        city = location if location.lower() not in ("germany", "deutschland", "") else None

        raw.append({
            "company": company,
            "role_title": title,
            "country": country,
            "city": city,
            "source": source,
            "apply_url": url,
            "visa_support": "Unknown",
            "lead_type": "live_opening",
            "credibility_score": 7.5,
            "notes": f"{'[Remote] ' if remote else ''}{description}",
        })
    return _filter_two_stage(raw, source)


def _parse_rss(xml_text: str, source: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    raw = []
    for node in root.findall(".//item")[:50]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        description = (node.findtext("description") or "").strip()
        location_match = re.search(
            r"(Germany|Berlin|Munich|Frankfurt|Hamburg|Dubai|UAE|Abu Dhabi|Remote|Europe)",
            description, re.I,
        )
        location = location_match.group(1) if location_match else ""
        country, city = _location_country(location)
        raw.append({
            "company": _extract_company(title),
            "role_title": _extract_role(title),
            "country": country,
            "city": city,
            "source": source,
            "apply_url": link or None,
            "visa_support": "Unknown",
            "lead_type": "live_opening",
            "credibility_score": 7.0,
            "notes": description[:500],
        })
    logger.info("RSS [%s] raw records: %d", source, len(raw))
    return _filter_two_stage(raw, source)


def _parse_json_jobs(payload: object, source: str) -> list[dict]:
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict):
        records = payload.get("jobs") or payload.get("data") or payload.get("results") or []
    else:
        records = []
    logger.info("JSON [%s] raw records: %d", source, len(records))
    raw = []
    for record in records[:100]:
        title = str(record.get("title") or record.get("position") or "").strip()
        company = str(
            record.get("company_name") or record.get("companyName") or record.get("company")
            or _extract_company(title)
        ).strip()
        raw_location = record.get("candidate_required_location") or record.get("location")
        if not raw_location and record.get("locationRestrictions"):
            raw_location = ", ".join(str(i) for i in (record.get("locationRestrictions") or [])[:2])
        location = str(raw_location or "").strip()
        country, city = _location_country(location)
        apply_url = (
            record.get("url") or record.get("apply_url")
            or record.get("applicationLink") or record.get("applyUrl")
        )
        raw.append({
            "company": company or "Unknown Company",
            "role_title": title or "Software Engineer",
            "country": country,
            "city": city,
            "source": source,
            "apply_url": apply_url,
            "salary_min": record.get("minSalary") or record.get("salary_min"),
            "salary_max": record.get("maxSalary") or record.get("salary_max"),
            "salary_currency": record.get("currency") or record.get("salary_currency"),
            "visa_support": "Unknown",
            "lead_type": "live_opening",
            "credibility_score": 7.2,
            "notes": str(record.get("description") or record.get("summary") or record.get("excerpt") or "")[:500],
        })
    return _filter_two_stage(raw, source)


def sync_live_job_sources() -> dict:
    urls = [u.strip() for u in os.getenv("JOB_FEED_URLS", "").split(",") if u.strip()]
    if not urls:
        urls = DEFAULT_JOB_FEED_URLS[:]

    logger.info("Job sync starting | sources=%d | urls=%s", len(urls), urls)

    source_stats: list[dict] = []
    total_fetched = 0
    total_new = 0
    total_dupes = 0
    errors = 0

    with httpx.Client(timeout=25.0, follow_redirects=True) as client:
        for url in urls:
            source = urlparse(url).netloc or url
            logger.info("Fetching [%s] ...", source)
            try:
                resp = client.get(url)
                resp.raise_for_status()
            except Exception as exc:
                logger.error("Fetch failed [%s]: %s", source, exc)
                source_stats.append({"source": source, "fetched": 0, "new": 0, "dupes": 0, "error": str(exc)})
                errors += 1
                continue

            content_type = resp.headers.get("content-type", "")
            try:
                if ARBEITNOW_SOURCE in source:
                    items = _parse_arbeitnow(resp.json(), source)
                elif "xml" in content_type or resp.text.lstrip().startswith("<"):
                    items = _parse_rss(resp.text, source)
                else:
                    items = _parse_json_jobs(resp.json(), source)
            except Exception as exc:
                logger.error("Parse failed [%s]: %s", source, exc)
                source_stats.append({"source": source, "fetched": 0, "new": 0, "dupes": 0, "error": str(exc)})
                errors += 1
                continue

            src_new = 0
            src_dupes = 0
            for item in items:
                if _insert_or_update_job(item):
                    src_new += 1
                else:
                    src_dupes += 1

            logger.info(
                "Saved [%s] | filtered=%d | new=%d | dupes=%d",
                source, len(items), src_new, src_dupes,
            )
            source_stats.append({
                "source": source, "fetched": len(items), "new": src_new, "dupes": src_dupes,
            })
            total_fetched += len(items)
            total_new += src_new
            total_dupes += src_dupes

    result = {
        "sources": source_stats,
        "total_fetched": total_fetched,
        "total_new": total_new,
        "total_dupes": total_dupes,
        "errors": errors,
        # keep legacy key for existing callers
        "new_count": total_new,
        "total_items": total_fetched,
    }
    logger.info("Job sync complete | new=%d | dupes=%d | errors=%d", total_new, total_dupes, errors)
    _log_run("job_sync", "success", json.dumps(result))
    return result


def daily_job_digest() -> dict:
    """Run daily job sync and create an in-app reminder when new jobs are found."""
    try:
        result = sync_live_job_sources()
        new_count = result.get("new_count", 0)
        if new_count > 0:
            from datetime import datetime
            from app.services.reminders import create_reminder
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
            create_reminder(
                title=f"{new_count} new Java job{'s' if new_count > 1 else ''} found — review now",
                scheduled_for=now,
                message=f"Daily job sync found {new_count} new matching job(s). Visit /jobs/review to approve or skip.",
                reminder_type="job_alert",
                channel="app",
            )
        _log_run("daily_digest", "success", json.dumps(result))
        return result
    except Exception as exc:
        _log_run("daily_digest", "error", str(exc))
        raise


def _amadeus_access_token(client: httpx.Client) -> str:
    client_id = os.getenv("AMADEUS_CLIENT_ID")
    client_secret = os.getenv("AMADEUS_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError("AMADEUS_CLIENT_ID and AMADEUS_CLIENT_SECRET must be configured")

    response = client.post(
        "https://test.api.amadeus.com/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def search_live_travel_options(travel_request: dict) -> dict:
    if not travel_request.get("depart_date"):
        raise ValueError("Travel request must include depart_date for live search")

    with httpx.Client(timeout=25.0, follow_redirects=True) as client:
        token = _amadeus_access_token(client)
        params = {
            "originLocationCode": travel_request["origin"][:3].upper(),
            "destinationLocationCode": travel_request["destination"][:3].upper(),
            "departureDate": travel_request["depart_date"],
            "adults": travel_request["traveler_count"] or 1,
            "currencyCode": travel_request["currency"] or "EUR",
            "max": 5,
        }
        if travel_request.get("return_date"):
            params["returnDate"] = travel_request["return_date"]

        response = client.get(
            "https://test.api.amadeus.com/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        data = response.json().get("data", [])

    if not data:
        raise ValueError("No live travel offers returned by Amadeus")

    with get_conn() as conn:
        conn.execute("DELETE FROM travel_options WHERE travel_request_id = ?", (travel_request["id"],))
        for offer in data[:5]:
            itineraries = offer.get("itineraries", [])
            first = itineraries[0] if itineraries else {}
            segments = first.get("segments", [])
            price = int(float(offer["price"]["grandTotal"]))
            conn.execute(
                """
                INSERT INTO travel_options (
                    travel_request_id, provider, category, price, currency,
                    duration_hours, stops, baggage_included, cancellation_flexibility,
                    transfer_risk, summary, booking_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    travel_request["id"],
                    "Amadeus Live",
                    "live offer",
                    price,
                    offer["price"]["currency"],
                    0,
                    max(len(segments) - 1, 0),
                    1,
                    "Check fare rules",
                    "Medium",
                    f"Live fare with {len(segments)} segment(s)",
                    None,
                ),
            )

    result = {"travel_request_id": travel_request["id"], "live_offers": len(data[:5])}
    _log_run("travel_search", "success", json.dumps(result), travel_request["id"])
    return result


def list_automation_runs(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM automation_runs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["summary"] = _summarize_details(item.get("details") or "")
        result.append(item)
    return result


def clear_automation_runs() -> dict:
    with get_conn() as conn:
        deleted = conn.execute("SELECT COUNT(*) AS count FROM automation_runs").fetchone()["count"]
        conn.execute("DELETE FROM automation_runs")
    return {"deleted": deleted}


def get_last_sync_time(run_type: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT created_at
            FROM automation_runs
            WHERE run_type = ? AND status = 'success'
            ORDER BY id DESC
            LIMIT 1
            """,
            (run_type,),
        ).fetchone()
    return row["created_at"] if row else None
