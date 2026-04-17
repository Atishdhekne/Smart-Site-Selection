from __future__ import annotations


def _text(value) -> str:
    return str(value or "").strip()


def log_chat_usage_event(
    logger_callback,
    *,
    username: str,
    full_name: str,
    role: str,
    page_name: str,
    context_scope: str,
    prompt: str,
    response_payload: dict,
) -> None:
    if not callable(logger_callback):
        return

    response_text = _text(response_payload.get("response_text", ""))
    intent_id = _text(response_payload.get("intent_id", ""))
    response_mode = _text(response_payload.get("response_mode", "fallback"))
    fallback_used = bool(response_payload.get("fallback_used", False))
    success = bool(response_payload.get("success", True))

    kwargs = {
        "username": _text(username),
        "full_name": _text(full_name),
        "role": _text(role),
        "page_name": _text(page_name),
        "prompt": _text(prompt),
        "response": response_text,
        "used_local_llm": False,
        "success": success,
        "error_message": "",
        "context_scope": _text(context_scope),
        "matched_intent_id": intent_id,
        "response_mode": response_mode,
        "fallback_used": fallback_used,
    }

    try:
        logger_callback(**kwargs)
    except TypeError:
        logger_callback(
            username=kwargs["username"],
            full_name=kwargs["full_name"],
            role=kwargs["role"],
            page_name=kwargs["page_name"],
            prompt=kwargs["prompt"],
            response=kwargs["response"],
            used_local_llm=False,
            success=kwargs["success"],
            error_message="",
        )
    except Exception:
        return
