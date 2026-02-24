# -*- coding: utf-8 -*-
"""
L3 一致性反思模块

在所有匹配和规则校验完成后，检查同类清单的定额一致性：
1. 按"语义指纹"把同类清单分组
2. 检测组内定额是否一致
3. 不一致时用加权投票纠正少数派

纯 Python 分析，不调 LLM，耗时可忽略。
"""

import re

from loguru import logger

import config


# ============================================================
# 匹配来源 → 投票权重
# ============================================================

# 来源权重：用户确认 > 规则直通 > 经验间接 > LLM分析 > 快通道 > 纯搜索
_SOURCE_WEIGHTS = {
    "experience_exact": 5.0,
    "experience_similar": 3.0,
    "experience_exact_confirmed": 3.5,
    "experience_similar_confirmed": 3.0,
    "rule_direct": 4.0,
    "agent": 2.0,
    "agent_fastpath": 1.5,
    "search": 1.0,
}


# ============================================================
# 语义指纹：判断哪些清单是"同类"
# ============================================================

# 位置前缀（去掉后不影响定额选择）
_LOCATION_PREFIXES = ("室内", "室外", "户内", "户外")

# 动作后缀（去掉后不影响定额选择）
_ACTION_SUFFIXES = ("安装", "敷设", "制作", "铺设", "制作安装")


def _normalize_core_name(name: str) -> str:
    """提取清单名称的核心部分

    去掉位置前缀（室内/室外）和动作后缀（安装/敷设），
    让"室内给水管道安装"和"给水管道"归为同类。
    """
    if not name:
        return ""

    result = name.strip()

    # 去位置前缀
    for prefix in _LOCATION_PREFIXES:
        if result.startswith(prefix):
            result = result[len(prefix):]
            break  # 只去一个前缀

    # 去动作后缀（按长度降序匹配，避免"安装"匹配到"制作安装"的尾部）
    for suffix in sorted(_ACTION_SUFFIXES, key=len, reverse=True):
        if result.endswith(suffix) and len(result) > len(suffix):
            result = result[:-len(suffix)]
            break

    return result.strip()


def _build_fingerprint(item: dict) -> str:
    """为清单项生成语义指纹

    指纹 = "核心名称|专业|参数1|参数2|..."
    指纹相同 = 应该套同一个定额。

    参数:
        item: bill_item 字典（包含 name, specialty, params 等）

    返回:
        指纹字符串
    """
    name = item.get("name", "")
    core_name = _normalize_core_name(name)
    specialty = item.get("specialty", "") or ""
    params = item.get("params", {}) or {}

    # 影响定额选择的关键参数（排序保证顺序一致）
    param_parts = []
    for key in ("dn", "cable_section", "material", "connection", "kva", "shape"):
        val = params.get(key)
        if val is not None and val != "":
            param_parts.append(f"{key}={val}")

    # 线缆类型（来自 bill_cleaner 的自动识别）
    cable_type = item.get("cable_type", "")
    if cable_type:
        param_parts.append(f"cable_type={cable_type}")

    return f"{core_name}|{specialty}|{'|'.join(sorted(param_parts))}"


# ============================================================
# 投票与纠正
# ============================================================

def _compute_vote_weight(result: dict) -> float:
    """计算一条结果的投票权重

    权重 = 来源基础权重 × (置信度 / 100)
    """
    source = result.get("match_source", "search") or "search"
    confidence = result.get("confidence", 50) or 50

    # 前缀匹配（按长度降序，避免短前缀抢先匹配长前缀的变体）
    base_weight = 1.0
    for key, weight in sorted(_SOURCE_WEIGHTS.items(), key=lambda x: len(x[0]), reverse=True):
        if source.startswith(key):
            base_weight = weight
            break

    return base_weight * (confidence / 100.0)


def _quota_signature(result: dict) -> tuple:
    """提取定额编号签名，用于一致性比对"""
    quotas = result.get("quotas") or []
    return tuple(
        str(q.get("quota_id", "")).strip()
        for q in quotas if q.get("quota_id")
    )


