"""运行时缓存：复用省份级重对象，降低每个任务的固定初始化成本。"""

from __future__ import annotations

import threading
import time
from typing import Any

from loguru import logger

import config
from src.experience_db import ExperienceDB
from src.hybrid_searcher import HybridSearcher
from src.method_cards import MethodCards
from src.reranker import Reranker
from src.rule_validator import RuleValidator
from src.unified_data_layer import UnifiedDataLayer

_rule_bundle_cache: dict[str, tuple[RuleValidator, Reranker]] = {}
_experience_db_cache: dict[str, ExperienceDB] = {}
_search_bundle_cache: dict[tuple[str, tuple[str, ...]], HybridSearcher] = {}
_method_cards_cache: MethodCards | None = None
_unified_data_layer_cache: dict[str, UnifiedDataLayer] = {}

_rule_bundle_lock = threading.Lock()
_experience_db_lock = threading.Lock()
_search_bundle_lock = threading.Lock()
_method_cards_lock = threading.Lock()
_unified_data_layer_lock = threading.Lock()
_prewarm_thread: threading.Thread | None = None
_prewarm_state_lock = threading.Lock()
_prewarm_state: dict[str, Any] = {
    "status": "idle",
    "started_at": 0.0,
    "finished_at": 0.0,
    "elapsed_ms": 0.0,
    "provinces": [],
    "error": "",
}


def _province_key(province: str | None) -> str:
    return str(province or "").strip()


def _aux_key(aux_provinces: list[str] | None) -> tuple[str, ...]:
    return tuple(str(item or "").strip() for item in (aux_provinces or []))


def get_rule_bundle(province: str | None = None) -> tuple[RuleValidator, Reranker]:
    key = _province_key(province)
    cached = _rule_bundle_cache.get(key)
    if cached is not None:
        logger.debug(f"runtime_cache hit: rule_bundle province={key or '<default>'}")
        return cached
    with _rule_bundle_lock:
        cached = _rule_bundle_cache.get(key)
        if cached is not None:
            logger.debug(f"runtime_cache hit(after-lock): rule_bundle province={key or '<default>'}")
            return cached
        logger.info(f"runtime_cache miss: rule_bundle province={key or '<default>'}，开始创建")
        started_at = time.perf_counter()
        cached = (RuleValidator(province=province), Reranker())
        _rule_bundle_cache[key] = cached
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(f"runtime_cache created: rule_bundle province={key or '<default>'} elapsed_ms={elapsed_ms:.1f}")
        return cached


def get_experience_db(province: str | None = None) -> ExperienceDB:
    key = _province_key(province)
    cached = _experience_db_cache.get(key)
    if cached is not None:
        logger.debug(f"runtime_cache hit: experience_db province={key or '<default>'}")
        return cached
    with _experience_db_lock:
        cached = _experience_db_cache.get(key)
        if cached is not None:
            logger.debug(f"runtime_cache hit(after-lock): experience_db province={key or '<default>'}")
            return cached
        logger.info(f"runtime_cache miss: experience_db province={key or '<default>'}，开始创建")
        started_at = time.perf_counter()
        cached = ExperienceDB(province=province)
        _experience_db_cache[key] = cached
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(f"runtime_cache created: experience_db province={key or '<default>'} elapsed_ms={elapsed_ms:.1f}")
        return cached


def get_search_bundle(province: str | None = None,
                      aux_provinces: list[str] | None = None) -> HybridSearcher:
    province_key = _province_key(province)
    aux_key = _aux_key(aux_provinces)
    key = (province_key, aux_key)
    cached = _search_bundle_cache.get(key)
    if cached is not None:
        logger.debug(
            f"runtime_cache hit: search_bundle province={province_key or '<default>'} aux={list(aux_key)}"
        )
        return cached
    with _search_bundle_lock:
        cached = _search_bundle_cache.get(key)
        if cached is not None:
            logger.debug(
                f"runtime_cache hit(after-lock): search_bundle province={province_key or '<default>'} aux={list(aux_key)}"
            )
            return cached
        logger.info(
            f"runtime_cache miss: search_bundle province={province_key or '<default>'} aux={list(aux_key)}，开始创建"
        )
        started_at = time.perf_counter()
        searcher = HybridSearcher(province)
        searcher.aux_searchers = []
        for aux_province in aux_provinces or []:
            searcher.aux_searchers.append(HybridSearcher(aux_province))
        cached = searcher
        _search_bundle_cache[key] = cached
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(
            f"runtime_cache created: search_bundle province={province_key or '<default>'} aux={list(aux_key)} elapsed_ms={elapsed_ms:.1f}"
        )
        return cached


