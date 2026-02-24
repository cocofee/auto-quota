# -*- coding: utf-8 -*-
"""
L4 主动学习模块 — 不确定项聚类请教

匹配完成后，把不确定的条目按类型分组：
- 每组选一条代表标记 [请教]，用户只改这几条代表
- 代表改完后 diff_learner 自然学到经验库，下次同类直通

纯 Python 标注，不调 LLM，耗时可忽略。
"""

from loguru import logger

import config
from src.consistency_checker import _build_fingerprint, _normalize_core_name


def _select_representative(members: list[tuple[int, dict]]) -> int:
    """从同组成员中选出最适合当代表的那条

    选择策略（优先级从高到低）：
    1. 置信度最低的（最需要人看）
    2. 有定额的优先（有东西可改比空白好）
    3. 序号最小的（在 Excel 前面好找）

    参数:
        members: [(原始索引, result), ...] 同组的所有成员

    返回:
        被选中的原始索引
    """
    def sort_key(member):
        idx, result = member
        confidence = result.get("confidence", 0)
        has_quota = 1 if result.get("quotas") else 0  # 有定额=1，无=0
        # 排序：置信度升序 → 有定额降序 → 序号升序
        return (confidence, -has_quota, idx)

    members_sorted = sorted(members, key=sort_key)
    return members_sorted[0][0]  # 返回最优候选的索引


def _make_group_label(fingerprint: str) -> str:
    """从指纹生成可读的组标签

    指纹格式："核心名称|专业|参数1|参数2"
    输出："给水管道DN25" 之类的简短描述
    """
    parts = fingerprint.split("|")
    core_name = parts[0] if parts else "未知"

    # 从参数中提取关键信息
    param_desc = []
    for part in parts[2:]:  # 跳过名称和专业
        if not part:
            continue
        if part.startswith("dn="):
            param_desc.append(f"DN{part[3:]}")
        elif part.startswith("cable_section="):
            param_desc.append(f"{part[14:]}mm²")
        elif part.startswith("material="):
            param_desc.append(part[9:])

    label = core_name
    if param_desc:
        label += " " + " ".join(param_desc[:2])  # 最多取2个参数
    return label


def mark_learning_groups(results: list[dict]) -> list[dict]:
    """L4 主动学习：把不确定项按类型分组并标注代表

    在匹配完成后、输出 Excel 前调用。
    纯 Python 标注，不调 LLM。

    参数:
        results: 全部匹配结果列表

    返回:
        标注后的结果列表（原地修改）
    """
    if not results:
        return results

    threshold = getattr(config, "CONFIDENCE_GREEN", 85)

    # 第1步：筛出不确定项（confidence < 85）
    uncertain = []  # [(原始索引, result)]
    for i, result in enumerate(results):
        confidence = result.get("confidence", 0) or 0
        if confidence < threshold:
            uncertain.append((i, result))

    if not uncertain:
        return results

    # 第2步：按语义指纹分组
    groups = {}  # {指纹: [(索引, result), ...]}
    for idx, result in uncertain:
        item = result.get("bill_item")
        if not item:
            continue
        fp = _build_fingerprint(item)
        if fp not in groups:
            groups[fp] = []
        groups[fp].append((idx, result))

    # 第3步：对 ≥2 条的组标注代表和从属
    groups_marked = 0
    representatives_count = 0
    followers_count = 0

    for fp, members in groups.items():
        if len(members) < 2:
            continue  # 单条不分组

        groups_marked += 1
        group_size = len(members)
        group_label = _make_group_label(fp)

        # 选代表
        rep_idx = _select_representative(members)

        for idx, result in members:
            result["l4_group_id"] = fp
            result["l4_group_size"] = group_size
            result["l4_group_label"] = group_label

            if idx == rep_idx:
                result["l4_representative"] = True
                representatives_count += 1
            else:
                result["l4_follower"] = True
                followers_count += 1

    # 日志汇总
    if groups_marked > 0:
        logger.info(
            f"L4主动学习: {len(uncertain)}条不确定项, "
            f"归为{groups_marked}组, "
            f"标注{representatives_count}条代表(请教), "
            f"{followers_count}条从属(同类待定)"
        )

    return results
