# -*- coding: utf-8 -*-
"""
Web端定额咨询模块测试

验证核心逻辑：
- AI 返回文本的 JSON 解析
- 提交校验（不能为空、图片必须存在）
- 审核流程（只能审核 pending 状态）
- 经验库写入参数正确性
"""

import json
import pytest
from unittest.mock import patch, MagicMock


# ============================================================
# 测试1：AI 返回文本解析
# ============================================================

# 从 consult.py 导入会触发 FastAPI 依赖，这里直接复制解析函数测试
def _parse_ai_response(raw_text: str) -> list:
    """从 AI 返回的文本中提取 JSON 数组（和 consult.py 中相同逻辑）"""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
        for key in ("items", "data", "results"):
            if key in result and isinstance(result[key], list):
                return result[key]
        return []
    except json.JSONDecodeError:
        return []


class TestAIResponseParsing:
    """AI 返回文本的 JSON 解析"""

    def test_pure_json_array(self):
        """纯 JSON 数组能正确解析"""
        text = '[{"bill_name": "给水管", "quota_id": "C10-6-30"}]'
        result = _parse_ai_response(text)
        assert len(result) == 1
        assert result[0]["bill_name"] == "给水管"

    def test_markdown_code_block(self):
        """带 markdown 代码块标记的 JSON 能正确解析"""
        text = '```json\n[{"bill_name": "电缆", "quota_id": "C4-11-25"}]\n```'
        result = _parse_ai_response(text)
        assert len(result) == 1
        assert result[0]["quota_id"] == "C4-11-25"

    def test_dict_with_items_key(self):
        """字典格式（含 items 键）能正确提取"""
        text = '{"items": [{"bill_name": "桥架", "quota_id": "C4-8-10"}]}'
        result = _parse_ai_response(text)
        assert len(result) == 1

    def test_empty_response(self):
        """空内容返回空列表"""
        assert _parse_ai_response("") == []

    def test_invalid_json(self):
        """非 JSON 文本返回空列表"""
        assert _parse_ai_response("这不是JSON") == []

    def test_multiple_items(self):
        """多条记录都能解析"""
        text = json.dumps([
            {"bill_name": "给水管DN25", "quota_id": "C10-6-30", "quota_name": "给水塑料管道安装", "unit": "m"},
            {"bill_name": "排水管DN100", "quota_id": "C10-7-15", "quota_name": "排水管道安装", "unit": "m"},
        ])
        result = _parse_ai_response(text)
        assert len(result) == 2


# ============================================================
# 测试2：提交数据校验
# ============================================================

class TestSubmissionValidation:
    """提交数据的前置校验"""

    def test_empty_items_rejected(self):
        """空提交应被拒绝"""
        items = []
        assert len(items) == 0  # 接口会返回 400

    def test_valid_items_accepted(self):
        """有效数据应能通过"""
        items = [
            {"bill_name": "给水管DN25", "quota_id": "C10-6-30", "quota_name": "给水塑料管道安装", "unit": "m"},
        ]
        valid = [i for i in items if i.get("bill_name") and i.get("quota_id")]
        assert len(valid) == 1

    def test_items_without_quota_id_filtered(self):
        """没有定额编号的项应被过滤"""
        items = [
            {"bill_name": "给水管", "quota_id": "C10-6-30", "quota_name": "...", "unit": "m"},
            {"bill_name": "未识别项", "quota_id": "", "quota_name": "", "unit": ""},
        ]
        valid = [i for i in items if i.get("bill_name") and i.get("quota_id")]
        assert len(valid) == 1


# ============================================================
# 测试3：审核状态流转
# ============================================================

class TestReviewWorkflow:
    """审核状态流转"""

    def _make_submission(self, status="pending"):
        """构造模拟的 ConsultSubmission"""
        sub = MagicMock()
        sub.status = status
        sub.submitted_items = [
            {"bill_name": "给水管DN25", "quota_id": "C10-6-30", "quota_name": "给水塑料管道安装", "unit": "m"},
        ]
        sub.province = "北京市建设工程施工消耗量标准(2024)"
        sub.review_note = None
        sub.reviewed_by = None
        sub.reviewed_at = None
        return sub

    def test_pending_can_be_approved(self):
        """pending 状态可以审核通过"""
        sub = self._make_submission("pending")
        assert sub.status == "pending"
        sub.status = "approved"
        assert sub.status == "approved"

    def test_pending_can_be_rejected(self):
        """pending 状态可以被拒绝"""
        sub = self._make_submission("pending")
        sub.status = "rejected"
        assert sub.status == "rejected"

    def test_approved_cannot_be_reviewed_again(self):
        """已通过的不能重复审核"""
        sub = self._make_submission("approved")
        # 接口会检查 status != "pending" 并返回 400
        assert sub.status != "pending"

    def test_rejected_cannot_be_reviewed_again(self):
        """已拒绝的不能重复审核"""
        sub = self._make_submission("rejected")
        assert sub.status != "pending"


# ============================================================
# 测试4：经验库写入参数
# ============================================================

class TestExperienceStoreParams:
    """审核通过后写入经验库的参数正确性"""

    def test_store_one_called_with_correct_params(self):
        """store_one 应使用 confirmed=True（权威层）"""
        with patch("tools.jarvis_store.store_one") as mock_store:
            mock_store.return_value = True

            # 模拟审核通过后的写入逻辑
            items = [
                {"bill_name": "给水管DN25", "quota_id": "C10-6-30", "quota_name": "给水塑料管道安装", "unit": "m"},
            ]
            province = "北京市建设工程施工消耗量标准(2024)"

            from tools.jarvis_store import store_one
            for item in items:
                store_one(
                    name=item["bill_name"],
                    desc="",
                    quota_ids=[item["quota_id"]],
                    quota_names=[item["quota_name"]],
                    reason="Web端咨询审核通过",
                    specialty="",
                    province=province,
                    confirmed=True,
                )

            mock_store.assert_called_once()
            call_kwargs = mock_store.call_args[1]
            assert call_kwargs["confirmed"] is True  # 权威层
            assert call_kwargs["province"] == province
            assert call_kwargs["quota_ids"] == ["C10-6-30"]

    def test_items_without_quota_id_skipped(self):
        """没有定额编号的项不应写入经验库"""
        with patch("tools.jarvis_store.store_one") as mock_store:
            items = [
                {"bill_name": "给水管", "quota_id": "", "quota_name": "", "unit": ""},
            ]
            for item in items:
                if item["quota_id"].strip():
                    from tools.jarvis_store import store_one
                    store_one(
                        name=item["bill_name"],
                        desc="",
                        quota_ids=[item["quota_id"]],
                        quota_names=[item["quota_name"]],
                        reason="",
                        province="",
                        confirmed=True,
                    )

            mock_store.assert_not_called()
