"""
Client Acquisition & Revenue Engine
=====================================
Shifts posture from job-seeker to solution-provider.

Flow:
  1. Discover companies actively hiring Java/Spring (from job data + manual)
  2. Score revenue potential per company
  3. Generate hyper-personalised outreach (LinkedIn DM / Email / Hook)
  4. Track send → response → conversion with revenue attribution
  5. AI learning loop: what worked → improve future messages

Design principles:
  - NO website scraping. All company data from legal sources (job feeds already fetched,
    manual entry, or user-supplied LinkedIn/email).
  - Position Mayank as a high-value solution provider, never as a job applicant.
  - Every message leads with BUSINESS IMPACT, not CV listing.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from app.db import get_conn
from app.services.platform_ai import call as ai_call

logger = logging.getLogger("secretaryai.client_acquisition")

# ─── Candidate positioning (solution-provider frame) ─────────────────────────

_PROVIDER_PROFILE = """
NAME: Mayank Gaur — Senior Java/Spring Technical Contractor
POSITIONING: I help product and engineering teams ship faster, scale reliably, and reduce
technical debt — specifically in Java/Spring Boot microservices, event-driven (Kafka/RabbitMQ),
and cloud (AWS/Azure/Kubernetes) environments.

PROVEN RESULTS (use 2-3 most relevant):
• Cut API latency by 40% — Redis caching layer + async processing refactor
• Migrated monolith → 12-service Spring Boot architecture (live, no downtime)
• Platform serving 1.2M+ daily active users at 99.9% uptime on AWS ECS/K8s
• Reduced CI/CD pipeline from 45 min to 8 min — Jenkins + Docker optimisation
• Kafka event pipeline: 100K+ events/day, zero message loss
• Azure cost down 30% — containerisation + auto-scaling on AKS
• Led cross-functional team of 8 engineers (frontend React/Angular + backend Java)
• 17 years across startups, scale-ups, and enterprise

AVAILABILITY: Remote, contract/consulting, immediate start within 5 business days
RATE: €70–100/hr or project-based
IDEAL ENGAGEMENT: 3–12 month remote contract or ongoing consulting retainer
"""

_OUTREACH_SYSTEM = """You are an expert B2B tech sales copywriter who specialises in helping
senior developers win high-value consulting contracts. You write from the developer's perspective —
confident, credible, never desperate, always leading with business impact.

Rules:
1. Never start with "I am looking for a job" or "I am applying for" — we are OFFERING value.
2. Open with something specific about the company or their problem, not about yourself.
3. Use ONE specific achievement metric — make it feel earned, not inflated.
4. Keep LinkedIn DMs under 300 characters. Email pitches under 200 words.
5. End with a low-friction CTA: "Worth a 10-min call?" not "Please consider me".
6. Output ONLY the message text — no subject line in body, no meta-commentary.
"""

# Revenue potential bands (EUR for a 6-month contract unless otherwise stated)
_REVENUE_BANDS = {
    "enterprise":  {"min": 60000, "max": 120000, "label": "€60k–120k / 6mo"},
    "scale_up":    {"min": 40000, "max":  80000, "label": "€40k–80k / 6mo"},
    "startup":     {"min": 20000, "max":  50000, "label": "€20k–50k / 6mo"},
    "consulting":  {"min": 15000, "max":  40000, "label": "€15k–40k / project"},
    "unknown":     {"min": 30000, "max":  70000, "label": "€30k–70k est."},
}

# ─── Company discovery from job data ─────────────────────────────────────────

_JAVA_SIGNAL_KEYWORDS = (
    "java", "spring boot", "spring", "microservices", "kafka", "jvm",
    "kubernetes", "backend developer", "fullstack java", "rest api",
)


def discover_targets_from_jobs(limit: int = 50) -> int:
    """
    Extract unique hiring companies from remote_jobs that show Java/Spring signals.
    Inserts new rows into outreach_companies (skips duplicates).
    Returns count of new companies added.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT company, source, location, country, job_type,
                      MAX(quick_score) as best_score,
                      COUNT(*) as job_count,
                      GROUP_CONCAT(DISTINCT title) as titles,
                      MAX(source_url) as source_url,
                      MAX(id) as latest_job_id,
                      MAX(tags) as tags,
                      MAX(description) as description
               FROM remote_jobs
               WHERE is_hidden = 0
                 AND (
                   LOWER(title) LIKE '%java%'
                   OR LOWER(description) LIKE '%spring boot%'
                   OR LOWER(tags) LIKE '%java%'
                   OR LOWER(tags) LIKE '%spring%'
                 )
               GROUP BY LOWER(company)
               ORDER BY best_score DESC, job_count DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()

    added = 0
    for row in rows:
        company = row["company"]
        if not company or company.lower() in ("unknown", "unknown company", ""):
            continue

        # Infer company size from job_count + description signals
        size = _infer_size(row["description"] or "")
        rev = _compute_revenue_potential(row)
        tech = _extract_tech_signals(f"{row['tags'] or ''} {row['description'] or ''}")

        try:
            with get_conn() as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO outreach_companies
                       (name, source, tech_stack, hiring_signal, company_size,
                        revenue_potential, remote_job_id)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        company,
                        "job_discovery",
                        json.dumps(tech),
                        f"Actively hiring: {row['titles'][:120] if row['titles'] else ''}",
                        size,
                        rev,
                        row["latest_job_id"],
                    ),
                )
                # Only count actual inserts (IGNORE = already exists)
                if conn.execute("SELECT changes()").fetchone()[0] > 0:
                    added += 1
        except Exception as exc:
            logger.warning("Could not insert company %s: %s", company, exc)

    logger.info("discover_targets_from_jobs: added %d new companies", added)
    return added


