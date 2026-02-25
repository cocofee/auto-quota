import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.api.tasks import _normalize_create_task_inputs  # noqa: E402


def test_normalize_create_task_inputs_happy_path():
    province, sheet, llm = _normalize_create_task_inputs(
        " 北京2024 ",
        " 给排水 ",
        " deepseek ",
    )
    assert province == "北京2024"
    assert sheet == "给排水"
    assert llm == "deepseek"


def test_normalize_create_task_inputs_rejects_empty_province():
    with pytest.raises(HTTPException) as exc:
        _normalize_create_task_inputs("   ", None, None)
    assert exc.value.status_code == 400
    assert "province" in str(exc.value.detail)


def test_normalize_create_task_inputs_rejects_overlong_fields():
    with pytest.raises(HTTPException):
        _normalize_create_task_inputs("p" * 256, None, None)
    with pytest.raises(HTTPException):
        _normalize_create_task_inputs("ok", "s" * 101, None)
    with pytest.raises(HTTPException):
        _normalize_create_task_inputs("ok", None, "m" * 51)
