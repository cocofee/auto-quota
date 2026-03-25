# -*- coding: utf-8 -*-
"""
Explicit promotion rules for staging candidate generation.

Current scope:
- OpenClaw approved-review -> audit_errors/promotion_queue admission rules
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
        parts.append(f"当清单项涉及“{bill_name}”")
    if bill_desc:
        parts.append(f"且特征为“{bill_desc[:120]}”")
    if original_quota.get("name") or original_quota.get("quota_id"):
        parts.append(
            f"若原候选为“{original_quota.get('name', '')}({original_quota.get('quota_id', '')})”"
        )
    if corrected_quota.get("name") or corrected_quota.get("quota_id"):
        parts.append(
            f"应优先复核并考虑改判为“{corrected_quota.get('name', '')}({corrected_quota.get('quota_id', '')})”"
        )
    if note:
        parts.append(f"依据：{note[:240]}")
    return "，".join(part for part in parts if part).strip("，")


def _build_method_text(
    bill_name: str,
    bill_desc: str,
    corrected_quota: dict[str, Any],
    note: str,
) -> str:
    parts: list[str] = []
    if bill_name:
        parts.append(f"审核“{bill_name}”时")
    else:
        parts.append("审核此类清单时")
    if bill_desc:
        parts.append(f"先结合特征“{bill_desc[:120]}”判断场景")
    if corrected_quota.get("name") or corrected_quota.get("quota_id"):
        parts.append(
            f"再优先核对“{corrected_quota.get('name', '')}({corrected_quota.get('quota_id', '')})”是否更符合"
        )
    if note:
        parts.append(f"人工审核依据：{note[:240]}")
    return "，".join(part for part in parts if part).strip("，")


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
) -> list[dict[str, Any]]:
    source = _normalize_source(match_source)
    changed = _quota_changed(original_quota, corrected_quota)
    note_ok = _has_meaningful_note(final_note)
    corrected_id = str((corrected_quota or {}).get("quota_id", "")).strip()
    corrected_name = str((corrected_quota or {}).get("name", "")).strip()
    corrected_unit = str((corrected_quota or {}).get("unit", "")).strip()
    if not changed or not note_ok or not corrected_id:
        return []

    candidates: list[dict[str, Any]] = []
    evidence_ref = f"task:{task_id}/result:{audit_id}"

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
            "candidate_title": f"{bill_name or '未命名清单'} 纠正规则候选",
            "candidate_summary": final_note[:300],
            "candidate_payload": {
                "province": province,
                "specialty": specialty,
                "chapter": "OpenClaw审核回流",
                "section": "",
                "source_file": f"staging:audit_errors:{audit_id}",
                "rule_text": _build_rule_candidate_text(
                    bill_name,
                    bill_desc,
                    original_quota,
                    corrected_quota,
                    final_note,
                ),
                "keywords": [kw for kw in [bill_name, corrected_name, corrected_id] if kw],
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
            "candidate_title": f"{bill_name or '未命名清单'} 审核方法候选",
            "candidate_summary": final_note[:300],
            "candidate_payload": {
                "province": province,
                "specialty": specialty,
                "category": bill_name or "OpenClaw审核方法",
                "method_text": _build_method_text(
                    bill_name,
                    bill_desc,
                    corrected_quota,
                    final_note,
                ),
                "keywords": [kw for kw in [bill_name, corrected_name, corrected_id] if kw],
                "pattern_keys": [kw for kw in [bill_name, specialty] if kw],
                "common_errors": f"避免误判为{original_quota.get('name', '')}".strip(),
                "sample_count": 1,
                "confirm_rate": 1.0,
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
            "candidate_title": f"{bill_name or '未命名清单'} 历史案例候选",
            "candidate_summary": final_note[:300],
            "candidate_payload": {
                "province": province,
                "specialty": specialty,
                "bill_text": bill_text,
                "bill_name": bill_name,
                "bill_desc": bill_desc,
                "bill_unit": corrected_unit,
                "unit": corrected_unit,
                "quota_ids": [corrected_id],
                "quota_names": [corrected_name] if corrected_name else [],
                "final_quota_code": corrected_id,
                "final_quota_name": corrected_name,
                "project_name": task_id,
                "summary": final_note[:300],
                "notes": "OpenClaw审核确认后回流",
                "confidence": 95,
            },
            "priority": 60,
            "approval_required": 1,
        })

    return candidates
