# -*- coding: utf-8 -*-
"""
规则知识后置校验器 — 匹配完成后用代码校验结果，提取相关规则提示

设计思路：
  不是硬校验（不强制修改结果），而是"规则提醒"：
  根据匹配结果从 rule_knowledge.db 搜索相关规则，
  提取系数、包含/不包含、适用范围等关键信息，
  生成用户可见的提示文本，写入输出Excel备注列。

  用户看到提示后可以参考或忽略，不影响匹配结果。

复用架构：
  用 rule_knowledge.py 的 search_rules() 接口搜索相关规则，
  用正则/关键词从规则文本中提取关键信息。
"""

import re
from loguru import logger

import config


# 缓存规则知识库实例（按省份缓存，避免重复加载）
_rule_kb_cache: dict = {}  # {province_key: kb_instance_or_None}
_rule_kb_failed: dict = {}  # {province_key: 失败次数}，允许重试
_RULE_KB_MAX_RETRIES = 3  # 连续失败超过此次数后停止重试


def _get_rule_kb(province: str = None):
    """获取规则知识库实例（按省份懒加载，失败可重试）"""
    cache_key = province or "__default__"

    # 已缓存成功的实例，直接返回
    if cache_key in _rule_kb_cache:
        return _rule_kb_cache[cache_key]

    # 连续失败超限，不再重试（避免每次调用都尝试加载）
    if _rule_kb_failed.get(cache_key, 0) >= _RULE_KB_MAX_RETRIES:
        return None

    try:
        from src.rule_knowledge import RuleKnowledge
        kb = RuleKnowledge(province=province)
        if kb.get_stats()["total"] > 0:
            _rule_kb_cache[cache_key] = kb
            _rule_kb_failed.pop(cache_key, None)  # 成功后清除失败计数
            return kb
        else:
            # 库为空，视为不可用但不计入失败（可能数据还没导入）
            logger.debug(f"规则知识库为空（省份={province}），规则提示跳过")
            return None
    except Exception as e:
        _rule_kb_failed[cache_key] = _rule_kb_failed.get(cache_key, 0) + 1
        fail_count = _rule_kb_failed[cache_key]
        logger.debug(f"规则知识库加载失败（{fail_count}/{_RULE_KB_MAX_RETRIES}，省份={province}）: {e}")
        return None


# 规则文本中提取关键信息的正则模式
# 匹配 "乘以系数X.XX" 或 "系数为X.XX" 等
_RE_COEFFICIENT = re.compile(
    r'(?:乘以|乘|×)\s*系数\s*(\d+\.?\d*)|'
    r'系数\s*(?:为|是)?\s*(\d+\.?\d*)|'
    r'人工[费]?\s*(?:乘以?|×)\s*(\d+\.?\d*)'
)
# 匹配 "已包括XX" / "不包括XX"
_RE_SCOPE = re.compile(
    r'(已包括|不包括|不含|包含|包括|不适用于|仅适用于|适用于)'
    r'([^。，；\n]{3,40})'
)


def check_by_rules(item: dict, result: dict, province: str = None) -> list[str]:
    """对单条匹配结果做规则知识校验，返回提示文本列表

    参数:
        item: 清单项字典（含name, description, specialty等）
        result: 匹配结果字典（含quotas列表）
        province: 当前省份

    返回:
        提示文本列表，如 ["超高系数1.1适用", "管道安装已包括管卡"]
        空列表表示没有相关规则提示
    """
    kb = _get_rule_kb(province)
    if not kb:
        return []

    quotas = result.get("quotas", [])
    if not quotas:
        return []

    # 构建搜索查询：清单名称 + 第一条定额名称
    bill_name = item.get("name", "")
    main_quota_name = quotas[0].get("name", "")
    query = f"{bill_name} {main_quota_name}".strip()
    if not query:
        return []

    # 搜索相关规则（top 3）
    try:
        rules = kb.search_rules(query, top_k=3, province=province)
    except Exception as e:
        logger.debug(f"规则搜索失败（不影响主流程）: {e}")
        return []

    if not rules:
        return []

    # 从搜索到的规则中提取关键信息
    hints = []
    seen_hints = set()  # 去重

    for rule in rules:
        content = rule.get("content", "")
        if not content:
            continue

        # 提取系数信息
        for m in _RE_COEFFICIENT.finditer(content):
            coef = m.group(1) or m.group(2) or m.group(3)
            if coef:
                # 获取系数的上下文（前后各取20字）
                start = max(0, m.start() - 20)
                end = min(len(content), m.end() + 20)
                context = content[start:end].strip()
                # 清理换行和多余空格
                context = re.sub(r'\s+', '', context)
                hint = f"系数{coef}: {context}"
                if hint not in seen_hints:
                    seen_hints.add(hint)
                    hints.append(hint)

        # 提取包含/不包含信息
        for m in _RE_SCOPE.finditer(content):
            keyword = m.group(1)  # "已包括" / "不包括" 等
            scope_text = m.group(2).strip()
            # 检查是否与当前清单/定额相关
            if _is_relevant_scope(bill_name, main_quota_name, scope_text):
                hint = f"{keyword}{scope_text}"
                if hint not in seen_hints:
                    seen_hints.add(hint)
                    hints.append(hint)

    # 最多返回3条提示，避免信息过载
    return hints[:3]


def _is_relevant_scope(bill_name: str, quota_name: str, scope_text: str) -> bool:
    """判断包含/不包含信息是否与当前清单/定额相关

    用2字中文词组（bigram）做重叠检测：
    从scope_text中抽取所有相邻2字中文组合，
    看是否有任一组合出现在清单名或定额名中。
    """
    # 提取scope_text中的连续中文字符
    chinese_chars = re.findall(r'[\u4e00-\u9fff]+', scope_text)
    if not chinese_chars:
        return False

    combined = bill_name + quota_name
    # 从每段连续中文中提取2字bigram
    for chars in chinese_chars:
        for i in range(len(chars) - 1):
            bigram = chars[i:i+2]
            if bigram in combined:
                return True
    return False


def format_rule_hints(hints: list[str]) -> str:
    """将规则提示列表格式化为单行文本（写入Excel备注列用）"""
    if not hints:
        return ""
    return "｜".join(hints)
