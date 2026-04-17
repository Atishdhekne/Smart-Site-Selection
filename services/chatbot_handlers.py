from __future__ import annotations

import re
from collections.abc import Callable

import pandas as pd


def _text(value) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def _scope_name(context: dict) -> str:
    scope = _text(context.get("scope_name", "")).strip()
    return scope or "current scope"


def _no_data_message(context: dict) -> str:
    return f"No sites are available in the selected scope ({_scope_name(context)}). Adjust filters or choose a different context scope."


def _safe_numeric(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(default)


def _safe_bool(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    series = df[col]
    if series.dtype == bool:
        return series.fillna(False)
    lowered = series.astype(str).str.strip().str.lower()
    return lowered.isin(["1", "true", "t", "yes", "y"])


def _safe_text(df: pd.DataFrame, col: str, default: str = "") -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index, dtype=str)
    return df[col].fillna(default).astype(str)


def _safe_top_rows(context_df: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    if context_df.empty:
        return context_df
    out = context_df.copy().reset_index(drop=True)
    for col in ["ai_rank_score", "feasibility_score", "qualification_score"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    sort_cols = [c for c in ["ai_rank_score", "feasibility_score", "qualification_score"] if c in out.columns]
    if sort_cols:
        out = out.sort_values(sort_cols, ascending=[False] * len(sort_cols), kind="mergesort")
    return out.head(n).copy()


def _site_line(row: pd.Series, index: int) -> str:
    site = _text(row.get("site_name", "Site")).strip() or "Unknown site"
    country = _text(row.get("country_label", row.get("country", "Unknown"))).strip() or "Unknown"
    ai_score = int(pd.to_numeric(row.get("ai_rank_score", 0), errors="coerce") or 0)
    feasibility = int(pd.to_numeric(row.get("feasibility_score", 0), errors="coerce") or 0)
    pi_name = _text(row.get("matched_pi_name", "No PI on file")).strip() or "No PI on file"
    return f"{index}. {site} ({country}) - AI {ai_score}, Feasibility {feasibility}, PI {pi_name}"


def _compact_names(values: pd.Series, *, max_items: int = 4) -> str:
    items = [item.strip() for item in values.astype(str).tolist() if item and item.strip()]
    if not items:
        return ""
    return ", ".join(items[:max_items])


def handle_top_sites(context_df: pd.DataFrame, context: dict) -> str:
    if context_df.empty:
        return _no_data_message(context)
    top = _safe_top_rows(context_df, n=5)
    lines = [_site_line(row, i + 1) for i, (_, row) in enumerate(top.iterrows())]
    return f"Top ranked sites in {_scope_name(context)} ({len(context_df)} rows):\n" + "\n".join(lines)


def handle_top_sites_europe(context_df: pd.DataFrame, context: dict) -> str:
    if context_df.empty:
        return _no_data_message(context)

    region = _safe_text(context_df, "region")
    europe_df = context_df[region.str.contains("europe", case=False, na=False)].copy()
    if europe_df.empty:
        return f"No Europe sites are available in {_scope_name(context)}."

    top = _safe_top_rows(europe_df, n=5)
    lines = [_site_line(row, i + 1) for i, (_, row) in enumerate(top.iterrows())]
    return f"Top European candidates in {_scope_name(context)} ({len(europe_df)} rows):\n" + "\n".join(lines)


def _status_summary(context_df: pd.DataFrame, context: dict, target_status: str, label: str) -> str:
    if context_df.empty:
        return _no_data_message(context)
    status = _safe_text(context_df, "final_status")
    subset = context_df[status.str.lower() == target_status.lower()].copy()
    count = len(subset)
    if count == 0:
        return f"No {label.lower()} are currently present in {_scope_name(context)}."
    names = _safe_text(subset.head(5), "site_name")
    preview = _compact_names(names, max_items=5)
    return f"{label}: {count} site(s) in {_scope_name(context)}." + (f" Example sites: {preview}." if preview else "")


def handle_selected_sites_summary(context_df: pd.DataFrame, context: dict) -> str:
    return _status_summary(context_df, context, "Selected", "Selected sites")


def handle_backup_sites_summary(context_df: pd.DataFrame, context: dict) -> str:
    return _status_summary(context_df, context, "Backup", "Backup sites")


def handle_rejected_sites_summary(context_df: pd.DataFrame, context: dict) -> str:
    return _status_summary(context_df, context, "Rejected", "Rejected sites")


def handle_pending_feasibility_responses(context_df: pd.DataFrame, context: dict) -> str:
    if context_df.empty:
        return _no_data_message(context)

    sent = _safe_bool(context_df, "survey_sent")
    received = _safe_bool(context_df, "response_received")
    pending = context_df[sent & (~received)].copy()
    if pending.empty:
        return f"No pending feasibility responses in {_scope_name(context)}."

    pending = pending.copy().reset_index(drop=True)
    days_open = _safe_numeric(pending, "days_open", 0)
    breaches = int((days_open > 7).sum())
    pending["days_open_safe"] = days_open
    pending = pending.sort_values("days_open_safe", ascending=False, kind="mergesort")

    preview_rows = pending.head(3)
    preview_lines = []
    for _, row in preview_rows.iterrows():
        site = _text(row.get("site_name", "Unknown site")).strip() or "Unknown site"
        open_days = int(pd.to_numeric(row.get("days_open_safe", 0), errors="coerce") or 0)
        preview_lines.append(f"{site} ({open_days}d)")

    preview_text = ", ".join(preview_lines)
    response = (
        f"Pending feasibility responses in {_scope_name(context)}: {len(pending)} site(s). "
        f"SLA breaches (>7 days open): {breaches}."
    )
    if preview_text:
        response += f" Follow-up queue: {preview_text}."
    return response


def handle_sla_breach_sites(context_df: pd.DataFrame, context: dict) -> str:
    if context_df.empty:
        return _no_data_message(context)

    sent = _safe_bool(context_df, "survey_sent")
    received = _safe_bool(context_df, "response_received")
    days_open = _safe_numeric(context_df, "days_open", 0)
    breached = context_df[sent & (~received) & (days_open > 7)].copy()
    if breached.empty:
        return f"No SLA breach sites are currently in {_scope_name(context)}."

    breached = breached.copy().reset_index(drop=True)
    breached["days_open_safe"] = _safe_numeric(breached, "days_open", 0)
    breached = breached.sort_values("days_open_safe", ascending=False, kind="mergesort")

    lines = []
    for i, (_, row) in enumerate(breached.head(5).iterrows()):
        site = _text(row.get("site_name", "Unknown site")).strip() or "Unknown site"
        country = _text(row.get("country_label", row.get("country", "Unknown"))).strip() or "Unknown"
        open_days = int(pd.to_numeric(row.get("days_open_safe", 0), errors="coerce") or 0)
        lines.append(f"{i + 1}. {site} ({country}) - {open_days} days open")
    return f"SLA breach sites in {_scope_name(context)}:\n" + "\n".join(lines)


def handle_high_risk_sites(context_df: pd.DataFrame, context: dict) -> str:
    if context_df.empty:
        return _no_data_message(context)

    risk = _safe_text(context_df, "risk_level")
    cra_flag = _safe_text(context_df, "cra_flag")
    high_risk = context_df[risk.str.lower().eq("high") | cra_flag.str.contains("risk", case=False, na=False)].copy()
    if high_risk.empty:
        return f"No high-risk sites are currently present in {_scope_name(context)}."

    top = _safe_top_rows(high_risk, n=5)
    lines = []
    for i, (_, row) in enumerate(top.iterrows()):
        site = _text(row.get("site_name", "Unknown site")).strip() or "Unknown site"
        country = _text(row.get("country_label", row.get("country", "Unknown"))).strip() or "Unknown"
        cra = _text(row.get("cra_flag", "None")).strip() or "None"
        lines.append(f"{i + 1}. {site} ({country}) - CRA flag: {cra}")
    return f"High-risk site summary for {_scope_name(context)} ({len(high_risk)} site(s)):\n" + "\n".join(lines)


def _query_keywords(query: str) -> list[str]:
    clean = re.sub(r"[^a-z0-9\s]", " ", str(query or "").lower())
    tokens = [t for t in clean.split() if t]
    stop = {
        "show",
        "find",
        "investigator",
        "pi",
        "for",
        "site",
        "details",
        "lookup",
        "about",
        "please",
        "who",
        "is",
        "the",
        "at",
    }
    return [t for t in tokens if t not in stop]


def handle_investigator_lookup(context_df: pd.DataFrame, context: dict) -> str:
    if context_df.empty:
        return _no_data_message(context)

    query = _text(context.get("query", ""))
    lookup_tokens = _query_keywords(query)
    site_names = _safe_text(context_df, "site_name")
    pi_names = _safe_text(context_df, "matched_pi_name")

    if lookup_tokens:
        mask = pd.Series(False, index=context_df.index)
        for token in lookup_tokens:
            mask = mask | site_names.str.contains(token, case=False, na=False) | pi_names.str.contains(token, case=False, na=False)
        matches = context_df[mask].copy()
        if matches.empty:
            return f"No investigator match was found in {_scope_name(context)} for that lookup."
    else:
        matches = _safe_top_rows(context_df, n=5)

    top = _safe_top_rows(matches, n=5)
    years = _safe_numeric(top, "pi_years_experience", 0)
    lines = []
    for i, (_, row) in enumerate(top.iterrows()):
        site = _text(row.get("site_name", "Unknown site")).strip() or "Unknown site"
        pi_name = _text(row.get("matched_pi_name", "No PI on file")).strip() or "No PI on file"
        exp = int(round(float(years.iloc[i]))) if i < len(years) else 0
        lines.append(f"{i + 1}. {site} - PI {pi_name} ({exp} years experience)")
    return f"Investigator lookup results in {_scope_name(context)}:\n" + "\n".join(lines)


def handle_current_study_context(context_df: pd.DataFrame, context: dict) -> str:
    trial = context.get("trial_context", {}) or {}
    study_title = _text(trial.get("study_title", "")).strip() or "Not set"
    protocol_id = _text(trial.get("protocol_id", "")).strip() or "Not set"
    ta = _text(trial.get("therapeutic_area", "Unknown")).strip() or "Unknown"
    indication = _text(trial.get("indication", "Unknown")).strip() or "Unknown"
    phase = _text(trial.get("phase", "Unknown")).strip() or "Unknown"
    enrollment = _text(trial.get("total_target_enrollment", "Unknown")).strip() or "Unknown"
    min_age = _text(trial.get("min_age", "Unknown")).strip() or "Unknown"
    max_age = _text(trial.get("max_age", "Unknown")).strip() or "Unknown"
    return (
        "Active study context: "
        f"Study title={study_title}; Protocol ID={protocol_id}; Therapeutic area={ta}; "
        f"Indication={indication}; Phase={phase}; Target enrollment={enrollment}; Age range={min_age}-{max_age}."
    )


def handle_current_context_scope(context_df: pd.DataFrame, context: dict) -> str:
    scope = _scope_name(context)
    if context_df.empty:
        return f"Current context scope is {scope}. No sites are available in this scope."

    region_series = _safe_text(context_df, "region")
    top_regions = region_series.value_counts().head(3)
    region_text = ", ".join([f"{idx}: {int(val)}" for idx, val in top_regions.items()])

    country_series = _safe_text(context_df, "country_label") if "country_label" in context_df.columns else _safe_text(context_df, "country")
    top_countries = country_series.value_counts().head(3)
    country_text = ", ".join([f"{idx}: {int(val)}" for idx, val in top_countries.items()])

    response = f"Current context scope is {scope} with {len(context_df)} row(s)."
    if region_text:
        response += f" Region mix: {region_text}."
    if country_text:
        response += f" Country mix: {country_text}."
    return response


def handle_qualification_summary(context_df: pd.DataFrame, context: dict) -> str:
    if context_df.empty:
        return _no_data_message(context)

    q_scores = _safe_numeric(context_df, "qualification_score", 0)
    avg_q = round(float(q_scores.mean()), 1) if not q_scores.empty else 0.0
    cda = _safe_text(context_df, "cda_status")
    cda_counts = cda.value_counts().to_dict()
    cda_summary = ", ".join([f"{k}: {v}" for k, v in cda_counts.items()])
    preferred = int(_safe_bool(context_df, "preferred").sum())
    flagged = int(_safe_text(context_df, "cra_flag").str.lower().ne("none").sum()) if "cra_flag" in context_df.columns else 0

    response = (
        f"Qualification summary for {_scope_name(context)}: average score {avg_q}, "
        f"preferred sites {preferred}, flagged sites {flagged}."
    )
    if cda_summary:
        response += f" CDA status counts - {cda_summary}."
    return response


def handle_final_selection_summary(context_df: pd.DataFrame, context: dict) -> str:
    if context_df.empty:
        return _no_data_message(context)

    status = _safe_text(context_df, "final_status")
    selected = int(status.str.lower().eq("selected").sum())
    backup = int(status.str.lower().eq("backup").sum())
    rejected = int(status.str.lower().eq("rejected").sum())
    included_mask = status.str.lower().ne("rejected")
    projected = int(_safe_numeric(context_df[included_mask], "projected_enroll_rate_per_month", 0).sum())
    return (
        f"Final selection summary for {_scope_name(context)}: "
        f"selected={selected}, backup={backup}, rejected={rejected}, projected monthly enrollment={projected}."
    )


def handle_notification_summary(context_df: pd.DataFrame, context: dict) -> str:
    loader = context.get("notifications_loader")
    if not callable(loader):
        return "Notification data source is unavailable for the current session."

    try:
        notes = loader()
    except Exception:
        return "Notification data source is currently unavailable."

    if not isinstance(notes, pd.DataFrame) or notes.empty:
        return "No notifications are currently available."

    ack = _safe_bool(notes, "acknowledged")
    pending_notes = notes[~ack].copy()
    pending = int(len(pending_notes))

    high = 0
    if "priority" in notes.columns:
        high = int(notes[notes["priority"].astype(str).str.lower().eq("high")].shape[0])

    response = f"Notification summary: total={len(notes)}, pending={pending}, high-priority={high}."
    if not pending_notes.empty and "type" in pending_notes.columns:
        type_counts = pending_notes["type"].astype(str).value_counts().head(2)
        type_text = ", ".join([f"{t}: {int(c)}" for t, c in type_counts.items()])
        if type_text:
            response += f" Pending type mix - {type_text}."
    return response


HANDLER_REGISTRY: dict[str, Callable[[pd.DataFrame, dict], str]] = {
    "handle_top_sites": handle_top_sites,
    "handle_top_sites_europe": handle_top_sites_europe,
    "handle_selected_sites_summary": handle_selected_sites_summary,
    "handle_backup_sites_summary": handle_backup_sites_summary,
    "handle_rejected_sites_summary": handle_rejected_sites_summary,
    "handle_pending_feasibility_responses": handle_pending_feasibility_responses,
    "handle_sla_breach_sites": handle_sla_breach_sites,
    "handle_high_risk_sites": handle_high_risk_sites,
    "handle_investigator_lookup": handle_investigator_lookup,
    "handle_current_study_context": handle_current_study_context,
    "handle_current_context_scope": handle_current_context_scope,
    "handle_qualification_summary": handle_qualification_summary,
    "handle_final_selection_summary": handle_final_selection_summary,
    "handle_notification_summary": handle_notification_summary,
}


def run_handler(handler_key: str, context_df: pd.DataFrame, context: dict) -> str:
    fn = HANDLER_REGISTRY.get(str(handler_key or "").strip())
    if fn is None:
        return "This request is not mapped to a deterministic handler yet."
    try:
        return fn(context_df, context)
    except Exception:
        return _no_data_message(context)
