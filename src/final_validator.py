from __future__ import annotations

from dataclasses import dataclass

from src.quota_search import search_by_id
from src.review_checkers import (
    check_category_mismatch,
    check_connection_mismatch,
    check_electric_pair,
    check_elevator_floor,
    check_elevator_type,
    check_material_mismatch,
    check_parameter_deviation,
    check_pipe_usage,
    check_sleeve_mismatch,
    extract_description_lines,
    extract_dn,
)
from src.review_correctors import correct_error


_UNIT_ALIASES = {
    "m": "m",
    "米": "m",
    "M": "m",
    "m2": "m2",
    "m²": "m2",
    "㎡": "m2",
    "平方米": "m2",
    "平米": "m2",
    "m3": "m3",
    "m³": "m3",
    "立方米": "m3",
    "kg": "kg",
    "千克": "kg",
    "公斤": "kg",
    "t": "t",
    "吨": "t",
}

_UNIT_FAMILY = {
    "m": "length",
    "m2": "area",
    "m3": "volume",
    "kg": "weight",
    "t": "weight",
    "台": "count",
    "套": "count",
    "组": "count",
    "个": "count",
    "只": "count",
    "樘": "count",
    "件": "count",
    "项": "item",
}


@dataclass(frozen=True)
class ValidationIssue:
    issue_type: str
    severity: str
    message: str

    def as_dict(self) -> dict:
        return {
            "type": self.issue_type,
            "severity": self.severity,
            "message": self.message,
        }


def _normalize_unit(unit: str) -> str:
    text = str(unit or "").strip()
    return _UNIT_ALIASES.get(text, text)


def _unit_family(unit: str) -> str:
    return _UNIT_FAMILY.get(_normalize_unit(unit), "")


def _candidate_features(quota: dict) -> dict:
    if not isinstance(quota, dict):
        return {}
    return (
        quota.get("candidate_canonical_features")
        or quota.get("canonical_features")
        or {}
    )


