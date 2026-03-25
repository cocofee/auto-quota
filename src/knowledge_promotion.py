# -*- coding: utf-8 -*-
"""
Promotion services from knowledge staging into formal knowledge layers.

Current scope:
- RuleKnowledge
- MethodCards
- ExperienceDB
"""

from __future__ import annotations

from typing import Any

from src.experience_db import ExperienceDB
from src.knowledge_staging import KnowledgeStaging
from src.method_cards import MethodCards
from src.rule_knowledge import RuleKnowledge


class KnowledgePromotionService:
    """Promotion service from staging records into formal knowledge stores."""

    def __init__(self, staging: KnowledgeStaging | None = None):
        self.staging = staging or KnowledgeStaging()

    def _get_promotion_for_target(self, promotion_id: int, *, target_layer: str) -> dict[str, Any]:
        promotion = self.staging.get_promotion(promotion_id)
        if not promotion:
            raise ValueError(f"promotion not found: {promotion_id}")
        if promotion.get("target_layer") != target_layer:
            raise ValueError(f"only {target_layer} promotion is supported for this operation")
        if promotion.get("review_status") != "approved":
            raise ValueError("promotion must be approved before execution")
        if promotion.get("status") not in {"approved", "promoted"}:
            raise ValueError("promotion must be in approved state before execution")
        return promotion

    @staticmethod
    def _pick_text(payload: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _pick_list(payload: dict[str, Any], *keys: str) -> list[str]:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                items = [str(item).strip() for item in value if str(item).strip()]
                if items:
                    return items
            if isinstance(value, str):
                text = value.strip()
                if text:
                    items = [item.strip() for item in text.replace("；", "，").replace(";", "，").split("，") if item.strip()]
                    if items:
                        return items
        return []

    def _normalize_rule_payload(self, promotion: dict[str, Any]) -> dict[str, Any]:
        payload = dict(promotion.get("candidate_payload") or {})
        province = self._pick_text(payload, "province", "source_province")
        specialty = self._pick_text(payload, "specialty")
        chapter = self._pick_text(payload, "chapter", "category")
        section = self._pick_text(payload, "section")
        rule_text = self._pick_text(
            payload,
            "rule_text",
            "final_conclusion",
            "finalConclusion",
            "conclusion",
            "answer",
            "decision",
        )
        judgment_basis = self._pick_text(payload, "judgment_basis", "judgmentBasis", "decision_basis", "basis", "rationale")
        exclusion_reasons = self._pick_list(payload, "exclusion_reasons", "exclusionReasons", "excluded_reasons")
        core_points = self._pick_list(payload, "core_knowledge_points", "coreKnowledgePoints", "knowledge_points", "key_points")

        parts = [rule_text]
        if judgment_basis:
            parts.append(f"判断依据：{judgment_basis}")
        if exclusion_reasons:
            parts.append("排除理由：" + "；".join(exclusion_reasons))
        if core_points:
            parts.append("核心知识点：" + "；".join(core_points))

        normalized_rule_text = "\n".join(part for part in parts if part).strip()
        return {
            "province": province,
            "specialty": specialty,
            "chapter": chapter,
            "section": section,
            "source_file": self._pick_text(payload, "source_file", "source", "source_name") or f"staging:promotion_queue:{promotion.get('id')}",
            "rule_text": normalized_rule_text,
        }

    def _normalize_method_payload(self, promotion: dict[str, Any]) -> dict[str, Any]:
        payload = dict(promotion.get("candidate_payload") or {})
        category = self._pick_text(payload, "category", "suggested_promotion_type", "suggestedPromotionType") or str(promotion.get("candidate_title", "")).strip()
        method_text = self._pick_text(
            payload,
            "method_text",
            "final_conclusion",
            "finalConclusion",
            "conclusion",
            "answer",
            "decision",
        )
        judgment_basis = self._pick_text(payload, "judgment_basis", "judgmentBasis", "decision_basis", "basis", "rationale")
        original_problem = self._pick_text(payload, "original_problem", "originalProblem", "question", "problem")
        exclusion_reasons = self._pick_list(payload, "exclusion_reasons", "exclusionReasons", "excluded_reasons")
        core_points = self._pick_list(payload, "core_knowledge_points", "coreKnowledgePoints", "knowledge_points", "key_points")
        tags = self._pick_list(payload, "tags", "labels")

        method_parts = [method_text]
        if judgment_basis:
            method_parts.append(f"判断依据：{judgment_basis}")
        if original_problem:
            method_parts.append(f"适用问题：{original_problem}")
        if core_points:
            method_parts.append("核心知识点：" + "；".join(core_points))

        common_errors_parts = []
        if exclusion_reasons:
            common_errors_parts.append("排除理由：" + "；".join(exclusion_reasons))
        common_errors = "\n".join(common_errors_parts).strip()

        keywords = self._pick_list(payload, "keywords", "keyword_list")
        if not keywords:
            keywords = tags or core_points
        pattern_keys = self._pick_list(payload, "pattern_keys", "patternKeys")
        if not pattern_keys:
            pattern_keys = tags

        return {
            "province": self._pick_text(payload, "province", "source_province"),
            "specialty": self._pick_text(payload, "specialty"),
            "category": category,
            "method_text": "\n".join(part for part in method_parts if part).strip(),
            "keywords": keywords,
            "pattern_keys": pattern_keys,
            "common_errors": common_errors,
            "sample_count": int(payload.get("sample_count", 0) or 0),
            "confirm_rate": float(payload.get("confirm_rate", 0) or 0),
            "universal_method": self._pick_text(payload, "universal_method"),
        }

    def _normalize_experience_payload(self, promotion: dict[str, Any]) -> dict[str, Any]:
        payload = dict(promotion.get("candidate_payload") or {})
        final_quota_code = self._pick_text(payload, "final_quota_code", "quota_id")
        final_quota_name = self._pick_text(payload, "final_quota_name", "quota_name")
        summary = self._pick_text(payload, "summary", "final_conclusion", "conclusion")
        original_problem = self._pick_text(payload, "original_problem", "originalProblem", "question", "problem")
        bill_name = self._pick_text(payload, "bill_name", "candidate_title") or str(promotion.get("candidate_title", "")).strip()
        bill_desc = self._pick_text(payload, "bill_desc", "description")
        bill_text = self._pick_text(payload, "bill_text")
        if not bill_text:
            bill_text = " ".join(part for part in [bill_name, bill_desc, original_problem] if part).strip()
        notes_parts = [
            summary,
            self._pick_text(payload, "judgment_basis", "judgmentBasis", "decision_basis"),
        ]
        return {
            "province": self._pick_text(payload, "province", "source_province"),
            "bill_text": bill_text,
            "bill_name": bill_name,
            "bill_desc": bill_desc,
            "bill_code": self._pick_text(payload, "bill_code"),
            "bill_unit": self._pick_text(payload, "bill_unit", "unit"),
            "specialty": self._pick_text(payload, "specialty"),
            "materials": payload.get("materials") if isinstance(payload.get("materials"), list) else [],
            "project_name": self._pick_text(payload, "project_name"),
            "notes": "\n".join(part for part in notes_parts if part).strip(),
            "confidence": int(payload.get("confidence", 95) or 95),
            "quota_ids": self._pick_list(payload, "quota_ids") or ([final_quota_code] if final_quota_code else []),
            "quota_names": self._pick_list(payload, "quota_names") or ([final_quota_name] if final_quota_name else []),
        }

    def _mark_promoted(self,
                       promotion: dict[str, Any],
                       *,
                       promotion_id: int,
                       target_layer: str,
                       promoted_target_id: str,
                       promoted_target_ref: str,
                       added: bool,
                       skipped: bool) -> dict[str, Any]:
        trace = (
            f"{promotion.get('source_table', '')}:{promotion.get('source_record_id', '')}"
            f" -> {target_layer}:{promoted_target_id}"
        )
        self.staging.mark_promotion_promoted(
            promotion_id,
            promoted_target_id=str(promoted_target_id or ""),
            promoted_target_ref=promoted_target_ref,
            target_version=1,
            promotion_trace=trace,
        )

        if promotion.get("source_table") == "audit_errors":
            try:
                self.staging.update_audit_error_status(
                    int(promotion.get("source_record_id", 0)),
                    review_status="promoted",
                    review_comment=trace,
                )
            except Exception:
                pass

        return {
            "promotion_id": promotion_id,
            "trace": trace,
            "added": added,
            "skipped": skipped,
        }

    def _get_promoted_promotion_for_rollback(self, promotion_id: int, *, target_layer: str) -> dict[str, Any]:
        promotion = self.staging.get_promotion(promotion_id)
        if not promotion:
            raise ValueError(f"promotion not found: {promotion_id}")
        if promotion.get("target_layer") != target_layer:
            raise ValueError(f"only {target_layer} promotions currently support rollback")
        if promotion.get("status") != "promoted":
            raise ValueError("promotion must be in promoted state before rollback")
        if not str(promotion.get("promoted_target_id", "")).strip():
            raise ValueError("promotion has no promoted target id")
        return promotion

    def _mark_rolled_back(self,
                          promotion: dict[str, Any],
                          *,
                          promotion_id: int,
                          target_layer: str,
                          promoted_target_id: str,
                          actor: str,
                          reason_text: str,
                          destination: str) -> dict[str, Any]:
        existing_trace = str(promotion.get("promotion_trace", "") or "").strip()
        rollback_trace = (
            f"{existing_trace}\n"
            f"ROLLBACK {target_layer}:{promoted_target_id} -> {destination}"
        ).strip()
        review_comment_parts = []
        if str(promotion.get("review_comment", "")).strip():
            review_comment_parts.append(str(promotion.get("review_comment", "")).strip())
        review_comment_parts.append(f"Rollback: {reason_text}")
        review_comment = "\n".join(review_comment_parts)

        self.staging.mark_promotion_rolled_back(
            promotion_id,
            reviewer=actor,
            review_comment=review_comment,
            promotion_trace=rollback_trace,
        )
        if promotion.get("source_table") == "audit_errors":
            try:
                audit_id = int(str(promotion.get("source_record_id", "")).split(":")[0])
                audit = self.staging.get_audit_error(audit_id) or {}
                audit_comment_parts = []
                if str(audit.get("review_comment", "")).strip():
                    audit_comment_parts.append(str(audit.get("review_comment", "")).strip())
                audit_comment_parts.append(f"Rollback: {reason_text}")
                self.staging.update_audit_error_status(
                    audit_id,
                    review_comment="\n".join(audit_comment_parts),
                )
            except Exception:
                pass

        return {
            "promotion_id": promotion_id,
            "target_layer": target_layer,
            "rolled_back": True,
            "trace": rollback_trace,
        }

    def promote_rule_candidate(self, promotion_id: int) -> dict[str, Any]:
        """
        Promote one approved staging candidate into RuleKnowledge.

        Expected candidate payload:
        {
            "province": "...",
            "specialty": "...",
            "rule_text": "...",
            "chapter": "...",
            "section": "...",
            "source_file": "..."
        }
        """
        promotion = self._get_promotion_for_target(promotion_id, target_layer="RuleKnowledge")
        if promotion.get("status") == "promoted":
            return {
                "promotion_id": promotion_id,
                "rule_id": promotion.get("promoted_target_id", ""),
                "already_promoted": True,
            }

        payload = self._normalize_rule_payload(promotion)
        province = str(payload.get("province", "")).strip()
        specialty = str(payload.get("specialty", "")).strip()
        chapter = str(payload.get("chapter", "")).strip()
        section = str(payload.get("section", "")).strip()
        source_file = str(payload.get("source_file", "")).strip() or f"staging:promotion_queue:{promotion_id}"
        rule_text = str(payload.get("rule_text", "")).strip()
        if not province or not rule_text:
            raise ValueError("候选内容缺少可晋升规则所需字段：需要 province 和可提炼的规则正文")

        kb = RuleKnowledge(province=province)
        write_result = kb.add_rule_text(
            content=rule_text,
            province=province,
            specialty=specialty,
            chapter=chapter,
            section=section,
            source_file=source_file,
        )
        rule_id = write_result.get("rule_id")
        result = self._mark_promoted(
            promotion,
            promotion_id=promotion_id,
            target_layer="RuleKnowledge",
            promoted_target_id=str(rule_id or ""),
            promoted_target_ref=f"rule_knowledge:{rule_id}" if rule_id else "",
            added=bool(write_result.get("added")),
            skipped=bool(write_result.get("skipped")),
        )
        result["rule_id"] = rule_id
        return result

    def promote_method_candidate(self, promotion_id: int) -> dict[str, Any]:
        """
        Promote one approved staging candidate into MethodCards.

        Expected candidate payload:
        {
            "province": "...",
            "specialty": "...",
            "category": "...",
            "method_text": "...",
            "keywords": ["..."],
            "pattern_keys": ["..."],
            "common_errors": "...",
            "sample_count": 0,
            "confirm_rate": 0
        }
        """
        promotion = self._get_promotion_for_target(promotion_id, target_layer="MethodCards")
        if promotion.get("status") == "promoted":
            return {
                "promotion_id": promotion_id,
                "card_id": promotion.get("promoted_target_id", ""),
                "already_promoted": True,
            }

        payload = self._normalize_method_payload(promotion)
        province = str(payload.get("province", "")).strip()
        specialty = str(payload.get("specialty", "")).strip()
        category = str(payload.get("category", "")).strip() or str(promotion.get("candidate_title", "")).strip()
        method_text = str(payload.get("method_text", "")).strip()
        keywords = payload.get("keywords") or []
        pattern_keys = payload.get("pattern_keys") or []
        common_errors = str(payload.get("common_errors", "")).strip()
        sample_count = int(payload.get("sample_count", 0) or 0)
        confirm_rate = float(payload.get("confirm_rate", 0) or 0)
        universal_method = str(payload.get("universal_method", "")).strip()
        if not category or not method_text:
            raise ValueError("候选内容缺少可晋升方法卡所需字段：需要可提炼的方法结论")

        mc = MethodCards()
        write_result = mc.add_method_text(
            category=category,
            specialty=specialty,
            method_text=method_text,
            keywords=keywords if isinstance(keywords, list) else [],
            pattern_keys=pattern_keys if isinstance(pattern_keys, list) else [],
            common_errors=common_errors,
            sample_count=sample_count,
            confirm_rate=confirm_rate,
            source_province=province,
            universal_method=universal_method,
        )
        card_id = write_result.get("card_id")
        result = self._mark_promoted(
            promotion,
            promotion_id=promotion_id,
            target_layer="MethodCards",
            promoted_target_id=str(card_id or ""),
            promoted_target_ref=f"method_cards:{card_id}" if card_id else "",
            added=bool(write_result.get("added")),
            skipped=bool(write_result.get("skipped")),
        )
        result["card_id"] = card_id
        return result

    def promote_experience_candidate(self, promotion_id: int) -> dict[str, Any]:
        """
        Promote one approved staging candidate into ExperienceDB.

        Expected candidate payload:
        {
            "province": "...",
            "bill_text": "...",
            "bill_name": "...",
            "bill_desc": "...",
            "bill_code": "...",
            "bill_unit": "...",
            "unit": "...",
            "quota_ids": ["..."],
            "quota_names": ["..."],
            "final_quota_code": "...",
            "final_quota_name": "...",
            "specialty": "...",
            "materials": [],
            "project_name": "...",
            "summary": "...",
            "notes": "...",
            "confidence": 0
        }
        """
        promotion = self._get_promotion_for_target(promotion_id, target_layer="ExperienceDB")
        if promotion.get("status") == "promoted":
            return {
                "promotion_id": promotion_id,
                "experience_id": promotion.get("promoted_target_id", ""),
                "already_promoted": True,
            }

        payload = self._normalize_experience_payload(promotion)
        province = str(payload.get("province", "")).strip()
        bill_text = str(payload.get("bill_text", "")).strip()
        bill_name = str(payload.get("bill_name", "")).strip()
        bill_desc = str(payload.get("bill_desc", "")).strip()
        if not bill_text:
            bill_text = " ".join(part for part in [bill_name, bill_desc] if part).strip()

        def _string_list(value: Any) -> list[str]:
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            if isinstance(value, str) and value.strip():
                return [value.strip()]
            return []

        raw_quota_ids = _string_list(payload.get("quota_ids"))
        raw_quota_names = _string_list(payload.get("quota_names"))
        if not raw_quota_ids:
            final_quota_code = str(payload.get("final_quota_code", "")).strip()
            if final_quota_code:
                raw_quota_ids = [final_quota_code]
        if not raw_quota_names:
            final_quota_name = str(payload.get("final_quota_name", "")).strip()
            if final_quota_name:
                raw_quota_names = [final_quota_name]

        quota_ids = _string_list(raw_quota_ids)
        quota_names = _string_list(raw_quota_names)
        bill_unit = str(payload.get("bill_unit", "") or payload.get("unit", "")).strip()
        specialty = str(payload.get("specialty", "")).strip()
        project_name = str(payload.get("project_name", "")).strip()
        materials = payload.get("materials") if isinstance(payload.get("materials"), list) else []
        try:
            confidence = int(float(payload.get("confidence", 95) or 95))
        except (TypeError, ValueError):
            confidence = 95

        if not province or not bill_text or not quota_ids:
            raise ValueError("候选内容缺少可晋升经验所需字段：需要 province、可检索 bill_text 和 quota_ids")

        note_parts: list[str] = [f"[staging_ref] promotion_queue:{promotion_id}"]
        summary = str(payload.get("summary", "")).strip()
        if summary:
            note_parts.append(f"[summary] {summary}")
        explicit_notes = str(payload.get("notes", "")).strip()
        if explicit_notes:
            note_parts.append(f"[notes] {explicit_notes}")
        evidence_ref = str(promotion.get("evidence_ref", "")).strip()
        if evidence_ref:
            note_parts.append(f"[evidence] {evidence_ref}")

        db = ExperienceDB(province=province)
        write_result = db.add_experience_text(
            province=province,
            bill_text=bill_text,
            bill_name=bill_name,
            bill_code=str(payload.get("bill_code", "")).strip(),
            bill_unit=bill_unit,
            quota_ids=quota_ids,
            quota_names=quota_names,
            specialty=specialty,
            materials=materials,
            project_name=project_name,
            notes="\n".join(note_parts),
            confidence=confidence,
        )
        experience_id = write_result.get("experience_id")
        result = self._mark_promoted(
            promotion,
            promotion_id=promotion_id,
            target_layer="ExperienceDB",
            promoted_target_id=str(experience_id or ""),
            promoted_target_ref=f"experience_db:{experience_id}" if experience_id else "",
            added=bool(write_result.get("added")),
            skipped=bool(write_result.get("skipped")),
        )
        result["experience_id"] = experience_id
        return result

    def rollback_experience_candidate(self, promotion_id: int, *, reason: str = "", actor: str = "") -> dict[str, Any]:
        """Rollback one promoted ExperienceDB record back to candidate layer."""
        promotion = self._get_promoted_promotion_for_rollback(
            promotion_id,
            target_layer="ExperienceDB",
        )
        promoted_target_id = str(promotion.get("promoted_target_id", "")).strip()
        payload = promotion.get("candidate_payload") or {}
        province = str(payload.get("province", "")).strip()
        if not province:
            raise ValueError("experience rollback requires candidate payload province")

        db = ExperienceDB(province=province)
        reason_text = str(reason or "").strip() or f"rollback from staging promotion {promotion_id}"
        rolled_back = db.demote_to_candidate(int(promoted_target_id), reason=reason_text)
        if not rolled_back:
            raise ValueError("target experience record not found or already demoted")

        result = self._mark_rolled_back(
            promotion,
            promotion_id=promotion_id,
            target_layer="ExperienceDB",
            promoted_target_id=promoted_target_id,
            actor=actor,
            reason_text=reason_text,
            destination="candidate",
        )
        result["experience_id"] = int(promoted_target_id)
        return result

    def rollback_rule_candidate(self, promotion_id: int, *, reason: str = "", actor: str = "") -> dict[str, Any]:
        """Soft-rollback one promoted RuleKnowledge record out of active retrieval."""
        promotion = self._get_promoted_promotion_for_rollback(
            promotion_id,
            target_layer="RuleKnowledge",
        )
        promoted_target_id = str(promotion.get("promoted_target_id", "")).strip()
        payload = promotion.get("candidate_payload") or {}
        province = str(payload.get("province", "")).strip()
        kb = RuleKnowledge(province=province or None)
        reason_text = str(reason or "").strip() or f"rollback from staging promotion {promotion_id}"
        rolled_back = kb.soft_disable_rule(int(promoted_target_id), reason=reason_text, actor=actor)
        if not rolled_back:
            raise ValueError("target rule record not found or already disabled")

        result = self._mark_rolled_back(
            promotion,
            promotion_id=promotion_id,
            target_layer="RuleKnowledge",
            promoted_target_id=promoted_target_id,
            actor=actor,
            reason_text=reason_text,
            destination="inactive",
        )
        result["rule_id"] = int(promoted_target_id)
        return result

    def rollback_method_candidate(self, promotion_id: int, *, reason: str = "", actor: str = "") -> dict[str, Any]:
        """Soft-rollback one promoted MethodCards record out of active retrieval."""
        promotion = self._get_promoted_promotion_for_rollback(
            promotion_id,
            target_layer="MethodCards",
        )
        promoted_target_id = str(promotion.get("promoted_target_id", "")).strip()
        mc = MethodCards()
        reason_text = str(reason or "").strip() or f"rollback from staging promotion {promotion_id}"
        rolled_back = mc.soft_disable_card(int(promoted_target_id), reason=reason_text, actor=actor)
        if not rolled_back:
            raise ValueError("target method card not found or already disabled")

        result = self._mark_rolled_back(
            promotion,
            promotion_id=promotion_id,
            target_layer="MethodCards",
            promoted_target_id=promoted_target_id,
            actor=actor,
            reason_text=reason_text,
            destination="inactive",
        )
        result["card_id"] = int(promoted_target_id)
        return result
