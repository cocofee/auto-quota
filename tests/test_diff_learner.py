# -*- coding: utf-8 -*-
"""
两文件对比学习模块测试

测试 DiffLearner 的核心逻辑：
1. _is_quota_id: 定额编号格式判断
2. _detect_header: 表头检测和列映射
3. diff_and_learn: 对比两份文件，区分 confirmed vs corrected，调用 add_experience
"""

from unittest.mock import MagicMock, patch

import pytest

from src.diff_learner import DiffLearner


# ================================================================
# _is_quota_id 测试
# ================================================================

class TestIsQuotaId:
    """测试定额编号格式识别"""

    def setup_method(self):
        self.learner = DiffLearner()

    @pytest.mark.parametrize("text,expected", [
        ("C10-1-10", True),     # 北京安装定额（标准格式）
        ("C4-8-3", True),       # 北京电气定额
        ("5-325", True),        # 四川定额（无字母前缀）
        ("C10-1-80", True),     # 带字母+数字+横杠
        ("D00003", True),       # D+5位数字格式
        ("A12345", True),       # 字母+5位数字
    ])
    def test_valid_quota_ids(self, text, expected):
        assert self.learner._is_quota_id(text) is expected

    @pytest.mark.parametrize("text,expected", [
        ("", False),            # 空字符串
        ("镀锌钢管", False),     # 纯中文
        ("DN25", False),        # 管径（D后面只有N+2位数字，不是定额格式）
        ("None", False),        # 字面量 None
        ("123", False),         # 纯数字（无横杠，不足5位）
    ])
    def test_invalid_quota_ids(self, text, expected):
        assert self.learner._is_quota_id(text) is expected


# ================================================================
# _detect_header 测试
# ================================================================

class TestDetectHeader:
    """测试表头检测逻辑"""

    def setup_method(self):
        self.learner = DiffLearner()

    def test_standard_header(self):
        """标准表头行（含序号、项目编码、项目名称等）"""
        rows = [
            ("序号", "项目编码", "项目名称", "项目特征", "计量单位", "工程量"),
            (1, "030402011001", "镀锌钢管DN25", "丝接", "m", 100),
        ]
        idx, col_map = self.learner._detect_header(rows)

        assert idx == 0
        assert col_map["index"] == 0
        assert col_map["code"] == 1
        assert col_map["name"] == 2
        assert col_map["description"] == 3

    def test_header_with_offset(self):
        """表头在第2行（第1行是标题）"""
        rows = [
            ("某项目清单", None, None, None, None),
            ("序号", "项目编码", "项目名称", "项目特征", "计量单位"),
            (1, "030402011001", "镀锌钢管DN25", "丝接", "m"),
        ]
        idx, col_map = self.learner._detect_header(rows)

        assert idx == 1
        assert "name" in col_map

    def test_no_header_fallback(self):
        """找不到表头 → 返回默认映射"""
        rows = [
            (1, "030402011001", "镀锌钢管DN25", "丝接", "m"),
            (None, "C10-1-10", "管道安装 DN25", None, None),
        ]
        idx, col_map = self.learner._detect_header(rows)

        # 默认值
        assert idx == 0
        assert col_map["name"] == 2


# ================================================================
# diff_and_learn 测试（mock ExperienceDB 和文件读取）
# ================================================================

