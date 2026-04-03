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


def _make_match_result(**overrides):
    payload = {
        "id": uuid.uuid4(),
        "index": 1,
        "bill_code": "031001001001",
        "bill_name": "给水管道安装",
        "bill_description": "室内 PPR 管",
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
        "openclaw_review_confirm_status": "pending",
        "openclaw_review_confirmed_by": "",
        "openclaw_review_confirm_time": None,
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

    security_schemes = payload["components"]["securitySchemes"]
    assert any(item.get("type") == "apiKey" for item in security_schemes.values())


def test_require_openclaw_api_key_validates_secret(monkeypatch):
    monkeypatch.setattr(openclaw_auth, "OPENCLAW_API_KEY", "secret-key")

    assert asyncio.run(openclaw_auth.require_openclaw_api_key("secret-key")) == "secret-key"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(openclaw_auth.require_openclaw_api_key("wrong-key"))
    assert exc.value.status_code == 401


def test_get_openclaw_service_user_reuses_existing_user(monkeypatch):
    monkeypatch.setattr(openclaw_auth, "OPENCLAW_SERVICE_EMAIL", "openclaw@system.local")
    monkeypatch.setattr(openclaw_auth, "OPENCLAW_SERVICE_QUOTA", 1000)

    user = SimpleNamespace(
        email="openclaw@system.local",
        is_active=True,
        is_admin=True,
        nickname="OpenClaw",
        quota_balance=1000,
    )
    db = _FakeDb(user)

    current_user = asyncio.run(openclaw_auth.get_openclaw_service_user("ignored", db))

    assert current_user is user


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
    assert match_result.openclaw_suggested_quotas[0]["name"] == "建议定额"
    assert response.openclaw_review_status == "reviewed"


def test_save_review_draft_allows_red_light_result(monkeypatch):
    match_result = _make_match_result(
        confidence=68,
        confidence_score=68,
        light_status="red",
    )
    db = _FakeDb()
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="鍖椾含"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    response = asyncio.run(
        openclaw_api.save_review_draft(
            task_id=uuid.uuid4(),
            result_id=match_result.id,
            req=OpenClawReviewDraftRequest(
                openclaw_suggested_quotas=[{"quota_id": "C10-8-8", "name": "绾㈢伅寤鸿瀹氶", "unit": "m"}],
                openclaw_review_note="绾㈢伅缁撴灉鍏佽 OpenClaw 缁欏嚭澶嶆牳寤鸿",
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
    match_result = _make_match_result(
        confidence=95,
        confidence_score=95,
        light_status="green",
    )
    db = _FakeDb()
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")

    async def _fake_get_match_result(**_kwargs):
        return SimpleNamespace(province="鍖椾含"), match_result

    monkeypatch.setattr(openclaw_api, "_get_match_result", _fake_get_match_result)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            openclaw_api.save_review_draft(
                task_id=uuid.uuid4(),
                result_id=match_result.id,
                req=OpenClawReviewDraftRequest(
                    openclaw_suggested_quotas=[{"quota_id": "C10-9-9", "name": "寤鸿瀹氶", "unit": "m"}],
                    openclaw_review_note="缁跨伅涓嶅簲鍐嶈蛋 draft",
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
    staging_calls = {}

    async def _fake_record_openclaw_approved_review_async(task, match_result_arg, *, actor, review_note=""):
        staging_calls["task"] = task
        staging_calls["match_result"] = match_result_arg
        staging_calls["actor"] = actor
        staging_calls["review_note"] = review_note
        return {"audit_error_id": 1, "promotion_id": 2, "queued_rule": True}

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
    assert match_result.quotas == [{"quota_id": "C10-1-1", "name": "原始定额", "unit": "m"}]
    assert match_result.corrected_quotas[0].quota_id == "C10-9-9"
    assert match_result.corrected_quotas[0].name == "建议定额"
    assert match_result.review_status == "corrected"
    assert match_result.openclaw_review_status == "applied"
    assert match_result.openclaw_review_confirm_status == "approved"
    assert match_result.openclaw_review_confirmed_by == "人工复核"
    assert response.corrected_quotas[0].quota_id == "C10-9-9"
    assert response.openclaw_review_status == "applied"
    assert staging_calls["task"].province == "北京"
    assert staging_calls["match_result"] is match_result
    assert staging_calls["actor"] == "人工复核"
    assert staging_calls["review_note"] == "人工确认通过"


def test_create_promotion_card_enqueues_staging_candidate(monkeypatch):
    service_user = SimpleNamespace(email="openclaw@system.local", nickname="OpenClaw")
    captured = {}

    class _FakeStaging:
        def enqueue_promotion(self, payload):
            captured["payload"] = payload
            return 12

        def get_promotion(self, record_id):
            assert record_id == 12
            return {
                "id": 12,
                "source_table": "openclaw_manual_cards",
                "source_record_id": "card-001",
                "target_layer": "MethodCards",
                "status": "draft",
                "review_status": "unreviewed",
            }

    monkeypatch.setattr(openclaw_api, "_get_staging", lambda: _FakeStaging())

    response = asyncio.run(
        openclaw_api.create_promotion_card(
            req=openclaw_api.OpenClawPromotionCardCreateRequest(
                card_id="card-001",
                candidate_type="method",
                candidate_title="桥架审核顺序",
                candidate_summary="先核专业，再核册号，再核单位。",
                candidate_payload={"steps": ["专业", "册号", "单位"]},
                evidence_ref="feishu://doc/card-001",
            ),
            service_user=service_user,
        )
    )

    assert captured["payload"]["source_table"] == "openclaw_manual_cards"
    assert captured["payload"]["source_record_id"] == "card-001"
    assert captured["payload"]["candidate_type"] == "method"
    assert captured["payload"]["target_layer"] == "MethodCards"
    assert captured["payload"]["owner"] == "openclaw@system.local"
    assert response.id == 12
    assert response.target_layer == "MethodCards"
    assert response.review_status == "unreviewed"


def test_build_auto_review_draft_request_keeps_green_jarvis_top1():
    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="Jarvis Task",
        province="beijing",
        status="completed",
        mode="agent",
        original_filename="input.xlsx",
    )
    match_result = _make_match_result(
        confidence=95,
        confidence_score=95,
        light_status="green",
    )

    req = openclaw_api._build_auto_review_draft_request(task, match_result)

    assert req.openclaw_decision_type == "agree"
    assert req.openclaw_suggested_quotas is not None
    assert req.openclaw_suggested_quotas[0].quota_id == "C10-1-1"
    assert req.openclaw_error_stage == "unknown"


def test_build_auto_review_draft_request_promotes_candidate_when_top1_missing():
    task = SimpleNamespace(
        id=uuid.uuid4(),
        name="Jarvis Task",
        province="beijing",
        status="completed",
        mode="agent",
        original_filename="input.xlsx",
    )
    match_result = _make_match_result(
        quotas=[],
        alternatives=[{"quota_id": "C10-8-8", "name": "Candidate Quota", "unit": "m", "source": "trace"}],
        confidence=68,
        confidence_score=68,
        light_status="red",
        trace={
            "steps": [
                {
                    "final_validation": {
                        "status": "manual_review",
                        "issues": [{"type": "category_mismatch"}],
                    }
                }
            ]
        },
    )

    req = openclaw_api._build_auto_review_draft_request(task, match_result)

    assert req.openclaw_decision_type == "override_within_candidates"
    assert req.openclaw_suggested_quotas is not None
    assert req.openclaw_suggested_quotas[0].quota_id == "C10-8-8"
    assert req.openclaw_error_stage == "final_validator"
    assert req.openclaw_error_type == "wrong_family"
