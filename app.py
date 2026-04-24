from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st
import pdfplumber
import re

def extract_protocol_data(pdf_file):
    text = ""
    with pdfplumber.open(pdf_file) as pdf:
        for page in pdf.pages:
            if page.extract_text():
                text += page.extract_text() + "\n"

    protocol_id = ""
    match = re.search(r"Trial ID:\s*(\S+)", text)
    if match:
        protocol_id = match.group(1)

    study_title = ""
    match = re.search(r"Protocol title:\s*(.+)", text)
    if match:
        study_title = match.group(1)

    therapeutic_area = ""
    if "diabetes" in text.lower():
        therapeutic_area = "Endocrinology"

    indication = ""
    match = re.search(r"type\s*1\s*diabetes", text.lower())
    if match:
        indication = "Type 1 Diabetes"

    return {
        "study_title": study_title,
        "protocol_id": protocol_id,
        "therapeutic_area": therapeutic_area,
        "indication": indication
    }

st.set_page_config(page_title="SmartSite Select", page_icon="🧬", layout="wide", initial_sidebar_state="expanded")

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
if not DATA_DIR.exists():
    for probe in [Path.cwd() / "data", Path(__file__).parent.parent / "data"]:
        if probe.exists():
            DATA_DIR = probe
            break

CONFIG_PATH = DATA_DIR / "README.md"
TIMESTAMP_FMT = "%Y-%m-%d %H:%M:%S"

SITE_ACTION_COLUMNS = [
    "site_id", "manual_select", "preferred", "final_status_override",
    "selection_justification", "cda_status_override", "cra_flag_override",
    "cra_comment", "notification_ack", "last_updated",
]
SITE_ACTION_TEXT_COLUMNS = [
    "site_id", "final_status_override", "selection_justification",
    "cda_status_override", "cra_flag_override", "cra_comment", "last_updated",
]
SITE_ACTION_BOOL_COLUMNS = ["manual_select", "preferred", "notification_ack"]

SURVEY_TRACKING_COLUMNS = [
    "site_id", "response_received", "survey_sent", "survey_sent_at",
    "response_received_at", "reminder_count", "days_open", "survey_template",
    "secure_link", "last_updated",
]
SURVEY_TRACKING_TEXT_COLUMNS = [
    "site_id", "survey_sent_at", "response_received_at", "survey_template",
    "secure_link", "last_updated",
]
SURVEY_TRACKING_BOOL_COLUMNS = ["survey_sent", "response_received"]
SURVEY_TRACKING_NUMERIC_SPEC = {
    "reminder_count": {"default": 0, "dtype": "int"},
    "days_open": {"default": 0, "dtype": "int"},
}

NOTIFICATION_COLUMNS = ["notification_id", "site_id", "type", "priority", "message", "created_at", "acknowledged"]
NOTIFICATION_TEXT_COLUMNS = ["notification_id", "site_id", "type", "priority", "message", "created_at"]
NOTIFICATION_BOOL_COLUMNS = ["acknowledged"]

USER_COLUMNS = ["username", "password", "full_name", "role", "is_active"]
USER_TEXT_COLUMNS = ["username", "password", "full_name", "role"]
USER_BOOL_COLUMNS = ["is_active"]

CHAT_USAGE_COLUMNS = [
    "usage_id", "username", "full_name", "role", "timestamp", "page_name",
    "prompt", "response", "used_local_llm", "success", "error_message",
]
CHAT_USAGE_TEXT_COLUMNS = ["usage_id", "username", "full_name", "role", "timestamp", "page_name", "prompt", "response", "error_message"]
CHAT_USAGE_BOOL_COLUMNS = ["used_local_llm", "success"]

DEFAULT_CHAT_GREETING = (
    "Ask me about site feasibility, qualification status, top sites in a region, "
    "or how the AI score was calculated."
)

TRUE_BOOL_VALUES = {"1", "true", "t", "yes", "y"}
FALSE_BOOL_VALUES = {"0", "false", "f", "no", "n", ""}


def now_ts() -> str:
    return datetime.now().strftime(TIMESTAMP_FMT)