class TestDiffAndLearn:
    """测试对比学习的核心逻辑"""

    def _mock_read_mapping(self, learner, original_data, corrected_data):
        """替换 _read_bill_quota_mapping，返回预设数据"""
        call_count = [0]
        def fake_read(path):
            call_count[0] += 1
            if call_count[0] == 1:
                return original_data
            return corrected_data
        learner._read_bill_quota_mapping = fake_read

    @patch("src.experience_db.ExperienceDB")
    def test_same_quotas_counted_as_confirmed(self, MockDB):
        """定额相同 → confirmed +1，默认存候选层(auto_review)"""
        mock_db = MagicMock()
        mock_db.add_experience.return_value = 1
        MockDB.return_value = mock_db

        learner = DiffLearner()

        original = [{
            "bill_name": "镀锌钢管DN25",
            "bill_desc": "丝接",
            "bill_code": "030402",
            "bill_unit": "m",
            "quota_ids": ["C10-1-10"],
            "quota_names": ["管道安装 DN25"],
        }]
        corrected = [{
            "bill_name": "镀锌钢管DN25",
            "bill_desc": "丝接",
            "bill_code": "030402",
            "bill_unit": "m",
            "quota_ids": ["C10-1-10"],
            "quota_names": ["管道安装 DN25"],
        }]

        self._mock_read_mapping(learner, original, corrected)
        # 默认 all_authority=False → 未修改的存候选层(auto_review)
        result = learner.diff_and_learn("fake_orig.xlsx", "fake_corr.xlsx")

        assert result["confirmed"] == 1
        assert result["corrected"] == 0
        # 默认行为：未修改的存候选层(auto_review)，不再是权威层
        call_args = mock_db.add_experience.call_args
        assert call_args[1]["source"] == "auto_review"

    @patch("src.experience_db.ExperienceDB")
    def test_different_quotas_counted_as_corrected(self, MockDB):
        """定额不同 → corrected +1，写入 user_correction"""
        mock_db = MagicMock()
        mock_db.add_experience.return_value = 1
        MockDB.return_value = mock_db

        learner = DiffLearner()

        original = [{
            "bill_name": "镀锌钢管DN25",
            "bill_desc": "丝接",
            "bill_code": "030402",
            "bill_unit": "m",
            "quota_ids": ["C10-1-10"],
            "quota_names": ["管道安装 DN25"],
        }]
        corrected = [{
            "bill_name": "镀锌钢管DN25",
            "bill_desc": "丝接",
            "bill_code": "030402",
            "bill_unit": "m",
            "quota_ids": ["C10-1-20"],  # 改了定额
            "quota_names": ["管道安装 DN32"],
        }]

        self._mock_read_mapping(learner, original, corrected)
        result = learner.diff_and_learn("fake_orig.xlsx", "fake_corr.xlsx")

        assert result["corrected"] == 1
        assert result["confirmed"] == 0
        # 修正类型应为 user_correction
        call_args = mock_db.add_experience.call_args
        assert call_args[1]["source"] == "user_correction"
        assert call_args[1]["confidence"] == 95

    @patch("src.experience_db.ExperienceDB")
    def test_both_empty_quotas_counted_as_skipped(self, MockDB):
        """原始和修正都没有定额 → skipped +1"""
        mock_db = MagicMock()
        MockDB.return_value = mock_db

        learner = DiffLearner()

        original = [{
            "bill_name": "某清单",
            "bill_desc": "",
            "quota_ids": [],
            "quota_names": [],
        }]
        corrected = [{
            "bill_name": "某清单",
            "bill_desc": "",
            "quota_ids": [],
            "quota_names": [],
        }]

        self._mock_read_mapping(learner, original, corrected)
        result = learner.diff_and_learn("fake_orig.xlsx", "fake_corr.xlsx")

        assert result["skipped"] == 1
        mock_db.add_experience.assert_not_called()

    @patch("src.experience_db.ExperienceDB")
    def test_empty_original_returns_zero(self, MockDB):
        """原始文件没数据 → 直接返回空结果"""
        mock_db = MagicMock()
        MockDB.return_value = mock_db

        learner = DiffLearner()
        self._mock_read_mapping(learner, [], [{"bill_name": "test", "quota_ids": ["C10-1-10"]}])
        result = learner.diff_and_learn("fake_orig.xlsx", "fake_corr.xlsx")

        assert result["total"] == 0

    @patch("src.experience_db.ExperienceDB")
    def test_corrected_shorter_than_original_skips_extra(self, MockDB):
        """修正文件比原始短 → 多出来的原始条目计为 skipped"""
        mock_db = MagicMock()
        mock_db.add_experience.return_value = 1
        MockDB.return_value = mock_db

        learner = DiffLearner()

        original = [
            {"bill_name": "清单1", "bill_desc": "", "bill_code": "", "bill_unit": "",
             "quota_ids": ["C10-1-10"], "quota_names": ["管道安装"]},
            {"bill_name": "清单2", "bill_desc": "", "bill_code": "", "bill_unit": "",
             "quota_ids": ["C10-1-20"], "quota_names": ["管道安装"]},
        ]
        corrected = [
            {"bill_name": "清单1", "bill_desc": "", "bill_code": "", "bill_unit": "",
             "quota_ids": ["C10-1-10"], "quota_names": ["管道安装"]},
        ]

        self._mock_read_mapping(learner, original, corrected)
        result = learner.diff_and_learn("fake_orig.xlsx", "fake_corr.xlsx")

        assert result["confirmed"] == 1
        assert result["skipped"] == 1
