import asyncio
import sys
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

_FAKE_DB_ROOT = Path(__file__).resolve().parents[1] / "output" / f"_tmp_openclaw_policy_{uuid.uuid4().hex}"
fake_config = types.ModuleType("config")
fake_config.resolve_province = lambda province, interactive=False: province
fake_config.get_quota_db_path = lambda province=None: ""
fake_config.get_current_province = lambda: ""
fake_config.DB_DIR = _FAKE_DB_ROOT
fake_config.COMMON_DB_DIR = _FAKE_DB_ROOT / "common"
fake_config.PROVINCES_DB_DIR = _FAKE_DB_ROOT / "provinces"
fake_config.__getattr__ = lambda name: ""
sys.modules.setdefault("config", fake_config)

from app.api import openclaw as openclaw_api  # noqa: E402


def _make_match_result(**overrides):
    payload = {
        "id": uuid.uuid4(),
        "task_id": uuid.uuid4(),
        "index": 1,
        "bill_code": "031001001001",
        "bill_name": "给水管道安装",
        "bill_description": "室内PPR给水管",
        "bill_unit": "m",
        "bill_quantity": 10.0,
        "bill_unit_price": None,
        "bill_amount": None,
        "specialty": "C10",
        "sheet_name": "给排水",
        "section": "给水工程",
        "quotas": [{"quota_id": "C10-1-1", "name": "原始定额", "unit": "m"}],
        "alternatives": None,
        "confidence": 82,
        "confidence_score": 82,
        "review_risk": "medium",
        "light_status": "yellow",
        "match_source": "search",
        "explanation": "候选接近，需要审核",
        "knowledge_evidence": None,
        "knowledge_basis": None,
        "knowledge_summary": None,
        "trace": None,
        "candidates_count": 5,
        "is_measure_item": False,
        "review_status": "pending",
        "corrected_quotas": None,
        "review_note": "",
        "openclaw_review_status": "pending",
        "openclaw_suggested_quotas": None,
        "openclaw_review_note": "",
        "openclaw_review_confidence": None,
        "openclaw_review_actor": "",
        "openclaw_review_time": None,
        "openclaw_decision_type": None,
        "openclaw_error_stage": None,
        "openclaw_error_type": None,
        "openclaw_retry_query": "",
        "openclaw_reason_codes": None,
        "openclaw_review_payload": None,
        "openclaw_review_confirm_status": "pending",
        "openclaw_review_confirmed_by": "",
        "openclaw_review_confirm_time": None,
        "human_feedback_payload": None,
        "created_at": datetime.now(UTC),
    }
    payload.update(overrides)
    return SimpleNamespace(**payload)


def test_quota_matches_bill_size_hint_requires_exact_dimension_signature():
    match_result = _make_match_result(
        bill_name="distribution box",
        bill_description="box size 600*900*220",
    )

    assert openclaw_api._quota_matches_bill_size_hint(
        match_result=match_result,
        quota={"name": "distribution box install 600*900"},
    ) is False
    assert openclaw_api._quota_matches_bill_size_hint(
        match_result=match_result,
        quota={"name": "distribution box install 600*900*220"},
    ) is True