def _apply_correction(result: dict, winner_results: list[dict]) -> bool:
    """将纠正应用到一条结果上

    从 winner 组选置信度最高的作为模板，复制其定额信息。
    置信度适当扣分，标记反思纠正。

    返回: True 表示实际执行了纠正，False 表示跳过
    """
    penalty = getattr(config, "REFLECTION_CONFIDENCE_PENALTY", 5)

    # 选最佳模板
    template = max(winner_results, key=lambda r: r.get("confidence", 0))
    template_quotas = template.get("quotas", [])
    if not template_quotas:
        return False  # 模板没有定额，不纠正

    # 保存旧信息
    old_quotas = result.get("quotas", [])
    old_main_id = old_quotas[0].get("quota_id", "?") if old_quotas else "?"
    old_confidence = result.get("confidence", 0)

    # 复制定额信息
    result["quotas"] = [dict(q) for q in template_quotas]

    # 调整置信度（不能因反思变得比模板更自信）
    template_conf = template.get("confidence", 50)
    new_conf = min(template_conf, old_confidence + 10) - penalty
    new_conf = max(new_conf, 30)  # 下限保护
    result["confidence"] = new_conf

    # 追加说明
    new_main_id = template_quotas[0].get("quota_id", "?")
    old_explanation = result.get("explanation", "") or ""
    result["explanation"] = (
        f"{old_explanation} | L3反思纠正: {old_main_id}→{new_main_id}（同类一致性）"
    ).strip(" |")

    # 标记
    result["reflection_corrected"] = True
    result["reflection_old_quota"] = old_main_id
    return True


# ============================================================
# 主入口
# ============================================================

def check_and_fix(results: list[dict]) -> list[dict]:
    """L3 一致性反思：检查同类清单匹配结果的一致性

    在所有匹配和规则校验完成后调用。
    纯 Python 分析，不调 LLM，时间复杂度 O(n)。

    参数:
        results: 全部匹配结果列表

    返回:
        处理后的结果列表（原地修改）
    """
    # 总开关
    if not getattr(config, "REFLECTION_ENABLED", True):
        return results

    if not results or len(results) < 2:
        return results

    skip_conf = getattr(config, "REFLECTION_SKIP_HIGH_CONFIDENCE", 90)
    min_ratio = getattr(config, "REFLECTION_MIN_VOTE_RATIO", 1.5)

    # 第1步：按语义指纹分组
    groups = {}  # {指纹: [(index, result), ...]}
    for i, result in enumerate(results):
        item = result.get("bill_item", {})
        if not item:
            continue
        fp = _build_fingerprint(item)
        if fp not in groups:
            groups[fp] = []
        groups[fp].append((i, result))

    # 第2步：检测不一致并纠正
    groups_checked = 0
    inconsistencies_found = 0
    corrections_made = 0
    conflicts_flagged = 0

    for fp, members in groups.items():
        if len(members) < 2:
            continue

        groups_checked += 1

        # 按定额签名分组
        sig_groups = {}  # {签名: [result, ...]}
        for _idx, result in members:
            sig = _quota_signature(result)
            sig_key = sig if sig else ("EMPTY",)
            if sig_key not in sig_groups:
                sig_groups[sig_key] = []
            sig_groups[sig_key].append(result)

        if len(sig_groups) <= 1:
            continue  # 一致，跳过

        inconsistencies_found += 1

        # 计算每个变体的总票权
        sig_scores = {}
        for sig, sig_results in sig_groups.items():
            sig_scores[sig] = sum(_compute_vote_weight(r) for r in sig_results)

        # 选票权最高的为 winner
        sorted_sigs = sorted(sig_scores.items(), key=lambda x: x[1], reverse=True)
        winner_sig = sorted_sigs[0][0]
        winner_score = sorted_sigs[0][1]
        runner_up_score = sorted_sigs[1][1] if len(sorted_sigs) > 1 else 0

        # 安全阈值：票权差距不够大则只标记不纠正
        if runner_up_score > 0 and winner_score / runner_up_score < min_ratio:
            conflicts_flagged += 1
            # 标记冲突但不纠正
            for sig, sig_results in sig_groups.items():
                for result in sig_results:
                    result["reflection_conflict"] = True
            sample_name = members[0][1].get("bill_item", {}).get("name", "?")
            logger.info(f"L3反思: [{sample_name}] {len(members)}条同类清单存在定额冲突"
                        f"（{len(sig_groups)}种定额，票权比不足{min_ratio}，标记待人工确认）")
            continue

        # 纠正少数派
        winner_results = sig_groups[winner_sig]
        for sig, sig_results in sig_groups.items():
            if sig == winner_sig:
                continue
            for result in sig_results:
                # 高置信度保护
                if result.get("confidence", 0) >= skip_conf:
                    continue
                if _apply_correction(result, winner_results):
                    corrections_made += 1

    # 日志汇总
    if groups_checked > 0:
        logger.info(
            f"L3反思: 检查{groups_checked}组同类清单, "
            f"发现{inconsistencies_found}组不一致, "
            f"纠正{corrections_made}条, "
            f"冲突标记{conflicts_flagged}组"
        )

    return results
