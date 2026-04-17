from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from services.chatbot_handlers import run_handler
from services.chatbot_matcher import match_intent


FAQ_PATH = Path(__file__).resolve().parent.parent / "data" / "chat_faq.json"


@lru_cache(maxsize=1)
def load_chat_faq_catalog() -> dict:
    with open(FAQ_PATH, "r", encoding="utf-8") as f:
        payload = json.load(f)
    if not isinstance(payload, dict):
        return {"intents": []}
    intents = payload.get("intents", [])
    if not isinstance(intents, list):
        intents = []
    return {"intents": intents}


def _safe_actions(intent: dict) -> list[dict]:
    actions = intent.get("actions", [])
    if not isinstance(actions, list):
        return []
    valid = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        label = str(action.get("label", "")).strip()
        action_type = str(action.get("type", "")).strip()
        target = str(action.get("target", "")).strip()
        if not label or action_type not in {"navigate", "prompt"} or not target:
            continue
        payload = {"label": label, "type": action_type, "target": target}
        if action_type == "navigate":
            target_page = str(action.get("target_page", target)).strip() or target
            payload["target_page"] = target_page
        valid.append(payload)
    return valid


def _safe_prompt_actions(suggestions: list[str], *, max_items: int = 3) -> list[dict]:
    actions = []
    for suggestion in suggestions[:max_items]:
        prompt = str(suggestion or "").strip()
        if not prompt:
            continue
        label = prompt
        if len(label) > 42:
            label = label[:39].rstrip() + "..."
        actions.append({"label": f"Try: {label.title()}", "type": "prompt", "target": prompt})
    return actions


def _intent_follow_up_actions(intent: dict) -> list[dict]:
    prompts = intent.get("follow_up_prompts", [])
    if not isinstance(prompts, list):
        return []

    actions = []
    for prompt in prompts:
        target = str(prompt or "").strip()
        if not target:
            continue
        label = target
        if len(label) > 36:
            label = label[:33].rstrip() + "..."
        actions.append({"label": f"Ask: {label.title()}", "type": "prompt", "target": target})
    return actions


def _dedupe_actions(actions: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for action in actions:
        if not isinstance(action, dict):
            continue
        item = {
            "label": str(action.get("label", "")).strip(),
            "type": str(action.get("type", "")).strip(),
            "target": str(action.get("target", "")).strip(),
        }
        target_page = str(action.get("target_page", "")).strip()
        if item["type"] == "navigate" and target_page:
            item["target_page"] = target_page

        key = (item["label"], item["type"], item["target"], item.get("target_page", ""))
        if not all(item.values()) or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _fallback_text(suggestions: list[str], scope_name: str) -> str:
    scope_text = str(scope_name or "").strip() or "current scope"
    if suggestions:
        joined = "; ".join(suggestions[:3])
        return (
            f"I could not map that request to a deterministic intent for {scope_text}. "
            f"Try one of these prompts: {joined}."
        )
    return (
        f"I could not map that request to a deterministic intent for {scope_text}. "
        "Try asking about top sites, feasibility responses, qualification summaries, final selection, or notifications."
    )


def generate_chatbot_response(query: str, context_df: pd.DataFrame, context_bundle: dict) -> dict:
    catalog = load_chat_faq_catalog()
    intents = catalog.get("intents", [])
    match = match_intent(query, intents)
    intent_id = str(match.get("intent_id", ""))
    match_type = str(match.get("match_type", "fallback"))
    fallback_used = match_type == "fallback" or not intent_id
    suggestions = match.get("suggestions", [])
    scope_name = str((context_bundle or {}).get("scope_name", "current scope"))

    intents_by_id = {
        str(intent.get("intent_id", "")).strip(): intent
        for intent in intents
        if isinstance(intent, dict)
    }

    if fallback_used:
        fallback_actions = _safe_prompt_actions(suggestions)
        return {
            "response_text": _fallback_text(suggestions, scope_name),
            "intent_id": "",
            "response_mode": "fallback",
            "success": True,
            "fallback_used": True,
            "actions": fallback_actions,
            "used_local_llm": False,
        }

    intent = intents_by_id.get(intent_id, {})
    actions = _dedupe_actions(_safe_actions(intent) + _intent_follow_up_actions(intent))

    handler_key = str(intent.get("handler", "")).strip()
    if handler_key:
        response_text = run_handler(handler_key, context_df, {**(context_bundle or {}), "query": query})
        response_mode = "handler"
    else:
        response_text = str(intent.get("response", "")).strip()
        response_mode = "static"

    if not response_text:
        fallback_actions = _safe_prompt_actions(suggestions)
        return {
            "response_text": _fallback_text(suggestions, scope_name),
            "intent_id": "",
            "response_mode": "fallback",
            "success": True,
            "fallback_used": True,
            "actions": fallback_actions,
            "used_local_llm": False,
        }

    if not actions and response_mode == "handler":
        actions = _safe_prompt_actions(suggestions, max_items=2)

    return {
        "response_text": response_text,
        "intent_id": intent_id,
        "response_mode": response_mode,
        "success": True,
        "fallback_used": False,
        "actions": actions,
        "used_local_llm": False,
    }
