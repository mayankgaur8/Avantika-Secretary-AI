"""WhatsApp channel adapter — real conversational AI assistant with command system."""

from __future__ import annotations

import html
import os
from typing import Any, Dict

from config import WHATSAPP_REPLY_CHAR_LIMIT
from secretary_agent import SecretaryAgent
from app.services.whatsapp_store import log_whatsapp_message

_sessions: Dict[str, SecretaryAgent] = {}


def _get_session(sender: str) -> SecretaryAgent:
    if sender not in _sessions:
        _sessions[sender] = SecretaryAgent()
    return _sessions[sender]


def _chunk_text(text: str, limit: int = WHATSAPP_REPLY_CHAR_LIMIT) -> str:
    compact = text.strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 60].rstrip() + "\n\n[Reply 'continue' for more.]"


def build_twiml_message(text: str) -> str:
    escaped = html.escape(text)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Message>{escaped}</Message>
</Response>"""


# ─── Fast Command Handlers ────────────────────────────────────────────────────

def _menu_response(_sender: str) -> str:
    return """*SecretaryAI Menu* ✦

*Job Search*
pipeline — view job pipeline
apply [company] — mark applied
follow up [company] — draft email

*Travel*
trips — view upcoming trips
flights [from] [to] [month] — search
watch [route] [price] — set fare alert
alerts — view price watches

*Relocation*
visa status — check visa tracker

*General*
help — how to use
reset — clear session
stop — pause notifications

Or just type naturally — I understand full sentences!"""


def _help_response(_sender: str) -> str:
    return """*SecretaryAI Help* ✦

Just type naturally! Examples:

"Find Java Lead jobs in Germany with visa sponsorship"
"Draft a follow-up email for SAP application"
"Find cheapest flights BLR to Frankfurt in May"
"Set fare alert for BLR to FRA under ₹40,000"
"What are my pending Germany visa tasks?"

