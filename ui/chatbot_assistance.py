from __future__ import annotations

from collections.abc import Callable, MutableMapping
from datetime import datetime
from html import escape

import pandas as pd
import streamlit as st

from services.chatbot_context import (
    CHAT_SCOPE_OPTIONS,
    default_scope_from_last_page,
    make_assistant_message,
    make_user_message,
    quick_prompts_for_scope,
)
from services.chatbot_engine import generate_chatbot_response
from services.chatbot_logging import log_chat_usage_event
from services.chatbot_sessions import (
    append_chat_message,
    create_new_chat_session,
    format_session_date_label,
    load_chat_session,
    load_chat_sessions,
    save_chat_session,
)


CHAT_SCOPE_KEY = "chat_context_scope"
ACTIVE_SESSION_KEY = "chatbot_active_session"
CHAT_INPUT_KEY = "chat_input_text"
PENDING_CHAT_LOAD_KEY = "pending_chat_load"

DEFAULT_SCOPE = "Site Filtering"
DEFAULT_TITLE = "New Chat"


def _inject_chat_styles() -> None:
    st.markdown(
        """
        <style>
        .chat-rail {
          background: #FFFFFF;
          border: 1px solid #D7E1EC;
          border-radius: 18px;
          padding: 14px;
          box-shadow: 0 1px 2px rgba(15,23,42,0.04);
        }
        .chat-rail-title {
          font-size: 15px;
          font-weight: 800;
          color: #1F4E8C;
          margin: 0;
        }
        .chat-rail-sub {
          font-size: 12px;
          color: #475569;
          margin: 4px 0 10px 0;
        }
        .chat-section-title {
          font-size: 12px;
          font-weight: 700;
          color: #334155;
          margin: 12px 0 6px 0;
        }
        .chat-session-time {
          color: #64748B;
          font-size: 11px;
          text-align: right;
          padding-top: 8px;
        }
        .chat-workspace {
          background: #F6FAFF;
          border: 1px solid #C8D8EC;
          border-radius: 18px;
          padding: 0;
          overflow: hidden;
        }
        .chat-workspace-head {
          background: linear-gradient(90deg, #2F6DB5, #1F4E8C);
          color: #FFFFFF;
          padding: 12px 14px;
        }
        .chat-workspace-title {
          font-size: 16px;
          font-weight: 800;
          margin: 0;
        }
        .chat-workspace-sub {
          font-size: 12px;
          opacity: 0.96;
          margin: 2px 0 0 0;
        }
        .chat-bubble-meta {
          color: #64748B;
          font-size: 11px;
          margin-bottom: 3px;
        }
        .chat-row-user {
          display: flex;
          justify-content: flex-end;
        }
        .chat-bubble-user {
          display: inline-block;
          max-width: 86%;
          background: #2F6DB5;
          color: #FFFFFF;
          border-radius: 14px;
          padding: 10px 12px;
          margin-bottom: 8px;
        }
        .chat-bubble-assistant {
          display: inline-block;
          max-width: 92%;
          background: #FFFFFF;
          color: #1F2937;
          border: 1px solid #D7E1EC;
          border-radius: 14px;
          padding: 10px 12px;
          margin-bottom: 8px;
        }
        .chat-empty {
          color: #475569;
          font-size: 13px;
          padding: 18px 2px;
        }
        .chat-chip-note {
          font-size: 12px;
          color: #334155;
          margin-bottom: 6px;
          font-weight: 600;
        }
        .chat-disclaimer {
          font-size: 11px;
          color: #64748B;
          margin-top: 6px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _normalize_text(value, normalize_text_value_callback: Callable) -> str:
    if callable(normalize_text_value_callback):
        return normalize_text_value_callback(value)
    return str(value or "")


def _safe_scope(scope_name: str, fallback: str = DEFAULT_SCOPE) -> str:
    scope = str(scope_name or "").strip()
    if scope in CHAT_SCOPE_OPTIONS:
        return scope
    return default_scope_from_last_page(fallback)


def _resolve_username(session_state: MutableMapping, normalize_text_value_callback: Callable) -> str:
    username = _normalize_text(session_state.get("current_user", ""), normalize_text_value_callback).strip()
    if username:
        return username
    return "anonymous"


def _welcome_message(selected_scope: str) -> dict:
    return make_assistant_message(
        (
            "Hi, I am your deterministic SmartSite assistant. "
            "I can summarize ranking, feasibility, qualification, final selection, and notifications. "
            f"Current context scope: {selected_scope}."
        ),
        intent_id="system_welcome",
        response_mode="static",
        actions=[
            {"label": "Show Top Ranked Sites", "type": "prompt", "target": "show top ranked sites"},
            {
                "label": "Navigate to Site Filtering",
                "type": "navigate",
                "target": "Site Filtering",
                "target_page": "Site Filtering",
            },
        ],
    )


def _create_session_with_welcome(username: str, scope_name: str) -> dict:
    session = create_new_chat_session(username, scope_name)
    append_chat_message(session["session_id"], _welcome_message(scope_name))
    return load_chat_session(session["session_id"]) or session


def _queue_pending_chat_load(
    *,
    session_state: MutableMapping,
    session_id: str,
    context_scope: str,
    default_scope: str,
) -> None:
    pending_session_id = str(session_id or "").strip()
    if not pending_session_id:
        return
    session_state[PENDING_CHAT_LOAD_KEY] = {
        "session_id": pending_session_id,
        "context_scope": str(context_scope or default_scope),
    }


def _apply_pending_chat_load(*, session_state: MutableMapping, default_scope: str) -> None:
    pending_payload = session_state.pop(PENDING_CHAT_LOAD_KEY, None)
    if not isinstance(pending_payload, dict):
        return

    pending_session_id = str(pending_payload.get("session_id", "")).strip()
    if not pending_session_id:
        return

    pending_scope = pending_payload.get("context_scope", default_scope)
    session_state[ACTIVE_SESSION_KEY] = pending_session_id
    session_state[CHAT_SCOPE_KEY] = _safe_scope(pending_scope, default_scope)


def _ensure_active_session(
    *,
    username: str,
    session_state: MutableMapping,
    default_scope: str,
) -> tuple[list[dict], dict]:
    sessions = load_chat_sessions(username)
    active_session_id = str(session_state.get(ACTIVE_SESSION_KEY, "")).strip()

    if not sessions:
        created = _create_session_with_welcome(username, default_scope)
        session_state[ACTIVE_SESSION_KEY] = created["session_id"]
        session_state[CHAT_SCOPE_KEY] = _safe_scope(created.get("context_scope", default_scope), default_scope)
        return [created], created

    session_lookup = {str(item.get("session_id", "")): item for item in sessions if item.get("session_id")}
    if active_session_id not in session_lookup:
        active_session_id = str(sessions[0].get("session_id", "")).strip()
        session_state[ACTIVE_SESSION_KEY] = active_session_id
        selected_scope = _safe_scope(session_lookup[active_session_id].get("context_scope", default_scope), default_scope)
        session_state[CHAT_SCOPE_KEY] = selected_scope

    active_session = load_chat_session(active_session_id) or session_lookup.get(active_session_id)
    if active_session is None:
        created = _create_session_with_welcome(username, default_scope)
        session_state[ACTIVE_SESSION_KEY] = created["session_id"]
        session_state[CHAT_SCOPE_KEY] = _safe_scope(created.get("context_scope", default_scope), default_scope)
        sessions = load_chat_sessions(username)
        return sessions, created
    return sessions, active_session


def _sync_active_scope(session_payload: dict, selected_scope: str) -> dict:
    current_scope = str(session_payload.get("context_scope", "")).strip()
    if current_scope == selected_scope:
        return session_payload
    session_payload["context_scope"] = selected_scope
    return save_chat_session(session_payload["session_id"], session_payload)


def _render_recent_sessions(
    *,
    sessions: list[dict],
    active_session_id: str,
    default_scope: str,
    session_state: MutableMapping,
) -> None:
    if not sessions:
        st.caption("No chat sessions yet. Start a new conversation.")
        return

    for index, session in enumerate(sessions):
        session_id = str(session.get("session_id", "")).strip()
        if not session_id:
            continue
        title = str(session.get("title", DEFAULT_TITLE)).strip() or DEFAULT_TITLE
        label = title if len(title) <= 42 else f"{title[:39].rstrip()}..."
        time_label = format_session_date_label(session.get("updated_at", ""))

        left, right = st.columns([0.82, 0.18], gap="small")
        button_type = "primary" if session_id == active_session_id else "secondary"
        if left.button(label, key=f"chat_session_{index}_{session_id}", use_container_width=True, type=button_type):
            loaded = load_chat_session(session_id) or session
            _queue_pending_chat_load(
                session_state=session_state,
                session_id=session_id,
                context_scope=loaded.get("context_scope", default_scope),
                default_scope=default_scope,
            )
            st.rerun()
        right.markdown(f"<div class='chat-session-time'>{escape(time_label or '')}</div>", unsafe_allow_html=True)


def _format_message_timestamp(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M"]:
        try:
            return datetime.strptime(text, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return text


def _render_assistant_actions(message: dict, message_index: int) -> dict | None:
    actions = message.get("actions", [])
    if not isinstance(actions, list) or not actions:
        return None

    functional_actions: list[dict] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        action_type = str(action.get("type", "")).strip()
        if action_type not in {"navigate", "prompt"}:
            continue
        target = str(action.get("target", "")).strip()
        target_page = str(action.get("target_page", target)).strip() if action_type == "navigate" else ""
        if action_type == "navigate" and not (target_page or target):
            continue
        if action_type == "prompt" and not target:
            continue
        functional_actions.append(action)

    if not functional_actions:
        return None

    action_to_run = None
    cols = st.columns(min(3, len(functional_actions)))
    for idx, action in enumerate(functional_actions):
        label = str(action.get("label", "Action")).strip() or "Action"
        if cols[idx % len(cols)].button(label, key=f"chat_action_{message_index}_{idx}", use_container_width=True):
            action_to_run = action
    return action_to_run


def _process_navigation_action(action: dict, session_state: MutableMapping, set_flash_message_callback: Callable) -> None:
    target_page = str(action.get("target_page") or action.get("target") or "").strip()
    if not target_page:
        return
    session_state["page"] = target_page
    if callable(set_flash_message_callback):
        set_flash_message_callback(f"Navigated to {target_page} from SmartSite AI action.", "info")
    st.rerun()


def _should_show_suggestions(messages: list[dict]) -> bool:
    if not messages:
        return True
    return not any(str(item.get("role", "")).strip().lower() == "user" for item in messages)


def _render_suggestion_chips(selected_scope: str, active_session_id: str) -> str:
    prompts = quick_prompts_for_scope(selected_scope, limit=7)
    if not prompts:
        return ""

    st.markdown("<div class='chat-chip-note'>Quick suggestions</div>", unsafe_allow_html=True)
    cols = st.columns(min(3, len(prompts)))
    for idx, prompt in enumerate(prompts):
        label = str(prompt.get("label", "Suggestion")).strip() or "Suggestion"
        prompt_text = str(prompt.get("prompt", "")).strip()
        if not prompt_text:
            continue
        help_text = str(prompt.get("description", "")).strip()
        key = f"chat_suggest_{active_session_id}_{idx}_{prompt.get('id', '')}"
        if cols[idx % len(cols)].button(label, key=key, use_container_width=True, help=help_text):
            return prompt_text
    return ""


def _run_prompt(
    prompt: str,
    *,
    active_session_id: str,
    selected_scope: str,
    context_df: pd.DataFrame,
    page_name: str,
    context_bundle: dict,
    session_state: MutableMapping,
    normalize_text_value_callback: Callable,
    log_chat_usage_callback: Callable,
) -> None:
    clean_prompt = _normalize_text(prompt, normalize_text_value_callback).strip()
    if not clean_prompt:
        return

    append_chat_message(active_session_id, make_user_message(clean_prompt))

    response_payload = generate_chatbot_response(clean_prompt, context_df, context_bundle)
    assistant_message = make_assistant_message(
        response_payload.get("response_text", ""),
        intent_id=response_payload.get("intent_id", ""),
        response_mode=response_payload.get("response_mode", ""),
        actions=response_payload.get("actions", []),
    )
    append_chat_message(active_session_id, assistant_message)

    log_chat_usage_event(
        log_chat_usage_callback,
        username=_normalize_text(session_state.get("current_user", ""), normalize_text_value_callback),
        full_name=_normalize_text(session_state.get("current_full_name", ""), normalize_text_value_callback),
        role=_normalize_text(session_state.get("current_role", ""), normalize_text_value_callback),
        page_name=page_name,
        context_scope=selected_scope,
        prompt=clean_prompt,
        response_payload=response_payload,
    )
    st.rerun()


def _get_scroll_container(height: int):
    try:
        return st.container(height=height, border=False)
    except TypeError:
        return st.container()


def render_chatbot_assistance_page(
    *,
    master_df: pd.DataFrame,
    page_name: str,
    resolve_chatbot_context_df_callback: Callable[[str, pd.DataFrame], pd.DataFrame],
    load_notifications_callback: Callable[[], pd.DataFrame],
    get_trial_context_callback: Callable[[], dict],
    log_chat_usage_callback: Callable,
    set_flash_message_callback: Callable[[str, str], None],
    session_state: MutableMapping,
    normalize_text_value_callback: Callable,
) -> None:
    _inject_chat_styles()

    st.markdown(
        "<div class='page-title'>Chatbot Assistance</div>"
        "<div class='page-sub'>Deterministic workflow guidance with persistent sessions, in-chat suggestions, and direct navigation actions.</div>",
        unsafe_allow_html=True,
    )

    last_context_page = _normalize_text(session_state.get("last_context_page", DEFAULT_SCOPE), normalize_text_value_callback)
    default_scope = default_scope_from_last_page(last_context_page)

    _apply_pending_chat_load(session_state=session_state, default_scope=default_scope)

    if CHAT_SCOPE_KEY not in session_state:
        session_state[CHAT_SCOPE_KEY] = default_scope

    username = _resolve_username(session_state, normalize_text_value_callback)
    sessions, active_session = _ensure_active_session(
        username=username,
        session_state=session_state,
        default_scope=default_scope,
    )

    active_session_id = str(active_session.get("session_id", "")).strip()
    state_session_id = str(session_state.get(ACTIVE_SESSION_KEY, "")).strip()
    if state_session_id:
        active_session_id = state_session_id

    if not active_session_id:
        st.error("Unable to initialize chat session.")
        return

    active_session = load_chat_session(active_session_id) or active_session

    current_scope = _safe_scope(session_state.get(CHAT_SCOPE_KEY, default_scope), default_scope)
    if current_scope not in CHAT_SCOPE_OPTIONS:
        current_scope = default_scope
    session_state[CHAT_SCOPE_KEY] = current_scope

    left_col, right_col = st.columns([0.82, 1.78], gap="small")

    with left_col:
        st.markdown("<div class='chat-rail'>", unsafe_allow_html=True)
        st.markdown("<p class='chat-rail-title'>Sessions</p>", unsafe_allow_html=True)
        st.markdown("<p class='chat-rail-sub'>Create and switch deterministic chat sessions.</p>", unsafe_allow_html=True)

        if st.button("New Chat", key="chat_new_session", use_container_width=True, type="primary"):
            selected_scope = _safe_scope(session_state.get(CHAT_SCOPE_KEY, default_scope), default_scope)
            created = _create_session_with_welcome(username, selected_scope)
            session_state[ACTIVE_SESSION_KEY] = created["session_id"]
            session_state[CHAT_SCOPE_KEY] = _safe_scope(created.get("context_scope", selected_scope), selected_scope)
            session_state[CHAT_INPUT_KEY] = ""
            st.rerun()

        selected_scope = st.selectbox(
            "Context Scope",
            options=CHAT_SCOPE_OPTIONS,
            index=CHAT_SCOPE_OPTIONS.index(current_scope),
            key=CHAT_SCOPE_KEY,
        )

        st.markdown("<div class='chat-section-title'>Recent Sessions</div>", unsafe_allow_html=True)
        sessions = load_chat_sessions(username)
        _render_recent_sessions(
            sessions=sessions,
            active_session_id=active_session_id,
            default_scope=default_scope,
            session_state=session_state,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with right_col:
        selected_scope = _safe_scope(session_state.get(CHAT_SCOPE_KEY, default_scope), default_scope)
        active_session = load_chat_session(active_session_id) or active_session
        active_session = _sync_active_scope(active_session, selected_scope)

        context_df = resolve_chatbot_context_df_callback(selected_scope, master_df)
        context_bundle = {
            "scope_name": selected_scope,
            "trial_context": get_trial_context_callback() if callable(get_trial_context_callback) else {},
            "notifications_loader": load_notifications_callback,
        }

        st.markdown("<div class='chat-workspace'>", unsafe_allow_html=True)
        st.markdown(
            (
                "<div class='chat-workspace-head'>"
                "<p class='chat-workspace-title'>SmartSite AI Assistant</p>"
                f"<p class='chat-workspace-sub'>Session: {escape(str(active_session.get('title', DEFAULT_TITLE)))} "
                f"| Scope: {escape(selected_scope)} | Rows: {len(context_df)} | Deterministic mode</p>"
                "</div>"
            ),
            unsafe_allow_html=True,
        )

        action_to_run = None
        messages = active_session.get("messages", []) if isinstance(active_session.get("messages", []), list) else []

        with _get_scroll_container(520):
            if not messages:
                st.markdown("<div class='chat-empty'>No messages yet. Use a suggestion or type a prompt below.</div>", unsafe_allow_html=True)
            for idx, message in enumerate(messages):
                role = str(message.get("role", "assistant")).strip().lower()
                content = escape(str(message.get("content", "")).strip()).replace("\n", "<br>")
                timestamp = _format_message_timestamp(message.get("timestamp", ""))

                if role == "user":
                    st.markdown(
                        f"<div class='chat-bubble-meta' style='text-align:right;'>You {escape(timestamp)}</div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<div class='chat-row-user'><div class='chat-bubble-user'>{content}</div></div>",
                        unsafe_allow_html=True,
                    )
                    continue

                st.markdown(
                    f"<div class='chat-bubble-meta'>SmartSite AI {escape(timestamp)}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"<div class='chat-bubble-assistant'>{content}</div>", unsafe_allow_html=True)
                clicked_action = _render_assistant_actions(message, idx)
                if clicked_action:
                    action_to_run = clicked_action

        pending_prompt = ""
        if _should_show_suggestions(messages):
            pending_prompt = _render_suggestion_chips(selected_scope, active_session_id)

        with st.form("chat_input_form", clear_on_submit=True):
            in_left, in_right = st.columns([0.86, 0.14], gap="small")
            with in_left:
                typed_prompt = st.text_input(
                    "Message SmartSite AI",
                    key=CHAT_INPUT_KEY,
                    placeholder="Ask about ranking, feasibility, qualification, notifications, or navigation...",
                    label_visibility="collapsed",
                )
            with in_right:
                submit = st.form_submit_button("Send", use_container_width=True)

        st.markdown(
            "<div class='chat-disclaimer'>Deterministic responses only. Replies use FAQ intent matching and local handlers scoped to current app data.</div>",
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

        if action_to_run:
            action_type = str(action_to_run.get("type", "")).strip()
            if action_type == "navigate":
                _process_navigation_action(action_to_run, session_state, set_flash_message_callback)
            elif action_type == "prompt" and not pending_prompt:
                pending_prompt = str(action_to_run.get("target", "")).strip()

        if submit and typed_prompt.strip():
            pending_prompt = typed_prompt.strip()

        if pending_prompt:
            _run_prompt(
                pending_prompt,
                active_session_id=active_session_id,
                selected_scope=selected_scope,
                context_df=context_df,
                page_name=page_name,
                context_bundle=context_bundle,
                session_state=session_state,
                normalize_text_value_callback=normalize_text_value_callback,
                log_chat_usage_callback=log_chat_usage_callback,
            )