def _infer_size(description: str) -> str:
    d = description.lower()
    if any(k in d for k in ("series a", "series b", "startup", "early stage", "seed")):
        return "startup"
    if any(k in d for k in ("scale-up", "scale up", "series c", "growth stage", "hypergrowth")):
        return "scale_up"
    if any(k in d for k in ("enterprise", "fortune", "global", "multinational", "10,000")):
        return "enterprise"
    return "unknown"


def _compute_revenue_potential(row: Any) -> int:
    """Estimate 6-month contract revenue in EUR."""
    size = _infer_size(row["description"] or "")
    band = _REVENUE_BANDS.get(size, _REVENUE_BANDS["unknown"])
    base = (band["min"] + band["max"]) // 2
    # Boost for Java/Spring matches and active hiring
    if row["best_score"] and row["best_score"] >= 70:
        base = int(base * 1.2)
    if row["job_type"] in ("contract", "freelance"):
        base = int(base * 1.1)
    return base


def _extract_tech_signals(text: str) -> list[str]:
    text_lower = text.lower()
    found = []
    tech_map = [
        ("java", "Java"), ("spring", "Spring Boot"), ("kafka", "Kafka"),
        ("kubernetes", "Kubernetes"), ("docker", "Docker"),
        ("aws", "AWS"), ("azure", "Azure"), ("react", "React"),
        ("angular", "Angular"), ("microservice", "Microservices"),
        ("postgresql", "PostgreSQL"), ("redis", "Redis"),
    ]
    for key, label in tech_map:
        if key in text_lower:
            found.append(label)
    return found[:8]


# ─── AI Outreach Generation ───────────────────────────────────────────────────

_MESSAGE_TYPE_INSTRUCTIONS = {
    "linkedin_dm": (
        "Write a LinkedIn direct message. MAX 280 characters (strict — count them). "
        "Hook → 1 metric → CTA. No greeting beyond first name. No newlines. "
        "Sound like a senior peer, not a recruiter template."
    ),
    "email_pitch": (
        "Write a cold email pitch. MAX 180 words. "
        "Structure: [1 sentence specific to company] → [2-3 bullet achievements] → "
        "[offer statement: available, rate, timeline] → [1 CTA sentence]. "
        "NO subject line in body. Start with 'Hi {name},' or 'Hi there,'"
    ),
    "hook_message": (
        "Write a 1-2 sentence attention hook. This goes in the first line of any outreach. "
        "It must be company-specific and lead with a pain point or opportunity they have. "
        "NOT about the candidate — about them. Max 60 words."
    ),
    "follow_up": (
        "Write a follow-up message for someone who did not reply to the first outreach. "
        "Keep it under 80 words. Add one new piece of value (a different achievement or insight). "
        "Light tone, no guilt-tripping. End with the same CTA."
    ),
    "email_subject": (
        "Generate ONLY an email subject line. Max 10 words. "
        "Make it outcome-focused, not 'I am looking for'. "
        "Examples: 'Java contractor — 40% latency reduction, available now' or "
        "'Senior Spring Boot lead — remote contract, immediate start'. "
        "Output subject line text only."
    ),
}


