"""Job automation and application-tracking service layer."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import re

from app.db import get_conn
from app.schemas import JobLeadCreate, ApplicationCreate
from app.services.parsers import parse_target_companies
from config import DOCS, WORKSPACE

PROFILE_SKILLS = {
    "java", "spring boot", "microservices", "kafka", "aws", "react", "angular",
    "architecture", "technical lead", "project lead", "kubernetes", "docker",
}

HIGH_VISA_SUPPORT = {"high", "medium-high", "yes"}
PROFILE_NAME = "Mayank Gaur"
PROFILE_EMAIL = "mayankgaur.8@gmail.com"
PROFILE_PHONE = "+91 9620439138"
PROFILE_LINKEDIN = "linkedin.com/in/mayank-gaur8/"
PROFILE_BASE_SUMMARY = (
    "Senior Java Engineer and Technical Lead with 17+ years of experience delivering "
    "Spring Boot, microservices, Kafka, AWS, Docker/Kubernetes, and React/Angular solutions "
    "for enterprise platforms. Proven results include 40% performance gains, 50% throughput "
    "improvement, and leadership of cross-functional delivery teams."
)

LEAD_TYPE_LABELS = {
    "target_role": "Target Role",
    "live_opening": "Live Opening",
    "verified_applied": "Verified Applied",
}


def _load_doc_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _extract_role_keywords(job: dict) -> list[str]:
    raw = " ".join(
        filter(
            None,
            [
                job.get("role_title"),
                job.get("company"),
                job.get("notes"),
                job.get("country"),
                job.get("city"),
            ],
        )
    ).lower()

    found = []
    for skill in PROFILE_SKILLS:
        if skill in raw:
            found.append(skill)

    title_tokens = re.findall(r"[a-zA-Z][a-zA-Z+/.-]+", (job.get("role_title") or "").lower())
    note_tokens = re.findall(r"[a-zA-Z][a-zA-Z+/.-]+", (job.get("notes") or "").lower())
    common_tokens = [
        token for token in title_tokens + note_tokens
        if token not in {"senior", "lead", "role", "team", "with", "your", "their", "that"}
        and len(token) > 3
    ]

    combined = []
    for token in found + common_tokens:
        normalized = token.strip().replace("  ", " ")
        if normalized and normalized not in combined:
            combined.append(normalized)
    return combined[:10]


def _tailored_summary(job: dict, keywords: list[str]) -> str:
    city = job.get("city") or job.get("country") or "the target location"
    role = job.get("role_title") or "Senior Java role"
    keyword_text = ", ".join(keywords[:6]) if keywords else "Java, Spring Boot, microservices, AWS"
    return (
        f"{PROFILE_BASE_SUMMARY} Targeting {role} opportunities with {job.get('company')} "
        f"in {city}. Strong fit for {keyword_text}, with relocation readiness supported by "
        "the German Chancenkarte process and immediate availability for remote interviews."
    )


def _cover_letter(job: dict, summary: str) -> str:
    company = job.get("company") or "your company"
    role = job.get("role_title") or "Senior Java Lead"
    city = job.get("city") or job.get("country") or "your location"
    notes = job.get("notes") or "your engineering environment and enterprise platform work"
    return f"""Dear Hiring Manager,

I am writing to express my interest in the {role} position at {company}.

With 17+ years of hands-on experience across Java, Spring Boot, microservices, Kafka, AWS, and frontend collaboration with React and Angular, I bring a mix of architecture depth, delivery ownership, and team leadership that aligns well with this opportunity.

{summary}

I am particularly interested in {company} because of {notes[:260].rstrip()}. My background includes building a Java performance optimization tool that improved system efficiency by 40%, architecting multi-threaded systems supporting 200+ simultaneous transactions, and leading cross-functional teams to sustained on-time delivery.

I am currently based in Bengaluru, India. I have applied for Germany's Opportunity Card (Chancenkarte), which supports relocation and rapid transition into the German market. I am available for remote interviews immediately and can travel for later-stage discussions when needed.

I would welcome the opportunity to discuss how I can contribute to {company}'s engineering goals in {city}.

