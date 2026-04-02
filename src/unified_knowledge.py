# -*- coding: utf-8 -*-
"""
Unified knowledge retrieval for experience cases, rule knowledge and method cards.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


_RULE_EXPLANATION_KEYWORDS = (
    "说明",
    "释义",
    "注",
    "适用",
    "工作内容",
    "章节",
    "子目",
    "范围",
)
_RULE_HARD_KEYWORDS = (
    "系数",
    "计算",
    "换算",
    "另计",
    "不包括",
    "包括",
    "不得",
    "应按",
    "执行",
    "计取",
    "乘以",
    "调整",
)


def _clean_inline(text: str, *, limit: int = 160) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    cleaned = re.sub(r"\s+", " ", raw)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


class UnifiedKnowledgeRetriever:
    """Aggregate three knowledge channels behind one retrieval entry."""

    def __init__(
        self,
        *,
        province: str = None,
        experience_db=None,
        rule_kb=None,
        method_cards_db=None,
        unified_data_layer=None,
    ):
        self.province = province
        self.experience_db = experience_db
        self.rule_kb = rule_kb
        self.method_cards_db = method_cards_db
        self.unified_data_layer = unified_data_layer

    def search_context(
        self,
        *,
        query_text: str,
        bill_name: str = "",
        bill_desc: str = "",
        province: str = None,
        specialty: str = "",
        unit: str = "",
        materials_signature: str = "",
        top_k_cases: int = 3,
        top_k_rules: int = 3,
        top_k_methods: int = 2,
        top_k_prices: int = 2,
    ) -> dict[str, Any]:
        province = province or self.province
        reference_cases = self._search_reference_cases(
            query_text=query_text,
            province=province,
            specialty=specialty,
            unit=unit,
            materials_signature=materials_signature,
            top_k=top_k_cases,
        )
        rules_context = self._search_rules(
            bill_name=bill_name,
            bill_desc=bill_desc,
            query_text=query_text,
            province=province,
            top_k=top_k_rules,
        )
        method_cards = self._search_method_cards(
            bill_name=bill_name,
            bill_desc=bill_desc,
            specialty=specialty,
            province=province,
            top_k=top_k_methods,
        )
        price_references = self._search_price_references(
            query_text=query_text,
            specialty=specialty,
            top_k=top_k_prices,
        )
        structured_rules = self._build_rule_knowledge(rules_context)
        structured_methods = self._build_method_knowledge(method_cards)
        structured_cases = self._build_reference_case_knowledge(reference_cases)
        structured_prices = self._build_price_reference_knowledge(price_references)

        knowledge_evidence = {
            "reference_cases": structured_cases,
            "quota_rules": structured_rules["quota_rules"],
            "quota_explanations": structured_rules["quota_explanations"],
            "method_cards": structured_methods,
            "price_references": structured_prices,
        }
        meta = {
            "reference_cases_count": len(reference_cases or []),
            "rules_context_count": len(rules_context or []),
            "method_cards_count": len(method_cards or []),
            "quota_rules_count": len(structured_rules["quota_rules"]),
            "quota_explanations_count": len(structured_rules["quota_explanations"]),
            "price_references_count": len(structured_prices),
        }
        return {
            "reference_cases": structured_cases,
            "rules_context": structured_rules["all_rules"],
            "method_cards": structured_methods,
            "price_references": structured_prices,
            "knowledge_evidence": knowledge_evidence,
            "meta": meta,
        }

    def _search_reference_cases(
        self,
        *,
        query_text: str,
        province: str = None,
        specialty: str = "",
        unit: str = "",
        materials_signature: str = "",
        top_k: int = 3,
    ) -> list[dict]:
        if not self.experience_db:
            return []
        try:
            if hasattr(self.experience_db, "search_experience"):
                records = self.experience_db.search_experience(
                    query_text,
                    top_k=max(top_k * 2, top_k),
                    min_confidence=70,
                    province=province,
                    specialty=specialty,
                    unit=unit,
                    materials_signature=materials_signature,
                )
                cases = []
                for record in records:
                    if record.get("gate") == "red":
                        continue
                    if record.get("match_type") in {"stale", "candidate"}:
                        continue
                    quota_strs = []
                    ids = record.get("quota_ids", []) or []
                    names = record.get("quota_names", []) or []
                    for index, qid in enumerate(ids):
                        name = names[index] if index < len(names) else ""
                        quota_strs.append(f"{qid} {name}".strip())
                    cases.append(
                        {
                            "record_id": record.get("id"),
                            "bill": record.get("bill_text", ""),
                            "quotas": quota_strs,
                            "confidence": record.get("confidence", 0),
                            "specialty": record.get("specialty", ""),
                            "gate": record.get("gate", ""),
                            "layer": record.get("layer", ""),
                        }
                    )
                return cases[:top_k]
            if hasattr(self.experience_db, "get_reference_cases"):
                return self.experience_db.get_reference_cases(
                    query_text,
                    top_k=top_k,
                    province=province,
                    specialty=specialty,
                )
        except Exception as exc:
            logger.debug(f"unified knowledge: reference case retrieval failed, continue degraded: {exc}")
        return []

    def _search_rules(
        self,
        *,
        bill_name: str,
        bill_desc: str,
        query_text: str,
        province: str = None,
        top_k: int = 3,
    ) -> list[dict]:
        if not self.rule_kb:
            return []
        try:
            query = " ".join(part for part in [bill_name, bill_desc, query_text] if str(part or "").strip()).strip()
            return self.rule_kb.search_rules(query, top_k=top_k, province=province)
        except Exception as exc:
            logger.debug(f"unified knowledge: rule retrieval failed, continue degraded: {exc}")
            return []

    def _search_method_cards(
        self,
        *,
        bill_name: str,
        bill_desc: str,
        specialty: str,
        province: str = None,
        top_k: int = 2,
    ) -> list[dict]:
        if not self.method_cards_db:
            return []
        try:
            return self.method_cards_db.find_relevant(
                bill_name,
                bill_desc,
                specialty=specialty,
                province=province,
                top_k=top_k,
            )
        except Exception as exc:
            logger.debug(f"unified knowledge: method card retrieval failed, continue degraded: {exc}")
            return []

    def _search_price_references(
        self,
        *,
        query_text: str,
        specialty: str,
        top_k: int = 2,
    ) -> list[dict]:
        if not self.unified_data_layer:
            return []
        try:
            result = self.unified_data_layer.search(
                {
                    "text": query_text,
                    "province": self.province,
                    "specialty": specialty,
                },
                sources=["price"],
                strategy="score",
                top_k=top_k,
            )
            return list((result.get("grouped") or {}).get("price") or [])[:top_k]
        except Exception as exc:
            logger.debug(f"unified knowledge: price retrieval failed, continue degraded: {exc}")
            return []

    def _build_reference_case_knowledge(self, cases: list[dict]) -> list[dict]:
        normalized = []
        for case in cases or []:
            if not isinstance(case, dict):
                continue
            normalized.append(
                {
                    "record_id": str(case.get("record_id", "") or "").strip(),
                    "bill": str(case.get("bill", "") or "").strip(),
                    "summary": _clean_inline(case.get("bill", ""), limit=100),
                    "quotas": [str(item).strip() for item in (case.get("quotas") or []) if str(item).strip()],
                    "confidence": case.get("confidence", 0),
                    "specialty": str(case.get("specialty", "") or "").strip(),
                    "gate": str(case.get("gate", "") or "").strip(),
                    "layer": str(case.get("layer", "") or "").strip(),
                }
            )
        return normalized

    def _build_rule_knowledge(self, rules: list[dict]) -> dict[str, list[dict]]:
        all_rules: list[dict] = []
        quota_rules: list[dict] = []
        quota_explanations: list[dict] = []

        for rule in rules or []:
            if not isinstance(rule, dict):
                continue
            normalized = self._normalize_rule(rule)
            all_rules.append(normalized)
            if normalized["rule_type"] == "quota_explanation":
                quota_explanations.append(normalized)
            else:
                quota_rules.append(normalized)

        return {
            "all_rules": all_rules,
            "quota_rules": quota_rules,
            "quota_explanations": quota_explanations,
        }

    def _normalize_rule(self, rule: dict) -> dict:
        chapter = str(rule.get("chapter", "") or "").strip()
        section = str(rule.get("section", "") or "").strip()
        content = str(rule.get("content", "") or "").strip()
        rule_type = self._classify_rule_type(chapter=chapter, section=section, content=content)
        title_parts = [part for part in [chapter, section] if part]
        return {
            "id": str(rule.get("id", "") or "").strip(),
            "province": str(rule.get("province", "") or "").strip(),
            "specialty": str(rule.get("specialty", "") or "").strip(),
            "chapter": chapter,
            "section": section,
            "title": " / ".join(title_parts),
            "content": content,
            "summary": _clean_inline(content),
            "rule_type": rule_type,
        }

    def _classify_rule_type(self, *, chapter: str, section: str, content: str) -> str:
        haystack = " ".join(part for part in [chapter, section, content] if part)
        if any(keyword in haystack for keyword in _RULE_HARD_KEYWORDS):
            return "quota_rule"
        if any(keyword in haystack for keyword in _RULE_EXPLANATION_KEYWORDS):
            return "quota_explanation"
        return "quota_rule"

    def _build_method_knowledge(self, cards: list[dict]) -> list[dict]:
        normalized = []
        for card in cards or []:
            if not isinstance(card, dict):
                continue
            universal_method = str(card.get("universal_method", "") or "").strip()
            method_text = str(card.get("method_text", "") or "").strip()
            normalized.append(
                {
                    "id": str(card.get("id", "") or "").strip(),
                    "category": str(card.get("category", "") or "").strip(),
                    "specialty": str(card.get("specialty", "") or "").strip(),
                    "scope": str(card.get("_scope", "") or "local").strip(),
                    "source_province": str(card.get("source_province", "") or "").strip(),
                    "summary": _clean_inline(universal_method or method_text),
                    "method_text": method_text,
                    "universal_method": universal_method,
                    "common_errors": str(card.get("common_errors", "") or "").strip(),
                }
            )
        return normalized

    def _build_price_reference_knowledge(self, prices: list[dict]) -> list[dict]:
        normalized = []
        for row in prices or []:
            if not isinstance(row, dict):
                continue
            raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
            normalized.append(
                {
                    "id": str(row.get("id", "") or raw.get("id", "") or "").strip(),
                    "title": str(row.get("title", "") or "").strip(),
                    "summary": _clean_inline(row.get("content", ""), limit=120),
                    "score": row.get("score", 0.0),
                    "quota_name": str(raw.get("quota_name", "") or "").strip(),
                    "unit": str(raw.get("unit", "") or "").strip(),
                    "price": raw.get("composite_unit_price", raw.get("price_value")),
                    "region": str(raw.get("region", "") or "").strip(),
                    "source_date": str(raw.get("source_date", "") or raw.get("price_date_iso", "") or "").strip(),
                }
            )
        return normalized
