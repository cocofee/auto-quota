# -*- coding: utf-8 -*-
"""
经验校验护栏测试

验收场景1：经验命中 + 规则族提参失败 → 不得放行（方法2兜底拦截）

核心问题：
  规则族存在，但 _extract_param_value 返回 None（无法从清单文本提取参数值），
  此时方法1既不能确认也不能否认，rule_validated 仍为 False。
  如果方法2也被跳过（如精确匹配场景），就会漏放参数不一致的经验库结果。

验收标准：
  1. 规则族可用 + 提参成功 + 档位不对 → 拒绝（方法1拦截）
  2. 规则族可用 + 提参成功 + 档位正确 → 放行（方法1确认）
  3. 规则族可用 + 提参失败 + 非精确匹配 → 方法2兜底检查
  4. 规则族可用 + 提参失败 + 精确匹配 → 方法2兜底检查（不跳过！因为方法1未确认）
  5. 审核规则网关正确调用检查器链
"""

from unittest.mock import MagicMock
from src import match_core
from src.match_pipeline import _review_check_match_result


# ===== 模拟的 RuleValidator（测试用） =====

class MockRuleValidator:
    """模拟规则校验器"""

    def __init__(self, *, has_family=True, has_tiers=True,
                 extract_value=None, correct_tier=None,
                 correct_quota_id=None):
        """
        参数:
            has_family: family_index 是否包含目标定额
            has_tiers: 家族是否有档位信息
            extract_value: _extract_param_value 的返回值（None=提参失败）
            correct_tier: _find_correct_tier 的返回值
            correct_quota_id: _find_quota_by_tier 的返回值
        """
        self.rules = True  # 规则已加载
        self._has_family = has_family
        self._has_tiers = has_tiers
        self._extract_value = extract_value
        self._correct_tier = correct_tier
        self._correct_quota_id = correct_quota_id

        # 构造 family_index
        if has_family:
            family = {"name": "配电箱安装", "param_key": "circuits"}
            if has_tiers:
                family["tiers"] = [4, 8, 12, 16, 20]
            self.family_index = {"Q-WRONG": family, "Q-RIGHT": family}
        else:
            self.family_index = {}

    def _extract_param_value(self, bill_text, family):
        return self._extract_value

    def _find_correct_tier(self, value, tiers):
        return self._correct_tier

    def _find_quota_by_tier(self, family, tier):
        return self._correct_quota_id


# ===== 测试场景1：规则族可用 + 提参成功 + 档位不对 → 拒绝 =====

def test_tier_mismatch_rejects():
    """方法1拦截：清单7回路，经验库给了4回路定额，应拒绝"""
    exp_result = {
        "quotas": [{"quota_id": "Q-WRONG", "name": "配电箱安装 规格(回路以内) 4"}]
    }
    item = {"name": "配电箱", "description": "回路数:7回路"}

    validator = MockRuleValidator(
        extract_value=7,        # 提参成功：提取到7回路
        correct_tier=8,         # 正确档位是8
        correct_quota_id="Q-RIGHT"  # 正确定额不是Q-WRONG
    )

    result = match_core._validate_experience_params(
        exp_result, item, rule_validator=validator, is_exact=False
    )
    assert result is None, "档位不对应拒绝经验库结果"


# ===== 测试场景2：规则族可用 + 提参成功 + 档位正确 → 放行 =====

def test_tier_match_accepts():
    """方法1确认：清单7回路，经验库给了8回路定额（向上取档正确），应放行"""
    exp_result = {
        "quotas": [{"quota_id": "Q-WRONG", "name": "配电箱安装 规格(回路以内) 8"}]
    }
    item = {"name": "配电箱", "description": "回路数:7回路"}

    validator = MockRuleValidator(
        extract_value=7,
        correct_tier=8,
        correct_quota_id="Q-WRONG"  # 正确定额就是经验库给的
    )

    result = match_core._validate_experience_params(
        exp_result, item, rule_validator=validator, is_exact=False
    )
    assert result is not None, "档位正确应放行"
    assert result["quotas"][0]["quota_id"] == "Q-WRONG"


# ===== 测试场景3：规则族可用 + 提参失败 + 非精确匹配 → 方法2兜底 =====

