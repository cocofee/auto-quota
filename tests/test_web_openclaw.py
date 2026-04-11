import asyncio
import importlib.util
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import types

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

fake_config = types.ModuleType("config")
fake_config.resolve_province = lambda province, interactive=False: province
fake_config.get_quota_db_path = lambda province=None: ""
fake_config.get_current_province = lambda: ""
fake_config.__getattr__ = lambda name: ""
sys.modules.setdefault("config", fake_config)

from app.api.openclaw import router as openclaw_router  # noqa: E402
from app.api import openclaw as openclaw_api  # noqa: E402
from app.api import results as results_api  # noqa: E402
from app.auth import openclaw as openclaw_auth  # noqa: E402
from app.schemas.result import OpenClawReviewConfirmRequest, OpenClawReviewDraftRequest  # noqa: E402
from app.schemas.file_intake import FileClassifyRequest, FileParseRequest, FileRouteRequest  # noqa: E402
from app.services import openclaw_review_service as review_service_api  # noqa: E402
from app.services.openclaw_review_service import OpenClawReviewService  # noqa: E402
from app.api import file_intake as file_intake_api  # noqa: E402


def _garble(text: str) -> str:
    return text.encode("utf-8").decode("latin1")


def _garble_gb18030(text: str) -> str:
    return text.encode("utf-8").decode("gb18030")


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    def __init__(self, user=None):
        self._user = user
        self.flushed = False

    async def execute(self, _query):
        return _Result(self._user)

    async def commit(self):
        return None

    async def refresh(self, _user):
        return None

    async def flush(self):
        self.flushed = True


class _ScalarResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _ListDb:
    def __init__(self, items):
        self._items = items

    async def execute(self, _query):
        return _ScalarResult(self._items)


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


def test_file_intake_low_confidence_goes_waiting_human(monkeypatch, tmp_path):
    db_path = tmp_path / "file_intake.db"
    original_db_cls = file_intake_api.FileIntakeDB
    monkeypatch.setattr(file_intake_api, "FileIntakeDB", lambda: original_db_cls(db_path))

    sample = tmp_path / "sample.xlsx"
    sample.write_bytes(b"fake")

    db = original_db_cls(db_path)
    record = db.create_file(
        filename="sample.xlsx",
        stored_path=str(sample),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_ext=".xlsx",
        file_size=4,
        actor="openclaw@system.local",
        created_by="openclaw@system.local",
    )

    async def _run():
        return await file_intake_api._classify_file(record["file_id"], FileClassifyRequest(force=False))

    monkeypatch.setattr(file_intake_api, "_classify_record", lambda _record: ("other", {"file_type": "other", "confidence": 0.25, "signals": []}))
    response = asyncio.run(_run())
    updated = db.get_file(record["file_id"])

    assert response.status == "waiting_human"
    assert updated["status"] == "waiting_human"
    assert updated["failure_type"] == "manual_review"
    assert updated["failure_stage"] == "classify-file"
    assert updated["needs_manual_review"] is True
    assert updated["manual_review_reason"] == "low_confidence_classification"
    assert updated["next_action"] == "manual-review"


def test_file_intake_manual_review_confirm_can_resume_parse(monkeypatch, tmp_path):
    db_path = tmp_path / "file_intake.db"
    original_db_cls = file_intake_api.FileIntakeDB
    monkeypatch.setattr(file_intake_api, "FileIntakeDB", lambda: original_db_cls(db_path))

    sample = tmp_path / "sample.xlsx"
    sample.write_bytes(b"fake")

    db = original_db_cls(db_path)
    record = db.create_file(
        filename="sample.xlsx",
        stored_path=str(sample),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_ext=".xlsx",
        file_size=4,
        actor="openclaw@system.local",
        created_by="openclaw@system.local",
    )
    db.update_failure(
        record["file_id"],
        error_message="classification confidence too low: 0.25",
        failure_type="manual_review",
        failure_stage="classify-file",
        needs_manual_review=True,
        manual_review_reason="low_confidence_classification",
    )

    monkeypatch.setattr(file_intake_api, "_parse_record", lambda _record: {"warnings": [], "bill_items": 3, "quote_items": 0})

    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw", id="svc")
    req = file_intake_api.FileManualReviewConfirmRequest(file_type="priced_bill_file", continue_from="parse")
    response = asyncio.run(file_intake_api.confirm_manual_review(record["file_id"], req, service_user))
    updated = db.get_file(record["file_id"])

    assert response.status == "parsed"
    assert updated["status"] == "parsed"
    assert updated["file_type"] == "priced_bill_file"
    assert updated["current_stage"] == "parse-file"
    assert updated["next_action"] == "route-decision"
    assert updated["needs_manual_review"] is False
    assert updated["failure_type"] == ""


def test_night_experiment_batch_summarize_keep():
    module_path = Path(__file__).resolve().parents[1] / "tools" / "night_experiment_file_intake_batch.py"
    spec = importlib.util.spec_from_file_location("night_exp_batch", module_path)
    night_exp_batch = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(night_exp_batch)

    results = [
        {
            "status": "routed",
            "elapsed_ms": 1200,
            "classify_result": {"confidence": 0.78},
            "parse_summary": {"bill_items": 10},
        },
        {
            "status": "routed",
            "elapsed_ms": 1500,
            "classify_result": {"confidence": 0.81},
            "parse_summary": {"bill_items": 8},
        },
        {
            "status": "routed",
            "elapsed_ms": 1100,
            "classify_result": {"confidence": 0.73},
            "parse_summary": {"bill_items": 6},
        },
        {
            "status": "routed",
            "elapsed_ms": 1000,
            "classify_result": {"confidence": 0.69},
            "parse_summary": {"bill_items": 5},
        },
        {
            "status": "waiting_human",
            "elapsed_ms": 900,
            "classify_result": {"confidence": 0.31},
            "parse_summary": {},
        },
    ]

    summary = night_exp_batch._summarize(results)

    assert summary["decision"] == "保留"
    assert summary["metrics"]["sample_count"] == 5
    assert summary["metrics"]["routed_count"] == 4
    assert summary["metrics"]["waiting_human_count"] == 1
    assert summary["metrics"]["total_bill_items"] == 29


