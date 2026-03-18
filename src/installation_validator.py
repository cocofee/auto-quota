from __future__ import annotations

from typing import Callable


class InstallationValidator:
    """安装专业核心参数校验器。"""

    GENERIC_SCORE = 0.64

    _CORE_SPECS = (
        ("dn", "DN", "", True),
        ("cable_section", "截面", "", True),
        ("kva", "容量", "kVA", True),
        ("kw", "功率", "kW", True),
        ("circuits", "回路", "", True),
        ("ampere", "电流", "A", True),
    )

    def __init__(self, tier_up_score_fn: Callable[[float, float], float]):
        self._tier_up_score = tier_up_score_fn

    def validate(self, bill_params: dict, quota_params: dict) -> dict:
        details: list[str] = []
        score_sum = 0.0
        check_count = 0
        hard_fail = False
        handled_params: set[str] = set()

        for key, label, unit, hard_match in self._CORE_SPECS:
            if key not in bill_params:
                continue

            handled_params.add(key)
            check_count += 1

            if key not in quota_params:
                score_sum += self.GENERIC_SCORE
                details.append(f"定额无{label}参数(通用定额降权)")
                continue

            bill_value = bill_params[key]
            quota_value = quota_params[key]
            unit_text = unit or ""

            if bill_value == quota_value:
                score_sum += 1.0
                details.append(f"{label}{bill_value}{unit_text}={quota_value}{unit_text} 精确匹配")
                continue

            if bill_value < quota_value:
                tier_score = self._tier_up_score(float(bill_value), float(quota_value))
                score_sum += tier_score
                details.append(f"{label}{bill_value}{unit_text}→{quota_value}{unit_text} 向上取档")
                continue

            if hard_match:
                hard_fail = True
                details.append(f"{label}{bill_value}{unit_text}>{quota_value}{unit_text} 不匹配(清单>定额)")
            else:
                score_sum += 0.3
                details.append(f"{label}{bill_value}{unit_text}≠{quota_value}{unit_text}")

        return {
            "details": details,
            "score_sum": score_sum,
            "check_count": check_count,
            "hard_fail": hard_fail,
            "handled_params": handled_params,
        }
