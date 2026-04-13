# -*- coding: utf-8 -*-
"""
Web端反馈上传模块测试

验证反馈上传API的核心逻辑：
- 任务状态校验（只有已完成的任务才能上传反馈）
- 重复上传拦截
- FeedbackLearner 调用参数正确性
- Task 模型字段更新

通过 mock FeedbackLearner 和 ORM 对象验证逻辑，不需要真正的数据库和Web服务器。
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


# ============================================================
# 辅助：构造模拟对象
# ============================================================

def _make_task(
    status="completed",
    feedback_path=None,
    feedback_stats=None,
    province="北京市建设工程施工消耗量标准(2024)",
):
    """构造模拟的 Task 对象"""
    task = MagicMock()
    task.status = status
    task.feedback_path = feedback_path
    task.feedback_uploaded_at = None
    task.feedback_stats = feedback_stats
    task.province = province
    task.name = "测试任务"
    task.original_filename = "测试清单.xlsx"
    task.id = "test-task-id"
    return task


# ============================================================
# 核心逻辑（从 feedback.py 提取，便于独立测试）
# ============================================================

def _validate_feedback_upload(task):
    """验证任务是否可以上传反馈

    返回 (可以上传, 错误原因)
    """
    if task.status != "completed":
        return False, "只有已完成的任务才能上传反馈"
    if task.feedback_path and (task.feedback_stats or {}).get("status") != "learn_failed":
        return False, "该任务已上传过反馈，不能重复上传"
    return True, None


def _process_feedback(save_path: str):
    """调用 FeedbackLearner 处理反馈Excel

    返回学习统计 {"total": ..., "learned": ...}
    """
    from src.feedback_learner import FeedbackLearner
    fl = FeedbackLearner()
    return fl.learn_from_corrected_excel(save_path)


def _update_task_feedback(task, save_path: str, stats: dict):
    """更新 Task 的反馈字段"""
    task.feedback_path = save_path
    task.feedback_uploaded_at = datetime.now(timezone.utc)
    task.feedback_stats = stats


# ============================================================
# 测试1：任务状态校验
# ============================================================

class TestFeedbackValidation:
    """反馈上传的前置校验"""

    def test_completed_task_can_upload(self):
        """已完成的任务可以上传反馈"""
        task = _make_task(status="completed")
        ok, error = _validate_feedback_upload(task)
        assert ok is True
        assert error is None

    def test_pending_task_rejected(self):
        """等待中的任务不能上传反馈"""
        task = _make_task(status="pending")
        ok, error = _validate_feedback_upload(task)
        assert ok is False
        assert "已完成" in error

    def test_running_task_rejected(self):
        """运行中的任务不能上传反馈"""
        task = _make_task(status="running")
        ok, error = _validate_feedback_upload(task)
        assert ok is False
        assert "已完成" in error

    def test_failed_task_rejected(self):
        """失败的任务不能上传反馈"""
        task = _make_task(status="failed")
        ok, error = _validate_feedback_upload(task)
        assert ok is False

    def test_duplicate_upload_rejected(self):
        """已上传过反馈的任务不能重复上传"""
        task = _make_task(status="completed", feedback_path="/some/path.xlsx")
        ok, error = _validate_feedback_upload(task)
        assert ok is False
        assert "重复" in error

    def test_failed_feedback_can_retry(self):
        """学习失败后的反馈允许重新上传"""
        task = _make_task(
            status="completed",
            feedback_path="/some/path.xlsx",
            feedback_stats={"status": "learn_failed"},
        )
        ok, error = _validate_feedback_upload(task)
        assert ok is True
        assert error is None


# ============================================================
# 测试2：FeedbackLearner 调用
# ============================================================

class TestFeedbackProcessing:
    """反馈处理（调用 FeedbackLearner）"""

    def test_calls_learn_from_corrected_excel(self):
        """应调用 learn_from_corrected_excel 并传入文件路径"""
        mock_stats = {"total": 15, "learned": 8}

        with patch("src.feedback_learner.FeedbackLearner") as MockFL:
            mock_instance = MagicMock()
            mock_instance.learn_from_corrected_excel.return_value = mock_stats
            MockFL.return_value = mock_instance

            result = _process_feedback("/tmp/feedback/test.xlsx")

            mock_instance.learn_from_corrected_excel.assert_called_once_with(
                "/tmp/feedback/test.xlsx"
            )
            assert result == mock_stats

    def test_returns_stats_correctly(self):
        """正确返回学习统计"""
        mock_stats = {"total": 0, "learned": 0}

        with patch("src.feedback_learner.FeedbackLearner") as MockFL:
            mock_instance = MagicMock()
            mock_instance.learn_from_corrected_excel.return_value = mock_stats
            MockFL.return_value = mock_instance

            result = _process_feedback("/tmp/empty.xlsx")
            assert result["total"] == 0
            assert result["learned"] == 0

    def test_exception_propagates(self):
        """FeedbackLearner 异常应该传播出来（由调用方处理）"""
        with patch("src.feedback_learner.FeedbackLearner") as MockFL:
            mock_instance = MagicMock()
            mock_instance.learn_from_corrected_excel.side_effect = Exception("文件损坏")
            MockFL.return_value = mock_instance

            with pytest.raises(Exception, match="文件损坏"):
                _process_feedback("/tmp/bad.xlsx")


# ============================================================
# 测试3：Task 模型更新
# ============================================================

class TestTaskUpdate:
    """Task 反馈字段更新"""

    def test_updates_all_feedback_fields(self):
        """应同时更新 feedback_path, feedback_uploaded_at, feedback_stats"""
        task = _make_task()
        stats = {"total": 10, "learned": 5}

        _update_task_feedback(task, "/path/to/feedback.xlsx", stats)

        assert task.feedback_path == "/path/to/feedback.xlsx"
        assert task.feedback_uploaded_at is not None
        assert task.feedback_stats == {"total": 10, "learned": 5}

    def test_uploaded_at_is_utc(self):
        """feedback_uploaded_at 应该是 UTC 时间"""
        task = _make_task()
        _update_task_feedback(task, "/path.xlsx", {"total": 0, "learned": 0})

        assert task.feedback_uploaded_at.tzinfo == timezone.utc

    def test_stats_preserved_exactly(self):
        """stats 字典应完整保留"""
        task = _make_task()
        stats = {"total": 25, "learned": 12}
        _update_task_feedback(task, "/path.xlsx", stats)

        assert task.feedback_stats["total"] == 25
        assert task.feedback_stats["learned"] == 12