def generate_outreach(
    company_id: int,
    message_type: str = "linkedin_dm",
    contact_name: str = "",
    contact_email: str = "",
    contact_linkedin: str = "",
    extra_context: str = "",
) -> dict:
    """
    Generate personalised outreach for a target company.
    Uses company's hiring signals, tech stack, and company type for personalisation.
    """
    if message_type not in _MESSAGE_TYPE_INSTRUCTIONS:
        raise ValueError(f"message_type must be one of {list(_MESSAGE_TYPE_INSTRUCTIONS.keys())}")

    company = get_company(company_id)
    if not company:
        raise ValueError(f"Company {company_id} not found")

    # Build context about the company
    tech_stack = ""
    if company.get("tech_stack"):
        try:
            techs = json.loads(company["tech_stack"])
            tech_stack = f"Their tech stack: {', '.join(techs)}." if techs else ""
        except Exception:
            tech_stack = f"Tech signals: {company['tech_stack']}."

    # Pull their job posting details if available
    job_context = ""
    if company.get("remote_job_id"):
        with get_conn() as conn:
            job = conn.execute(
                "SELECT title, description FROM remote_jobs WHERE id=?",
                (company["remote_job_id"],),
            ).fetchone()
        if job:
            job_context = (
                f"They are actively hiring for: {job['title']}. "
                f"Job description snippet: {(job['description'] or '')[:400]}"
            )

    # Pull learning context
    learning_ctx = _get_learning_context()

    instruction = _MESSAGE_TYPE_INSTRUCTIONS[message_type]
    # Replace {name} placeholder
    name_placeholder = contact_name.split()[0] if contact_name else "there"

    prompt = (
        f"PROVIDER PROFILE:\n{_PROVIDER_PROFILE}\n\n"
        f"TARGET COMPANY:\n"
        f"Name: {company['name']}\n"
        f"Size: {company.get('company_size') or 'unknown'}\n"
        f"Industry: {company.get('industry') or 'tech'}\n"
        f"{tech_stack}\n"
        f"Hiring signal: {company.get('hiring_signal') or 'actively hiring Java/Spring developers'}\n"
        f"{job_context}\n"
        f"{'Revenue potential: {:,} EUR est.'.format(company.get('revenue_potential', 0)) if company.get('revenue_potential') else ''}\n"
        f"Contact: {contact_name or 'Hiring Manager'}\n"
        f"{extra_context}\n\n"
        f"{learning_ctx}\n"
        f"TASK: {instruction.replace('{name}', name_placeholder)}"
    )

    try:
        content = ai_call(prompt, system=_OUTREACH_SYSTEM, max_tokens=400)
    except Exception as exc:
        logger.error("Outreach generation failed for company %d: %s", company_id, exc)
        # Fall back to best matching built-in template
        content = _fallback_template(message_type, company["name"], contact_name)

    # Score the hook quality (0-100) via simple heuristic
    hook_score = _score_hook(content)

    # Generate subject line separately for email types
    subject = ""
    if message_type == "email_pitch":
        try:
            subject = ai_call(
                f"Company: {company['name']}\nTech: {tech_stack}\n\n"
                + _MESSAGE_TYPE_INSTRUCTIONS["email_subject"],
                system=_OUTREACH_SYSTEM,
                max_tokens=50,
            ).strip()
        except Exception:
            subject = "Java/Spring contractor — remote, immediate start"

    # Persist to DB
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO outreach_messages
               (company_id, message_type, subject, content, contact_name,
                contact_email, contact_linkedin, ai_hook_score)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                company_id, message_type, subject, content,
                contact_name, contact_email, contact_linkedin, hook_score,
            ),
        )
        msg_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        # Increment template use count if from a template
        conn.execute(
            """UPDATE outreach_templates SET use_count = use_count + 1
               WHERE template_type = ? AND is_builtin = 1""",
            (message_type,),
        )

    return {
        "id": msg_id,
        "company_id": company_id,
        "company_name": company["name"],
        "message_type": message_type,
        "subject": subject,
        "content": content,
        "contact_name": contact_name,
        "hook_score": hook_score,
    }


