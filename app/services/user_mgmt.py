"""User profile and onboarding management."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_conn


def get_user(user_id: int = 1) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_job_profile(user_id: int = 1) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM job_profile WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def get_travel_profile(user_id: int = 1) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM travel_profile WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else None


def update_job_profile(updates: dict[str, Any], user_id: int = 1) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    allowed = {
        "current_role", "current_company", "years_experience", "current_salary",
        "current_salary_currency", "target_roles", "target_countries",
        "target_salary_min", "target_salary_max", "target_salary_currency",
        "visa_status", "remote_preference", "relocation_readiness", "notes",
    }
    fields = {k: v for k, v in updates.items() if k in allowed}
    fields["updated_at"] = now
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM job_profile WHERE user_id=?", (user_id,)
        ).fetchone()
        if existing:
            set_clause = ", ".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE job_profile SET {set_clause} WHERE user_id=?",
                (*fields.values(), user_id),
            )
        else:
            fields["user_id"] = user_id
            cols = ", ".join(fields.keys())
            placeholders = ", ".join("?" for _ in fields)
            conn.execute(
                f"INSERT INTO job_profile ({cols}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
        row = conn.execute(
            "SELECT * FROM job_profile WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else {}


def update_travel_profile(updates: dict[str, Any], user_id: int = 1) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    allowed = {
        "home_city", "home_airport", "preferred_airlines", "loyalty_programs",
        "seat_preference", "hotel_preference", "typical_budget_range", "passport_countries",
    }
    fields = {k: v for k, v in updates.items() if k in allowed}
    fields["updated_at"] = now
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM travel_profile WHERE user_id=?", (user_id,)
        ).fetchone()
        if existing:
            set_clause = ", ".join(f"{k}=?" for k in fields)
            conn.execute(
                f"UPDATE travel_profile SET {set_clause} WHERE user_id=?",
                (*fields.values(), user_id),
            )
        else:
            fields["user_id"] = user_id
            cols = ", ".join(fields.keys())
            placeholders = ", ".join("?" for _ in fields)
            conn.execute(
                f"INSERT INTO travel_profile ({cols}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
        row = conn.execute(
            "SELECT * FROM travel_profile WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
    return dict(row) if row else {}


def complete_onboarding(user_id: int = 1) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET onboarding_complete=1, updated_at=? WHERE id=?",
            (datetime.utcnow().isoformat(), user_id),
        )


def get_executive_summary(user_id: int = 1) -> dict[str, Any]:
    """Aggregate summary for dashboard header."""
    from app.services.pipeline import get_pipeline_summary
    from app.services.reminders import get_visa_tracker, list_reminders, list_price_watches

    user = get_user(user_id)
    job_profile = get_job_profile(user_id)
    pipeline = get_pipeline_summary()
    visas = get_visa_tracker(user_id)
    reminders = list_reminders(user_id, "pending")
    price_watches = list_price_watches(user_id)

    due_reminders = [
        r for r in reminders
        if r["scheduled_for"] <= datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    ]

    return {
        "user": user,
        "job_profile": job_profile,
        "pipeline": pipeline,
        "visa_count": len(visas),
        "visa_active": visas[0] if visas else None,
        "reminders_pending": len(reminders),
        "reminders_due": len(due_reminders),
        "price_watches_active": len(price_watches),
    }