def test_choose_best_safe_candidate_keeps_keyword_hits_when_search_hint_present(monkeypatch):
    async def _fake_search_quotas(*, keyword, province, book, chapter, limit, user):
        _ = (keyword, province, book, chapter, limit, user)
        return {
            "items": [
                {
                    "quota_id": "03-10-3-10",
                    "name": "valve install DN50",
                    "unit": "ea",
                    "score": 0.99,
                    "book": "C10",
                    "chapter": "valve",
                }
            ],
            "total": 1,
        }

    async def _fake_smart_search(*, name, province, description, specialty, limit, user):
        _ = (name, province, description, specialty, limit, user)
        return {
            "items": [
                {
                    "quota_id": "03-10-3-80",
                    "name": "valve install DN80",
                    "unit": "m",
                    "score": 0.98,
                    "book": "C10",
                    "chapter": "valve",
                }
            ],
            "search_query": "valve install DN50",
            "specialty": "C10",
            "province": province,
        }

    monkeypatch.setattr(openclaw_api.quota_search_api, "search_quotas", _fake_search_quotas)
    monkeypatch.setattr(openclaw_api.quota_search_api, "smart_search", _fake_smart_search)

    task = SimpleNamespace(
        id=uuid.uuid4(),
        province="demo-province",
    )
    match_result = _make_match_result(
        bill_name="valve",
        bill_description="DN50",
        bill_unit="ea",
        specialty="C10",
    )

    candidate, rejection_reasons, candidate_source = asyncio.run(
        openclaw_api._choose_best_safe_candidate(
            task=task,
            match_result=match_result,
            current_quotas=[{"quota_id": "03-10-3-99", "name": "current", "unit": "ea"}],
            candidate_pool=[],
            audit_queries=["valve install"],
            search_name_override="valve install",
            allow_slow_search=True,
        )
    )

    assert candidate is not None
    assert candidate["quota_id"] == "03-10-3-10"
    assert candidate_source.startswith("audit_keyword:")
    assert rejection_reasons == []