def test_night_experiment_batch_render_markdown_contains_summary():
    module_path = Path(__file__).resolve().parents[1] / "tools" / "night_experiment_file_intake_batch.py"
    spec = importlib.util.spec_from_file_location("night_exp_batch", module_path)
    night_exp_batch = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(night_exp_batch)

    payload = {
        "experiment_id": "night-exp-002",
        "experiment_goal": "goal",
        "change": "change",
        "sample_set": "samples",
        "results": [
            {
                "filename": "a.xlsx",
                "status": "routed",
                "file_type": "priced_bill_file",
                "next_action": "observe-downstream",
                "classify_result": {"confidence": 0.74},
            }
        ],
        "summary": {
            "decision": "保留",
            "reason": "ok",
            "next_step": "继续",
            "metrics": {
                "sample_count": 1,
                "routed_count": 1,
                "waiting_human_count": 0,
                "avg_confidence": 0.74,
                "avg_elapsed_ms": 1000,
                "total_bill_items": 7,
            },
        },
    }

    markdown = night_exp_batch._render_markdown(payload)

    assert "# night-exp-002 实验报告" in markdown
    assert "- 判定：保留" in markdown
    assert "`a.xlsx` | status=routed | file_type=priced_bill_file" in markdown


def test_night_experiment_runner_judge_runner_keep():
    module_path = Path(__file__).resolve().parents[1] / "tools" / "night_experiment_runner.py"
    spec = importlib.util.spec_from_file_location("night_exp_runner", module_path)
    night_exp_runner = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(night_exp_runner)

    stages = [
        {"experiment_id": "night-exp-001", "summary": {"decision": "保留", "reason": "ok"}},
        {"experiment_id": "night-exp-002", "summary": {"decision": "保留", "reason": "ok"}},
        {"experiment_id": "night-exp-003", "summary": {"decision": "保留", "reason": "ok"}},
    ]

    summary = night_exp_runner._judge_runner(stages)

    assert summary["decision"] == "保留"
    assert "最小可运行闭环" in summary["reason"]


def test_openclaw_openapi_contains_bridge_routes_only():
    app = FastAPI()
    app.include_router(openclaw_router, prefix="/api/openclaw")
    client = TestClient(app)

    response = client.get("/api/openclaw/openapi.json")
    assert response.status_code == 200

    payload = response.json()
    assert "/api/openclaw/qmd-search" in payload["paths"]
    assert "/api/openclaw/quota-search/smart" in payload["paths"]
    assert "/api/openclaw/tasks" in payload["paths"]
    assert "/api/openclaw/tasks/{task_id}/review-items" in payload["paths"]
    assert "/api/openclaw/tasks/{task_id}/review-pending" in payload["paths"]
    assert "/api/openclaw/tasks/{task_id}/results/{result_id}/review-draft" in payload["paths"]
    assert "/api/openclaw/tasks/{task_id}/results/{result_id}/review-confirm" in payload["paths"]
    assert "/api/openclaw/promotion-cards" in payload["paths"]
    assert "/api/openclaw/source-packs" in payload["paths"]
    assert "/api/openclaw/source-packs/{source_id}" in payload["paths"]
    assert "/api/openclaw/source-packs/{source_id}/learn" in payload["paths"]
    assert "/api/openclaw/openapi.json" not in payload["paths"]


def test_require_openclaw_api_key_validates_secret(monkeypatch):
    monkeypatch.setattr(openclaw_auth, "OPENCLAW_API_KEY", "secret-key")
    assert asyncio.run(openclaw_auth.require_openclaw_api_key("secret-key")) == "secret-key"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(openclaw_auth.require_openclaw_api_key("wrong-key"))
    assert exc.value.status_code == 401


def test_openclaw_policy_bucket():
    assert openclaw_api._openclaw_policy_bucket(95) == "green"
    assert openclaw_api._openclaw_policy_bucket(openclaw_api.GREEN_THRESHOLD) == "green"
    assert openclaw_api._openclaw_policy_bucket(80) == "yellow"
    assert openclaw_api._openclaw_policy_bucket(openclaw_api.YELLOW_THRESHOLD) == "yellow"
    assert openclaw_api._openclaw_policy_bucket(69) == "red"


def test_save_review_draft_does_not_change_formal_result(monkeypatch):
    match_result = _make_match_result()
    db = _FakeDb()
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="北京"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    response = asyncio.run(
        openclaw_api.save_review_draft(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewDraftRequest(
                openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"}],
                openclaw_review_note="OpenClaw 建议替换",
                openclaw_review_confidence=88,
            ),
            db=db,
            service_user=service_user,
        )
    )

    assert db.flushed is True
    assert match_result.quotas == [{"quota_id": "C10-1-1", "name": "原始定额", "unit": "m"}]
    assert match_result.corrected_quotas is None
    assert match_result.review_status == "pending"
    assert match_result.openclaw_review_status == "reviewed"
    assert match_result.openclaw_review_confirm_status == "pending"
    assert match_result.openclaw_suggested_quotas[0]["quota_id"] == "C10-9-9"
    assert response.openclaw_review_status == "reviewed"


def test_save_review_draft_repairs_garbled_suggested_quota_name(monkeypatch):
    match_result = _make_match_result()
    db = _FakeDb()
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="北京"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    response = asyncio.run(
        openclaw_api.save_review_draft(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewDraftRequest(
                openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": _garble("建议定额"), "unit": "m"}],
                openclaw_review_note="OpenClaw 建议替换",
                openclaw_review_confidence=88,
            ),
            db=db,
            service_user=service_user,
        )
    )

    assert match_result.openclaw_suggested_quotas[0]["name"] == "建议定额"
    assert response.openclaw_suggested_quotas[0].name == "建议定额"


def test_save_review_draft_repairs_gb18030_garbled_suggested_quota_name(monkeypatch):
    match_result = _make_match_result()
    db = _FakeDb()
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")
    expected_name = "\u5efa\u8bae\u5b9a\u989d"

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="\u5317\u4eac"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    response = asyncio.run(
        openclaw_api.save_review_draft(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewDraftRequest(
                openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": _garble_gb18030(expected_name), "unit": "m"}],
                openclaw_review_note="OpenClaw \u5efa\u8bae\u66ff\u6362",
                openclaw_review_confidence=88,
            ),
            db=db,
            service_user=service_user,
        )
    )

    assert match_result.openclaw_suggested_quotas[0]["name"] == expected_name
    assert response.openclaw_suggested_quotas[0].name == expected_name


