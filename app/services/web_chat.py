"""Lightweight web chat session handling for the dashboard assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict
from uuid import uuid4

from secretary_agent import SecretaryAgent


WELCOME_MESSAGE = (
    "I'm ready to help with travel planning, job search, salary negotiation, and workflow automation.\n\n"
    "Click a Quick Start card above or type your request below."
)


@dataclass
class ChatSession:
    agent: SecretaryAgent | None = None
    messages: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.messages:
            self.messages.append({"role": "assistant", "content": WELCOME_MESSAGE})


_sessions: Dict[str, ChatSession] = {}


def _get_or_create_session(session_id: str | None) -> tuple[str, ChatSession]:
    actual_id = session_id or uuid4().hex
    if actual_id not in _sessions:
        _sessions[actual_id] = ChatSession()
    return actual_id, _sessions[actual_id]


def get_chat_state(session_id: str | None) -> tuple[str, list[dict], list[str]]:
    actual_id, session = _get_or_create_session(session_id)
    return actual_id, list(session.messages), _recent_user_actions(session.messages)


def reset_chat_session(session_id: str | None) -> tuple[str, list[dict], list[str]]:
    actual_id = session_id or uuid4().hex
    _sessions[actual_id] = ChatSession()
    return actual_id, list(_sessions[actual_id].messages), []


def send_chat_message(session_id: str | None, user_message: str) -> tuple[str, list[dict], list[str], str]:
    actual_id, session = _get_or_create_session(session_id)
    cleaned = (user_message or "").strip()
    if not cleaned:
        return actual_id, list(session.messages), _recent_user_actions(session.messages), ""

    session.messages.append({"role": "user", "content": cleaned})

    try:
        if session.agent is None:
            session.agent = SecretaryAgent()
        reply = session.agent.get_response(cleaned).strip()
    except Exception as exc:
        reply = (
            "The chat assistant could not complete that request right now. "
            f"Please check the model credentials or service availability. Detail: {exc}"
        )

    session.messages.append({"role": "assistant", "content": reply})
    recent_actions = _recent_user_actions(session.messages)
    return actual_id, list(session.messages), recent_actions, reply


def _recent_user_actions(messages: list[dict], limit: int = 5) -> list[str]:
    prompts: list[str] = []
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = (message.get("content") or "").strip()
        if not content or content in prompts:
            continue
        prompts.append(content)
        if len(prompts) >= limit:
            break
    prompts.reverse()
    return prompts
