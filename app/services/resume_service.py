"""
Resume-Aware Job Application Engine
=====================================
Generates tailored resumes, apply packages, and enhanced AI match scores
using the candidate's structured resume profile stored in resume_profiles.

Core flow:
  1. get_profile()          → load master resume (seeded from resume_ATS markdown)
  2. update_profile(data)   → edit any field, update DB
  3. tailor_resume(job_id)  → AI-tailor summary/skills/bullets for that JD
  4. generate_apply_package(job_id) → full kit: CV + cover letter + email + LinkedIn + ATS
  5. resume_ai_match(job_id) → enhanced match score using resume vs JD
  6. list_apply_packages()  → paginated packages list for UI

All AI calls go through platform_ai.call() with JSON-only prompts.
Every function has a safe fallback for when AI is unavailable.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from app.db import get_conn
from app.services.platform_ai import call as ai_call

logger = logging.getLogger("secretaryai.resume_service")

# ─── AI system prompt ────────────────────────────────────────────────────────

_RESUME_SYSTEM = (
    "You are a senior technical recruiter and ATS expert specialising in Java/Spring "
    "engineering roles in European tech companies. You generate concise, metric-rich, "
    "ATS-optimised application materials. Always output valid JSON — no markdown, "
    "no code fences, no commentary outside the JSON object."
)


# ─── Profile CRUD ─────────────────────────────────────────────────────────────

def get_profile(user_id: int = 1) -> dict:
    """Return master resume profile. Creates empty profile if none exists."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM resume_profiles WHERE user_id=? LIMIT 1", (user_id,)
        ).fetchone()
    if not row:
        return _empty_profile(user_id)
    p = dict(row)
    for field in ("skills", "certifications", "education", "work_history",
                  "projects", "achievements", "languages", "target_roles",
                  "target_locations"):
        raw = p.get(field)
        if raw:
            try:
                p[field] = json.loads(raw)
            except Exception:
                p[field] = []
        else:
            p[field] = []
    return p


def _empty_profile(user_id: int = 1) -> dict:
    return {
        "id": None, "user_id": user_id,
        "full_name": "", "headline": "", "email": "", "phone": "",
        "location": "", "linkedin_url": "", "github_url": "", "portfolio_url": "",
        "years_experience": 0, "target_roles": [], "target_locations": [],
        "visa_status": "", "relocation_ready": 1,
        "salary_min": 0, "salary_max": 0, "salary_currency": "EUR",
        "summary": "", "skills": [], "certifications": [], "education": [],
        "work_history": [], "projects": [], "achievements": [], "languages": [],
        "raw_text": "", "updated_at": None,
        "_is_empty": True,
    }


def update_profile(data: dict, user_id: int = 1) -> dict:
    """
    Upsert resume profile. JSON fields (lists/dicts) are serialised automatically.
    Returns the updated profile.
    """
    json_fields = {"skills", "certifications", "education", "work_history",
                   "projects", "achievements", "languages", "target_roles",
                   "target_locations"}
    serialised: dict[str, Any] = {}
    for key, val in data.items():
        if key in json_fields:
            serialised[key] = json.dumps(val) if not isinstance(val, str) else val
        else:
            serialised[key] = val

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM resume_profiles WHERE user_id=?", (user_id,)
        ).fetchone()
        if existing:
            set_clauses = ", ".join(f"{k}=?" for k in serialised)
            conn.execute(
                f"UPDATE resume_profiles SET {set_clauses}, updated_at=CURRENT_TIMESTAMP "
                f"WHERE user_id=?",
                list(serialised.values()) + [user_id],
            )
        else:
            cols = ", ".join(["user_id"] + list(serialised.keys()))
            placeholders = ", ".join(["?"] * (1 + len(serialised)))
            conn.execute(
                f"INSERT INTO resume_profiles ({cols}) VALUES ({placeholders})",
                [user_id] + list(serialised.values()),
            )
    return get_profile(user_id)


