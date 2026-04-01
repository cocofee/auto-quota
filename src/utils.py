# -*- coding: utf-8 -*-
"""通用工具函数 — 被多个模块复用的基础原语"""

from __future__ import annotations

import json
import re


def safe_float(value, default: float = 0.0) -> float:
    """安全转换为浮点数，失败时返回默认值。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_json_list(raw) -> list:
    """安全解析JSON数组，异常时返回空列表。

    支持：已经是list直接返回、JSON字符串解析、逗号/分号/换行分隔回退。
    """
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except Exception:
        text = str(raw or "").strip()
        if not text:
            return []
        return [part.strip() for part in re.split(r"[,，;\n]+", text) if part.strip()]


def dedupe_keep_order(values) -> list[str]:
    """去重但保持原始顺序。"""
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def cached_with_key_lock(cache: dict, key, compute_fn, cache_lock=None):
    """带per-key锁的缓存获取。

    - 不同key可并行计算（各自持独立锁）
    - 同key等待不重复计算（double-check locking）
    - cache_lock=None 时退化为简单dict读写
    """
    if cache_lock is not None:
        import threading
        with cache_lock:
            if key in cache:
                return cache[key]
            lock_key = ("_lock_", key)
            if lock_key not in cache:
                cache[lock_key] = threading.Lock()
            key_lock = cache[lock_key]
        with key_lock:
            with cache_lock:
                if key in cache:
                    return cache[key]
            value = compute_fn()
            with cache_lock:
                cache[key] = value
            return value
    if key not in cache:
        cache[key] = compute_fn()
    return cache[key]
