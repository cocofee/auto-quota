# -*- coding: utf-8 -*-
"""
LTR v4 同族排名特征计算（训练和推理共用）

给模型新增5个"同族比较"特征，帮助区分同一参数层级内的候选定额。
核心场景：一堆"管道安装DN20/DN25/DN32"名称几乎一样，模型需要知道
"谁的参数最近"而不只是"谁的语义分高"。

训练和推理必须调用同一个函数，保证特征计算完全一致。
"""
from __future__ import annotations

import math
from collections import defaultdict


# v4特征列名（和ltr_prepare_data.py/ltr_train.py中一致）
V4_FEATURE_NAMES = [
    "param_tier_rank",       # 同tier内参数距离排名 [0,1]
    "family_size",           # log1p(组内数)/log1p(20)
    "param_score_rank",      # 同tier内param_score排名 [0,1]
    "rerank_within_tier",    # 同tier内rerank_score排名 [0,1]
    "dist_to_tier_best",     # 与同tier最优param_score的差 [0,1]
]


def compute_within_tier_features(candidates: list[dict]) -> None:
    """
    计算v4同族排名特征，直接写入每个候选的字典中。

    输入要求：每个候选dict必须有以下字段：
        - param_tier (int): 参数匹配层级（0=硬失败, 1=部分匹配, 2=精确匹配）
        - param_score (float): 参数匹配得分 [0,1]
        - rerank_score (float): 精排分（可能不存在，默认0）
        - _ltr_param (dict): 包含 param_main_rel_dist（主参数相对距离）
        - quota_id (str): 定额编号（用于tie-breaking）

    输出：每个候选新增5个字段（直接修改dict）：
        - _v4_param_tier_rank: [0,1]，0=参数距离最近
        - _v4_family_size: log1p归一化的组内数量
        - _v4_param_score_rank: [0,1]，0=param_score最高
        - _v4_rerank_within_tier: [0,1]，0=rerank分最高
        - _v4_dist_to_tier_best: [0,1]，0=就是最优

    排名规则（固定tie-breaking保证确定性）：
        - param_tier_rank: param_main_rel_dist升序 → quota_id字典序
        - param_score_rank: param_score降序 → quota_id字典序
        - rerank_within_tier: rerank_score降序 → quota_id字典序
    """
    if not candidates:
        return

    # 按tier分组
    tier_groups: dict[int, list[int]] = defaultdict(list)  # tier → 候选索引列表
    for i, c in enumerate(candidates):
        tier = c.get("param_tier", 1)
        tier_groups[tier].append(i)

    # log1p(20)预计算（20是候选池上限）
    log1p_20 = math.log1p(20)

    for tier, indices in tier_groups.items():
        group_size = len(indices)

        # family_size：log1p归一化
        family_size = math.log1p(group_size) / log1p_20 if log1p_20 > 0 else 0.0

        # 提取排序键
        # param_tier_rank：按param_main_rel_dist升序（越小=越近），tie用quota_id升序
        param_dist_keys = []
        for idx in indices:
            c = candidates[idx]
            ltr_param = c.get("_ltr_param", {})
            rel_dist = ltr_param.get("param_main_rel_dist", 1.0)
            qid = str(c.get("quota_id", ""))
            param_dist_keys.append((rel_dist, qid, idx))
        param_dist_keys.sort(key=lambda x: (x[0], x[1]))

        # param_score_rank：按param_score降序（越高=越好），tie用quota_id升序
        param_score_keys = []
        for idx in indices:
            c = candidates[idx]
            ps = c.get("param_score", 0.0)
            qid = str(c.get("quota_id", ""))
            param_score_keys.append((-ps, qid, idx))  # 负号实现降序
        param_score_keys.sort(key=lambda x: (x[0], x[1]))

        # rerank_within_tier：按rerank_score降序，tie用quota_id升序
        rerank_keys = []
        for idx in indices:
            c = candidates[idx]
            rr = c.get("rerank_score", c.get("hybrid_score", 0.0)) or 0.0
            qid = str(c.get("quota_id", ""))
            rerank_keys.append((-rr, qid, idx))
        rerank_keys.sort(key=lambda x: (x[0], x[1]))

        # 归一化分母
        norm = max(group_size - 1, 1)

        # 找同tier内最优param_score（用于dist_to_tier_best）
        best_param_score = max(
            candidates[idx].get("param_score", 0.0) for idx in indices
        )

        # 分配排名
        param_dist_rank_map = {}
        for rank, (_, _, idx) in enumerate(param_dist_keys):
            param_dist_rank_map[idx] = rank / norm

        param_score_rank_map = {}
        for rank, (_, _, idx) in enumerate(param_score_keys):
            param_score_rank_map[idx] = rank / norm

        rerank_rank_map = {}
        for rank, (_, _, idx) in enumerate(rerank_keys):
            rerank_rank_map[idx] = rank / norm

        # 写入每个候选
        for idx in indices:
            c = candidates[idx]
            c["_v4_param_tier_rank"] = param_dist_rank_map[idx]
            c["_v4_family_size"] = family_size
            c["_v4_param_score_rank"] = param_score_rank_map[idx]
            c["_v4_rerank_within_tier"] = rerank_rank_map[idx]
            c["_v4_dist_to_tier_best"] = best_param_score - c.get("param_score", 0.0)
