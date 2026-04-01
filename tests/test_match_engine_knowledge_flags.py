from __future__ import annotations

import config
import sys
import types

from src import match_engine
from src.agent_matcher import AgentMatcher
from src.match_pipeline import _apply_rule_backup
from src.param_validator import ParamValidator


class _StubReasoningAgent:
    def build_packet(self, *args, **kwargs):
        return {
            "engaged": False,
            "conflict_summaries": [],
            "compare_points": [],
            "decision": {},
        }


class _CapturingAgent:
    def __init__(self):
        self.calls = []

    def match_single(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "bill_item": kwargs["bill_item"],
            "quotas": [{"quota_id": "Q-1", "name": "quota", "unit": "m"}],
            "confidence": 86,
            "explanation": "ok",
            "match_source": "agent",
        }


class _StubKnowledgeRetriever:
    def search_context(self, **kwargs):
        return {
            "reference_cases": [{"record_id": "11", "bill": "镀锌钢管 DN20"}],
            "rules_context": [
                {"id": "rule_ctx_1", "title": "规则正文", "content": "正文"},
                {"id": "rule_ctx_2", "title": "章节说明", "content": "说明"},
            ],
            "method_cards": [{"id": "card_1", "category": "管道安装", "summary": "先材质后连接"}],
            "knowledge_evidence": {
                "reference_cases": [{"record_id": "11", "bill": "镀锌钢管 DN20"}],
                "quota_rules": [{"id": "rule_ctx_1", "title": "规则正文"}],
                "quota_explanations": [{"id": "rule_ctx_2", "title": "章节说明"}],
                "method_cards": [{"id": "card_1", "category": "管道安装", "summary": "先材质后连接"}],
            },
            "meta": {
                "reference_cases_count": 1,
                "quota_rules_count": 1,
                "quota_explanations_count": 1,
                "method_cards_count": 1,
            },
        }


def test_resolve_agent_mode_result_keeps_knowledge_trace_when_prompt_flags_disabled(monkeypatch):
    agent = _CapturingAgent()
    monkeypatch.setattr(match_engine, "ReasoningAgent", lambda: _StubReasoningAgent())
    monkeypatch.setattr(match_engine.config, "AGENT_RULES_IN_PROMPT", False)
    monkeypatch.setattr(match_engine.config, "AGENT_METHOD_CARDS_IN_PROMPT", False)

    item = {"name": "镀锌钢管 DN20", "description": "", "unit": "m", "specialty": "C10"}
    result, _, _ = match_engine._resolve_agent_mode_result(
        agent=agent,
        item=item,
        candidates=[{"quota_id": "Q-1", "name": "quota", "param_match": True, "param_score": 0.9}],
        experience_db=None,
        canonical_query={"validation_query": "镀锌钢管 DN20", "search_query": "镀锌钢管 DN20"},
        rule_kb=None,
        name=item["name"],
        desc=item["description"],
        exp_backup={},
        rule_backup={},
        exp_hits=0,
        rule_hits=0,
        full_query="镀锌钢管 DN20",
        search_query="镀锌钢管 DN20",
        province="测试省",
        unified_knowledge_retriever=_StubKnowledgeRetriever(),
        unified_knowledge_cache={},
        reference_cases_cache={},
        rules_context_cache={},
        method_cards_db=object(),
        overview_context="",
    )

    prompt_call = agent.calls[0]
    assert prompt_call["rules_context"] is None
    assert prompt_call["method_cards"] is None
    assert prompt_call["knowledge_evidence"]["reference_cases"][0]["record_id"] == "11"
    assert prompt_call["knowledge_evidence"]["quota_rules"] == []
    assert prompt_call["knowledge_evidence"]["quota_explanations"] == []
    assert prompt_call["knowledge_evidence"]["method_cards"] == []

    assert result["knowledge_evidence"]["quota_rules"][0]["id"] == "rule_ctx_1"
    assert result["knowledge_evidence"]["quota_explanations"][0]["id"] == "rule_ctx_2"
    assert result["knowledge_evidence"]["method_cards"][0]["id"] == "card_1"
    step = next(step for step in result["trace"]["steps"] if step.get("stage") == "agent_llm")
    assert step["quota_rule_ids"] == ["rule_ctx_1"]
    assert step["quota_explanation_ids"] == ["rule_ctx_2"]
    assert step["method_card_ids"] == ["card_1"]


