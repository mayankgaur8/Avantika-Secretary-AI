"""SQLite database helpers for the local secretary app."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT 'User',
    email TEXT UNIQUE,
    phone TEXT UNIQUE,
    plan TEXT DEFAULT 'free',
    plan_expires_at TEXT,
    timezone TEXT DEFAULT 'Asia/Kolkata',
    whatsapp_opt_in INTEGER DEFAULT 0,
    email_opt_in INTEGER DEFAULT 1,
    onboarding_complete INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    current_role TEXT,
    current_company TEXT,
    years_experience INTEGER,
    current_salary INTEGER,
    current_salary_currency TEXT DEFAULT 'INR',
    target_roles TEXT,
    target_countries TEXT,
    target_salary_min INTEGER,
    target_salary_max INTEGER,
    target_salary_currency TEXT DEFAULT 'EUR',
    visa_status TEXT DEFAULT 'requires_sponsorship',
    remote_preference TEXT DEFAULT 'hybrid',
    relocation_readiness TEXT DEFAULT '3months',
    notes TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS travel_profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    home_city TEXT DEFAULT 'Bangalore',
    home_airport TEXT DEFAULT 'BLR',
    preferred_airlines TEXT,
    loyalty_programs TEXT,
    seat_preference TEXT DEFAULT 'aisle',
    hotel_preference TEXT DEFAULT 'business',
    typical_budget_range TEXT,
    passport_countries TEXT DEFAULT 'India',
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    current_role TEXT,
    location TEXT,
    experience_years INTEGER,
    target_roles TEXT,
    target_locations TEXT,
    visa_status TEXT,
    salary_target_min INTEGER,
    salary_target_max INTEGER,
    salary_currency TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS job_leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    role_title TEXT NOT NULL,
    country TEXT,
    city TEXT,
    source TEXT,
    apply_url TEXT,
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency TEXT,
    visa_support TEXT,
    match_score REAL DEFAULT 0,
    credibility_score REAL DEFAULT 0,
    priority_score REAL DEFAULT 0,
    status TEXT DEFAULT 'shortlisted',
    pipeline_stage TEXT DEFAULT 'Identified',
    lead_type TEXT DEFAULT 'target_role',
    contact_name TEXT,
    contact_email TEXT,
    applied_date TEXT,
    last_action_date TEXT,
    next_action TEXT,
    next_action_due TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(company, role_title, city)
);

CREATE TABLE IF NOT EXISTS company_watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    company_name TEXT NOT NULL,
    company_domain TEXT,
    company_size TEXT,
    tech_stack TEXT,
    glassdoor_rating REAL,
    visa_sponsorship TEXT,
    alert_on_new_job INTEGER DEFAULT 1,
    job_keywords TEXT,
    notes TEXT,
    last_checked_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_lead_id INTEGER NOT NULL,
    stage TEXT NOT NULL DEFAULT 'Applied',
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP,
    next_action TEXT,
    follow_up_due TEXT,
    contact_name TEXT,
    contact_channel TEXT,
    submission_proof TEXT,
    verified_applied INTEGER DEFAULT 0,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(job_lead_id) REFERENCES job_leads(id)
);

CREATE TABLE IF NOT EXISTS application_drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_lead_id INTEGER NOT NULL UNIQUE,
    tailored_summary TEXT NOT NULL,
    resume_keywords TEXT NOT NULL,
    recruiter_message TEXT NOT NULL,
    cover_letter TEXT NOT NULL,
    form_answers TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(job_lead_id) REFERENCES job_leads(id)
);

CREATE TABLE IF NOT EXISTS travel_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    depart_date TEXT,
    return_date TEXT,
    traveler_count INTEGER DEFAULT 1,
    baggage TEXT,
    budget INTEGER,
    currency TEXT DEFAULT 'EUR',
    purpose TEXT,
    notes TEXT,
    status TEXT DEFAULT 'planning',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS price_watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    watch_type TEXT NOT NULL,
    route_or_property TEXT NOT NULL,
    date_range TEXT,
    target_price INTEGER,
    current_price INTEGER,
    currency TEXT DEFAULT 'INR',
    active INTEGER DEFAULT 1,
    last_checked_at TEXT,
    alert_sent_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    reminder_type TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT,
    scheduled_for TEXT NOT NULL,
    channel TEXT DEFAULT 'whatsapp',
    related_entity_type TEXT,
    related_entity_id INTEGER,
    status TEXT DEFAULT 'pending',
    sent_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS visa_tracker (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    visa_type TEXT NOT NULL,
    target_country TEXT NOT NULL,
    application_status TEXT DEFAULT 'preparing',
    submitted_date TEXT,
    decision_date TEXT,
    expiry_date TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS visa_checklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    visa_tracker_id INTEGER NOT NULL,
    item TEXT NOT NULL,
    completed INTEGER DEFAULT 0,
    due_date TEXT,
    notes TEXT,
    sort_order INTEGER DEFAULT 0,
    FOREIGN KEY(visa_tracker_id) REFERENCES visa_tracker(id)
);

CREATE TABLE IF NOT EXISTS travel_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    travel_request_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    category TEXT NOT NULL,
    price INTEGER NOT NULL,
    currency TEXT DEFAULT 'EUR',
    duration_hours REAL,
    stops INTEGER DEFAULT 0,
    baggage_included INTEGER DEFAULT 0,
    cancellation_flexibility TEXT,
    transfer_risk TEXT,
    summary TEXT,
    booking_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(travel_request_id) REFERENCES travel_requests(id)
);

CREATE TABLE IF NOT EXISTS accommodation_options (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    travel_request_id INTEGER NOT NULL,
    provider TEXT NOT NULL,
    stay_type TEXT NOT NULL,
    category TEXT NOT NULL,
    price_per_night INTEGER NOT NULL,
    total_price INTEGER NOT NULL,
    currency TEXT DEFAULT 'EUR',
    cancellation_flexibility TEXT,
    safety_level TEXT,
    summary TEXT,
    booking_url TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(travel_request_id) REFERENCES travel_requests(id)
);

CREATE TABLE IF NOT EXISTS travel_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    travel_request_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'draft',
    recommended_flight_option_id INTEGER,
    recommended_stay_option_id INTEGER,
    confirmed_flight_option_id INTEGER,
    confirmed_stay_option_id INTEGER,
    recommendation_notes TEXT,
    confirmed_at TEXT,
    booking_status TEXT DEFAULT 'pending_confirmation',
    booking_notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(travel_request_id) REFERENCES travel_requests(id)
);

CREATE TABLE IF NOT EXISTS automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type TEXT NOT NULL,
    target_id INTEGER,
    status TEXT NOT NULL,
    details TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender TEXT NOT NULL,
    profile_name TEXT,
    direction TEXT NOT NULL,
    message_text TEXT NOT NULL,
    twilio_sid TEXT,
    wa_intent TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS weekly_briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    briefing_text TEXT,
    sent_via TEXT DEFAULT 'email',
    sent_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

-- ─── REMOTE JOB DISCOVERY MODULE ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS remote_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    external_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    title TEXT NOT NULL,
    company TEXT NOT NULL,
    location TEXT,
    country TEXT,
    remote_type TEXT DEFAULT 'remote',
    job_type TEXT DEFAULT 'fulltime',
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency TEXT DEFAULT 'EUR',
    hourly_rate_min INTEGER,
    hourly_rate_max INTEGER,
    description TEXT,
    tags TEXT,
    posted_at TEXT,
    is_europe_friendly INTEGER DEFAULT 0,
    is_saved INTEGER DEFAULT 0,
    is_hidden INTEGER DEFAULT 0,
    application_status TEXT DEFAULT 'new',
    applied_at TEXT,
    follow_up_date TEXT,
    notes TEXT,
    resume_used TEXT,
    contact_person TEXT,
    salary_discussed INTEGER,
    quick_score INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS remote_job_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_job_id INTEGER NOT NULL UNIQUE,
    match_score INTEGER DEFAULT 0,
    match_reasons TEXT,
    missing_skills TEXT,
    salary_assessment TEXT,
    europe_score INTEGER DEFAULT 0,
    travel_fund_score INTEGER DEFAULT 0,
    estimated_monthly_eur INTEGER,
    match_explanation TEXT,
    matched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(remote_job_id) REFERENCES remote_jobs(id)
);

CREATE TABLE IF NOT EXISTS remote_proposals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_job_id INTEGER NOT NULL,
    proposal_type TEXT,
    content TEXT,
    generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(remote_job_id) REFERENCES remote_jobs(id)
);

-- ─── APPLY ENGINE MODULE ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS apply_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_job_id INTEGER NOT NULL,
    proposal_type TEXT,
    proposal_content TEXT,
    applied_at TEXT DEFAULT CURRENT_TIMESTAMP,
    response_received INTEGER DEFAULT 0,
    response_type TEXT,
    response_at TEXT,
    days_to_response INTEGER,
    notes TEXT,
    FOREIGN KEY(remote_job_id) REFERENCES remote_jobs(id)
);

CREATE TABLE IF NOT EXISTS smart_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_job_id INTEGER,
    alert_type TEXT NOT NULL,
    alert_message TEXT,
    alert_data TEXT,
    is_read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(remote_job_id) REFERENCES remote_jobs(id)
);

-- ─── CLIENT ACQUISITION & REVENUE ENGINE ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS outreach_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL COLLATE NOCASE UNIQUE,
    domain TEXT,
    linkedin_url TEXT,
    website TEXT,
    industry TEXT,
    company_size TEXT,
    tech_stack TEXT,
    hiring_signal TEXT,
    source TEXT DEFAULT 'manual',
    remote_job_id INTEGER,
    revenue_potential INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    notes TEXT,
    last_contacted_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(remote_job_id) REFERENCES remote_jobs(id)
);

CREATE TABLE IF NOT EXISTS outreach_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    message_type TEXT NOT NULL,
    subject TEXT,
    content TEXT NOT NULL,
    contact_name TEXT,
    contact_email TEXT,
    contact_linkedin TEXT,
    status TEXT DEFAULT 'draft',
    sent_at TEXT,
    responded_at TEXT,
    response_type TEXT,
    response_content TEXT,
    conversion_value INTEGER,
    ai_hook_score INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(company_id) REFERENCES outreach_companies(id)
);

CREATE TABLE IF NOT EXISTS outreach_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    template_type TEXT,
    subject_template TEXT,
    content_template TEXT,
    use_count INTEGER DEFAULT 0,
    response_count INTEGER DEFAULT 0,
    conversion_count INTEGER DEFAULT 0,
    is_active INTEGER DEFAULT 1,
    is_builtin INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ─── RESUME-AWARE APPLICATION ENGINE ──────────────────────────────────────────

CREATE TABLE IF NOT EXISTS resume_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER DEFAULT 1,
    full_name TEXT,
    headline TEXT,
    email TEXT,
    phone TEXT,
    location TEXT,
    linkedin_url TEXT,
    github_url TEXT,
    portfolio_url TEXT,
    years_experience INTEGER DEFAULT 0,
    target_roles TEXT,
    target_locations TEXT,
    visa_status TEXT,
    relocation_ready INTEGER DEFAULT 1,
    salary_min INTEGER,
    salary_max INTEGER,
    salary_currency TEXT DEFAULT 'EUR',
    summary TEXT,
    skills TEXT,
    certifications TEXT,
    education TEXT,
    work_history TEXT,
    projects TEXT,
    achievements TEXT,
    languages TEXT,
    raw_text TEXT,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS resume_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_job_id INTEGER,
    profile_id INTEGER DEFAULT 1,
    version_name TEXT,
    tailored_summary TEXT,
    tailored_skills TEXT,
    tailored_bullets TEXT,
    ats_keywords TEXT,
    ats_score INTEGER DEFAULT 0,
    missing_keywords TEXT,
    generation_notes TEXT,
    resume_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(remote_job_id) REFERENCES remote_jobs(id)
);

CREATE TABLE IF NOT EXISTS apply_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_job_id INTEGER NOT NULL UNIQUE,
    profile_id INTEGER DEFAULT 1,
    cover_letter TEXT,
    email_subject TEXT,
    recruiter_email TEXT,
    linkedin_message TEXT,
    screening_answers TEXT,
    ats_analysis TEXT,
    tailored_resume_json TEXT,
    resume_version_id INTEGER,
    status TEXT DEFAULT 'draft',
    generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    applied_at TEXT,
    FOREIGN KEY(remote_job_id) REFERENCES remote_jobs(id)
);

CREATE TABLE IF NOT EXISTS resume_job_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    remote_job_id INTEGER NOT NULL UNIQUE,
    profile_id INTEGER DEFAULT 1,
    skills_overlap INTEGER DEFAULT 0,
    title_relevance INTEGER DEFAULT 0,
    domain_score INTEGER DEFAULT 0,
    seniority_fit INTEGER DEFAULT 0,
    relocation_fit INTEGER DEFAULT 0,
    composite_score INTEGER DEFAULT 0,
    matched_skills TEXT,
    missing_skills TEXT,
    ats_keywords TEXT,
    recommendation TEXT,
    analysis TEXT,
    matched_at TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(remote_job_id) REFERENCES remote_jobs(id)
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        _seed_defaults(conn)
        conn.commit()


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _migrate(conn: sqlite3.Connection) -> None:
    # job_leads migrations
    if not _has_column(conn, "job_leads", "lead_type"):
        conn.execute("ALTER TABLE job_leads ADD COLUMN lead_type TEXT DEFAULT 'target_role'")
    if not _has_column(conn, "job_leads", "pipeline_stage"):
        conn.execute("ALTER TABLE job_leads ADD COLUMN pipeline_stage TEXT DEFAULT 'Identified'")
    if not _has_column(conn, "job_leads", "contact_name"):
        conn.execute("ALTER TABLE job_leads ADD COLUMN contact_name TEXT")
    if not _has_column(conn, "job_leads", "contact_email"):
        conn.execute("ALTER TABLE job_leads ADD COLUMN contact_email TEXT")
    if not _has_column(conn, "job_leads", "applied_date"):
        conn.execute("ALTER TABLE job_leads ADD COLUMN applied_date TEXT")
    if not _has_column(conn, "job_leads", "last_action_date"):
        conn.execute("ALTER TABLE job_leads ADD COLUMN last_action_date TEXT")
    if not _has_column(conn, "job_leads", "next_action"):
        conn.execute("ALTER TABLE job_leads ADD COLUMN next_action TEXT")
    if not _has_column(conn, "job_leads", "next_action_due"):
        conn.execute("ALTER TABLE job_leads ADD COLUMN next_action_due TEXT")
    if not _has_column(conn, "job_leads", "review_status"):
        conn.execute("ALTER TABLE job_leads ADD COLUMN review_status TEXT DEFAULT 'approved'")
    # applications migrations
    if not _has_column(conn, "applications", "submission_proof"):
        conn.execute("ALTER TABLE applications ADD COLUMN submission_proof TEXT")
    if not _has_column(conn, "applications", "verified_applied"):
        conn.execute("ALTER TABLE applications ADD COLUMN verified_applied INTEGER DEFAULT 0")
    # travel_requests migrations
    if not _has_column(conn, "travel_requests", "status"):
        conn.execute("ALTER TABLE travel_requests ADD COLUMN status TEXT DEFAULT 'planning'")
    # whatsapp_messages migrations
    if not _has_column(conn, "whatsapp_messages", "wa_intent"):
        conn.execute("ALTER TABLE whatsapp_messages ADD COLUMN wa_intent TEXT")
    # remote_jobs migrations (new columns added over time).
    # Guard each block individually — no early return, so ALL migrations always run.
    if _has_table(conn, "remote_jobs"):
        if not _has_column(conn, "remote_jobs", "quick_score"):
            conn.execute("ALTER TABLE remote_jobs ADD COLUMN quick_score INTEGER DEFAULT 0")
        if not _has_column(conn, "remote_jobs", "follow_up_date"):
            conn.execute("ALTER TABLE remote_jobs ADD COLUMN follow_up_date TEXT")
        if not _has_column(conn, "remote_jobs", "salary_discussed"):
            conn.execute("ALTER TABLE remote_jobs ADD COLUMN salary_discussed INTEGER")
        # Apply Engine additions
        if not _has_column(conn, "remote_jobs", "pipeline_stage"):
            conn.execute("ALTER TABLE remote_jobs ADD COLUMN pipeline_stage TEXT DEFAULT 'DISCOVERED'")
        if not _has_column(conn, "remote_jobs", "income_priority_score"):
            conn.execute("ALTER TABLE remote_jobs ADD COLUMN income_priority_score INTEGER DEFAULT 0")
        if not _has_column(conn, "remote_jobs", "is_fast_pay"):
            conn.execute("ALTER TABLE remote_jobs ADD COLUMN is_fast_pay INTEGER DEFAULT 0")
        if not _has_column(conn, "remote_jobs", "apply_kit_ready"):
            conn.execute("ALTER TABLE remote_jobs ADD COLUMN apply_kit_ready INTEGER DEFAULT 0")
        if not _has_column(conn, "remote_jobs", "last_stage_changed_at"):
            conn.execute("ALTER TABLE remote_jobs ADD COLUMN last_stage_changed_at TEXT")
    # Client Acquisition seeding (always runs regardless of remote_jobs state)
    if _has_table(conn, "outreach_companies"):
        _seed_outreach_templates(conn)
    # Resume Profile seeding — pre-populate from known resume data
    if _has_table(conn, "resume_profiles"):
        _seed_resume_profile(conn)


def _seed_outreach_templates(conn: sqlite3.Connection) -> None:
    """Seed high-conversion built-in outreach templates (runs once)."""
    if conn.execute("SELECT COUNT(*) FROM outreach_templates WHERE is_builtin=1").fetchone()[0] > 0:
        return
    templates = [
        (
            "Problem-Aware Hook",
            "hook_message",
            None,
            "I noticed {company} is scaling its {tech} stack — I've helped 3 teams do exactly this, "
            "cutting deploy cycles from 45 min to 8 min and reducing infra cost by 30%. "
            "Worth a quick 10-min call?",
        ),
        (
            "Social Proof LinkedIn DM",
            "linkedin_dm",
            None,
            "Hi {name}, I architected a Spring Boot microservices platform serving 1.2M daily users — "
            "saw {company} is building something similar. I'm available for contract/consulting work "
            "and could add immediate value. Open to a quick chat?",
        ),
        (
            "Direct Value Email",
            "email_pitch",
            "Java/Spring Lead available for contract — 17 yrs exp, immediate start",
            "Hi {name},\n\nI'm a Java Full-Stack Technical Lead with 17 years of experience "
            "specialising in Spring Boot microservices, Kafka event-driven systems, and cloud "
            "(AWS/Azure/K8s).\n\nRecent impact:\n"
            "• Reduced API latency 40% via async refactor + Redis caching\n"
            "• Led 12-service microservices migration from monolith\n"
            "• System serving 1.2M DAUs at 99.9% uptime\n\n"
            "I'm available for remote contract work (€70-100/hr) and could start within 5 business days.\n\n"
            "Would a 15-minute scoping call make sense this week?\n\nBest,\nMayank Gaur",
        ),
        (
            "Pain Point Opener",
            "linkedin_dm",
            None,
            "Hey {name} — senior Java teams often lose weeks when modernising legacy systems. "
            "I've done it 3× (monolith → microservices, CI/CD from scratch, Kafka event pipelines). "
            "If {company} has similar challenges, I'm available as a hands-on contractor. "
            "5 min call?",
        ),
        (
            "Follow-Up (No Response)",
            "follow_up",
            "Following up — Java contract availability",
            "Hi {name},\n\nFollowing up on my message from last week. "
            "Still available for remote Java/Spring contract work — happy to do a no-obligation "
            "30-min architecture review call to see if there's a fit.\n\nBest, Mayank",
        ),
    ]
    for name, ttype, subject, content in templates:
        conn.execute(
            """INSERT OR IGNORE INTO outreach_templates
               (name, template_type, subject_template, content_template, is_builtin)
               VALUES (?,?,?,?,1)""",
            (name, ttype, subject, content),
        )


def _seed_resume_profile(conn: sqlite3.Connection) -> None:
    """Pre-populate resume_profiles with Mayank's known resume data (runs once)."""
    import json as _json
    if conn.execute("SELECT COUNT(*) FROM resume_profiles WHERE user_id=1").fetchone()[0] > 0:
        return

    skills = _json.dumps([
        "Java 17", "Java 11", "Java 8", "Spring Boot", "Spring Framework",
        "Spring Security", "Microservices", "REST APIs", "GraphQL",
        "Hibernate ORM", "JPA", "Apache Kafka", "JMS", "Event-Driven Architecture",
        "React", "Angular", "AWS Lambda", "AWS EC2", "AWS RDS", "AWS SQS",
        "AWS API Gateway", "Azure", "Docker", "Kubernetes", "Jenkins",
        "CI/CD Pipelines", "Git", "GitHub", "Maven", "SonarQube",
        "OAuth2", "JWT", "JUnit", "Mockito", "TDD", "Postman", "Swagger",
        "SQL", "PostgreSQL", "MySQL", "Redis", "Multithreading", "Java Concurrency",
        "JVM Monitoring", "GC Optimization", "Design Patterns", "Agile", "Scrum",
        "JIRA", "System Design", "Linux",
    ])
    certifications = _json.dumps([
        "AWS Certified Solutions Architect — Associate Level",
        "Spring Boot Professional Training Certification",
        "Team & Project Management Certification (Projektmanagement)",
    ])
    education = _json.dumps([
        {
            "degree": "Master of Computer Applications (MCA)",
            "field": "Applications and Software Development",
            "institution": "IGNOU, New Delhi",
            "year_start": 2002,
            "year_end": 2005,
        },
        {
            "degree": "Post Graduate Diploma in Computer Application",
            "field": "Computer Application",
            "institution": "IGNOU, New Delhi",
            "year_start": 2001,
            "year_end": 2002,
        },
        {
            "degree": "Bachelor of Commerce (B.COM)",
            "field": "Accounting and Finance",
            "institution": "Allahabad University",
            "year_start": 1998,
            "year_end": 2000,
        },
    ])
    work_history = _json.dumps([
        {
            "title": "Project Lead",
            "company": "Wipro Technologies",
            "location": "Bengaluru, India",
            "start": "Feb 2021",
            "end": "Present",
            "bullets": [
                "Designed and deployed a Java-based performance monitoring tool delivering 40% improvement in system efficiency and 20% increase in client satisfaction across 500+ client touchpoints.",
                "Led cross-functional delivery teams ensuring 100% on-time project completion across all active engagements.",
                "Built a standardized documentation framework covering 15 concurrent projects, cutting onboarding time by 30% and reducing data retrieval time by 25%.",
                "Established KPIs and reporting structures for 500+ client feedback loops, improving accountability by 25%.",
                "Technologies: Java 17, Spring Boot, Microservices, REST API, AWS, Docker, Kubernetes, Kafka, Agile/Scrum, JIRA, SonarQube",
            ],
        },
        {
            "title": "Java Consultant",
            "company": "Virtusa Consulting Services",
            "location": "Bengaluru, India",
            "start": "Aug 2018",
            "end": "Aug 2020",
            "bullets": [
                "Rewrote legacy codebase achieving 40% reduction in application load times and 25% increase in user retention.",
                "Designed multi-threaded Java architecture supporting 200+ simultaneous transactions with 50% throughput increase.",
                "Led architectural design of distributed Java systems focusing on scalability, resilience, and cloud-native patterns.",
                "Technologies: Java 11, Spring Boot, Multithreading, JVM Monitoring, GC Optimization, Hibernate ORM, REST API, CI/CD",
            ],
        },
        {
            "title": "Senior Software Developer",
            "company": "TEKsystems",
            "location": "Bengaluru, India",
            "start": "Sep 2017",
            "end": "Aug 2018",
            "bullets": [
                "Mentored 6 junior developers in Java best practices, design patterns, and TDD.",
                "Performed systematic code refactoring delivering 25% improvement in API response times.",
                "Technologies: Java 8, Spring Framework, RESTful APIs, Design Patterns, TDD, Git, Maven",
            ],
        },
        {
            "title": "Java Consultant",
            "company": "Pyramid Consulting",
            "location": "Bengaluru, India",
            "start": "Sep 2016",
            "end": "Feb 2017",
            "bullets": [
                "Designed microservices architecture increasing platform user load capacity by 2x.",
                "Implemented CI/CD pipelines using Jenkins, significantly reducing time-to-market.",
                "Technologies: Java 8, Microservices, Spring Boot, CI/CD, Jenkins, Docker, Multithreading",
            ],
        },
        {
            "title": "Senior Software Developer",
            "company": "Object Technology Solutions",
            "location": "Bengaluru, India",
            "start": "Feb 2016",
            "end": "Aug 2016",
            "bullets": [
                "Identified and resolved critical performance bottlenecks across core Java applications.",
                "Technologies: Core Java, J2EE, Spring, SQL, Performance Tuning",
            ],
        },
        {
            "title": "Java Consultant",
            "company": "Blue Star Infotech",
            "location": "Bengaluru, India",
            "start": "Feb 2015",
            "end": "Nov 2015",
            "bullets": [
                "Conducted code audit resulting in 15% increase in transaction handling capacity.",
                "Implemented multi-threaded solutions and evaluated third-party library integrations.",
            ],
        },
        {
            "title": "Technical Lead",
            "company": "Wipro Technologies",
            "location": "Bengaluru, India",
            "start": "Jan 2010",
            "end": "May 2014",
            "bullets": [
                "Led 1-on-1 mentoring of junior developers and coordinated cross-department delivery.",
                "Implemented CI/CD pipelines enabling faster release cycles.",
                "Established performance monitoring systems minimizing production downtime.",
            ],
        },
    ])
    achievements = _json.dumps([
        "Engineered a Java performance optimization tool at Wipro delivering 40% system efficiency increase and 20% improvement in client satisfaction across 500+ touchpoints.",
        "Architected a multi-threaded Java system at Virtusa handling 200+ simultaneous transactions — 50% throughput increase and 40% load time reduction.",
        "Designed microservices architecture at Pyramid Consulting that doubled user load capacity (2x) and accelerated time-to-market via CI/CD.",
        "Created standardized documentation framework used across 15 projects, reducing onboarding time by 30% and search time by 25%.",
        "Established KPIs and accountability frameworks for cross-functional teams resulting in 25% increase in team accountability and sustained on-time delivery.",
    ])
    target_roles = _json.dumps(["Senior Java Engineer", "Technical Lead", "Software Architect", "Java Contractor", "Spring Boot Consultant"])
    target_locations = _json.dumps(["Germany", "Berlin", "Munich", "Frankfurt", "Hamburg", "Stuttgart", "Düsseldorf", "Netherlands", "Amsterdam", "Remote"])
    languages = _json.dumps([
        {"language": "English", "level": "C1 — Advanced (business fluent, interview-ready)"},
        {"language": "German", "level": "A1 — Beginner (actively learning)"},
    ])
    summary = (
        "Results-driven Senior Java Engineer and Technical Lead with 17+ years designing and delivering "
        "cloud-native, microservices-based enterprise applications. Proven track record leading cross-functional "
        "teams of 10+ developers, driving 40% performance gains, and delivering 100% on-time project completion "
        "across global clients. Deep expertise in Spring Boot, event-driven architecture (Apache Kafka), REST APIs, "
        "AWS, Docker, and Kubernetes. Available for immediate remote contract or relocation to Germany/Europe. "
        "Seeking Senior Java Lead or Software Architect roles at €90,000–€100,000."
    )
    conn.execute(
        """INSERT INTO resume_profiles
           (user_id, full_name, headline, email, phone, location, linkedin_url,
            years_experience, target_roles, target_locations, visa_status,
            relocation_ready, salary_min, salary_max, salary_currency,
            summary, skills, certifications, education, work_history,
            achievements, languages)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            1,
            "Mayank Gaur",
            "Senior Java Engineer & Technical Lead | 17+ Years | Spring Boot · Microservices · AWS · React",
            "mayankgaur.8@gmail.com",
            "+91 9620439138",
            "Bengaluru, India",
            "https://linkedin.com/in/mayank-gaur8/",
            17,
            target_roles,
            target_locations,
            "chancenkarte_applied",
            1,
            90000,
            100000,
            "EUR",
            summary,
            skills,
            certifications,
            education,
            work_history,
            achievements,
            languages,
        ),
    )


def _seed_defaults(conn: sqlite3.Connection) -> None:
    """Seed a default user and profiles if none exist."""
    row = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
    if row:
        return
    conn.execute(
        """INSERT INTO users (name, email, plan, onboarding_complete)
           VALUES ('Mayank Gaur', 'admin@secretaryai.com', 'pro', 1)"""
    )
    conn.execute(
        """INSERT INTO job_profile
           (user_id, current_role, current_company, years_experience, current_salary,
            current_salary_currency, target_roles, target_countries,
            target_salary_min, target_salary_max, target_salary_currency,
            visa_status, remote_preference, relocation_readiness)
           VALUES (1, 'Java Lead', 'Current Company', 17, 4500000,
                   'INR', 'Java Lead,Engineering Manager,Principal Engineer',
                   'DE,AE,CA,IE', 80000, 120000, 'EUR',
                   'requires_sponsorship', 'hybrid', '3months')"""
    )
    conn.execute(
        """INSERT INTO travel_profile
           (user_id, home_city, home_airport, seat_preference, hotel_preference, passport_countries)
           VALUES (1, 'Bangalore', 'BLR', 'aisle', 'business', 'India')"""
    )
    # Seed default visa tracker for Germany Opportunity Card
    conn.execute(
        """INSERT INTO visa_tracker
           (user_id, visa_type, target_country, application_status, notes)
           VALUES (1, 'German Opportunity Card (Chancenkarte)', 'Germany',
                   'submitted',
                   'Applied Q1 2026. 1-year job search visa. Blue Card conversion path after employment.')"""
    )
    visa_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    checklist = [
        ("Passport (valid 6+ months)", 1, None),
        ("Degree certificates apostilled", 1, None),
        ("German translation of documents", 1, None),
        ("Proof of funds (€5,000+ in account)", 1, None),
        ("Biometric photos", 1, None),
        ("Health insurance proof", 0, "2026-04-15"),
        ("Application fee payment receipt", 1, None),
        ("Open bank account in Germany", 0, "2026-06-01"),
        ("Register address (Anmeldung) on arrival", 0, None),
        ("Tax ID (Steueridentifikationsnummer)", 0, None),
        ("Health insurance (public scheme)", 0, None),
        ("Pension registration", 0, None),
    ]
    for i, (item, completed, due) in enumerate(checklist):
        conn.execute(
            "INSERT INTO visa_checklist (visa_tracker_id, item, completed, due_date, sort_order) VALUES (?,?,?,?,?)",
            (visa_id, item, completed, due, i),
        )


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
