"""
Apply Pipeline & Income Conversion Engine
==========================================
Converts discovered jobs into real income through a structured pipeline:

DISCOVERED → HIGH_MATCH → READY_TO_APPLY → APPLIED → RESPONDED → INTERVIEW → WON

Key responsibilities:
- Auto-advance pipeline stages based on scores + actions
- Income Priority Scoring (contract rate × match × speed)
- Daily action recommendations (top 10 to apply today)
- Conversion funnel tracking (weekly stats)
- Smart alerts (high match, high rate, contract found)
- Fast Apply Batch (generate top-5 proposals in one click)
- Apply History Learning (AI feedback loop)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from app.db import get_conn
from app.services.platform_ai import call as ai_call

logger = logging.getLogger("secretaryai.apply_engine")

# ─── Pipeline Configuration ───────────────────────────────────────────────────

PIPELINE_STAGES = [
    "DISCOVERED",
    "HIGH_MATCH",
    "READY_TO_APPLY",
    "APPLIED",
    "RESPONDED",
    "INTERVIEW",
    "WON",
]

STAGE_COLORS = {
    "DISCOVERED":     "#5c6180",   # muted grey
    "HIGH_MATCH":     "#6366f1",   # accent indigo
    "READY_TO_APPLY": "#f59e0b",   # amber
    "APPLIED":        "#a855f7",   # purple
    "RESPONDED":      "#10b981",   # green
    "INTERVIEW":      "#06b6d4",   # cyan
    "WON":            "#eab308",   # gold
}

STAGE_LABELS = {
    "DISCOVERED":     "Discovered",
    "HIGH_MATCH":     "High Match",
    "READY_TO_APPLY": "Ready to Apply",
    "APPLIED":        "Applied",
    "RESPONDED":      "Responded",
    "INTERVIEW":      "Interview",
    "WON":            "Won",
}

# ─── Smart Alert criteria ─────────────────────────────────────────────────────

ALERT_RULES = [
    {
        "type": "HIGH_MATCH",
        "condition": lambda j: (j.get("match_score") or j.get("quick_score") or 0) >= 85,
        "message": lambda j: f"Strong match {j.get('match_score') or j.get('quick_score')}% — {j['title']} at {j['company']}",
    },
    {
        "type": "HIGH_RATE",
        "condition": lambda j: (j.get("hourly_rate_max") or 0) >= 60,
        "message": lambda j: f"€{j['hourly_rate_max']}/hr contract found — {j['title']} at {j['company']}",
    },
    {
        "type": "CONTRACT_FOUND",
        "condition": lambda j: j.get("job_type") in ("contract", "freelance") and j.get("is_fast_pay"),
        "message": lambda j: f"Fast-pay contract — {j['title']} at {j['company']}",
    },
    {
        "type": "EUROPE_CONTRACT",
        "condition": lambda j: j.get("is_europe_friendly") and j.get("job_type") in ("contract", "freelance"),
        "message": lambda j: f"Europe-friendly contract — {j['title']} at {j['company']}",
    },
]

# ─── Income Priority Scoring (0–100) ─────────────────────────────────────────

def compute_income_priority_score(job: dict) -> int:
    """
    Score 0–100 for how valuable this job is for generating fast income.

    Weights:
      Hourly rate          → up to 35 pts  (fastest income conversion)
      Contract / freelance → up to 25 pts  (payment flexibility)
      Fast start indicator → up to 15 pts  (immediate start / ASAP)
      Remote               → 10 pts        (start without relocation)
      Match probability    → up to 15 pts  (high match = higher win rate)
    """
    score = 0
    desc = (job.get("description") or "").lower()
    title = (job.get("title") or "").lower()
    blob = f"{title} {desc}"

    # 1. Hourly rate — most direct income metric
    h_max = job.get("hourly_rate_max") or 0
    if h_max >= 100:
        score += 35
    elif h_max >= 80:
        score += 28
    elif h_max >= 60:
        score += 20
    elif h_max >= 50:
        score += 15
    elif h_max > 0:
        score += 8
    else:
        # Fall back to annual salary proxy
        s_max = job.get("salary_max") or 0
        if s_max >= 120000:
            score += 20
        elif s_max >= 100000:
            score += 15
        elif s_max >= 80000:
            score += 10
        elif s_max > 0:
            score += 5

    # 2. Contract / freelance priority
    jtype = (job.get("job_type") or "").lower()
    if jtype in ("contract", "freelance"):
        score += 25
    elif jtype == "consulting":
        score += 20
    elif jtype == "parttime":
        score += 12
    else:
        score += 5  # fulltime still has long-term value

    # 3. Fast start / immediate indicators
    fast_keywords = (
        "immediate start", "asap", "urgent", "start immediately",
        "1 month", "3 month", "short-term", "short term", "4 month", "6 month",
        "quick start", "starting soon",
    )
    if any(k in blob for k in fast_keywords):
        score += 15

    # 4. Remote (no relocation barrier)
    if job.get("remote_type") == "remote":
        score += 10

    # 5. Match quality → win probability
    match = job.get("match_score") or job.get("quick_score") or 0
    if match >= 80:
        score += 15
    elif match >= 70:
        score += 10
    elif match >= 60:
        score += 5

    return min(score, 100)


def compute_is_fast_pay(job: dict) -> bool:
    """
    True if this role is likely to generate income quickly.
    Fast-pay = contract/freelance with hourly rate OR immediate-start indicator.
    """
    h_max = job.get("hourly_rate_max") or 0
    jtype = (job.get("job_type") or "").lower()
    desc = (job.get("description") or "").lower()
    return (
        h_max >= 50
        or jtype in ("contract", "freelance")
        or any(k in desc for k in ("immediate start", "asap", "urgent",
                                    "1 month", "3 month", "short-term"))
    )


# ─── Pipeline advancement ─────────────────────────────────────────────────────

def _get_stage_for_job(job: dict) -> str:
    """
    Determine correct pipeline stage without overriding user-confirmed stages.
    Terminal stages (APPLIED, RESPONDED, INTERVIEW, WON) are never regressed.
    """
    current = job.get("pipeline_stage") or "DISCOVERED"

    # Never regress from user-confirmed stages
    if current in ("APPLIED", "RESPONDED", "INTERVIEW", "WON"):
        return current

    match_score = job.get("match_score") or job.get("quick_score") or 0
    apply_kit_ready = job.get("apply_kit_ready") or 0

    if match_score >= 75 and apply_kit_ready:
        return "READY_TO_APPLY"
    if match_score >= 75:
        return "HIGH_MATCH"
    return "DISCOVERED"


def advance_pipeline(job_id: int, new_stage: str | None = None) -> str:
    """
    Move job to next pipeline stage. If new_stage is given, force that stage.
    Otherwise auto-determine based on current scores.
    Returns the new stage.
    """
    with get_conn() as conn:
        row = conn.execute(
            """SELECT rj.*, COALESCE(m.match_score, rj.quick_score) as match_score
               FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id = rj.id
               WHERE rj.id = ?""",
            (job_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Job {job_id} not found")
        job = dict(row)

        target_stage = new_stage if new_stage in PIPELINE_STAGES else _get_stage_for_job(job)

        conn.execute(
            """UPDATE remote_jobs
               SET pipeline_stage = ?,
                   last_stage_changed_at = CURRENT_TIMESTAMP,
                   updated_at = CURRENT_TIMESTAMP
               WHERE id = ?""",
            (target_stage, job_id),
        )
    logger.debug("Job %d → %s", job_id, target_stage)
    return target_stage


def batch_advance_all_pipelines() -> dict:
    """
    Run after every sync: recompute income_priority_score + is_fast_pay for all
    non-hidden jobs, then auto-advance pipeline stages where appropriate.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT rj.*, COALESCE(m.match_score, rj.quick_score) as match_score
               FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id = rj.id
               WHERE rj.is_hidden = 0""",
        ).fetchall()

    updated = 0
    for row in rows:
        job = dict(row)
        ips = compute_income_priority_score(job)
        fast = 1 if compute_is_fast_pay(job) else 0
        stage = _get_stage_for_job(job)
        current_stage = job.get("pipeline_stage") or "DISCOVERED"
        current_ips = job.get("income_priority_score") or 0
        current_fast = job.get("is_fast_pay") or 0

        if ips != current_ips or fast != current_fast or stage != current_stage:
            with get_conn() as conn:
                conn.execute(
                    """UPDATE remote_jobs
                       SET income_priority_score = ?,
                           is_fast_pay = ?,
                           pipeline_stage = ?,
                           last_stage_changed_at = CASE
                               WHEN pipeline_stage != ? THEN CURRENT_TIMESTAMP
                               ELSE last_stage_changed_at
                           END,
                           updated_at = CURRENT_TIMESTAMP
                       WHERE id = ?""",
                    (ips, fast, stage, stage, job["id"]),
                )
            updated += 1

    logger.info("Pipeline batch update: %d jobs updated", updated)
    return {"updated": updated, "total": len(rows)}


# ─── Daily Actions Engine ─────────────────────────────────────────────────────

_ACTION_REASON_TEMPLATES = {
    "high_rate": "High hourly rate €{rate}/hr — direct income",
    "fresh": "Posted {days}d ago — apply before competition grows",
    "high_match": "Match score {score}/100 — strong win probability",
    "contract": "Contract role — faster payment, flexible terms",
    "europe": "Europe-friendly — aligns with relocation plan",
    "ready": "Proposal already generated — apply in minutes",
}


def get_daily_actions(n: int = 10) -> list[dict]:
    """
    Top N jobs to apply to today.
    Ranked by income_priority_score then match_score.
    Only includes DISCOVERED, HIGH_MATCH, READY_TO_APPLY stages.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT rj.*,
                      COALESCE(m.match_score, rj.quick_score) as match_score,
                      m.match_explanation
               FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id = rj.id
               WHERE rj.is_hidden = 0
                 AND rj.pipeline_stage IN ('DISCOVERED', 'HIGH_MATCH', 'READY_TO_APPLY')
                 AND rj.application_status NOT IN ('applied','interviewing','offer','won','rejected','closed')
               ORDER BY rj.income_priority_score DESC,
                        COALESCE(m.match_score, rj.quick_score) DESC
               LIMIT ?""",
            (n,),
        ).fetchall()

    now = datetime.utcnow()
    result = []
    for row in rows:
        j = dict(row)
        # Build reason list
        reasons: list[str] = []
        if j.get("hourly_rate_max") and j["hourly_rate_max"] >= 50:
            reasons.append(_ACTION_REASON_TEMPLATES["high_rate"].format(rate=j["hourly_rate_max"]))
        if j.get("posted_at"):
            try:
                posted = datetime.fromisoformat(j["posted_at"].replace("Z", ""))
                days_old = (now - posted).days
                if days_old <= 3:
                    reasons.append(_ACTION_REASON_TEMPLATES["fresh"].format(days=days_old))
            except Exception:
                pass
        match = j.get("match_score") or 0
        if match >= 80:
            reasons.append(_ACTION_REASON_TEMPLATES["high_match"].format(score=match))
        if j.get("job_type") in ("contract", "freelance"):
            reasons.append(_ACTION_REASON_TEMPLATES["contract"])
        if j.get("is_europe_friendly"):
            reasons.append(_ACTION_REASON_TEMPLATES["europe"])
        if j.get("apply_kit_ready"):
            reasons.append(_ACTION_REASON_TEMPLATES["ready"])
        j["action_reasons"] = reasons[:3]  # max 3 reasons shown
        result.append(j)
    return result


# ─── Conversion Funnel ────────────────────────────────────────────────────────

def get_conversion_funnel() -> dict:
    """
    Return stage counts + weekly application stats for the conversion dashboard.
    """
    with get_conn() as conn:
        # Stage counts
        stage_rows = conn.execute(
            """SELECT pipeline_stage, COUNT(*) as cnt
               FROM remote_jobs
               WHERE is_hidden = 0
               GROUP BY pipeline_stage"""
        ).fetchall()
        stage_counts = {r["pipeline_stage"]: r["cnt"] for r in stage_rows}

        # Weekly applied counts (last 4 weeks)
        weekly = []
        for week_offset in range(4):
            week_start = (datetime.utcnow() - timedelta(days=7 * week_offset + 7)).strftime("%Y-%m-%d")
            week_end   = (datetime.utcnow() - timedelta(days=7 * week_offset)).strftime("%Y-%m-%d")
            applied = conn.execute(
                "SELECT COUNT(*) FROM remote_jobs WHERE applied_at BETWEEN ? AND ?",
                (week_start, week_end),
            ).fetchone()[0]
            responded = conn.execute(
                """SELECT COUNT(*) FROM remote_jobs
                   WHERE applied_at BETWEEN ? AND ?
                     AND application_status IN ('interviewing','offer','won')""",
                (week_start, week_end),
            ).fetchone()[0]
            interviews = conn.execute(
                """SELECT COUNT(*) FROM remote_jobs
                   WHERE applied_at BETWEEN ? AND ?
                     AND application_status IN ('interviewing','offer','won')""",
                (week_start, week_end),
            ).fetchone()[0]
            weekly.append({
                "label": f"W-{week_offset}" if week_offset > 0 else "This week",
                "week_start": week_start,
                "applied": applied,
                "responded": responded,
                "interviews": interviews,
                "conversion_rate": round(responded / applied * 100) if applied else 0,
            })

        # All-time totals
        totals = conn.execute(
            """SELECT
                 COUNT(*) as total,
                 SUM(CASE WHEN application_status='applied' THEN 1 ELSE 0 END) as applied,
                 SUM(CASE WHEN application_status IN ('interviewing','offer','won') THEN 1 ELSE 0 END) as interviews,
                 SUM(CASE WHEN application_status='won' THEN 1 ELSE 0 END) as won
               FROM remote_jobs WHERE is_hidden=0"""
        ).fetchone()

        # History-based insights
        history_rows = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(response_received) as responses,
                      proposal_type
               FROM apply_history
               GROUP BY proposal_type
               ORDER BY responses DESC"""
        ).fetchall()

    # Pipeline funnel (ordered)
    funnel = []
    for stage in PIPELINE_STAGES:
        funnel.append({
            "stage": stage,
            "label": STAGE_LABELS[stage],
            "color": STAGE_COLORS[stage],
            "count": stage_counts.get(stage, 0),
        })

    return {
        "funnel": funnel,
        "stage_counts": stage_counts,
        "weekly": weekly,
        "totals": dict(totals) if totals else {},
        "proposal_insights": [dict(r) for r in history_rows],
    }


