import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "web" / "backend"
for candidate in (PROJECT_ROOT, BACKEND_ROOT):
    candidate_str = str(candidate)
    if candidate_str not in sys.path:
        sys.path.insert(0, candidate_str)

from app.api.results import (
    _extract_knowledge_basis,
    _extract_knowledge_evidence,
    _extract_knowledge_summary,
    _to_result_response,
)
from app.services.match_service import _compact_trace


def test_compact_trace_keeps_knowledge_fields():
    trace = _compact_trace(
        {
            "path": ["agent_llm", "final_validate"],
            "final_source": "agent",
            "final_confidence": 93,
            "steps": [
                {
                    "stage": "agent_llm",
                    "reference_cases_count": 1,
                    "reference_case_ids": ["11"],
                    "rules_context_count": 2,
                    "rule_context_ids": ["rule_1", "rule_2"],
                    "quota_rules_count": 1,
                    "quota_rule_ids": ["rule_2"],
                    "quota_explanations_count": 1,
                    "quota_explanation_ids": ["rule_1"],
                    "method_cards_count": 1,
                    "method_card_ids": ["7"],
                    "knowledge_evidence": {
                        "reference_cases": [{"record_id": "11"}],
                        "quota_rules": [{"id": "rule_2"}],
                        "quota_explanations": [{"id": "rule_1"}],
                        "method_cards": [{"id": "7"}],
                    },
                    "knowledge_basis": {
                        "rule_ids": ["rule_2"],
                        "method_card_ids": ["7"],
                    },
                    "knowledge_summary": {
                        "quota_rules_count": 1,
                        "quota_explanations_count": 1,
                    },
                }
            ],
        }
    )

    step = trace["steps"][0]
    assert step["reference_case_ids"] == ["11"]
    assert step["rule_context_ids"] == ["rule_1", "rule_2"]
    assert step["quota_rule_ids"] == ["rule_2"]
    assert step["quota_explanation_ids"] == ["rule_1"]
    assert step["method_card_ids"] == ["7"]
    assert step["knowledge_evidence"]["quota_rules"][0]["id"] == "rule_2"
    assert step["knowledge_basis"]["method_card_ids"] == ["7"]


def test_extract_knowledge_evidence_prefers_structured_payload():
    match_result = SimpleNamespace(
        trace={
            "steps": [
                {
                    "stage": "agent_llm",
                    "knowledge_evidence": {
                        "reference_cases": [{"record_id": "11"}],
                        "quota_rules": [{"id": "rule_2"}],
                        "quota_explanations": [{"id": "rule_1"}],
                        "method_cards": [{"id": "7"}],
                    }
                }
            ]
        }
    )

    evidence = _extract_knowledge_evidence(match_result)
    assert evidence["reference_cases"][0]["record_id"] == "11"
    assert evidence["quota_rules"][0]["id"] == "rule_2"
    assert evidence["quota_explanations"][0]["id"] == "rule_1"
    assert evidence["method_cards"][0]["id"] == "7"


def test_extract_knowledge_basis_and_summary_fallback_from_trace_ids():
    match_result = SimpleNamespace(
        trace={
            "steps": [
                {
                    "stage": "agent_llm",
                    "reference_case_ids": ["11"],
                    "rule_context_ids": ["rule_1"],
                    "quota_rule_ids": ["rule_2"],
                    "quota_explanation_ids": ["rule_1"],
                    "method_card_ids": ["7"],
                }
            ]
        }
    )

    basis = _extract_knowledge_basis(match_result)
    summary = _extract_knowledge_summary(match_result)

    assert basis == {
        "reference_case_ids": ["11"],
        "rule_ids": ["rule_2", "rule_1"],
        "method_card_ids": ["7"],
    }
    assert summary == {
        "reference_cases_count": 1,
        "quota_rules_count": 1,
        "quota_explanations_count": 1,
        "method_cards_count": 1,
    }


def test_to_result_response_includes_knowledge_meta_and_trace():
    trace = {
        "path": ["agent_llm", "final_validate"],
        "final_source": "agent",
        "final_confidence": 93,
        "steps": [
            {
                "stage": "agent_llm",
                "knowledge_evidence": {
                    "reference_cases": [{"record_id": "11"}],
                    "quota_rules": [{"id": "rule_2"}],
                    "quota_explanations": [{"id": "rule_1"}],
                    "method_cards": [{"id": "7"}],
                },
                "knowledge_basis": {
                    "reference_case_ids": ["11"],
                    "rule_ids": ["rule_2"],
                    "method_card_ids": ["7"],
                },
                "knowledge_summary": {
                    "reference_cases_count": 1,
                    "quota_rules_count": 1,
                    "quota_explanations_count": 1,
                    "method_cards_count": 1,
                },
            }
        ],
    }
    result = SimpleNamespace(
        id=uuid.uuid4(),
        index=0,
        bill_code="031001005001",
        bill_name="给排水管道",
        bill_description="室内铸铁排水管",
        bill_unit="m",
        bill_quantity=1.0,
        bill_unit_price=None,
        bill_amount=None,
        specialty="C10",
        sheet_name="表-01",
        section="给排水",
        quotas=[{"quota_id": "2-10-1-229", "name": "铸铁排水管", "unit": "10m"}],
        alternatives=None,
        confidence=88,
        confidence_score=88,
        review_risk="low",
        light_status="yellow",
        match_source="rule",
        explanation="规则库命中",
        candidates_count=5,
        is_measure_item=False,
        review_status="pending",
        corrected_quotas=None,
        review_note="",
        openclaw_review_status="pending",
        openclaw_suggested_quotas=None,
        openclaw_review_note="",
        openclaw_review_confidence=None,
        openclaw_review_actor="",
        openclaw_review_time=None,
        openclaw_review_confirm_status="pending",
        openclaw_review_confirmed_by="",
        openclaw_review_confirm_time=None,
        created_at=datetime.now(UTC),
        trace=trace,
    )

    resp = _to_result_response(result)

    assert resp.knowledge_evidence["quota_rules"][0]["id"] == "rule_2"
    assert resp.knowledge_basis["rule_ids"] == ["rule_2"]
    assert resp.knowledge_summary["method_cards_count"] == 1
    assert resp.trace["path"] == ["agent_llm", "final_validate"]