def load_json_config(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_csv(name: str) -> pd.DataFrame:
    path = DATA_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Missing required data file: {path}")
    return pd.read_csv(path)


def save_csv(df: pd.DataFrame, name: str) -> None:
    (DATA_DIR / name).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(DATA_DIR / name, index=False)


def load_or_init(name: str, columns: list[str]) -> pd.DataFrame:
    path = DATA_DIR / name
    if path.exists():
        df = pd.read_csv(path)
        for col in columns:
            if col not in df.columns:
                df[col] = ""
        return df[columns]
    df = pd.DataFrame(columns=columns)
    save_csv(df, name)
    return df


def append_row(name: str, row: dict, columns: list[str]) -> None:
    df = load_or_init(name, columns)
    df.loc[len(df)] = {c: row.get(c, "") for c in columns}
    save_csv(df, name)


def append_audit(action: str, entity_type: str, entity_id: str, details: str) -> None:
    append_row(
        "audit_log.csv",
        {"timestamp": now_ts(), "action": action, "entity_type": entity_type,
         "entity_id": entity_id, "details": details},
        ["timestamp", "action", "entity_type", "entity_id", "details"],
    )


def _is_missing(value) -> bool:
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False


def normalize_text_value(value) -> str:
    if _is_missing(value):
        return ""
    return str(value)


def normalize_bool_value(value) -> bool:
    if isinstance(value, bool):
        return value
    if _is_missing(value):
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in TRUE_BOOL_VALUES:
        return True
    if text in FALSE_BOOL_VALUES:
        return False
    return False


def normalize_text_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in cols:
        if col not in out.columns:
            out[col] = ""
        out[col] = out[col].apply(normalize_text_value)
    return out


def normalize_bool_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in cols:
        if col not in out.columns:
            out[col] = False
        out[col] = out[col].apply(normalize_bool_value).astype(bool)
    return out


def normalize_numeric_columns(df: pd.DataFrame, spec: dict[str, dict]) -> pd.DataFrame:
    out = df.copy()
    for col, cfg in spec.items():
        default = cfg.get("default", 0)
        dtype = cfg.get("dtype", "float")
        if col not in out.columns:
            out[col] = default
        out[col] = pd.to_numeric(out[col], errors="coerce").fillna(default)
        if dtype == "int":
            out[col] = out[col].round().astype(int)
        elif dtype == "float":
            out[col] = out[col].astype(float)
    return out


def normalize_site_actions(df: pd.DataFrame) -> pd.DataFrame:
    out = normalize_text_columns(df, SITE_ACTION_TEXT_COLUMNS)
    out = normalize_bool_columns(out, SITE_ACTION_BOOL_COLUMNS)
    return out[SITE_ACTION_COLUMNS]


def normalize_survey_tracking(df: pd.DataFrame) -> pd.DataFrame:
    out = normalize_text_columns(df, SURVEY_TRACKING_TEXT_COLUMNS)
    out = normalize_bool_columns(out, SURVEY_TRACKING_BOOL_COLUMNS)
    out = normalize_numeric_columns(out, SURVEY_TRACKING_NUMERIC_SPEC)
    return out[SURVEY_TRACKING_COLUMNS]


def normalize_notifications(df: pd.DataFrame) -> pd.DataFrame:
    out = normalize_text_columns(df, NOTIFICATION_TEXT_COLUMNS)
    out = normalize_bool_columns(out, NOTIFICATION_BOOL_COLUMNS)
    return out[NOTIFICATION_COLUMNS]


def default_site_action_row(site_id: str) -> dict:
    return {
        "site_id": site_id, "manual_select": False, "preferred": False,
        "final_status_override": "", "selection_justification": "",
        "cda_status_override": "", "cra_flag_override": "", "cra_comment": "",
        "notification_ack": False, "last_updated": "",
    }


def default_survey_tracking_row(site_id: str, response_received: bool = False) -> dict:
    sent = bool(response_received)
    return {
        "site_id": site_id, "response_received": bool(response_received),
        "survey_sent": sent, "survey_sent_at": "", "response_received_at": "",
        "reminder_count": 0, "days_open": 0, "survey_template": "",
        "secure_link": "", "last_updated": "",
    }


def _ensure_site_rows(df: pd.DataFrame, site_ids: list[str], row_builder) -> tuple[pd.DataFrame, bool]:
    out = df.copy()
    if "site_id" not in out.columns:
        out["site_id"] = ""
    out["site_id"] = out["site_id"].apply(normalize_text_value)
    existing = set(out["site_id"])
    missing = [sid for sid in site_ids if sid not in existing]
    if not missing:
        return out, False
    additions = pd.DataFrame([row_builder(sid) for sid in missing])
    out = pd.concat([out, additions], ignore_index=True)
    return out, True


def load_or_init_site_actions(site_ids: list[str]) -> pd.DataFrame:
    path = DATA_DIR / "site_actions.csv"
    changed = False
    if path.exists():
        df = pd.read_csv(path)
        changed = any(col not in df.columns for col in SITE_ACTION_COLUMNS)
    else:
        df = pd.DataFrame([default_site_action_row(sid) for sid in site_ids])
        save_csv(df, path.name)
        changed = True
    df = normalize_site_actions(df)
    df, added_rows = _ensure_site_rows(df, site_ids, default_site_action_row)
    if added_rows:
        changed = True
    df = normalize_site_actions(df.drop_duplicates(subset=["site_id"], keep="last"))
    if changed:
        save_csv(df, path.name)
    return df


def load_or_init_survey_tracking(site_ids: list[str], feasibility: pd.DataFrame) -> pd.DataFrame:
    path = DATA_DIR / "survey_tracking.csv"
    changed = False
    has_response = feasibility.groupby("site_id")["interest_level"].apply(
        lambda s: s.fillna("").astype(str).str.len().gt(0).any()
    )

    def survey_row_builder(site_id: str) -> dict:
        return default_survey_tracking_row(site_id, bool(has_response.get(site_id, False)))

    if path.exists():
        df = pd.read_csv(path)
        changed = any(col not in df.columns for col in SURVEY_TRACKING_COLUMNS)
    else:
        df = pd.DataFrame([survey_row_builder(sid) for sid in site_ids])
        save_csv(df, path.name)
        changed = True
    df = normalize_survey_tracking(df)
    df, added_rows = _ensure_site_rows(df, site_ids, survey_row_builder)
    if added_rows:
        changed = True
    df = normalize_survey_tracking(df.drop_duplicates(subset=["site_id"], keep="last"))
    if changed:
        save_csv(df, path.name)
    return df


def update_days_open(df: pd.DataFrame) -> pd.DataFrame:
    out = normalize_survey_tracking(df)
    sent = pd.to_datetime(out["survey_sent_at"], errors="coerce")
    out["days_open"] = ((pd.Timestamp.now() - sent).dt.days).fillna(0).clip(lower=0).astype(int)
    out.loc[~out["survey_sent"], "days_open"] = 0
    return out


def load_or_init_notifications() -> pd.DataFrame:
    path = DATA_DIR / "notifications.csv"
    changed = False
    if path.exists():
        df = pd.read_csv(path)
        changed = any(col not in df.columns for col in NOTIFICATION_COLUMNS)
    else:
        df = pd.DataFrame(columns=NOTIFICATION_COLUMNS)
        save_csv(df, path.name)
        changed = True
    df = normalize_notifications(df)
    if changed:
        save_csv(df, path.name)
    return df


def default_user_rows() -> list[dict]:
    return [
        {"username": "admin", "password": "admin123", "full_name": "Alex Morgan", "role": "Admin", "is_active": True},
        {"username": "cra_user", "password": "cra123", "full_name": "Jordan Lee", "role": "CRA", "is_active": True},
    ]


def load_or_init_users() -> pd.DataFrame:
    path = DATA_DIR / "users.csv"
    changed = False
    if path.exists():
        df = pd.read_csv(path)
        changed = any(col not in df.columns for col in USER_COLUMNS)
    else:
        df = pd.DataFrame(default_user_rows())
        save_csv(df, path.name)
        changed = True
    if df.empty:
        df = pd.DataFrame(default_user_rows())
        changed = True
    df = normalize_text_columns(df, USER_TEXT_COLUMNS)
    df = normalize_bool_columns(df, USER_BOOL_COLUMNS)
    for col in USER_COLUMNS:
        if col not in df.columns:
            df[col] = "" if col in USER_TEXT_COLUMNS else False
    df = df[USER_COLUMNS]
    if changed:
        save_csv(df, path.name)
    return df


def load_users() -> pd.DataFrame:
    return load_or_init_users()


def authenticate_user(username: str, password: str) -> dict | None:
    users = load_users().copy()
    user_name_key = normalize_text_value(username).strip().lower()
    password_key = normalize_text_value(password)
    if not user_name_key or not password_key:
        return None
    users["_username_key"] = users["username"].str.strip().str.lower()
    match = users[
        (users["_username_key"] == user_name_key)
        & (users["password"] == password_key)
        & (users["is_active"])
    ]
    if match.empty:
        return None
    row = match.iloc[0]
    return {
        "username": normalize_text_value(row["username"]),
        "full_name": normalize_text_value(row["full_name"]),
        "role": normalize_text_value(row["role"]),
    }


def load_or_init_chat_usage() -> pd.DataFrame:
    path = DATA_DIR / "chat_usage.csv"
    changed = False
    if path.exists():
        df = pd.read_csv(path)
        changed = any(col not in df.columns for col in CHAT_USAGE_COLUMNS)
    else:
        df = pd.DataFrame(columns=CHAT_USAGE_COLUMNS)
        save_csv(df, path.name)
        changed = True
    df = normalize_text_columns(df, CHAT_USAGE_TEXT_COLUMNS)
    df = normalize_bool_columns(df, CHAT_USAGE_BOOL_COLUMNS)
    df = df[CHAT_USAGE_COLUMNS]
    if changed:
        save_csv(df, path.name)
    return df


def truncate_for_storage(value: str, max_len: int = 2000) -> str:
    text = normalize_text_value(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def append_chat_usage(username, full_name, role, page_name, prompt, response, used_local_llm, success, error_message) -> None:
    try:
        usage = load_or_init_chat_usage()
        usage.loc[len(usage)] = {
            "usage_id": f"U{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
            "username": truncate_for_storage(username, 120),
            "full_name": truncate_for_storage(full_name, 200),
            "role": truncate_for_storage(role, 80),
            "timestamp": now_ts(),
            "page_name": truncate_for_storage(page_name, 120),
            "prompt": truncate_for_storage(prompt, 1600),
            "response": truncate_for_storage(response, 1600),
            "used_local_llm": bool(used_local_llm),
            "success": bool(success),
            "error_message": truncate_for_storage(error_message, 400),
        }
        save_csv(usage[CHAT_USAGE_COLUMNS], "chat_usage.csv")
    except Exception:
        pass


def reset_chat_history() -> None:
    st.session_state["chat_history"] = [{"role": "assistant", "content": DEFAULT_CHAT_GREETING}]


CONFIG = load_json_config(CONFIG_PATH)
SITES = load_csv("sites.csv")
PIS = load_csv("principal_investigators.csv")
PERF = load_csv("site_performance_history.csv")
FEAS = load_csv("feasibility_responses_new_trial.csv")
REC = load_csv("recommended_top_sites.csv")

ACTIONS = load_or_init_site_actions(SITES["site_id"].astype(str).tolist())
TRACK = update_days_open(load_or_init_survey_tracking(SITES["site_id"].astype(str).tolist(), FEAS))
save_csv(normalize_survey_tracking(TRACK), "survey_tracking.csv")
NOTES = load_or_init_notifications()
AUDIT = load_or_init("audit_log.csv", ["timestamp", "action", "entity_type", "entity_id", "details"])
USERS = load_or_init_users()
CHAT_USAGE = load_or_init_chat_usage()

TRIAL = CONFIG.get("new_trial", {})
WEIGHTS = CONFIG.get("scoring_weights", {})
TRIAL_PHASE_OPTIONS = ["I", "I/II", "II", "III", "IV"]
TRIAL_CONTEXT_WIDGET_KEYS = {
    "study_title": "setup_study_title",
    "protocol_id": "setup_protocol_id",
    "therapeutic_area": "setup_therapeutic_area",
    "indication": "setup_indication",
    "phase": "setup_phase",
    "total_target_enrollment": "setup_total_target_enrollment",
    "min_age": "setup_min_age",
    "max_age": "setup_max_age",
    "gender": "setup_gender",
    "target_geographies": "setup_target_geographies",
    "require_biomarker_testing": "setup_require_biomarker_testing",
    "rare_disease_protocol": "setup_rare_disease_protocol",
    "competitive_trial_density_tolerance": "setup_competitive_trial_density_tolerance",
    "irb_preference": "setup_irb_preference",
}


def get_trial_ta_options() -> list[str]:
    values = set()
    if "therapeutic_area" in PERF.columns:
        values.update(PERF["therapeutic_area"].dropna().astype(str).str.strip().tolist())
    if "new_trial_ta" in FEAS.columns:
        values.update(FEAS["new_trial_ta"].dropna().astype(str).str.strip().tolist())
    trial_ta = normalize_text_value(TRIAL.get("therapeutic_area", "Oncology")).strip()
    if trial_ta:
        values.add(trial_ta)
    return sorted(v for v in values if v)


def get_trial_indication_options(therapeutic_area: str | None = None) -> list[str]:
    ta = normalize_text_value(therapeutic_area).strip()
    values = set()
    if {"therapeutic_area", "indication"}.issubset(PERF.columns):
        perf_slice = PERF
        if ta:
            perf_slice = perf_slice[perf_slice["therapeutic_area"].astype(str).str.strip() == ta]
        values.update(perf_slice["indication"].dropna().astype(str).str.strip().tolist())
    if {"new_trial_ta", "new_trial_indication"}.issubset(FEAS.columns):
        feas_slice = FEAS
        if ta:
            feas_slice = feas_slice[feas_slice["new_trial_ta"].astype(str).str.strip() == ta]
        values.update(feas_slice["new_trial_indication"].dropna().astype(str).str.strip().tolist())
    if not values:
        if "indication" in PERF.columns:
            values.update(PERF["indication"].dropna().astype(str).str.strip().tolist())
        if "new_trial_indication" in FEAS.columns:
            values.update(FEAS["new_trial_indication"].dropna().astype(str).str.strip().tolist())
    trial_ind = normalize_text_value(TRIAL.get("indication", "NSCLC")).strip()
    if trial_ind:
        values.add(trial_ind)
    return sorted(v for v in values if v)


def _build_default_trial_context(trial_seed: dict) -> dict:
    trial_ta = normalize_text_value(trial_seed.get("therapeutic_area", "Oncology")).strip() or "Oncology"
    ta_options = get_trial_ta_options()
    if trial_ta not in ta_options:
        ta_options = sorted(set(ta_options + [trial_ta]))
    if ta_options and trial_ta not in ta_options:
        trial_ta = ta_options[0]

    trial_ind = normalize_text_value(trial_seed.get("indication", "NSCLC")).strip() or "NSCLC"
    indication_options = get_trial_indication_options(trial_ta)
    if trial_ind not in indication_options:
        indication_options = sorted(set(indication_options + [trial_ind]))
    if indication_options and trial_ind not in indication_options:
        trial_ind = indication_options[0]

    trial_phase = normalize_text_value(trial_seed.get("phase", "III")).strip() or "III"
    if trial_phase not in TRIAL_PHASE_OPTIONS:
        trial_phase = "III"

    geo_options = sorted(SITES["region"].dropna().astype(str).str.strip().unique().tolist())
    default_geos = geo_options[:3] if geo_options else []

    return {
        "study_title": "", "protocol_id": "", "therapeutic_area": trial_ta,
        "indication": trial_ind, "phase": trial_phase, "total_target_enrollment": 450,
        "min_age": 18, "max_age": 85, "gender": "All",
        "target_geographies": default_geos, "require_biomarker_testing": True,
        "rare_disease_protocol": False, "competitive_trial_density_tolerance": "Medium (Standard)",
        "irb_preference": "Central Preferred", "generated_at": "",
    }


DEFAULT_TRIAL_CONTEXT = _build_default_trial_context(TRIAL)


def normalize_trial_context(raw_context: dict | None) -> dict:
    merged = DEFAULT_TRIAL_CONTEXT.copy()
    if isinstance(raw_context, dict):
        merged.update(raw_context)

    ta_options = get_trial_ta_options()
    therapeutic_area = normalize_text_value(merged.get("therapeutic_area", DEFAULT_TRIAL_CONTEXT["therapeutic_area"])).strip()
    if not therapeutic_area:
        therapeutic_area = DEFAULT_TRIAL_CONTEXT["therapeutic_area"]
    if ta_options and therapeutic_area not in ta_options:
        therapeutic_area = ta_options[0]

    indication_options = get_trial_indication_options(therapeutic_area)
    indication = normalize_text_value(merged.get("indication", DEFAULT_TRIAL_CONTEXT["indication"])).strip()
    if not indication:
        indication = DEFAULT_TRIAL_CONTEXT["indication"]
    if indication_options and indication not in indication_options:
        indication = indication_options[0]

    phase = normalize_text_value(merged.get("phase", DEFAULT_TRIAL_CONTEXT["phase"])).strip()
    if phase not in TRIAL_PHASE_OPTIONS:
        phase = DEFAULT_TRIAL_CONTEXT["phase"]

    geo_options = set(SITES["region"].dropna().astype(str).str.strip().tolist())
    geo_raw = merged.get("target_geographies", DEFAULT_TRIAL_CONTEXT["target_geographies"])
    if isinstance(geo_raw, list):
        target_geographies = [normalize_text_value(g).strip() for g in geo_raw if normalize_text_value(g).strip() in geo_options]
    else:
        target_geographies = []
    if not target_geographies:
        target_geographies = [g for g in DEFAULT_TRIAL_CONTEXT["target_geographies"] if g in geo_options]

    enrollment_raw = pd.to_numeric(merged.get("total_target_enrollment", DEFAULT_TRIAL_CONTEXT["total_target_enrollment"]), errors="coerce")
    min_age_raw = pd.to_numeric(merged.get("min_age", DEFAULT_TRIAL_CONTEXT["min_age"]), errors="coerce")
    max_age_raw = pd.to_numeric(merged.get("max_age", DEFAULT_TRIAL_CONTEXT["max_age"]), errors="coerce")

    total_target_enrollment = int(enrollment_raw) if not _is_missing(enrollment_raw) else int(DEFAULT_TRIAL_CONTEXT["total_target_enrollment"])
    min_age = int(min_age_raw) if not _is_missing(min_age_raw) else int(DEFAULT_TRIAL_CONTEXT["min_age"])
    max_age = int(max_age_raw) if not _is_missing(max_age_raw) else int(DEFAULT_TRIAL_CONTEXT["max_age"])
    min_age = max(0, min_age)
    max_age = max(min_age, max_age)

    gender = normalize_text_value(merged.get("gender", DEFAULT_TRIAL_CONTEXT["gender"])).strip() or DEFAULT_TRIAL_CONTEXT["gender"]
    if gender not in {"All", "Male", "Female"}:
        gender = "All"

    tolerance = normalize_text_value(merged.get("competitive_trial_density_tolerance", DEFAULT_TRIAL_CONTEXT["competitive_trial_density_tolerance"])).strip() or DEFAULT_TRIAL_CONTEXT["competitive_trial_density_tolerance"]
    if tolerance not in {"Low", "Medium (Standard)", "High"}:
        tolerance = DEFAULT_TRIAL_CONTEXT["competitive_trial_density_tolerance"]

    irb_preference = normalize_text_value(merged.get("irb_preference", DEFAULT_TRIAL_CONTEXT["irb_preference"])).strip() or DEFAULT_TRIAL_CONTEXT["irb_preference"]
    if irb_preference not in {"Either", "Central Preferred", "Local Accepted"}:
        irb_preference = DEFAULT_TRIAL_CONTEXT["irb_preference"]

    return {
        "study_title": normalize_text_value(merged.get("study_title", DEFAULT_TRIAL_CONTEXT["study_title"])).strip(),
        "protocol_id": normalize_text_value(merged.get("protocol_id", DEFAULT_TRIAL_CONTEXT["protocol_id"])).strip(),
        "therapeutic_area": therapeutic_area, "indication": indication, "phase": phase,
        "total_target_enrollment": max(1, total_target_enrollment),
        "min_age": min_age, "max_age": max_age, "gender": gender,
        "target_geographies": target_geographies,
        "require_biomarker_testing": normalize_bool_value(merged.get("require_biomarker_testing", DEFAULT_TRIAL_CONTEXT["require_biomarker_testing"])),
        "rare_disease_protocol": normalize_bool_value(merged.get("rare_disease_protocol", DEFAULT_TRIAL_CONTEXT["rare_disease_protocol"])),
        "competitive_trial_density_tolerance": tolerance,
        "irb_preference": irb_preference,
        "generated_at": normalize_text_value(merged.get("generated_at", DEFAULT_TRIAL_CONTEXT["generated_at"])).strip(),
    }


def reset_trial_identity_fields_for_new_entry() -> None:
    st.session_state["trial_context"] = normalize_trial_context(st.session_state.get("trial_context"))
    st.session_state["trial_context"]["study_title"] = ""
    st.session_state["trial_context"]["protocol_id"] = ""
    st.session_state.pop("setup_study_title", None)
    st.session_state.pop("setup_protocol_id", None)


def initialize_trial_context_state() -> None:
    st.session_state["trial_context"] = normalize_trial_context(st.session_state.get("trial_context"))
    active = st.session_state["trial_context"]
    for field, widget_key in TRIAL_CONTEXT_WIDGET_KEYS.items():
        if widget_key not in st.session_state:
            st.session_state[widget_key] = active.get(field)
    history = st.session_state.get("trial_context_history")
    if not isinstance(history, list):
        st.session_state["trial_context_history"] = []


def get_active_trial_context() -> dict:
    return normalize_trial_context(st.session_state.get("trial_context"))


def get_trial_context_from_setup_widgets() -> dict:
    draft = {}
    for field, widget_key in TRIAL_CONTEXT_WIDGET_KEYS.items():
        draft[field] = st.session_state.get(widget_key)
    return normalize_trial_context(draft)


def build_best_pi_lookup(pis: pd.DataFrame, trial_ta: str, trial_indication: str) -> pd.DataFrame:
    required_text = ["site_id", "pi_name", "specialty_therapeutic_area", "specialty_indication"]
    required_numeric = ["years_experience", "completed_trials", "audit_findings_last_3y"]
    pi_df = pis.copy()
    for col in required_text:
        if col not in pi_df.columns:
            pi_df[col] = ""
    for col in required_numeric:
        if col not in pi_df.columns:
            pi_df[col] = 0
    pi_df = normalize_text_columns(pi_df, required_text)
    pi_df = normalize_numeric_columns(pi_df, {
        "years_experience": {"default": 0, "dtype": "float"},
        "completed_trials": {"default": 0, "dtype": "float"},
        "audit_findings_last_3y": {"default": 0, "dtype": "float"},
    })
    pi_df = pi_df[(pi_df["site_id"] != "") & (pi_df["pi_name"] != "")].copy()
    if pi_df.empty:
        return pd.DataFrame(columns=["site_id", "matched_pi_name", "pi_years_experience", "pi_completed_trials", "pi_audit_findings_last_3y"])

    trial_ta_norm = normalize_text_value(trial_ta).strip().lower()
    trial_ind_norm = normalize_text_value(trial_indication).strip().lower()
    pi_df["_ta_match"] = pi_df["specialty_therapeutic_area"].str.strip().str.lower().eq(trial_ta_norm)
    pi_df["_ind_match"] = pi_df["specialty_indication"].str.strip().str.lower().eq(trial_ind_norm)
    pi_df["_match_tier"] = 2
    pi_df.loc[pi_df["_ta_match"], "_match_tier"] = 1
    pi_df.loc[pi_df["_ta_match"] & pi_df["_ind_match"], "_match_tier"] = 0

    ranked = pi_df.sort_values(
        ["site_id", "_match_tier", "years_experience", "completed_trials", "pi_name"],
        ascending=[True, True, False, False, True], kind="mergesort",
    )
    best = ranked.drop_duplicates("site_id", keep="first").rename(columns={
        "pi_name": "matched_pi_name", "years_experience": "pi_years_experience",
        "completed_trials": "pi_completed_trials", "audit_findings_last_3y": "pi_audit_findings_last_3y",
    })
    return best[["site_id", "matched_pi_name", "pi_years_experience", "pi_completed_trials", "pi_audit_findings_last_3y"]]


@st.cache_data(show_spinner=False)
def build_master(sites, pis, perf, feas, rec, actions, track, trial_ta: str, trial_indication: str, trial_phase: str):
    trial_ta_key = normalize_text_value(trial_ta).strip().lower()
    trial_ind_key = normalize_text_value(trial_indication).strip().lower()
    trial_phase_key = normalize_text_value(trial_phase).strip().lower()

    pi_match = build_best_pi_lookup(pis, trial_ta, trial_indication)

    perf_work = perf.copy()
    for col in ["therapeutic_area", "indication"]:
        if col not in perf_work.columns:
            perf_work[col] = ""
    perf_match = perf_work[
        perf_work["therapeutic_area"].astype(str).str.strip().str.lower().eq(trial_ta_key)
        & perf_work["indication"].astype(str).str.strip().str.lower().eq(trial_ind_key)
    ].copy()
    perf_agg = perf_match.groupby("site_id", as_index=False).agg(
        avg_enroll_rate_per_month=("avg_enroll_rate_per_month", "mean"),
        screen_fail_rate=("screen_fail_rate", "mean"),
        protocol_deviation_rate=("protocol_deviation_rate", "mean"),
        data_entry_lag_days=("data_entry_lag_days", "mean"),
        retention_rate=("retention_rate", "mean"),
        competing_trials_same_ta=("competing_trials_same_ta", "mean"),
        site_startup_days_hist=("site_startup_days", "mean"),
        actual_enrollment=("actual_enrollment", "sum"),
        target_enrollment=("target_enrollment", "sum"),
    )

    feas_match = feas.copy()
    if "new_trial_ta" in feas_match.columns:
        feas_match = feas_match[feas_match["new_trial_ta"].astype(str).str.strip().str.lower().eq(trial_ta_key)]
    if "new_trial_indication" in feas_match.columns:
        feas_match = feas_match[feas_match["new_trial_indication"].astype(str).str.strip().str.lower().eq(trial_ind_key)]
    if "new_trial_phase" in feas_match.columns:
        feas_match = feas_match[feas_match["new_trial_phase"].astype(str).str.strip().str.lower().eq(trial_phase_key)]
    feas_match = feas_match.copy()

    df = sites.merge(pi_match[[c for c in pi_match.columns if c in [
        "site_id", "matched_pi_name", "pi_years_experience", "pi_completed_trials", "pi_audit_findings_last_3y"
    ]]], on="site_id", how="left")
    df = df.merge(perf_agg, on="site_id", how="left")
    df = df.merge(feas_match, on="site_id", how="left")
    df = df.merge(rec, on="site_id", how="left", suffixes=("", "_rec"))
    df = df.merge(actions, on="site_id", how="left")
    df = df.merge(track, on="site_id", how="left")

    for col in ["interest_level", "est_startup_days", "projected_enroll_rate_per_month", "central_irb_preferred"]:
        rec_col = f"{col}_rec"
        if rec_col in df.columns:
            if col in df.columns:
                df[col] = df[col].fillna(df[rec_col])
            else:
                df[col] = df[rec_col]

    for col in [
        "pi_years_experience", "pi_completed_trials", "pi_audit_findings_last_3y",
        "avg_enroll_rate_per_month", "screen_fail_rate", "protocol_deviation_rate", "data_entry_lag_days",
        "retention_rate", "competing_trials_same_ta", "site_startup_days_hist", "actual_enrollment",
        "target_enrollment", "est_startup_days", "projected_enroll_rate_per_month", "site_selection_score"
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in ["manual_select", "preferred", "survey_sent", "response_received"]:
        if col in df.columns:
            df[col] = df[col].apply(normalize_bool_value)
        else:
            df[col] = False

    reminder_series = pd.to_numeric(df["reminder_count"], errors="coerce") if "reminder_count" in df.columns else pd.Series(0, index=df.index)
    days_open_series = pd.to_numeric(df["days_open"], errors="coerce") if "days_open" in df.columns else pd.Series(0, index=df.index)
    central_series = pd.to_numeric(df["central_irb_preferred"], errors="coerce") if "central_irb_preferred" in df.columns else pd.Series(0, index=df.index)
    df["reminder_count"] = reminder_series.fillna(0).astype(int)
    df["days_open"] = days_open_series.fillna(0).astype(int)
    df["central_irb_preferred"] = central_series.fillna(0).astype(int)

    if "matched_pi_name" not in df.columns:
        df["matched_pi_name"] = "No PI on file"
    df["matched_pi_name"] = df["matched_pi_name"].apply(normalize_text_value).replace("", "No PI on file")

    interest_weight = {"High": 100, "Medium": 70, "Low": 35}
    df["interest_score"] = df["interest_level"].map(interest_weight).fillna(0)
    df["ai_rank_score"] = (df["site_selection_score"].fillna(0) * 300).clip(0, 100).round(0)
    df["feasibility_score"] = (
        df["interest_score"] * 0.35
        + df["projected_enroll_rate_per_month"].fillna(df["avg_enroll_rate_per_month"]).fillna(0).clip(0, 10) * 5.5
        + (100 - df["est_startup_days"].fillna(df["site_startup_days_hist"]).fillna(70).clip(0, 120)) * 0.18
        + df["retention_rate"].fillna(0.75) * 20
        + df["central_irb_preferred"] * 8
    ).clip(0, 100).round(0)
    df["qualification_score"] = (
        df["ai_rank_score"] * 0.45 + df["feasibility_score"] * 0.35
        + df["pi_years_experience"].fillna(0) * 1.25
        - df["pi_audit_findings_last_3y"].fillna(0) * 3.5
    ).clip(0, 100).round(0)

    def risk_bucket(r):
        score = 0
        score += int(r.get("screen_fail_rate", 0) > 0.22)
        score += int(r.get("protocol_deviation_rate", 0) > 0.08)
        score += int(r.get("data_entry_lag_days", 0) > 7)
        score += int(r.get("competing_trials_same_ta", 0) >= 3)
        score += int(r.get("pi_audit_findings_last_3y", 0) >= 2)
        return "High" if score >= 3 else "Medium" if score >= 1 else "Low"

    df["risk_level"] = df.apply(risk_bucket, axis=1)
    default_cda = pd.cut(df["ai_rank_score"], bins=[-1, 60, 84, 100], labels=["Pending", "In Review", "Executed"]).astype(str)
    cda_override = df["cda_status_override"] if "cda_status_override" in df.columns else pd.Series("", index=df.index)
    df["cda_status"] = cda_override.replace("", pd.NA).fillna(default_cda)

    def cra_flag(r):
        override = str(r.get("cra_flag_override", "") or "").strip()
        if override:
            return override
        if r["risk_level"] == "High":
            return "Risk"
        if r["survey_sent"] and (not r["response_received"]) and r["days_open"] > 7:
            return "Feasibility Delay"
        if int(r.get("central_irb_preferred", 0)) == 0:
            return "IRB Review"
        return "None"

    df["cra_flag"] = df.apply(cra_flag, axis=1)
    df["final_status"] = "Backup"
    df.loc[df["preferred"], "final_status"] = "Selected"
    df.loc[df["risk_level"] == "High", "final_status"] = "Rejected"
    override_series = df["final_status_override"] if "final_status_override" in df.columns else pd.Series("", index=df.index)
    override = override_series.replace("", pd.NA)
    df["final_status"] = override.fillna(df["final_status"])
    df["country_label"] = df["country"].replace({
        "US": "United States", "UK": "United Kingdom", "IN": "India", "DE": "Germany",
        "FR": "France", "ES": "Spain", "CN": "China", "JP": "Japan", "CA": "Canada", "AU": "Australia"
    })
    df = df.sort_values(["ai_rank_score", "feasibility_score", "qualification_score"], ascending=False).reset_index(drop=True)
    return df


def clear_and_rerun():
    build_master.clear()
    st.rerun()


def _load_site_actions_for_batch(site_ids: list[str]) -> pd.DataFrame:
    requested_ids = [normalize_text_value(sid).strip() for sid in site_ids if normalize_text_value(sid).strip()]
    known_ids = SITES["site_id"].astype(str).tolist()
    all_ids = list(dict.fromkeys(known_ids + requested_ids))
    return normalize_site_actions(load_or_init_site_actions(all_ids))


def _apply_site_action_updates(df: pd.DataFrame, updates_by_site: dict[str, dict], update_ts: str) -> tuple[pd.DataFrame, bool]:
    out = normalize_site_actions(df)
    changed = False
    site_to_idx = {normalize_text_value(sid): idx for idx, sid in out["site_id"].items()}

    for site_id, updates in updates_by_site.items():
        site_key = normalize_text_value(site_id).strip()
        if not site_key or not isinstance(updates, dict):
            continue
        row_idx = site_to_idx.get(site_key)
        if row_idx is None:
            out = pd.concat([out, pd.DataFrame([default_site_action_row(site_key)])], ignore_index=True)
            row_idx = out.index[-1]
            site_to_idx[site_key] = row_idx

        row_changed = False
        for field, value in updates.items():
            if field not in SITE_ACTION_COLUMNS or field in {"site_id", "last_updated"}:
                continue
            normalized_value = normalize_bool_value(value) if field in SITE_ACTION_BOOL_COLUMNS else normalize_text_value(value)
            if out.at[row_idx, field] != normalized_value:
                out.at[row_idx, field] = normalized_value
                row_changed = True

        if row_changed:
            out.at[row_idx, "site_id"] = site_key
            out.at[row_idx, "last_updated"] = normalize_text_value(update_ts)
            changed = True

    return normalize_site_actions(out), changed


def persist_site_actions_by_row(updates_by_site: dict[str, dict]):
    if not isinstance(updates_by_site, dict) or not updates_by_site:
        return
    df = _load_site_actions_for_batch(list(updates_by_site.keys()))
    updated, changed = _apply_site_action_updates(df, updates_by_site, now_ts())
    if changed:
        save_csv(updated, "site_actions.csv")


def persist_site_action(site_id: str, **updates):
    site_key = normalize_text_value(site_id)
    if not site_key:
        return
    persist_site_actions_by_row({site_key: updates})


def persist_bulk_site_action(site_ids: list[str], **updates):
    updates_by_site = {
        normalize_text_value(sid).strip(): updates
        for sid in site_ids if normalize_text_value(sid).strip()
    }
    persist_site_actions_by_row(updates_by_site)


def persist_distribution(site_ids: list[str], template_name: str):
    df = normalize_survey_tracking(load_or_init_survey_tracking(SITES["site_id"].astype(str).tolist(), FEAS))
    ts = now_ts()
    for sid in site_ids:
        site_key = normalize_text_value(sid)
        idx = df.index[df["site_id"] == site_key]
        if len(idx) == 0:
            continue
        i = idx[0]
        df.at[i, "survey_sent"] = True
        df.at[i, "survey_sent_at"] = normalize_text_value(ts)
        df.at[i, "survey_template"] = normalize_text_value(template_name)
        df.at[i, "secure_link"] = f"https://secure-survey.local/{site_key.lower()}"
        df.at[i, "last_updated"] = normalize_text_value(ts)
        append_audit("survey_distributed", "site", site_key, template_name)
    save_csv(normalize_survey_tracking(update_days_open(df)), "survey_tracking.csv")


def persist_reminders(site_ids: list[str]):
    df = normalize_survey_tracking(load_or_init_survey_tracking(SITES["site_id"].astype(str).tolist(), FEAS))
    ts = now_ts()
    for sid in site_ids:
        site_key = normalize_text_value(sid)
        idx = df.index[df["site_id"] == site_key]
        if len(idx) == 0:
            continue
        i = idx[0]
        reminder_value = pd.to_numeric(df.at[i, "reminder_count"], errors="coerce")
        df.at[i, "reminder_count"] = int(0 if _is_missing(reminder_value) else reminder_value) + 1
        df.at[i, "last_updated"] = normalize_text_value(ts)
        append_audit("survey_reminder", "site", site_key, "Reminder sent")
    save_csv(normalize_survey_tracking(update_days_open(df)), "survey_tracking.csv")


def upsert_notification(site_id: str, note_type: str, priority: str, message: str):
    notes = normalize_notifications(load_or_init_notifications())
    existing_ids = pd.to_numeric(notes["notification_id"].str.replace("N", "", regex=False), errors="coerce").dropna()
    next_num = int(existing_ids.max()) + 1 if not existing_ids.empty else 1
    next_id = f"N{next_num:04d}"
    notes.loc[len(notes)] = {
        "notification_id": next_id, "site_id": normalize_text_value(site_id),
        "type": normalize_text_value(note_type), "priority": normalize_text_value(priority),
        "message": normalize_text_value(message), "created_at": normalize_text_value(now_ts()),
        "acknowledged": False,
    }
    save_csv(normalize_notifications(notes), "notifications.csv")


def acknowledge_notification(note_id: str):
    notes = normalize_notifications(load_or_init_notifications())
    idx = notes.index[notes["notification_id"] == normalize_text_value(note_id)]
    if len(idx):
        notes.at[idx[0], "acknowledged"] = True
        save_csv(normalize_notifications(notes), "notifications.csv")


def ranking_explanation(site_row: pd.Series) -> pd.DataFrame:
    mapping = {
        "avg_enroll_rate_per_month_scaled": "Enrollment rate",
        "screen_fail_rate_scaled": "Screen fail rate",
        "protocol_deviation_rate_scaled": "Protocol deviation rate",
        "data_entry_lag_days_scaled": "Data entry lag",
        "retention_rate_scaled": "Retention rate",
        "competing_trials_same_ta_scaled": "Competing trials",
    }
    rows = []
    for col, label in mapping.items():
        value = float(site_row.get(col, 0) or 0)
        weight = float(WEIGHTS.get(col, 0) or 0)
        rows.append({"Factor": label, "Scaled input": round(value, 3), "Weight": weight, "Contribution": round(value * weight, 3)})
    return pd.DataFrame(rows).sort_values("Contribution", ascending=False)


def style_app():
    st.markdown("""
    <style>
    :root {
      --page-bg:#EEF3F8; --sidebar-blue:#2F6DB5; --panel-dark:#163E73;
      --panel-dark-alt:#1F4E8C; --card-white:#FFFFFF; --text-dark:#1F2937;
      --text-muted:#6B7280; --border:#D7E1EC;
    }
    .stApp { background: var(--page-bg); color: var(--text-dark); }
    [data-testid="stSidebar"] {
      background: linear-gradient(180deg, var(--sidebar-blue), #2B63A6 72%, #24558F);
      border-right: 1px solid rgba(255,255,255,.2);
    }
    [data-testid="stSidebar"] * { color:#FFFFFF !important; }
    [data-testid="stSidebar"] .stRadio label { background: transparent !important; }
    .block-container { padding-top: 1rem; padding-bottom: 2rem; max-width: 1280px; }
    .topbar {
      background: var(--sidebar-blue); border-radius: 16px; padding: 14px 18px;
      color: #FFFFFF; margin-bottom: 18px; display: flex;
      justify-content: space-between; align-items: center; gap: 14px;
    }
    .crumb { font-size: 14px; opacity:.95; }
    .search-pill { background: rgba(255,255,255,.94); color: var(--text-muted); border-radius:12px; padding:10px 16px; min-width:260px; text-align:left; }
    .page-title { font-size: 28px; font-weight: 800; color: var(--text-dark); margin-bottom: 2px; }
    .page-sub { color: var(--text-muted); font-size: 15px; margin-bottom: 18px; }
    .metrics { display:grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap:14px; margin-bottom:18px; }
    .metric-card { background: var(--panel-dark-alt); color:#FFFFFF; border-radius:18px; padding:18px 20px; box-shadow: 0 2px 8px rgba(28,54,89,.08); }
    .metric-card * { color:#FFFFFF; }
    .metric-card.light { background: var(--card-white); color: var(--text-dark); border: 1px solid var(--border); }
    .metric-card.light * { color: var(--text-dark); }
    .metric-label { font-size:13px; text-transform:uppercase; letter-spacing:.04em; opacity:.92; }
    .metric-value { font-size:22px; font-weight:800; margin-top:6px; }
    .surface { background: var(--card-white); border:1px solid var(--border); border-radius:20px; padding:18px; margin-bottom:16px; box-shadow:0 1px 3px rgba(16,24,40,.04); color: var(--text-dark); }
    .surface * { color: var(--text-dark); }
    .surface .metric-card, .surface .metric-card * { color:#FFFFFF !important; }
    .surface .metric-card.light, .surface .metric-card.light * { color: var(--text-dark) !important; }
    .surface-dark { background: linear-gradient(160deg, var(--panel-dark), var(--panel-dark-alt)); border-radius:20px; padding:18px; color:#FFFFFF; margin-bottom:16px; }
    .surface-dark * { color:#FFFFFF; }
    .section-head { font-size:16px; font-weight:800; margin-bottom:10px; color: var(--text-dark); }
    .surface-dark .section-head { color:#FFFFFF !important; }
    .site-chip { display:inline-block; padding:4px 10px; border-radius:999px; font-size:12px; font-weight:700; }
    .chip-success { background:#dcfce7; color:#166534; }
    .chip-warning { background:#fef3c7; color:#92400e; }
    .chip-danger { background:#fee2e2; color:#b91c1c; }
    .chip-info { background:#dbeafe; color:#1d4ed8; }
    .small-note, .stCaption, .page-sub, .footer-note, .search-pill { color: var(--text-muted); }
    .footer-note { font-size:12px; line-height:1.45; padding:14px 8px 2px 8px; color:#E8F1FE; }
    div[data-testid="stDataFrame"] div[role="table"], div[data-testid="stTable"] table { border-radius:16px; overflow:hidden; }
    div[data-testid="stDataFrame"] [role="columnheader"], div[data-testid="stTable"] th {
      background: #E7EEF7 !important; color: var(--text-dark) !important; border-bottom: 1px solid var(--border) !important;
    }
    div[data-testid="stDataFrame"] [role="gridcell"], div[data-testid="stTable"] td {
      background: #FFFFFF !important; color: var(--text-dark) !important; border-bottom: 1px solid #ECF1F7 !important;
    }
    .stTextInput label, .stTextArea label, .stSelectbox label, .stRadio label, .stSlider label,
    .stMultiSelect label, .stCheckbox label, .stNumberInput label { color: var(--text-dark) !important; font-weight: 600; }
    .stTextInput input, .stTextArea textarea, .stNumberInput input,
    .stSelectbox [data-baseweb="select"] > div, .stMultiSelect [data-baseweb="select"] > div {
      background: #FFFFFF !important; color: var(--text-dark) !important; border: 1px solid var(--border) !important;
    }
    div[data-testid="stTextInput"] input {
      color: #1F2937 !important; -webkit-text-fill-color: #1F2937 !important;
      caret-color: #1F2937 !important; opacity: 1 !important; background: #FFFFFF !important;
    }
    div[data-testid="stTextInput"] input::placeholder,
    div[data-testid="stTextInput"] input::-webkit-input-placeholder {
      color: #94A3B8 !important; -webkit-text-fill-color: #94A3B8 !important; opacity: 1 !important;
    }
    .stRadio [role="radiogroup"] label, .stRadio [role="radiogroup"] p { color: var(--text-dark) !important; }
    .stButton > button, .stDownloadButton > button {
      background: #EDF3FB; color: var(--text-dark); border: 1px solid #BFD0E5; border-radius: 10px; font-weight: 700;
    }
    .stButton > button:hover, .stDownloadButton > button:hover { border-color: #99B5D4; color: #112236; }
    .stButton > button[kind="primary"] {
      background: var(--panel-dark-alt); color: #FFFFFF; border: 1px solid #10335D; box-shadow: 0 8px 20px rgba(20, 53, 97, .18);
    }
    .stButton > button[kind="primary"]:hover { background: #1B4680; border-color: #0F2E53; }
    .streamlit-expanderHeader, .streamlit-expanderContent, details, details * { color: var(--text-dark) !important; }
    @media (max-width: 980px) { .metrics { grid-template-columns:1fr 1fr; } }
    </style>
    """, unsafe_allow_html=True)


def render_topbar(title: str):
    st.markdown(
        f"<div class='topbar'><div class='crumb'>SmartSite Select &gt; {title}</div>"
        f"<div class='search-pill'>🔎 Search studies, sites, PIs...</div></div>",
        unsafe_allow_html=True,
    )


def metric_cards(items):
    html = "<div class='metrics'>"
    for label, value, tone in items:
        klass = "metric-card light" if tone == "light" else "metric-card"
        html += f"<div class='{klass}'><div class='metric-label'>{label}</div><div class='metric-value'>{value}</div></div>"
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


def apply_global_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    out = df.copy()
    region = normalize_text_value(filters.get("region", "All"))
    country = normalize_text_value(filters.get("country", "All"))
    institution = normalize_text_value(filters.get("institution", "All"))
    interest = normalize_text_value(filters.get("interest", "All"))
    min_score_raw = pd.to_numeric(filters.get("min_ai_rank", 0), errors="coerce")
    min_score = int(0 if _is_missing(min_score_raw) else min_score_raw)
    if region != "All":
        out = out[out["region"] == region]
    if country != "All":
        out = out[out["country"] == country]
    if institution != "All":
        out = out[out["institution_type"] == institution]
    if interest != "All":
        out = out[out["interest_level"].fillna("Unknown") == interest]
    out = out[out["ai_rank_score"] >= min_score]
    return out.reset_index(drop=True)


def apply_site_filtering_local_filters(df: pd.DataFrame, search_term: str, exp_filter: list[str]) -> pd.DataFrame:
    out = df.copy()
    search_text = normalize_text_value(search_term).strip()
    selected_exp = exp_filter if isinstance(exp_filter, list) else []
    if search_text:
        out = out[
            out["site_name"].str.contains(search_text, case=False, na=False)
            | out["matched_pi_name"].str.contains(search_text, case=False, na=False)
            | out["site_id"].str.contains(search_text, case=False, na=False)
        ]
    if selected_exp:
        keep = pd.Series(False, index=out.index)
        if "High (10+ years)" in selected_exp:
            keep |= out["pi_years_experience"].fillna(0) >= 10
        if "Medium (5-10 years)" in selected_exp:
            keep |= out["pi_years_experience"].fillna(0).between(5, 9.999)
        if "Low (<5 years)" in selected_exp:
            keep |= out["pi_years_experience"].fillna(0) < 5
        out = out[keep]
    return out.reset_index(drop=True)


def build_feasibility_trend_data(df: pd.DataFrame) -> dict:
    scoped = df.copy().reset_index(drop=True)
    if scoped.empty:
        return {"mode": "demo", "reason": "No response records in scope.", "data": pd.DataFrame()}

    sent_dt = pd.to_datetime(scoped.get("survey_sent_at"), errors="coerce") if "survey_sent_at" in scoped.columns else pd.Series(pd.NaT, index=scoped.index)
    recv_dt = pd.to_datetime(scoped.get("response_received_at"), errors="coerce") if "response_received_at" in scoped.columns else pd.Series(pd.NaT, index=scoped.index)

    event_rows = []
    for ts in sent_dt.dropna().tolist():
        event_rows.append({"event_date": pd.Timestamp(ts).normalize(), "sent_delta": 1, "received_delta": 0})
    for ts in recv_dt.dropna().tolist():
        event_rows.append({"event_date": pd.Timestamp(ts).normalize(), "sent_delta": 0, "received_delta": 1})

    if not event_rows:
        return {"mode": "demo", "reason": "Demo trend estimate (timestamps incomplete)", "data": pd.DataFrame()}

    events_df = pd.DataFrame(event_rows)
    daily = (
        events_df.groupby("event_date", as_index=False)
        .agg(sent_delta=("sent_delta", "sum"), received_delta=("received_delta", "sum"))
        .sort_values("event_date").reset_index(drop=True)
    )
    if len(daily) < 2:
        return {"mode": "demo", "reason": "Demo trend estimate (timestamps incomplete)", "data": pd.DataFrame()}

    daily["sent"] = daily["sent_delta"].cumsum()
    daily["received"] = daily["received_delta"].cumsum().clip(lower=0)
    daily["received"] = daily[["sent", "received"]].min(axis=1)
    daily["pending"] = (daily["sent"] - daily["received"]).clip(lower=0)
    daily["date_label"] = daily["event_date"].dt.strftime("%Y-%m-%d")
    return {"mode": "real", "reason": "", "data": daily[["event_date", "date_label", "sent", "received", "pending"]]}


def get_feasibility_distribution_page_df(master_df: pd.DataFrame, base_view: pd.DataFrame) -> pd.DataFrame:
    selected_ids = set(master_df.loc[master_df["manual_select"], "site_id"].astype(str).tolist())
    if not selected_ids:
        return base_view.head(12).copy().reset_index(drop=True)
    return base_view[base_view["site_id"].astype(str).isin(selected_ids)].copy().reset_index(drop=True)


def get_feasibility_responses_page_df(base_view: pd.DataFrame) -> pd.DataFrame:
    active_mask = (
        base_view["survey_sent"].fillna(False)
        | base_view["response_received"].fillna(False)
        | (base_view["days_open"].fillna(0) > 0)
    )
    return base_view[active_mask].copy().reset_index(drop=True)


def get_feasibility_analysis_page_df(base_view: pd.DataFrame) -> pd.DataFrame:
    return base_view.copy().reset_index(drop=True)


def get_qualification_page_df(base_view: pd.DataFrame) -> pd.DataFrame:
    return base_view.copy().reset_index(drop=True)


def get_final_selection_page_df(base_view: pd.DataFrame) -> pd.DataFrame:
    return base_view.copy().reset_index(drop=True)


def render_page_filters(master_df: pd.DataFrame, key_prefix: str) -> dict:
    st.markdown("**Study Filters**")
    region = st.selectbox("Region", ["All"] + sorted(master_df["region"].dropna().unique().tolist()), key=f"{key_prefix}_region")
    country = st.selectbox("Country", ["All"] + sorted(master_df["country"].dropna().unique().tolist()), key=f"{key_prefix}_country")
    institution = st.selectbox("Institution", ["All"] + sorted(master_df["institution_type"].dropna().unique().tolist()), key=f"{key_prefix}_institution")
    interest = st.selectbox("Interest", ["All"] + sorted(master_df["interest_level"].dropna().unique().tolist()), key=f"{key_prefix}_interest")
    min_ai_rank = st.slider("Min AI Match", 0, 100, 75, key=f"{key_prefix}_min_ai_rank")
    return {"region": region, "country": country, "institution": institution, "interest": interest, "min_ai_rank": min_ai_rank}


def page_filter_key_prefix(page_name: str) -> str:
    return normalize_text_value(page_name).lower().replace(" ", "_").replace("/", "_")


def get_last_used_filters_for_page(page_name: str, master_df: pd.DataFrame) -> dict:
    prefix = f"filters_{page_filter_key_prefix(page_name)}"
    region_options = ["All"] + sorted(master_df["region"].dropna().unique().tolist())
    country_options = ["All"] + sorted(master_df["country"].dropna().unique().tolist())
    institution_options = ["All"] + sorted(master_df["institution_type"].dropna().unique().tolist())
    interest_options = ["All"] + sorted(master_df["interest_level"].dropna().unique().tolist())

    region = normalize_text_value(st.session_state.get(f"{prefix}_region", "All"))
    country = normalize_text_value(st.session_state.get(f"{prefix}_country", "All"))
    institution = normalize_text_value(st.session_state.get(f"{prefix}_institution", "All"))
    interest = normalize_text_value(st.session_state.get(f"{prefix}_interest", "All"))
    min_ai_rank_raw = pd.to_numeric(st.session_state.get(f"{prefix}_min_ai_rank", 0), errors="coerce")
    min_ai_rank = int(0 if _is_missing(min_ai_rank_raw) else min_ai_rank_raw)

    return {
        "region": region if region in region_options else "All",
        "country": country if country in country_options else "All",
        "institution": institution if institution in institution_options else "All",
        "interest": interest if interest in interest_options else "All",
        "min_ai_rank": max(0, min(100, min_ai_rank)),
    }


def set_flash_message(message: str, level: str = "success") -> None:
    st.session_state["flash_message"] = {"message": message, "level": level}


def render_flash_message() -> None:
    flash = st.session_state.pop("flash_message", None)
    if not isinstance(flash, dict):
        return
    level = normalize_text_value(flash.get("level", "success")).lower()
    message = normalize_text_value(flash.get("message", "")).strip()
    if not message:
        return
    if level == "error":
        st.error(message)
    elif level == "warning":
        st.warning(message)
    elif level == "info":
        st.info(message)
    else:
        st.success(message)


def initialize_auth_state() -> None:
    defaults = {"authenticated": False, "current_user": "", "current_full_name": "", "current_role": ""}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def perform_logout() -> None:
    username = normalize_text_value(st.session_state.get("current_user", ""))
    if username:
        append_audit("logout", "user", username, "User logged out")
    st.session_state["authenticated"] = False
    st.session_state["current_user"] = ""
    st.session_state["current_full_name"] = ""
    st.session_state["current_role"] = ""
    st.session_state["page"] = "Study Setup and Site Filtering"
    reset_chat_history()
    reset_trial_identity_fields_for_new_entry()
    set_flash_message("Logged out successfully.")
    st.rerun()


def render_login_screen() -> None:
    render_topbar("Login")
    render_flash_message()
    st.markdown(
        "<div class='page-title'>SmartSite Select Login</div>"
        "<div class='page-sub'>Authenticate with a local account to access workflow pages and persistence actions.</div>",
        unsafe_allow_html=True,
    )
    left, center, right = st.columns([1.0, 1.2, 1.0])
    with center:
        with st.container(border=True):
            st.markdown("### Sign in")
            username = st.text_input("Username", key="login_username")
            password = st.text_input("Password", type="password", key="login_password")
            if st.button("Login", use_container_width=True, type="primary", key="login_button"):
                user = authenticate_user(username, password)
                if user is None:
                    st.error("Invalid credentials or inactive account.")
                else:
                    st.session_state["authenticated"] = True
                    st.session_state["current_user"] = user["username"]
                    st.session_state["current_full_name"] = user["full_name"]
                    st.session_state["current_role"] = user["role"]
                    reset_chat_history()
                    append_audit("login", "user", user["username"], f"role={user['role']}; full_name={user['full_name']}")
                    reset_trial_identity_fields_for_new_entry()
                    set_flash_message(f"Welcome {user['full_name']}. Login successful.")
                    st.rerun()
            st.caption("Demo users: admin/admin123 (Admin), cra_user/cra123 (CRA)")


def build_chat_context(page_name: str, context_df: pd.DataFrame) -> str:
    trial_context = get_active_trial_context()
    active_view = context_df.copy().reset_index(drop=True)
    top_rows = active_view.head(5)
    top_sites = []
    for row in top_rows.itertuples():
        top_sites.append(f"- {row.site_name} ({row.country_label}) | AI {int(row.ai_rank_score)} | Feasibility {int(row.feasibility_score)} | PI {row.matched_pi_name}")
    if not top_sites:
        top_sites = ["- No sites available in current filtered view"]

    sent = int(active_view["survey_sent"].sum()) if "survey_sent" in active_view.columns else 0
    received = int(active_view["response_received"].sum()) if "response_received" in active_view.columns else 0
    pending = int(((active_view["survey_sent"]) & (~active_view["response_received"])).sum()) if {"survey_sent", "response_received"}.issubset(active_view.columns) else 0
    reminders = int(active_view["reminder_count"].sum()) if "reminder_count" in active_view.columns else 0
    selected = int((active_view["final_status"] == "Selected").sum()) if "final_status" in active_view.columns else 0
    backup = int((active_view["final_status"] == "Backup").sum()) if "final_status" in active_view.columns else 0
    rejected = int((active_view["final_status"] == "Rejected").sum()) if "final_status" in active_view.columns else 0
    high_match = int((active_view["ai_rank_score"] >= 85).sum()) if "ai_rank_score" in active_view.columns else 0
    high_risk = int((active_view["risk_level"] == "High").sum()) if "risk_level" in active_view.columns else 0
    avg_qualification = float(active_view["qualification_score"].mean()) if "qualification_score" in active_view.columns and not active_view.empty else 0.0

    return "\n".join([
        f"Current workflow page: {page_name}",
        f"Trial context: TA={trial_context['therapeutic_area']}; Indication={trial_context['indication']}; Phase={trial_context['phase']}; Enrollment target={trial_context['total_target_enrollment']}",
        "Top ranked sites:", *top_sites,
        f"Feasibility: sent={sent}, received={received}, pending={pending}, reminders={reminders}",
        f"Final decisions: selected={selected}, backup={backup}, rejected={rejected}",
        f"Qualification metrics: avg_qualification={avg_qualification:.1f}, high_risk={high_risk}, high_match={high_match}",
    ])


def query_local_llm(prompt: str, context: str) -> str:
    system_instruction = (
        "You are the SmartSite Select assistant. Use only the supplied app context. "
        "If information is unavailable, say so briefly. Answer in under 120 words unless the user asks for detail."
    )
    payload = {
        "model": "qwen2.5:7b", "stream": False,
        "prompt": f"System instruction:\n{system_instruction}\n\nApp context:\n{context}\n\nUser question:\n{prompt}\n\nAssistant answer:",
    }
    response = requests.post("http://localhost:11434/api/generate", json=payload, timeout=25)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("Invalid Ollama response payload")
    answer = normalize_text_value(body.get("response", "")).strip()
    if not answer:
        raise ValueError("Empty Ollama response")
    return answer


def chatbot_answer_fallback(query: str, context_df: pd.DataFrame) -> str:
    q = query.lower()
    scoped_df = context_df.copy().reset_index(drop=True)
    if scoped_df.empty:
        return "No sites available in the current context. Adjust filters or navigate to a workflow page with data."
    if any(k in q for k in ["top", "best", "recommend"]):
        top = scoped_df.iloc[0]
        return f"Top recommended site is {top['site_name']} in {top['country_label']} with AI match {int(top['ai_rank_score'])}% and feasibility {int(top['feasibility_score'])}/100."
    if "europe" in q:
        europe = scoped_df[scoped_df["region"].str.contains("Europe", case=False, na=False)].head(5)
        if europe.empty:
            return "No Europe sites are currently in the filtered view."
        lines = [f"{i+1}. {r.site_name} ({r.country_label}) – {int(r.ai_rank_score)}%" for i, r in enumerate(europe.itertuples())]
        return "Top European candidates right now:\n\n" + "\n".join(lines)
    if "qualification" in q or "cda" in q:
        sel = int((scoped_df["final_status"] == "Selected").sum())
        return f"There are {sel} currently selected sites. CDA status, CRA flags, preferred flags, and comments persist to CSV sidecar files."
    if "feasibility" in q:
        sent = int(scoped_df["survey_sent"].sum())
        recv = int(scoped_df["response_received"].sum())
        breaches = int(((scoped_df["survey_sent"]) & (~scoped_df["response_received"]) & (scoped_df["days_open"] > 7)).sum())
        return f"Feasibility dashboard shows {recv} received responses out of {sent} sent surveys, with {breaches} SLA breaches."
    return "I can answer questions about site ranking, feasibility, qualification, final selection, and CSV persistence."


def chatbot_answer(query: str, page_name: str, context_df: pd.DataFrame) -> dict:
    context = build_chat_context(page_name, context_df)
    try:
        answer = query_local_llm(query, context)
        return {"response": answer, "used_local_llm": True, "fallback_used": False, "success": True, "error_message": ""}
    except Exception as exc:
        return {
            "response": chatbot_answer_fallback(query, context_df),
            "used_local_llm": False, "fallback_used": True, "success": False,
            "error_message": truncate_for_storage(str(exc), 400),
        }


def render_chatbot_panel():
    if not st.session_state.get("chatbot_open", False):
        return

    st.markdown("---")
    st.markdown("<div class='page-title' style='font-size:20px'>🤖 AI Assistant</div>", unsafe_allow_html=True)
    st.caption("Ask about site feasibility, qualification status, or top sites in a region.")

    chat_context_df = MASTER.copy().reset_index(drop=True)

    if "chat_history" not in st.session_state:
        reset_chat_history()

    for msg in st.session_state.chat_history[-6:]:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    prompt = st.chat_input("Ask SmartSite Select assistant...", key="floating_chat_input")
    if prompt:
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        answer_payload = chatbot_answer(prompt, "Assistant Panel", chat_context_df)
        answer_text = normalize_text_value(answer_payload.get("response", "")).strip() or chatbot_answer_fallback(prompt, chat_context_df)
        st.session_state.chat_history.append({"role": "assistant", "content": answer_text})
        append_chat_usage(
            username=normalize_text_value(st.session_state.get("current_user", "")),
            full_name=normalize_text_value(st.session_state.get("current_full_name", "")),
            role=normalize_text_value(st.session_state.get("current_role", "")),
            page_name="Floating Assistant",
            prompt=prompt, response=answer_text,
            used_local_llm=bool(answer_payload.get("used_local_llm", False)),
            success=bool(answer_payload.get("success", False)),
            error_message=normalize_text_value(answer_payload.get("error_message", "")),
        )
        st.rerun()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Clear Chat", use_container_width=True, key="clear_chat_btn"):
            reset_chat_history()
            st.rerun()
    with col2:
        if st.button("Close ✕", use_container_width=True, key="close_chat_btn"):
            st.session_state["chatbot_open"] = False
            st.rerun()


# ═══════════════════════════════════════════════════════════════════════════════
# BOOT
# ═══════════════════════════════════════════════════════════════════════════════
style_app()
initialize_auth_state()

if not bool(st.session_state.get("authenticated", False)):
    with st.sidebar:
        st.markdown("## SmartSite Select")
        st.caption("AI-driven smart site selection")
        st.markdown("---")
        st.caption("Sign in to access workflow pages and persisted actions.")
    render_login_screen()
    st.stop()

initialize_trial_context_state()
ACTIVE_TRIAL_CONTEXT = get_active_trial_context()
MASTER = build_master(
    SITES, PIS, PERF, FEAS, REC, ACTIONS, TRACK,
    ACTIVE_TRIAL_CONTEXT["therapeutic_area"],
    ACTIVE_TRIAL_CONTEXT["indication"],
    ACTIVE_TRIAL_CONTEXT["phase"],
)

workflow_labels = {
    "Study Setup and Site Filtering": "Study Setup & Site Filtering",
    "Feasibility Distribution and Responses": "Feasibility Distribution & Responses",
    "Feasibility Analysis and Qualification": "Feasibility Analysis & Qualification",
    "Final Selection": "Final Selection",
}

filter_enabled_pages = {
    "Feasibility Distribution and Responses",
    "Feasibility Analysis and Qualification",
    "Final Selection",
}

workflow_pages = list(workflow_labels.keys())
if "page" not in st.session_state or st.session_state["page"] not in workflow_labels:
    st.session_state["page"] = workflow_pages[0]

active_trial_context = get_active_trial_context()

filters = {"region": "All", "country": "All", "institution": "All", "interest": "All", "min_ai_rank": 0}
with st.sidebar:
    st.markdown("## SmartSite Select")
    st.caption("AI-driven smart site selection")
    st.caption(f"Signed in as {st.session_state['current_full_name']} ({st.session_state['current_role']})")
    if st.button("Logout", use_container_width=True, key="logout_button"):
        perform_logout()
    st.markdown("---")
    page = st.radio(
        "Workflow", workflow_pages,
        index=workflow_pages.index(st.session_state["page"]),
        label_visibility="visible",
    )
    st.session_state["page"] = page
    st.markdown("---")
    if page in filter_enabled_pages:
        page_key = page_filter_key_prefix(page)
        filters = render_page_filters(MASTER, key_prefix=f"filters_{page_key}")
    else:
        st.caption("Study filters are hidden on this page.")
    st.markdown("---")
    # ── REMOVED: Therapeutic Area, Indication, Phase, Dataset Version block ──
    # AI assistant toggle
    if st.button("🤖 AI Assistant", use_container_width=True, key="sidebar_chatbot_btn"):
        st.session_state["chatbot_open"] = not st.session_state.get("chatbot_open", False)
    st.markdown("<div class='footer-note'>AI Status<br>Models are up to date. Last sync uses the local data folder and persisted app actions.</div>", unsafe_allow_html=True)

if page in filter_enabled_pages:
    base_view = apply_global_filters(MASTER, filters)
else:
    base_view = MASTER.copy().reset_index(drop=True)

render_topbar(workflow_labels[page])
render_flash_message()

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 1 — Study Setup AND Site Filtering
# ═══════════════════════════════════════════════════════════════════════════════
if page == "Study Setup and Site Filtering":
    st.markdown(
        "<div class='page-title'>Study Setup & Site Filtering</div>"
        "<div class='page-sub'>Configure clinical trial parameters, upload a digitalized protocol copy, and identify candidate investigator sites from historical performance and AI ranking.</div>",
        unsafe_allow_html=True,
    )

    ta_options = get_trial_ta_options()
    if not ta_options:
        ta_options = [active_trial_context["therapeutic_area"]]
    ta_key = TRIAL_CONTEXT_WIDGET_KEYS["therapeutic_area"]
    if normalize_text_value(st.session_state.get(ta_key, "")) not in ta_options:
        st.session_state[ta_key] = active_trial_context["therapeutic_area"] if active_trial_context["therapeutic_area"] in ta_options else ta_options[0]

    indication_options = get_trial_indication_options(st.session_state[ta_key])
    if not indication_options:
        indication_options = [active_trial_context["indication"]]
    indication_key = TRIAL_CONTEXT_WIDGET_KEYS["indication"]
    if normalize_text_value(st.session_state.get(indication_key, "")) not in indication_options:
        st.session_state[indication_key] = active_trial_context["indication"] if active_trial_context["indication"] in indication_options else indication_options[0]

    geo_options = sorted(SITES["region"].dropna().astype(str).str.strip().unique().tolist())
    geos_key = TRIAL_CONTEXT_WIDGET_KEYS["target_geographies"]
    geos_state = st.session_state.get(geos_key)
    if isinstance(geos_state, list):
        st.session_state[geos_key] = [g for g in geos_state if g in geo_options]
    else:
        st.session_state[geos_key] = [g for g in active_trial_context["target_geographies"] if g in geo_options]

    # ── Section A: Protocol Definition ──────────────────────────────────────
    with st.container(border=True):
        st.markdown("<div class='section-head'>Protocol Definition</div>", unsafe_allow_html=True)

        _, upload_col = st.columns([2, 1])
        with upload_col:
            st.markdown(
                """<style>
                div[data-testid="stFileUploader"]{border:2px solid #2563EB !important;border-radius:8px !important;background:#EFF6FF !important;padding:4px 8px !important;}
                div[data-testid="stFileUploader"] button{background:#2563EB !important;color:#fff !important;border-radius:6px !important;font-size:11px !important;padding:2px 10px !important;}
                div[data-testid="stFileUploader"] label,div[data-testid="stFileUploader"] span,div[data-testid="stFileUploader"] p{font-size:11px !important;color:#2563EB !important;}
                </style>""",
                unsafe_allow_html=True
            )
            pdf_file = st.file_uploader("Upload Protocol (PDF)", type=["pdf"])
            data = None
            if pdf_file is not None:
                pdf_file.seek(0)
                data = extract_protocol_data(pdf_file)
                if data:
                    st.success("Protocol auto-filled from PDF")
                    if data.get("study_title"):
                        st.session_state[TRIAL_CONTEXT_WIDGET_KEYS["study_title"]] = data["study_title"]
                    if data.get("protocol_id"):
                        st.session_state[TRIAL_CONTEXT_WIDGET_KEYS["protocol_id"]] = data["protocol_id"]
                    if data.get("therapeutic_area"):
                        st.session_state[TRIAL_CONTEXT_WIDGET_KEYS["therapeutic_area"]] = data["therapeutic_area"]
                    if data.get("indication"):
                        st.session_state[TRIAL_CONTEXT_WIDGET_KEYS["indication"]] = data["indication"]

        c1, c2 = st.columns(2)
        c1.text_input("Study Title", key=TRIAL_CONTEXT_WIDGET_KEYS["study_title"], placeholder="e.g. Phase III Evaluation of NSCLC in Oncology")
        c2.text_input("Protocol ID", key=TRIAL_CONTEXT_WIDGET_KEYS["protocol_id"], placeholder="e.g. ST-III-ONC-03")

        st.divider()
        st.markdown("<div class='section-head'>Clinical Parameters</div>", unsafe_allow_html=True)
        c3, c4 = st.columns(2)
        c3.selectbox("Therapeutic Area", ta_options, key=ta_key)
        c4.selectbox("Indication", indication_options, key=indication_key)
        st.radio("Study Phase", TRIAL_PHASE_OPTIONS, horizontal=True, key=TRIAL_CONTEXT_WIDGET_KEYS["phase"])

        st.divider()
        st.markdown("<div class='section-head'>Population & Geography</div>", unsafe_allow_html=True)
        c5, c6, c7, c8 = st.columns([1.25, 1.0, 1.0, 0.95])
        c5.number_input("Total Target Enrollment", min_value=1, step=10, key=TRIAL_CONTEXT_WIDGET_KEYS["total_target_enrollment"])
        c6.number_input("Min Age (in years)", min_value=0, step=1, key=TRIAL_CONTEXT_WIDGET_KEYS["min_age"])
        c7.number_input("Max Age (in years)", min_value=0, step=1, key=TRIAL_CONTEXT_WIDGET_KEYS["max_age"])
        c8.selectbox("Gender", ["All", "Male", "Female"], key=TRIAL_CONTEXT_WIDGET_KEYS["gender"])
        st.multiselect("Target Geographies", geo_options, key=geos_key)

        with st.expander("Advanced Feasibility Parameters", expanded=False):
            a1, a2 = st.columns(2)
            a1.checkbox("Require Biomarker Testing", key=TRIAL_CONTEXT_WIDGET_KEYS["require_biomarker_testing"])
            a1.caption("Prioritize sites with in-house genomic sequencing capabilities.")
            a1.checkbox("Rare Disease Protocol", key=TRIAL_CONTEXT_WIDGET_KEYS["rare_disease_protocol"])
            a1.caption("Adjusts AI modeling for hyper-specific patient populations.")
            a2.selectbox("Competitive Trial Density Tolerance", ["Low", "Medium (Standard)", "High"], key=TRIAL_CONTEXT_WIDGET_KEYS["competitive_trial_density_tolerance"])
            a2.selectbox("IRB Preference", ["Either", "Central Preferred", "Local Accepted"], key=TRIAL_CONTEXT_WIDGET_KEYS["irb_preference"])

        if st.button("Generate AI Recommendations ⚡", use_container_width=True, type="primary"):
            captured_context = get_trial_context_from_setup_widgets()
            captured_context["generated_at"] = now_ts()
            st.session_state["trial_context"] = normalize_trial_context(captured_context)
            history = st.session_state.get("trial_context_history", [])
            if not isinstance(history, list):
                history = []
            history.append({"timestamp": captured_context["generated_at"], "context": captured_context.copy()})
            st.session_state["trial_context_history"] = history[-25:]
            append_audit(
                "study_setup_generate", "protocol", captured_context["protocol_id"],
                f"TA={captured_context['therapeutic_area']}; indication={captured_context['indication']}; phase={captured_context['phase']}; "
                f"enrollment={captured_context['total_target_enrollment']}; age={captured_context['min_age']}-{captured_context['max_age']}; "
                f"gender={captured_context['gender']}; geographies={','.join(captured_context['target_geographies']) or 'None'}; "
                f"biomarker={captured_context['require_biomarker_testing']}; rare_protocol={captured_context['rare_disease_protocol']}; "
                f"density_tolerance={captured_context['competitive_trial_density_tolerance']}; irb_pref={captured_context['irb_preference']}",
            )
            set_flash_message("AI Recommendations generated. Study setup parameters captured and model context refreshed.")
            clear_and_rerun()

    st.divider()

    # ── Section B: Feasibility Questionnaire Checklist ──────────────────────
    with st.container(border=True):
        st.markdown("<div class='section-head'>Feasibility Questionnaire Checklist Template</div>", unsafe_allow_html=True)
        st.caption(
            "Review and customise the checklist items to be included in feasibility surveys sent to sites. "
            "Tick items to include in the auto-generated survey template for this protocol."
        )

        checklist_categories = {
            "Operational Parameters": [
                ("enroll_rate", "Enrollment Rate (avg patients/month for this TA/Indication)"),
                ("site_activation", "Site Activation Timeline (days from contract to FPI)"),
                ("dropout_rate", "Dropout / Early Termination Rate (%)"),
                ("screen_fail", "Screen Failure Rate (%)"),
                ("protocol_deviation", "Protocol Deviation Rate (%)"),
                ("data_entry_lag", "Data Entry Lag (days from visit to EDC entry)"),
            ],
            "Clinical & Feasibility Parameters": [
                ("investigator_qual", "Investigator Qualifications & Experience in Indication"),
                ("patient_pool", "Patient Pool Estimate (eligible patients per year)"),
                ("competing_trials", "Competing Trials in Same TA at Site"),
                ("site_facility", "Site Facility Assessment (space, staff, equipment)"),
                ("data_quality", "Data Quality Score (historical EDC query rate)"),
                ("biomarker_cap", "Biomarker / Genomic Testing Capability (in-house)"),
            ],
            "Quality & Compliance Parameters": [
                ("audit_findings", "Audit Findings (last 3 years — number of critical/major)"),
                ("sae_reporting", "SAE Reporting Timeliness (% on-time in last trial)"),
                ("monitoring_score", "Monitoring Visit Compliance Score"),
                ("gcp_training", "GCP Training Currency (date of last certification)"),
                ("irb_approval", "IRB / Ethics Committee Approval Timeline (avg days)"),
            ],
            "Infrastructure & Resource Parameters": [
                ("tech_readiness", "Technology Readiness (EDC, ePRO, eConsent access)"),
                ("satellite_facility", "Satellite Facility Availability"),
                ("ip_storage", "Investigational Product (IP) Storage Capability"),
                ("qualified_staff", "Qualified Staff Count (dedicated study coordinators)"),
                ("lab_capability", "Central / Local Lab Capability & Accreditation"),
            ],
            "Insurance / Reimbursement & Regulatory": [
                ("country_reg_timeline", "Country Regulatory Submission Timeline (avg days)"),
                ("contract_policy", "Contract & Budget Negotiation Flexibility"),
                ("insurance_coverage", "Site Insurance / Indemnity Coverage Confirmed"),
                ("central_irb", "Central IRB Acceptance (site willing to use central IRB)"),
                ("patient_reimbursement", "Patient Reimbursement / Travel Support Available"),
            ],
        }

        if "fq_checklist" not in st.session_state:
            st.session_state["fq_checklist"] = {
                item_key: True
                for items in checklist_categories.values()
                for item_key, _ in items
            }

        tabs = st.tabs(list(checklist_categories.keys()))
        for tab, (category, items) in zip(tabs, checklist_categories.items()):
            with tab:
                for item_key, label in items:
                    current_val = st.session_state["fq_checklist"].get(item_key, True)
                    new_val = st.checkbox(label, value=current_val, key=f"fq_{item_key}")
                    st.session_state["fq_checklist"][item_key] = new_val

        col_dl, col_rst = st.columns([1, 1])
        with col_dl:
            selected_items = [
                (cat, label)
                for cat, items in checklist_categories.items()
                for item_key, label in items
                if st.session_state["fq_checklist"].get(item_key, True)
            ]
            ta_label = active_trial_context["therapeutic_area"].replace(" ", "_")
            ind_label = active_trial_context["indication"].replace(" ", "_")
            lines = [
                f"Feasibility Questionnaire Checklist Template",
                f"Therapeutic Area: {active_trial_context['therapeutic_area']}",
                f"Indication: {active_trial_context['indication']}",
                f"Phase: {active_trial_context['phase']}",
                f"Generated: {now_ts()}",
                "",
            ]
            current_cat = None
            for cat, label in selected_items:
                if cat != current_cat:
                    lines.append(f"\n## {cat}")
                    current_cat = cat
                lines.append(f"  [ ] {label}")
            checklist_text = "\n".join(lines)
            st.download_button(
                "⬇ Download Checklist Template (.txt)",
                data=checklist_text,
                file_name=f"feasibility_checklist_{ta_label}_{ind_label}.txt",
                mime="text/plain",
                use_container_width=True,
            )
        with col_rst:
            if st.button("Reset All to Default (All Checked)", use_container_width=True, key="fq_reset_btn"):
                for cat_items in checklist_categories.values():
                    for item_key, _ in cat_items:
                        st.session_state["fq_checklist"][item_key] = True
                st.rerun()

        selected_count = sum(1 for v in st.session_state["fq_checklist"].values() if v)
        total_count = sum(len(items) for items in checklist_categories.values())
        st.caption(f"{selected_count} of {total_count} checklist items selected for the feasibility survey template.")

    st.divider()

    # ── Section C: Site Filtering & Ranking ─────────────────────────────────
    st.markdown(
        "<div class='page-title' style='font-size:22px'>Site Filtering & Ranking</div>"
        "<div class='page-sub'>CRA-identified potential investigator sites from HSP, CTMS, EDC and Citeline databases, ranked by AI match score. Set cut-off value to filter candidates.</div>",
        unsafe_allow_html=True,
    )

    metric_cards([
        ("Total Sites Analyzed", f"{len(MASTER):,}", "dark"),
        ("High Match Candidates", int((MASTER["ai_rank_score"] >= 85).sum()), "light"),
        ("Avg. AI Match Score", f"{int(MASTER['ai_rank_score'].mean()) if not MASTER.empty else 0}%", "dark"),
        ("Est. Enrollment Reach", int(MASTER["projected_enroll_rate_per_month"].fillna(0).head(25).sum()), "dark"),
    ])

    # ── REMOVED: Global Distribution map section ──
    # Sites table + filters side by side
    left, right = st.columns([0.9, 1.25])
    with left:
        with st.container(border=True):
            st.markdown("<div class='section-head'>Filters</div>", unsafe_allow_html=True)
            ai_cutoff = st.slider("AI Match Score Cut-off", 0, 100, 75, key="site_filter_ai_cutoff")
            search_term = st.text_input("Search sites", placeholder="Site name, PI, or ID", key="global_search")
            exp_filter = st.multiselect("PI Experience", ["High (10+ years)", "Medium (5-10 years)", "Low (<5 years)"], key="global_exp_filter")
            filtered_master = MASTER[MASTER["ai_rank_score"] >= ai_cutoff].copy().reset_index(drop=True)
            filtered_master = apply_site_filtering_local_filters(filtered_master, search_term, exp_filter)
            st.caption(f"Showing {len(filtered_master)} sites above AI cut-off of {ai_cutoff}.")

    with right:
        display = filtered_master[["site_id", "site_name", "country_label", "matched_pi_name", "pi_years_experience", "ai_rank_score", "risk_level", "manual_select"]].copy()
        display.columns = ["Site ID", "Site Details", "Location", "PI Info", "PI Experience", "AI Match Score", "Risk", "Select"]
        edited = st.data_editor(
            display, use_container_width=True, hide_index=True,
            disabled=[c for c in display.columns if c != "Select"],
            column_config={
                "Select": st.column_config.CheckboxColumn("Select"),
                "AI Match Score": st.column_config.ProgressColumn("AI Match Score", min_value=0, max_value=100, format="%d%%"),
            },
            key="site_filter_editor",
        )
        if st.button("Proceed to Feasibility →", use_container_width=True, type="primary"):
            selected_ids = edited.loc[edited["Select"], "Site ID"].tolist()
            updates_by_site = {normalize_text_value(sid): {"manual_select": False} for sid in MASTER["site_id"].astype(str).tolist()}
            for sid in selected_ids:
                updates_by_site[normalize_text_value(sid)] = {"manual_select": True}
            persist_site_actions_by_row(updates_by_site)
            for sid in selected_ids:
                append_audit("manual_select", "site", sid, "Selected from Site Filtering Dashboard")
            set_flash_message(f"Saved {len(selected_ids)} selected candidate sites. Proceeding to Feasibility.")
            st.session_state["page"] = "Feasibility Distribution and Responses"
            clear_and_rerun()

        if not MASTER.empty:
            st.markdown("<div class='surface' style='margin-top:14px'><div class='section-head'>Explainable AI — Top Site</div>", unsafe_allow_html=True)
            st.dataframe(ranking_explanation(MASTER.iloc[0]), hide_index=True, use_container_width=True)
            st.markdown("</div>", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 2 — Feasibility Distribution AND Responses
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Feasibility Distribution and Responses":
    st.markdown(
        "<div class='page-title'>Feasibility Distribution & Responses</div>"
        "<div class='page-sub'>Distribute feasibility surveys to AI-ranked sites and monitor response rates, SLA status, and bottlenecks.</div>",
        unsafe_allow_html=True,
    )

    distribution_df = get_feasibility_distribution_page_df(MASTER, base_view)
    responses_df = get_feasibility_responses_page_df(base_view)

    sent = int(responses_df["survey_sent"].sum()) if not responses_df.empty else 0
    recv = int(responses_df["response_received"].sum()) if not responses_df.empty else 0
    pending_count = int(((responses_df["survey_sent"]) & (~responses_df["response_received"])).sum()) if not responses_df.empty else 0
    breaches = int(((responses_df["survey_sent"]) & (~responses_df["response_received"]) & (responses_df["days_open"] > 7)).sum()) if not responses_df.empty else 0

    metric_cards([
        ("Total Selected", len(distribution_df), "dark"),
        ("Surveys Sent", sent, "dark"),
        ("Response Rate", f"{round((recv/sent)*100) if sent else 0}%", "dark"),
        ("SLA Breaches", breaches, "light"),
    ])

    # ── Distribution controls ──────────────────────────────────────────────
    st.markdown("### Distribution")
    left, right = st.columns([0.65, 1.45])
    with left:
        with st.container(border=True):
            st.markdown("<div class='section-head'>Auto-Select Rules</div>", unsafe_allow_html=True)
            threshold = st.slider("Min. AI Match Score", 50, 100, 85)
            min_trials = st.slider("PI Experience (years)", 0, 20, 5)
            auto_include_top_10 = st.checkbox("Auto-include Top 10%", value=True)
            template = st.text_input(
                "Survey Template",
                value="Diabetes Mellitus Feasibility",
            )
            if st.button("Apply Rules", use_container_width=True):
                base = base_view[(base_view["ai_rank_score"] >= threshold) & (base_view["pi_years_experience"].fillna(0) >= min_trials)].copy()
                if auto_include_top_10:
                    top_decile = base_view.head(max(1, int(len(base_view) * 0.10))).copy()
                    base = pd.concat([top_decile, base], ignore_index=True).drop_duplicates(subset=["site_id"], keep="first")
                updates_by_site = {normalize_text_value(sid): {"manual_select": False} for sid in MASTER["site_id"].astype(str).tolist()}
                for sid in base["site_id"].astype(str).tolist():
                    updates_by_site[normalize_text_value(sid)] = {"manual_select": True}
                persist_site_actions_by_row(updates_by_site)
                set_flash_message(f"Rules applied. Updated manual selections for {len(base)} sites.")
                clear_and_rerun()

            chosen = st.multiselect(
                "Distribution list",
                options=distribution_df["site_name"].tolist(),
                default=distribution_df["site_name"].head(8).tolist(),
            )
            if st.button("Send Feasibility Surveys", use_container_width=True):
                ids = distribution_df[distribution_df["site_name"].isin(chosen)]["site_id"].tolist()
                persist_distribution(ids, template)
                for sid in ids:
                    upsert_notification(sid, "Feasibility Survey Submitted", "Medium", f"Survey distributed using template {template}")
                set_flash_message(f"Distribution persisted for {len(ids)} sites.")
                clear_and_rerun()

            pending_ids = distribution_df[(distribution_df["survey_sent"]) & (~distribution_df["response_received"])]["site_id"].tolist()
            if st.button("Send Reminders", use_container_width=True):
                persist_reminders(pending_ids)
                for sid in pending_ids:
                    upsert_notification(sid, "SLA Breach Warning", "High", "Reminder triggered for pending feasibility survey")
                set_flash_message(f"Reminder counts updated for {len(pending_ids)} sites.")
                clear_and_rerun()

    with right:
        st.markdown("<div class='surface-dark'><div class='section-head' style='color:#fff'>Feasibility Distribution Status</div>", unsafe_allow_html=True)
        dist = distribution_df[["site_name", "country_label", "matched_pi_name", "ai_rank_score", "survey_sent", "response_received", "reminder_count"]].copy()
        dist["Survey Status"] = dist.apply(lambda r: "Delivered" if r["survey_sent"] or r["response_received"] else "Pending", axis=1)
        dist["AI Match"] = dist["ai_rank_score"].astype(int)
        dist = dist[["site_name", "country_label", "matched_pi_name", "Survey Status", "reminder_count"]]
        dist.columns = ["Site Details", "Site Location", "PI Details", "Survey Status", "Reminders"]
        st.dataframe(dist, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)

    st.divider()

    # ── REMOVED: Response Tracking section (pie chart + bar chart + site tracking table) ──
    # Site Response Tracking table only
    st.markdown("### Site Response Tracking")
    st.markdown("<div class='surface-dark'><div class='section-head' style='color:#fff'>Site Response Tracking</div>", unsafe_allow_html=True)
    if not responses_df.empty:
        tracking = responses_df[["site_name", "country_label", "matched_pi_name", "response_received", "feasibility_score", "days_open", "reminder_count"]].copy()
        tracking["Survey Status"] = tracking["response_received"].map({True: "Received", False: "Pending"})
        tracking.loc[(tracking["Survey Status"] == "Pending") & (tracking["days_open"] > 7), "Survey Status"] = "Overdue"
        tracking["Last Contact"] = tracking["days_open"].map(lambda d: f"{int(d)} days ago" if d else "Today")
        tracking = tracking[["site_name", "country_label", "matched_pi_name", "Survey Status", "feasibility_score", "Last Contact", "reminder_count"]]
        tracking.columns = ["Site Details", "Site Location", "PI Details", "Survey Status", "Feasibility Score", "Last Contact", "Reminders"]
        st.dataframe(tracking.head(25), use_container_width=True, hide_index=True)
    else:
        st.info("No active response records. Send surveys to populate this table.")
    st.markdown("</div>", unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 3 — Feasibility Analysis AND Qualification
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Feasibility Analysis and Qualification":
    st.markdown(
        "<div class='page-title'>Feasibility Analysis & Qualification</div>"
        "<div class='page-sub'>Drill into site-level feasibility detail with AI explainability, then review CDA state, CRA flags, and finalize qualification decisions.</div>",
        unsafe_allow_html=True,
    )
    st.caption("This page reflects current sidebar Study Filters.")

    analysis_df = get_feasibility_analysis_page_df(base_view)

    if analysis_df.empty:
        st.info("No sites available in the current filtered cohort.")
    else:
        # ── Feasibility Analysis ─────────────────────────────────────────────
        st.markdown("### Site Feasibility Analysis")
        site_options = analysis_df[["site_id", "site_name", "country_label"]].drop_duplicates(subset=["site_id"]).copy()
        site_labels = {r.site_id: f"{r.site_name} ({r.country_label})" for r in site_options.itertuples(index=False)}
        selected_site_id = st.selectbox(
            "Choose Site for Analysis",
            site_options["site_id"].tolist(),
            format_func=lambda sid: site_labels.get(sid, sid),
        )
        row = analysis_df[analysis_df["site_id"] == selected_site_id].iloc[0]
        st.markdown(
            f"<div class='surface-dark'><div style='display:flex;justify-content:space-between;align-items:center'>"
            f"<div><div style='font-size:22px;font-weight:800'>{row['site_name']}</div>"
            f"<div>{row['city']}, {row['country_label']}  •  PI: {row['matched_pi_name']}  •  Status: Feasibility {('Completed' if row['response_received'] else 'Pending')}</div>"
            f"</div><div style='font-size:46px;font-weight:800'>{int(row['ai_rank_score'])}<span style='font-size:18px'>/100</span></div></div></div>",
            unsafe_allow_html=True,
        )

        # ── REMOVED: Site Selection Parameters radar chart + Key Metrics panel ──
        # Replaced with a clean metrics row + table for the same data
        st.markdown("<div class='section-head' style='margin-top:12px'>Key Metrics</div>", unsafe_allow_html=True)
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("AI Match Score", f"{int(row.get('ai_rank_score', 0))}/100")
        m2.metric("Feasibility Score", f"{int(row.get('feasibility_score', 0))}/100")
        m3.metric("Qualification Score", f"{int(row.get('qualification_score', 0))}/100")
        m4.metric("Risk Level", row.get("risk_level", "Unknown"))
        m5.metric("PI Experience", f"{int(row.get('pi_years_experience', 0) or 0)} yrs")
        m6.metric("Enrollment Rate", f"{round(row.get('avg_enroll_rate_per_month', 0) or 0, 1)}/mo")

        st.divider()

        # ── Qualification Section ────────────────────────────────────────────
        st.markdown("### Qualification & CDA Review")
        qual_df = get_qualification_page_df(base_view)

        q_col1, q_col2 = st.columns([1.5, 1.0])
        with q_col1:
            with st.container(border=True):
                st.markdown("<div class='section-head'>Qualification Dashboard</div>", unsafe_allow_html=True)
                qual_display = qual_df[[
                    "site_id", "site_name", "country_label", "matched_pi_name",
                    "ai_rank_score", "qualification_score", "risk_level",
                    "cda_status", "cra_flag", "preferred"
                ]].copy()
                qual_display.columns = [
                    "Site ID", "Site", "Location", "PI",
                    "AI Score", "Qual Score", "Risk",
                    "CDA Status", "CRA Flag", "Preferred"
                ]
                st.dataframe(qual_display.head(30), use_container_width=True, hide_index=True)

        with q_col2:
            with st.container(border=True):
                st.markdown("<div class='section-head'>Bulk Actions</div>", unsafe_allow_html=True)
                bulk_sites = st.multiselect(
                    "Select sites for bulk action",
                    options=qual_df["site_id"].tolist(),
                    format_func=lambda sid: f"{sid} — {qual_df[qual_df['site_id']==sid]['site_name'].values[0] if not qual_df[qual_df['site_id']==sid].empty else sid}",
                    key="qual_bulk_select",
                )
                bulk_status = st.selectbox("Set Final Status", ["", "Selected", "Backup", "Rejected"], key="qual_bulk_status")
                bulk_cda = st.selectbox("Set CDA Status", ["", "Pending", "In Review", "Executed"], key="qual_bulk_cda")
                bulk_preferred = st.checkbox("Mark as Preferred", key="qual_bulk_preferred")
                if st.button("Apply Bulk Actions", use_container_width=True, type="primary", key="qual_bulk_apply"):
                    if bulk_sites:
                        updates: dict = {}
                        if bulk_status:
                            updates["final_status_override"] = bulk_status
                        if bulk_cda:
                            updates["cda_status_override"] = bulk_cda
                        if bulk_preferred:
                            updates["preferred"] = True
                        if updates:
                            persist_bulk_site_action(bulk_sites, **updates)
                            for sid in bulk_sites:
                                append_audit("bulk_qualify", "site", sid, str(updates))
                            set_flash_message(f"Bulk actions applied to {len(bulk_sites)} sites.")
                            clear_and_rerun()

            with st.container(border=True):
                st.markdown("<div class='section-head'>CRA Flag Summary</div>", unsafe_allow_html=True)
                if not qual_df.empty and "cra_flag" in qual_df.columns:
                    flag_counts = qual_df["cra_flag"].value_counts().reset_index()
                    flag_counts.columns = ["CRA Flag", "Count"]
                    fig_flags = px.bar(flag_counts, x="CRA Flag", y="Count", color="CRA Flag",
                                       color_discrete_map={"Risk": "#ef4444", "Feasibility Delay": "#f59e0b",
                                                           "IRB Review": "#3b82f6", "None": "#22c55e"})
                    fig_flags.update_layout(height=220, showlegend=False, margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig_flags, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# PAGE 4 — Final Selection
# ═══════════════════════════════════════════════════════════════════════════════
elif page == "Final Selection":
    st.markdown(
        "<div class='page-title'>Final Selection</div>"
        "<div class='page-sub'>Review and confirm the final list of selected, backup, and rejected investigator sites for this study.</div>",
        unsafe_allow_html=True,
    )

    final_df = get_final_selection_page_df(base_view)

    selected_sites = final_df[final_df["final_status"] == "Selected"]
    backup_sites = final_df[final_df["final_status"] == "Backup"]
    rejected_sites = final_df[final_df["final_status"] == "Rejected"]

    metric_cards([
        ("Selected Sites", len(selected_sites), "dark"),
        ("Backup Sites", len(backup_sites), "dark"),
        ("Rejected Sites", len(rejected_sites), "light"),
        ("Avg. Qualification Score", f"{int(final_df['qualification_score'].mean()) if not final_df.empty else 0}", "dark"),
    ])

    tab_sel, tab_bak, tab_rej, tab_all = st.tabs(["✅ Selected", "🔄 Backup", "❌ Rejected", "📋 All Sites"])

    def render_final_table(df: pd.DataFrame, allow_edit: bool = False, table_key: str = "final_tbl"):
        if df.empty:
            st.info("No sites in this category.")
            return
        cols = ["site_id", "site_name", "country_label", "matched_pi_name", "ai_rank_score",
                "qualification_score", "risk_level", "cda_status", "cra_flag",
                "preferred", "final_status", "selection_justification"]
        display = df[[c for c in cols if c in df.columns]].copy()
        display.columns = [c.replace("_", " ").title() for c in display.columns]
        if allow_edit and "Selection Justification" in display.columns:
            edited = st.data_editor(
                display, use_container_width=True, hide_index=True,
                disabled=[c for c in display.columns if c != "Selection Justification"],
                key=table_key,
            )
            if st.button("Save Justifications", key=f"{table_key}_save"):
                orig_ids = df["site_id"].tolist()
                justifications = edited["Selection Justification"].tolist() if "Selection Justification" in edited.columns else []
                updates_by_site = {}
                for sid, just in zip(orig_ids, justifications):
                    updates_by_site[normalize_text_value(sid)] = {"selection_justification": normalize_text_value(just)}
                persist_site_actions_by_row(updates_by_site)
                set_flash_message("Justifications saved.")
                clear_and_rerun()
        else:
            st.dataframe(display, use_container_width=True, hide_index=True)

    with tab_sel:
        render_final_table(selected_sites, allow_edit=True, table_key="final_selected_editor")
    with tab_bak:
        render_final_table(backup_sites, table_key="final_backup_tbl")
    with tab_rej:
        render_final_table(rejected_sites, table_key="final_rejected_tbl")
    with tab_all:
        render_final_table(final_df, table_key="final_all_tbl")

    st.divider()
    st.markdown("### Export")
    export_cols = ["site_id", "site_name", "country_label", "matched_pi_name",
                   "ai_rank_score", "qualification_score", "risk_level",
                   "cda_status", "final_status", "selection_justification"]
    export_df = final_df[[c for c in export_cols if c in final_df.columns]].copy()
    csv_bytes = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇ Download Final Site List (.csv)",
        data=csv_bytes,
        file_name=f"final_site_selection_{active_trial_context['therapeutic_area'].replace(' ','_')}_{active_trial_context['indication'].replace(' ','_')}.csv",
        mime="text/csv",
        use_container_width=False,
    )

# ── Floating chatbot panel ────────────────────────────────────────────────────
render_chatbot_panel()
