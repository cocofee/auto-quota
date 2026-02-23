# -*- coding: utf-8 -*-
"""
学习笔记模块测试

测试 LearningNotebook 的核心逻辑：
1. record_note: 记录笔记到SQLite
2. mark_user_feedback: 标记用户反馈
3. get_notes_by_pattern: 按模式键查询
4. get_extractable_patterns: 可提炼模式的阈值判断
5. get_stats: 统计信息
6. extract_pattern_key: 模式键提取（模块级函数）
"""

import os
import tempfile

import pytest

from src.learning_notebook import LearningNotebook, extract_pattern_key


@pytest.fixture
def notebook():
    """创建使用临时数据库的 LearningNotebook 实例"""
    # 用 tempfile 替代 tmp_path，避免 Windows 权限问题
    fd, db_path = tempfile.mkstemp(suffix=".db", prefix="test_notes_")
    os.close(fd)
    nb = LearningNotebook(db_path=db_path)
    yield nb
    # 清理临时文件
    try:
        os.unlink(db_path)
    except OSError:
        pass


# ================================================================
# record_note 测试
# ================================================================

class TestRecordNote:
    """测试笔记记录"""

    def test_basic_record(self, notebook):
        """正常记录一条笔记，返回正数 ID"""
        note_id = notebook.record_note({
            "bill_text": "镀锌钢管DN25 丝接",
            "bill_name": "镀锌钢管DN25",
            "bill_description": "丝接",
            "result_quota_ids": ["C10-1-10"],
            "result_quota_names": ["管道安装 DN25"],
            "confidence": 85,
        })
        assert note_id > 0

    def test_empty_bill_text_returns_negative(self, notebook):
        """bill_text 为空 → 返回 -1，不写入数据库"""
        note_id = notebook.record_note({"bill_text": ""})
        assert note_id == -1

        # 确认数据库里确实没有记录
        stats = notebook.get_stats()
        assert stats["total"] == 0

    def test_auto_pattern_key(self, notebook):
        """不传 pattern_key → 自动从 bill_name 提取"""
        notebook.record_note({
            "bill_text": "镀锌钢管DN25 丝接",
            "bill_name": "镀锌钢管DN25",
            "bill_description": "丝接",
        })

        # 用 get_stats 确认记录已写入
        stats = notebook.get_stats()
        assert stats["total"] == 1

    def test_explicit_pattern_key(self, notebook):
        """传入 pattern_key → 使用传入的值"""
        notebook.record_note({
            "bill_text": "镀锌钢管DN25 丝接",
            "pattern_key": "管道安装_镀锌钢管_丝接_DN*",
        })

        notes = notebook.get_notes_by_pattern("管道安装_镀锌钢管_丝接_DN*")
        assert len(notes) == 1

    def test_list_fields_stored_as_json(self, notebook):
        """列表字段（quota_ids, quota_names）应存为JSON并可正确取回"""
        ids = ["C10-1-10", "C10-7-1"]
        names = ["管道安装 DN25", "管卡安装 DN25"]

        notebook.record_note({
            "bill_text": "镀锌钢管DN25 丝接",
            "pattern_key": "test_json",
            "result_quota_ids": ids,
            "result_quota_names": names,
        })

        notes = notebook.get_notes_by_pattern("test_json")
        assert len(notes) == 1
        assert notes[0]["result_quota_ids"] == ids
        assert notes[0]["result_quota_names"] == names


# ================================================================
# mark_user_feedback 测试
# ================================================================

class TestMarkUserFeedback:
    """测试用户反馈标记"""

    def test_mark_confirmed(self, notebook):
        """标记为 confirmed"""
        note_id = notebook.record_note({
            "bill_text": "测试清单",
            "pattern_key": "test_feedback",
        })

        notebook.mark_user_feedback(note_id, "confirmed")

        notes = notebook.get_notes_by_pattern("test_feedback")
        assert notes[0]["user_feedback"] == "confirmed"

    def test_mark_corrected_with_new_ids(self, notebook):
        """标记为 corrected，附带新的定额编号"""
        note_id = notebook.record_note({
            "bill_text": "测试清单",
            "pattern_key": "test_corrected",
            "result_quota_ids": ["C10-1-10"],
        })

        notebook.mark_user_feedback(
            note_id, "corrected",
            corrected_quota_ids=["C10-1-20"]
        )

        notes = notebook.get_notes_by_pattern("test_corrected")
        assert notes[0]["user_feedback"] == "corrected"
        assert notes[0]["corrected_quota_ids"] == ["C10-1-20"]

    def test_invalid_feedback_degrades_to_pending(self, notebook):
        """非法反馈值 → 降级为 pending"""
        note_id = notebook.record_note({
            "bill_text": "测试清单",
            "pattern_key": "test_invalid",
        })

        notebook.mark_user_feedback(note_id, "invalid_value")

        notes = notebook.get_notes_by_pattern("test_invalid")
        assert notes[0]["user_feedback"] == "pending"


# ================================================================
# get_extractable_patterns 测试
# ================================================================

