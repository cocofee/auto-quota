# -*- coding: utf-8 -*-
"""
Promotion candidate builders for confirmed OpenClaw reviews.

This module converts one approved correction into staging candidates that can be
promoted into RuleKnowledge / MethodCards / ExperienceDB.
"""

from __future__ import annotations

from typing import Any


def _normalize_source(match_source: str) -> str:
    return str(match_source or "").strip().lower()


def _has_meaningful_note(note: str) -> bool:
    return len(str(note or "").strip()) >= 6


def _quota_changed(original_quota: dict[str, Any], corrected_quota: dict[str, Any]) -> bool:
    original_id = str((original_quota or {}).get("quota_id", "")).strip()
    corrected_id = str((corrected_quota or {}).get("quota_id", "")).strip()
    if not corrected_id:
        return False
    if not original_id:
        return True
    return original_id != corrected_id


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _clean_list(value: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in _as_list(value):
        text = _clean_str(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _report_promotion_hint(report: dict[str, Any] | None, hint_type: str) -> dict[str, Any]:
    hints = _as_dict(_as_dict(report).get("promotion_hints"))
    return _as_dict(hints.get(hint_type))


def classify_openclaw_audit_error(match_source: str) -> dict[str, Any]:
    source = _normalize_source(match_source)
    if "experience" in source:
        return {
            "error_type": "polluted_experience",
            "root_cause_tags": ["experience", "correction"],
            "can_promote_rule": False,
            "can_promote_method": False,
        }
    if source.startswith("rule"):
        return {
            "error_type": "wrong_rule",
            "root_cause_tags": ["rule", "correction"],
            "can_promote_rule": True,
            "can_promote_method": False,
        }
    if "search" in source:
        return {
            "error_type": "wrong_rank",
            "root_cause_tags": ["search", "ranking"],
            "can_promote_rule": True,
            "can_promote_method": True,
        }
    if source.startswith("agent"):
        return {
            "error_type": "wrong_rank",
            "root_cause_tags": ["agent", "ranking"],
            "can_promote_rule": False,
            "can_promote_method": True,
        }
    return {
        "error_type": "review_corrected",
        "root_cause_tags": ["correction"],
        "can_promote_rule": False,
        "can_promote_method": False,
    }


def _build_rule_candidate_text(
    bill_name: str,
    bill_desc: str,
    original_quota: dict[str, Any],
    corrected_quota: dict[str, Any],
    note: str,
) -> str:
    parts: list[str] = []
    if bill_name:
        parts.append(f"Bill item: {bill_name}")
    if bill_desc:
        parts.append(f"Features: {bill_desc[:120]}")
    if original_quota.get("name") or original_quota.get("quota_id"):
        parts.append(
            f"If current top1 is {original_quota.get('name', '')}({original_quota.get('quota_id', '')})"
        )
    if corrected_quota.get("name") or corrected_quota.get("quota_id"):
        parts.append(
            f"prefer {corrected_quota.get('name', '')}({corrected_quota.get('quota_id', '')}) after review"
        )
    if note:
        parts.append(f"Basis: {note[:240]}")
    return "; ".join(part for part in parts if part).strip("; ")


def _build_method_text(
    bill_name: str,
    bill_desc: str,
    corrected_quota: dict[str, Any],
    note: str,
) -> str:
    parts: list[str] = []
    parts.append(f"Review item {bill_name}" if bill_name else "Review this bill item")
    if bill_desc:
        parts.append(f"Check features {bill_desc[:120]}")
    if corrected_quota.get("name") or corrected_quota.get("quota_id"):
        parts.append(
            f"then verify whether {corrected_quota.get('name', '')}({corrected_quota.get('quota_id', '')}) fits better"
        )
    if note:
        parts.append(f"Human basis: {note[:240]}")
    return "; ".join(part for part in parts if part).strip("; ")


def build_openclaw_promotion_candidates(
    *,
    task_id: str,
    province: str,
    specialty: str,
    bill_name: str,
    bill_desc: str,
    match_source: str,
    original_quota: dict[str, Any],
    corrected_quota: dict[str, Any],
    final_note: str,
    audit_id: int,
    report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    source = _normalize_source(match_source)
    changed = _quota_changed(original_quota, corrected_quota)
    note_ok = _has_meaningful_note(final_note)
    corrected_id = _clean_str((corrected_quota or {}).get("quota_id"))
    corrected_name = _clean_str((corrected_quota or {}).get("name"))
    corrected_unit = _clean_str((corrected_quota or {}).get("unit"))
    if not changed or not note_ok or not corrected_id:
        return []

    candidates: list[dict[str, Any]] = []
    evidence_ref = f"task:{task_id}/result:{audit_id}"
    rule_hint = _report_promotion_hint(report, "rule")
    method_hint = _report_promotion_hint(report, "method")
    experience_hint = _report_promotion_hint(report, "experience")

    if source.startswith("rule") or "search" in source:
        candidates.append({
            "source_id": task_id,
            "source_type": "audit_error",
            "source_table": "audit_errors",
            "source_record_id": str(audit_id),
            "owner": "",
            "evidence_ref": evidence_ref,
            "candidate_type": "rule",
            "target_layer": "RuleKnowledge",
            "candidate_title": f"{bill_name or 'unnamed_bill'} correction rule",
            "candidate_summary": final_note[:300],
            "candidate_payload": {
                "province": province,
                "specialty": specialty,
                "chapter": _clean_str(rule_hint.get("chapter")) or "OpenClaw Review Loop",
                "section": _clean_str(rule_hint.get("section")),
                "source_file": f"staging:audit_errors:{audit_id}",
                "rule_text": _clean_str(rule_hint.get("rule_text")) or _build_rule_candidate_text(
                    bill_name,
                    bill_desc,
                    original_quota,
                    corrected_quota,
                    final_note,
                ),
                "keywords": _clean_list(rule_hint.get("keywords")) or [kw for kw in [bill_name, corrected_name, corrected_id] if kw],
                "judgment_basis": _clean_str(rule_hint.get("judgment_basis")) or final_note[:240],
                "core_knowledge_points": _clean_list(rule_hint.get("core_knowledge_points")),
                "exclusion_reasons": _clean_list(rule_hint.get("exclusion_reasons")),
            },
            "priority": 30,
            "approval_required": 1,
        })

    if ("search" in source or source.startswith("agent")) and specialty:
        candidates.append({
            "source_id": task_id,
            "source_type": "audit_error",
            "source_table": "audit_errors",
            "source_record_id": str(audit_id),
            "owner": "",
            "evidence_ref": evidence_ref,
            "candidate_type": "method",
            "target_layer": "MethodCards",
            "candidate_title": f"{bill_name or 'unnamed_bill'} review method",
            "candidate_summary": final_note[:300],
            "candidate_payload": {
                "province": province,
                "specialty": specialty,
                "category": _clean_str(method_hint.get("category")) or bill_name or "OpenClaw Review",
                "method_text": _clean_str(method_hint.get("method_text")) or _build_method_text(
                    bill_name,
                    bill_desc,
                    corrected_quota,
                    final_note,
                ),
                "keywords": _clean_list(method_hint.get("keywords")) or [kw for kw in [bill_name, corrected_name, corrected_id] if kw],
                "pattern_keys": _clean_list(method_hint.get("pattern_keys")) or [kw for kw in [bill_name, specialty] if kw],
                "common_errors": _clean_str(method_hint.get("common_errors")) or f"avoid misjudging as {original_quota.get('name', '')}".strip(),
                "sample_count": int(method_hint.get("sample_count", 1) or 1),
                "confirm_rate": float(method_hint.get("confirm_rate", 1.0) or 1.0),
                "judgment_basis": _clean_str(method_hint.get("judgment_basis")) or final_note[:240],
                "core_knowledge_points": _clean_list(method_hint.get("core_knowledge_points")),
                "exclusion_reasons": _clean_list(method_hint.get("exclusion_reasons")),
            },
            "priority": 45,
            "approval_required": 1,
        })

    if "experience" not in source:
        bill_text = " ".join(part for part in [bill_name, bill_desc] if part).strip()
        candidates.append({
            "source_id": task_id,
            "source_type": "audit_error",
            "source_table": "audit_errors",
            "source_record_id": str(audit_id),
            "owner": "",
            "evidence_ref": evidence_ref,
            "candidate_type": "experience",
            "target_layer": "ExperienceDB",
            "candidate_title": f"{bill_name or 'unnamed_bill'} correction case",
            "candidate_summary": final_note[:300],
            "candidate_payload": {
                "province": _clean_str(experience_hint.get("province")) or province,
                "specialty": _clean_str(experience_hint.get("specialty")) or specialty,
                "bill_text": _clean_str(experience_hint.get("bill_text")) or bill_text,
                "bill_name": _clean_str(experience_hint.get("bill_name")) or bill_name,
                "bill_desc": _clean_str(experience_hint.get("bill_desc")) or bill_desc,
                "bill_code": _clean_str(experience_hint.get("bill_code")),
                "bill_unit": _clean_str(experience_hint.get("bill_unit")) or corrected_unit,
                "unit": _clean_str(experience_hint.get("unit")) or corrected_unit,
                "quota_ids": _clean_list(experience_hint.get("quota_ids")) or [corrected_id],
                "quota_names": _clean_list(experience_hint.get("quota_names")) or ([corrected_name] if corrected_name else []),
                "final_quota_code": _clean_str(experience_hint.get("final_quota_code")) or corrected_id,
                "final_quota_name": _clean_str(experience_hint.get("final_quota_name")) or corrected_name,
                "project_name": _clean_str(experience_hint.get("project_name")) or task_id,
                "summary": _clean_str(experience_hint.get("summary")) or final_note[:300],
                "notes": _clean_str(experience_hint.get("notes")) or "OpenClaw approved correction feedback",
                "confidence": int(experience_hint.get("confidence", 95) or 95),
                "judgment_basis": _clean_str(experience_hint.get("judgment_basis")) or final_note[:240],
            },
            "priority": 60,
            "approval_required": 1,
        })

    return candidates