def profile_blob(profile: dict) -> str:
    """Compact text representation for AI prompts."""
    skills_str = ", ".join(profile.get("skills") or [])
    achievements = "\n".join(f"• {a}" for a in (profile.get("achievements") or []))
    roles_str = ", ".join(
        f"{r['title']} at {r['company']} ({r.get('start','')}–{r.get('end','')})"
        for r in (profile.get("work_history") or [])[:4]
    )
    certs_str = ", ".join(profile.get("certifications") or [])
    return (
        f"CANDIDATE: {profile.get('full_name')} — {profile.get('headline')}\n"
        f"EXPERIENCE: {profile.get('years_experience')} years\n"
        f"LOCATION: {profile.get('location')} | VISA: {profile.get('visa_status')}\n"
        f"SALARY TARGET: €{profile.get('salary_min', 0):,}–€{profile.get('salary_max', 0):,}/yr\n"
        f"SKILLS: {skills_str}\n"
        f"CERTIFICATIONS: {certs_str}\n"
        f"RECENT ROLES: {roles_str}\n"
        f"PROFILE SUMMARY: {profile.get('summary', '')}\n"
        f"KEY ACHIEVEMENTS:\n{achievements}\n"
    )


def has_profile() -> bool:
    """Return True if a profile exists and is populated."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT full_name FROM resume_profiles WHERE user_id=1 LIMIT 1"
        ).fetchone()
    return bool(row and row["full_name"])


# ─── Resume Tailoring ─────────────────────────────────────────────────────────

def tailor_resume(job_id: int) -> dict:
    """
    AI-tailor the resume for a specific job.
    Stores the result in resume_versions and updates apply_kit_ready on the job.
    Returns the version dict.
    """
    profile = get_profile()
    job = _get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    if not profile.get("full_name"):
        return _fallback_tailor(job)

    prompt = (
        f"RESUME PROFILE:\n{profile_blob(profile)}\n\n"
        f"JOB:\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Description: {(job.get('description') or '')[:1500]}\n\n"
        "OUTPUT a JSON object with EXACTLY these keys:\n"
        '{\n'
        '  "tailored_summary": "<2-3 sentence tailored summary starting with seniority + years>",\n'
        '  "tailored_skills": ["skill1", ...],\n'
        '  "ats_keywords": ["kw1", ...],\n'
        '  "ats_score": <integer 0-100>,\n'
        '  "highlighted_bullets": {"CompanyName": ["bullet1", "bullet2"]},\n'
        '  "missing_keywords": ["keyword1", ...],\n'
        '  "tailoring_notes": "<1 sentence advice>"\n'
        '}'
    )
    try:
        raw = ai_call(prompt, system=_RESUME_SYSTEM, max_tokens=800)
        data = _parse_json(raw)
    except Exception as exc:
        logger.error("tailor_resume AI call failed for job %d: %s", job_id, exc)
        data = _fallback_tailor(job)

    # Persist version
    with get_conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO resume_versions
               (remote_job_id, profile_id, version_name, tailored_summary,
                tailored_skills, ats_keywords, ats_score, tailored_bullets,
                missing_keywords, generation_notes)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                job_id, 1,
                f"{job.get('company')} — {job.get('title')}",
                data.get("tailored_summary", ""),
                json.dumps(data.get("tailored_skills") or []),
                json.dumps(data.get("ats_keywords") or []),
                data.get("ats_score", 0),
                json.dumps(data.get("highlighted_bullets") or {}),
                json.dumps(data.get("missing_keywords") or []),
                data.get("tailoring_notes", ""),
            ),
        )
        conn.execute(
            "UPDATE remote_jobs SET apply_kit_ready=1 WHERE id=?", (job_id,)
        )
        version_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    data["version_id"] = version_id
    data["job_id"] = job_id
    logger.info("tailor_resume: job_id=%d ats_score=%s version_id=%d",
                job_id, data.get("ats_score"), version_id)
    return data


def _fallback_tailor(job: dict) -> dict:
    desc_lower = (job.get("description") or "").lower()
    java_kws = [k for k in ("java", "spring boot", "microservices", "kafka",
                             "kubernetes", "docker", "aws", "rest api")
                if k in desc_lower]
    return {
        "tailored_summary": (
            "Senior Java Engineer & Technical Lead with 17+ years building cloud-native "
            "microservices systems. Expert in Spring Boot, event-driven architecture, and "
            "scalable distributed systems on AWS/Azure. Available immediately for remote "
            "contract or Europe relocation."
        ),
        "tailored_skills": ["Java 17", "Spring Boot", "Microservices", "Kafka",
                            "Docker", "Kubernetes", "AWS", "REST APIs", "CI/CD"],
        "ats_keywords": java_kws or ["java", "spring", "microservices"],
        "ats_score": 72,
        "highlighted_bullets": {},
        "missing_keywords": [],
        "tailoring_notes": "AI unavailable — generic tailoring applied.",
        "_fallback": True,
    }


# ─── Apply Package Generation ─────────────────────────────────────────────────

def generate_apply_package(job_id: int, regenerate: bool = False) -> dict:
    """
    Generate a complete apply package for a job:
    cover letter, recruiter email, LinkedIn message, ATS analysis, screening answers.
    Saves to apply_packages table and marks the job as apply_kit_ready.
    """
    # Check cache unless regenerating
    if not regenerate:
        cached = get_apply_package(job_id)
        if cached:
            return cached

    profile = get_profile()
    job = _get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    # Try to get existing tailor data for ATS keywords
    with get_conn() as conn:
        ver = conn.execute(
            "SELECT * FROM resume_versions WHERE remote_job_id=? ORDER BY id DESC LIMIT 1",
            (job_id,)
        ).fetchone()
    tailor_ctx = ""
    if ver:
        kws = ", ".join(json.loads(ver["ats_keywords"] or "[]")[:8])
        tailor_ctx = f"Key ATS keywords for this role: {kws}.\n"

    p_blob = profile_blob(profile) if profile.get("full_name") else _default_profile_blob()
    jd_snippet = (job.get("description") or "")[:1800]

    prompt = (
        f"RESUME PROFILE:\n{p_blob}\n\n"
        f"TARGET JOB:\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Location: {job.get('location')}\nType: {job.get('job_type')}\n"
        f"Description: {jd_snippet}\n{tailor_ctx}\n"
        "Generate a complete application package. Output a single JSON object:\n"
        "{\n"
        '  "cover_letter": "<4-paragraph cover letter — hook, proof, value, CTA>",\n'
        '  "email_subject": "<subject line, max 12 words>",\n'
        '  "recruiter_email": "<full cold email, max 200 words, start with Hi [Hiring Manager]>",\n'
        '  "linkedin_message": "<LinkedIn connection request message, max 250 chars>",\n'
        '  "screening_answers": [\n'
        '    {"question": "Years of Java experience?", "answer": "..."},\n'
        '    {"question": "Are you eligible to work in [country]?", "answer": "..."},\n'
        '    {"question": "Notice period / availability?", "answer": "..."}\n'
        "  ],\n"
        '  "ats_analysis": {\n'
        '    "score": <0-100>,\n'
        '    "matched_keywords": ["kw1", ...],\n'
        '    "missing_keywords": ["kw1", ...],\n'
        '    "suggestions": "<1-2 sentence improvement tips>"\n'
        "  }\n"
        "}"
    )

    try:
        raw = ai_call(prompt, system=_RESUME_SYSTEM, max_tokens=1500)
        data = _parse_json(raw)
    except Exception as exc:
        logger.error("generate_apply_package AI failed for job %d: %s", job_id, exc)
        data = _fallback_package(job, profile)

    # Persist package
    ats = data.get("ats_analysis") or {}
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO apply_packages
               (remote_job_id, profile_id, cover_letter, email_subject,
                recruiter_email, linkedin_message, screening_answers,
                ats_analysis, tailored_resume_json,
                resume_version_id, status, generated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(remote_job_id) DO UPDATE SET
                 cover_letter=excluded.cover_letter,
                 email_subject=excluded.email_subject,
                 recruiter_email=excluded.recruiter_email,
                 linkedin_message=excluded.linkedin_message,
                 screening_answers=excluded.screening_answers,
                 ats_analysis=excluded.ats_analysis,
                 tailored_resume_json=excluded.tailored_resume_json,
                 status='ready',
                 generated_at=CURRENT_TIMESTAMP""",
            (
                job_id, 1,
                data.get("cover_letter", ""),
                data.get("email_subject", ""),
                data.get("recruiter_email", ""),
                data.get("linkedin_message", ""),
                json.dumps(data.get("screening_answers") or []),
                json.dumps(ats),
                json.dumps({}),   # tailored_resume_json — populated from resume_versions if available
                ver["id"] if ver else None,
                "ready",
            ),
        )
        conn.execute(
            "UPDATE remote_jobs SET apply_kit_ready=1, pipeline_stage='READY_TO_APPLY' WHERE id=?",
            (job_id,)
        )

    data["job_id"] = job_id
    data["job"] = {"id": job_id, "title": job.get("title"), "company": job.get("company"),
                   "source_url": job.get("source_url"), "location": job.get("location")}
    data["status"] = "ready"
    logger.info("generate_apply_package: job_id=%d ats_score=%s",
                job_id, ats.get("score"))
    return data


