"""
定额规则 — 后置校验模块

从 rule_validator.py 拆分出来，负责匹配后的结果校验：
1. validate_result: 校验单条匹配结果的档位是否正确
2. validate_results: 批量校验所有匹配结果
3. 文本型家族校验（电梯、起重机等 value_type="text" 的家族）
4. 辅助方法（置信度调整、结果标记等）

使用方式（方法重绑定，调用方无需感知拆分）：
    from src.rule_validator import RuleValidator
    rv = RuleValidator()
    rv.validate_results(results)  # 内部自动调用本模块的方法
"""

from loguru import logger
from src.text_parser import parser as text_parser


def validate_result(self, result: dict, bill_text: str) -> dict:
    """
    校验一条匹配结果，必要时纠正档位

    参数:
        result: 匹配结果字典，包含 quotas, confidence 等
        bill_text: 清单完整文本（名称+描述），用于提取参数值

    返回:
        修改后的 result（原地修改并返回）
    """
    if not self.rules or not self.family_index:
        return result

    quotas = result.get("quotas", [])
    if not quotas:
        return result

    # 取主定额（第一条）
    main_quota = quotas[0]
    quota_id = main_quota.get("quota_id", "")

    # 查找该定额所在的家族
    family = self.family_index.get(quota_id)
    if not family:
        # 规则文件里没有这个定额（可能是独立定额）→ 不干预
        return result
    family_name = family.get("name", "")

    # 有档位信息才做校验（纯文字类型的家族不校验）
    tiers = family.get("tiers")
    if not tiers:
        return self._validate_non_tier_family(result, bill_text, family, family_name)

    # 从清单文本中提取数值参数
    bill_value = self._extract_param_value(bill_text, family)

    if bill_value is None:
        # 清单里提取不到参数值 → 不干预
        self._mark_rule_validation(
            result, self._family_note(family_name, "，清单未提供参数值"))
        return result

    # 计算正确的档位（向上取档：选≥bill_value的最小档）
    correct_tier = self._find_correct_tier(bill_value, tiers)
    if correct_tier is None:
        # 参数值超出所有档位范围
        result["rule_note"] = (f"属于家族「{family_name}」，"
                               f"参数值{bill_value}超出最大档{tiers[-1]}")
        return result

    # 找到正确档位对应的定额编号
    correct_quota_id = self._find_quota_by_tier(family, correct_tier)

    # 比较：当前选的定额和正确定额是否一致？
    if quota_id == correct_quota_id:
        # 档位正确 → 置信度加分
        self._bump_confidence(result, add=8, cap=100)
        self._mark_rule_validation(
            result, f"规则校验通过: 「{family_name}」参数{bill_value}→档位{correct_tier}✓")
        return result

    # 档位错误，但未找到对应编号
    if not correct_quota_id:
        result["rule_note"] = (f"属于家族「{family_name}」，"
                               f"参数{bill_value}→档位{correct_tier}，"
                               f"但未找到对应编号")
        return result

    # 档位错误 → 纠正
    old_id = quota_id
    old_name = main_quota.get("name", "")

    # 从家族中找到正确定额的名称
    correct_name = self._find_quota_name(family, correct_quota_id)

    # 纠正主定额
    self._set_main_quota(
        main_quota, correct_quota_id, correct_name or main_quota.get("name", ""))

    # 置信度：纠正后给一个合理分数
    # 但如果原始结果是"回退候选"（参数不匹配），不应强制拉高
    if "回退候选" in result.get("explanation", ""):
        # 回退候选：纠正档位有帮助，但定额本身可能不对，小幅加分
        self._bump_confidence(result, add=10, cap=55)
    else:
        self._bump_confidence(result, floor=75, cap=100)

    self._mark_rule_validation(
        result,
        (f"规则纠正档位: 「{family_name}」"
         f"参数{bill_value}→档位{correct_tier}, "
         f"原{old_id}→改为{correct_quota_id}"),
        corrected=True,
    )

    logger.debug(f"规则纠正: {old_id}({old_name}) → "
                f"{correct_quota_id}({correct_name}), "
                f"参数值={bill_value}, 正确档={correct_tier}")
    return result


def _validate_non_tier_family(self, result: dict, bill_text: str,
                              family: dict, family_name: str) -> dict:
    """无数值档位家族的统一校验入口。"""
    # 文本型家族通用校验（电梯、起重机等 value_type="text" 的家族）
    if family.get("value_type") == "text":
        return self._validate_text_family_result(result, bill_text, family)
    # 其他无数值档位的家族 → 加小幅置信度
    self._bump_confidence(result, add=3, cap=100)
    self._mark_rule_validation(result, self._family_note(family_name))
    return result