Or use shortcuts: menu, pipeline, trips, alerts, visa status"""


def _pipeline_response(_sender: str) -> str:
    try:
        from app.services.pipeline import get_pipeline_summary, get_follow_ups_due
        s = get_pipeline_summary()
        msg = f"*Your Job Pipeline* ▣\n\nTotal: {s['total']} | Active: {s['active']}\n"
        for stage, count in (s.get("by_stage") or {}).items():
            if count > 0:
                emoji = {"Identified": "🔵", "Applied": "📤", "Responded": "📬",
                         "Interview": "🎯", "Offer": "✅", "Rejected": "❌"}.get(stage, "•")
                msg += f"{emoji} {stage}: {count}\n"
        follow_ups = get_follow_ups_due()
        if follow_ups:
            msg += f"\n⏰ *Follow-ups due ({len(follow_ups)}):*\n"
            for j in follow_ups[:3]:
                msg += f"• {j['company']} — {j.get('next_action_due', 'now')}\n"
        else:
            msg += "\n✅ No follow-ups overdue!"
        return msg
    except Exception:
        return "Pipeline loading... Visit /pipeline for details."


def _trips_response(_sender: str) -> str:
    try:
        from app.services.travel import list_travel_requests
        trips = list_travel_requests(limit=5)
        if not trips:
            return "No trips planned yet.\n\nType: 'flights BLR FRA May' to plan a trip!"
        msg = "*Your Upcoming Trips* ✈\n\n"
        for t in trips:
            msg += f"• {t['origin']} → {t['destination']}\n  📅 {t.get('depart_date') or 'No date'} · {(t.get('status') or 'planning').title()}\n"
        return msg
    except Exception:
        return "Trips loading... Visit /travel for details."


def _alerts_response(_sender: str) -> str:
    try:
        from app.services.reminders import list_price_watches
        watches = list_price_watches()
        if not watches:
            return "No price watches active.\n\nSet one: 'watch BLR FRA 40000'"
        msg = "*Active Price Alerts* 🔔\n\n"
        for w in watches:
            msg += f"• {w['route_or_property']}"
            if w.get("target_price"):
                msg += f" — Target: {w['currency']} {w['target_price']:,}"
            msg += "\n"
        return msg
    except Exception:
        return "Alerts loading... Visit /travel for details."


def _visa_response(_sender: str) -> str:
    try:
        from app.services.reminders import get_visa_tracker
        visas = get_visa_tracker()
        if not visas:
            return "No visa applications tracked.\n\nAdd one at /relocation"
        msg = "*Visa & Relocation Status* ⊙\n\n"
        for v in visas:
            msg += f"*{v['visa_type']}*\n{v['target_country']} · {(v.get('application_status') or 'unknown').title()}\n"
            if v.get("progress"):
                p = v["progress"]
                msg += f"Checklist: {p['done']}/{p['total']} ({p['percent']}%)\n"
            pending = [c for c in v.get("checklist", []) if not c["completed"]]
            if pending:
                msg += "Pending:\n"
                for item in pending[:3]:
                    due = f" (due {item['due_date']})" if item.get("due_date") else ""
                    msg += f"• {item['item']}{due}\n"
            msg += "\n"
        return msg.strip()
    except Exception:
        return "Visa status loading... Visit /relocation for details."


def _stop_response(_sender: str) -> str:
    return "✅ Notifications paused.\n\nReply *start* to resume."


def _reset_response(sender: str) -> str:
    _sessions.pop(sender, None)
    return "✅ Session reset. How can I help you?"


# ─── Command routing ──────────────────────────────────────────────────────────

_COMMANDS = {
    "menu":       _menu_response,
    "help":       _help_response,
    "pipeline":   _pipeline_response,
    "jobs":       _pipeline_response,
    "trips":      _trips_response,
    "alerts":     _alerts_response,
    "visa":       _visa_response,
    "visa status":_visa_response,
    "stop":       _stop_response,
    "reset":      _reset_response,
    "/reset":     _reset_response,
    "start":      lambda s: "✅ Notifications resumed!",
    "resume":     lambda s: "✅ Notifications resumed!",
}


def _route_command(message: str, sender: str) -> str | None:
    low = message.lower().strip()

    if low in _COMMANDS:
        return _COMMANDS[low](sender)

    # apply [company]
    if low.startswith("apply "):
        company = message[6:].strip()
        try:
            from app.services.pipeline import move_pipeline_stage
            from app.db import get_conn
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT id FROM job_leads WHERE company LIKE ? LIMIT 1",
                    (f"%{company}%",),
                ).fetchone()
            if row:
                move_pipeline_stage(row["id"], "Applied")
                return f"✅ {company} moved to *Applied*!\n\nFollow-up reminder set for 7 days."
        except Exception:
            pass
        return f"'{company}' not found in pipeline. Add at /pipeline"

    # watch [route] [price]
    if low.startswith("watch "):
        parts = message.split()
        if len(parts) >= 3:
            try:
                price_str = parts[-1].replace("₹", "").replace(",", "")
                price = int(price_str)
                route = " ".join(parts[1:-1])
                from app.services.reminders import add_price_watch
                add_price_watch(route_or_property=route, target_price=price)
                return f"✅ Price watch set!\n\nRoute: {route}\nAlert below: ₹{price:,}"
            except Exception:
                pass

    return None  # Let AI handle it


def handle_whatsapp_message(sender: str, body: str, profile_name: str | None = None) -> str:
    message = (body or "").strip()
    if not message:
        return build_twiml_message(
            "👋 I'm your AI Secretary. Type *menu* for commands, or just ask me anything!"
        )

    log_whatsapp_message(sender=sender, direction="inbound", message_text=message, profile_name=profile_name)

    # Fast command routing
    fast_reply = _route_command(message, sender)
    if fast_reply:
        final = _chunk_text(fast_reply)
        log_whatsapp_message(sender=sender, direction="outbound", message_text=final, profile_name=profile_name)
        return build_twiml_message(final)

    # AI response with WhatsApp-optimized context
    wa_context = (
        "You are responding via WhatsApp. Be concise and mobile-friendly. "
        "Use *bold* for emphasis. Max 3 short paragraphs. Always end with a clear next step.\n"
    )
    if profile_name:
        wa_context += f"User name: {profile_name}\n"
    wa_context += f"User message: {message}"

    agent = _get_session(sender)
    reply = agent.get_response(wa_context)
    final = _chunk_text(reply)
    log_whatsapp_message(sender=sender, direction="outbound", message_text=final, profile_name=profile_name)
    return build_twiml_message(final)


def validate_twilio_request(url: str, form_data: dict, signature: str) -> bool:
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    if not auth_token:
        return True
    try:
        from twilio.request_validator import RequestValidator
    except Exception:
        return False
    validator = RequestValidator(auth_token)
    return validator.validate(url, form_data, signature)


def get_threads_for_display() -> dict[str, Any]:
    """Group WhatsApp messages by sender for the chat UI."""
    from app.services.whatsapp_store import list_whatsapp_messages
    messages = list_whatsapp_messages(limit=200)
    threads: dict[str, Any] = {}
    for msg in messages:
        sender = msg.get("sender", "unknown")
        if sender not in threads:
            threads[sender] = {
                "sender": sender,
                "profile_name": msg.get("profile_name") or sender,
                "messages": [],
            }
        threads[sender]["messages"].append(msg)
    for t in threads.values():
        t["messages"].sort(key=lambda m: m.get("created_at", ""))
    return threads