def get_apply_package(job_id: int) -> dict | None:
    """Return saved apply package with job details, or None if not generated yet."""
    with get_conn() as conn:
        row = conn.execute(
            """SELECT ap.*, rj.title, rj.company, rj.source_url,
                      rj.location, rj.job_type, rj.salary_min, rj.salary_max,
                      rj.salary_currency, rj.quick_score, rj.pipeline_stage
               FROM apply_packages ap
               JOIN remote_jobs rj ON rj.id = ap.remote_job_id
               WHERE ap.remote_job_id=?""",
            (job_id,)
        ).fetchone()
    if not row:
        return None
    pkg = dict(row)
    for field in ("screening_answers", "ats_analysis", "tailored_resume_json"):
        raw = pkg.get(field)
        if raw:
            try:
                pkg[field] = json.loads(raw)
            except Exception:
                pkg[field] = {} if field != "screening_answers" else []
        else:
            pkg[field] = {} if field != "screening_answers" else []
    return pkg


def list_apply_packages(page: int = 1, per_page: int = 20) -> dict:
    """Paginated list of all apply packages with job info."""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM apply_packages").fetchone()[0]
        offset = (page - 1) * per_page
        rows = conn.execute(
            """SELECT ap.id, ap.remote_job_id, ap.status, ap.generated_at, ap.applied_at,
                      ap.ats_analysis, ap.email_subject,
                      rj.title, rj.company, rj.source_url, rj.location,
                      rj.quick_score, rj.pipeline_stage, rj.salary_min, rj.salary_max,
                      rj.salary_currency
               FROM apply_packages ap
               JOIN remote_jobs rj ON rj.id = ap.remote_job_id
               ORDER BY ap.generated_at DESC
               LIMIT ? OFFSET ?""",
            (per_page, offset),
        ).fetchall()

    packages = []
    for row in rows:
        pkg = dict(row)
        ats_raw = pkg.get("ats_analysis")
        ats = {}
        if ats_raw:
            try:
                ats = json.loads(ats_raw)
            except Exception:
                pass
        pkg["ats_score"] = ats.get("score", 0)
        pkg["ats_suggestions"] = ats.get("suggestions", "")
        packages.append(pkg)

    return {
        "packages": packages,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, (total + per_page - 1) // per_page),
        "stats": _package_stats(),
    }