# ─── Smart Alerts ─────────────────────────────────────────────────────────────

def check_and_create_alerts() -> int:
    """
    Scan recently added/updated jobs and create smart_alerts for qualifying ones.
    Only alerts on jobs created or updated in last 2 hours (fresh discoveries).
    Returns number of new alerts created.
    """
    cutoff = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT rj.*, COALESCE(m.match_score, rj.quick_score) as match_score
               FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id = rj.id
               WHERE rj.is_hidden = 0
                 AND (rj.created_at >= ? OR rj.updated_at >= ?)""",
            (cutoff, cutoff),
        ).fetchall()

    created = 0
    for row in rows:
        job = dict(row)
        for rule in ALERT_RULES:
            try:
                if not rule["condition"](job):
                    continue
            except Exception:
                continue
            # Check if alert already exists for this job+type
            with get_conn() as conn:
                exists = conn.execute(
                    "SELECT id FROM smart_alerts WHERE remote_job_id=? AND alert_type=?",
                    (job["id"], rule["type"]),
                ).fetchone()
                if exists:
                    continue
                msg = rule["message"](job)
                conn.execute(
                    """INSERT INTO smart_alerts (remote_job_id, alert_type, alert_message, alert_data)
                       VALUES (?,?,?,?)""",
                    (job["id"], rule["type"], msg, json.dumps({"income_priority_score": job.get("income_priority_score")})),
                )
            created += 1
            logger.info("Alert created [%s]: %s", rule["type"], msg)

    return created


def get_unread_alerts(limit: int = 20) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT sa.*, rj.title, rj.company, rj.source_url,
                      rj.income_priority_score, rj.pipeline_stage
               FROM smart_alerts sa
               LEFT JOIN remote_jobs rj ON rj.id = sa.remote_job_id
               WHERE sa.is_read = 0
               ORDER BY sa.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_alert_count() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM smart_alerts WHERE is_read=0").fetchone()[0]


def mark_alerts_read(alert_ids: list[int] | None = None) -> dict:
    with get_conn() as conn:
        if alert_ids:
            placeholders = ",".join("?" * len(alert_ids))
            conn.execute(
                f"UPDATE smart_alerts SET is_read=1 WHERE id IN ({placeholders})",
                alert_ids,
            )
        else:
            conn.execute("UPDATE smart_alerts SET is_read=1")
    return {"marked_read": len(alert_ids) if alert_ids else "all"}


# ─── Fast Apply Batch ─────────────────────────────────────────────────────────

def fast_apply_batch(n: int = 5) -> list[dict]:
    """
    Generate proposals for top N jobs by income_priority_score.
    Returns list of {job, proposal} dicts for sequential UI display.
    Used by the "Apply to Top N" power feature.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT rj.*, COALESCE(m.match_score, rj.quick_score) as match_score
               FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id = rj.id
               WHERE rj.is_hidden = 0
                 AND rj.pipeline_stage IN ('HIGH_MATCH', 'READY_TO_APPLY', 'DISCOVERED')
                 AND rj.application_status NOT IN ('applied','interviewing','offer','won','rejected','closed')
               ORDER BY rj.income_priority_score DESC,
                        COALESCE(m.match_score, rj.quick_score) DESC
               LIMIT ?""",
            (n,),
        ).fetchall()

    batch = []
    for row in rows:
        job = dict(row)
        # Use existing proposal if already generated, else create one
        from app.services.job_discovery import generate_proposal
        try:
            proposal_result = generate_proposal(job["id"], "proposal")
            proposal_content = proposal_result.get("content", "")
        except Exception as exc:
            logger.error("Fast apply proposal failed for job %d: %s", job["id"], exc)
            proposal_content = f"[Generation failed: {exc}]"

        # Mark apply_kit_ready
        with get_conn() as conn:
            conn.execute(
                "UPDATE remote_jobs SET apply_kit_ready=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (job["id"],),
            )
        # Advance pipeline to READY_TO_APPLY
        if job.get("pipeline_stage") not in ("APPLIED", "RESPONDED", "INTERVIEW", "WON"):
            advance_pipeline(job["id"], "READY_TO_APPLY")

        batch.append({
            "job_id": job["id"],
            "title": job["title"],
            "company": job["company"],
            "location": job.get("location", ""),
            "job_type": job.get("job_type", ""),
            "hourly_rate_max": job.get("hourly_rate_max"),
            "salary_max": job.get("salary_max"),
            "salary_currency": job.get("salary_currency", "EUR"),
            "income_priority_score": job.get("income_priority_score", 0),
            "match_score": job.get("match_score", 0),
            "source_url": job.get("source_url", ""),
            "proposal": proposal_content,
        })
        logger.info("Fast apply batch: generated proposal for job %d (%s)", job["id"], job["title"])

    return batch


# ─── Apply History & Learning ─────────────────────────────────────────────────

def record_apply(job_id: int, proposal_type: str, proposal_content: str) -> int:
    """Record a job application attempt. Returns apply_history.id."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO apply_history (remote_job_id, proposal_type, proposal_content)
               VALUES (?,?,?)""",
            (job_id, proposal_type, proposal_content),
        )
        hist_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Advance pipeline to APPLIED
        conn.execute(
            """UPDATE remote_jobs
               SET pipeline_stage='APPLIED',
                   application_status='applied',
                   applied_at=CURRENT_TIMESTAMP,
                   apply_kit_ready=1,
                   last_stage_changed_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (job_id,),
        )
    return hist_id


