"""Pydantic request/response schemas."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class JobLeadCreate(BaseModel):
    company: str
    role_title: str
    country: Optional[str] = None
    city: Optional[str] = None
    source: str = "manual"
    apply_url: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    salary_currency: Optional[str] = None
    visa_support: Optional[str] = None
    lead_type: str = "target_role"
    notes: Optional[str] = None


class ApplicationCreate(BaseModel):
    next_action: Optional[str] = None
    follow_up_due: Optional[str] = None
    contact_name: Optional[str] = None
    contact_channel: Optional[str] = None
    submission_proof: Optional[str] = None
    notes: Optional[str] = None


class TravelRequestCreate(BaseModel):
    origin: str
    destination: str
    depart_date: Optional[str] = None
    return_date: Optional[str] = None
    traveler_count: int = Field(default=1, ge=1, le=10)
    baggage: Optional[str] = None
    budget: Optional[int] = None
    currency: str = "EUR"
    purpose: Optional[str] = None
    notes: Optional[str] = None