def _package_stats() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT COUNT(*) as total,
                      SUM(CASE WHEN status='ready' THEN 1 ELSE 0 END) as ready,
                      SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END) as applied,
                      SUM(CASE WHEN status='draft' THEN 1 ELSE 0 END) as draft
               FROM apply_packages"""
        ).fetchone()
    return dict(row) if row else {"total": 0, "ready": 0, "applied": 0, "draft": 0}


def update_package_status(job_id: int, status: str) -> dict:
    valid = {"draft", "ready", "applied"}
    if status not in valid:
        raise ValueError(f"status must be one of {valid}")
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE apply_packages SET status=?, applied_at=? WHERE remote_job_id=?",
            (status, now if status == "applied" else None, job_id),
        )
        if status == "applied":
            conn.execute(
                "UPDATE remote_jobs SET application_status='applied', "
                "applied_at=?, pipeline_stage='APPLIED' WHERE id=?",
                (now, job_id),
            )
    return {"job_id": job_id, "status": status}


# ─── Resume-Enhanced AI Match ──────────────────────────────────────────────────

def resume_ai_match(job_id: int) -> dict:
    """
    Enhanced match scoring using resume vs job description.
    Returns a more accurate composite score than quick_score alone.
    """
    profile = get_profile()
    job = _get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    if not profile.get("full_name"):
        return _fallback_match(job)

    prompt = (
        f"RESUME:\n{profile_blob(profile)}\n\n"
        f"JOB:\nTitle: {job.get('title')}\nCompany: {job.get('company')}\n"
        f"Type: {job.get('job_type')}\nLocation: {job.get('location')}\n"
        f"Description: {(job.get('description') or '')[:1500]}\n\n"
        "Score this candidate-job match. Output JSON:\n"
        "{\n"
        '  "skills_overlap": <0-100>,\n'
        '  "title_relevance": <0-100>,\n'
        '  "domain_score": <0-100>,\n'
        '  "seniority_fit": <0-100>,\n'
        '  "relocation_fit": <0-100>,\n'
        '  "composite_score": <0-100>,\n'
        '  "matched_skills": ["skill1", ...],\n'
        '  "missing_skills": ["skill1", ...],\n'
        '  "ats_keywords": ["kw1", ...],\n'
        '  "recommendation": "STRONG_APPLY|APPLY|CONSIDER|SKIP",\n'
        '  "analysis": "<2-3 sentences>"\n'
        "}"
    )

    try:
        raw = ai_call(prompt, system=_RESUME_SYSTEM, max_tokens=600)
        data = _parse_json(raw)
    except Exception as exc:
        logger.error("resume_ai_match AI failed for job %d: %s", job_id, exc)
        data = _fallback_match(job)

    # Persist
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO resume_job_matches
               (remote_job_id, profile_id, skills_overlap, title_relevance,
                domain_score, seniority_fit, relocation_fit, composite_score,
                matched_skills, missing_skills, ats_keywords, recommendation, analysis)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(remote_job_id) DO UPDATE SET
                 skills_overlap=excluded.skills_overlap,
                 title_relevance=excluded.title_relevance,
                 domain_score=excluded.domain_score,
                 seniority_fit=excluded.seniority_fit,
                 relocation_fit=excluded.relocation_fit,
                 composite_score=excluded.composite_score,
                 matched_skills=excluded.matched_skills,
                 missing_skills=excluded.missing_skills,
                 ats_keywords=excluded.ats_keywords,
                 recommendation=excluded.recommendation,
                 analysis=excluded.analysis,
                 matched_at=CURRENT_TIMESTAMP""",
            (
                job_id, 1,
                data.get("skills_overlap", 0),
                data.get("title_relevance", 0),
                data.get("domain_score", 0),
                data.get("seniority_fit", 0),
                data.get("relocation_fit", 0),
                data.get("composite_score", 0),
                json.dumps(data.get("matched_skills") or []),
                json.dumps(data.get("missing_skills") or []),
                json.dumps(data.get("ats_keywords") or []),
                data.get("recommendation", "CONSIDER"),
                data.get("analysis", ""),
            ),
        )
        # Also update quick_score on the job if our composite is higher
        if data.get("composite_score", 0) > 0:
            conn.execute(
                "UPDATE remote_jobs SET quick_score=MAX(quick_score, ?) WHERE id=?",
                (data["composite_score"], job_id),
            )

    data["job_id"] = job_id
    return data