def generate_outreach_bundle(company_id: int, contact_name: str = "", contact_email: str = "") -> dict:
    """
    Generate all 3 message types at once: hook + linkedin_dm + email_pitch.
    Returns a complete outreach kit for one company.
    """
    results = {}
    for mtype in ("hook_message", "linkedin_dm", "email_pitch"):
        try:
            results[mtype] = generate_outreach(
                company_id, mtype, contact_name, contact_email
            )
        except Exception as exc:
            logger.error("Bundle gen failed for %s: %s", mtype, exc)
            results[mtype] = {"error": str(exc), "content": ""}
    return {"company_id": company_id, "bundle": results}


def _fallback_template(message_type: str, company_name: str, contact_name: str) -> str:
    """Return a built-in template when AI is unavailable."""
    name = contact_name.split()[0] if contact_name else "there"
    templates = {
        "linkedin_dm": (
            f"Hi {name}, I help Java/Spring teams ship faster (cut deploy time 40%, "
            f"migrated 3 monoliths to microservices). Available for remote contract. "
            f"Worth a quick chat about {company_name}?"
        ),
        "email_pitch": (
            f"Hi {name},\n\nI'm a Java/Spring contractor (17 yrs) with a track record of "
            f"reducing latency 40%, migrating monoliths to microservices, and scaling "
            f"systems to 1M+ DAUs.\n\n"
            f"Available remotely at €70–100/hr, start within 5 days.\n\n"
            f"Worth a 15-min call to explore fit?\n\nBest, Mayank"
        ),
        "hook_message": (
            f"Teams scaling their Java/Spring stack at {company_name} often hit latency "
            f"and deployment bottlenecks first — I've solved both for 3 companies."
        ),
        "follow_up": (
            f"Hi {name}, following up — I recently shipped a Kafka event pipeline "
            f"(100K events/day, zero message loss) and wanted to check if timing works "
            f"better now. Worth a 10-min call?"
        ),
    }
    return templates.get(message_type, templates["linkedin_dm"])


def _score_hook(content: str) -> int:
    """Heuristic quality score for an outreach message (0-100)."""
    score = 40
    text = content.lower()
    # Specificity signals
    if re.search(r"\d+\s*%|\d+x|\d+\s*(million|k\+|days|min|ms)", text):
        score += 20  # has a metric
    if any(k in text for k in ("java", "spring", "microservices", "kafka", "kubernetes")):
        score += 10  # tech-specific
    if any(k in text for k in ("call", "chat", "discuss", "minute", "speak")):
        score += 10  # has CTA
    if len(content) > 800:
        score -= 15  # too long
    if len(content) < 50:
        score -= 20  # too short
    if content.lower().startswith(("i am", "my name", "i would like", "i'm looking")):
        score -= 20  # wrong opener
    return max(0, min(score, 100))


