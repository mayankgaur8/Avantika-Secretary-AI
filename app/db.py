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
