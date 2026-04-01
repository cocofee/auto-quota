import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.api.results import _to_result_response  # noqa: E402
from app.api.tasks import _to_task_response  # noqa: E402
from app.text_utils import normalize_client_filename, repair_mojibake_data, repair_mojibake_text  # noqa: E402


def _garble(text: str) -> str:
    return text.encode("utf-8").decode("latin1")


def test_repair_mojibake_text_keeps_valid_chinese():
    assert repair_mojibake_text("测试清单.xlsx") == "测试清单.xlsx"


def test_normalize_client_filename_repairs_utf8_latin1_mojibake():
    garbled = _garble("测试清单.xlsx")
    assert normalize_client_filename(garbled) == "测试清单.xlsx"


def test_to_task_response_repairs_existing_garbled_task_names():
    task = SimpleNamespace(
        id=uuid.uuid4(),
        name=_garble("安装工程任务"),
        original_filename=_garble("测试清单.xlsx"),
        mode="agent",
        province=_garble("北京定额库"),
        sheet=None,
        limit_count=None,
        use_experience=True,
        agent_llm="deepseek",
        status="completed",
        progress=100,
        progress_current=0,
        progress_message=_garble("已完成"),
        error_message=None,
        stats=None,
        created_at="2026-03-20T00:00:00",
        started_at=None,
        completed_at=None,
        username=None,
        feedback_path=None,
        feedback_uploaded_at=None,
        feedback_stats=None,
    )

    resp = _to_task_response(task)

    assert resp.name == "安装工程任务"
    assert resp.original_filename == "测试清单.xlsx"
    assert resp.province == "北京定额库"
    assert resp.progress_message == "已完成"


def test_repair_mojibake_data_repairs_nested_structures():
    original = {
        "name": "宁夏安装工程计价定额(2019)",
        "items": [
            {"bill_name": "给排水工程"},
            {"quota_name": "铸铁管\n1.安装部位:室内"},
        ],
    }
    garbled = {
        "name": _garble(original["name"]),
        "items": [
            {"bill_name": _garble(original["items"][0]["bill_name"])},
            {"quota_name": _garble(original["items"][1]["quota_name"])},
        ],
    }

    assert repair_mojibake_data(garbled, preserve_newlines=True) == original


def test_to_result_response_repairs_garbled_bill_and_quota_text():
    original_bill = "铸铁管\n1.安装部位:室内"
    original_quota = "给排水管道 室内柔性铸铁排水管"
    result = SimpleNamespace(
        id=uuid.uuid4(),
        index=0,
        bill_code="031001005001",
        bill_name=_garble(original_bill),
        bill_description=_garble(original_bill),
        bill_unit="m",
        bill_quantity=1.0,
        bill_unit_price=None,
        bill_amount=None,
        specialty="C10",
        sheet_name=_garble("表-05 分部分项工程量清单与计价表"),
        section=_garble("给排水管道"),
        quotas=[{"quota_id": "2-10-1-229", "name": _garble(original_quota), "unit": "10m"}],
        alternatives=[{"name": _garble(original_quota), "reason": _garble("室内排水")}],
        confidence=75,
        match_source="search",
        explanation=_garble("DN80→100 向上取档"),
        candidates_count=20,
        is_measure_item=False,
        review_status="pending",
        corrected_quotas=None,
        review_note="",
        created_at=datetime.now(UTC),
    )

    resp = _to_result_response(result)

    assert resp.bill_name == original_bill
    assert resp.bill_description == original_bill
    assert resp.sheet_name == "表-05 分部分项工程量清单与计价表"
    assert resp.section == "给排水管道"
    assert resp.quotas and resp.quotas[0].name == original_quota
    assert resp.alternatives and resp.alternatives[0]["reason"] == "室内排水"