Yours sincerely,
{PROFILE_NAME}
Email: {PROFILE_EMAIL}
Phone: {PROFILE_PHONE}
LinkedIn: {PROFILE_LINKEDIN}
"""


def _recruiter_message(job: dict, keywords: list[str]) -> str:
    company = job.get("company") or "your company"
    role = job.get("role_title") or "Senior Java Lead"
    location = ", ".join(filter(None, [job.get("city"), job.get("country")]))
    focus = ", ".join(keywords[:5]) if keywords else "Spring Boot, microservices, AWS, and team leadership"
    return f"""Hi [Recruiter Name],

I am reaching out regarding the {role} opportunity at {company}{f' in {location}' if location else ''}.

I bring 17+ years of Java full-stack and technical leadership experience, with strong alignment in {focus}. In recent roles, I delivered 40% performance gains, 50% throughput improvement, and led cross-functional delivery for enterprise platforms.

I am currently based in India and pursuing relocation through the German Chancenkarte route. I am available for remote interviews immediately and would be happy to share my resume for review.

Would you be open to a short conversation?

Best regards,
{PROFILE_NAME}
{PROFILE_LINKEDIN}
"""


def _form_answers(job: dict, summary: str) -> str:
    city = job.get("city") or "Germany"
    return f"""Why do you want to work here?
I am interested in this role because it combines senior Java engineering, architecture responsibility, and business-critical delivery in {city}. My background in performance optimization, microservices, and team leadership aligns well with the type of work described for this opportunity.

Why are you a fit for this role?
{summary}

Do you require sponsorship?
I have applied for the German Opportunity Card (Chancenkarte), which supports my relocation and near-term work eligibility in Germany. For long-term employment, I would require the standard employer-supported work authorization conversion process.

