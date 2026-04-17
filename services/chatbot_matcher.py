from __future__ import annotations

import re
from collections.abc import Iterable


NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]")
SPACE_RE = re.compile(r"\s+")

CANONICAL_REPLACEMENTS = [
    (re.compile(r"\bpis?\b"), "investigator"),
    (re.compile(r"\bfeas\b"), "feasibility"),
    (re.compile(r"\bnotif(?:ication|ications)?\b"), "notification"),
    (re.compile(r"\bctx\b"), "context"),
    (re.compile(r"\brisky\b"), "risk"),
    (re.compile(r"\bnon\s*responders?\b"), "pending responses"),
]

NAVIGATION_VERBS = {"go", "open", "navigate", "take", "switch", "move"}
STOP_TOKENS = {
    "a",
    "an",
    "and",
    "about",
    "for",
    "from",
    "in",
    "is",
    "me",
    "my",
    "of",
    "on",
    "please",
    "show",
    "the",
    "to",
    "what",
    "with",
}


def normalize_query(text: str) -> str:
    lowered = str(text or "").lower().replace("'", "")
    for pattern, replacement in CANONICAL_REPLACEMENTS:
        lowered = pattern.sub(replacement, lowered)
    cleaned = NON_ALNUM_RE.sub(" ", lowered)
    return SPACE_RE.sub(" ", cleaned).strip()


def _stem_token(token: str) -> str:
    value = token.strip()
    if len(value) > 5 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 5 and value.endswith("ing"):
        return value[:-3]
    if len(value) > 4 and value.endswith("ed"):
        return value[:-2]
    if len(value) > 4 and value.endswith("s"):
        return value[:-1]
    return value


def _tokens(text: str) -> list[str]:
    normalized = normalize_query(text)
    if not normalized:
        return []
    return [_stem_token(tok) for tok in normalized.split(" ") if tok]


def _bigrams(tokens: list[str]) -> set[tuple[str, str]]:
    if len(tokens) < 2:
        return set()
    return {(tokens[i], tokens[i + 1]) for i in range(len(tokens) - 1)}


def _pattern_score(query_norm: str, query_tokens: list[str], query_bigrams: set[tuple[str, str]], pattern: str) -> float:
    pattern_norm = normalize_query(pattern)
    if not pattern_norm:
        return 0.0

    pattern_tokens = [_stem_token(tok) for tok in pattern_norm.split(" ") if tok]
    pattern_token_set = set(pattern_tokens)
    query_token_set = set(query_tokens)
    overlap = query_token_set.intersection(pattern_token_set)

    pattern_coverage = len(overlap) / max(1, len(pattern_token_set))
    query_coverage = len(overlap) / max(1, len(query_token_set))

    pattern_bigrams = _bigrams(pattern_tokens)
    bigram_overlap = len(query_bigrams.intersection(pattern_bigrams))
    bigram_coverage = bigram_overlap / max(1, len(pattern_bigrams)) if pattern_bigrams else 0.0

    phrase_bonus = 0.0
    if query_norm and (query_norm in pattern_norm or pattern_norm in query_norm):
        phrase_bonus = 0.15

    score = (pattern_coverage * 0.55) + (query_coverage * 0.25) + (bigram_coverage * 0.20) + phrase_bonus
    return min(1.0, score)


def _iter_intent_patterns(intents: Iterable[dict]) -> list[tuple[dict, str, str]]:
    rows: list[tuple[dict, str, str]] = []
    for intent in intents:
        intent_id = str(intent.get("intent_id", "")).strip()
        if not intent_id:
            continue
        combined_patterns = []
        for source_key in ["patterns", "aliases"]:
            source_patterns = intent.get(source_key, [])
            if isinstance(source_patterns, list):
                combined_patterns.extend(source_patterns)
        for pattern in combined_patterns:
            pattern_text = str(pattern or "").strip()
            pattern_norm = normalize_query(pattern_text)
            if pattern_norm:
                rows.append((intent, pattern_text, pattern_norm))
    return rows


def _intent_keywords(intent: dict) -> set[str]:
    keywords = set()
    raw_keywords = intent.get("keywords", [])
    if isinstance(raw_keywords, list):
        for keyword in raw_keywords:
            keywords.update(_tokens(str(keyword or "")))

    category = str(intent.get("category", ""))
    keywords.update(_tokens(category))

    for action in intent.get("actions", []) or []:
        if not isinstance(action, dict):
            continue
        keywords.update(_tokens(str(action.get("target", ""))))

    return {token for token in keywords if token and token not in STOP_TOKENS}