def test_save_review_draft_recovers_question_mark_suggested_quota_name_from_alternatives(monkeypatch):
    expected_name = "\u81ea\u52a8\u7a7a\u6c14\u5f00\u5173\u5b89\u88c5 \u7535\u52a8\u5f0f"
    match_result = _make_match_result(
        alternatives=[{"quota_id": "C10-9-9", "name": expected_name, "unit": "\u4e2a"}],
    )
    db = _FakeDb()
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="\u5317\u4eac"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    response = asyncio.run(
        openclaw_api.save_review_draft(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewDraftRequest(
                openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": "???????? ???", "unit": "\u4e2a"}],
                openclaw_review_note="OpenClaw review note",
                openclaw_review_confidence=88,
            ),
            db=db,
            service_user=service_user,
        )
    )

    assert match_result.openclaw_suggested_quotas[0]["name"] == expected_name
    assert response.openclaw_suggested_quotas[0].name == expected_name


def test_save_review_draft_allows_red_light_result(monkeypatch):
    match_result = _make_match_result(confidence=68, confidence_score=68, light_status="red")
    db = _FakeDb()
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="北京"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    response = asyncio.run(
        openclaw_api.save_review_draft(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewDraftRequest(
                openclaw_suggested_quotas=[{"quota_id": "C10-8-8", "name": "红灯建议定额", "unit": "m"}],
                openclaw_review_note="红灯结果允许 OpenClaw 给出复核建议",
                openclaw_review_confidence=76,
            ),
            db=db,
            service_user=service_user,
        )
    )

    assert db.flushed is True
    assert match_result.review_status == "pending"
    assert match_result.corrected_quotas is None
    assert match_result.openclaw_review_status == "reviewed"
    assert match_result.openclaw_suggested_quotas[0]["quota_id"] == "C10-8-8"
    assert response.openclaw_review_status == "reviewed"


def test_save_review_draft_still_blocks_green_light_result(monkeypatch):
    match_result = _make_match_result(confidence=95, confidence_score=95, light_status="green")
    db = _FakeDb()
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="北京"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            openclaw_api.save_review_draft(
                task_id=uuid.uuid4(),
                result_id=match_result.id,
                req=OpenClawReviewDraftRequest(
                    openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"}],
                    openclaw_review_note="绿灯不应再走 draft",
                    openclaw_review_confidence=96,
                ),
                db=db,
                service_user=service_user,
            )
        )
    assert exc.value.status_code == 409


def test_build_result_list_response_tolerates_legacy_openclaw_field_values():
    match_result = _make_match_result(
        openclaw_review_status="",
        openclaw_review_confirm_status="",
        openclaw_decision_type="",
        openclaw_error_stage="",
        openclaw_error_type="",
        openclaw_review_payload="legacy-string-payload",
        openclaw_reason_codes="legacy-string-codes",
        human_feedback_payload="legacy-string-feedback",
    )

    response = openclaw_api._build_result_list_response([match_result])

    assert response.total == 1
    item = response.items[0]
    assert item.openclaw_review_status == "pending"
    assert item.openclaw_review_confirm_status == "pending"
    assert item.openclaw_decision_type is None
    assert item.openclaw_error_stage is None
    assert item.openclaw_error_type is None
    assert item.openclaw_review_payload is None
    assert item.openclaw_reason_codes is None
    assert item.human_feedback_payload is None


def test_build_result_list_response_recovers_question_mark_suggested_quota_name():
    expected_name = "\u7ba1\u5185\u7a7f\u7ebf \u7a7f\u52a8\u529b\u7ebf \u94dc\u82af\u5bfc\u7ebf\u622a\u9762\uff08mm2\uff09 \u22642.5"
    match_result = _make_match_result(
        alternatives=[{"quota_id": "C10-9-9", "name": expected_name, "unit": "m"}],
        openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": "???? ???? ??????(mm2) ?2.5", "unit": "m"}],
        openclaw_review_payload={
            "decision_type": "override_within_candidates",
            "suggested_quotas": [{"quota_id": "C10-9-9", "name": "???? ???? ??????(mm2) ?2.5", "unit": "m"}],
        },
    )

    response = openclaw_api._build_result_list_response([match_result])

    assert response.items[0].openclaw_suggested_quotas[0].name == expected_name
    assert response.items[0].openclaw_review_payload["suggested_quotas"][0]["name"] == expected_name


def test_openclaw_review_context_repairs_garbled_quota_text():
    service = OpenClawReviewService()
    task = SimpleNamespace(
        id=uuid.uuid4(),
        name=_garble("安装任务"),
        province=_garble("北京定额库"),
        mode="search",
        original_filename=_garble("测试清单.xlsx"),
    )
    match_result = _make_match_result(
        bill_name=_garble("给水管道安装"),
        bill_description=_garble("室内PPR给水管"),
        quotas=[{"quota_id": "C10-1-1", "name": _garble("室内塑料给水管"), "unit": "m"}],
        alternatives=[{"quota_id": "C10-1-2", "name": _garble("室内镀锌钢管"), "unit": "m"}],
    )

    context = service.build_review_context(task, match_result)

    assert context["task"]["name"] == "安装任务"
    assert context["task"]["province"] == "北京定额库"
    assert context["jarvis_result"]["top1_quota_name"] == "室内塑料给水管"
    assert context["candidate_pool"][0]["name"] == "室内塑料给水管"


def test_openclaw_review_context_includes_qmd_recall(monkeypatch):
    class _FakeQMDService:
        def recall_for_review_context(self, task, match_result, *, top_k=3):
            assert top_k == 3
            return {
                "query": f"{match_result.bill_name} {match_result.bill_description}",
                "count": 1,
                "filters": {},
                "hits": [
                    {
                        "chunk_id": "rules-1",
                        "score": 0.91,
                        "title": "BV-2.5 穿管纠正规则",
                        "heading": "BV-2.5 穿管",
                        "category": "rules",
                        "page_type": "rule",
                        "path": "rules/bv-2.5.md",
                        "province": "",
                        "specialty": "安装",
                        "status": "active",
                        "source_kind": "",
                        "source_refs_text": "source-1",
                        "preview": "优先穿管敷设。",
                        "document": "优先穿管敷设。",
                    }
                ],
            }

    monkeypatch.setattr(review_service_api, "get_default_qmd_service", lambda: _FakeQMDService())

    service = OpenClawReviewService()
    task = SimpleNamespace(id=uuid.uuid4(), name="安装任务", province="北京", mode="search", original_filename="demo.xlsx")
    match_result = _make_match_result(
        bill_name="BV-2.5 导线",
        bill_description="穿管敷设",
        specialty="安装",
    )

    context = service.build_review_context(task, match_result)

    assert context["qmd_recall"]["count"] == 1
    assert context["qmd_recall"]["hits"][0]["title"] == "BV-2.5 穿管纠正规则"