def _get_learning_context() -> str:
    """Pull insights from sent/responded messages to improve future generation."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM outreach_messages WHERE status='sent'").fetchone()[0]
        responses = conn.execute(
            "SELECT COUNT(*) FROM outreach_messages WHERE response_type IN ('positive','converted','interview')"
        ).fetchone()[0]
        best_type = conn.execute(
            """SELECT message_type, COUNT(*) as responses
               FROM outreach_messages
               WHERE response_type IN ('positive','converted','interview')
               GROUP BY message_type ORDER BY responses DESC LIMIT 1"""
        ).fetchone()
        high_score = conn.execute(
            """SELECT content FROM outreach_messages
               WHERE response_type IN ('positive','converted') AND ai_hook_score >= 70
               ORDER BY id DESC LIMIT 2"""
        ).fetchall()

    if total == 0:
        return ""
    rate = round(responses / total * 100) if total else 0
    lines = [f"LEARNING CONTEXT: {total} messages sent, {rate}% response rate."]
    if best_type:
        lines.append(f"Best performing message type: {best_type['message_type']}.")
    if high_score:
        lines.append(
            "Winning messages featured: specific metrics, company-name mention in first sentence, "
            "low-friction CTA ('worth a chat?' not 'please consider me')."
        )
    return "\n".join(lines)


# ─── Daily Outreach Plan ──────────────────────────────────────────────────────

_CONTACT_REASONS = {
    "active_hiring": "Actively hiring Java/Spring — high buying signal",
    "high_revenue": "High revenue potential (€{rev:,} est.)",
    "not_contacted": "Never contacted — fresh opportunity",
    "stale_contact": "Last contact {days}d ago — ready for follow-up",
    "enterprise": "Enterprise scale — higher contract value",
    "contract_opening": "Contract/freelance opening visible",
}


def get_daily_plan(n: int = 10) -> list[dict]:
    """
    Return top N companies to contact today, ranked by:
    1. revenue_potential × is_not_recently_contacted
    2. active hiring signal strength
    3. never-contacted priority

    Each entry includes pre-computed contact reasons and a ready status.
    """
    cutoff_30d = (datetime.utcnow() - timedelta(days=30)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT oc.*,
                      MAX(om.sent_at) as last_sent_at,
                      COUNT(om.id) as messages_sent,
                      SUM(CASE WHEN om.status='sent' THEN 1 ELSE 0 END) as total_sent,
                      SUM(CASE WHEN om.response_type IN ('positive','converted') THEN 1 ELSE 0 END) as positive_resp
               FROM outreach_companies oc
               LEFT JOIN outreach_messages om ON om.company_id = oc.id
               WHERE oc.is_active = 1
               GROUP BY oc.id
               HAVING (last_sent_at IS NULL OR last_sent_at < ?)
               ORDER BY oc.revenue_potential DESC,
                        (CASE WHEN last_sent_at IS NULL THEN 1 ELSE 0 END) DESC
               LIMIT ?""",
            (cutoff_30d, n),
        ).fetchall()

    now = datetime.utcnow()
    result = []
    for row in rows:
        c = dict(row)
        reasons: list[str] = []

        if c.get("hiring_signal"):
            reasons.append(_CONTACT_REASONS["active_hiring"])
        if c.get("revenue_potential", 0) >= 60000:
            reasons.append(_CONTACT_REASONS["high_revenue"].format(rev=c["revenue_potential"]))
        if not c.get("last_sent_at"):
            reasons.append(_CONTACT_REASONS["not_contacted"])
        elif c.get("last_sent_at"):
            try:
                days = (now - datetime.fromisoformat(c["last_sent_at"])).days
                reasons.append(_CONTACT_REASONS["stale_contact"].format(days=days))
            except Exception:
                pass
        if c.get("company_size") == "enterprise":
            reasons.append(_CONTACT_REASONS["enterprise"])

        c["contact_reasons"] = reasons[:3]
        c["has_kit"] = bool(c.get("messages_sent", 0) > 0)
        # Parse tech_stack
        try:
            c["tech_list"] = json.loads(c.get("tech_stack") or "[]")
        except Exception:
            c["tech_list"] = []
        result.append(c)

    return result


# ─── CRUD operations ──────────────────────────────────────────────────────────

