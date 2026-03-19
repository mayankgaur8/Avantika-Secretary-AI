"""Playwright-backed browser automation for semi-automatic job applications."""

from __future__ import annotations

import json
import os
from pathlib import Path

from app.db import get_conn
from app.services.jobs import generate_application_draft, get_job_lead
from config import AUTOMATION_DIR, WORKSPACE


def _log_run(run_type: str, status: str, details: str, target_id: int | None = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO automation_runs (run_type, target_id, status, details)
            VALUES (?, ?, ?, ?)
            """,
            (run_type, target_id, status, details),
        )


def _payload_for_job(job_id: int) -> dict:
    job = get_job_lead(job_id)
    draft = generate_application_draft(job_id)
    return {
        "job_id": job_id,
        "apply_url": job.get("apply_url"),
        "company": job.get("company"),
        "role_title": job.get("role_title"),
        "candidate": {
            "full_name": "Mayank Gaur",
            "email": "mayankgaur.8@gmail.com",
            "phone": "+91 9620439138",
            "linkedin": "https://linkedin.com/in/mayank-gaur8/",
            "location": "Bengaluru, India",
        },
        "documents": {
            "resume_markdown": str(WORKSPACE / "resume_ATS_germany_english.md"),
            "resume_html": str(WORKSPACE / "resume_professional.html"),
        },
        "drafts": {
            "summary": draft["tailored_summary"],
            "keywords": draft["resume_keywords"],
            "recruiter_message": draft["recruiter_message"],
            "cover_letter": draft["cover_letter"],
            "form_answers": draft["form_answers"],
        },
    }


def build_browser_payload(job_id: int) -> dict:
    AUTOMATION_DIR.mkdir(parents=True, exist_ok=True)
    payload = _payload_for_job(job_id)
    payload_path = AUTOMATION_DIR / f"job_{job_id}_payload.json"
    payload_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return {"payload_path": str(payload_path), "job_id": job_id}


def _friendly_reason(exc_text: str) -> str:
    lowered = exc_text.lower()
    if "permission denied" in lowered or "operation not permitted" in lowered:
        return "Browser automation could not start because local browser permissions blocked Chromium in the current environment."
    if "playwright not available" in lowered:
        return "Browser automation is not available until Playwright is installed."
    if "no application url stored" in lowered:
        return "This lead does not have an application URL yet."
    if "net::" in lowered or "dns" in lowered:
        return "Browser automation could not reach the target site due to network or DNS restrictions."
    return "Browser automation could not complete on this site. The saved payload can still be used for manual follow-through."


async def run_browser_apply(job_id: int, headless: bool | None = None) -> dict:
    payload_meta = build_browser_payload(job_id)
    payload = json.loads(Path(payload_meta["payload_path"]).read_text(encoding="utf-8"))
    url = payload.get("apply_url")
    if not url:
        result = {
            "status": "blocked",
            "reason": "No application URL stored for this lead",
            "user_message": "This lead does not have an application URL yet.",
            "payload_path": payload_meta["payload_path"],
        }
        _log_run("browser_apply", "blocked", json.dumps(result), job_id)
        return result

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        result = {
            "status": "blocked",
            "reason": f"Playwright not available: {exc}",
            "user_message": "Browser automation is not available until Playwright is installed.",
            "payload_path": payload_meta["payload_path"],
        }
        _log_run("browser_apply", "blocked", json.dumps(result), job_id)
        return result

    headless = headless if headless is not None else os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless)
            page = await browser.new_page()
            await page.goto(url, wait_until="domcontentloaded")

            selectors = {
                "name": ["input[name*=name i]", "input[autocomplete=name]", "input[id*=name i]"],
                "email": ["input[type=email]", "input[name*=email i]", "input[id*=email i]"],
                "phone": ["input[type=tel]", "input[name*=phone i]", "input[id*=phone i]"],
                "linkedin": ["input[name*=linkedin i]", "input[id*=linkedin i]"],
                "cover_letter": ["textarea[name*=cover i]", "textarea[id*=cover i]", "textarea"],
            }

            async def fill_first(selector_list: list[str], value: str) -> None:
                for selector in selector_list:
                    locator = page.locator(selector).first
                    if await locator.count():
                        await locator.fill(value)
                        return

            await fill_first(selectors["name"], payload["candidate"]["full_name"])
            await fill_first(selectors["email"], payload["candidate"]["email"])
            await fill_first(selectors["phone"], payload["candidate"]["phone"])
            await fill_first(selectors["linkedin"], payload["candidate"]["linkedin"])
            await fill_first(selectors["cover_letter"], payload["drafts"]["cover_letter"])

            await browser.close()

        result = {
            "status": "completed",
            "job_id": job_id,
            "payload_path": payload_meta["payload_path"],
            "url": url,
            "user_message": "Browser automation opened the target page and filled common application fields.",
        }
        _log_run("browser_apply", "completed", json.dumps(result), job_id)
        return result
    except Exception as exc:
        friendly = _friendly_reason(str(exc))
        result = {
            "status": "prepared",
            "reason": str(exc),
            "user_message": friendly + " The application payload has been prepared for manual completion.",
            "job_id": job_id,
            "payload_path": payload_meta["payload_path"],
            "url": url,
        }
        _log_run("browser_apply", "prepared", json.dumps(result), job_id)
        return result