def test_openclaw_structured_draft_contains_absorbable_report():
    service = OpenClawReviewService()
    task = SimpleNamespace(id=uuid.uuid4(), name="安装任务", province="北京", mode="search", original_filename="demo.xlsx")
    match_result = _make_match_result(
        bill_name="三联单控开关",
        bill_description="暗装 86 型",
        specialty="安装",
        quotas=[{"quota_id": "C10-1-1", "name": "原始定额", "unit": "个"}],
        alternatives=[{"quota_id": "C10-1-2", "name": "修正定额", "unit": "个"}],
    )

    draft = service.build_structured_draft(
        task,
        match_result,
        decision_type="override_within_candidates",
        suggested_quotas=[{"quota_id": "C10-1-2", "name": "修正定额", "unit": "个"}],
        review_confidence=91,
        error_stage="ranker",
        error_type="wrong_param",
        retry_query="三联单控开关 暗装 86 型",
        reason_codes=["candidate_pool_better", "issue:wrong_param"],
        note="当前 top1 参数不符，候选池内已有更优项。",
        evidence={"issue_types": ["wrong_param"], "qmd_summary": {"count": 2}},
    )

    payload = draft["openclaw_review_payload"]
    report = payload["jarvis_absorbable_report"]

    assert report["schema_version"] == "openclaw_review_report.v1"
    assert report["decision"]["decision_type"] == "override_within_candidates"
    assert report["openclaw_top1"]["quota_id"] == "C10-1-2"
    assert report["learning_record"]["final_quota_code"] == "C10-1-2"
    assert "reason:candidate_pool_better" in report["judgment"]["basis_points"]
    assert report["promotion_hints"]["experience"]["quota_ids"] == ["C10-1-2"]


