"""Pure routing decisions for WhatsApp parent messages.

This module intentionally has no network, database, or WhatsApp side effects.
Task 4 can persist and display these decision dictionaries in the admin UI.
"""

from typing import Any, Dict

from whatsapp_nlu import (
    detect_local_intent,
    extract_local_profile_patch,
    is_hard_off_topic,
    normalize_parent_text,
)


LOCAL_COMMAND_INTENTS = {"next_page", "all_courses", "reset"}
CONCERN_PROFILE_KEYS = ("pain_points", "topic", "target")
AMBIGUOUS_PROFILE_HINTS = (
    "小朋友",
    "孩子",
    "子女",
    "仔女",
    "兒子",
    "儿子",
    "女兒",
    "女儿",
    "大仔",
    "細仔",
    "细仔",
    "讀緊",
    "读紧",
    "讀書",
    "读书",
    "高小",
    "低小",
    "幼稚園",
    "幼儿园",
    "幼兒園",
    "中學",
    "中学",
    "好細",
    "好细",
    "青春期",
)


def _has_value(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set)):
        return any(_has_value(item) for item in value)
    return value is not None


def _profile_ready(profile: Dict[str, Any]) -> bool:
    return _has_value(profile.get("age_groups")) and any(
        _has_value(profile.get(key)) for key in CONCERN_PROFILE_KEYS
    )


def _merge_profile_patch(profile: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(profile)
    merged.update(patch)
    return merged


def _looks_like_ambiguous_profile_text(normalized_text: str) -> bool:
    if not normalized_text or len(normalized_text) > 80:
        return False
    return any(hint in normalized_text for hint in AMBIGUOUS_PROFILE_HINTS)


def _local_profile_update_action(profile: Dict[str, Any]) -> str:
    if _profile_ready(profile):
        return "recommend_after_profile_update"
    if not _has_value(profile.get("age_groups")):
        return "ask_missing_age"
    return "ask_missing_concern"


def _base_decision(route: str, normalized_text: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "route": route,
        "normalized_text": normalized_text,
        "intent": "",
        "allow_llm": False,
        "llm_purpose": "",
        "profile_ready": _profile_ready(profile),
        "profile_patch": {},
        "recommended_action": "no_action",
    }


def decide_message_route(text: str, profile: dict) -> dict:
    """Decide how a WhatsApp text should be handled without performing the action."""
    normalized_text = normalize_parent_text(text)
    current_profile = dict(profile or {})

    if is_hard_off_topic(normalized_text):
        decision = _base_decision("off_topic", normalized_text, current_profile)
        decision.update({
            "allow_llm": False,
            "recommended_action": "local_refusal",
        })
        return decision

    local_intent = detect_local_intent(normalized_text)
    if local_intent in LOCAL_COMMAND_INTENTS:
        decision = _base_decision("local_command", normalized_text, current_profile)
        decision.update({
            "intent": local_intent,
            "recommended_action": "execute_local_command",
        })
        return decision

    profile_patch = extract_local_profile_patch(normalized_text)
    if profile_patch:
        merged_profile = _merge_profile_patch(current_profile, profile_patch)
        decision = _base_decision("local_profile_update", normalized_text, merged_profile)
        decision.update({
            "profile_patch": profile_patch,
            "recommended_action": _local_profile_update_action(merged_profile),
        })
        return decision

    if local_intent == "courses" and _profile_ready(current_profile):
        decision = _base_decision("recommend_courses", normalized_text, current_profile)
        decision.update({
            "intent": local_intent,
            "allow_llm": True,
            "llm_purpose": "bounded_recommendation",
            "recommended_action": "recommend_courses",
        })
        return decision

    if _looks_like_ambiguous_profile_text(normalized_text):
        decision = _base_decision("llm_profile_extraction", normalized_text, current_profile)
        decision.update({
            "allow_llm": True,
            "llm_purpose": "profile_extraction",
            "recommended_action": "extract_profile_with_llm",
        })
        return decision

    if local_intent == "courses":
        decision = _base_decision("local_command", normalized_text, current_profile)
        decision.update({
            "intent": local_intent,
            "allow_llm": False,
            "recommended_action": "ask_for_profile_completion",
        })
        return decision

    decision = _base_decision("unknown", normalized_text, current_profile)
    decision.update({
        "allow_llm": False,
        "recommended_action": "ask_for_supported_query",
    })
    return decision
