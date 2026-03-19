"""Company tracker service — watch, enrich, and monitor target companies."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import get_conn

DEFAULT_COMPANIES = [
    {"company_name": "SAP SE", "company_domain": "sap.com", "company_size": "100,000+",
     "tech_stack": "Java, ABAP, Cloud, Kubernetes", "glassdoor_rating": 4.1,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Lead,Principal,Architect"},
    {"company_name": "Siemens", "company_domain": "siemens.com", "company_size": "300,000+",
     "tech_stack": "Java, Python, IoT, Azure", "glassdoor_rating": 3.9,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Senior,Lead,Backend"},
    {"company_name": "Deutsche Telekom", "company_domain": "telekom.de", "company_size": "200,000+",
     "tech_stack": "Java, Microservices, AWS, Kubernetes", "glassdoor_rating": 3.7,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Lead,Cloud,DevOps"},
    {"company_name": "Zalando", "company_domain": "zalando.de", "company_size": "15,000+",
     "tech_stack": "Java, Kotlin, Kafka, AWS", "glassdoor_rating": 4.0,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Kotlin,Lead,Senior"},
    {"company_name": "Bosch", "company_domain": "bosch.com", "company_size": "400,000+",
     "tech_stack": "Java, C++, Embedded, IoT", "glassdoor_rating": 4.0,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Lead,Senior,Architect"},
    {"company_name": "IONOS", "company_domain": "ionos.com", "company_size": "10,000+",
     "tech_stack": "Java, Go, Cloud, Kubernetes", "glassdoor_rating": 3.8,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Backend,Lead"},
    {"company_name": "Namics / Merkle", "company_domain": "namics.com", "company_size": "1,000+",
     "tech_stack": "Java, React, Commerce, Hybris", "glassdoor_rating": 3.9,
     "visa_sponsorship": "Maybe", "job_keywords": "Java,Lead,Commerce"},
    {"company_name": "Personio", "company_domain": "personio.com", "company_size": "2,000+",
     "tech_stack": "PHP, Java, React, AWS", "glassdoor_rating": 3.8,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Lead,Senior,Backend"},
    {"company_name": "N26", "company_domain": "n26.com", "company_size": "1,500+",
     "tech_stack": "Java, Kotlin, AWS, Microservices", "glassdoor_rating": 3.6,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Kotlin,Lead,Fintech"},
    {"company_name": "Celonis", "company_domain": "celonis.com", "company_size": "3,000+",
     "tech_stack": "Java, Python, Analytics, Cloud", "glassdoor_rating": 4.2,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Lead,Analytics,Engineer"},
    # Dubai
    {"company_name": "Noon", "company_domain": "noon.com", "company_size": "5,000+",
     "tech_stack": "Java, Python, AWS, Microservices", "glassdoor_rating": 3.5,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Lead,Ecommerce,Backend"},
    {"company_name": "Careem", "company_domain": "careem.com", "company_size": "2,000+",
     "tech_stack": "Java, Kotlin, Go, AWS", "glassdoor_rating": 3.9,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Lead,Senior,Platform"},
    {"company_name": "Emirates Group IT", "company_domain": "emiratesgroup.com", "company_size": "10,000+",
     "tech_stack": "Java, Oracle, SAP, Cloud", "glassdoor_rating": 3.8,
     "visa_sponsorship": "Yes", "job_keywords": "Java,Lead,Architecture,ERP"},
]


def list_companies(user_id: int = 1) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM company_watches WHERE user_id=? ORDER BY company_name",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_company(
    company_name: str,
    company_domain: str = "",
    company_size: str = "",
    tech_stack: str = "",
    glassdoor_rating: float = 0.0,
    visa_sponsorship: str = "Unknown",
    job_keywords: str = "",
    notes: str = "",
    user_id: int = 1,
) -> dict[str, Any]:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO company_watches
               (user_id, company_name, company_domain, company_size, tech_stack,
                glassdoor_rating, visa_sponsorship, job_keywords, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (user_id, company_name, company_domain, company_size, tech_stack,
             glassdoor_rating, visa_sponsorship, job_keywords, notes, now),
        )
        row = conn.execute(
            "SELECT * FROM company_watches WHERE company_name=? AND user_id=?",
            (company_name, user_id),
        ).fetchone()
    return dict(row) if row else {}


def seed_default_companies(user_id: int = 1) -> dict[str, int]:
    inserted = 0
    for c in DEFAULT_COMPANIES:
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM company_watches WHERE company_name=? AND user_id=?",
                (c["company_name"], user_id),
            ).fetchone()
            if not existing:
                add_company(user_id=user_id, **c)
                inserted += 1
    return {"inserted": inserted, "total": len(DEFAULT_COMPANIES)}


def delete_company(company_id: int) -> bool:
    with get_conn() as conn:
        conn.execute("DELETE FROM company_watches WHERE id=?", (company_id,))
    return True


def toggle_alert(company_id: int) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM company_watches WHERE id=?", (company_id,)).fetchone()
        if not row:
            raise ValueError("Company not found")
        new_val = 0 if row["alert_on_new_job"] else 1
        conn.execute(
            "UPDATE company_watches SET alert_on_new_job=? WHERE id=?",
            (new_val, company_id),
        )
        row = conn.execute("SELECT * FROM company_watches WHERE id=?", (company_id,)).fetchone()
    return dict(row)