def get_method_cards_db() -> MethodCards:
    global _method_cards_cache
    cached = _method_cards_cache
    if cached is not None:
        logger.debug("runtime_cache hit: method_cards")
        return cached
    with _method_cards_lock:
        cached = _method_cards_cache
        if cached is not None:
            logger.debug("runtime_cache hit(after-lock): method_cards")
            return cached
        logger.info("runtime_cache miss: method_cards，开始创建")
        started_at = time.perf_counter()
        cached = MethodCards()
        _method_cards_cache = cached
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(f"runtime_cache created: method_cards elapsed_ms={elapsed_ms:.1f}")
        return cached


def get_unified_data_layer(province: str | None = None,
                           experience_db: ExperienceDB | None = None) -> UnifiedDataLayer:
    key = _province_key(province)
    cached = _unified_data_layer_cache.get(key)
    if cached is not None:
        if experience_db is not None:
            cached.experience_db = experience_db
        logger.debug(f"runtime_cache hit: unified_data_layer province={key or '<default>'}")
        return cached
    with _unified_data_layer_lock:
        cached = _unified_data_layer_cache.get(key)
        if cached is not None:
            if experience_db is not None:
                cached.experience_db = experience_db
            logger.debug(f"runtime_cache hit(after-lock): unified_data_layer province={key or '<default>'}")
            return cached
        logger.info(f"runtime_cache miss: unified_data_layer province={key or '<default>'}，开始创建")
        started_at = time.perf_counter()
        cached = UnifiedDataLayer(province=province, experience_db=experience_db)
        _unified_data_layer_cache[key] = cached
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        logger.info(f"runtime_cache created: unified_data_layer province={key or '<default>'} elapsed_ms={elapsed_ms:.1f}")
        return cached


def clear_runtime_cache() -> None:
    global _method_cards_cache
    with _rule_bundle_lock:
        _rule_bundle_cache.clear()
    with _experience_db_lock:
        _experience_db_cache.clear()
    with _search_bundle_lock:
        _search_bundle_cache.clear()
    with _method_cards_lock:
        _method_cards_cache = None
    with _unified_data_layer_lock:
        _unified_data_layer_cache.clear()
    logger.info("runtime_cache cleared")


def cache_stats() -> dict[str, Any]:
    return {
        "rule_bundles": len(_rule_bundle_cache),
        "experience_dbs": len(_experience_db_cache),
        "search_bundles": len(_search_bundle_cache),
        "method_cards_cached": _method_cards_cache is not None,
        "unified_data_layers": len(_unified_data_layer_cache),
        "rule_bundle_keys": sorted(_rule_bundle_cache.keys()),
        "experience_db_keys": sorted(_experience_db_cache.keys()),
        "search_bundle_keys": [
            {"province": province, "aux_provinces": list(aux)}
            for province, aux in sorted(_search_bundle_cache.keys())
        ],
        "unified_data_layer_keys": sorted(_unified_data_layer_cache.keys()),
    }


def prewarm_status() -> dict[str, Any]:
    with _prewarm_state_lock:
        return dict(_prewarm_state)


def _set_prewarm_state(**updates) -> None:
    with _prewarm_state_lock:
        _prewarm_state.update(updates)