What is your availability?
I am available for remote interviews immediately. I can relocate and begin onboarding based on final visa timing, and I can travel for interview rounds if needed.
"""


def _resume_keywords_text(keywords: list[str]) -> str:
    default_keywords = [
        "Java 17", "Spring Boot", "Microservices", "Kafka", "AWS",
        "Docker", "Kubernetes", "React", "Angular", "Technical Leadership",
    ]
    merged = []
    for keyword in keywords + default_keywords:
        formatted = keyword.title() if keyword.islower() else keyword
        if formatted not in merged:
            merged.append(formatted)
    return ", ".join(merged[:12])


def compute_priority(match_score: float, visa_support: str | None, salary_max: int | None) -> float:
    visa_bonus = 1.5 if (visa_support or "").strip().lower() in HIGH_VISA_SUPPORT else 0.5
    salary_bonus = min((salary_max or 0) / 50000, 3.0)
    return round(match_score + visa_bonus + salary_bonus, 2)


def list_job_leads(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT jl.*, a.stage AS application_stage, a.follow_up_due,
                   a.submission_proof, a.verified_applied
            FROM job_leads jl
            LEFT JOIN applications a ON a.job_lead_id = jl.id
            ORDER BY jl.priority_score DESC, jl.match_score DESC, jl.company ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        if item.get("verified_applied"):
            item["display_label"] = LEAD_TYPE_LABELS["verified_applied"]
        else:
            item["display_label"] = LEAD_TYPE_LABELS.get(item.get("lead_type") or "target_role", "Target Role")
        item["source_label"] = item.get("source") or "manual"
        items.append(item)
    return items


def get_job_lead(job_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM job_leads WHERE id = ?", (job_id,)).fetchone()
    if row is None:
        raise ValueError(f"Job lead {job_id} not found")
    return dict(row)


def create_job_lead(payload: JobLeadCreate) -> dict:
    priority = compute_priority(0, payload.visa_support, payload.salary_max)
    credibility = max(6.0, float(priority))
    with get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO job_leads (
                company, role_title, country, city, source, apply_url, salary_min,
                salary_max, salary_currency, visa_support, match_score,
                credibility_score, priority_score, notes
                , lead_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.company,
                payload.role_title,
                payload.country,
                payload.city,
                payload.source,
                payload.apply_url,
                payload.salary_min,
                payload.salary_max,
                payload.salary_currency,
                payload.visa_support,
                0,
                credibility,
                priority,
                payload.notes,
                payload.lead_type,
            ),
        )
        row_id = cursor.lastrowid
        row = conn.execute("SELECT * FROM job_leads WHERE id = ?", (row_id,)).fetchone()
    return dict(row)


def import_target_companies() -> dict:
    inserted = 0
    updated = 0
    parsed = parse_target_companies()

    with get_conn() as conn:
        for item in parsed:
            priority = compute_priority(item["match_score"], item["visa_support"], item["salary_max"])
            exists = conn.execute(
                "SELECT id FROM job_leads WHERE company = ? AND role_title = ? AND COALESCE(city, '') = COALESCE(?, '')",
                (item["company"], item["role_title"], item["city"]),
            ).fetchone()

            if exists:
                conn.execute(
                    """
                    UPDATE job_leads
                    SET country = ?, source = ?, apply_url = ?, salary_min = ?, salary_max = ?,
                        salary_currency = ?, visa_support = ?, match_score = ?, credibility_score = ?,
                        priority_score = ?, notes = ?, lead_type = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (
                        item["country"],
                        item["source"],
                        item["apply_url"],
                        item["salary_min"],
                        item["salary_max"],
                        item["salary_currency"],
                        item["visa_support"],
                        item["match_score"],
                        max(7.0, item["match_score"]),
                        priority,
                        item["notes"],
                        item.get("lead_type", "target_role"),
                        exists["id"],
                    ),
                )
                updated += 1
            else:
                conn.execute(
                    """
                    INSERT INTO job_leads (
                        company, role_title, country, city, source, apply_url, salary_min,
                        salary_max, salary_currency, visa_support, match_score,
                        credibility_score, priority_score, notes, lead_type
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["company"],
                        item["role_title"],
                        item["country"],
                        item["city"],
                        item["source"],
                        item["apply_url"],
                        item["salary_min"],
                        item["salary_max"],
                        item["salary_currency"],
                        item["visa_support"],
                        item["match_score"],
                        max(7.0, item["match_score"]),
                        priority,
                        item["notes"],
                        item.get("lead_type", "target_role"),
                    ),
                )
                inserted += 1

    return {"inserted": inserted, "updated": updated, "total_parsed": len(parsed)}


def apply_to_job(job_id: int, payload: ApplicationCreate | None = None) -> dict:
    payload = payload or ApplicationCreate()
    with get_conn() as conn:
        job = conn.execute("SELECT * FROM job_leads WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError(f"Job lead {job_id} not found")

        existing = conn.execute(
            "SELECT * FROM applications WHERE job_lead_id = ? ORDER BY id DESC LIMIT 1",
            (job_id,),
        ).fetchone()

        follow_up_due = payload.follow_up_due or str(date.today() + timedelta(days=7))
        verified_applied = 1 if payload.submission_proof else 0
        stage = "Verified Applied" if verified_applied else "Tracked"
        if existing:
            conn.execute(
                """
                UPDATE applications
                SET stage = ?, next_action = ?, follow_up_due = ?, contact_name = ?,
                    contact_channel = ?, submission_proof = ?, verified_applied = ?, notes = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    stage,
                    payload.next_action or "Send follow-up if no reply in 7 days",
                    follow_up_due,
                    payload.contact_name,
                    payload.contact_channel,
                    payload.submission_proof,
                    verified_applied,
                    payload.notes,
                    existing["id"],
                ),
            )
            application_id = existing["id"]
        else:
            cursor = conn.execute(
                """
                INSERT INTO applications (
                    job_lead_id, stage, next_action, follow_up_due,
                    contact_name, contact_channel, submission_proof, verified_applied, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    stage,
                    payload.next_action or "Prepare tailored outreach and follow up in 7 days",
                    follow_up_due,
                    payload.contact_name,
                    payload.contact_channel,
                    payload.submission_proof,
                    verified_applied,
                    payload.notes,
                ),
            )
            application_id = cursor.lastrowid

        lead_status = "verified_applied" if verified_applied else "tracked"
        lead_type = "verified_applied" if verified_applied else job["lead_type"]
        conn.execute(
            "UPDATE job_leads SET status = ?, lead_type = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (lead_status, lead_type, job_id),
        )
        row = conn.execute("SELECT * FROM applications WHERE id = ?", (application_id,)).fetchone()
    return dict(row)


def list_applications(limit: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT a.*, jl.company, jl.role_title, jl.country, jl.city, jl.source, jl.lead_type
            FROM applications a
            JOIN job_leads jl ON jl.id = a.job_lead_id
            ORDER BY COALESCE(a.follow_up_due, a.applied_at) ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["display_label"] = "Verified Applied" if item.get("verified_applied") else "Tracked Only"
        item["source_label"] = item.get("source") or "manual"
        items.append(item)
    return items


def generate_application_draft(job_id: int) -> dict:
    job = get_job_lead(job_id)
    keywords = _extract_role_keywords(job)
    tailored_summary = _tailored_summary(job, keywords)
    resume_keywords = _resume_keywords_text(keywords)
    recruiter_message = _recruiter_message(job, keywords)
    cover_letter = _cover_letter(job, tailored_summary)
    form_answers = _form_answers(job, tailored_summary)

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM application_drafts WHERE job_lead_id = ?",
            (job_id,),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE application_drafts
                SET tailored_summary = ?, resume_keywords = ?, recruiter_message = ?,
                    cover_letter = ?, form_answers = ?, updated_at = CURRENT_TIMESTAMP
                WHERE job_lead_id = ?
                """,
                (
                    tailored_summary,
                    resume_keywords,
                    recruiter_message,
                    cover_letter,
                    form_answers,
                    job_id,
                ),
            )
        else:
            conn.execute(
                """
                INSERT INTO application_drafts (
                    job_lead_id, tailored_summary, resume_keywords,
                    recruiter_message, cover_letter, form_answers
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    tailored_summary,
                    resume_keywords,
                    recruiter_message,
                    cover_letter,
                    form_answers,
                ),
            )

        row = conn.execute(
            """
            SELECT ad.*, jl.company, jl.role_title, jl.city, jl.country
            FROM application_drafts ad
            JOIN job_leads jl ON jl.id = ad.job_lead_id
            WHERE ad.job_lead_id = ?
            """,
            (job_id,),
        ).fetchone()
    return dict(row)


def get_application_draft(job_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT ad.*, jl.company, jl.role_title, jl.city, jl.country
            FROM application_drafts ad
            JOIN job_leads jl ON jl.id = ad.job_lead_id
            WHERE ad.job_lead_id = ?
            """,
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def dashboard_job_summary() -> dict:
    with get_conn() as conn:
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total_jobs,
                SUM(CASE WHEN status = 'shortlisted' THEN 1 ELSE 0 END) AS shortlisted,
                SUM(CASE WHEN status = 'tracked' THEN 1 ELSE 0 END) AS tracked,
                SUM(CASE WHEN status = 'verified_applied' THEN 1 ELSE 0 END) AS verified_applied
            FROM job_leads
            """
        ).fetchone()
        due = conn.execute(
            """
            SELECT COUNT(*) AS follow_ups_due
            FROM applications
            WHERE follow_up_due IS NOT NULL AND follow_up_due <= date('now')
            """
        ).fetchone()
    return {
        "total_jobs": counts["total_jobs"] or 0,
        "shortlisted": counts["shortlisted"] or 0,
        "tracked": counts["tracked"] or 0,
        "verified_applied": counts["verified_applied"] or 0,
        "follow_ups_due": due["follow_ups_due"] or 0,
    }


def clear_job_leads() -> dict:
    with get_conn() as conn:
        deleted = conn.execute("SELECT COUNT(*) AS count FROM job_leads").fetchone()["count"]
        conn.execute("DELETE FROM application_drafts")
        conn.execute("DELETE FROM applications")
        conn.execute("DELETE FROM job_leads")
    return {"deleted": deleted}
