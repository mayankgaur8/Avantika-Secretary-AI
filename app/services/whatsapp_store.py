"""Persistence helpers for WhatsApp messages."""

from __future__ import annotations

from app.db import get_conn


def log_whatsapp_message(
    sender: str,
    direction: str,
    message_text: str,
    profile_name: str | None = None,
    twilio_sid: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO whatsapp_messages (sender, profile_name, direction, message_text, twilio_sid)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sender, profile_name, direction, message_text, twilio_sid),
        )


def list_whatsapp_messages(limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM whatsapp_messages
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]
