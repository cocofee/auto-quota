from __future__ import annotations

import re
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
        frozenset(("电缆", "电缆头")),
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
        frozenset(("cable_head_accessory", "cable_family")),
    }
    _TRAIT_CONFLICT_GROUPS = (
        frozenset(("单栓", "双栓")),
        frozenset(("刚性", "柔性")),
        frozenset(("感烟", "感温")),
        frozenset(("单控", "双控")),
        frozenset(("单相", "三相")),
        frozenset(("三孔", "五孔")),
        frozenset(("带接地", "不带接地")),
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
        ("conduit_dn", "配管直径", "", True),
        ("cable_section", "截面", "", True),
        ("cable_cores", "芯数", "", True),
        ("kva", "容量", "kVA", True),
        ("kw", "功率", "kW", True),
        ("circuits", "回路", "", True),
        ("port_count", "口数", "口", True),
        ("ampere", "电流", "A", True),
        ("half_perimeter", "半周长", "mm", True),
        ("bridge_wh_sum", "桥架宽高和", "mm", True),
        ("perimeter", "周长", "mm", True),
        ("switch_gangs", "联数", "", True),
    )
    _INSTALL_COMPAT_GROUPS = (
        frozenset(("挂墙", "挂壁", "壁挂", "悬挂", "明装", "明敷")),
        frozenset(("暗装", "暗敷", "嵌入", "嵌墙")),
        frozenset(("落地",)),
    )

    def __init__(self, tier_up_score_fn: Callable[[float, float], float]):
        self._tier_up_score = tier_up_score_fn

    @staticmethod
    def _quota_book(qid: str) -> str:
        qid = str(qid or "").strip()
        if len(qid) >= 2 and qid[0] == "C" and qid[1].isalpha():
            letter_map = {'A': 'C1', 'B': 'C2', 'C': 'C3', 'D': 'C4',
                          'E': 'C5', 'F': 'C6', 'G': 'C7', 'H': 'C8',
                          'I': 'C9', 'J': 'C10', 'K': 'C11', 'L': 'C12'}
            return letter_map.get(qid[1], "")
        match = re.match(r"(C\d+)-", qid)
        if match:
            return match.group(1)
        match = re.match(r"(\d+)-", qid)
        if match:
            return f"C{match.group(1)}"
        return ""

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

    @staticmethod
    def _split_multi_value(value: str) -> set[str]:
        return {
            part.strip()
            for part in str(value or "").replace("、", "/").replace("|", "/").split("/")
            if part.strip()
        }

    @classmethod
    def _install_methods_compatible(cls, left: str, right: str) -> bool:
        if not left or not right:
            return False
        if left == right:
            return True
        return any(left in group and right in group for group in cls._INSTALL_COMPAT_GROUPS)

    @classmethod
    def _install_method_group(cls, value: str) -> frozenset[str] | None:
        for group in cls._INSTALL_COMPAT_GROUPS:
            if value in group:
                return group
        return None

    @classmethod
    def _install_methods_hard_conflict(cls, left: str, right: str) -> bool:
        if not left or not right or cls._install_methods_compatible(left, right):
            return False
        left_group = cls._install_method_group(left)
        right_group = cls._install_method_group(right)
        return bool(left_group and right_group and left_group != right_group)

    @staticmethod
    def _split_support_actions(value: str) -> set[str]:
        text = str(value or "").strip()
        if not text:
            return set()
        actions = set()
        if "制作" in text:
            actions.add("制作")
        if "安装" in text:
            actions.add("安装")
        return actions

    @classmethod
    def _support_actions_compatible(cls, bill_value: str, quota_value: str) -> bool:
        bill_actions = cls._split_support_actions(bill_value)
        quota_actions = cls._split_support_actions(quota_value)
        if not bill_actions or not quota_actions:
            return False
        return bill_actions.issubset(quota_actions)

    def _validate_exact_text_param(self,
                                   *,
                                   key: str,
                                   label: str,
                                   strict: bool,
                                   bill_params: dict,
                                   quota_params: dict,
                                   handled_params: set[str],
                                   details: list[str]) -> tuple[float, int, bool]:
        if key not in bill_params:
            return 0.0, 0, False
        handled_params.add(key)
        bill_value = str(bill_params.get(key) or "").strip()
        if not bill_value:
            return 0.0, 0, False
        if key not in quota_params:
            details.append(f"定额无{label}参数(通用定额降权)")
            return self.GENERIC_SCORE, 1, False

        quota_value = str(quota_params.get(key) or "").strip()
        if not quota_value:
            details.append(f"定额无{label}参数(通用定额降权)")
            return self.GENERIC_SCORE, 1, False
        if bill_value == quota_value:
            details.append(f"{label}:{bill_value}")
            return 1.0, 1, False
        if key == "support_action" and self._support_actions_compatible(bill_value, quota_value):
            details.append(f"{label}:{bill_value}->{quota_value}")
            return 0.9, 1, False

        details.append(f"{label}冲突:{bill_value}!={quota_value}")
        if strict:
            return 0.0, 1, True
        return 0.35, 1, False

    def _validate_surface_process(self,
                                  *,
                                  bill_params: dict,
                                  quota_params: dict,
                                  handled_params: set[str],
                                  details: list[str]) -> tuple[float, int, bool]:
        key = "surface_process"
        if key not in bill_params:
            return 0.0, 0, False
        handled_params.add(key)
        bill_parts = self._split_multi_value(bill_params.get(key))
        if not bill_parts:
            return 0.0, 0, False
        if key not in quota_params:
            details.append("定额无表面处理参数(通用定额降权)")
            return self.GENERIC_SCORE, 1, False

        quota_parts = self._split_multi_value(quota_params.get(key))
        if not quota_parts:
            details.append("定额无表面处理参数(通用定额降权)")
            return self.GENERIC_SCORE, 1, False

        overlap = bill_parts & quota_parts
        if overlap:
            score = len(overlap) / max(len(bill_parts), len(quota_parts))
            details.append(f"表面处理:{'/'.join(sorted(overlap))}")
            return max(score, 0.7), 1, False

        details.append(
            f"表面处理偏差:{'/'.join(sorted(bill_parts))}!={'/'.join(sorted(quota_parts))}"
        )
        return 0.35, 1, False

    def _validate_install_method(self,
                                 *,
                                 bill_params: dict,
                                 quota_params: dict,
                                 handled_params: set[str],
                                 details: list[str]) -> tuple[float, int, bool]:
        if "install_method" not in bill_params:
            return 0.0, 0, False
        handled_params.add("install_method")
        bill_value = str(bill_params.get("install_method") or "").strip()
        if not bill_value:
            return 0.0, 0, False

        quota_value = str(quota_params.get("install_method") or "").strip()
        if not quota_value:
            details.append("定额无安装方式参数(通用定额降权)")
            return self.GENERIC_SCORE, 1, False
        if self._install_methods_compatible(bill_value, quota_value):
            details.append(f"安装方式:{bill_value}~{quota_value}")
            return 1.0, 1, False
        if self._install_methods_hard_conflict(bill_value, quota_value):
            details.append(f"安装方式冲突:{bill_value}!={quota_value}")
            return 0.0, 1, True
        details.append(f"安装方式偏差:{bill_value}!={quota_value}")
        return 0.35, 1, False

    def _validate_plugin_preferences(self,
                                     *,
                                     plugin_hints: dict | None,
                                     candidate_quota_id: str,
                                     candidate_quota_name: str,
                                     details: list[str]) -> tuple[float, int, bool]:
        plugin_hints = dict(plugin_hints or {})
        preferred_books = {str(value or "").strip() for value in plugin_hints.get("preferred_books", []) if str(value or "").strip()}
        preferred_names = [str(value or "").strip() for value in plugin_hints.get("preferred_quota_names", []) if str(value or "").strip()]
        avoided_names = [str(value or "").strip() for value in plugin_hints.get("avoided_quota_names", []) if str(value or "").strip()]
        if not any((preferred_books, preferred_names, avoided_names)):
            return 0.0, 0, False

        score = 0.0
        checks = 0
        quota_name = str(candidate_quota_name or "").strip()
        quota_book = self._quota_book(candidate_quota_id)
        if preferred_books:
            checks += 1
            if quota_book in preferred_books:
                score += 0.08
                details.append(f"plugin优先册:{quota_book}")

        if preferred_names:
            checks += 1
            if any(name and name in quota_name for name in preferred_names):
                score += 0.12
                details.append("plugin优先名称命中")

        if avoided_names:
            checks += 1
            if any(name and name in quota_name for name in avoided_names):
                score -= 0.12
                details.append("plugin规避名称命中")

        return score, checks, False

    def validate(self, bill_params: dict, quota_params: dict,
                 bill_canonical_features: dict | None = None,
                 quota_canonical_features: dict | None = None,
                 plugin_hints: dict | None = None,
                 candidate_quota_id: str = "",
                 candidate_quota_name: str = "") -> dict:
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

        for key, label, strict in (
            ("support_scope", "support_scope", True),
            ("support_action", "support_action", False),
            ("sanitary_mount_mode", "sanitary_mount_mode", True),
            ("sanitary_flush_mode", "sanitary_flush_mode", True),
            ("sanitary_water_mode", "用水方式", True),
            ("sanitary_nozzle_mode", "龙头形式", True),
            ("sanitary_tank_mode", "水箱形式", True),
            ("lamp_type", "lamp_type", True),
            ("valve_type", "阀门类型", True),
            ("valve_connection_family", "阀门连接家族", True),
            ("support_material", "支架材质", True),
            ("sanitary_subtype", "卫生器具", True),
            ("cable_type", "线缆类型", True),
            ("cable_head_type", "电缆头类型", True),
            ("conduit_type", "配管类型", True),
            ("box_mount_mode", "配电箱安装方式", True),
            ("bridge_type", "桥架类型", True),
            ("outlet_grounding", "插座接地", True),
            ("wire_type", "线缆型号", False),
        ):
            add_score, add_checks, add_hard_fail = self._validate_exact_text_param(
                key=key,
                label=label,
                strict=strict,
                bill_params=bill_params,
                quota_params=quota_params,
                handled_params=handled_params,
                details=details,
            )
            score_sum += add_score
            check_count += add_checks
            hard_fail = hard_fail or add_hard_fail

        add_score, add_checks, add_hard_fail = self._validate_install_method(
            bill_params=bill_params,
            quota_params=quota_params,
            handled_params=handled_params,
            details=details,
        )
        score_sum += add_score
        check_count += add_checks
        hard_fail = hard_fail or add_hard_fail

        add_score, add_checks, add_hard_fail = self._validate_surface_process(
            bill_params=bill_params,
            quota_params=quota_params,
            handled_params=handled_params,
            details=details,
        )
        score_sum += add_score
        check_count += add_checks
        hard_fail = hard_fail or add_hard_fail

        add_score, add_checks, add_hard_fail = self._validate_plugin_preferences(
            plugin_hints=plugin_hints,
            candidate_quota_id=candidate_quota_id,
            candidate_quota_name=candidate_quota_name,
            details=details,
        )
        score_sum += add_score
        check_count += add_checks
        hard_fail = hard_fail or add_hard_fail

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
