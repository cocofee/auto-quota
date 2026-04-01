from __future__ import annotations

from typing import Any, Mapping


SUPPORT_KEYWORDS = (
    "支架",
    "吊架",
    "支吊架",
    "支撑架",
    "管架",
    "吊托",
    "抗震",
)

PIPE_SCOPE_KEYWORDS = (
    "管道",
    "给水",
    "排水",
    "给排水",
    "消防",
    "喷淋",
    "消火栓",
    "风管",
    "暖通",
    "空调",
    "通风",
)

BRIDGE_KEYWORDS = (
    "桥架",
    "电缆桥架",
    "线槽",
    "母线槽",
)

GENERIC_PIPE_SUPPORT_SUBJECTS = (
    "管道支架",
    "管道支吊架",
    "支吊架",
    "吊托支架",
    "一般管架",
    "管架",
    "抗震支架",
    "抗震支吊架",
    "设备支架",
    "设备吊架",
    "设备支吊架",
)

GENERIC_SLEEVE_SUBJECTS = (
    "套管",
    "钢套管",
    "钢制套管",
    "普通钢制套管",
    "一般钢套管",
    "填料套管",
    "防水套管",
    "刚性防水套管",
    "柔性防水套管",
)

GUARDED_FAMILIES = {
    "pipe_support",
    "bridge_support",
    "bridge_raceway",
    "pipe_sleeve",
}


def resolve_primary_subject_hint(context_prior: Mapping[str, Any] | None = None) -> str:
    context_prior = dict(context_prior or {})
    primary_profile = dict(context_prior.get("primary_query_profile") or {})
    for candidate in (
        context_prior.get("primary_subject"),
        primary_profile.get("primary_subject"),
        primary_profile.get("primary_text"),
    ):
        text = str(candidate or "").strip()
        if text:
            return text
    return ""


def subject_matches_family(subject: str, family: str) -> bool:
    text = str(subject or "").strip()
    if not text:
        return True

    if family == "pipe_support":
        has_support = any(keyword in text for keyword in SUPPORT_KEYWORDS)
        has_pipe_scope = any(keyword in text for keyword in PIPE_SCOPE_KEYWORDS)
        has_bridge_scope = any(keyword in text for keyword in BRIDGE_KEYWORDS)
        is_generic = any(token in text for token in GENERIC_PIPE_SUPPORT_SUBJECTS)
        if has_bridge_scope:
            return False
        return has_support and (has_pipe_scope or is_generic)

    if family == "bridge_support":
        return (
            any(keyword in text for keyword in SUPPORT_KEYWORDS)
            and any(keyword in text for keyword in BRIDGE_KEYWORDS)
        )

    if family == "bridge_raceway":
        return any(keyword in text for keyword in BRIDGE_KEYWORDS)

    if family == "pipe_sleeve":
        if text in GENERIC_SLEEVE_SUBJECTS:
            return True
        return any(text.startswith(token) for token in GENERIC_SLEEVE_SUBJECTS)

    return True


def should_suppress_family_hint(
    family: str,
    context_prior: Mapping[str, Any] | None = None,
) -> bool:
    family = str(family or "").strip()
    if family not in GUARDED_FAMILIES:
        return False
    subject = resolve_primary_subject_hint(context_prior)
    if not subject:
        return False
    return not subject_matches_family(subject, family)


def is_family_hint_term(term: str, family: str) -> bool:
    text = str(term or "").strip()
    if not text:
        return False

    if family == "pipe_support":
        return any(keyword in text for keyword in SUPPORT_KEYWORDS + PIPE_SCOPE_KEYWORDS)

    if family == "bridge_support":
        return any(keyword in text for keyword in SUPPORT_KEYWORDS + BRIDGE_KEYWORDS + ("电气",))

    if family == "bridge_raceway":
        return any(keyword in text for keyword in BRIDGE_KEYWORDS + ("电气",))

    if family == "pipe_sleeve":
        return any(keyword in text for keyword in GENERIC_SLEEVE_SUBJECTS + ("套管", "给排水", "电气"))

    return False
