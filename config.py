"""Configuration for the Executive Travel & Job Search Secretary Agent."""

import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()

# Model
MODEL = "claude-opus-4-6"
MAX_TOKENS = 8192

# Paths
WORKSPACE = Path(__file__).resolve().parent
DEFAULT_MEMORY_DIR = (
    Path.home()
    / ".claude/projects/-Users-mayankgaur-Documents-Travel-Job-search-Agent/memory"
)
MEMORY_DIR = Path(os.getenv("SECRETARY_MEMORY_DIR", DEFAULT_MEMORY_DIR))
DB_PATH = Path(os.getenv("SECRETARY_APP_DB", WORKSPACE / "secretary_agent.db"))
TEMPLATES_DIR = WORKSPACE / "templates"
STATIC_DIR = WORKSPACE / "static"
AUTOMATION_DIR = WORKSPACE / "automation_payloads"
WHATSAPP_REPLY_CHAR_LIMIT = int(os.getenv("WHATSAPP_REPLY_CHAR_LIMIT", "1400"))

DOCS = {
    "resume_ats":        WORKSPACE / "resume_ATS_germany_english.md",
    "master_plan":       WORKSPACE / "germany_master_plan.md",
    "target_companies":  WORKSPACE / "target_companies_germany_dubai.md",
    "applications":      WORKSPACE / "tier1_application_packages.md",
    "templates":         WORKSPACE / "application_templates.md",
    "interview_prep":    WORKSPACE / "interview_prep_java_germany.md",
    "freelance":         WORKSPACE / "freelance_business_setup.md",
    "financial_plan":    WORKSPACE / "financial_plan_germany_move.md",
    "dubai_track":       WORKSPACE / "dubai_parallel_track.md",
    "travel_checklist":  WORKSPACE / "germany_travel_checklist.md",
    "automation":        WORKSPACE / "weekly_automation_routine.md",
    "job_tracker":       WORKSPACE / "job_tracker_template.md",
}

MEMORY_FILES = {
    "profile":   MEMORY_DIR / "user_profile.md",
    "chancenkarte": MEMORY_DIR / "project_germany_opportunity_card.md",
}