def _resolve_prewarm_provinces(provinces: list[str] | None = None) -> list[str]:
    items = [
        str(item or "").strip()
        for item in (
            provinces
            or getattr(config, "RUNTIME_PREWARM_PROVINCES", None)
            or [config.get_current_province()]
        )
        if str(item or "").strip()
    ]
    if not items:
        items = [config.get_current_province()]
    return list(dict.fromkeys(items))


def prewarm_runtime_cache(
    provinces: list[str] | None = None,
    *,
    include_universal_kb: bool | None = None,
    include_method_cards: bool | None = None,
    load_vector_index: bool | None = None,
) -> dict[str, Any]:
    include_universal_kb = (
        getattr(config, "RUNTIME_PREWARM_INCLUDE_UNIVERSAL_KB", True)
        if include_universal_kb is None else bool(include_universal_kb)
    )
    include_method_cards = (
        getattr(config, "RUNTIME_PREWARM_INCLUDE_METHOD_CARDS", False)
        if include_method_cards is None else bool(include_method_cards)
    )
    load_vector_index = (
        getattr(config, "RUNTIME_PREWARM_VECTOR_INDEX", True)
        if load_vector_index is None else bool(load_vector_index)
    )
    province_list = _resolve_prewarm_provinces(provinces)
    started_at = time.perf_counter()
    _set_prewarm_state(
        status="running",
        started_at=time.time(),
        finished_at=0.0,
        elapsed_ms=0.0,
        provinces=list(province_list),
        error="",
    )
    logger.info(
        "runtime_prewarm started: "
        f"provinces={province_list} "
        f"include_universal_kb={include_universal_kb} "
        f"include_method_cards={include_method_cards} "
        f"load_vector_index={load_vector_index}"
    )
    try:
        if include_method_cards:
            get_method_cards_db()

        for province in province_list:
            experience_db = get_experience_db(province=province)
            experience_db.get_total_count_fast(province=province)
            get_rule_bundle(province=province)
            searcher = get_search_bundle(province=province)
            _ = searcher.bm25_engine
            if getattr(config, "VECTOR_ENABLED", True) and load_vector_index:
                try:
                    _ = searcher.vector_engine.collection.count()
                except Exception as exc:
                    logger.warning(
                        f"runtime_prewarm vector warmup failed: province={province} error={exc}"
                    )
            if include_universal_kb:
                try:
                    kb = searcher.universal_kb
                    if kb:
                        kb.has_authority_records()
                except Exception as exc:
                    logger.warning(
                        f"runtime_prewarm universal_kb warmup failed: province={province} error={exc}"
                    )

        elapsed_ms = (time.perf_counter() - started_at) * 1000
        _set_prewarm_state(
            status="completed",
            finished_at=time.time(),
            elapsed_ms=elapsed_ms,
            error="",
        )
        logger.info(
            f"runtime_prewarm completed: provinces={province_list} elapsed_ms={elapsed_ms:.1f}"
        )
        return prewarm_status()
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        _set_prewarm_state(
            status="failed",
            finished_at=time.time(),
            elapsed_ms=elapsed_ms,
            error=str(exc),
        )
        logger.exception(f"runtime_prewarm failed: provinces={province_list} error={exc}")
        raise


def start_background_prewarm(
    provinces: list[str] | None = None,
    *,
    include_universal_kb: bool | None = None,
    include_method_cards: bool | None = None,
    load_vector_index: bool | None = None,
) -> threading.Thread | None:
    global _prewarm_thread
    if not getattr(config, "RUNTIME_PREWARM_ENABLED", True):
        logger.info("runtime_prewarm disabled by config")
        return None

    existing = _prewarm_thread
    if existing is not None and existing.is_alive():
        logger.info("runtime_prewarm already running, skip duplicate start")
        return existing

    thread = threading.Thread(
        target=prewarm_runtime_cache,
        kwargs={
            "provinces": provinces,
            "include_universal_kb": include_universal_kb,
            "include_method_cards": include_method_cards,
            "load_vector_index": load_vector_index,
        },
        name="runtime-prewarm",
        daemon=True,
    )
    _prewarm_thread = thread
    thread.start()
    return thread