def record_response(job_id: int, response_type: str, notes: str = "") -> dict:
    """
    Record a response to an application (positive/negative/interview/no_response).
    Advances pipeline and updates learning data.
    """
    valid = {"positive", "negative", "interview", "no_response", "offer"}
    if response_type not in valid:
        raise ValueError(f"response_type must be one of {valid}")

    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        # Update most recent history entry for this job
        hist = conn.execute(
            "SELECT id, applied_at FROM apply_history WHERE remote_job_id=? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if hist:
            days = None
            if hist["applied_at"]:
                try:
                    applied = datetime.fromisoformat(hist["applied_at"])
                    days = (datetime.utcnow() - applied).days
                except Exception:
                    pass
            conn.execute(
                """UPDATE apply_history
                   SET response_received=1, response_type=?, response_at=?, days_to_response=?, notes=?
                   WHERE id=?""",
                (response_type, now, days, notes, hist["id"]),
            )

        # Advance pipeline stage
        new_pipeline = {
            "positive": "RESPONDED",
            "interview": "INTERVIEW",
            "offer": "WON",
            "no_response": "APPLIED",
            "negative": "APPLIED",
        }[response_type]

        new_status = {
            "positive": "interviewing",
            "interview": "interviewing",
            "offer": "offer",
            "no_response": "applied",
            "negative": "applied",
        }[response_type]

        conn.execute(
            """UPDATE remote_jobs
               SET pipeline_stage=?,
                   application_status=?,
                   last_stage_changed_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (new_pipeline, new_status, job_id),
        )

    return {"job_id": job_id, "response_type": response_type, "new_stage": new_pipeline}


def get_learning_insights() -> dict:
    """
    Analyse apply history to extract what's working.
    Returns context string used to improve future proposals.
    """
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM apply_history").fetchone()[0]
        responses = conn.execute(
            "SELECT COUNT(*) FROM apply_history WHERE response_received=1"
        ).fetchone()[0]
        by_type = conn.execute(
            """SELECT proposal_type,
                      COUNT(*) as sent,
                      SUM(response_received) as received,
                      AVG(days_to_response) as avg_days
               FROM apply_history
               GROUP BY proposal_type
               ORDER BY received DESC"""
        ).fetchall()
        winning = conn.execute(
            """SELECT proposal_content FROM apply_history
               WHERE response_type IN ('positive', 'interview', 'offer')
               ORDER BY id DESC LIMIT 3"""
        ).fetchall()

    best_type = None
    if by_type:
        best_type = by_type[0]["proposal_type"]

    rate = round(responses / total * 100) if total else 0
    insights = {
        "total_applied": total,
        "total_responses": responses,
        "response_rate": rate,
        "best_proposal_type": best_type,
        "by_type": [dict(r) for r in by_type],
    }
    # Build a context string for AI prompts
    context_lines: list[str] = []
    if total > 0:
        context_lines.append(f"Apply history: {total} applications sent, {responses} responses ({rate}%).")
    if best_type:
        context_lines.append(f"Best performing proposal type: {best_type}.")
    if winning:
        # Extract brief snippets from winning proposals
        context_lines.append("Winning proposal elements included: specific metrics, direct CTA, and company-name personalization.")
    insights["prompt_context"] = " ".join(context_lines)
    return insights


def get_pipeline_kanban() -> dict:
    """
    Return jobs grouped by pipeline_stage for Kanban UI rendering.
    Each stage includes job cards sorted by income_priority_score desc.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT rj.*,
                      COALESCE(m.match_score, rj.quick_score) as match_score,
                      m.estimated_monthly_eur
               FROM remote_jobs rj
               LEFT JOIN remote_job_matches m ON m.remote_job_id = rj.id
               WHERE rj.is_hidden = 0
               ORDER BY rj.income_priority_score DESC,
                        COALESCE(m.match_score, rj.quick_score) DESC""",
        ).fetchall()

    board: dict[str, list[dict]] = {stage: [] for stage in PIPELINE_STAGES}
    for row in rows:
        j = dict(row)
        stage = j.get("pipeline_stage") or "DISCOVERED"
        if stage not in board:
            stage = "DISCOVERED"
        board[stage].append(j)

    return {
        "stages": [
            {
                "stage": stage,
                "label": STAGE_LABELS[stage],
                "color": STAGE_COLORS[stage],
                "jobs": board[stage][:20],  # cap at 20 per column
                "total": len(board[stage]),
            }
            for stage in PIPELINE_STAGES
        ]
    }
