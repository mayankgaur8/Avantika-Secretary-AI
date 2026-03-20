"""Live provider integrations for jobs and travel."""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import httpx

from app.db import get_conn
from app.services.jobs import compute_priority

DEFAULT_JOB_FEED_URLS = [
    "https://www.arbeitnow.com/api/job-board-api",
    "https://himalayas.app/jobs/api?limit=20",
]
ARBEITNOW_SOURCE = "arbeitnow.com"
ROLE_KEYWORDS = ("java", "spring", "backend", "software architect", "technical lead", "fullstack", "jvm", "kotlin")
LOCATION_KEYWORDS = ("germany", "berlin", "munich", "frankfurt", "hamburg", "stuttgart", "cologne", "düsseldorf", "remote", "dubai", "uae", "abu dhabi")

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


def _allow_job_item(item: dict) -> bool:
    role_blob = " ".join(
        filter(None, [item.get("role_title"), item.get("notes"), item.get("company")])
    ).lower()
    location_blob = " ".join(filter(None, [item.get("country"), item.get("city")])).lower()
    role_match = any(keyword in role_blob for keyword in ROLE_KEYWORDS)
    location_match = any(keyword in location_blob for keyword in LOCATION_KEYWORDS)
    return role_match and location_match


def _parse_arbeitnow(payload: dict, source: str) -> list[dict]:
    """Parse Arbeitnow job board API response (Germany-focused)."""
    records = payload.get("data", [])
    items = []
    for record in records[:80]:
        title = (record.get("title") or "").strip()
        company = (record.get("company_name") or "Unknown Company").strip()
        location = (record.get("location") or "Germany").strip()
        url = record.get("url")
        tags = record.get("tags") or []
        description = (record.get("description") or "")[:500]
        remote = record.get("remote", False)

        role_blob = f"{title} {' '.join(tags)} {description[:300]}".lower()
        if not any(kw in role_blob for kw in ROLE_KEYWORDS):
            continue

        country = "Germany"
        city = location if location.lower() not in ("germany", "deutschland", "") else None

        items.append({
            "company": company,
            "role_title": title,
            "country": country,
            "city": city,
            "source": source,
            "apply_url": url,
            "visa_support": "Unknown",
            "lead_type": "live_opening",
            "match_score": 7.2,
            "credibility_score": 7.5,
            "notes": f"{'[Remote] ' if remote else ''}{description}",
        })
    return items


def _parse_rss(xml_text: str, source: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for node in root.findall(".//item")[:25]:
        title = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        description = (node.findtext("description") or "").strip()
        location_match = re.search(r"(Germany|Berlin|Munich|Frankfurt|Hamburg|Dubai|UAE|Abu Dhabi)", description, re.I)
        location = location_match.group(1) if location_match else ""
        country, city = _location_country(location)
        items.append(
            {
                "company": _extract_company(title),
                "role_title": _extract_role(title),
                "country": country,
                "city": city,
                "source": source,
                "apply_url": link or None,
                "visa_support": "Unknown",
                "lead_type": "live_opening",
                "match_score": 6.5,
                "credibility_score": 7.0,
                "notes": description[:500],
            }
        )
    return [item for item in items if _allow_job_item(item)]


def _parse_json_jobs(payload: object, source: str) -> list[dict]:
    records = payload if isinstance(payload, list) else payload.get("jobs", []) if isinstance(payload, dict) else []
    items = []
    for record in records[:50]:
        title = str(record.get("title") or record.get("position") or "").strip()
        company = str(
            record.get("company_name")
            or record.get("companyName")
            or record.get("company")
            or _extract_company(title)
        ).strip()
        raw_location = record.get("candidate_required_location") or record.get("location")
        if not raw_location and record.get("locationRestrictions"):
            restrictions = record.get("locationRestrictions") or []
            raw_location = ", ".join(str(item) for item in restrictions[:2])
        location = str(raw_location or "").strip()
        country, city = _location_country(location)
        apply_url = (
            record.get("url")
            or record.get("apply_url")
            or record.get("applicationLink")
            or record.get("applyUrl")
        )
        salary_min = record.get("minSalary")
        salary_max = record.get("maxSalary")
        salary_currency = record.get("currency")
        notes = str(
            record.get("description")
            or record.get("summary")
            or record.get("excerpt")
            or ""
        )[:500]
        items.append(
            {
                "company": company or "Unknown Company",
                "role_title": title or "Software Engineer",
                "country": country,
                "city": city,
                "source": source,
                "apply_url": apply_url,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": salary_currency,
                "visa_support": "Unknown",
                "lead_type": "live_opening",
                "match_score": 6.8,
                "credibility_score": 7.2,
                "notes": notes,
            }
        )
    return [item for item in items if _allow_job_item(item)]


def sync_live_job_sources() -> dict:
    urls = [item.strip() for item in os.getenv("JOB_FEED_URLS", "").split(",") if item.strip()]
    if not urls:
        urls = DEFAULT_JOB_FEED_URLS[:]

    source_stats = []
    total_items = 0
    new_count = 0
    with httpx.Client(timeout=25.0, follow_redirects=True) as client:
        for url in urls:
            try:
                response = client.get(url)
                response.raise_for_status()
            except Exception as exc:
                source_stats.append({"source": url, "items": 0, "error": str(exc)})
                continue
            content_type = response.headers.get("content-type", "")
            source = urlparse(url).netloc or url
            if ARBEITNOW_SOURCE in source:
                items = _parse_arbeitnow(response.json(), source)
            elif "xml" in content_type or response.text.lstrip().startswith("<"):
                items = _parse_rss(response.text, source)
            else:
                items = _parse_json_jobs(response.json(), source)
            for item in items:
                if _insert_or_update_job(item):
                    new_count += 1
            source_stats.append({"source": source, "items": len(items)})
            total_items += len(items)

    result = {"sources": source_stats, "total_items": total_items, "new_count": new_count}
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
