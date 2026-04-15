import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND = ROOT / "web" / "backend"
if str(WEB_BACKEND) not in sys.path:
    sys.path.insert(0, str(WEB_BACKEND))

from app.api.feedback import _validate_feedback_alignment


def test_validate_feedback_alignment_accepts_matching_rows_with_sheet_variation():
    source_rows = [
        {"code": "030109001006", "name": "离心式泵", "unit": "台", "quantity": 2},
        {"code": "030113017003", "name": "冷却塔", "unit": "台", "quantity": 1},
        {"code": "030113015004", "name": "过滤器", "unit": "台", "quantity": 1},
    ]
    feedback_rows = [
        {"code": "030109001006", "name": "离心式泵", "unit": "台", "quantity": 2},
        {"code": "030113017003", "name": "冷却塔", "unit": "台", "quantity": 1},
        {"code": "030113015004", "name": "过滤器", "unit": "台", "quantity": 1},
    ]

    _validate_feedback_alignment(
        source_rows=source_rows,
        source_sheets=["表-08 分部分项工程和单价措施项目清单与计价表(含分部小计)"],
        feedback_rows=feedback_rows,
        feedback_sheets=["表-08+分部分项工程和单价措施项目清单与计价表"],
    )


def test_validate_feedback_alignment_rejects_wrong_task_feedback():
    source_rows = [
        {"code": "030109001006", "name": "离心式泵", "unit": "台", "quantity": 2},
        {"code": "030113017003", "name": "冷却塔", "unit": "台", "quantity": 1},
        {"code": "030113015004", "name": "过滤器", "unit": "台", "quantity": 1},
    ]
    feedback_rows = [
        {"code": "030801006012", "name": "低压不锈钢管", "unit": "m", "quantity": 20},
        {"code": "030804003001", "name": "不锈钢无缝三通", "unit": "个", "quantity": 2},
        {"code": "030804003002", "name": "不锈钢无缝三通", "unit": "个", "quantity": 4},
    ]

    with pytest.raises(ValueError, match="反馈文件与原任务清单不一致"):
        _validate_feedback_alignment(
            source_rows=source_rows,
            source_sheets=["表-08 分部分项工程和单价措施项目清单与计价表(含分部小计)"],
            feedback_rows=feedback_rows,
            feedback_sheets=["表-08+分部分项工程和单价措施项目清单与计价表"],
        )