def _is_navigation_query(query_tokens: list[str]) -> bool:
    return bool(set(query_tokens).intersection(NAVIGATION_VERBS))


def _intent_has_navigation_action(intent: dict) -> bool:
    for action in intent.get("actions", []) or []:
        if isinstance(action, dict) and str(action.get("type", "")).strip() == "navigate":
            return True
    return False


def _build_fallback_suggestions(pattern_rows: list[tuple[dict, str, str]], max_items: int = 3) -> list[str]:
    suggestions: list[str] = []
    for _, pattern_text, _ in pattern_rows:
        if not pattern_text or pattern_text in suggestions:
            continue
        suggestions.append(pattern_text)
        if len(suggestions) >= max_items:
            break
    return suggestions


def match_intent(query: str, intents: list[dict], threshold: float = 0.46) -> dict:
    query_norm = normalize_query(query)
    pattern_rows = _iter_intent_patterns(intents)
    if not query_norm or not pattern_rows:
        suggestions = _build_fallback_suggestions(pattern_rows)
        return {
            "intent_id": "",
            "match_type": "fallback",
            "score": 0.0,
            "suggestions": suggestions,
        }

    exact_lookup: dict[str, dict] = {}
    for intent, _, pattern_norm in pattern_rows:
        exact_lookup.setdefault(pattern_norm, intent)
    exact_hit = exact_lookup.get(query_norm)
    if exact_hit:
        return {
            "intent_id": str(exact_hit.get("intent_id", "")),
            "match_type": "exact",
            "score": 1.0,
            "suggestions": [],
        }

    query_tokens = _tokens(query_norm)
    query_bigrams = _bigrams(query_tokens)
    query_token_set = set(query_tokens)
    navigation_query = _is_navigation_query(query_tokens)

    per_intent_rows: dict[str, dict] = {}
    for intent, pattern_text, _ in pattern_rows:
        intent_id = str(intent.get("intent_id", "")).strip()
        if not intent_id:
            continue
        row = per_intent_rows.setdefault(intent_id, {"intent": intent, "best_pattern_score": 0.0, "best_pattern_text": ""})
        score = _pattern_score(query_norm, query_tokens, query_bigrams, pattern_text)
        if score > row["best_pattern_score"]:
            row["best_pattern_score"] = score
            row["best_pattern_text"] = pattern_text

    scored_intents: list[tuple[float, float, str, str]] = []
    for intent_id, row in per_intent_rows.items():
        intent = row["intent"]
        pattern_score = float(row["best_pattern_score"])
        keywords = _intent_keywords(intent)
        keyword_overlap = len(query_token_set.intersection(keywords))
        keyword_score = keyword_overlap / max(1, len(keywords))

        total = (pattern_score * 0.84) + (keyword_score * 0.16)
        if navigation_query and _intent_has_navigation_action(intent):
            total += 0.06

        if "summary" in query_token_set and "summary" in keywords:
            total += 0.04

        scored_intents.append(
            (
                min(1.0, total),
                pattern_score,
                intent_id,
                str(row["best_pattern_text"]),
            )
        )

    if not scored_intents:
        suggestions = _build_fallback_suggestions(pattern_rows)
        return {
            "intent_id": "",
            "match_type": "fallback",
            "score": 0.0,
            "suggestions": suggestions,
        }

    scored_intents.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best_score, _, best_intent_id, _ = scored_intents[0]

    suggestions: list[str] = []
    for _, pattern_score, _, pattern_text in scored_intents:
        if pattern_score <= 0.05 or not pattern_text:
            continue
        if pattern_text not in suggestions:
            suggestions.append(pattern_text)
        if len(suggestions) >= 3:
            break

    if not suggestions:
        suggestions = _build_fallback_suggestions(pattern_rows)

    adaptive_threshold = threshold
    if navigation_query:
        adaptive_threshold -= 0.04
    if len(query_tokens) >= 6:
        adaptive_threshold -= 0.03
    adaptive_threshold = max(0.38, adaptive_threshold)

    if best_score >= adaptive_threshold and best_intent_id:
        return {
            "intent_id": best_intent_id,
            "match_type": "keyword",
            "score": round(best_score, 4),
            "suggestions": suggestions,
        }

    return {
        "intent_id": "",
        "match_type": "fallback",
        "score": round(best_score, 4),
        "suggestions": suggestions,
    }
