from __future__ import annotations


_ISSUE_TAG_MAP = {
    "ambiguity_review": ["ambiguity_review", "manual_review"],
    "unit_conflict": ["unit_conflict", "manual_review"],
    "anchor_conflict": ["anchor_conflict", "manual_review"],
    "category_mismatch": ["family_conflict", "manual_review"],
    "material_mismatch": ["material_conflict", "manual_review"],
    "connection_mismatch": ["connection_conflict", "manual_review"],
    "parameter_deviation": ["param_conflict", "manual_review"],
    "pipe_usage_conflict": ["context_conflict", "manual_review"],
    "sleeve_mismatch": ["family_conflict", "manual_review"],
    "electric_pair_mismatch": ["pair_conflict", "manual_review"],
    "elevator_type_mismatch": ["family_conflict", "manual_review"],
    "elevator_floor_mismatch": ["param_conflict", "manual_review"],
    "review_corrected": ["corrected"],
}


def merge_reason_tags(*groups) -> list[str]:
    merged: list[str] = []
    for group in groups:
        if not group:
            continue
        for tag in group:
            tag = str(tag or "").strip()
            if tag and tag not in merged:
                merged.append(tag)
    return merged


def apply_reason_metadata(result: dict,
                          *,
                          primary_reason: str = "",
                          reason_tags=None,
                          detail: str = "",
                          stage: str = "") -> dict:
    if not isinstance(result, dict):
        return result
    merged_tags = merge_reason_tags(result.get("reason_tags") or [], reason_tags or [])
    if stage:
        merged_tags = merge_reason_tags(merged_tags, [stage])
    if merged_tags:
        result["reason_tags"] = merged_tags
    if primary_reason:
        result["primary_reason"] = primary_reason
    if detail:
        result["reason_detail"] = detail
        result.setdefault("explanation", detail)
        if not result.get("quotas"):
            result.setdefault("no_match_reason", detail)
    return result


def tags_for_issue(issue_type: str) -> list[str]:
    return list(_ISSUE_TAG_MAP.get(str(issue_type or "").strip(), []))

