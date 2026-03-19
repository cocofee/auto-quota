from __future__ import annotations

from typing import Callable


class InstallationValidator:
    """安装专业核心参数校验器。"""

    GENERIC_SCORE = 0.64
    _SYSTEM_HARD_CONFLICTS = {
        frozenset(("消防", "电气")),
        frozenset(("电气", "给排水")),
        frozenset(("电气", "通风空调")),
        frozenset(("消防", "给排水")),
        frozenset(("消防", "通风空调")),
        frozenset(("给排水", "通风空调")),
    }
    _ENTITY_HARD_CONFLICTS = {
        frozenset(("电缆", "配管")),
        frozenset(("电缆", "桥架")),
        frozenset(("配管", "桥架")),
        frozenset(("开关", "插座")),
        frozenset(("闸阀", "止回阀")),
        frozenset(("闸阀", "蝶阀")),
        frozenset(("止回阀", "蝶阀")),
        frozenset(("坐便器", "蹲便器")),
        frozenset(("坐便器", "小便器")),
        frozenset(("坐便器", "洗脸盆")),
        frozenset(("坐便器", "洗涤盆")),
        frozenset(("蹲便器", "小便器")),
        frozenset(("洗脸盆", "淋浴器")),
        frozenset(("洗脸盆", "小便器")),
        frozenset(("洗脸盆", "洗涤盆")),
        frozenset(("洗涤盆", "小便器")),
        frozenset(("洗涤盆", "淋浴器")),
        frozenset(("压力开关", "开关插座")),
        frozenset(("压力开关", "开关")),
        frozenset(("报警按钮", "开关插座")),
        frozenset(("报警按钮", "开关")),
        frozenset(("风阀", "风管")),
        frozenset(("风口", "风管")),
        frozenset(("风阀", "风口")),
        frozenset(("卫生间通风器", "风机")),
        frozenset(("卫生间通风器", "排气扇")),
        frozenset(("排气扇", "风机")),
        frozenset(("暖风机", "风机")),
        frozenset(("套管", "管道")),
        frozenset(("水泵", "管道")),
        frozenset(("消火栓", "管道")),
        frozenset(("消火栓", "阀门")),
        frozenset(("管道", "阀门")),
        frozenset(("配电箱", "阀门")),
        frozenset(("水表", "阀门")),
        frozenset(("倒流防止器", "阀门")),
        frozenset(("过滤器", "阀门")),
        frozenset(("软接头", "阀门")),
        frozenset(("减压器", "阀门")),
    }
    _FAMILY_HARD_CONFLICTS = {
        frozenset(("bridge_support", "bridge_raceway")),
        frozenset(("bridge_support", "pipe_support")),
        frozenset(("pipe_support", "bridge_raceway")),
        frozenset(("valve_body", "valve_accessory")),
        frozenset(("air_terminal", "air_valve")),
        frozenset(("air_terminal", "air_device")),
        frozenset(("air_valve", "air_device")),
        frozenset(("electrical_box", "conduit_raceway")),
        frozenset(("electrical_box", "cable_family")),
    }
    _TRAIT_CONFLICT_GROUPS = (
        frozenset(("单栓", "双栓")),
        frozenset(("刚性", "柔性")),
        frozenset(("感烟", "感温")),
        frozenset(("单控", "双控")),
        frozenset(("单相", "三相")),
        frozenset(("三孔", "五孔")),
        frozenset(("吸顶灯", "筒灯", "应急灯")),
        frozenset(("吸顶式", "嵌入式", "壁挂式", "落地式", "悬挂式")),
        frozenset(("离心式", "轴流式")),
        frozenset(("湿式", "干式", "预作用", "雨淋")),
        frozenset(("直立型", "下垂型", "边墙型")),
        frozenset(("托盘式", "槽式", "梯式", "线槽")),
        frozenset(("一般管架", "支撑架")),
        frozenset(("防雨百叶", "格栅风口", "钢百叶窗", "板式排烟口")),
    )

    _CORE_SPECS = (
        ("dn", "DN", "", True),
        ("cable_section", "截面", "", True),
        ("cable_cores", "芯数", "", True),
        ("kva", "容量", "kVA", True),
        ("kw", "功率", "kW", True),
        ("circuits", "回路", "", True),
        ("port_count", "口数", "口", True),
        ("ampere", "电流", "A", True),
    )

    def __init__(self, tier_up_score_fn: Callable[[float, float], float]):
        self._tier_up_score = tier_up_score_fn

    @classmethod
    def _is_conflict(cls, left: str, right: str, conflicts: set[frozenset[str]]) -> bool:
        return bool(left and right and left != right and frozenset((left, right)) in conflicts)

    @classmethod
    def _find_trait_conflict(cls, bill_traits: list[str], quota_traits: list[str]) -> tuple[str, str] | None:
        bill_set = {str(item or "").strip() for item in bill_traits if str(item or "").strip()}
        quota_set = {str(item or "").strip() for item in quota_traits if str(item or "").strip()}
        if not bill_set or not quota_set:
            return None
        for group in cls._TRAIT_CONFLICT_GROUPS:
            bill_hit = next((trait for trait in group if trait in bill_set), "")
            quota_hit = next((trait for trait in group if trait in quota_set), "")
            if bill_hit and quota_hit and bill_hit != quota_hit:
                return bill_hit, quota_hit
        return None

    def validate(self, bill_params: dict, quota_params: dict,
                 bill_canonical_features: dict | None = None,
                 quota_canonical_features: dict | None = None) -> dict:
        details: list[str] = []
        score_sum = 0.0
        check_count = 0
        hard_fail = False
        handled_params: set[str] = set()
        bill_canonical_features = dict(bill_canonical_features or {})
        quota_canonical_features = dict(quota_canonical_features or {})

        bill_system = str(bill_canonical_features.get("system") or "").strip()
        quota_system = str(quota_canonical_features.get("system") or "").strip()
        if self._is_conflict(bill_system, quota_system, self._SYSTEM_HARD_CONFLICTS):
            hard_fail = True
            check_count += 1
            details.append(f"系统冲突:{bill_system}!={quota_system}")

        bill_entity = str(bill_canonical_features.get("entity") or "").strip()
        quota_entity = str(quota_canonical_features.get("entity") or "").strip()
        if self._is_conflict(bill_entity, quota_entity, self._ENTITY_HARD_CONFLICTS):
            hard_fail = True
            check_count += 1
            details.append(f"实体冲突:{bill_entity}!={quota_entity}")

        bill_family = str(bill_canonical_features.get("family") or "").strip()
        quota_family = str(quota_canonical_features.get("family") or "").strip()
        if self._is_conflict(bill_family, quota_family, self._FAMILY_HARD_CONFLICTS):
            hard_fail = True
            check_count += 1
            details.append(f"家族冲突:{bill_family}!={quota_family}")

        trait_conflict = self._find_trait_conflict(
            list(bill_canonical_features.get("traits") or []),
            list(quota_canonical_features.get("traits") or []),
        )
        if trait_conflict:
            hard_fail = True
            check_count += 1
            details.append(f"特征冲突:{trait_conflict[0]}!={trait_conflict[1]}")

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
                details.append(f"{label}{bill_value}{unit_text}≥{quota_value}{unit_text}")

        return {
            "details": details,
            "score_sum": score_sum,
            "check_count": check_count,
            "hard_fail": hard_fail,
            "handled_params": handled_params,
        }
