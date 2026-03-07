# -*- coding: utf-8 -*-
"""
兼容性判断原语 — 统一的材质/连接方式/品类词典和判定函数

背景：
  param_validator、rule_family、review_checkers 三个模块各自维护了
  一套材质兼容、连接方式兼容的判断逻辑，数据和逻辑高度相似但细节不同，
  改一处忘另一处就会产生不一致。

设计：
  本模块提供统一的"底层词典"和"判定函数"，三个模块共用同一份数据。
  - 词典定义在这里（单一事实来源）
  - 判定函数在这里（统一逻辑）
  - 各模块按需调用，不再各自维护独立实现
"""


# ============================================================
# 材质族谱：同族内的材质视为"近似匹配"
# ============================================================
MATERIAL_FAMILIES = {
    "钢塑族": ["钢塑", "钢塑复合管", "衬塑钢管", "涂塑钢管", "衬塑", "涂塑",
               "涂塑碳钢管", "热浸塑钢管",
               "涂覆碳钢管", "涂覆钢管", "PSP钢塑复合管"],
    "铝塑族": ["铝塑", "铝塑复合管", "塑铝稳态管", "铝合金衬塑管"],
    "镀锌钢族": ["镀锌钢管", "镀锌"],
    "焊接钢族": ["焊接钢管", "碳钢", "碳钢管"],
    "不锈钢族": ["不锈钢管", "薄壁不锈钢管", "不锈钢"],
    "铸铁族": ["铸铁管", "球墨铸铁管", "柔性铸铁管", "铸铁"],
    "PPR族": ["PPR管", "PP管", "PPR复合管", "PPR冷水管", "PPR热水管"],
    "PE族": ["PE管", "HDPE管"],
    "PVC族": ["PVC管", "UPVC管", "CPVC管"],
    "铜族": ["铜", "铜管", "铜制", "紫铜管", "黄铜管"],
    "铜芯族": ["铜芯", "铜芯电缆", "铜导线"],
    "铝芯族": ["铝芯", "铝芯电缆", "铝导线", "高压铝芯电缆"],
    "碳钢板族": ["碳钢", "薄钢板", "钢板", "钢板制", "镀锌钢板"],
    "玻璃钢族": ["玻璃钢", "玻璃钢管", "FRP管", "FRP"],
}

# 泛称→具体材质映射：泛称是一大类的统称，和该类下的所有具体材质都兼容
GENERIC_MATERIALS = {
    "塑料管": ["PPR管", "PE管", "PVC管", "UPVC管", "HDPE管", "PP管",
               "ABS管", "CPVC管", "PPR复合管", "PPR冷水管", "PPR热水管"],
    "复合管": ["钢塑复合管", "铝塑复合管", "PPR复合管", "衬塑钢管", "涂塑钢管",
               "钢丝网骨架管", "孔网钢带管", "塑铝稳态管", "铝合金衬塑管"],
    "钢管": ["镀锌钢管", "焊接钢管", "无缝钢管", "不锈钢管", "薄壁不锈钢管"],
    "钢板": ["薄钢板", "镀锌钢板", "不锈钢板", "碳钢板"],
    "钢制": ["镀锌", "镀锌钢管", "焊接钢管", "碳钢"],
    "金属软管": ["不锈钢软管", "碳钢软管", "不锈钢"],
    # 涂塑/涂覆管道在实际造价中借用镀锌钢管定额换主材（行业通用做法）
    "涂塑钢管": ["镀锌钢管", "焊接钢管"],
    "涂塑碳钢管": ["镀锌钢管", "焊接钢管"],
    "涂覆碳钢管": ["镀锌钢管", "焊接钢管"],
    "涂覆钢管": ["镀锌钢管", "焊接钢管"],
    "涂塑": ["镀锌钢管", "镀锌"],
    "涂覆": ["镀锌钢管", "镀锌"],
}

