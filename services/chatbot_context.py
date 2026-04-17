from __future__ import annotations

from datetime import datetime
from uuid import uuid4


TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"

CHAT_SCOPE_OPTIONS = [
    "Site Filtering",
    "Feasibility Distribution",
    "Feasibility Responses",
    "Feasibility Analysis",
    "Qualification",
    "Final Selection",
    "All Sites (Unfiltered)",
]

QUICK_PROMPTS = [
    {
        "id": "qp_top_sites",
        "label": "Show Top Ranked Sites",
        "prompt": "show top ranked sites",
        "description": "List the strongest ranked sites in the selected scope.",
        "scopes": ["all"],
    },
    {
        "id": "qp_top_europe",
        "label": "Show Top Sites in Europe",
        "prompt": "show top sites in europe",
        "description": "Filter ranked suggestions to Europe-focused candidates.",
        "scopes": ["all"],
    },
    {
        "id": "qp_qualification_summary",
        "label": "Qualification Summary",
        "prompt": "qualification summary",
        "description": "Summarize qualification score and CDA/CRA indicators.",
        "scopes": ["Qualification", "Final Selection", "all"],
    },
    {
        "id": "qp_pending_feasibility",
        "label": "Pending Feasibility Responses",
        "prompt": "pending feasibility responses",
        "description": "Review pending response count and SLA risk.",
        "scopes": ["Feasibility Distribution", "Feasibility Responses", "Feasibility Analysis", "all"],
    },
    {
        "id": "qp_notification_summary",
        "label": "Notification Summary",
        "prompt": "notification summary",
        "description": "Summarize total, pending, and high-priority notifications.",
        "scopes": ["all"],
    },
    {
        "id": "qp_cda_meaning",
        "label": "What Does CDA Mean?",
        "prompt": "what does cda mean",
        "description": "Explain CDA terminology used in qualification.",
        "scopes": ["all"],
    },
    {
        "id": "qp_nav_site_filtering",
        "label": "Navigate to Site Filtering",
        "prompt": "navigate to site filtering",
        "description": "Jump directly to Site Filtering workflow page.",
        "scopes": ["all"],
    },
]


def timestamp_now() -> str:
    return datetime.now().strftime(TIMESTAMP_FMT)


def _normalize_actions(actions: list[dict] | None) -> list[dict]:
    if not isinstance(actions, list):
        return []

    valid_actions: list[dict] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        label = str(action.get("label", "")).strip()
        action_type = str(action.get("type", "")).strip()
        target = str(action.get("target", "")).strip()
        if not label or action_type not in {"navigate", "prompt"} or not target:
            continue

        payload = {
            "label": label,
            "type": action_type,
            "target": target,
        }
        if action_type == "navigate":
            payload["target_page"] = str(action.get("target_page", target)).strip() or target
        valid_actions.append(payload)
    return valid_actions


def make_message(
    role: str,
    content: str,
    *,
    intent_id: str = "",
    response_mode: str = "",
    actions: list[dict] | None = None,
    timestamp: str | None = None,
) -> dict:
    payload = {
        "id": f"msg_{uuid4().hex[:12]}",
        "role": str(role or "assistant").strip(),
        "content": str(content or "").strip(),
        "timestamp": str(timestamp or timestamp_now()).strip(),
    }
    if payload["role"] == "assistant":
        payload["intent_id"] = str(intent_id or "").strip()
        payload["response_mode"] = str(response_mode or "").strip()
        payload["actions"] = _normalize_actions(actions)
    return payload


def make_user_message(content: str, *, timestamp: str | None = None) -> dict:
    return make_message("user", content, timestamp=timestamp)


def make_assistant_message(
    content: str,
    *,
    intent_id: str = "",
    response_mode: str = "",
    actions: list[dict] | None = None,
    timestamp: str | None = None,
) -> dict:
    return make_message(
        "assistant",
        content,
        intent_id=intent_id,
        response_mode=response_mode,
        actions=actions,
        timestamp=timestamp,
    )


def default_scope_from_last_page(last_context_page: str) -> str:
    normalized = str(last_context_page or "").strip()
    if normalized in CHAT_SCOPE_OPTIONS:
        return normalized
    return "Site Filtering"


def _scope_matches(prompt_scopes: list[str], scope_name: str) -> bool:
    if not prompt_scopes:
        return True
    normalized = {str(item).strip() for item in prompt_scopes}
    normalized_lower = {item.lower() for item in normalized}
    return "all" in normalized_lower or scope_name in normalized


def quick_prompts_for_scope(scope_name: str, *, limit: int = 7) -> list[dict]:
    normalized_scope = default_scope_from_last_page(scope_name)
    scoped_prompts = [prompt for prompt in QUICK_PROMPTS if _scope_matches(prompt.get("scopes", []), normalized_scope)]
    if not scoped_prompts:
        scoped_prompts = QUICK_PROMPTS
    return scoped_prompts[: max(1, int(limit or 1))]