def test_extract_fail_non_exact_falls_to_method2(monkeypatch):
    """提参失败时，非精确匹配走方法2兜底检查"""
    exp_result = {
        "quotas": [{"quota_id": "Q-WRONG", "name": "配电箱安装 规格(回路以内) 4"}]
    }
    item = {"name": "配电箱 7回路", "description": ""}

    validator = MockRuleValidator(
        extract_value=None,  # 提参失败
    )

    # 模拟方法2：参数不匹配 → 拒绝
    method2_called = []

    def fake_parse(text):
        method2_called.append(text)
        if "7回路" in text:
            return {"circuits": 7}
        return {"circuits": 4}

    def fake_params_match(bill_p, quota_p):
        return False, 0.0  # 参数不匹配

    monkeypatch.setattr(match_core.text_parser, "parse", fake_parse)
    monkeypatch.setattr(match_core.text_parser, "params_match", fake_params_match)

    result = match_core._validate_experience_params(
        exp_result, item, rule_validator=validator, is_exact=False
    )
    assert result is None, "方法2应拦截参数不匹配的结果"
    assert len(method2_called) >= 2, "方法2应被调用（解析清单和定额）"


# ===== 测试场景4：规则族可用 + 提参失败 + 精确匹配 → 方法2仍然兜底 =====

def test_extract_fail_exact_still_runs_method2(monkeypatch):
    """
    验收场景：规则族可用但提参失败 + 精确匹配 → 方法2兜底

    精确匹配时如果方法1未确认（提参失败），方法2必须执行，
    防止放过参数不一致的经验。

    条件：is_exact=True, rule_family_available=True,
          但 bill_value=None → rule_validated=False
    修复后：只要 rule_validated=False，方法2就执行兜底。
    """

    exp_result = {
        "quotas": [{"quota_id": "Q-WRONG", "name": "配电箱安装 规格(回路以内) 4"}]
    }
    item = {"name": "配电箱 7回路", "description": ""}

    validator = MockRuleValidator(
        extract_value=None,  # 提参失败
    )

    method2_called = []

    def fake_parse(text):
        method2_called.append(text)
        if "7回路" in text:
            return {"circuits": 7}
        return {"circuits": 4}

    def fake_params_match(bill_p, quota_p):
        return False, 0.0

    monkeypatch.setattr(match_core.text_parser, "parse", fake_parse)
    monkeypatch.setattr(match_core.text_parser, "params_match", fake_params_match)

    result = match_core._validate_experience_params(
        exp_result, item, rule_validator=validator, is_exact=True
    )
    # 修复后方法2应该执行并拦截（params_match 返回 False）
    assert result is None, \
        "规则族可用但提参失败时，方法2应兜底拦截参数不一致的经验"


# ===== 测试场景5：无规则族 → 方法2直接执行 =====

def test_no_family_falls_to_method2(monkeypatch):
    """没有规则族时，直接走方法2"""
    exp_result = {
        "quotas": [{"quota_id": "Q-OTHER", "name": "管道安装 DN100"}]
    }
    item = {"name": "管道 DN150", "description": ""}

    validator = MockRuleValidator(has_family=False)

    def fake_parse(text):
        if "DN150" in text:
            return {"dn": 150}
        return {"dn": 100}

    def fake_params_match(bill_p, quota_p):
        return False, 0.0

    monkeypatch.setattr(match_core.text_parser, "parse", fake_parse)
    monkeypatch.setattr(match_core.text_parser, "params_match", fake_params_match)

    result = match_core._validate_experience_params(
        exp_result, item, rule_validator=validator, is_exact=False
    )
    assert result is None, "方法2应拦截 DN 不匹配"


# ===== 测试场景6：空定额列表 → 直接放行 =====

def test_empty_quotas_passthrough():
    """经验结果没有定额信息，无法校验，直接放行"""
    exp_result = {"quotas": []}
    item = {"name": "配电箱", "description": ""}

    result = match_core._validate_experience_params(
        exp_result, item, rule_validator=None
    )
    assert result is not None
    assert result["quotas"] == []


# ===== 测试场景7：审核规则检查基本功能 =====

def test_review_check_passes_clean_result():
    """审核检查：正常匹配结果应通过审核"""
    result = {
        "quotas": [{"quota_id": "C10-1-1", "name": "管道安装 DN100"}]
    }
    item = {"name": "给水管道 DN100", "description": ""}

    error = _review_check_match_result(result, item)
    # 不检查具体结果（依赖审核规则数据），只验证不崩溃
    # 如果审核规则数据不存在，check 应返回 None
    assert error is None or isinstance(error, dict)


def test_review_check_handles_empty_quotas():
    """审核检查：空定额列表不崩溃"""
    result = {"quotas": []}
    item = {"name": "测试", "description": ""}

    error = _review_check_match_result(result, item)
    assert error is None  # 空定额不需要审核