# 材质商品名 → 定额中的标准名称（用于rule_family的材质代号兼容检查）
MATERIAL_TRADE_TO_QUOTA = {
    # 热塑性塑料管（热熔连接）
    "PPR":  ["塑料管", "给水塑料管"],
    "PE":   ["塑料管"],
    "HDPE": ["塑料管"],
    "PB":   ["塑料管"],
    "PERT": ["塑料管"],
    # 热固性塑料管（粘接）
    "PVC":   ["塑料管"],
    "UPVC":  ["塑料管", "排水塑料管"],
    "PVC-U": ["塑料管", "排水塑料管"],
    "CPVC":  ["塑料管"],
    "ABS":   ["塑料管"],
}


# ============================================================
# 连接方式同义词组：组内的连接方式视为等价
# ============================================================
CONNECTION_SYNONYMS = [
    {"承插", "粘接"},      # PVC排水管：承插连接≈粘接
    {"双热熔", "热熔"},    # 双热熔是热熔的变体（PSP钢塑管用双热熔）
]

# 材质 → 默认连接方式
MATERIAL_DEFAULT_CONNECTION = {
    "PPR": "热熔", "PE": "热熔", "HDPE": "热熔", "PB": "热熔", "PERT": "热熔",
    "PVC": "粘接", "UPVC": "粘接", "PVC-U": "粘接", "CPVC": "粘接", "ABS": "粘接",
}


# ============================================================
# 判定函数
# ============================================================

def materials_compatible(mat1: str, mat2: str) -> bool:
    """
    判断两种材质是否兼容（统一判定逻辑）

    兼容规则（按优先级）：
    1. 完全相同 → 兼容
    2. 同族材质（如"钢塑"和"钢塑复合管"都在钢塑族）→ 兼容
    3. 泛称兼容（如"塑料管"和"PPR管"）→ 兼容
    4. 材质代号兼容（如PPR→塑料管）→ 兼容
    5. 子串包含（如"复合管"⊂"钢塑复合管"）→ 兼容
    6. 以上都不满足 → 不兼容
    """
    if mat1 == mat2:
        return True

    # 规则2：同族检查
    for family_members in MATERIAL_FAMILIES.values():
        if mat1 in family_members and mat2 in family_members:
            return True

    # 规则3：泛称兼容
    for generic, specifics in GENERIC_MATERIALS.items():
        if mat1 == generic and mat2 in specifics:
            return True
        if mat2 == generic and mat1 in specifics:
            return True

    # 规则4：材质代号兼容（PPR→塑料管 等）
    for trade_name, quota_names in MATERIAL_TRADE_TO_QUOTA.items():
        m1_upper = mat1.upper()
        m2_upper = mat2.upper()
        if trade_name in m1_upper:
            for qn in quota_names:
                if qn == mat2 or qn in mat2 or mat2 in qn:
                    return True
        if trade_name in m2_upper:
            for qn in quota_names:
                if qn == mat1 or qn in mat1 or mat1 in qn:
                    return True

    # 规则5：子串包含
    if mat1 in mat2 or mat2 in mat1:
        return True

    return False


def connections_compatible(conn1: str, conn2: str) -> bool:
    """
    判断两种连接方式是否兼容（统一判定逻辑）

    兼容规则：
    1. 完全相同 → 兼容
    2. 子串匹配（如"卡压"在"卡压、环压连接"中）→ 兼容
    3. 都含"法兰" → 兼容（焊接法兰/螺纹法兰都是法兰子类型）
    4. 行业同义词（"承插"≈"粘接"、"双热熔"≈"热熔"）→ 兼容
    5. 其他 → 不兼容
    """
    if conn1 == conn2:
        return True

    # 规则2：子串匹配
    if conn1 in conn2 or conn2 in conn1:
        return True

    # 规则3：法兰系列互相兼容
    if "法兰" in conn1 and "法兰" in conn2:
        return True

    # 规则4：行业同义词
    for syn_group in CONNECTION_SYNONYMS:
        c1_in = any(s in conn1 for s in syn_group)
        c2_in = any(s in conn2 for s in syn_group)
        if c1_in and c2_in:
            return True

    return False