def test_build_auto_review_draft_request_overrides_pipe_support_with_search_candidate(monkeypatch):
    async def _fake_object_guard(**kwargs):
        _ = kwargs
        return {
            "same_object": False,
            "bill_object": "成品管卡",
            "quota_object": "给水管道",
            "reason": "清单对象是管卡附件，不是管道本体",
            "search_hint": "",
            "confidence": 98,
        }

    monkeypatch.setattr(openclaw_api, "_review_object_guard", _fake_object_guard)

    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="安装任务",
        province="上海市安装工程预算定额(2016)",
        mode="search",
        original_filename="demo.xlsx",
    )
    match_result = _make_match_result(
        bill_name="成品管卡",
        bill_description="1.名称：成品管卡\n2.规格：DN25",
        bill_unit="套",
        specialty="C10",
        light_status="red",
        confidence=92,
        confidence_score=92,
        quotas=[{"quota_id": "03-10-1-86", "name": "给排水管道 室内薄壁不锈钢管(卡套连接) 公称直径 25mm以内", "unit": "套"}],
        alternatives=[
            {"quota_id": "03-10-1-86", "name": "给排水管道 室内薄壁不锈钢管(卡套连接) 公称直径 25mm以内", "unit": "套"},
            {"quota_id": "03-10-2-12", "name": "成品管卡安装 公称直径 32mm以内", "unit": "套"},
        ],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "ambiguity_review", "severity": "error", "message": "候选分差偏小，建议复核"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "override_within_candidates"
    assert req.openclaw_suggested_quotas[0].quota_id == "03-10-2-12"
    assert "entity_conflict:pipe_support" in (req.openclaw_reason_codes or [])
    assert "candidate_source:candidate_pool" in (req.openclaw_reason_codes or [])


def test_forced_smart_search_does_not_displace_safe_candidate_pool(monkeypatch):
    async def _fake_search_quotas(*, keyword, province, book, chapter, limit, user):
        _ = (keyword, province, book, chapter, limit, user)
        return {"items": [], "total": 0}

    async def _fake_smart_search(*, name, province, description, specialty, limit, user):
        _ = (name, province, description, specialty, limit, user)
        return {
            "items": [
                {
                    "quota_id": "C4-10-114",
                    "name": "live smart-search candidate",
                    "unit": "m",
                    "score": 0.99,
                    "book": "C4",
                }
            ],
            "search_query": "forced live search",
        }

    monkeypatch.setattr(openclaw_api.quota_search_api, "search_quotas", _fake_search_quotas)
    monkeypatch.setattr(openclaw_api.quota_search_api, "smart_search", _fake_smart_search)

    task = SimpleNamespace(id=uuid.uuid4(), province="北京市建设工程施工消耗量标准(2024)")
    match_result = _make_match_result(
        bill_name="配管（SC20）",
        bill_description="配管SC20，暗敷,从配电箱至灯位",
        bill_unit="m",
        specialty="C4",
    )

    candidate, rejection_reasons, candidate_source = asyncio.run(
        openclaw_api._choose_best_safe_candidate(
            task=task,
            match_result=match_result,
            current_quotas=[{"quota_id": "C4-4-37", "name": "current", "unit": "台"}],
            candidate_pool=[
                {
                    "quota_id": "C4-4-38",
                    "name": "配电箱箱体安装 配电箱半周长(m以内) 暗装 1",
                    "unit": "m",
                    "source": "candidate_pool",
                    "rerank_score": 0.25,
                }
            ],
            search_name_override="配管安装 SC20 暗敷",
            allow_slow_search=True,
        )
    )

    assert candidate is not None
    assert candidate["quota_id"] == "C4-4-38"
    assert candidate_source == "candidate_pool"
    assert rejection_reasons == []


def test_forced_smart_search_survives_keyword_limit(monkeypatch):
    async def _fake_search_quotas(*, keyword, province, book, chapter, limit, user):
        _ = (keyword, province, book, chapter, user)
        return {
            "items": [
                {
                    "quota_id": f"K-{idx}",
                    "name": f"keyword candidate {idx}",
                    "unit": "套",
                    "score": 0.99 - idx / 100,
                    "book": "C10",
                }
                for idx in range(limit)
            ],
            "total": limit,
        }

    async def _fake_smart_search(*, name, province, description, specialty, limit, user):
        _ = (name, province, description, specialty, limit, user)
        return {
            "items": [
                {
                    "quota_id": "03-10-2-12",
                    "name": "成品管卡安装 公称直径 32mm以内",
                    "unit": "套",
                    "score": 0.93,
                    "book": "C10",
                    "chapter": "管道支架",
                }
            ],
            "search_query": "成品管卡安装 DN25",
        }

    monkeypatch.setattr(openclaw_api.quota_search_api, "search_quotas", _fake_search_quotas)
    monkeypatch.setattr(openclaw_api.quota_search_api, "smart_search", _fake_smart_search)

    task = SimpleNamespace(id=uuid.uuid4(), province="上海市安装工程预算定额(2016)")
    match_result = _make_match_result(
        bill_name="成品管卡",
        bill_description="1.名称：成品管卡\n2.规格：DN25",
        bill_unit="套",
        specialty="C10",
    )

    candidates = asyncio.run(
        openclaw_api._search_better_quota_candidates(
            task=task,
            match_result=match_result,
            audit_queries=["给排水管道"],
            search_name_override="成品管卡安装",
            limit=5,
        )
    )

    assert [item["quota_id"] for item in candidates][0] == "03-10-2-12"
    assert len(candidates) == 5


def test_build_auto_review_draft_request_can_agentically_search_beyond_bad_candidate_pool(monkeypatch):
    async def _fake_object_guard(**kwargs):
        _ = kwargs
        return {
            "same_object": False,
            "bill_object": "成品管卡",
            "quota_object": "给水管道",
            "reason": "清单对象是管卡附件，不是管道本体",
            "search_hint": "成品管卡安装",
            "confidence": 98,
        }

    async def _fake_smart_search(*, name, province, description, specialty, limit, user):
        assert name == "成品管卡安装"
        assert province == "上海市安装工程预算定额(2016)"
        assert specialty == "C10"
        _ = (description, limit, user)
        return {
            "items": [
                {
                    "quota_id": "03-10-2-12",
                    "name": "成品管卡安装 公称直径 32mm以内",
                    "unit": "套",
                    "score": 0.93,
                    "book": "C10",
                    "chapter": "管道支架",
                }
            ],
            "search_query": "成品管卡安装 DN25",
            "specialty": "C10",
            "province": province,
        }

    monkeypatch.setattr(openclaw_api, "_review_object_guard", _fake_object_guard)
    monkeypatch.setattr(openclaw_api.quota_search_api, "smart_search", _fake_smart_search)

    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="安装任务",
        province="上海市安装工程预算定额(2016)",
        mode="search",
        original_filename="demo.xlsx",
    )
    match_result = _make_match_result(
        bill_name="成品管卡",
        bill_description="1.名称：成品管卡\n2.规格：DN25",
        bill_unit="套",
        specialty="C10",
        light_status="red",
        confidence=92,
        confidence_score=92,
        quotas=[{"quota_id": "03-10-1-86", "name": "给排水管道 室内薄壁不锈钢管(卡套连接) 公称直径 25mm以内", "unit": "套"}],
        alternatives=[
            {"quota_id": "03-10-1-86", "name": "给排水管道 室内薄壁不锈钢管(卡套连接) 公称直径 25mm以内", "unit": "套"},
            {"quota_id": "03-10-1-307", "name": "给排水管道 室内塑料给水管(热熔连接) 公称外径 25mm以内", "unit": "套"},
        ],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "ambiguity_review", "severity": "error", "message": "候选分差偏小，建议复核"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "override_within_candidates"
    assert req.openclaw_suggested_quotas[0].quota_id == "03-10-2-12"
    assert "entity_conflict:pipe_support" in (req.openclaw_reason_codes or [])
    assert "candidate_source:smart_search:成品管卡安装 DN25" in (req.openclaw_reason_codes or [])
    assert "llm_search_hint:成品管卡安装" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_allows_slow_search_without_current_top1_when_object_guard_requests_it(monkeypatch):
    async def _fake_object_guard(**kwargs):
        _ = kwargs
        return {
            "same_object": False,
            "bill_object": "\u6210\u54c1\u7ba1\u5361",
            "quota_object": "",
            "reason": "Jarvis \u8fd8\u6ca1\u6709\u7a33\u5b9a top1\uff0c\u4f46\u5bf9\u8c61\u662f\u7ba1\u5361\u9644\u4ef6",
            "search_hint": "\u6210\u54c1\u7ba1\u5361\u5b89\u88c5",
            "confidence": 97,
        }

    async def _fake_search_quotas(*, keyword, province, book, chapter, limit, user):
        _ = (keyword, province, book, chapter, limit, user)
        return {"items": [], "total": 0}

    async def _fake_smart_search(*, name, province, description, specialty, limit, user):
        assert name == "\u6210\u54c1\u7ba1\u5361\u5b89\u88c5"
        assert province == "\u4e0a\u6d77\u5e02\u5b89\u88c5\u5de5\u7a0b\u9884\u7b97\u5b9a\u989d(2016)"
        assert specialty == "C10"
        _ = (description, limit, user)
        return {
            "items": [
                {
                    "quota_id": "03-10-2-12",
                    "name": "\u6210\u54c1\u7ba1\u5361\u5b89\u88c5 \u516c\u79f0\u76f4\u5f84 32mm\u4ee5\u5185",
                    "unit": "\u5957",
                    "score": 0.93,
                    "book": "C10",
                    "chapter": "\u7ba1\u9053\u652f\u67b6",
                }
            ],
            "search_query": "\u6210\u54c1\u7ba1\u5361\u5b89\u88c5 DN25",
            "specialty": "C10",
            "province": province,
        }

    monkeypatch.setattr(openclaw_api, "_review_object_guard", _fake_object_guard)
    monkeypatch.setattr(openclaw_api.quota_search_api, "search_quotas", _fake_search_quotas)
    monkeypatch.setattr(openclaw_api.quota_search_api, "smart_search", _fake_smart_search)

    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="\u5b89\u88c5\u4efb\u52a1",
        province="\u4e0a\u6d77\u5e02\u5b89\u88c5\u5de5\u7a0b\u9884\u7b97\u5b9a\u989d(2016)",
        mode="search",
        original_filename="demo.xlsx",
    )
    match_result = _make_match_result(
        bill_name="\u6210\u54c1\u7ba1\u5361",
        bill_description="1.\u540d\u79f0:\u6210\u54c1\u7ba1\u5361 2.\u89c4\u683c:DN25",
        bill_unit="\u5957",
        specialty="C10",
        light_status="red",
        confidence=40,
        confidence_score=40,
        quotas=None,
        alternatives=None,
        trace={
            "steps": [
                {
                    "final_validation": {"issues": []},
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "override_within_candidates"
    assert req.openclaw_suggested_quotas[0].quota_id == "03-10-2-12"
    assert "candidate_source:smart_search:\u6210\u54c1\u7ba1\u5361\u5b89\u88c5 DN25" in (req.openclaw_reason_codes or [])
    assert "llm_search_hint:\u6210\u54c1\u7ba1\u5361\u5b89\u88c5" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_prefers_candidate_pool_before_independent_audit(monkeypatch):
    async def _fake_object_guard(**kwargs):
        _ = kwargs
        return {
            "same_object": False,
            "bill_object": "成品管卡",
            "quota_object": "给排水管道",
            "reason": "审核应优先对象一致性，而不是优先信候选池",
            "search_hint": "成品管卡安装",
            "audit_queries": ["成品管卡安装"],
            "confidence": 99,
        }

    async def _fake_search_quotas(*, keyword, province, book, chapter, limit, user):
        assert keyword == "成品管卡安装"
        assert province == "上海市安装工程预算定额(2016)"
        assert book == "C10"
        assert chapter is None
        assert limit == 5
        return {
            "items": [
                {
                    "quota_id": "03-10-2-12",
                    "name": "成品管卡安装 公称直径 32mm以内",
                    "unit": "个",
                    "book": "C10",
                    "chapter": "管道支架",
                }
            ],
            "total": 1,
        }

    async def _fake_smart_search(**kwargs):
        _ = kwargs
        return {"items": [], "search_query": "", "specialty": "C10"}

    monkeypatch.setattr(openclaw_api, "_review_object_guard", _fake_object_guard)
    monkeypatch.setattr(openclaw_api.quota_search_api, "search_quotas", _fake_search_quotas)
    monkeypatch.setattr(openclaw_api.quota_search_api, "smart_search", _fake_smart_search)

    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="安装任务",
        province="上海市安装工程预算定额(2016)",
        mode="search",
        original_filename="demo.xlsx",
    )
    match_result = _make_match_result(
        bill_name="成品管卡",
        bill_description="1.名称：成品管卡\n2.规格：DN25",
        bill_unit="个",
        specialty="C10",
        light_status="red",
        confidence=92,
        confidence_score=92,
        quotas=[{"quota_id": "03-10-1-86", "name": "给排水管道 室内薄壁不锈钢管(卡套连接) 公称直径 25mm以内", "unit": "个"}],
        alternatives=[
            {"quota_id": "03-10-1-86", "name": "给排水管道 室内薄壁不锈钢管(卡套连接) 公称直径 25mm以内", "unit": "个"},
            {"quota_id": "03-10-2-99", "name": "成品管卡安装 公称直径 100mm以内", "unit": "个"},
        ],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "ambiguity_review", "severity": "error", "message": "候选分差偏小，建议复核"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "override_within_candidates"
    assert req.openclaw_suggested_quotas[0].quota_id == "03-10-2-99"
    assert "candidate_source:candidate_pool" in (req.openclaw_reason_codes or [])
    assert "audit_query:成品管卡安装" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_overrides_with_audit_candidate_on_soft_issue(monkeypatch):
    async def _fake_object_guard(**kwargs):
        _ = kwargs
        return {
            "same_object": True,
            "bill_object": "队门",
            "quota_object": "队门",
            "reason": "对象一致，但 Jarvis top1 不是审库最优项",
            "search_hint": "闸阀安装",
            "audit_queries": ["闸阀安装"],
            "confidence": 95,
        }

    async def _fake_search_quotas(*, keyword, province, book, chapter, limit, user):
        assert keyword == "闸阀安装"
        return {
            "items": [
                {
                    "quota_id": "03-10-3-10",
                    "name": "\u95f8\u9600\u5b89\u88c5 \u516c\u79f0\u76f4\u5f84 50mm\u4ee5\u5185",
                    "unit": "个",
                    "book": "C10",
                    "chapter": "阀门",
                }
            ],
            "total": 1,
        }

    async def _fake_smart_search(**kwargs):
        _ = kwargs
        return {"items": [], "search_query": "", "specialty": "C10"}

    monkeypatch.setattr(openclaw_api, "_review_object_guard", _fake_object_guard)
    monkeypatch.setattr(openclaw_api.quota_search_api, "search_quotas", _fake_search_quotas)
    monkeypatch.setattr(openclaw_api.quota_search_api, "smart_search", _fake_smart_search)

    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="安装任务",
        province="上海市安装工程预算定额(2016)",
        mode="search",
        original_filename="demo.xlsx",
    )
    match_result = _make_match_result(
        bill_name="闸阀",
        bill_description="1.名称：闸阀\n2.规格：DN50",
        bill_unit="个",
        specialty="C10",
        light_status="yellow",
        confidence=88,
        confidence_score=88,
        quotas=[{"quota_id": "03-10-3-99", "name": "阀门安装 公称直径 100mm以内", "unit": "个"}],
        alternatives=[
            {"quota_id": "03-10-3-99", "name": "阀门安装 公称直径 100mm以内", "unit": "个"},
        ],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "ambiguity_review", "severity": "error", "message": "候选分差偏小，建议复核"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "override_within_candidates"
    assert req.openclaw_suggested_quotas[0].quota_id == "03-10-3-10"
    assert "candidate_source:audit_keyword:闸阀安装" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_uses_llm_object_guard_when_rules_are_not_decisive(monkeypatch):
    monkeypatch.setattr(
        openclaw_api,
        "_call_review_llm_json",
        lambda prompt: {
            "same_object": True,
            "bill_object": "阀门",
            "quota_object": "阀门",
            "reason": "llm_verified_same_object",
            "search_hint": "阀门安装",
            "audit_queries": ["阀门安装"],
            "confidence": 91,
        },
    )

    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="安装任务",
        province="上海市安装工程预算定额(2016)",
        mode="search",
        original_filename="demo.xlsx",
    )
    match_result = _make_match_result(
        bill_name="阀门",
        bill_description="DN50",
        bill_unit="个",
        specialty="C10",
        light_status="yellow",
        confidence=84,
        confidence_score=84,
        quotas=[{"quota_id": "03-10-3-10", "name": "阀门安装 公称直径 50mm以内", "unit": "个"}],
        alternatives=[{"quota_id": "03-10-3-10", "name": "阀门安装 公称直径 50mm以内", "unit": "个"}],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "ambiguity_review", "severity": "warning", "message": "候选接近，建议复核"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "agree"
    assert "llm_object_guard_used" in (req.openclaw_reason_codes or [])
    assert "llm_entity_verified" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_agrees_when_only_ambiguity_and_top1_is_object_consistent(monkeypatch):
    async def _fake_object_guard(**kwargs):
        _ = kwargs
        return {
            "same_object": True,
            "bill_object": "阀门",
            "quota_object": "阀门",
            "reason": "对象一致，仅候选分差接近",
            "search_hint": "闸阀安装",
            "audit_queries": ["闸阀安装"],
            "confidence": 96,
        }

    async def _fake_search_quotas(*, keyword, province, book, chapter, limit, user):
        _ = (province, book, chapter, limit, user)
        assert keyword == "闸阀安装"
        return {
            "items": [
                {
                    "quota_id": "03-10-3-10",
                    "name": "闸阀安装 公称直径 50mm以内",
                    "unit": "个",
                    "book": "C10",
                    "chapter": "阀门",
                }
            ],
            "total": 1,
        }

    async def _fake_smart_search(**kwargs):
        _ = kwargs
        return {"items": [], "search_query": "", "specialty": "C10"}

    monkeypatch.setattr(openclaw_api, "_review_object_guard", _fake_object_guard)
    monkeypatch.setattr(openclaw_api.quota_search_api, "search_quotas", _fake_search_quotas)
    monkeypatch.setattr(openclaw_api.quota_search_api, "smart_search", _fake_smart_search)

    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="安装任务",
        province="上海市安装工程预算定额(2016)",
        mode="search",
        original_filename="demo.xlsx",
    )
    match_result = _make_match_result(
        bill_name="闸阀",
        bill_description="1.名称：闸阀\n2.规格：DN50",
        bill_unit="个",
        specialty="C10",
        light_status="yellow",
        confidence=84,
        confidence_score=84,
        quotas=[{"quota_id": "03-10-3-10", "name": "闸阀安装 公称直径 50mm以内", "unit": "个"}],
        alternatives=[
            {"quota_id": "03-10-3-10", "name": "闸阀安装 公称直径 50mm以内", "unit": "个"},
            {"quota_id": "03-10-3-11", "name": "闸阀安装 公称直径 80mm以内", "unit": "个"},
        ],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "ambiguity_review", "severity": "warning", "message": "候选接近，建议复核"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "agree"
    assert req.openclaw_suggested_quotas[0].quota_id == "03-10-3-10"
    assert "jarvis_top1_verified" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_keeps_manual_gate_when_only_ambiguity_and_search_finds_other_candidate(monkeypatch):
    async def _fake_object_guard(**kwargs):
        _ = kwargs
        return {
            "same_object": True,
            "bill_object": "阀门",
            "quota_object": "阀门",
            "reason": "对象一致，但候选之间接近",
            "search_hint": "闸阀安装",
            "audit_queries": ["闸阀安装"],
            "confidence": 95,
        }

    async def _fake_search_quotas(*, keyword, province, book, chapter, limit, user):
        _ = (province, book, chapter, limit, user)
        assert keyword == "闸阀安装"
        return {
            "items": [
                {
                    "quota_id": "03-10-3-10",
                    "name": "闸阀安装 公称直径 50mm以内",
                    "unit": "个",
                    "book": "C10",
                    "chapter": "阀门",
                }
            ],
            "total": 1,
        }

    async def _fake_smart_search(**kwargs):
        _ = kwargs
        return {"items": [], "search_query": "", "specialty": "C10"}

    monkeypatch.setattr(openclaw_api, "_review_object_guard", _fake_object_guard)
    monkeypatch.setattr(openclaw_api.quota_search_api, "search_quotas", _fake_search_quotas)
    monkeypatch.setattr(openclaw_api.quota_search_api, "smart_search", _fake_smart_search)

    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="安装任务",
        province="上海市安装工程预算定额(2016)",
        mode="search",
        original_filename="demo.xlsx",
    )
    match_result = _make_match_result(
        bill_name="闸阀",
        bill_description="1.名称：闸阀\n2.规格：DN50",
        bill_unit="个",
        specialty="C10",
        light_status="yellow",
        confidence=83,
        confidence_score=83,
        quotas=[{"quota_id": "03-10-3-99", "name": "阀门安装 公称直径 100mm以内", "unit": "个"}],
        alternatives=[
            {"quota_id": "03-10-3-99", "name": "阀门安装 公称直径 100mm以内", "unit": "个"},
        ],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "ambiguity_review", "severity": "warning", "message": "候选接近，建议复核"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "candidate_pool_insufficient"
    assert not req.openclaw_suggested_quotas
    assert "needs_manual_gate" in (req.openclaw_reason_codes or [])