def _fallback_match(job: dict) -> dict:
    desc_lower = (job.get("description") or "").lower()
    hits = sum(1 for k in ("java", "spring", "microservices", "kafka", "docker",
                           "kubernetes", "aws", "rest") if k in desc_lower)
    score = min(50 + hits * 5, 85)
    return {
        "skills_overlap": score,
        "title_relevance": 70,
        "domain_score": 75,
        "seniority_fit": 80,
        "relocation_fit": 85,
        "composite_score": score,
        "matched_skills": ["Java", "Spring Boot"],
        "missing_skills": [],
        "ats_keywords": [],
        "recommendation": "APPLY" if score >= 65 else "CONSIDER",
        "analysis": "AI unavailable — estimated from keyword matching.",
        "_fallback": True,
    }


def get_resume_match(job_id: int) -> dict | None:
    """Return stored resume match for a job, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM resume_job_matches WHERE remote_job_id=?", (job_id,)
        ).fetchone()
    if not row:
        return None
    m = dict(row)
    for field in ("matched_skills", "missing_skills", "ats_keywords"):
        raw = m.get(field)
        m[field] = json.loads(raw) if raw else []
    return m


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_job(job_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM remote_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(row) if row else None


def _parse_json(raw: str) -> dict:
    """Extract JSON from AI response, stripping markdown fences if present."""
    text = raw.strip()
    # Strip ```json ... ``` fences
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Find first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    return json.loads(text)


def _default_profile_blob() -> str:
    """Compact profile text when no DB profile exists yet."""
    return (
        "CANDIDATE: Mayank Gaur — Senior Java Engineer & Technical Lead\n"
        "EXPERIENCE: 17 years\n"
        "LOCATION: Bengaluru, India | VISA: chancenkarte_applied\n"
        "SALARY TARGET: €90,000–€100,000/yr\n"
        "SKILLS: Java 17, Spring Boot, Microservices, Kafka, Docker, Kubernetes, "
        "AWS, Azure, REST APIs, React, Angular, CI/CD, JUnit\n"
        "KEY ACHIEVEMENTS:\n"
        "• 40% improvement in system efficiency at Wipro Technologies\n"
        "• Multi-threaded Java system handling 200+ simultaneous transactions (50% throughput increase)\n"
        "• Microservices architecture doubling user load capacity at Pyramid Consulting\n"
    )


def _fallback_package(job: dict, profile: dict) -> dict:
    name = profile.get("full_name") or "Mayank Gaur"
    title = job.get("title", "the role")
    company = job.get("company", "your company")
    return {
        "cover_letter": (
            f"Dear Hiring Manager,\n\n"
            f"I am writing to express strong interest in the {title} position at {company}. "
            f"With 17+ years of Java/Spring Boot engineering experience — including leading "
            f"cross-functional teams and architecting systems serving 1M+ daily users — I am "
            f"confident I can deliver immediate impact.\n\n"
            f"Key highlights: 40% system efficiency improvement at Wipro; multi-threaded Java "
            f"system handling 200+ concurrent transactions; microservices architecture doubling "
            f"user load capacity. AWS Certified, available remotely or for Europe relocation.\n\n"
            f"I would welcome the opportunity to discuss how my background aligns with your needs. "
            f"Please find my resume attached.\n\nBest regards,\n{name}"
        ),
        "email_subject": f"Senior Java Engineer — 17yrs Spring Boot/Microservices — {company}",
        "recruiter_email": (
            f"Hi,\n\nI came across the {title} opening at {company} and wanted to reach out directly.\n\n"
            f"I'm a Senior Java Engineer/Technical Lead with 17+ years in Spring Boot, microservices, "
            f"Kafka, Docker/K8s, and AWS. Recent highlights: 40% latency reduction, systems at 1M+ "
            f"DAU, team of 10+ engineers. Available remotely, open to Europe relocation.\n\n"
            f"Would a brief call make sense? Happy to share my profile.\n\nBest,\n{name}"
        ),
        "linkedin_message": (
            f"Hi, I'm a Senior Java/Spring Boot engineer (17yrs) interested in opportunities at "
            f"{company}. Built 1M-DAU systems, led teams of 10+. Open for remote/Europe roles. "
            f"Worth a chat?"
        ),
        "screening_answers": [
            {"question": "Years of Java experience?", "answer": "17+ years, including Java 8, 11, and 17."},
            {"question": "Are you eligible to work in the EU?", "answer": "Applied for German Chancenkarte; open to sponsorship and relocation."},
            {"question": "Notice period?", "answer": "Available immediately for remote contract; 4 weeks for relocation."},
        ],
        "ats_analysis": {
            "score": 72,
            "matched_keywords": ["java", "spring boot", "microservices"],
            "missing_keywords": [],
            "suggestions": "AI unavailable — fallback package generated.",
        },
        "_fallback": True,
    }