def test_openai_compatible_call_requires_api_key(monkeypatch):
    matcher = AgentMatcher(llm_type="deepseek", province="测试省")
    monkeypatch.setattr(config, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(config, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(config, "DEEPSEEK_MODEL", "deepseek-chat")

    try:
        matcher._call_openai_compatible("hello")
        raised = None
    except Exception as exc:  # pragma: no cover - assertion below checks exact type
        raised = exc

    assert isinstance(raised, ValueError)
    assert "DEEPSEEK" in str(raised)


def test_rule_backup_advisory_keeps_prior_knowledge_trace():
    result = {
        "match_source": "agent",
        "confidence": 70,
        "knowledge_evidence": {"quota_rules": [{"id": "rule_a"}]},
        "knowledge_summary": {"quota_rules_count": 1},
        "trace": {
            "steps": [
                {
                    "stage": "agent_llm",
                    "quota_rule_ids": ["rule_a"],
                    "knowledge_evidence": {"quota_rules": [{"id": "rule_a"}]},
                }
            ]
        },
    }
    rule_backup = {
        "match_source": "rule",
        "confidence": 88,
        "quotas": [{"quota_id": "Q-1"}],
        "trace": {"steps": [{"stage": "rule_backup"}]},
    }

    overridden, hits = _apply_rule_backup(result, rule_backup, 0, prefer_label="Agent")

    assert hits == 0
    assert overridden["match_source"] == "agent"
    assert overridden["knowledge_evidence"]["quota_rules"][0]["id"] == "rule_a"
    stages = [step.get("stage") for step in overridden["trace"]["steps"]]
    assert "agent_llm" in stages
    assert "rule_backup_advisory" in stages
    assert overridden["backup_advisories"][0]["type"] == "rule_backup"
    assert overridden["backup_advisories"][0]["quota_id"] == "Q-1"


def test_param_validator_skips_legacy_ltr_when_ltr_v2_disabled(monkeypatch):
    monkeypatch.setattr(config, "PARAM_VALIDATOR_LEGACY_LTR_ENABLED", False)
    ParamValidator._ltr_model = "stale"
    ParamValidator._ltr_model_loaded = False

    ParamValidator._load_ltr_model()

    assert ParamValidator._ltr_model_loaded is True
    assert ParamValidator._ltr_model is None


def test_match_search_only_wires_experience_db_into_searcher(monkeypatch):
    experience_db = object()
    captured = {"set_calls": [], "prepare_seen_db": None}

    class DummySearcher:
        def __init__(self):
            self._experience_db = None

        def set_experience_db(self, db):
            captured["set_calls"].append(db)
            self._experience_db = db

    class DummyValidator:
        pass

    class DummyRuleValidator:
        def validate_results(self, _results):
            return None

    class DummyFinalValidator:
        def __init__(self, *args, **kwargs):
            pass

        def validate_results(self, _results):
            return None

    def fake_prepare_match_iteration(*, searcher, results, exp_hits, rule_hits, **kwargs):
        captured["prepare_seen_db"] = getattr(searcher, "_experience_db", None)
        results.append(
            {
                "bill_item": kwargs["item"],
                "quotas": [{"quota_id": "Q-1", "name": "quota", "unit": "m"}],
                "post_arbiter_top1_id": "Q-0",
                "confidence": 90,
                "match_source": "search",
                "trace": {"steps": []},
            }
        )
        return True, exp_hits, rule_hits, None

    monkeypatch.setattr(
        match_engine,
        "_create_rule_validator_and_reranker",
        lambda province=None: (DummyRuleValidator(), object()),
    )
    monkeypatch.setattr(match_engine, "_prepare_match_iteration", fake_prepare_match_iteration)
    monkeypatch.setattr(match_engine, "FinalValidator", DummyFinalValidator)
    monkeypatch.setitem(
        sys.modules,
        "src.consistency_checker",
        types.SimpleNamespace(check_and_fix=lambda results: results),
    )

    results = match_engine.match_search_only(
        [{"name": "桥架", "description": "", "specialty": "安装"}],
        searcher=DummySearcher(),
        validator=DummyValidator(),
        experience_db=experience_db,
        province="测试省",
    )

    assert captured["set_calls"] == [experience_db]
    assert captured["prepare_seen_db"] is experience_db
    assert results[0]["quotas"][0]["quota_id"] == "Q-1"
    assert results[0].get("final_changed_by", "") == ""
