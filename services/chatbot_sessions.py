from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4


TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"
DEFAULT_SCOPE = "Site Filtering"
DEFAULT_TITLE = "New Chat"

BASE_DIR = Path(__file__).resolve().parent.parent
SESSIONS_DIR = BASE_DIR / "data" / "chat_sessions"

SESSION_ID_SAFE = re.compile(r"[^a-zA-Z0-9_-]")
USERNAME_SAFE = re.compile(r"[^a-zA-Z0-9_.-]")


def _now() -> datetime:
    return datetime.now()


def _timestamp_now() -> str:
    return _now().strftime(TIMESTAMP_FMT)


def _ensure_sessions_dir() -> None:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _normalize_username(username) -> str:
    text = str(username or "").strip().lower()
    cleaned = USERNAME_SAFE.sub("_", text)
    cleaned = cleaned.strip("._-")
    return cleaned or "anonymous"


def _normalize_session_id(session_id) -> str:
    cleaned = SESSION_ID_SAFE.sub("_", str(session_id or "").strip())
    cleaned = cleaned.strip("_")
    return cleaned


def _session_path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{_normalize_session_id(session_id)}.json"


def _parse_timestamp(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    for fmt in [TIMESTAMP_FMT, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M"]:
        try:
            dt = datetime.strptime(text, fmt)
            if fmt == "%H:%M":
                today = _now()
                dt = dt.replace(year=today.year, month=today.month, day=today.day)
            return dt
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _normalize_timestamp(value, *, fallback: str | None = None) -> str:
    parsed = _parse_timestamp(value)
    if parsed is not None:
        return parsed.strftime(TIMESTAMP_FMT)
    if fallback:
        parsed_fallback = _parse_timestamp(fallback)
        if parsed_fallback is not None:
            return parsed_fallback.strftime(TIMESTAMP_FMT)
    return _timestamp_now()


def _sanitize_action(action: dict) -> dict | None:
    if not isinstance(action, dict):
        return None

    label = str(action.get("label", "")).strip()
    action_type = str(action.get("type", "")).strip()
    target = str(action.get("target", "")).strip()
    if not label or action_type not in {"navigate", "prompt"} or not target:
        return None

    payload = {
        "label": label,
        "type": action_type,
        "target": target,
    }
    if action_type == "navigate":
        payload["target_page"] = str(action.get("target_page", target)).strip() or target
    return payload


def _sanitize_message(message: dict) -> dict | None:
    if not isinstance(message, dict):
        return None

    role = str(message.get("role", "")).strip().lower()
    if role not in {"user", "assistant"}:
        return None

    content = str(message.get("content", "")).strip()
    if not content:
        return None

    payload = {
        "role": role,
        "content": content,
        "timestamp": _normalize_timestamp(message.get("timestamp")),
    }

    raw_actions = message.get("actions", [])
    if role == "assistant" and isinstance(raw_actions, list):
        clean_actions = []
        for action in raw_actions:
            clean = _sanitize_action(action)
            if clean:
                clean_actions.append(clean)
        if clean_actions:
            payload["actions"] = clean_actions
    return payload


def _sanitize_session(
    payload: dict,
    *,
    fallback_session_id: str = "",
    fallback_username: str = "",
    fallback_scope: str = DEFAULT_SCOPE,
) -> dict | None:
    if not isinstance(payload, dict):
        return None

    session_id = _normalize_session_id(payload.get("session_id") or fallback_session_id)
    if not session_id:
        return None

    username = _normalize_username(payload.get("username") or fallback_username)
    created_at = _normalize_timestamp(payload.get("created_at"))
    updated_at = _normalize_timestamp(payload.get("updated_at"), fallback=created_at)
    context_scope = str(payload.get("context_scope") or fallback_scope or DEFAULT_SCOPE).strip() or DEFAULT_SCOPE

    title = str(payload.get("title") or "").strip() or DEFAULT_TITLE
    raw_messages = payload.get("messages", [])
    messages = []
    if isinstance(raw_messages, list):
        for message in raw_messages:
            clean_message = _sanitize_message(message)
            if clean_message:
                messages.append(clean_message)

    return {
        "session_id": session_id,
        "title": title,
        "username": username,
        "created_at": created_at,
        "updated_at": updated_at,
        "context_scope": context_scope,
        "messages": messages,
    }


def _read_session_file(path: Path) -> dict | None:
    if not path.exists() or not path.is_file():
        return None

    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None

    return _sanitize_session(payload, fallback_session_id=path.stem)


def generate_session_title(first_user_message) -> str:
    text = " ".join(str(first_user_message or "").strip().split())
    if not text:
        return DEFAULT_TITLE

    max_len = 64
    if len(text) <= max_len:
        return text

    head = text[:max_len].rsplit(" ", 1)[0].strip()
    return (head or text[:max_len]).rstrip(".,;: ") + "..."


def format_session_date_label(timestamp_value) -> str:
    parsed = _parse_timestamp(timestamp_value)
    if parsed is None:
        return ""

    today = _now().date()
    if parsed.date() == today:
        return "Today"
    if parsed.date() == (today - timedelta(days=1)):
        return "Yesterday"
    return parsed.strftime("%b %d").replace(" 0", " ")


def load_chat_sessions(username) -> list[dict]:
    _ensure_sessions_dir()
    normalized_username = _normalize_username(username)
    sessions: list[dict] = []

    for path in SESSIONS_DIR.glob("*.json"):
        session = _read_session_file(path)
        if not session:
            continue
        if _normalize_username(session.get("username")) != normalized_username:
            continue
        sessions.append(session)

    sessions.sort(
        key=lambda item: _parse_timestamp(item.get("updated_at"))
        or _parse_timestamp(item.get("created_at"))
        or datetime.min,
        reverse=True,
    )
    return sessions


def load_chat_session(session_id) -> dict | None:
    _ensure_sessions_dir()
    normalized_id = _normalize_session_id(session_id)
    if not normalized_id:
        return None
    return _read_session_file(_session_path(normalized_id))


def create_new_chat_session(username, context_scope) -> dict:
    _ensure_sessions_dir()
    timestamp = _timestamp_now()
    session_id = f"sess_{_now().strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:6]}"
    payload = {
        "session_id": session_id,
        "title": DEFAULT_TITLE,
        "username": _normalize_username(username),
        "created_at": timestamp,
        "updated_at": timestamp,
        "context_scope": str(context_scope or DEFAULT_SCOPE).strip() or DEFAULT_SCOPE,
        "messages": [],
    }
    return save_chat_session(session_id, payload)


def save_chat_session(session_id, payload) -> dict:
    _ensure_sessions_dir()
    normalized_id = _normalize_session_id(session_id)
    if not normalized_id:
        raise ValueError("session_id is required")

    existing = load_chat_session(normalized_id)
    fallback_username = existing.get("username", "") if isinstance(existing, dict) else ""
    fallback_scope = existing.get("context_scope", DEFAULT_SCOPE) if isinstance(existing, dict) else DEFAULT_SCOPE

    clean_payload = _sanitize_session(
        payload,
        fallback_session_id=normalized_id,
        fallback_username=fallback_username,
        fallback_scope=fallback_scope,
    )
    if clean_payload is None:
        raise ValueError("Invalid session payload")

    if isinstance(existing, dict) and existing.get("created_at"):
        clean_payload["created_at"] = _normalize_timestamp(clean_payload.get("created_at"), fallback=existing["created_at"])
    clean_payload["updated_at"] = _timestamp_now()

    path = _session_path(normalized_id)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(clean_payload, handle, ensure_ascii=True, indent=2)
    return clean_payload


def append_chat_message(session_id, message) -> dict:
    normalized_id = _normalize_session_id(session_id)
    if not normalized_id:
        raise ValueError("session_id is required")

    session = load_chat_session(normalized_id)
    if session is None:
        session = create_new_chat_session("anonymous", DEFAULT_SCOPE)
        normalized_id = session["session_id"]

    clean_message = _sanitize_message(message)
    if clean_message is None:
        return session

    messages = list(session.get("messages", []))
    messages.append(clean_message)
    session["messages"] = messages

    if clean_message["role"] == "user":
        current_title = str(session.get("title", "")).strip()
        if not current_title or current_title.lower() == DEFAULT_TITLE.lower():
            session["title"] = generate_session_title(clean_message.get("content", ""))

    return save_chat_session(normalized_id, session)


def delete_chat_session(session_id) -> bool:
    _ensure_sessions_dir()
    normalized_id = _normalize_session_id(session_id)
    if not normalized_id:
        return False

    path = _session_path(normalized_id)
    if not path.exists():
        return False

    try:
        path.unlink()
    except OSError:
        return False
    return True
