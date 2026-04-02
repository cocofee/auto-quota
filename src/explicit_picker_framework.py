# -*- coding: utf-8 -*-
"""Shared framework for explicit family pickers."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Callable

from src.explicit_family_picker_utils import pick_best_candidate, score_candidate
from src.text_parser import parser as text_parser

BillContextBuilder = Callable[[str, dict], dict | None]
CandidateFilter = Callable[[dict, dict, dict], bool]
CandidateScoreAdjuster = Callable[[dict, dict, dict], int]


class ExplicitPickerFramework:
    """Rule-driven framework with hook points for explicit pickers."""

    RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "explicit_picker_rules.json"

    @classmethod
    @lru_cache(maxsize=1)
    def _load_rules(cls) -> dict[str, dict]:
        with cls.RULES_PATH.open("r", encoding="utf-8") as fh:
            return json.load(fh)

    def pick(
        self,
        bill_text: str,
        candidates: list[dict],
        picker_type: str,
        *,
        build_context: BillContextBuilder | None = None,
        candidate_filter: CandidateFilter | None = None,
        score_adjuster: CandidateScoreAdjuster | None = None,
    ) -> dict | None:
        rules = self._load_rules().get(picker_type)
        if not rules:
            return None

        text = str(bill_text or "")
        if not self._match_triggers(text, rules):
            return None

        context = build_context(text, rules) if build_context else {"bill_text": text}
        if context is None or context.get("abstain"):
            return None
        context.setdefault("bill_text", text)

        scored: list[tuple[tuple[int, float, float], dict]] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            quota_name = str(candidate.get("name", "") or "")
            if not quota_name:
                continue
            if not self._candidate_passes_static_filters(quota_name, rules):
                continue

            candidate_context = self._build_candidate_context(candidate)
            if candidate_filter and not candidate_filter(candidate, context, candidate_context):
                continue

            score = self._score_candidate_text(quota_name, context, rules)
            score += self._score_numeric_rules(context, candidate_context, rules)
            if score_adjuster:
                score += int(score_adjuster(candidate, context, candidate_context) or 0)

            if score <= 0:
                continue
            scored.append(score_candidate(candidate, score))

        return pick_best_candidate(scored)

    def _match_triggers(self, text: str, rules: dict) -> bool:
        trigger_any = [str(word) for word in rules.get("trigger_any", []) if str(word)]
        trigger_none = [str(word) for word in rules.get("trigger_none", []) if str(word)]
        if trigger_any and not any(word in text for word in trigger_any):
            return False
        if trigger_none and any(word in text for word in trigger_none):
            return False
        return True

    def _candidate_passes_static_filters(self, quota_name: str, rules: dict) -> bool:
        required_any = [str(word) for word in rules.get("candidate_require_any", []) if str(word)]
        forbidden_any = [str(word) for word in rules.get("candidate_forbid_any", []) if str(word)]
        if required_any and not any(word in quota_name for word in required_any):
            return False
        if forbidden_any and any(word in quota_name for word in forbidden_any):
            return False
        return True

    def _build_candidate_context(self, candidate: dict) -> dict:
        quota_name = str(candidate.get("name", "") or "")
        return {
            "quota_name": quota_name,
            "candidate_params": text_parser.parse(quota_name),
        }

    def _score_candidate_text(self, quota_name: str, context: dict, rules: dict) -> int:
        score = int(rules.get("base_score", 0) or 0)

        for group in rules.get("text_score_groups", []):
            words = self._resolve_context_words(context, str(group.get("context_key") or ""))
            if not words:
                continue
            weight = int(group.get("weight", 0) or 0)
            score += sum(weight for word in words if word and word in quota_name)

        for group in rules.get("text_penalty_groups", []):
            words = self._resolve_context_words(context, str(group.get("context_key") or ""))
            if not words:
                continue
            weight = int(group.get("weight", 0) or 0)
            score -= sum(weight for word in words if word and word in quota_name)

        return score

    def _score_numeric_rules(self, context: dict, candidate_context: dict, rules: dict) -> int:
        score = 0
        bill_params = context.get("bill_params") or {}
        candidate_params = candidate_context.get("candidate_params") or {}

        for rule in rules.get("numeric_rules", []):
            bill_field = str(rule.get("bill_field") or "")
            candidate_field = str(rule.get("candidate_field") or bill_field)
            if not bill_field or not candidate_field:
                continue

            bill_value = bill_params.get(bill_field)
            candidate_value = candidate_params.get(candidate_field)
            if bill_value is None or candidate_value is None:
                continue

            if bill_value == candidate_value:
                score += int(rule.get("equal", 0) or 0)
            elif candidate_value > bill_value:
                score += int(rule.get("greater", 0) or 0)
            else:
                score += int(rule.get("less", 0) or 0)

        return score

    def _resolve_context_words(self, context: dict, key: str) -> list[str]:
        value = context.get(key, [])
        if isinstance(value, str):
            return [value] if value else []
        if isinstance(value, list):
            return [str(word) for word in value if str(word)]
        return []
