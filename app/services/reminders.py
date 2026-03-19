"""Reminders and visa tracker service."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_conn


# ─── Reminders ────────────────────────────────────────────────────────────────

def list_reminders(user_id: int = 1, status: str = "pending") -> list[dict[str, Any]]:
    with get_conn() as conn:
        if status == "all":
            rows = conn.execute(
                "SELECT * FROM reminders WHERE user_id=? ORDER BY scheduled_for ASC",
                (user_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM reminders WHERE user_id=? AND status=? ORDER BY scheduled_for ASC",
                (user_id, status),
            ).fetchall()
    return [dict(r) for r in rows]


def create_reminder(
    title: str,
    scheduled_for: str,
    message: str = "",
    reminder_type: str = "custom",
    channel: str = "whatsapp",
    related_entity_type: str = "",
    related_entity_id: int | None = None,
    user_id: int = 1,
) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO reminders
               (user_id, reminder_type, title, message, scheduled_for, channel,
                related_entity_type, related_entity_id, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (user_id, reminder_type, title, message, scheduled_for, channel,
             related_entity_type, related_entity_id, "pending", now),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM reminders WHERE id=?", (row_id,)).fetchone()
    return dict(row)


def mark_sent(reminder_id: int) -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE reminders SET status='sent', sent_at=? WHERE id=?",
            (now, reminder_id),
        )


def delete_reminder(reminder_id: int) -> bool:
    with get_conn() as conn:
        conn.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))
    return True


def get_due_reminders(user_id: int = 1) -> list[dict[str, Any]]:
    """Get reminders due now or overdue."""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM reminders
               WHERE user_id=? AND status='pending'
               AND scheduled_for <= ?
               ORDER BY scheduled_for ASC""",
            (user_id, now),
        ).fetchall()
    return [dict(r) for r in rows]


# ─── Visa Tracker ─────────────────────────────────────────────────────────────

def get_visa_tracker(user_id: int = 1) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM visa_tracker WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        result = []
        for r in rows:
            v = dict(r)
            checklist = conn.execute(
                "SELECT * FROM visa_checklist WHERE visa_tracker_id=? ORDER BY sort_order",
                (v["id"],),
            ).fetchall()
            v["checklist"] = [dict(c) for c in checklist]
            v["progress"] = _checklist_progress(v["checklist"])
            result.append(v)
    return result


def _checklist_progress(checklist: list[dict]) -> dict[str, Any]:
    total = len(checklist)
    done = sum(1 for c in checklist if c["completed"])
    pct = int((done / total) * 100) if total else 0
    return {"total": total, "done": done, "percent": pct}


def toggle_checklist_item(item_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM visa_checklist WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise ValueError("Checklist item not found")
        new_val = 0 if row["completed"] else 1
        conn.execute(
            "UPDATE visa_checklist SET completed=? WHERE id=?",
            (new_val, item_id),
        )
        row = conn.execute("SELECT * FROM visa_checklist WHERE id=?", (item_id,)).fetchone()
    return dict(row)


def add_visa(
    visa_type: str,
    target_country: str,
    application_status: str = "preparing",
    notes: str = "",
    user_id: int = 1,
) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO visa_tracker
               (user_id, visa_type, target_country, application_status, notes, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (user_id, visa_type, target_country, application_status, notes, now, now),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM visa_tracker WHERE id=?", (row_id,)).fetchone()
    return dict(row)


def update_visa_status(visa_id: int, status: str, notes: str = "") -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        if notes:
            conn.execute(
                "UPDATE visa_tracker SET application_status=?, notes=?, updated_at=? WHERE id=?",
                (status, notes, now, visa_id),
            )
        else:
            conn.execute(
                "UPDATE visa_tracker SET application_status=?, updated_at=? WHERE id=?",
                (status, now, visa_id),
            )
        row = conn.execute("SELECT * FROM visa_tracker WHERE id=?", (visa_id,)).fetchone()
    return dict(row) if row else {}


# ─── Price Watches ────────────────────────────────────────────────────────────

def list_price_watches(user_id: int = 1) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM price_watches WHERE user_id=? AND active=1 ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_price_watch(
    route_or_property: str,
    watch_type: str = "flight",
    date_range: str = "",
    target_price: int = 0,
    currency: str = "INR",
    user_id: int = 1,
) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO price_watches
               (user_id, watch_type, route_or_property, date_range, target_price, currency, active, created_at)
               VALUES (?,?,?,?,?,?,1,?)""",
            (user_id, watch_type, route_or_property, date_range, target_price, currency, now),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = conn.execute("SELECT * FROM price_watches WHERE id=?", (row_id,)).fetchone()
    return dict(row)


def deactivate_price_watch(watch_id: int) -> bool:
    with get_conn() as conn:
        conn.execute("UPDATE price_watches SET active=0 WHERE id=?", (watch_id,))
    return True
