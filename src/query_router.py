import re
from typing import Any, Mapping


_INSTALLATION_HINTS = (
    "安装",
    "配线",
    "配管",
    "桥架",
    "电缆",
    "电线",
    "导线",
    "套管",
    "接线盒",
    "配电箱",
    "阀门",
    "风管",
    "喷淋",
    "消火栓",
    "给排水",
    "消防",
    "弱电",
    "强电",
    "机电",
)

_MATERIAL_HINTS = (
    "主材",
    "材料",
    "设备",
    "品牌",
    "厂家",
    "单价",
    "询价",
    "采购",
)

_INSTALLATION_ACTION_HINTS = (
    "安装",
    "敷设",
    "配线",
    "配管",
    "制作",
    "接线",
    "穿线",
)

_SPEC_PATTERNS = (
    r"DN\s*\d+",
    r"\d+\s*回路",
    r"\d+(?:\.\d+)?\s*(mm2|mm²|kva|kv|kw|a|m)\b",
    r"(?:SC|PC|PVC|JDG|KBG|RC|MT|FPC)\s*\d+",
    r"(?:WDZ[NZ]?-?|NH-?)*(?:BV|BYJ|BVR|BLV|RVS|RVV|YJV|YJY)\s*-?\s*\d",
    r"\d+\s*[*×xX]\s*\d+(?:\.\d+)?(?:\s*\+\s*\d+\s*[*×xX]\s*\d+(?:\.\d+)?)*",
    r"[A-Za-z]{1,8}[-_/]*\d+(?:[*×xX/]\d+)*",
)

_COMPLEX_INSTALL_SPEC_PATTERN = re.compile(
    r"\d+\s*[*×xX]\s*\d+(?:\.\d+)?\s*\+\s*\d+\s*[*×xX]\s*\d+(?:\.\d+)?",
    flags=re.IGNORECASE,
)


def count_spec_signals(text: str) -> int:
    text = str(text or "")
    hits = 0
    for pattern in _SPEC_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits += 1
    return hits


def normalize_query_route(route_profile: Any) -> str:
    if isinstance(route_profile, Mapping):
        route = route_profile.get("route")
    else:
        route = route_profile
    route = str(route or "").strip()
    return route or "balanced"


def build_query_route_profile(
    query: str,
    *,
    item: Mapping[str, Any] | None = None,
    specialty: str | None = None,
    canonical_features: Mapping[str, Any] | None = None,
    context_prior: Mapping[str, Any] | None = None,
) -> dict:
    text = str(query or "").strip()
    upper_text = text.upper()
    chinese_len = len(re.findall(r"[\u4e00-\u9fff]", text))
    spec_signal_count = count_spec_signals(text)
    has_complex_install_spec = bool(_COMPLEX_INSTALL_SPEC_PATTERN.search(text))

    item = item or {}
    canonical_features = canonical_features or {}
    context_prior = context_prior or {}

    context_text = " ".join(
        str(v or "")
        for v in (
            specialty,
            item.get("specialty"),
            item.get("section"),
            canonical_features.get("entity"),
            canonical_features.get("canonical_name"),
            canonical_features.get("system"),
            context_prior.get("primary_subject"),
            " ".join(context_prior.get("decisive_terms", []) or []),
            context_prior.get("noise_marker"),
            " ".join(context_prior.get("context_hints", []) or []),
        )
        if v
    )

    is_installation_context = any(keyword in text for keyword in _INSTALLATION_HINTS) or any(
        keyword in context_text for keyword in _INSTALLATION_HINTS
    )
    is_material_query = (
        any(keyword in text for keyword in _MATERIAL_HINTS)
        and not any(keyword in text for keyword in _INSTALLATION_ACTION_HINTS)
    )
    if canonical_features.get("category") == "material" or item.get("is_material"):
        is_material_query = True

    is_ambiguous_short = spec_signal_count == 0 and chinese_len > 0 and chinese_len <= 6
    is_semantic_description = spec_signal_count == 0 and chinese_len >= 18

    route = "balanced"
    reason = "balanced"
    if is_material_query and not is_installation_context:
        route = "material"
        reason = "material_heavy"
    elif is_installation_context and (spec_signal_count >= 1 or has_complex_install_spec):
        route = "installation_spec"
        reason = "spec_heavy_installation"
    elif is_semantic_description:
        route = "semantic_description"
        reason = "semantic_heavy"
    elif is_ambiguous_short:
        route = "ambiguous_short"
        reason = "ambiguous_short"
    elif spec_signal_count >= 2 or (
        spec_signal_count >= 1 and chinese_len <= 20 and any(code in upper_text for code in ("DN", "JDG", "KBG", "BV", "YJV"))
    ):
        route = "spec_heavy"
        reason = "spec_heavy"

    return {
        "route": route,
        "reason": reason,
        "spec_signal_count": spec_signal_count,
        "has_complex_install_spec": has_complex_install_spec,
        "is_installation_context": is_installation_context,
        "is_material_query": is_material_query,
        "is_ambiguous_short": is_ambiguous_short,
        "chinese_len": chinese_len,
        "primary_subject": str(context_prior.get("primary_subject") or ""),
        "decisive_terms": list(context_prior.get("decisive_terms", []) or [])[:4],
        "noise_marker": str(context_prior.get("noise_marker") or ""),
        "context": context_text[:120],
    }
