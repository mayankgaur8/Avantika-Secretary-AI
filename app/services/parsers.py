"""Workspace document parsers used to seed the backend."""

from __future__ import annotations

import re
from pathlib import Path

from config import DOCS

TARGET_HEADER_RE = re.compile(r"^###\s+\d+\.\s+(.+)$", re.MULTILINE)


def _extract_field(block: str, label: str) -> str:
    match = re.search(rf"\*\*{re.escape(label)}:\*\*\s*(.+)", block)
    return match.group(1).strip() if match else ""


def _parse_salary(raw_salary: str) -> tuple[int | None, int | None, str | None]:
    if not raw_salary:
        return None, None, None

    currency = None
    if "€" in raw_salary:
        currency = "EUR"
    elif "AED" in raw_salary:
        currency = "AED"

    numbers = [int(num.replace(",", "")) for num in re.findall(r"(\d[\d,]*)", raw_salary)]
    if not numbers:
        return None, None, currency
    if len(numbers) == 1:
        return numbers[0], numbers[0], currency
    return numbers[0], numbers[1], currency


def parse_target_companies(path: Path | None = None) -> list[dict]:
    path = path or DOCS["target_companies"]
    text = path.read_text(encoding="utf-8")

    headers = list(TARGET_HEADER_RE.finditer(text))
    results: list[dict] = []
    current_region = "Germany"

    for index, header in enumerate(headers):
        start = header.start()
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        block = text[start:end]
        company = header.group(1).strip()

        if "## DUBAI" in text[:start]:
            current_region = "UAE"

        location = _extract_field(block, "Location")
        city = location.split("/")[0].strip() if location else None
        salary_min, salary_max, salary_currency = _parse_salary(_extract_field(block, "Typical Salary"))
        score_raw = _extract_field(block, "Match Score")
        score = float(score_raw.split("/")[0]) if score_raw else 0.0
        visa_support = _extract_field(block, "Visa Sponsorship") or "Unknown"
        apply_text = _extract_field(block, "How to Apply")
        apply_url = ""
        domain_match = re.search(r"([A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s|]+)?)", apply_text)
        if domain_match:
            apply_url = "https://" + domain_match.group(1).rstrip(".")

        notes = _extract_field(block, "Notes")
        why = _extract_field(block, "Why Mayank")
        note_blob = "\n".join(part for part in (why, notes) if part)

        if company.startswith("DUBAI"):
            current_region = "UAE"

        results.append(
            {
                "company": company,
                "role_title": "Senior Java Lead / Architect",
                "country": "UAE" if current_region == "UAE" else "Germany",
                "city": city,
                "source": "workspace_target_companies",
                "apply_url": apply_url or None,
                "salary_min": salary_min,
                "salary_max": salary_max,
                "salary_currency": salary_currency,
                "visa_support": visa_support,
                "match_score": score,
                "notes": note_blob,
            }
        )

    return results