class FinalValidator:
    def __init__(self, *, province: str | None = None, auto_correct: bool = True):
        self.province = province
        self.auto_correct = auto_correct

    def validate_results(self, results: list[dict]) -> list[dict]:
        for result in results or []:
            self.validate_result(result)
        return results

    def validate_result(self, result: dict) -> dict:
        if not isinstance(result, dict):
            return result

        item = result.get("bill_item") or {}
        issues: list[ValidationIssue] = []
        corrected = False

        ambiguity_issue = self._check_reasoning_review(result)
        if ambiguity_issue:
            issues.append(ambiguity_issue)
            self._cap_confidence(result, cap=78)

        unit_issue = self._check_unit_conflict(item, result)
        if unit_issue:
            issues.append(unit_issue)
            self._cap_confidence(result, cap=68)

        anchor_issue = self._check_anchor_conflict(item, result)
        if anchor_issue:
            issues.append(anchor_issue)
            self._cap_confidence(result, cap=64)

        review_error = self._check_review_error(item, result)
        if review_error:
            correction = self._try_auto_correct(item, result, review_error)
            if correction:
                corrected = True
                issues.append(ValidationIssue(
                    issue_type="review_corrected",
                    severity="info",
                    message=f"{review_error.get('type', '')} -> {correction['quota_id']}",
                ))
                result["final_review_correction"] = correction
                self._cap_confidence(result, floor=72, cap=86)
            else:
                issues.append(ValidationIssue(
                    issue_type=review_error.get("type", "review_conflict"),
                    severity="error",
                    message=review_error.get("reason", "审核规则冲突"),
                ))
                self._cap_confidence(result, cap=62)

        status = "ok"
        if corrected:
            status = "corrected"
        elif issues:
            status = "manual_review"

        result["final_validation"] = {
            "status": status,
            "corrected": corrected,
            "issues": [issue.as_dict() for issue in issues],
        }
        return result

    def _check_reasoning_review(self, result: dict) -> ValidationIssue | None:
        decision = result.get("reasoning_decision") or {}
        if not isinstance(decision, dict):
            return None
        if not bool(decision.get("require_final_review")):
            return None

        reason = str(decision.get("reason") or "ambiguous")
        risk_level = str(decision.get("risk_level") or "medium")
        route = str(decision.get("route") or "")
        return ValidationIssue(
            issue_type="ambiguity_review",
            severity="warning" if risk_level != "high" else "error",
            message=f"reason={reason}; risk={risk_level}; route={route}",
        )

    def _check_unit_conflict(self, item: dict, result: dict) -> ValidationIssue | None:
        quotas = result.get("quotas") or []
        if not quotas:
            return None
        bill_unit = str(item.get("unit") or "").strip()
        quota_unit = str(quotas[0].get("unit") or "").strip()
        if not bill_unit or not quota_unit:
            return None

        bill_family = _unit_family(bill_unit)
        quota_family = _unit_family(quota_unit)
        if not bill_family or not quota_family or bill_family == quota_family:
            return None

        return ValidationIssue(
            issue_type="unit_conflict",
            severity="error",
            message=f"清单单位{bill_unit}与定额单位{quota_unit}不一致",
        )

    def _check_anchor_conflict(self, item: dict, result: dict) -> ValidationIssue | None:
        quotas = result.get("quotas") or []
        if not quotas:
            return None

        item_features = item.get("canonical_features") or {}
        quota_features = _candidate_features(quotas[0])
        if not isinstance(item_features, dict) or not isinstance(quota_features, dict):
            return None

        conflicts = []
        for field, label in (
            ("entity", "构件"),
            ("system", "系统"),
            ("material", "材质"),
            ("connection", "连接"),
        ):
            item_value = str(item_features.get(field) or "").strip()
            quota_value = str(quota_features.get(field) or "").strip()
            if item_value and quota_value and item_value != quota_value:
                conflicts.append(f"{label}:{item_value}/{quota_value}")

        if not conflicts:
            return None

        return ValidationIssue(
            issue_type="anchor_conflict",
            severity="error",
            message="属性锚点冲突: " + "; ".join(conflicts[:3]),
        )

    def _check_review_error(self, item: dict, result: dict) -> dict | None:
        quotas = result.get("quotas") or []
        if not quotas:
            return None

        main_quota = quotas[0]
        quota_name = str(main_quota.get("name") or "").strip()
        quota_id = str(main_quota.get("quota_id") or "").strip()
        if not quota_name:
            return None

        desc_lines = extract_description_lines(item.get("description", "") or "")
        errors = [
            check_category_mismatch(item, quota_name, desc_lines),
            check_sleeve_mismatch(item, quota_name, desc_lines),
            check_material_mismatch(item, quota_name, desc_lines),
            check_connection_mismatch(item, quota_name, desc_lines),
            check_pipe_usage(item, quota_name, desc_lines),
            check_parameter_deviation(item, quota_name, desc_lines),
            check_electric_pair(item, quota_name, desc_lines),
            check_elevator_type(item, quota_name, desc_lines),
            check_elevator_floor(item, quota_name, desc_lines, quota_id=quota_id),
        ]
        return next((error for error in errors if error), None)

    def _try_auto_correct(self, item: dict, result: dict, review_error: dict) -> dict | None:
        if not self.auto_correct:
            return None

        dn = extract_dn(f"{item.get('name', '')} {item.get('description', '')}".strip())
        corrected = correct_error(item, review_error, dn, province=self.province)
        if not corrected:
            return None

        quotas = result.get("quotas") or []
        if not quotas:
            return None

        corrected_province = corrected.get("province") or self.province
        corrected_row = search_by_id(corrected["quota_id"], province=corrected_province)
        corrected_unit = quotas[0].get("unit", "")
        if corrected_row and len(corrected_row) >= 3:
            corrected_unit = corrected_row[2]

        quotas[0]["quota_id"] = corrected["quota_id"]
        quotas[0]["name"] = corrected["quota_name"]
        quotas[0]["unit"] = corrected_unit
        quotas[0]["reason"] = review_error.get("reason", "")
        return corrected

    @staticmethod
    def _cap_confidence(result: dict, *, floor: int | None = None, cap: int | None = None):
        try:
            current = int(result.get("confidence", 0) or 0)
        except (TypeError, ValueError):
            current = 0
        if floor is not None:
            current = max(current, floor)
        if cap is not None:
            current = min(current, cap)
        result["confidence"] = current