def test_openclaw_qmd_search_endpoint(monkeypatch):
    class _FakeQMDService:
        def search(self, request):
            assert request.query == "BV-2.5 穿管纠正"
            return {
                "query": request.query,
                "count": 1,
                "filters": {"category": "rules"},
                "hits": [
                    {
                        "chunk_id": "rules-1",
                        "score": 0.93,
                        "title": "BV-2.5 穿管纠正规则",
                        "heading": "穿管",
                        "category": "rules",
                        "page_type": "rule",
                        "path": "rules/bv-2.5.md",
                        "province": "",
                        "specialty": "安装",
                        "status": "active",
                        "source_kind": "",
                        "source_refs_text": "source-1",
                        "preview": "优先穿管敷设。",
                        "document": "优先穿管敷设。",
                    }
                ],
            }

    monkeypatch.setattr(openclaw_api, "get_default_qmd_service", lambda: _FakeQMDService())

    app = FastAPI()
    app.include_router(openclaw_router, prefix="/api/openclaw")
    app.dependency_overrides[openclaw_api.get_openclaw_read_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(),
        email="openclaw@test.local",
        nickname="OpenClaw",
        is_admin=True,
    )
    client = TestClient(app)

    response = client.get(
        "/api/openclaw/qmd-search",
        params={"q": "BV-2.5 穿管纠正", "category": "rules", "top_k": 3},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["hits"][0]["path"] == "rules/bv-2.5.md"


def test_openclaw_report_analyze_endpoint_returns_absorbable(monkeypatch):
    app = FastAPI()
    app.include_router(openclaw_router, prefix="/api/openclaw")
    app.dependency_overrides[openclaw_api.get_openclaw_read_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(),
        email="openclaw@test.local",
        nickname="OpenClaw",
        is_admin=True,
    )
    client = TestClient(app)

    response = client.post(
        "/api/openclaw/report-analyze",
        json={
            "openclaw_review_status": "applied",
            "current_quota": {"quota_id": "C10-1-1", "name": "原始定额", "unit": "m"},
            "openclaw_review_payload": {
                "jarvis_absorbable_report": {
                    "jarvis_top1": {"quota_id": "C10-1-1", "name": "原始定额", "unit": "m"},
                    "openclaw_top1": {"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"},
                    "decision": {"reason_codes": ["candidate_pool_better"]},
                    "judgment": {"basis_summary": "候选池中已有更优项。"},
                }
            },
            "human_feedback_payload": {
                "protocol_version": "lobster_review_feedback.v1",
                "source": "lobster_audit",
                "adopt_openclaw": False,
                "final_quota": {"quota_id": "C10-8-8", "name": "人工终版定额", "unit": "m"},
                "manual_reason_codes": ["manual_override", "param_checked"],
                "manual_note": "人工终审改为最终定额。",
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["absorbability"] == "absorbable"
    assert payload["primary_target"] == "ExperienceDB"
    assert "audit_errors" in payload["learning_targets"]
    assert payload["final_quota"]["quota_id"] == "C10-8-8"
    assert payload["normalized_feedback_payload"]["protocol_version"] == "lobster_review_feedback.v1"


def test_openclaw_report_analyze_endpoint_returns_partial_for_draft_only(monkeypatch):
    app = FastAPI()
    app.include_router(openclaw_router, prefix="/api/openclaw")
    app.dependency_overrides[openclaw_api.get_openclaw_read_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(),
        email="openclaw@test.local",
        nickname="OpenClaw",
        is_admin=True,
    )
    client = TestClient(app)

    response = client.post(
        "/api/openclaw/report-analyze",
        json={
            "openclaw_review_status": "reviewed",
            "openclaw_review_note": "仅有草稿建议",
            "openclaw_reason_codes": ["candidate_pool_better"],
            "suggested_quotas": [{"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["absorbability"] == "partial"
    assert "confirmed_final_state" in payload["missing_fields"]


def test_list_review_items_only_returns_yellow_red_and_pending_reviews(monkeypatch):
    yellow = _make_match_result(light_status="yellow")
    red = _make_match_result(light_status="red")
    green = _make_match_result(light_status="green")
    reviewed_green = _make_match_result(
        light_status="green",
        openclaw_review_status="reviewed",
        openclaw_review_confirm_status="pending",
    )

    async def _fake_get_user_task(*_args, **_kwargs):
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(openclaw_api, "get_user_task", _fake_get_user_task)

    response = asyncio.run(
        openclaw_api.list_review_items(
            task_id=uuid.uuid4(),
            db=_ListDb([yellow, red, green, reviewed_green]),
            reader=SimpleNamespace(),
        )
    )

    assert response.total == 3
    ids = {item.id for item in response.items}
    assert yellow.id in ids
    assert red.id in ids
    assert reviewed_green.id in ids
    assert green.id not in ids


def test_review_confirm_reject_only_marks_rejected(monkeypatch):
    match_result = _make_match_result(
        openclaw_review_status="reviewed",
        openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"}],
        openclaw_review_note="建议改成 PPR 定额",
    )
    db = _FakeDb()
    reviewer = SimpleNamespace(id=uuid.uuid4(), email="reviewer@example.com", nickname="人工复核")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="北京"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    response = asyncio.run(
        openclaw_api.review_confirm(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewConfirmRequest(decision="reject", review_note="参数不一致"),
            db=db,
            user=reviewer,
        )
    )

    assert db.flushed is True
    assert match_result.corrected_quotas is None
    assert match_result.review_status == "pending"
    assert match_result.openclaw_review_status == "rejected"
    assert match_result.openclaw_review_confirm_status == "rejected"
    assert match_result.openclaw_review_confirmed_by == "人工复核"
    assert response.openclaw_review_status == "rejected"


def test_review_confirm_approve_applies_suggested_quotas(monkeypatch):
    suggested = [{"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"}]
    match_result = _make_match_result(
        openclaw_review_status="reviewed",
        openclaw_suggested_quotas=suggested,
        openclaw_review_note="OpenClaw 建议替换",
    )
    db = _FakeDb()
    reviewer = SimpleNamespace(id=uuid.uuid4(), email="reviewer@example.com", nickname="人工复核")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="北京"), match_result

    async def _fake_correct_result(*, req, **_kwargs):
        match_result.corrected_quotas = req.corrected_quotas
        match_result.review_status = "corrected"
        match_result.review_note = req.review_note
        return None

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)
    monkeypatch.setattr(openclaw_api.results_api, "correct_result", _fake_correct_result)

    async def _fake_record_openclaw_approved_review_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.services.openclaw_staging.record_openclaw_approved_review_async",
        _fake_record_openclaw_approved_review_async,
    )

    response = asyncio.run(
        openclaw_api.review_confirm(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewConfirmRequest(decision="approve", review_note="人工确认通过"),
            db=db,
            user=reviewer,
        )
    )

    assert db.flushed is True
    assert match_result.corrected_quotas[0].quota_id == "C10-9-9"
    assert match_result.review_status == "corrected"
    assert match_result.openclaw_review_status == "applied"
    assert match_result.openclaw_review_confirm_status == "approved"
    assert response.corrected_quotas[0].quota_id == "C10-9-9"


def test_review_confirm_approve_persists_human_feedback_payload(monkeypatch):
    suggested = [{"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"}]
    match_result = _make_match_result(
        openclaw_review_status="reviewed",
        openclaw_suggested_quotas=suggested,
        openclaw_review_note="OpenClaw 建议替换",
    )
    db = _FakeDb()
    reviewer = SimpleNamespace(id=uuid.uuid4(), email="reviewer@example.com", nickname="人工复核")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="北京"), match_result

    async def _fake_correct_result(*, req, **_kwargs):
        match_result.corrected_quotas = req.corrected_quotas
        match_result.review_status = "corrected"
        match_result.review_note = req.review_note
        return None

    async def _fake_record_openclaw_approved_review_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)
    monkeypatch.setattr(openclaw_api.results_api, "correct_result", _fake_correct_result)
    monkeypatch.setattr(
        "app.services.openclaw_staging.record_openclaw_approved_review_async",
        _fake_record_openclaw_approved_review_async,
    )

    payload = {
        "error_tags": ["wrong_family", "wrong_param"],
        "root_cause": "retrieval_bias",
        "decision_basis": "清单是配管，候选却落到配电箱，且单位 m/台 冲突",
        "action": "retry_search_then_select",
        "note": "先回电气配管语义重搜，再人工确认",
        "review_bucket": "red",
    }
    response = asyncio.run(
        openclaw_api.review_confirm(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewConfirmRequest(
                decision="approve",
                review_note="人工确认通过",
                human_feedback_payload=payload,
            ),
            db=db,
            user=reviewer,
        )
    )

    assert match_result.human_feedback_payload["protocol_version"] == "lobster_review_feedback.v1"
    assert match_result.human_feedback_payload["decision"] == "approve"
    assert match_result.human_feedback_payload["manual_reason_codes"] == ["wrong_family", "wrong_param"]
    assert match_result.human_feedback_payload["manual_note"] == "先回电气配管语义重搜，再人工确认"
    assert response.human_feedback_payload["reviewer"] == "人工复核"


def test_review_confirm_approve_allows_external_final_quota_override(monkeypatch):
    suggested = [{"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"}]
    manual_final = {"quota_id": "C10-8-8", "name": "人工修正定额", "unit": "m"}
    match_result = _make_match_result(
        openclaw_review_status="reviewed",
        openclaw_suggested_quotas=suggested,
        openclaw_review_note="OpenClaw 建议替换",
        openclaw_review_payload={
            "jarvis_absorbable_report": {
                "decision": {"decision_type": "override_within_candidates"},
                "judgment": {"basis_points": []},
                "learning_record": {},
                "promotion_hints": {"experience": {}},
            }
        },
    )
    db = _FakeDb()
    reviewer = SimpleNamespace(id=uuid.uuid4(), email="reviewer@example.com", nickname="人工复核")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="北京"), match_result

    async def _fake_correct_result(*, req, **_kwargs):
        match_result.corrected_quotas = req.corrected_quotas
        match_result.review_status = "corrected"
        match_result.review_note = req.review_note
        return None

    async def _fake_record_openclaw_approved_review_async(*_args, **_kwargs):
        return None

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)
    monkeypatch.setattr(openclaw_api.results_api, "correct_result", _fake_correct_result)
    monkeypatch.setattr(
        "app.services.openclaw_staging.record_openclaw_approved_review_async",
        _fake_record_openclaw_approved_review_async,
    )

    response = asyncio.run(
        openclaw_api.review_confirm(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewConfirmRequest(
                decision="approve",
                review_note="人工终审",
                human_feedback_payload={
                    "source": "lobster_audit",
                    "adopt_openclaw": False,
                    "final_quota": manual_final,
                    "manual_reason_codes": ["manual_override", "param_checked"],
                    "manual_note": "龙虾审计改为人工终版定额",
                    "promotion_decision": "manual_override",
                },
            ),
            db=db,
            user=reviewer,
        )
    )

    assert match_result.corrected_quotas[0].quota_id == "C10-8-8"
    assert response.corrected_quotas[0].quota_id == "C10-8-8"
    assert match_result.human_feedback_payload["adopt_openclaw"] is False
    assert match_result.human_feedback_payload["source"] == "lobster_audit"
    report = match_result.openclaw_review_payload["jarvis_absorbable_report"]
    assert report["decision"]["final_quota_id"] == "C10-8-8"
    assert report["learning_record"]["final_quota_code"] == "C10-8-8"
    assert "manual_reason:manual_override" in report["judgment"]["basis_points"]


def test_matches_review_job_scope_excludes_green_pending_formal_results():
    green = _make_match_result(light_status="green", review_status="pending")
    yellow = _make_match_result(light_status="yellow", review_status="pending")
    reviewed_green = _make_match_result(
        light_status="green",
        review_status="pending",
        openclaw_review_status="reviewed",
        openclaw_review_confirm_status="pending",
    )

    assert openclaw_api._matches_review_job_scope(green, "yellow_red_pending") is False
    assert openclaw_api._matches_review_job_scope(yellow, "yellow_red_pending") is True
    assert openclaw_api._matches_review_job_scope(reviewed_green, "need_review") is True


def test_list_review_pending_includes_legacy_drafted_rows(monkeypatch):
    legacy_draft = _make_match_result(
        light_status="red",
        openclaw_review_status="pending",
        openclaw_review_confirm_status="pending",
        openclaw_decision_type="override_within_candidates",
        openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"}],
        openclaw_review_payload={"decision_type": "override_within_candidates"},
    )

    async def _fake_get_user_task(*_args, **_kwargs):
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(openclaw_api, "get_user_task", _fake_get_user_task)

    response = asyncio.run(
        openclaw_api.list_review_pending(
            task_id=uuid.uuid4(),
            db=_ListDb([legacy_draft]),
            user=SimpleNamespace(),
        )
    )

    assert response.total == 1
    assert response.items[0].id == legacy_draft.id
    assert response.items[0].openclaw_review_status == "reviewed"


def test_to_result_response_normalizes_legacy_openclaw_draft_state():
    legacy_draft = _make_match_result(
        openclaw_review_status="pending",
        openclaw_review_confirm_status="pending",
        openclaw_decision_type="override_within_candidates",
        openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": "建议定额", "unit": "m"}],
        openclaw_reason_codes='["light_red","jarvis_top1_unverified"]',
        openclaw_review_payload='{"decision_type":"override_within_candidates"}',
    )

    response = results_api._to_result_response(legacy_draft)

    assert response.openclaw_review_status == "reviewed"
    assert response.openclaw_reason_codes == ["light_red", "jarvis_top1_unverified"]
    assert response.openclaw_review_payload == {"decision_type": "override_within_candidates"}


def test_resolve_auto_review_run_defaults_to_yellow_red_pending(monkeypatch):
    fake_task = SimpleNamespace(id=uuid.uuid4(), status="completed")

    async def _fake_get_review_job_source_task(**_kwargs):
        return fake_task

    monkeypatch.setattr(openclaw_api, "_get_review_job_source_task", _fake_get_review_job_source_task)

    task, scope, review_job = asyncio.run(
        openclaw_api._resolve_auto_review_run(
            task_id=uuid.uuid4(),
            requested_scope=None,
            review_job_id=None,
            db=SimpleNamespace(),
        )
    )

    assert task is fake_task
    assert scope == "yellow_red_pending"
    assert review_job is None


def test_batch_auto_review_request_defaults_to_yellow_red_pending():
    req = openclaw_api.OpenClawBatchAutoReviewRequest()
    assert req.scope == "yellow_red_pending"


def test_auto_review_result_skips_green_item_even_when_called_directly(monkeypatch):
    task_id = uuid.uuid4()
    result_id = uuid.uuid4()
    match_result = _make_match_result(
        id=result_id,
        light_status="green",
        confidence=95,
        confidence_score=95,
        openclaw_review_status="pending",
    )
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")

    async def _fake_resolve_auto_review_run(**_kwargs):
        return SimpleNamespace(id=task_id), "yellow_red_pending", None

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(id=task_id), match_result

    monkeypatch.setattr(openclaw_api, "_resolve_auto_review_run", _fake_resolve_auto_review_run)
    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    response = asyncio.run(
        openclaw_api.auto_review_result(
            task_id=task_id,
            result_id=result_id,
            req=openclaw_api.OpenClawAutoReviewRequest(),
            db=SimpleNamespace(),
            service_user=service_user,
        )
    )

    assert response.status == "skipped"
    assert response.reviewable is False
    assert match_result.openclaw_review_status == "pending"


def test_build_auto_review_draft_request_uses_better_candidate_for_obvious_family_conflict():
    task = SimpleNamespace(id=uuid.uuid4(), name="脏数据测试", province="北京市建设工程施工消耗量标准(2024)", mode="search", original_filename="dirty_data_sample.xlsx")
    match_result = _make_match_result(
        bill_name="配管（SC20）",
        bill_description="配管SC20，暗敷,从配电箱至灯位",
        bill_unit="m",
        specialty="C4",
        light_status="red",
        confidence=17,
        confidence_score=17,
        quotas=[{"quota_id": "C4-4-37", "name": "配电箱箱体安装 配电箱半周长(m以内) 明装 2.5", "unit": "台"}],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "unit_conflict", "severity": "error", "message": "清单单位 m 与定额单位 台 不一致"},
                            {"type": "category_mismatch", "severity": "error", "message": "配管不应落到配电箱家族"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
        alternatives=[
            {"quota_id": "C4-4-38", "name": "配电箱箱体安装 配电箱半周长(m以内) 暗装 1", "unit": "台"}
        ],
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "override_within_candidates"
    assert req.openclaw_suggested_quotas[0].quota_id == "C4-4-38"
    assert req.openclaw_error_type == "wrong_family"
    assert req.openclaw_error_stage == "final_validator"
    assert "unit_conflict" in (req.openclaw_reason_codes or [])
    assert "category_mismatch" in (req.openclaw_reason_codes or [])
    assert "candidate_source:candidate_pool" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_keeps_candidate_pool_insufficient_when_no_candidates():
    task = SimpleNamespace(id=uuid.uuid4(), name="脏数据测试", province="北京市建设工程施工消耗量标准(2024)", mode="search", original_filename="dirty_data_sample.xlsx")
    match_result = _make_match_result(
        bill_name="阀",
        bill_description="DN50",
        bill_unit="个",
        specialty="C4",
        light_status="red",
        confidence=0,
        confidence_score=0,
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

    assert req.openclaw_decision_type == "candidate_pool_insufficient"
    assert req.openclaw_suggested_quotas in (None, [])
    assert req.openclaw_error_type in {"unknown", "missing_candidate"}


def test_build_auto_review_draft_request_does_not_default_agree_when_yellow_has_issue_signals():
    task = SimpleNamespace(id=uuid.uuid4(), name="安装任务", province="上海市安装工程预算定额(2016)", mode="search", original_filename="demo.xlsx")
    match_result = _make_match_result(
        bill_name="给水塑料管 PPR管",
        bill_description="室内给水塑料管 De25 热熔连接",
        bill_unit="m",
        specialty="C10",
        light_status="yellow",
        confidence=72,
        confidence_score=72,
        quotas=[{"quota_id": "03-10-1-86", "name": "采暖管道 室内镀锌钢管 螺纹连接 DN25", "unit": "m"}],
        alternatives=[
            {"quota_id": "03-10-2-11", "name": "给水塑料管 PPR管 热熔连接 De25", "unit": "m"}
        ],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "category_mismatch", "severity": "error", "message": "给水塑料管不应落到采暖管道"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type != "agree"
    assert "category_mismatch" in (req.openclaw_reason_codes or [])
    assert any(code in (req.openclaw_reason_codes or []) for code in ["jarvis_top1_unverified", "needs_manual_gate", "jarvis_problem_detected"])


def test_build_auto_review_draft_request_yellow_issue_uses_better_candidate_from_pool():
    task = SimpleNamespace(id=uuid.uuid4(), name="安装任务", province="上海市安装工程预算定额(2016)", mode="search", original_filename="demo.xlsx")
    match_result = _make_match_result(
        bill_name="给水塑料管 PPR管",
        bill_description="室内给水塑料管 De25 热熔连接",
        bill_unit="m",
        specialty="C10",
        light_status="yellow",
        confidence=72,
        confidence_score=72,
        quotas=[{"quota_id": "03-10-1-86", "name": "采暖管道 室内镀锌钢管 螺纹连接 DN25", "unit": "m"}],
        alternatives=[
            {"quota_id": "03-10-2-11", "name": "给水塑料管 PPR管 热熔连接 De25", "unit": "m"}
        ],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "category_mismatch", "severity": "error", "message": "给水塑料管不应落到采暖管道"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "override_within_candidates"
    assert req.openclaw_suggested_quotas[0].quota_id == "03-10-2-11"
    assert "candidate_source:candidate_pool" in (req.openclaw_reason_codes or [])
    assert "jarvis_top1_unverified" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_prefers_candidate_with_matching_specialty_book():
    task = SimpleNamespace(id=uuid.uuid4(), name="电气任务", province="上海市安装工程预算定额(2016)", mode="search", original_filename="demo.xlsx")
    match_result = _make_match_result(
        bill_name="配管",
        bill_description="SC20 暗敷",
        bill_unit="m",
        specialty="C4",
        light_status="yellow",
        confidence=75,
        confidence_score=75,
        quotas=[{"quota_id": "C10-1-01", "name": "配管 暗敷 SC20", "unit": "m"}],
        alternatives=[
            {"quota_id": "C10-1-02", "name": "配管 暗敷 SC25", "unit": "m"},
            {"quota_id": "C4-1-02", "name": "配管 暗敷 SC20", "unit": "m"},
        ],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "book_conflict", "severity": "error", "message": "当前 top1 册号不一致"},
                        ]
                    },
                    "query_route": {"route": "installation_spec"},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "override_within_candidates"
    assert req.openclaw_suggested_quotas[0].quota_id == "C4-1-02"
    assert "candidate_source:candidate_pool" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_green_without_issue_signals_can_agree():
    task = SimpleNamespace(id=uuid.uuid4(), name="安装任务", province="上海市安装工程预算定额(2016)", mode="search", original_filename="demo.xlsx")
    match_result = _make_match_result(
        bill_name="钢套管",
        bill_description="刚性防水套管 DN100",
        bill_unit="个",
        specialty="C10",
        light_status="green",
        confidence=93,
        confidence_score=93,
        quotas=[{"quota_id": "03-10-9-01", "name": "刚性防水套管制作安装 DN100", "unit": "个"}],
        alternatives=[{"quota_id": "03-10-9-01", "name": "刚性防水套管制作安装 DN100", "unit": "个"}],
        trace={"steps": [{"final_validation": {"issues": []}, "query_route": {}}]},
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "agree"
    assert [item.model_dump(exclude_none=True) for item in (req.openclaw_suggested_quotas or [])] == [
        {"quota_id": "03-10-9-01", "name": "刚性防水套管制作安装 DN100", "unit": "个", "source": ""}
    ]


def test_build_auto_review_draft_request_yellow_without_issue_signals_can_agree():
    task = SimpleNamespace(id=uuid.uuid4(), name="安装任务", province="上海市安装工程预算定额(2016)", mode="search", original_filename="demo.xlsx")
    match_result = _make_match_result(
        bill_name="卧式磁卡水表",
        bill_description="DN25 丝扣连接",
        bill_unit="组",
        specialty="C10",
        light_status="yellow",
        confidence=78,
        confidence_score=78,
        quotas=[{"quota_id": "03-10-3-295", "name": "IC卡水表安装(螺纹连接) 公称直径 25mm以内", "unit": "组"}],
        alternatives=[{"quota_id": "03-10-3-295", "name": "IC卡水表安装(螺纹连接) 公称直径 25mm以内", "unit": "组"}],
        trace={"steps": [{"final_validation": {"issues": []}, "query_route": {}}]},
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "agree"
    assert "jarvis_top1_verified" in (req.openclaw_reason_codes or [])
    assert [item.model_dump(exclude_none=True) for item in (req.openclaw_suggested_quotas or [])] == [
        {"quota_id": "03-10-3-295", "name": "IC卡水表安装(螺纹连接) 公称直径 25mm以内", "unit": "组", "source": ""}
    ]


def test_build_auto_review_draft_request_red_with_soft_issue_signals_can_agree():
    task = SimpleNamespace(id=uuid.uuid4(), name="安装任务", province="上海市安装工程预算定额(2016)", mode="search", original_filename="demo.xlsx")
    match_result = _make_match_result(
        bill_name="刚性防水套管",
        bill_description="DN100",
        bill_unit="个",
        specialty="C10",
        light_status="red",
        confidence=93,
        confidence_score=93,
        quotas=[{"quota_id": "03-10-9-01", "name": "刚性防水套管制作安装 DN100", "unit": "个"}],
        alternatives=[{"quota_id": "03-10-9-01", "name": "刚性防水套管制作安装 DN100", "unit": "个"}],
        trace={
            "steps": [
                {
                    "final_validation": {
                        "issues": [
                            {"type": "ambiguity_review", "severity": "warning", "message": "候选分差偏小，建议复核"},
                        ]
                    },
                    "query_route": {},
                }
            ]
        },
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert req.openclaw_decision_type == "agree"
    assert "ambiguity_review" in (req.openclaw_reason_codes or [])
    assert "jarvis_top1_verified" in (req.openclaw_reason_codes or [])


def test_build_auto_review_draft_request_carries_qmd_evidence(monkeypatch):
    class _FakeQMDService:
        def recall_for_review_context(self, task, match_result, *, top_k=3):
            return {
                "query": f"{match_result.bill_name} {match_result.bill_description}",
                "count": 2,
                "filters": {},
                "hits": [
                    {
                        "title": "BV-2.5 穿管纠正规则",
                        "heading": "穿管",
                        "category": "rules",
                        "page_type": "rule",
                        "path": "rules/bv-2.5.md",
                        "preview": "BV-2.5 导线通常对应穿管敷设。",
                        "score": 0.95,
                    }
                ],
            }

    monkeypatch.setattr(review_service_api, "get_default_qmd_service", lambda: _FakeQMDService())

    task = SimpleNamespace(id=uuid.uuid4(), name="安装任务", province="北京", mode="search", original_filename="demo.xlsx")
    match_result = _make_match_result(
        bill_name="BV-2.5 导线",
        bill_description="穿管敷设",
        specialty="安装",
        light_status="yellow",
        confidence=70,
        confidence_score=70,
        trace={"steps": [{"final_validation": {"issues": []}, "query_route": {}}]},
    )

    req = asyncio.run(openclaw_api._build_auto_review_draft_request(task, match_result))

    assert "QMD证据" in (req.openclaw_review_note or "")
    evidence = (req.openclaw_review_payload or {}).get("evidence") or {}
    assert evidence["qmd_summary"]["count"] == 2
    assert evidence["qmd_recall"]["hits"][0]["title"] == "BV-2.5 穿管纠正规则"


def test_openclaw_list_source_packs_endpoint(monkeypatch):
    class _FakeSourceLearningService:
        def list_source_packs(self, **kwargs):
            assert kwargs["q"] == "山东"
            assert kwargs["limit"] == 5
            return {
                "items": [
                    {
                        "source_id": "doc-001",
                        "title": "山东安装定额资料",
                        "summary": "source summary",
                        "source_kind": "doc",
                        "province": "山东2025",
                        "specialty": "安装",
                        "created_at": "2026-04-07",
                        "confidence": 80,
                        "full_text_path": "C:/packs/doc-001.md",
                        "evidence_refs": ["E:/Jarvis-Raw/10_docs/demo.txt"],
                        "tags": ["document"],
                    }
                ],
                "total": 1,
            }

    monkeypatch.setattr(openclaw_api, "_get_source_learning_service", lambda: _FakeSourceLearningService())

    app = FastAPI()
    app.include_router(openclaw_router, prefix="/api/openclaw")
    app.dependency_overrides[openclaw_api.get_openclaw_read_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(),
        email="openclaw@test.local",
        nickname="OpenClaw",
        is_admin=True,
    )
    client = TestClient(app)

    response = client.get("/api/openclaw/source-packs", params={"q": "山东", "limit": 5})

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["source_id"] == "doc-001"
    assert payload["items"][0]["province"] == "山东2025"


def test_openclaw_learn_source_pack_endpoint(monkeypatch):
    class _FakeSourceLearningService:
        def extract_source_pack(self, source_id: str, **kwargs):
            assert source_id == "doc-001"
            assert kwargs["dry_run"] is True
            assert kwargs["llm_type"] == "openai"
            return {
                "source_id": "doc-001",
                "title": "山东安装定额资料",
                "chunks": 2,
                "raw_candidates": 3,
                "merged_candidates": 2,
                "staged": 0,
                "staged_ids": [],
                "candidates": [
                    {
                        "candidate_type": "rule",
                        "candidate_title": "SC20 暗配先看材质",
                        "target_layer": "RuleKnowledge",
                    }
                ],
                "pack": {
                    "source_id": "doc-001",
                    "title": "山东安装定额资料",
                    "summary": "source summary",
                    "source_kind": "doc",
                    "province": "山东2025",
                    "specialty": "安装",
                    "created_at": "2026-04-07",
                    "confidence": 80,
                    "full_text_path": "C:/packs/doc-001.md",
                    "evidence_refs": [],
                    "tags": ["document"],
                },
            }

    monkeypatch.setattr(openclaw_api, "_get_source_learning_service", lambda: _FakeSourceLearningService())

    app = FastAPI()
    app.include_router(openclaw_router, prefix="/api/openclaw")
    app.dependency_overrides[openclaw_api.get_openclaw_service_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(),
        email="openclaw@test.local",
        nickname="OpenClaw",
        is_admin=True,
    )
    client = TestClient(app)

    response = client.post(
        "/api/openclaw/source-packs/doc-001/learn",
        json={"dry_run": True, "llm_type": "openai", "chunk_size": 1200, "overlap": 120, "max_chunks": 8},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source_id"] == "doc-001"
    assert payload["merged_candidates"] == 2
    assert payload["candidates"][0]["target_layer"] == "RuleKnowledge"
    assert payload["pack"]["province"] == "山东2025"