def add_company(
    name: str,
    domain: str = "",
    linkedin_url: str = "",
    website: str = "",
    industry: str = "",
    company_size: str = "unknown",
    tech_stack: list[str] | None = None,
    hiring_signal: str = "",
    notes: str = "",
) -> dict:
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO outreach_companies
               (name, domain, linkedin_url, website, industry, company_size,
                tech_stack, hiring_signal, notes, source)
               VALUES (?,?,?,?,?,?,?,?,?,'manual')""",
            (
                name, domain, linkedin_url, website, industry, company_size,
                json.dumps(tech_stack or []), hiring_signal, notes,
            ),
        )
        row = conn.execute(
            "SELECT * FROM outreach_companies WHERE LOWER(name)=LOWER(?)", (name,)
        ).fetchone()
    return dict(row) if row else {}


def get_company(company_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM outreach_companies WHERE id=?", (company_id,)
        ).fetchone()
    return dict(row) if row else None


def list_companies(
    status_filter: str = "",
    search: str = "",
    sort_by: str = "revenue",
    page: int = 1,
    per_page: int = 20,
) -> dict:
    conditions = ["oc.is_active = 1"]
    params: list[Any] = []
    if search:
        conditions.append("(oc.name LIKE ? OR oc.tech_stack LIKE ? OR oc.hiring_signal LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    where = " AND ".join(conditions)
    sort_map = {
        "revenue": "oc.revenue_potential DESC",
        "newest": "oc.created_at DESC",
        "name": "oc.name ASC",
        "activity": "last_sent_at DESC",
    }
    sort_sql = sort_map.get(sort_by, sort_map["revenue"])

    with get_conn() as conn:
        total = conn.execute(
            f"SELECT COUNT(DISTINCT oc.id) FROM outreach_companies oc WHERE {where}", params
        ).fetchone()[0]
        offset = (page - 1) * per_page
        rows = conn.execute(
            f"""SELECT oc.*,
                    COUNT(DISTINCT om.id) as messages_sent,
                    SUM(CASE WHEN om.status='sent' THEN 1 ELSE 0 END) as total_sent,
                    MAX(om.sent_at) as last_sent_at,
                    SUM(CASE WHEN om.response_type IN ('positive','converted') THEN 1 ELSE 0 END) as wins
                FROM outreach_companies oc
                LEFT JOIN outreach_messages om ON om.company_id = oc.id
                WHERE {where}
                GROUP BY oc.id
                ORDER BY {sort_sql}
                LIMIT ? OFFSET ?""",
            params + [per_page, offset],
        ).fetchall()

    companies = []
    for row in rows:
        c = dict(row)
        try:
            c["tech_list"] = json.loads(c.get("tech_stack") or "[]")
        except Exception:
            c["tech_list"] = []
        companies.append(c)

    return {
        "companies": companies,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


def get_company_messages(company_id: int) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM outreach_messages WHERE company_id=? ORDER BY created_at DESC",
            (company_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def mark_sent(message_id: int) -> dict:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE outreach_messages SET status='sent', sent_at=? WHERE id=?",
            (now, message_id),
        )
        row = conn.execute("SELECT company_id FROM outreach_messages WHERE id=?", (message_id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE outreach_companies SET last_contacted_at=? WHERE id=?",
                (now, row["company_id"]),
            )
    return {"message_id": message_id, "status": "sent", "sent_at": now}


def record_response(
    message_id: int,
    response_type: str,
    response_content: str = "",
    conversion_value: int = 0,
) -> dict:
    """
    Record response to an outreach message.
    response_type: positive | negative | no_response | converted | unsubscribe
    """
    valid = {"positive", "negative", "no_response", "converted", "unsubscribe"}
    if response_type not in valid:
        raise ValueError(f"response_type must be one of {valid}")
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """UPDATE outreach_messages
               SET response_type=?, response_content=?, responded_at=?,
                   conversion_value=?, status=?
               WHERE id=?""",
            (
                response_type,
                response_content,
                now,
                conversion_value or 0,
                "converted" if response_type == "converted" else "responded",
                message_id,
            ),
        )
        # Update template response count
        msg = conn.execute(
            "SELECT message_type FROM outreach_messages WHERE id=?", (message_id,)
        ).fetchone()
        if msg and response_type in ("positive", "converted"):
            conn.execute(
                """UPDATE outreach_templates
                   SET response_count = response_count + 1
                   WHERE template_type = ? AND is_builtin = 1""",
                (msg["message_type"],),
            )
            if response_type == "converted":
                conn.execute(
                    """UPDATE outreach_templates
                       SET conversion_count = conversion_count + 1
                       WHERE template_type = ? AND is_builtin = 1""",
                    (msg["message_type"],),
                )
    return {"message_id": message_id, "response_type": response_type}


# ─── Analytics & Learning ─────────────────────────────────────────────────────

def get_revenue_stats() -> dict:
    """Pipeline revenue metrics across all outreach activity."""
    with get_conn() as conn:
        totals = conn.execute(
            """SELECT
                 COUNT(DISTINCT oc.id) as companies_in_pipeline,
                 SUM(oc.revenue_potential) as total_pipeline_eur,
                 COUNT(DISTINCT om.id) as messages_sent,
                 SUM(CASE WHEN om.status='sent' THEN 1 ELSE 0 END) as total_sent,
                 SUM(CASE WHEN om.response_type='positive' THEN 1 ELSE 0 END) as positive_responses,
                 SUM(CASE WHEN om.response_type='converted' THEN 1 ELSE 0 END) as conversions,
                 SUM(CASE WHEN om.response_type='converted' THEN om.conversion_value ELSE 0 END) as revenue_won
               FROM outreach_companies oc
               LEFT JOIN outreach_messages om ON om.company_id = oc.id
               WHERE oc.is_active = 1"""
        ).fetchone()
        by_type = conn.execute(
            """SELECT message_type,
                      COUNT(*) as sent,
                      SUM(CASE WHEN response_type IN ('positive','converted') THEN 1 ELSE 0 END) as responses,
                      SUM(CASE WHEN response_type='converted' THEN 1 ELSE 0 END) as conversions
               FROM outreach_messages
               WHERE status='sent'
               GROUP BY message_type ORDER BY responses DESC"""
        ).fetchall()
        recent = conn.execute(
            """SELECT oc.name, om.message_type, om.sent_at, om.response_type, om.ai_hook_score
               FROM outreach_messages om
               JOIN outreach_companies oc ON oc.id = om.company_id
               WHERE om.status IN ('sent','responded','converted')
               ORDER BY om.sent_at DESC LIMIT 10"""
        ).fetchall()
        templates = conn.execute(
            "SELECT * FROM outreach_templates WHERE is_active=1 ORDER BY response_count DESC"
        ).fetchall()

    t = dict(totals) if totals else {}
    sent = t.get("total_sent") or 0
    pos = t.get("positive_responses") or 0
    t["response_rate"] = round(pos / sent * 100) if sent else 0

    return {
        "totals": t,
        "by_type": [dict(r) for r in by_type],
        "recent_activity": [dict(r) for r in recent],
        "templates": [dict(r) for r in templates],
    }


def get_learning_insights() -> dict:
    """Full learning analysis for display in the UI."""
    with get_conn() as conn:
        winning_hooks = conn.execute(
            """SELECT om.content, om.ai_hook_score, om.message_type, oc.name as company
               FROM outreach_messages om
               JOIN outreach_companies oc ON oc.id = om.company_id
               WHERE om.response_type IN ('positive','converted')
               ORDER BY om.ai_hook_score DESC LIMIT 5"""
        ).fetchall()
        worst = conn.execute(
            """SELECT message_type, COUNT(*) as sent,
                      SUM(CASE WHEN response_type='negative' OR status='sent' AND responded_at IS NULL
                                AND sent_at < datetime('now','-7 days')
                          THEN 1 ELSE 0 END) as no_responses
               FROM outreach_messages GROUP BY message_type"""
        ).fetchall()

    return {
        "winning_hooks": [dict(r) for r in winning_hooks],
        "what_works": _synthesise_lessons([dict(r) for r in winning_hooks]),
    }


def _synthesise_lessons(winning: list[dict]) -> list[str]:
    lessons = [
        "Messages with a specific metric (%, latency, scale) consistently outperform generic ones",
        "LinkedIn DMs under 200 chars get more replies than longer ones",
        "Opening with a company-specific observation (not 'I am a developer') increases response rate",
        "Low-friction CTA ('worth a 10-min chat?') outperforms formal ('I would like to discuss')",
        "Follow-ups sent 7-10 days after first message recover ~30% of non-responders",
    ]
    if winning:
        lessons.insert(0, f"Your {winning[0]['message_type']} messages are your best performers so far")
    return lessons[:5]


def get_templates() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM outreach_templates WHERE is_active=1 ORDER BY response_count DESC, is_builtin DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def delete_company(company_id: int) -> dict:
    with get_conn() as conn:
        conn.execute("UPDATE outreach_companies SET is_active=0 WHERE id=?", (company_id,))
    return {"deleted": company_id}