def _validate_text_family_result(self, result: dict, bill_text: str,
                                  family: dict) -> dict:
    """
    通用文本型家族校验：检查搜索匹配到的定额档位是否正确

    适用于所有 value_type="text" 的家族（电梯、起重机等），
    不需要为每种设备写专用校验代码。
    """
    family_name = family.get("name", "")
    bill_params = text_parser.parse(bill_text)

    # 解析 quota values 中的数字
    value_tiers = self._parse_text_values(family.get("quotas", []))
    if not value_tiers:
        # 纯文本值，无法做数字校验
        self._mark_rule_validation(result, self._family_note(family_name))
        return result

    # 从清单中提取对应参数
    bill_value = self._extract_text_param(bill_text, bill_params, family)
    if bill_value is None:
        self._mark_rule_validation(
            result, self._family_note(family_name, "，清单未提供对应参数"))
        return result

    # 找正确的档位
    correct_quota, _ = self._find_text_tier(value_tiers, bill_value)
    if not correct_quota:
        return result

    # 比较当前定额和正确定额
    main_quota = result.get("quotas", [{}])[0]
    current_id = main_quota.get("quota_id", "")
    correct_id = correct_quota.get("id")

    # 从正确档位的 value 中提取显示数字
    tier_display = self._extract_tier_display(correct_quota.get("value", ""))

    if current_id == correct_id:
        # 档位正确
        self._bump_confidence(result, add=8, cap=100)
        self._mark_rule_validation(
            result, f"文本型校验通过: 「{family_name}」参数{bill_value}→{tier_display}档 ✓")
    else:
        # 档位错误，纠正
        prefix = family.get("prefix", "")
        correct_name = (
            f"{prefix} {correct_quota.get('value', '')}".strip())
        self._set_main_quota(main_quota, correct_id, correct_name)
        self._bump_confidence(result, floor=75, cap=100)
        self._mark_rule_validation(
            result,
            (f"文本型校验纠正: 「{family_name}」"
             f"参数{bill_value}→{tier_display}档, "
             f"原{current_id}→改为{correct_id}"),
            corrected=True,
        )
        logger.info(
            f"文本型校验纠正: {current_id} → {correct_id} "
            f"(参数{bill_value}→{tier_display}档)")

    return result


def validate_results(self, results: list) -> list:
    """
    批量校验所有匹配结果

    参数:
        results: 匹配结果列表

    返回:
        校验后的结果列表（原地修改）
    """
    if not self.rules:
        return results

    validated = 0
    corrected = 0

    for result in results:
        # 跳过经验库直通的结果（已在经验库匹配阶段做过参数校验）
        if self._is_experience_source(result):
            continue

        # 组合清单文本
        bill_text = self._compose_bill_text(result.get("bill_item", {}))

        self.validate_result(result, bill_text)

        validated, corrected = self._tally_validation_flags(
            result, validated, corrected)

    if validated > 0:
        logger.info(f"规则校验: {validated} 条命中规则, {corrected} 条档位被纠正")

    return results


# ================================================================
# 辅助方法（原来是 @staticmethod，这里加了 self 参数以适配方法重绑定）
# ================================================================

def _is_experience_source(self, result: dict) -> bool:
    """是否为经验库来源结果（批量规则校验时跳过）。"""
    return result.get("match_source", "").startswith("experience")


def _compose_bill_text(self, item: dict) -> str:
    """统一拼接清单名称+描述文本。"""
    return f"{item.get('name', '')} {item.get('description', '')}".strip()


def _tally_validation_flags(self, result: dict, validated: int,
                            corrected: int) -> tuple:
    """根据结果中的规则校验标记累计计数。"""
    if result.get("rule_validated"):
        validated += 1
    if result.get("rule_corrected"):
        corrected += 1
    return validated, corrected


def _family_note(self, family_name: str, suffix: str = "") -> str:
    """统一构建家族说明。"""
    return f"属于家族「{family_name}」{suffix}"


def _mark_rule_validation(self, result: dict, note: str, corrected: bool = False):
    """统一设置规则校验标记字段。"""
    result["rule_validated"] = True
    if corrected:
        result["rule_corrected"] = True
    result["rule_note"] = note


def _bump_confidence(self, result: dict, add: float = 0,
                     floor: float = None, cap: float = 100):
    """统一调整置信度（加分/保底/封顶）。"""
    try:
        conf = float(result.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if add:
        conf += add
    if floor is not None:
        conf = max(conf, float(floor))
    if cap is not None:
        conf = min(conf, float(cap))
    if abs(conf - round(conf)) < 1e-9:
        conf = int(round(conf))
    result["confidence"] = conf


def _set_main_quota(self, main_quota: dict, quota_id: str, quota_name: str):
    """统一写回主定额编号与名称。"""
    main_quota["quota_id"] = quota_id
    main_quota["name"] = quota_name
