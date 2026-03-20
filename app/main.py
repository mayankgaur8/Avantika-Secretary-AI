"""FastAPI web app — SecretaryAI: Executive Travel + Job Search Secretary."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta
from urllib.parse import quote

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import init_db
from app.services.browser_automation import run_browser_apply
from app.services.integrations import (
    clear_automation_runs, daily_job_digest, get_last_sync_time,
    list_automation_runs, suggest_salary, sync_live_job_sources,
)
from app.services.whatsapp import handle_whatsapp_message, validate_twilio_request, get_threads_for_display
from app.services.whatsapp_store import list_whatsapp_messages
from app.services.web_chat import get_chat_state, reset_chat_session, send_chat_message
from app.schemas import ApplicationCreate, JobLeadCreate, TravelRequestCreate
from app.services.jobs import (
    apply_to_job, count_pending_review, create_job_lead, dashboard_job_summary,
    generate_application_draft, get_application_draft,
    import_target_companies, list_applications, list_job_leads, list_pending_review_jobs,
    review_job, clear_job_leads,
)
from app.services.travel import (
    confirm_travel_plan, create_travel_request, dashboard_travel_summary,
    generate_accommodation_options, generate_live_travel_options,
    generate_travel_options, get_travel_workflow, list_travel_options,
    list_travel_requests, prepare_travel_booking, recommend_travel_plan,
)
from app.services.pipeline import (
    get_pipeline_summary, get_kanban_board, move_pipeline_stage,
    update_job_lead, add_to_pipeline, get_follow_ups_due, get_pipeline_job,
)
from app.services.companies import (
    list_companies, add_company, seed_default_companies,
    delete_company, toggle_alert,
)
from app.services.reminders import (
    list_reminders, create_reminder, delete_reminder,
    get_visa_tracker, toggle_checklist_item, add_visa, update_visa_status,
    list_price_watches, add_price_watch, deactivate_price_watch,
)
from app.services.user_mgmt import (
    get_user, get_job_profile, get_travel_profile,
    update_job_profile, update_travel_profile,
)
from config import STATIC_DIR, TEMPLATES_DIR

app = FastAPI(title="SecretaryAI — Executive Travel & Job Search Secretary")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
CHAT_SESSION_COOKIE = "secretary_chat_session"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_scheduler: BackgroundScheduler | None = None


def _run_startup_sync() -> None:
    """Run job digest in background if no sync in last 20 hours."""
    import time
    time.sleep(10)  # let app finish booting first
    try:
        last = get_last_sync_time("job_sync")
        if last:
            cutoff = datetime.utcnow() - timedelta(hours=20)
            if datetime.fromisoformat(last) > cutoff:
                print("[SecretaryAI] Scheduler: recent sync found, skipping startup sync.", flush=True)
                return
        print("[SecretaryAI] Scheduler: no recent sync — running startup job digest...", flush=True)
        result = daily_job_digest()
        print(f"[SecretaryAI] Scheduler: startup sync done — {result.get('new_count', 0)} new jobs, {result.get('total_items', 0)} total.", flush=True)
    except Exception as exc:
        print(f"[SecretaryAI] Scheduler: startup sync failed — {exc}", flush=True)


@app.on_event("startup")
def startup() -> None:
    init_db()

    global _scheduler
    _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    _scheduler.add_job(
        daily_job_digest,
        CronTrigger(hour=8, minute=0, timezone="Asia/Kolkata"),
        id="daily_job_sync",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    _scheduler.start()
    print("[SecretaryAI] Scheduler: started — daily sync at 08:00 IST.", flush=True)

    # Run a sync immediately on startup if no recent sync exists
    threading.Thread(target=_run_startup_sync, daemon=True).start()


@app.on_event("shutdown")
def shutdown() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)


def _time_greeting() -> str:
    h = datetime.now().hour
    if h < 12:
        return "morning"
    elif h < 17:
        return "afternoon"
    return "evening"


def _base_ctx(request: Request) -> dict:
    user = get_user(1)
    pipeline = get_pipeline_summary()
    visas = get_visa_tracker(1)
    visa_active = visas[0] if visas else None
    reminders = list_reminders(1, "pending")
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    due = len([r for r in reminders if r.get("scheduled_for", "9999") <= now_str])
    return {
        "user": user,
        "pipeline_summary": pipeline,
        "reminders_due": due,
        "jobs_to_review": count_pending_review(),
        "active_page": "",
        "message": None,
        "message_level": "info",
        "visa_active": visa_active,
    }


def _set_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(CHAT_SESSION_COOKIE, session_id, httponly=True, samesite="lax")


def _redirect(url: str, message: str = "", level: str = "info") -> RedirectResponse:
    if message:
        sep = "&" if "?" in url else "?"
        url += f"{sep}message={quote(message)}&level={quote(level)}"
    return RedirectResponse(url=url, status_code=303)


# ─── ROOT ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/dashboard", status_code=302)


# ─── DASHBOARD ────────────────────────────────────────────────────────────────

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, message: str | None = None, level: str = "info"):
    ctx = _base_ctx(request)
    session_id, chat_messages, _ = get_chat_state(request.cookies.get(CHAT_SESSION_COOKIE))
    pipeline = get_pipeline_summary()
    visas = get_visa_tracker(1)
    visa_active = visas[0] if visas else None
    travel = list_travel_requests(limit=5)
    price_watches = list_price_watches(1)
    companies = list_companies(1)
    ctx.update({
        "active_page": "dashboard",
        "time_greeting": _time_greeting(),
        "pipeline": pipeline,
        "recent_jobs": list_job_leads(limit=10),
        "follow_ups": get_follow_ups_due(),
        "travel_requests": travel,
        "travel_count": len(travel),
        "price_watches": len(price_watches),
        "company_count": len(companies),
        "visa_active": visa_active,
        "automation_runs": list_automation_runs(limit=8),
        "chat_messages": chat_messages,
        "message": message,
        "message_level": level,
    })
    resp = templates.TemplateResponse(request, "dashboard.html", ctx)
    _set_cookie(resp, session_id)
    return resp


# ─── JOB REVIEW QUEUE ────────────────────────────────────────────────────────

@app.get("/jobs/review", response_class=HTMLResponse)
def jobs_review_page(request: Request, message: str | None = None, level: str = "info"):
    ctx = _base_ctx(request)
    jobs = list_pending_review_jobs()
    # Enrich each job with a salary suggestion if feed didn't provide one
    for job in jobs:
        if not job.get("salary_max"):
            lo, hi, cur = suggest_salary(job.get("role_title", ""), job.get("country", ""))
            job["salary_suggested_min"] = lo
            job["salary_suggested_max"] = hi
            job["salary_suggested_currency"] = cur
        else:
            job["salary_suggested_min"] = job["salary_min"]
            job["salary_suggested_max"] = job["salary_max"]
            job["salary_suggested_currency"] = job.get("salary_currency") or "EUR"
    ctx.update({
        "active_page": "review",
        "jobs": jobs,
        "last_sync": get_last_sync_time("job_sync"),
        "message": message,
        "message_level": level,
    })
    return templates.TemplateResponse(request, "jobs_review.html", ctx)


@app.post("/api/jobs/review/{job_id}/approve")
def api_review_approve(job_id: int):
    return review_job(job_id, "approve")


@app.post("/api/jobs/review/{job_id}/skip")
def api_review_skip(job_id: int):
    return review_job(job_id, "skip")


@app.post("/api/jobs/review/{job_id}/apply-now")
def api_review_apply_now(job_id: int):
    review_job(job_id, "approve")
    generate_application_draft(job_id)
    apply_to_job(job_id)
    move_pipeline_stage(job_id, "Applied")
    return {"status": "applied", "job_id": job_id}


# ─── PIPELINE ─────────────────────────────────────────────────────────────────

@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page(request: Request, draft_job_id: int | None = None, message: str | None = None, level: str = "info"):
    ctx = _base_ctx(request)
    board = get_kanban_board()
    summary = get_pipeline_summary()
    draft_pack = None
    if draft_job_id:
        raw = get_application_draft(draft_job_id)
        if raw:
            job = get_pipeline_job(draft_job_id)
            draft_pack = {"draft": raw, "job": job}
    ctx.update({
        "active_page": "pipeline",
        "board": board,
        "summary": summary,
        "draft_pack": draft_pack,
        "message": message,
        "message_level": level,
    })
    return templates.TemplateResponse(request, "pipeline.html", ctx)


# ─── COMPANIES ────────────────────────────────────────────────────────────────

@app.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request, message: str | None = None, level: str = "info"):
    ctx = _base_ctx(request)
    ctx.update({
        "active_page": "companies",
        "companies": list_companies(1),
        "message": message,
        "message_level": level,
    })
    return templates.TemplateResponse(request, "companies.html", ctx)


# ─── TRAVEL ───────────────────────────────────────────────────────────────────

@app.get("/travel", response_class=HTMLResponse)
def travel_page(request: Request, message: str | None = None, level: str = "info"):
    ctx = _base_ctx(request)
    travel_reqs = list_travel_requests(limit=20)
    enriched = []
    for t in travel_reqs:
        trip = dict(t)
        trip["options"] = list_travel_options(t["id"])
        trip["plan"] = get_travel_workflow(t["id"])
        enriched.append(trip)
    ctx.update({
        "active_page": "travel",
        "travel_requests": enriched,
        "price_watches": list_price_watches(1),
        "message": message,
        "message_level": level,
    })
    return templates.TemplateResponse(request, "travel.html", ctx)


# ─── WHATSAPP ─────────────────────────────────────────────────────────────────

@app.get("/whatsapp", response_class=HTMLResponse)
def whatsapp_page(request: Request):
    ctx = _base_ctx(request)
    threads = get_threads_for_display()
    all_msgs = list_whatsapp_messages(limit=100)
    ctx.update({
        "active_page": "whatsapp",
        "threads": threads,
        "messages": all_msgs,
        "wa_phone": os.environ.get("TWILIO_WHATSAPP_NUMBER", ""),
    })
    return templates.TemplateResponse(request, "whatsapp.html", ctx)


# ─── RELOCATION ───────────────────────────────────────────────────────────────

@app.get("/relocation", response_class=HTMLResponse)
def relocation_page(request: Request, message: str | None = None, level: str = "info"):
    ctx = _base_ctx(request)
    visas = get_visa_tracker(1)
    job_profile = get_job_profile(1) or {}
    target_countries = [c.strip() for c in (job_profile.get("target_countries") or "DE,AE,CA,IE").split(",")]
    ctx.update({
        "active_page": "relocation",
        "visas": visas,
        "job_profile": job_profile,
        "target_countries": target_countries,
        "message": message,
        "message_level": level,
    })
    return templates.TemplateResponse(request, "relocation.html", ctx)


# ─── PRICING ──────────────────────────────────────────────────────────────────

@app.get("/pricing", response_class=HTMLResponse)
def pricing_page(request: Request):
    ctx = _base_ctx(request)
    ctx["active_page"] = "pricing"
    return templates.TemplateResponse(request, "pricing.html", ctx)


# ─── CHAT ─────────────────────────────────────────────────────────────────────

@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    ctx = _base_ctx(request)
    session_id, chat_messages, _ = get_chat_state(request.cookies.get(CHAT_SESSION_COOKIE))
    ctx.update({"active_page": "chat", "chat_messages": chat_messages})
    resp = templates.TemplateResponse(request, "chat.html", ctx)
    _set_cookie(resp, session_id)
    return resp


# ─── SETTINGS ─────────────────────────────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    ctx = _base_ctx(request)
    ctx.update({
        "active_page": "settings",
        "job_profile": get_job_profile(1),
        "travel_profile": get_travel_profile(1),
    })
    return templates.TemplateResponse(request, "relocation.html", ctx)


# ─── API: CHAT ────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str


@app.post("/api/chat")
def api_chat(request: Request, payload: ChatRequest):
    session_id, messages, recent_actions, reply = send_chat_message(
        request.cookies.get(CHAT_SESSION_COOKIE), payload.message,
    )
    resp = JSONResponse({"reply": reply, "messages": messages, "recent_actions": recent_actions})
    _set_cookie(resp, session_id)
    return resp


@app.post("/api/chat/reset")
def api_chat_reset(request: Request):
    session_id, messages, recent_actions = reset_chat_session(request.cookies.get(CHAT_SESSION_COOKIE))
    resp = JSONResponse({"messages": messages, "recent_actions": recent_actions})
    _set_cookie(resp, session_id)
    return resp


# ─── API: PIPELINE ────────────────────────────────────────────────────────────

@app.get("/api/pipeline/{job_id}")
def api_get_job(job_id: int):
    job = get_pipeline_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/api/pipeline/{job_id}/stage")
def api_move_stage(job_id: int, payload: dict):
    stage = payload.get("stage")
    if not stage:
        raise HTTPException(status_code=400, detail="stage required")
    try:
        return move_pipeline_stage(job_id, stage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/pipeline/{job_id}")
def api_update_job(job_id: int, payload: dict):
    try:
        return update_job_lead(job_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/pipeline")
def api_add_pipeline(payload: dict):
    return add_to_pipeline(
        company=payload.get("company", ""),
        role_title=payload.get("role_title", ""),
        country=payload.get("country", ""),
        city=payload.get("city", ""),
        apply_url=payload.get("apply_url", ""),
        salary_min=int(payload.get("salary_min") or 0),
        salary_max=int(payload.get("salary_max") or 0),
        notes=payload.get("notes", ""),
        stage=payload.get("stage", "Identified"),
    )


# ─── API: COMPANIES ───────────────────────────────────────────────────────────

@app.get("/api/companies")
def api_list_companies():
    return list_companies(1)


@app.post("/api/companies")
def api_add_company_route(payload: dict):
    return add_company(**{k: v for k, v in payload.items() if k in {
        "company_name","company_domain","company_size","tech_stack",
        "glassdoor_rating","visa_sponsorship","job_keywords","notes"
    }})


@app.post("/api/companies/seed")
def api_seed_companies():
    result = seed_default_companies(1)
    return _redirect("/companies", f"Loaded {result['inserted']} companies", "success")


@app.delete("/api/companies/{company_id}")
def api_delete_company_route(company_id: int):
    return {"deleted": delete_company(company_id)}


@app.post("/api/companies/{company_id}/toggle-alert")
def api_toggle_alert_route(company_id: int):
    return toggle_alert(company_id)


# ─── API: VISA ────────────────────────────────────────────────────────────────

@app.post("/api/visa/checklist/{item_id}/toggle")
def api_toggle_check(item_id: int):
    return toggle_checklist_item(item_id)


@app.post("/api/visa/{visa_id}/status")
def api_visa_status(visa_id: int, payload: dict):
    return update_visa_status(visa_id, payload.get("status", "preparing"), payload.get("notes", ""))


@app.post("/api/visa")
def api_add_visa_route(payload: dict):
    return add_visa(
        visa_type=payload.get("visa_type",""),
        target_country=payload.get("target_country",""),
        application_status=payload.get("application_status","preparing"),
        notes=payload.get("notes",""),
    )


# ─── API: TRAVEL WATCHES ──────────────────────────────────────────────────────

@app.get("/api/travel/watches")
def api_watches():
    return list_price_watches(1)


@app.post("/api/travel/watches")
def api_add_watch(payload: dict):
    return add_price_watch(
        route_or_property=payload.get("route_or_property",""),
        watch_type=payload.get("watch_type","flight"),
        date_range=payload.get("date_range",""),
        target_price=int(payload.get("target_price") or 0),
        currency=payload.get("currency","INR"),
    )


@app.post("/api/travel/watches/{watch_id}/stop")
def api_stop_watch(watch_id: int):
    return {"stopped": deactivate_price_watch(watch_id)}


# ─── API: PROFILE ─────────────────────────────────────────────────────────────

@app.post("/api/profile/job")
async def api_update_job_profile(request: Request):
    form = await request.form()
    update_job_profile(dict(form), 1)
    return _redirect("/relocation", "Profile updated", "success")


# ─── REMINDERS PAGE ───────────────────────────────────────────────────────────

@app.get("/reminders", response_class=HTMLResponse)
def reminders_page(request: Request, message: str | None = None, level: str = "info"):
    ctx = _base_ctx(request)
    all_reminders = list_reminders(1, "all")
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
    ctx.update({
        "active_page": "reminders",
        "reminders": all_reminders,
        "message": message,
        "message_level": level,
        "now_str": now_str,
    })
    return templates.TemplateResponse(request, "reminders.html", ctx)


# ─── API: REMINDERS ───────────────────────────────────────────────────────────

@app.get("/api/reminders")
def api_reminders():
    return list_reminders(1, "pending")


@app.post("/api/reminders")
def api_create_reminder_route(payload: dict):
    return create_reminder(
        title=payload.get("title",""),
        scheduled_for=payload.get("scheduled_for",""),
        message=payload.get("message",""),
        reminder_type=payload.get("reminder_type","custom"),
        channel=payload.get("channel","whatsapp"),
    )


@app.delete("/api/reminders/{reminder_id}")
def api_delete_reminder_route(reminder_id: int):
    return {"deleted": delete_reminder(reminder_id)}


# ─── WHATSAPP WEBHOOK ─────────────────────────────────────────────────────────

@app.post("/whatsapp/webhook")
async def whatsapp_webhook(
    request: Request,
    Body: str = Form(default=""),
    From: str = Form(default=""),
    ProfileName: str = Form(default=""),
    MessageSid: str = Form(default=""),
):
    if not From:
        raise HTTPException(status_code=400, detail="Missing sender")
    form = await request.form()
    signature = request.headers.get("X-Twilio-Signature", "")
    if not validate_twilio_request(str(request.url), dict(form), signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")
    xml = handle_whatsapp_message(sender=From, body=Body, profile_name=ProfileName or None)
    return Response(content=xml, media_type="application/xml")


@app.get("/whatsapp/health")
def whatsapp_health():
    return {"status": "ok"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/scheduler/status")
def scheduler_status():
    last_sync = get_last_sync_time("job_sync")
    last_digest = get_last_sync_time("daily_digest")
    running = bool(_scheduler and _scheduler.running)
    next_run = None
    if running:
        job = _scheduler.get_job("daily_job_sync")
        if job and job.next_run_time:
            next_run = job.next_run_time.isoformat()
    return {
        "scheduler_running": running,
        "next_run_IST": next_run,
        "last_job_sync": last_sync,
        "last_digest": last_digest,
    }


# ─── STATUS / API ─────────────────────────────────────────────────────────────

@app.get("/api/status")
def api_status():
    return {
        "job_summary": dashboard_job_summary(),
        "travel_summary": dashboard_travel_summary(),
        "pipeline": get_pipeline_summary(),
        "applications": len(list_applications(limit=200)),
    }


# ─── ACTION ROUTES (form POSTs → redirect) ────────────────────────────────────

@app.post("/actions/import-targets")
def action_import_targets():
    r = import_target_companies()
    return _redirect("/pipeline", f"Imported {r['inserted']} companies.", "success")


@app.post("/actions/run-daily-digest")
def action_run_digest():
    try:
        r = daily_job_digest()
        new = r.get("new_count", 0)
        return _redirect("/jobs/review", f"Sync done — {new} new job{'s' if new != 1 else ''} found.", "success")
    except Exception as exc:
        return _redirect("/jobs/review", str(exc), "error")


@app.post("/actions/sync-live-jobs")
def action_sync_live():
    try:
        r = sync_live_job_sources()
        return _redirect("/pipeline", f"Sync done. {r['total_items']} items.", "success")
    except Exception as exc:
        return _redirect("/pipeline", str(exc), "error")


@app.post("/actions/automation-runs/clear")
def action_clear_runs():
    r = clear_automation_runs()
    return _redirect("/dashboard", f"Cleared {r['deleted']} runs.", "success")


@app.post("/actions/job-leads/clear")
def action_clear_jobs():
    r = clear_job_leads()
    return _redirect("/pipeline", f"Cleared {r['deleted']} jobs.", "success")


@app.post("/actions/jobs/{job_id}/apply")
def action_apply_job(job_id: int):
    try:
        apply_to_job(job_id, ApplicationCreate())
        move_pipeline_stage(job_id, "Applied")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _redirect(f"/pipeline?draft_job_id={job_id}", "Marked as Applied.", "success")


@app.post("/actions/jobs/{job_id}/draft-pack")
def action_draft_pack(job_id: int):
    try:
        generate_application_draft(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _redirect(f"/pipeline?draft_job_id={job_id}", "Draft pack generated.", "success")


@app.post("/actions/travel")
def action_create_travel(
    origin: str = Form(...),
    destination: str = Form(...),
    depart_date: str = Form(""),
    return_date: str = Form(""),
    traveler_count: int = Form(1),
    baggage: str = Form("Cabin + 1 checked bag"),
    budget: int | None = Form(default=None),
    currency: str = Form("EUR"),
    purpose: str = Form(""),
    notes: str = Form(""),
):
    record = create_travel_request(TravelRequestCreate(
        origin=origin, destination=destination,
        depart_date=depart_date or None, return_date=return_date or None,
        traveler_count=traveler_count, baggage=baggage or None,
        budget=budget, currency=currency, purpose=purpose or None, notes=notes or None,
    ))
    generate_travel_options(record["id"])
    generate_accommodation_options(record["id"])
    recommend_travel_plan(record["id"])
    return _redirect("/travel", "Trip planned with AI recommendations.", "success")


@app.post("/actions/travel/{tid}/recommend")
def action_recommend(tid: int):
    try:
        generate_travel_options(tid)
        generate_accommodation_options(tid)
        recommend_travel_plan(tid)
        return _redirect("/travel", "Recommendations refreshed.", "success")
    except Exception as exc:
        return _redirect("/travel", str(exc), "error")


@app.post("/actions/travel/{tid}/confirm")
def action_confirm(tid: int):
    try:
        confirm_travel_plan(tid)
        return _redirect("/travel", "Trip confirmed.", "success")
    except Exception as exc:
        return _redirect("/travel", str(exc), "error")


@app.post("/actions/travel/{tid}/search-live")
def action_live_travel(tid: int):
    try:
        r = generate_live_travel_options(tid)
        return _redirect("/travel", f"Live search: {r['live_offers']} offers.", "success")
    except Exception as exc:
        return _redirect("/travel", str(exc), "error")


@app.post("/actions/travel/{tid}/prepare-booking")
def action_prepare_booking(tid: int):
    try:
        prepare_travel_booking(tid)
        return _redirect("/travel", "Booking prepared.", "success")
    except Exception as exc:
        return _redirect("/travel", str(exc), "error")


@app.post("/actions/jobs/{job_id}/browser-apply")
async def action_browser_apply(job_id: int):
    try:
        result = await run_browser_apply(job_id)
        level = "success" if result.get("status") == "completed" else "error"
        return _redirect(f"/pipeline?draft_job_id={job_id}", result.get("user_message", "done"), level)
    except Exception as exc:
        return _redirect("/pipeline", str(exc), "error")


# ─── LEGACY JOBS/TRAVEL API ───────────────────────────────────────────────────

@app.get("/api/jobs")
def api_jobs():
    return list_job_leads(limit=200)


@app.post("/api/jobs/import-targets")
def api_import():
    return import_target_companies()


@app.post("/api/jobs/sync-live")
def api_sync():
    try:
        return sync_live_job_sources()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/jobs")
def api_create_job(payload: JobLeadCreate):
    return create_job_lead(payload)


@app.post("/api/jobs/{job_id}/apply")
def api_apply(job_id: int, payload: ApplicationCreate | None = None):
    try:
        return apply_to_job(job_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/applications")
def api_applications():
    return list_applications(limit=200)


@app.post("/api/jobs/{job_id}/draft-pack")
def api_draft_pack(job_id: int):
    try:
        return generate_application_draft(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/travel/requests")
def api_travel_reqs():
    return list_travel_requests(limit=100)


@app.post("/api/travel/requests")
def api_create_travel(payload: TravelRequestCreate):
    return create_travel_request(payload)


@app.post("/api/travel/requests/{tid}/generate-options")
def api_gen_options(tid: int):
    try:
        return generate_travel_options(tid)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/travel/requests/{tid}/search-live")
def api_live_search(tid: int):
    try:
        return generate_live_travel_options(tid)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/travel/requests/{tid}/options")
def api_travel_options(tid: int):
    return list_travel_options(tid)


@app.post("/api/browser/apply/{job_id}")
async def api_browser_apply(job_id: int):
    try:
        return await run_browser_apply(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