class TestGetExtractablePatterns:
    """测试可提炼模式的阈值判断"""

    def test_below_min_count_not_returned(self, notebook):
        """笔记数不足 min_count → 不返回"""
        # 只写2条（默认 min_count=5）
        for _ in range(2):
            notebook.record_note({
                "bill_text": "测试清单",
                "pattern_key": "too_few",
                "result_quota_ids": ["C10-1-10"],
            })

        patterns = notebook.get_extractable_patterns(min_count=5)
        assert len(patterns) == 0

    def test_meets_threshold_returned(self, notebook):
        """满足所有条件 → 返回该模式"""
        # 写5条相同模式，全部标记为 confirmed
        for i in range(5):
            note_id = notebook.record_note({
                "bill_text": f"镀锌钢管DN{25 + i * 5} 丝接",
                "pattern_key": "管道_镀锌_丝接_DN*",
                "result_quota_ids": ["C10-1-10"],
            })
            notebook.mark_user_feedback(note_id, "confirmed")

        patterns = notebook.get_extractable_patterns(min_count=5, min_confirm_rate=0.5)
        assert len(patterns) == 1
        assert patterns[0]["pattern_key"] == "管道_镀锌_丝接_DN*"
        assert patterns[0]["total_count"] == 5
        assert patterns[0]["consistency"] >= 0.8

    def test_low_confirm_rate_filtered(self, notebook):
        """确认率不足 → 不返回"""
        # 5条中只有1条 confirmed（确认率 20% < 50%）
        for i in range(5):
            note_id = notebook.record_note({
                "bill_text": f"测试清单{i}",
                "pattern_key": "low_confirm",
                "result_quota_ids": ["C10-1-10"],
            })
            if i == 0:
                notebook.mark_user_feedback(note_id, "confirmed")

        patterns = notebook.get_extractable_patterns(min_count=5, min_confirm_rate=0.5)
        assert len(patterns) == 0

    def test_low_consistency_filtered(self, notebook):
        """定额家族不一致（<80%）→ 不返回"""
        # 5条中每条用不同的定额家族
        families = ["C10-1-", "C10-2-", "C10-3-", "C4-1-", "C7-1-"]
        for i in range(5):
            note_id = notebook.record_note({
                "bill_text": f"测试清单{i}",
                "pattern_key": "inconsistent_family",
                "result_quota_ids": [f"{families[i]}10"],
            })
            notebook.mark_user_feedback(note_id, "confirmed")

        patterns = notebook.get_extractable_patterns(min_count=5, min_confirm_rate=0.5)
        assert len(patterns) == 0


# ================================================================
# get_stats 测试
# ================================================================

class TestGetStats:
    """测试统计信息"""

    def test_empty_db(self, notebook):
        """空数据库"""
        stats = notebook.get_stats()
        assert stats["total"] == 0
        assert stats["confirmed"] == 0
        assert stats["corrected"] == 0
        assert stats["pending"] == 0

    def test_counts_by_feedback(self, notebook):
        """不同反馈状态正确计数"""
        # 3条 pending, 2条 confirmed, 1条 corrected
        ids = []
        for i in range(6):
            nid = notebook.record_note({
                "bill_text": f"测试{i}",
                "pattern_key": f"stats_test_{i}",
            })
            ids.append(nid)

        notebook.mark_user_feedback(ids[0], "confirmed")
        notebook.mark_user_feedback(ids[1], "confirmed")
        notebook.mark_user_feedback(ids[2], "corrected", ["C10-1-20"])

        stats = notebook.get_stats()
        assert stats["total"] == 6
        assert stats["confirmed"] == 2
        assert stats["corrected"] == 1
        assert stats["pending"] == 3


# ================================================================
# extract_pattern_key 测试（模块级函数）
# ================================================================

class TestExtractPatternKey:
    """测试模式键提取"""

    def test_dn_replaced_with_wildcard(self):
        """DN+数字 → DN*"""
        key = extract_pattern_key("镀锌钢管DN25", "丝接")
        assert "25" not in key  # 具体数字应被替换

    def test_same_pattern_different_dn(self):
        """不同DN值应产生相同模式键"""
        key1 = extract_pattern_key("镀锌钢管DN25", "丝接")
        key2 = extract_pattern_key("镀锌钢管DN50", "丝接")
        assert key1 == key2

    def test_different_material_different_key(self):
        """不同材质应产生不同模式键"""
        key1 = extract_pattern_key("镀锌钢管DN25", "丝接")
        key2 = extract_pattern_key("PPR管DN25", "热熔")
        assert key1 != key2

    def test_empty_input(self):
        """空输入 → 空字符串"""
        assert extract_pattern_key("", "") == ""

    def test_section_replaced_with_wildcard(self):
        """截面数字被替换"""
        key1 = extract_pattern_key("电力电缆", "4×70mm²")
        key2 = extract_pattern_key("电力电缆", "4×120mm²")
        assert key1 == key2

    def test_long_input_truncated(self):
        """超长输入被截断到80字符"""
        long_name = "很长的清单名称" * 20  # 140个字符
        key = extract_pattern_key(long_name, "")
        assert len(key) <= 80
