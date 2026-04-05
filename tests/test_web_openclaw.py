import asyncio
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.api.openclaw import router as openclaw_router  # noqa: E402
from app.api import openclaw as openclaw_api  # noqa: E402
from app.auth import openclaw as openclaw_auth  # noqa: E402
from app.schemas.result import OpenClawReviewConfirmRequest, OpenClawReviewDraftRequest  # noqa: E402
from app.services.openclaw_review_service import OpenClawReviewService  # noqa: E402


def _garble(text: str) -> str:
    return text.encode("utf-8").decode("latin1")


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


def test_openclaw_openapi_contains_bridge_routes_only():
    app = FastAPI()
    app.include_router(openclaw_router, prefix="/api/openclaw")
    client = TestClient(app)

    response = client.get("/api/openclaw/openapi.json")
    assert response.status_code == 200

    payload = response.json()
    assert "/api/openclaw/quota-search/smart" in payload["paths"]
    assert "/api/openclaw/tasks" in payload["paths"]
    assert "/api/openclaw/tasks/{task_id}/review-items" in payload["paths"]
    assert "/api/openclaw/tasks/{task_id}/review-pending" in payload["paths"]
    assert "/api/openclaw/tasks/{task_id}/results/{result_id}/review-draft" in payload["paths"]
    assert "/api/openclaw/tasks/{task_id}/results/{result_id}/review-confirm" in payload["paths"]
    assert "/api/openclaw/promotion-cards" in payload["paths"]
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
        return SimpleNamespace(province="\u5317\u4eac"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    response = asyncio.run(
        openclaw_api.save_review_draft(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewDraftRequest(
                openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": _garble("\u5efa\u8bae\u5b9a\u989d"), "unit": "m"}],
                openclaw_review_note="OpenClaw \u5efa\u8bae\u66ff\u6362",
                openclaw_review_confidence=88,
            ),
            db=db,
            service_user=service_user,
        )
    )

    assert match_result.openclaw_suggested_quotas[0]["name"] == "\u5efa\u8bae\u5b9a\u989d"
    assert response.openclaw_suggested_quotas[0].name == "\u5efa\u8bae\u5b9a\u989d"


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


def test_openclaw_review_context_repairs_garbled_quota_text():
    service = OpenClawReviewService()
    task = SimpleNamespace(
        id=uuid.uuid4(),
        name=_garble("\u5b89\u88c5\u4efb\u52a1"),
        province=_garble("\u5317\u4eac\u5b9a\u989d\u5e93"),
        mode="search",
        original_filename=_garble("\u6d4b\u8bd5\u6e05\u5355.xlsx"),
    )
    match_result = _make_match_result(
        bill_name=_garble("\u7ed9\u6c34\u7ba1\u9053\u5b89\u88c5"),
        bill_description=_garble("\u5ba4\u5185PPR\u7ed9\u6c34\u7ba1"),
        quotas=[{"quota_id": "C10-1-1", "name": _garble("\u5ba4\u5185\u5851\u6599\u7ed9\u6c34\u7ba1"), "unit": "m"}],
        alternatives=[{"quota_id": "C10-1-2", "name": _garble("\u5ba4\u5185\u9540\u950c\u94a2\u7ba1"), "unit": "m"}],
    )

    context = service.build_review_context(task, match_result)

    assert context["task"]["name"] == "\u5b89\u88c5\u4efb\u52a1"
    assert context["task"]["province"] == "\u5317\u4eac\u5b9a\u989d\u5e93"
    assert context["jarvis_result"]["top1_quota_name"] == "\u5ba4\u5185\u5851\u6599\u7ed9\u6c34\u7ba1"
    assert context["candidate_pool"][0]["name"] == "\u5ba4\u5185\u5851\u6599\u7ed9\u6c34\u7ba1"


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
        "root_cause": "classifier_bias",
        "note": "先错专业，再错参数",
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

    assert match_result.human_feedback_payload == payload
    assert response.human_feedback_payload == payload


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
