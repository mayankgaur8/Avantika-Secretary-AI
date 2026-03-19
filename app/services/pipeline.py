"""Job pipeline service — Kanban stage management and pipeline analytics."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.db import get_conn

PIPELINE_STAGES = ["Identified", "Applied", "Responded", "Interview", "Offer", "Rejected", "Archived"]

STAGE_COLORS = {
    "Identified": "#6366f1",
    "Applied": "#3b82f6",
    "Responded": "#06b6d4",
    "Interview": "#f59e0b",
    "Offer": "#10b981",
    "Rejected": "#ef4444",
    "Archived": "#6b7280",
}


def get_pipeline_summary() -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pipeline_stage, COUNT(*) as cnt FROM job_leads GROUP BY pipeline_stage"
        ).fetchall()
        counts = {r["pipeline_stage"]: r["cnt"] for r in rows}
        total = sum(counts.values())
        active = sum(counts.get(s, 0) for s in ["Applied", "Responded", "Interview", "Offer"])
        offers = counts.get("Offer", 0)
        interviews = counts.get("Interview", 0)
        follow_ups = conn.execute(
            """SELECT COUNT(*) as cnt FROM job_leads
               WHERE pipeline_stage IN ('Applied','Responded')
               AND (next_action_due IS NULL OR next_action_due <= date('now', '+3 days'))
               AND pipeline_stage != 'Rejected' AND pipeline_stage != 'Archived'"""
        ).fetchone()["cnt"]
    return {
        "total": total,
        "active": active,
        "offers": offers,
        "interviews": interviews,
        "follow_ups_due": follow_ups,
        "by_stage": counts,
    }


def get_kanban_board() -> list[dict[str, Any]]:
    """Return pipeline grouped by stage for Kanban rendering."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT j.*, ad.tailored_summary, ad.cover_letter
               FROM job_leads j
               LEFT JOIN application_drafts ad ON ad.job_lead_id = j.id
               WHERE j.pipeline_stage != 'Archived'
               ORDER BY j.priority_score DESC, j.created_at DESC"""
        ).fetchall()

    jobs = [dict(r) for r in rows]
    board = []
    for stage in PIPELINE_STAGES:
        if stage == "Archived":
            continue
        board.append({
            "stage": stage,
            "color": STAGE_COLORS[stage],
            "cards": [j for j in jobs if j.get("pipeline_stage") == stage],
        })
    return board


def move_pipeline_stage(job_id: int, new_stage: str) -> dict[str, Any]:
    if new_stage not in PIPELINE_STAGES:
        raise ValueError(f"Invalid stage: {new_stage}")
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE job_leads SET pipeline_stage=?, last_action_date=?, updated_at=?
               WHERE id=?""",
            (new_stage, now[:10], now, job_id),
        )
        # Auto-set follow-up due date when moving to Applied
        if new_stage == "Applied":
            follow_up = (datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d")
            conn.execute(
                "UPDATE job_leads SET applied_date=?, next_action='Follow-up email', next_action_due=? WHERE id=?",
                (now[:10], follow_up, job_id),
            )
        row = conn.execute("SELECT * FROM job_leads WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else {}


def update_job_lead(job_id: int, updates: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "pipeline_stage", "status", "next_action", "next_action_due",
        "contact_name", "contact_email", "notes", "applied_date",
    }
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        raise ValueError("No valid fields to update")
    fields["updated_at"] = datetime.utcnow().isoformat()
    set_clause = ", ".join(f"{k}=?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE job_leads SET {set_clause} WHERE id=?",
            (*fields.values(), job_id),
        )
        row = conn.execute("SELECT * FROM job_leads WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else {}


def add_to_pipeline(
    company: str,
    role_title: str,
    country: str = "",
    city: str = "",
    apply_url: str = "",
    salary_min: int = 0,
    salary_max: int = 0,
    salary_currency: str = "EUR",
    match_score: float = 0.75,
    notes: str = "",
    stage: str = "Identified",
) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        try:
            conn.execute(
                """INSERT INTO job_leads
                   (company, role_title, country, city, apply_url, salary_min, salary_max,
                    salary_currency, match_score, pipeline_stage, status, notes, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (company, role_title, country, city, apply_url, salary_min, salary_max,
                 salary_currency, match_score, stage, "shortlisted", notes, now, now),
            )
            row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            row = conn.execute("SELECT * FROM job_leads WHERE id=?", (row_id,)).fetchone()
            return dict(row)
        except Exception:
            row = conn.execute(
                "SELECT * FROM job_leads WHERE company=? AND role_title=?",
                (company, role_title),
            ).fetchone()
            return dict(row) if row else {}


def get_follow_ups_due() -> list[dict[str, Any]]:
    """Jobs that need a follow-up action in the next 3 days."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM job_leads
               WHERE pipeline_stage IN ('Applied', 'Responded')
               AND next_action_due <= date('now', '+3 days')
               AND pipeline_stage NOT IN ('Rejected', 'Archived')
               ORDER BY next_action_due ASC
               LIMIT 20"""
        ).fetchall()
    return [dict(r) for r in rows]


def get_pipeline_job(job_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM job_leads WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def get_archived_jobs(limit: int = 20) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM job_leads WHERE pipeline_stage='Archived' ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]
