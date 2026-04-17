"""
混合搜索引擎
功能：
1. 同时调用BM25关键词搜索和BGE向量语义搜索
2. 用RRF（Reciprocal Rank Fusion，倒数排名融合）算法合并结果
3. 返回融合排序后的Top K候选定额

为什么要混合搜索？
- BM25擅长：精确关键词匹配（"DN150"、"YJV-4*185"等型号规格）
- 向量搜索擅长：语义相似匹配（"水泵"和"离心泵"是相关的）
- 两者互补，混合后效果最好

RRF算法原理：
- 不直接比较BM25分数和向量分数（它们量纲不同，不可直接相加）
- 而是根据各自的排名来融合：排名越靠前，贡献越大
- 公式：RRF_score = Σ (weight / (k + rank))
- k=60是标准常数，防止排名第1的权重过大
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from pathlib import Path

from loguru import logger

import config
from src.bm25_engine import BM25Engine
from src.candidate_canonicalizer import attach_candidate_canonical_features
from src.province_book_mapper import normalize_requested_books_for_search
from src.quota_search import search_by_id
from src.query_router import build_query_route_profile, count_spec_signals
from src.specialty_classifier import get_book_from_quota_id
from src.text_parser import parser as text_parser
from src.utils import safe_json_list


class HybridSearcher:
    _kb_keyword_executor = ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="universal-kb-keyword",
    )
    """混合搜索引擎：BM25 + 向量搜索，RRF融合"""
    _FAMILY_GATE_HARD_CONFLICTS = {
        frozenset(("bridge_support", "bridge_raceway")),
        frozenset(("bridge_support", "pipe_support")),
        frozenset(("pipe_support", "bridge_raceway")),
        frozenset(("valve_body", "valve_accessory")),
        frozenset(("air_terminal", "air_valve")),
        frozenset(("air_terminal", "air_device")),
        frozenset(("air_valve", "air_device")),
        frozenset(("electrical_box", "conduit_raceway")),
        frozenset(("electrical_box", "cable_family")),
        frozenset(("electrical_box", "protection_device")),
        frozenset(("cable_head_accessory", "cable_family")),
    }
    _FAMILY_GATE_STRICT_ENTITY_FAMILIES = {
        "bridge_raceway",
        "bridge_support",
        "pipe_support",
        "valve_accessory",
        "air_device",
        "sanitary_fixture",
        "electrical_box",
        "protection_device",
        "conduit_raceway",
    }
    _FAMILY_WINDOW_FAMILIES = {
        "valve_accessory",
        "bridge_support",
        "sanitary_fixture",
        "air_terminal",
        "air_valve",
        "air_device",
        "electrical_box",
        "protection_device",
        "conduit_raceway",
    }
    _KB_HINT_OBJECT_FAMILIES = {
        "bridge_raceway",
        "bridge_support",
        "pipe_run",
        "pipe_support",
        "pipe_sleeve",
        "valve_body",
        "valve_accessory",
        "air_terminal",
        "air_valve",
        "air_device",
        "sanitary_fixture",
        "electrical_box",
        "protection_device",
        "conduit_raceway",
        "cable_family",
        "cable_head_accessory",
    }
    _CONDUIT_HINT_KEEP_WORDS = ("电线管", "配管", "导管", "金属软管", "可挠金属套管")
    _CONDUIT_HINT_BLOCK_WORDS = ("钢管敷设", "镀锌钢管", "焊接钢管")
    _KB_HINT_FEE_BLOCK_WORDS = ("增加费", "附加费", "脚手架", "系数", "降效")
    _KB_HINT_FEE_CONTEXT_WORDS = ("建筑层数", "层数", "檐高", "高度", "高层建筑")
    _QUOTA_DN_BUCKETS = (
        15, 20, 25, 32, 40, 50, 65, 80, 100, 125, 150, 200, 250, 300, 350,
        400, 500, 600, 700, 800, 900, 1000,
    )
    _QUOTA_CAPACITY_BUCKETS = (
        1, 2, 3, 5, 10, 15, 20, 30, 50, 75, 100, 150, 200, 300, 500, 800, 1000,
    )
    _INVERTER_POWER_BUCKETS = (250, 1000)
    _PLASTIC_PIPE_MARKERS = ("UPVC", "PVC", "PPR", "PE", "HDPE", "塑料")

    def __init__(self, province: str = None, experience_db=None, unified_data_layer=None):
        """
        参数:
            province: 省份名称，默认用config配置
            experience_db: 经验库实例（可选，提供反馈偏置数据）
        """
        self.province = province or config.get_current_province()
        self._experience_db = experience_db
        self._unified_data_layer = unified_data_layer

        # 两个搜索引擎（延迟初始化）
        self._bm25_engine = None
        self._vector_engine = None

        # 通用知识库（延迟初始化）
        self._universal_kb = None
        self._kb_keyword_cache = {}
        self._KB_KEYWORD_CACHE_MAX = 256
        self._kb_keyword_blocked_until = 0.0

        # 会话级搜索缓存：相同 normalized_query + books 的搜索结果复用
        # 同一批次中"DN25镀锌钢管"和"DN32镀锌钢管"可能生成相同的 normalized query
        # 缓存避免重复执行向量搜索（最耗时的环节）
        self._session_cache = {}  # {cache_key: candidates_list}
        self._SESSION_CACHE_MAX = 1000  # 缓存上限，防止长时间运行内存泄漏

        # 反馈偏置缓存（用用户修正/确认数据动态校准检索权重）
        self._feedback_bias_value = 0.0
        self._feedback_bias_ts = 0.0

        # 编号体系检测缓存（行业定额用纯数字book，不兼容C1-C12搜索）
        self._uses_standard_books = None

    def set_experience_db(self, experience_db) -> None:
        """设置经验库实例（延迟注入，避免循环依赖）。"""
        self._experience_db = experience_db
        unified_data_layer = getattr(self, "_unified_data_layer", None)
        if unified_data_layer not in (None, False):
            try:
                unified_data_layer.experience_db = experience_db
            except Exception:
                pass
        for aux_searcher in list(getattr(self, "aux_searchers", []) or []):
            setter = getattr(aux_searcher, "set_experience_db", None)
            if callable(setter):
                setter(experience_db)

    def reset_runtime_state(self, *, include_aux: bool = True) -> None:
        """清理跨请求不应复用的易失状态，保留省份级索引本体。"""
        session_cache = getattr(self, "_session_cache", None)
        if isinstance(session_cache, dict):
            session_cache.clear()
        else:
            self._session_cache = {}

        if not include_aux:
            return
        for aux_searcher in list(getattr(self, "aux_searchers", []) or []):
            resetter = getattr(aux_searcher, "reset_runtime_state", None)
            if callable(resetter):
                resetter()

    @staticmethod
    def _normalize_session_cache_key_part(value):
        if isinstance(value, dict):
            return {
                str(key): HybridSearcher._normalize_session_cache_key_part(value[key])
                for key in sorted(value, key=lambda item: str(item))
            }
        if isinstance(value, (list, tuple)):
            return [HybridSearcher._normalize_session_cache_key_part(item) for item in value]
        if isinstance(value, set):
            normalized = [
                HybridSearcher._normalize_session_cache_key_part(item)
                for item in value
            ]
            return sorted(
                normalized,
                key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
        if isinstance(value, float):
            return round(value, 8)
        if value is None or isinstance(value, (str, int, bool)):
            return value
        return str(value)

    def _build_session_cache_key(self, *,
                                 query: str,
                                 books: list[str] | None,
                                 top_k: int,
                                 adaptive_strategy: str,
                                 primary_query_profile: dict | None,
                                 query_features: dict | None,
                                 route_profile: dict | None,
                                 rank_window: int,
                                 bm25_weight: float,
                                 vector_weight: float,
                                 weight_reason: str,
                                 bm25_top_k: int,
                                 vector_top_k: int) -> str:
        payload = {
            "query": str(query or ""),
            "books": sorted(str(book).strip() for book in (books or []) if str(book).strip()),
            "top_k": int(top_k),
            "adaptive_strategy": str(adaptive_strategy or "standard").strip().lower(),
            "primary_query_profile": dict(primary_query_profile or {}),
            "query_features": dict(query_features or {}),
            "route_profile": dict(route_profile or {}),
            "rank_window": int(rank_window),
            "bm25_weight": float(bm25_weight),
            "vector_weight": float(vector_weight),
            "weight_reason": str(weight_reason or ""),
            "bm25_top_k": int(bm25_top_k),
            "vector_top_k": int(vector_top_k),
            "vector_enabled": bool(config.VECTOR_ENABLED),
        }
        normalized = self._normalize_session_cache_key_part(payload)
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _stable_result_identity(candidate: dict) -> tuple[str, str, str]:
        return (
            str(candidate.get("quota_id", "") or "").strip(),
            str(candidate.get("name", "") or "").strip(),
            str(candidate.get("id", "") or "").strip(),
        )

    @classmethod
    def _hybrid_result_sort_key(cls, candidate: dict) -> tuple:
        def _rank_value(value) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 10**9

        bm25_rank = _rank_value(candidate.get("bm25_rank"))
        vector_rank = _rank_value(candidate.get("vector_rank"))
        best_rank = min(bm25_rank, vector_rank)
        quota_id, name, db_id = cls._stable_result_identity(candidate)
        return (
            -float(candidate.get("hybrid_score", 0.0) or 0.0),
            best_rank,
            vector_rank,
            bm25_rank,
            quota_id,
            name,
            db_id,
        )

    @staticmethod
    def _normalize_requested_books_for_nonstandard_db(
        books: list[str] | None,
        available_books: set[str] | None,
        province: str | None = None,
    ) -> list[str] | None:
        return normalize_requested_books_for_search(
            books,
            province=province,
            available_books=available_books,
        )

    def collect_prior_candidates(
        self,
        query_text: str,
        *,
        full_query: str = "",
        books: list[str] | None = None,
        item: dict | None = None,
        top_k: int = 8,
        exact_only: bool = False,
    ) -> list[dict]:
        if not bool(getattr(config, "SEARCH_PRIOR_CANDIDATES_ENABLED", True)):
            return []

        priors: list[dict] = []
        priors.extend(
            self._collect_quota_alias_exact_prior_candidates(
                query_text=query_text,
                full_query=full_query,
                item=item,
                books=books,
                top_k=max(1, min(top_k, 4)),
            )
        )
        if bool(getattr(config, "SEARCH_EXPERIENCE_INJECTION_ENABLED", True)):
            priors.extend(
                self._collect_experience_exact_prior_candidates(
                    query_text=query_text,
                    full_query=full_query,
                    item=item,
                    top_k=max(1, min(top_k, 4)),
                )
            )
        if bool(getattr(config, "SEARCH_UNIVERSAL_KB_INJECTION_ENABLED", True)):
            priors.extend(
                self._collect_universal_kb_exact_prior_candidates(
                    query_text=query_text,
                    full_query=full_query,
                    item=item,
                    books=books,
                    top_k=max(1, min(top_k, 4)),
                )
            )
        if not exact_only:
            unified_priors = None
            if bool(getattr(config, "SEARCH_UNIFIED_DATA_PRIOR_ENABLED", True)):
                unified_priors = self._collect_unified_data_prior_candidates(
                    query_text=query_text,
                    full_query=full_query,
                    item=item,
                    books=books,
                    top_k=max(1, min(top_k, 4)),
                )
            if unified_priors is not None:
                priors.extend(unified_priors)
            else:
                if bool(getattr(config, "SEARCH_EXPERIENCE_INJECTION_ENABLED", True)):
                    priors.extend(
                        self._collect_experience_prior_candidates(
                            query_text=query_text,
                            full_query=full_query,
                            item=item,
                            top_k=max(1, min(top_k, 4)),
                        )
                    )
                if bool(getattr(config, "SEARCH_UNIVERSAL_KB_INJECTION_ENABLED", True)):
                    priors.extend(
                        self._collect_universal_kb_prior_candidates(
                            query_text=query_text,
                            full_query=full_query,
                            item=item,
                            books=books,
                            top_k=max(1, min(top_k, 4)),
                        )
                    )

        deduped: dict[str, dict] = {}
        for candidate in priors:
            quota_id = str(candidate.get("quota_id", "") or "").strip()
            if not quota_id:
                continue
            existing = deduped.get(quota_id)
            if existing is None:
                deduped[quota_id] = candidate
                continue
            existing_sources = set(existing.get("knowledge_prior_sources") or [])
            merged_sources = list(existing_sources | set(candidate.get("knowledge_prior_sources") or []))
            if float(candidate.get("knowledge_prior_score", 0.0) or 0.0) > float(existing.get("knowledge_prior_score", 0.0) or 0.0):
                merged = dict(candidate)
                merged["knowledge_prior_sources"] = merged_sources
                deduped[quota_id] = merged
            else:
                existing["knowledge_prior_sources"] = merged_sources

        results = list(deduped.values())
        results.sort(key=self._stable_result_identity)
        results.sort(
            key=lambda candidate: (
                float(candidate.get("knowledge_prior_score", 0.0) or 0.0),
                float(candidate.get("hybrid_score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        return results[:top_k]

    @staticmethod
    def _normalize_alias_text(text: str) -> str:
        return re.sub(r"\s+", "", str(text or "").strip()).lower()

    def _collect_quota_alias_exact_prior_candidates(
        self,
        *,
        query_text: str,
        full_query: str = "",
        item: dict | None = None,
        books: list[str] | None = None,
        top_k: int = 3,
    ) -> list[dict]:
        item = dict(item or {})
        variants = self._build_prior_query_variants(
            query_text,
            full_query=full_query,
            item=item,
        )
        primary_query_profile = dict(
            (item.get("canonical_query") or {}).get("primary_query_profile")
            or item.get("primary_query_profile")
            or {}
        )
        alias_pool = [
            str(value).strip()
            for value in list(primary_query_profile.get("quota_aliases") or [])
            if str(value).strip()
        ]
        alias_pool.extend(variants[:2])
        if not alias_pool:
            return []

        candidates: list[dict] = []
        seen_quota_ids: set[str] = set()
        seen_aliases: set[str] = set()
        for alias in alias_pool:
            normalized_alias = self._normalize_alias_text(alias)
            if not normalized_alias or normalized_alias in seen_aliases:
                continue
            seen_aliases.add(normalized_alias)
            try:
                matched = self.bm25_engine.search(alias, top_k=max(6, top_k * 3), books=books)
            except Exception as e:
                logger.debug(f"quota alias exact prior search failed: {alias[:40]} {e}")
                continue
            for row in matched or []:
                quota_id = str(row.get("quota_id", "") or "").strip()
                quota_name = str(row.get("name", "") or "").strip()
                if not quota_id or not quota_name or quota_id in seen_quota_ids:
                    continue
                if self._normalize_alias_text(quota_name) != normalized_alias:
                    continue
                candidate = dict(row)
                candidate["match_source"] = "quota_alias_exact"
                candidate["quota_alias_exact"] = alias
                candidate["knowledge_prior_sources"] = ["quota_alias"]
                candidate["knowledge_prior_score"] = 0.98
                candidates.append(candidate)
                seen_quota_ids.add(quota_id)
                if len(candidates) >= top_k:
                    return candidates
        return candidates

    @staticmethod
    def _build_prior_query_variants(
        query_text: str,
        *,
        full_query: str = "",
        item: dict | None = None,
    ) -> list[str]:
        item = dict(item or {})
        canonical_query = dict(item.get("canonical_query") or {})
        primary_query_profile = dict(
            canonical_query.get("primary_query_profile")
            or item.get("primary_query_profile")
            or {}
        )
        name = str(item.get("name", "") or "").strip()
        desc = str(item.get("description", "") or "").strip()
        full_text = " ".join(part for part in (name, desc) if part).strip()
        query_features = dict(item.get("canonical_features") or item.get("query_features") or {})
        if not query_features:
            query_features = text_parser.parse_canonical(
                query_text or full_query or full_text
            )

        variants: list[str] = []
        for value in (
            canonical_query.get("normalized_query", ""),
            full_text,
            canonical_query.get("validation_query", ""),
            canonical_query.get("search_query", ""),
            primary_query_profile.get("primary_text", ""),
            primary_query_profile.get("primary_subject", ""),
            *list(primary_query_profile.get("quota_aliases") or [])[:2],
            " ".join(
                str(value).strip()
                for value in (
                    [primary_query_profile.get("primary_subject", "")]
                    + list(primary_query_profile.get("decisive_terms") or [])[:2]
                )
                if str(value).strip()
            ),
            full_query,
            query_text,
            name,
            desc,
        ):
            text = str(value or "").strip()
            if text and text not in variants:
                variants.append(text)
        for value in HybridSearcher._build_quota_style_query_variants(
            query_features=query_features,
            primary_query_profile=primary_query_profile,
        ):
            if value not in variants:
                variants.append(value)
        return variants

    @staticmethod
    def _looks_like_numeric_alias(text: str) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        return bool(
            re.search(r"\d", text)
            or re.search(r"(?:DN|DE|KW|KVA|MM)\b", text, flags=re.IGNORECASE)
        )

    @classmethod
    def _select_retrieval_aliases(
        cls,
        aliases: list[str] | None,
        *,
        max_count: int = 2,
    ) -> list[str]:
        pool = [
            str(value).strip()
            for value in (aliases or [])
            if str(value).strip()
        ]
        if len(pool) <= max_count:
            return pool

        selected: list[str] = []

        def _push(value: str) -> None:
            if value and value not in selected:
                selected.append(value)

        _push(pool[0])
        first_is_numeric = cls._looks_like_numeric_alias(pool[0])
        contrast = next(
            (
                alias
                for alias in pool[1:]
                if cls._looks_like_numeric_alias(alias) != first_is_numeric
            ),
            "",
        )
        if contrast:
            _push(contrast)
        for alias in pool[1:]:
            if len(selected) >= max_count:
                break
            _push(alias)
        return selected[:max_count]

    @staticmethod
    def _coerce_prior_list(value) -> list[str]:
        return [
            str(item).strip()
            for item in safe_json_list(value)
            if str(item).strip()
        ]

    @property
    def uses_standard_books(self) -> bool:
        """检测当前定额库是否使用标准C1-C12编号体系

        行业定额（石油、电力等）用纯数字book("1","2"...),
        不兼容C1-C12分类搜索，需要跳过book过滤搜全库。
        """
        if self._uses_standard_books is None:
            # 确保BM25索引已加载（quota_books在ensure_index后才有值）
            self.bm25_engine.ensure_index()
            books = self.bm25_engine.quota_books
            if not books:
                self._uses_standard_books = True  # 无数据时默认标准
            else:
                # 有任何一个C开头的book → 标准体系
                has_c = any(v.startswith("C") for v in books.values() if v)
                self._uses_standard_books = has_c
        return self._uses_standard_books

    @property
    def bm25_engine(self):
        """延迟加载BM25引擎"""
        if self._bm25_engine is None:
            self._bm25_engine = BM25Engine(self.province)
        return self._bm25_engine

    @property
    def vector_engine(self):
        """延迟加载向量引擎（VECTOR_ENABLED=false时不加载，避免导入torch等大包）"""
        if self._vector_engine is None:
            from src.vector_engine import VectorEngine
            self._vector_engine = VectorEngine(self.province)
        return self._vector_engine

    @property
    def universal_kb(self):
        """延迟加载通用知识库"""
        if self._universal_kb is None:
            try:
                from src.universal_kb import UniversalKB
                self._universal_kb = UniversalKB()
                # 检查是否有数据（没数据就不用查了）
                if not self._universal_kb.has_authority_records():
                    logger.debug("通用知识库权威层为空，跳过知识库增强")
                    self._universal_kb = False  # 标记为不可用，避免反复初始化
            except Exception as e:
                logger.debug(f"通用知识库加载失败（不影响基础搜索）: {e}")
                self._universal_kb = False
        return self._universal_kb if self._universal_kb is not False else None

    @property
    def unified_data_layer(self):
        """寤惰繜鍔犺浇缁熶竴鏁版嵁灞?"""
        current = getattr(self, "_unified_data_layer", None)
        if current is None:
            try:
                from src.unified_data_layer import UnifiedDataLayer

                self._unified_data_layer = UnifiedDataLayer(
                    province=self.province,
                    experience_db=self._experience_db,
                )
            except Exception as e:
                logger.debug(f"缁熶竴鏁版嵁灞傚姞杞藉け璐ワ紙鍥為€€鍒版棫妫€绱㈤€昏緫锛? {e}")
                self._unified_data_layer = False
        return self._unified_data_layer if self._unified_data_layer is not False else None

    def _materialize_quota_candidate(self, quota_id: str, fallback_name: str = "",
                                     fallback_unit: str = "") -> dict | None:
        quota_id = str(quota_id or "").strip()
        if not quota_id:
            return None
        row = search_by_id(quota_id, province=self.province)
        if row:
            quota_id, quota_name, unit = row
        else:
            quota_name = str(fallback_name or "").strip()
            unit = str(fallback_unit or "").strip()
            if not quota_name:
                return None
        return {
            "quota_id": str(quota_id or "").strip(),
            "name": str(quota_name or "").strip(),
            "unit": str(unit or "").strip(),
            "id": None,
            "db_id": None,
            "candidate_canonical_features": text_parser.parse_canonical(
                str(quota_name or "").strip(),
                specialty=get_book_from_quota_id(quota_id) or "",
            ),
        }

    def _collect_unified_data_prior_candidates(
        self,
        *,
        query_text: str,
        full_query: str = "",
        item: dict | None = None,
        books: list[str] | None = None,
        top_k: int = 3,
    ) -> list[dict] | None:
        unified_data_layer = self.unified_data_layer
        if not unified_data_layer:
            return None

        item = dict(item or {})
        query = str(full_query or query_text or "").strip()
        if not query:
            return []

        payload = {
            "text": query,
            "province": self.province,
            "specialty": str(item.get("specialty", "") or "").strip(),
            "unit": str(item.get("unit", "") or "").strip(),
            "materials_signature": str(item.get("materials_signature", "") or "").strip(),
            "install_method": str(item.get("install_method", "") or "").strip(),
        }
        if books and len(books) == 1:
            payload["book"] = str(books[0] or "").strip()

        try:
            search_result = unified_data_layer.search(
                payload,
                sources=["experience", "universal_kb", "quota"],
                strategy="auto",
                top_k=max(top_k * 2, top_k),
                authority_only=True,
            )
        except Exception as e:
            logger.debug(f"缁熶竴鏁版嵁鍏堥獙鍊欓€夋绱㈠け璐ワ紙鍥為€€鍒版棫妫€绱㈤€昏緫锛? {e}")
            return None

        grouped = dict(search_result.get("grouped") or {})
        candidates: list[dict] = []

        for entry in grouped.get("experience", []) or []:
            raw = dict(entry.get("raw") or {})
            if str(raw.get("gate", "") or "").strip() == "red":
                continue
            if str(raw.get("match_type", "") or "").strip() in {"stale", "candidate"}:
                continue
            quota_ids = self._coerce_prior_list(raw.get("quota_ids"))
            quota_names = self._coerce_prior_list(raw.get("quota_names"))
            if not quota_ids:
                continue
            quota_id = str(quota_ids[0] or "").strip()
            fallback_name = quota_names[0] if quota_names else ""
            candidate = self._materialize_quota_candidate(quota_id, fallback_name=fallback_name)
            if not candidate:
                continue
            prior_score = max(
                float(raw.get("total_score", 0.0) or 0.0),
                float(raw.get("similarity", 0.0) or 0.0) * 0.9,
                float(raw.get("confidence", 0.0) or 0.0) / 100.0 * 0.8,
            )
            candidate.update({
                "match_source": "experience_injected",
                "is_experience_candidate": 1,
                "experience_record_id": raw.get("id"),
                "experience_layer": raw.get("layer", ""),
                "experience_gate": raw.get("gate", ""),
                "experience_similarity": float(raw.get("similarity", 0.0) or 0.0),
                "experience_total_score": float(raw.get("total_score", 0.0) or 0.0),
                "experience_confidence": float(raw.get("confidence", 0.0) or 0.0),
                "knowledge_prior_sources": ["experience"],
                "knowledge_prior_score": prior_score,
            })
            candidates.append(candidate)
            if len(candidates) >= top_k:
                return candidates[:top_k]

        for entry in grouped.get("universal_kb", []) or []:
            raw = dict(entry.get("raw") or {})
            similarity = float(raw.get("similarity", entry.get("similarity", 0.0)) or 0.0)
            confidence = float(raw.get("confidence", entry.get("confidence", 0.0)) or 0.0)
            if similarity < 0.75 or confidence < 70:
                continue
            patterns = [str(p).strip() for p in (raw.get("quota_patterns") or []) if str(p).strip()]
            for pattern in patterns[:2]:
                try:
                    matched = self.bm25_engine.search(pattern, top_k=2, books=books)
                except Exception as e:
                    logger.debug(f"缁熶竴鏁版嵁KB妯″紡妫€绱㈠け璐ワ紙涓嶅奖鍝嶄富娴佺▼锛? {pattern[:40]} {e}")
                    continue
                for row in matched or []:
                    quota_id = str(row.get("quota_id", "") or "").strip()
                    quota_name = str(row.get("name", "") or "").strip()
                    if not quota_id or not quota_name:
                        continue
                    candidate = dict(row)
                    candidate["match_source"] = "kb_injected"
                    candidate["is_kb_candidate"] = 1
                    candidate["kb_bill_pattern"] = raw.get("bill_pattern", "")
                    candidate["kb_quota_pattern"] = pattern
                    candidate["kb_similarity"] = similarity
                    candidate["kb_confidence"] = confidence
                    candidate["knowledge_prior_sources"] = ["universal_kb"]
                    candidate["knowledge_prior_score"] = max(
                        similarity * 0.9,
                        confidence / 100.0 * 0.75,
                    )
                    candidates.append(candidate)
                    if len(candidates) >= top_k:
                        return candidates[:top_k]

        for entry in grouped.get("quota", []) or []:
            raw = dict(entry.get("raw") or {})
            quota_id = str(raw.get("quota_id", "") or "").strip()
            quota_name = str(raw.get("name", "") or "").strip()
            if not quota_id or not quota_name:
                continue
            candidate = dict(raw)
            candidate.setdefault("unit", str(raw.get("unit", "") or "").strip())
            candidate["match_source"] = "quota_unified"
            candidate["knowledge_prior_sources"] = ["quota"]
            candidate["knowledge_prior_score"] = float(entry.get("score", 0.0) or 0.0)
            candidates.append(candidate)
            if len(candidates) >= top_k:
                break

        return candidates[:top_k]

    def _collect_experience_prior_candidates(
        self,
        *,
        query_text: str,
        full_query: str = "",
        item: dict | None = None,
        top_k: int = 3,
    ) -> list[dict]:
        if not self._experience_db:
            return []

        item = dict(item or {})
        query = str(full_query or query_text or "").strip()
        if not query:
            return []

        try:
            records = self._experience_db.search_experience(
                query,
                top_k=max(top_k, 3),
                min_confidence=70,
                province=self.province,
                specialty=str(item.get("specialty", "") or "").strip(),
                unit=str(item.get("unit", "") or "").strip(),
                materials_signature=str(item.get("materials_signature", "") or "").strip(),
                install_method=str(item.get("install_method", "") or "").strip(),
            )
        except Exception as e:
            logger.debug(f"经验先验候选检索失败（不影响主流程）: {e}")
            return []

        candidates: list[dict] = []
        for record in records or []:
            if str(record.get("gate", "") or "").strip() == "red":
                continue
            if str(record.get("match_type", "") or "").strip() in {"stale", "candidate"}:
                continue
            quota_ids = self._coerce_prior_list(record.get("quota_ids"))
            quota_names = self._coerce_prior_list(record.get("quota_names"))
            if not quota_ids:
                continue
            quota_id = str(quota_ids[0] or "").strip()
            fallback_name = quota_names[0] if quota_names else ""
            candidate = self._materialize_quota_candidate(quota_id, fallback_name=fallback_name)
            if not candidate:
                continue
            prior_score = max(
                float(record.get("total_score", 0.0) or 0.0),
                float(record.get("similarity", 0.0) or 0.0) * 0.9,
                float(record.get("confidence", 0.0) or 0.0) / 100.0 * 0.8,
            )
            candidate.update({
                "match_source": "experience_injected",
                "is_experience_candidate": 1,
                "experience_record_id": record.get("id"),
                "experience_layer": record.get("layer", ""),
                "experience_gate": record.get("gate", ""),
                "experience_similarity": float(record.get("similarity", 0.0) or 0.0),
                "experience_total_score": float(record.get("total_score", 0.0) or 0.0),
                "experience_confidence": float(record.get("confidence", 0.0) or 0.0),
                "knowledge_prior_sources": ["experience"],
                "knowledge_prior_score": prior_score,
            })
            candidates.append(candidate)
            if len(candidates) >= top_k:
                break
        return candidates

    def _collect_experience_exact_prior_candidates(
        self,
        *,
        query_text: str,
        full_query: str = "",
        item: dict | None = None,
        top_k: int = 3,
    ) -> list[dict]:
        if not self._experience_db:
            return []

        item = dict(item or {})
        variants = self._build_prior_query_variants(
            query_text,
            full_query=full_query,
            item=item,
        )
        if not variants:
            return []

        candidates: list[dict] = []
        seen_quota_ids: set[str] = set()
        min_confidence = 70

        exact_lookup = getattr(self._experience_db, "_find_exact_match", None)
        if callable(exact_lookup):
            excluded_sources_getter = getattr(
                self._experience_db,
                "_get_online_excluded_sources",
                None,
            )
            excluded_sources = (
                list(excluded_sources_getter())
                if callable(excluded_sources_getter)
                else None
            )
            for variant in variants:
                try:
                    record = exact_lookup(
                        variant,
                        self.province,
                        authority_only=True,
                        exclude_sources=excluded_sources,
                    )
                except TypeError:
                    record = exact_lookup(variant, self.province, authority_only=True)
                except Exception as e:
                    logger.debug(f"缁忛獙 exact prior 鏌ヨ澶辫触锛堜笉褰卞搷涓绘祦绋嬶級: {e}")
                    record = None
                if not record or int(record.get("confidence") or 0) < min_confidence:
                    continue
                quota_ids = self._coerce_prior_list(record.get("quota_ids"))
                quota_names = self._coerce_prior_list(record.get("quota_names"))
                if not quota_ids:
                    continue
                quota_id = str(quota_ids[0] or "").strip()
                if not quota_id or quota_id in seen_quota_ids:
                    continue
                candidate = self._materialize_quota_candidate(
                    quota_id,
                    fallback_name=quota_names[0] if quota_names else "",
                )
                if not candidate:
                    continue
                candidate.update({
                    "match_source": "experience_injected_exact",
                    "is_experience_candidate": 1,
                    "experience_record_id": record.get("id"),
                    "experience_layer": record.get("layer", ""),
                    "experience_gate": "green",
                    "experience_similarity": 1.0,
                    "experience_total_score": 1.0,
                    "experience_confidence": float(record.get("confidence", 0.0) or 0.0),
                    "knowledge_prior_sources": ["experience"],
                    "knowledge_prior_score": 1.10,
                    "experience_exact_variant": variant,
                })
                candidates.append(candidate)
                seen_quota_ids.add(quota_id)
                if len(candidates) >= top_k:
                    return candidates

        find_experience = getattr(self._experience_db, "find_experience", None)
        if not callable(find_experience):
            return candidates

        for variant in variants:
            try:
                records = find_experience(
                    variant,
                    province=self.province,
                    limit=max(top_k, 5),
                    online_only=True,
                )
            except TypeError:
                records = find_experience(variant, province=self.province, limit=max(top_k, 5))
            except Exception as e:
                logger.debug(f"缁忛獙 bill_name exact prior 鏌ヨ澶辫触锛堜笉褰卞搷涓绘祦绋嬶級: {e}")
                continue
            for record in records or []:
                if str(record.get("bill_name", "") or "").strip() != variant:
                    continue
                if str(record.get("layer", "") or "").strip() == "candidate":
                    continue
                if int(record.get("confidence") or 0) < min_confidence:
                    continue
                quota_ids = self._coerce_prior_list(record.get("quota_ids"))
                quota_names = self._coerce_prior_list(record.get("quota_names"))
                if not quota_ids:
                    continue
                quota_id = str(quota_ids[0] or "").strip()
                if not quota_id or quota_id in seen_quota_ids:
                    continue
                candidate = self._materialize_quota_candidate(
                    quota_id,
                    fallback_name=quota_names[0] if quota_names else "",
                )
                if not candidate:
                    continue
                candidate.update({
                    "match_source": "experience_injected_exact",
                    "is_experience_candidate": 1,
                    "experience_record_id": record.get("id"),
                    "experience_layer": record.get("layer", ""),
                    "experience_gate": "green",
                    "experience_similarity": 1.0,
                    "experience_total_score": 1.0,
                    "experience_confidence": float(record.get("confidence", 0.0) or 0.0),
                    "knowledge_prior_sources": ["experience"],
                    "knowledge_prior_score": 1.05,
                    "experience_exact_variant": variant,
                })
                candidates.append(candidate)
                seen_quota_ids.add(quota_id)
                if len(candidates) >= top_k:
                    return candidates
        return candidates

    def _collect_universal_kb_prior_candidates(
        self,
        *,
        query_text: str,
        full_query: str = "",
        item: dict | None = None,
        books: list[str] | None = None,
        top_k: int = 3,
    ) -> list[dict]:
        if not self.universal_kb:
            return []

        query = str(full_query or query_text or "").strip()
        if not query:
            return []

        try:
            hints = self.universal_kb.search_hints(query, top_k=2, authority_only=True)
        except Exception as e:
            logger.debug(f"通用知识先验候选检索失败（不影响主流程）: {e}")
            return []

        candidates: list[dict] = []
        for hint in hints or []:
            similarity = float(hint.get("similarity", 0.0) or 0.0)
            confidence = float(hint.get("confidence", 0.0) or 0.0)
            if similarity < 0.75 or confidence < 70:
                continue
            patterns = [str(p).strip() for p in (hint.get("quota_patterns") or []) if str(p).strip()]
            for pattern in patterns[:2]:
                try:
                    matched = self.bm25_engine.search(pattern, top_k=2, books=books)
                except Exception as e:
                    logger.debug(f"通用知识定额模式检索失败（不影响主流程）: {pattern[:40]} {e}")
                    continue
                for row in matched or []:
                    quota_id = str(row.get("quota_id", "") or "").strip()
                    quota_name = str(row.get("name", "") or "").strip()
                    if not quota_id or not quota_name:
                        continue
                    candidate = dict(row)
                    candidate["match_source"] = "kb_injected"
                    candidate["is_kb_candidate"] = 1
                    candidate["kb_bill_pattern"] = hint.get("bill_pattern", "")
                    candidate["kb_quota_pattern"] = pattern
                    candidate["kb_similarity"] = similarity
                    candidate["kb_confidence"] = confidence
                    candidate["knowledge_prior_sources"] = ["universal_kb"]
                    candidate["knowledge_prior_score"] = max(
                        similarity * 0.9,
                        confidence / 100.0 * 0.75,
                    )
                    candidates.append(candidate)
                    if len(candidates) >= top_k:
                        return candidates
        return candidates

    def _collect_universal_kb_exact_prior_candidates(
        self,
        *,
        query_text: str,
        full_query: str = "",
        item: dict | None = None,
        books: list[str] | None = None,
        top_k: int = 3,
    ) -> list[dict]:
        if not self.universal_kb:
            return []

        exact_lookup = getattr(self.universal_kb, "_find_exact", None)
        if not callable(exact_lookup):
            return []

        variants = self._build_prior_query_variants(
            query_text,
            full_query=full_query,
            item=item,
        )
        if not variants:
            return []

        candidates: list[dict] = []
        seen_quota_ids: set[str] = set()
        for variant in variants:
            try:
                hint = exact_lookup(variant)
            except Exception as e:
                logger.debug(f"閫氱敤鐭ヨ瘑 exact prior 鏌ヨ澶辫触锛堜笉褰卞搷涓绘祦绋嬶級: {e}")
                hint = None
            if not hint:
                continue
            if str(hint.get("layer", "") or "").strip() != "authority":
                continue
            if float(hint.get("confidence", 0.0) or 0.0) < 70:
                continue
            patterns = [str(p).strip() for p in safe_json_list(hint.get("quota_patterns")) if str(p).strip()]
            for pattern in patterns[:2]:
                try:
                    matched = self.bm25_engine.search(pattern, top_k=2, books=books)
                except Exception as e:
                    logger.debug(f"閫氱敤鐭ヨ瘑 exact prior 瀹氶妫€绱㈠け璐ワ紙涓嶅奖鍝嶄富娴佺▼锛? {e}")
                    continue
                for row in matched or []:
                    quota_id = str(row.get("quota_id", "") or "").strip()
                    if not quota_id or quota_id in seen_quota_ids:
                        continue
                    candidate = dict(row)
                    candidate["match_source"] = "kb_injected_exact"
                    candidate["is_kb_candidate"] = 1
                    candidate["kb_bill_pattern"] = hint.get("bill_pattern", "")
                    candidate["kb_quota_pattern"] = pattern
                    candidate["kb_similarity"] = 1.0
                    candidate["kb_confidence"] = float(hint.get("confidence", 0.0) or 0.0)
                    candidate["knowledge_prior_sources"] = ["universal_kb"]
                    candidate["knowledge_prior_score"] = 1.00
                    candidate["kb_exact_variant"] = variant
                    candidates.append(candidate)
                    seen_quota_ids.add(quota_id)
                    if len(candidates) >= top_k:
                        return candidates
        return candidates

    def search(self, query: str, top_k: int = None,
               bm25_weight: float = None, vector_weight: float = None,
               books: list[str] = None,
               item: dict | None = None,
               context_prior: dict | None = None) -> list[dict]:
        """
        混合搜索：同时执行BM25和向量搜索，用RRF融合结果

        流程：
        1. 查通用知识库，获取搜索增强关键词（如果有权威层数据）
        2. 用原始query + 增强关键词分别执行BM25和向量搜索
        3. RRF融合排序

        参数:
            query: 搜索文本（清单项目名称+特征描述）
            top_k: 最终返回的候选数量
            bm25_weight: BM25搜索权重（默认0.3）
            vector_weight: 向量搜索权重（默认0.7）
            books: 限定搜索的册号列表（如["C10", "C8"]），为None时搜索全库

        返回:
            融合排序后的候选定额列表，每条包含:
            {id, quota_id, name, unit, hybrid_score, bm25_rank, vector_rank, ...}
        """
        top_k = top_k or config.HYBRID_TOP_K
        item = dict(item or {})
        context_prior = dict(context_prior or {})
        adaptive_strategy = str(
            item.get("adaptive_strategy")
            or context_prior.get("adaptive_strategy")
            or "standard"
        ).strip().lower()
        if adaptive_strategy not in {"fast", "standard", "deep"}:
            adaptive_strategy = "standard"
        primary_query_profile = dict(
            (item.get("canonical_query") or {}).get("primary_query_profile")
            or item.get("primary_query_profile")
            or context_prior.get("primary_query_profile")
            or {}
        )
        query_features = text_parser.parse_canonical(query or "")
        route_profile = build_query_route_profile(
            query,
            canonical_features=query_features,
            context_prior=context_prior,
        )
        rank_window = self._resolve_rank_window(
            top_k=top_k,
            query_features=query_features,
            route_profile=route_profile,
            adaptive_strategy=adaptive_strategy,
        )
        base_bm25_weight = config.BM25_WEIGHT if bm25_weight is None else bm25_weight
        base_vector_weight = config.VECTOR_WEIGHT if vector_weight is None else vector_weight
        bm25_weight, vector_weight, weight_reason = self._get_adaptive_weights(
            query=query,
            bm25_weight=base_bm25_weight,
            vector_weight=base_vector_weight,
        )
        bm25_top_k = self._resolve_engine_top_k(
            engine="bm25",
            top_k=top_k,
            rank_window=rank_window,
            adaptive_strategy=adaptive_strategy,
        )
        vector_top_k = self._resolve_engine_top_k(
            engine="vector",
            top_k=top_k,
            rank_window=rank_window,
            adaptive_strategy=adaptive_strategy,
        )
        # 非标准编号定额库（宁夏/甘肃/深圳等地方定额）：book翻译
        # API层传来的books带C前缀（如["C10"]），但这些省份的book是纯数字
        # match_core.py已有翻译逻辑，翻译后的books是纯数字（如["10"]），不需要再处理
        # 只在books仍带C前缀时介入（说明是API直接调的，没经过match_core翻译）
        if books and not self.uses_standard_books:
            available_books = set(self.bm25_engine.quota_books.values())
            books = self._normalize_requested_books_for_nonstandard_db(
                books,
                available_books,
                province=self.province,
            )

        # 会话缓存检查：相同query+books组合复用搜索结果
        cache_key = self._build_session_cache_key(
            query=query,
            books=books,
            top_k=top_k,
            adaptive_strategy=adaptive_strategy,
            primary_query_profile=primary_query_profile,
            query_features=query_features,
            route_profile=route_profile,
            rank_window=rank_window,
            bm25_weight=bm25_weight,
            vector_weight=vector_weight,
            weight_reason=weight_reason,
            bm25_top_k=bm25_top_k,
            vector_top_k=vector_top_k,
        )
        cached = self._session_cache.get(cache_key)
        if cached is not None:
            import copy
            logger.debug(f"搜索缓存命中: '{query[:20]}...' ({len(cached)}条)")
            finalized = self._finalize_candidates(
                copy.deepcopy(cached),
                query_text=query,
                expected_books=books,
            )
            return finalized[:top_k]

        # ============================================================
        # 第0步：查通用知识库获取搜索增强关键词
        # ============================================================
        kb_hints = []
        if adaptive_strategy == "standard" and self.universal_kb:
            try:
                cached_kb_hints = self._kb_keyword_cache.get(query)
                if cached_kb_hints is not None:
                    kb_hints = list(cached_kb_hints)
                else:
                    timeout_sec = float(getattr(config, "UNIVERSAL_KB_KEYWORD_TIMEOUT_SEC", 2.0) or 0.0)
                    blocked_until = float(getattr(self, "_kb_keyword_blocked_until", 0.0) or 0.0)
                    now = time.monotonic()
                    if timeout_sec > 0 and now < blocked_until:
                        kb_hints = []
                    elif timeout_sec > 0:
                        future = self._kb_keyword_executor.submit(
                            self.universal_kb.get_search_keywords,
                            query,
                        )
                        try:
                            kb_hints = future.result(timeout=timeout_sec)
                        except FutureTimeoutError:
                            cooldown_sec = float(
                                getattr(config, "UNIVERSAL_KB_KEYWORD_COOLDOWN_SEC", 30.0) or 0.0
                            )
                            self._kb_keyword_blocked_until = time.monotonic() + max(cooldown_sec, 0.0)
                            future.cancel()
                            logger.warning(
                                    f"閫氱敤鐭ヨ瘑搴撳叧閿瘝澧炲己瓒呮椂({timeout_sec:.1f}s)锛岃烦杩囨湰娆″寮? {query[:40]}"
                            )
                            kb_hints = []
                        except Exception as e:
                                logger.debug(f"閫氱敤鐭ヨ瘑搴撴煡璇㈠け璐ワ紙涓嶅奖鍝嶆悳绱級: {e}")
                                kb_hints = []
                    else:
                        kb_hints = self.universal_kb.get_search_keywords(query)
                    if len(self._kb_keyword_cache) >= self._KB_KEYWORD_CACHE_MAX:
                        old_key = next(iter(self._kb_keyword_cache))
                        del self._kb_keyword_cache[old_key]
                    self._kb_keyword_cache[query] = list(kb_hints or [])
                kb_hints = self._filter_kb_hints_for_query_features(
                    kb_hints,
                    query_features=query_features,
                )
                if kb_hints:
                    logger.debug(f"通用知识库增强: {kb_hints[:3]}")
            except Exception as e:
                logger.debug(f"通用知识库查询失败（不影响搜索）: {e}")

        # ============================================================
        # 第1步：多查询变体检索（Query2doc / MuGI 思路的轻量落地）
        # ============================================================
        query_variants = self._build_query_variants(
            query,
            kb_hints,
            query_features=query_features,
            route_profile=route_profile,
            primary_query_profile=primary_query_profile,
            adaptive_strategy=adaptive_strategy,
        )
        bm25_runs = []
        vector_runs = []
        total_bm25_hits = 0
        total_vector_hits = 0
        # 向量搜索开关（环境变量VECTOR_ENABLED=false可关闭，Docker/懒猫无GPU时用）
        vector_enabled = config.VECTOR_ENABLED

        # 批量预编码所有查询变体的向量（一次GPU调用，比逐条快很多）
        variant_queries = [v["query"] for v in query_variants]
        if vector_enabled:
            try:
                all_embeddings = self.vector_engine.encode_queries(variant_queries)
            except Exception as e:
                logger.warning(f"批量向量编码失败: {e}")
                all_embeddings = [None] * len(variant_queries)
        else:
            all_embeddings = [None] * len(variant_queries)

        for idx, variant in enumerate(query_variants, start=1):
            q_text = variant["query"]
            q_weight = variant["weight"]
            q_tag = variant["tag"]

            bm25_results = []
            try:
                bm25_results = self.bm25_engine.search(
                    q_text, top_k=bm25_top_k, books=books
                )
                total_bm25_hits += len(bm25_results)
            except Exception as e:
                logger.warning(f"BM25搜索失败[{q_tag}]: {e}")

            vector_results = []
            if vector_enabled:
                try:
                    # 使用预计算的向量，跳过逐条编码
                    embedding = all_embeddings[idx - 1] if all_embeddings[0] is not None else None
                    vector_results = self.vector_engine.search(
                        q_text, top_k=vector_top_k, books=books,
                        precomputed_embedding=embedding
                    )
                    total_vector_hits += len(vector_results)
                except Exception as e:
                    logger.warning(f"向量搜索失败[{q_tag}]: {e}")

            bm25_runs.append({
                "tag": q_tag,
                "query": q_text,
                "weight": q_weight,
                "results": bm25_results,
            })
            vector_runs.append({
                "tag": q_tag,
                "query": q_text,
                "weight": q_weight,
                "results": vector_results,
            })

            logger.debug(
                f"变体#{idx} [{q_tag}] 检索完成: "
                f"BM25={len(bm25_results)} 向量={len(vector_results)}"
            )

        # 如果两路都没有结果，返回空
        if total_bm25_hits == 0 and total_vector_hits == 0:
            logger.warning(f"两路搜索均无结果: '{query}'")
            return []

        # 如果只有一路有结果，做该路的多查询融合排序后返回
        if total_bm25_hits == 0:
            vector_only = self._merge_single_engine_runs(
                vector_runs, engine="vector", k=config.RRF_K
            )
            top_results = vector_only[:rank_window]
            for r in top_results:
                r["hybrid_score"] = r.get("vector_rrf_score", r.get("vector_score", 0))
                r["bm25_rank"] = None
                r["fusion_mode"] = "vector_only_rrf"
                r["effective_bm25_weight"] = bm25_weight
                r["effective_vector_weight"] = vector_weight
                r["fusion_weight_reason"] = weight_reason
            finalized = self._finalize_candidates(top_results, query_text=query, expected_books=books)
            return finalized[:top_k]

        if total_vector_hits == 0:
            bm25_only = self._merge_single_engine_runs(
                bm25_runs, engine="bm25", k=config.RRF_K
            )
            top_results = bm25_only[:rank_window]
            for r in top_results:
                r["hybrid_score"] = r.get("bm25_rrf_score", r.get("bm25_score", 0))
                r["vector_rank"] = None
                r["fusion_mode"] = "bm25_only_rrf"
                r["effective_bm25_weight"] = bm25_weight
                r["effective_vector_weight"] = vector_weight
                r["fusion_weight_reason"] = weight_reason
            finalized = self._finalize_candidates(top_results, query_text=query, expected_books=books)
            return finalized[:top_k]

        # ============================================================
        # 第2步：RRF融合排序
        # ============================================================

        use_multi_query = bool(getattr(config, "HYBRID_MULTI_QUERY_FUSION", True))
        multi_query_effective = use_multi_query and len(query_variants) > 1
        if multi_query_effective:
            merged = self._rrf_fusion_multi_query(
                bm25_runs=bm25_runs,
                vector_runs=vector_runs,
                bm25_weight=bm25_weight,
                vector_weight=vector_weight,
                k=config.RRF_K,
            )
        else:
            merged = self._rrf_fusion(
                bm25_results=bm25_runs[0]["results"],
                vector_results=vector_runs[0]["results"],
                bm25_weight=bm25_weight,
                vector_weight=vector_weight,
                k=config.RRF_K,
            )

        top_results = merged[:rank_window]

        for r in top_results:
            r["fusion_mode"] = "adaptive_multi_query_rrf" if multi_query_effective else "adaptive_rrf"
            r["effective_bm25_weight"] = bm25_weight
            r["effective_vector_weight"] = vector_weight
            r["fusion_weight_reason"] = weight_reason
            r["query_variants"] = [v["tag"] for v in query_variants]

        logger.debug(
            f"混合搜索: 变体={len(query_variants)} "
            f"BM25累计={total_bm25_hits} 向量累计={total_vector_hits} "
            f"权重(bm25={bm25_weight:.2f}, vector={vector_weight:.2f}, reason={weight_reason}) "
            f"→ 融合后 {len(top_results)} 条"
        )

        # 存入会话缓存（搜索结果不变的情况下复用）
        if top_results:
            import copy
            # 缓存超限时清除最早的一半，避免内存持续增长
            if len(self._session_cache) >= self._SESSION_CACHE_MAX:
                keys_to_remove = list(self._session_cache.keys())[:len(self._session_cache) // 2]
                for k in keys_to_remove:
                    del self._session_cache[k]
                logger.debug(f"搜索缓存超限({self._SESSION_CACHE_MAX})，已清除{len(keys_to_remove)}条旧缓存")
            self._session_cache[cache_key] = copy.deepcopy(top_results)
            finalized = self._finalize_candidates(
                copy.deepcopy(self._session_cache[cache_key]),
                query_text=query,
                expected_books=books,
            )
            return finalized[:top_k]

        return self._finalize_candidates(top_results, query_text=query, expected_books=books)[:top_k]

    def _get_adaptive_weights(self, query: str, bm25_weight: float,
                              vector_weight: float) -> tuple[float, float, str]:
        """
        查询自适应权重：
        - 规格型号/数字参数密集：提高BM25占比
        - 纯语义描述为主：提高向量占比
        """
        if not bool(getattr(config, "HYBRID_ADAPTIVE_FUSION", True)):
            total = max(bm25_weight + vector_weight, 1e-9)
            return bm25_weight / total, vector_weight / total, "static"

        boost = float(getattr(config, "HYBRID_ADAPTIVE_BOOST", 0.18))
        boost = min(max(boost, 0.0), 0.4)

        pattern_hits = 0
        spec_patterns = [
            r"DN\s*\d+",
            r"\d+\s*回路",
            r"\d+(\.\d+)?\s*(mm2|mm²|kva|kv|kw|a|m)",
            r"[A-Za-z]{1,8}[-_/]*\d+([*×xX/]\d+)*",
        ]
        for pattern in spec_patterns:
            if re.search(pattern, query, flags=re.IGNORECASE):
                pattern_hits += 1

        chinese_len = len(re.findall(r"[\u4e00-\u9fff]", query))
        route_profile = build_query_route_profile(query)
        route = str((route_profile or {}).get("route") or "").strip()
        reason = "balanced"
        new_bm25 = bm25_weight
        new_vector = vector_weight

        if route == "installation_spec":
            install_boost = max(boost, 0.28)
            new_bm25 = bm25_weight + install_boost
            new_vector = vector_weight - install_boost
            reason = str((route_profile or {}).get("reason") or "spec_heavy_installation")
        elif route == "spec_heavy" or pattern_hits >= 2:
            new_bm25 = bm25_weight + boost
            new_vector = vector_weight - boost
            reason = "spec_heavy"
        elif pattern_hits == 0 and chinese_len >= 18:
            new_bm25 = bm25_weight - boost
            new_vector = vector_weight + boost
            reason = "semantic_heavy"

        feedback_bias = self._get_feedback_bias()
        if feedback_bias != 0:
            new_bm25 += feedback_bias
            new_vector -= feedback_bias
            reason = f"{reason}+feedback"

        new_bm25 = max(new_bm25, 0.1)
        new_vector = max(new_vector, 0.1)
        total = new_bm25 + new_vector
        return new_bm25 / total, new_vector / total, reason

    @staticmethod
    def _count_spec_signals(text: str) -> int:
        return count_spec_signals(text)

    def _get_feedback_bias(self) -> float:
        """
        基于经验库最近样本计算全局偏置：
        - 规格型清单纠错率更高 -> 往BM25偏
        - 语义型清单纠错率更高 -> 往向量偏
        """
        if not bool(getattr(config, "HYBRID_FEEDBACK_ADAPTIVE_BIAS", True)):
            return 0.0

        refresh_sec = max(int(getattr(config, "HYBRID_FEEDBACK_BIAS_REFRESH_SEC", 300)), 30)
        now = time.time()
        if now - self._feedback_bias_ts < refresh_sec:
            return self._feedback_bias_value

        min_samples = max(int(getattr(config, "HYBRID_FEEDBACK_MIN_SAMPLES", 60)), 20)
        max_bias = float(getattr(config, "HYBRID_FEEDBACK_BIAS_MAX", 0.08))
        max_bias = min(max(max_bias, 0.0), 0.2)

        bias = 0.0
        try:
            if not self._experience_db:
                self._feedback_bias_value = 0.0
                self._feedback_bias_ts = now
                return 0.0

            rows = self._experience_db.get_feedback_bias_data(self.province)
            if not rows:
                self._feedback_bias_value = 0.0
                self._feedback_bias_ts = now
                return 0.0

            if len(rows) < min_samples:
                self._feedback_bias_value = 0.0
                self._feedback_bias_ts = now
                return 0.0

            spec_total = 0
            spec_corr = 0
            sem_total = 0
            sem_corr = 0

            for source, bill_text in rows:
                text = str(bill_text or "").strip()
                if not text:
                    continue
                is_spec = self._is_spec_heavy_text(text)
                is_corr = (source == "user_correction")
                if is_spec:
                    spec_total += 1
                    if is_corr:
                        spec_corr += 1
                else:
                    sem_total += 1
                    if is_corr:
                        sem_corr += 1

            if spec_total >= min_samples // 3 and sem_total >= min_samples // 3:
                spec_err = spec_corr / max(spec_total, 1)
                sem_err = sem_corr / max(sem_total, 1)
                delta = spec_err - sem_err
                # delta>0: 规格型更难，向BM25偏；delta<0: 语义型更难，向向量偏
                bias = max(-max_bias, min(max_bias, delta * 0.25))

        except Exception as e:
            logger.debug(f"反馈偏置计算失败（忽略，不影响主流程）: {e}")
            bias = 0.0

        self._feedback_bias_value = bias
        self._feedback_bias_ts = now
        return bias

    @staticmethod
    def _is_spec_heavy_text(text: str) -> bool:
        """判断文本是否为规格参数主导（DN/回路/型号/参数单位等）。"""
        patterns = [
            r"DN\s*\d+",
            r"\d+\s*回路",
            r"\d+(\.\d+)?\s*(mm2|mm²|kva|kv|kw|a|m)",
            r"[A-Za-z]{1,8}[-_/]*\d+([*×xX/]\d+)*",
        ]
        regex_hits = 0
        for p in patterns:
            if re.search(p, text, flags=re.IGNORECASE):
                regex_hits += 1
        # Preserve regex detection as a local fallback instead of letting
        # routed signal counting silently overwrite it.
        hits = max(regex_hits, HybridSearcher._count_spec_signals(text))
        chinese_len = len(re.findall(r"[\u4e00-\u9fff]", text))
        return hits >= 2 or (hits >= 1 and chinese_len <= 20)

    def _build_query_variants(self, query: str, kb_hints: list[str], *,
                              query_features: dict | None = None,
                              route_profile: dict | None = None,
                              primary_query_profile: dict | None = None,
                              adaptive_strategy: str | None = None) -> list[dict]:
        """
        构造少量高价值查询变体，避免纯原始query召回盲区。
        """
        strategy = str(adaptive_strategy or "standard").strip().lower()
        max_variants = int(getattr(config, "HYBRID_QUERY_VARIANTS", 4))
        if strategy == "fast":
            max_variants = min(max_variants, 2)
        elif strategy == "standard":
            max_variants = min(
                max_variants,
                max(1, int(getattr(config, "HYBRID_STANDARD_QUERY_VARIANTS", 3) or 3)),
            )
        elif strategy == "deep":
            max_variants = max(max_variants, 4)
        strategy_variant_cap = None
        if strategy == "deep":
            strategy_variant_cap = int(getattr(config, "HYBRID_DEEP_QUERY_VARIANTS", 3) or 3)
        max_variants = max(max_variants, 1)
        query_features = dict(query_features or {})
        route_profile = dict(route_profile or {})
        primary_query_profile = dict(primary_query_profile or {})
        if (
            str(query_features.get("family") or "").strip() == "pipe_support"
            and str(query_features.get("support_scope") or "").strip() == "管道支架"
            and dict(query_features.get("numeric_params") or {}).get("weight_t") is None
        ):
            kb_hints = [
                str(hint).strip()
                for hint in (kb_hints or [])
                if str(hint).strip()
                and "单件重量" not in str(hint)
                and "每组重量" not in str(hint)
            ]
        if query_features.get("family") and strategy != "standard":
            max_variants = max(max_variants, 5)
        numeric_params = dict(query_features.get("numeric_params") or {})
        if strategy != "standard" and primary_query_profile.get("quota_aliases") and any(
            numeric_params.get(key) is not None for key in ("dn", "kva", "kw")
        ):
            max_variants = max(max_variants, 7)
        if strategy_variant_cap is not None:
            max_variants = min(max_variants, max(1, strategy_variant_cap))
        raw_weights = getattr(config, "HYBRID_VARIANT_WEIGHTS", [1.0, 0.75, 0.60, 0.50])
        if not isinstance(raw_weights, (list, tuple)) or not raw_weights:
            raw_weights = [1.0, 0.75, 0.60, 0.50]
        variant_weights = [max(float(w), 0.05) for w in raw_weights]

        variants = []
        seen = set()

        def _add(q: str, tag: str):
            qn = re.sub(r"\s+", " ", q or "").strip()
            if not qn or qn in seen:
                return
            idx = len(variants)
            w = variant_weights[idx] if idx < len(variant_weights) else variant_weights[-1] * 0.85
            variants.append({"query": qn, "tag": tag, "weight": w})
            seen.add(qn)

        # V1: 原始query
        _add(query, "raw")

        # V2: 纯知识提示变体
        # 当 universal_kb 已经给出明确方向时，直接用定额族名检索，
        # 避免“原始短清单名+噪声词”继续主导排序。
        if kb_hints and len(variants) < max_variants:
            _add(kb_hints[0], "kb_hint_only")

        # V3: 规范化query（去噪、统一分隔符）
        normalized = re.sub(r"[，,。；;、|/\\]+", " ", query)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        _add(normalized, "normalized")

        primary_text = str(primary_query_profile.get("primary_text") or "").strip()
        primary_subject = str(primary_query_profile.get("primary_subject") or "").strip()
        quota_aliases = [
            str(value).strip()
            for value in list(primary_query_profile.get("quota_aliases") or [])
            if str(value).strip()
        ]
        decisive_terms = [
            str(value).strip()
            for value in list(primary_query_profile.get("decisive_terms") or [])
            if str(value).strip()
        ]
        retrieval_aliases = self._select_retrieval_aliases(quota_aliases, max_count=2)
        short_subject = re.sub(r"\s+", "", primary_subject)
        prefer_quota_alias_first = (
            str((route_profile or {}).get("route") or "").strip() in {"spec_heavy", "installation_spec"}
            and bool(retrieval_aliases)
            and not decisive_terms
            and 0 < len(short_subject) <= 12
        )
        if prefer_quota_alias_first:
            for idx, alias in enumerate(retrieval_aliases, start=1):
                if len(variants) >= max_variants:
                    break
                _add(alias, f"quota_alias_{idx}")
        if primary_text and primary_text != normalized and len(variants) < max_variants:
            _add(primary_text, "primary_text")
        if primary_subject and primary_subject not in {query, normalized, primary_text} and len(variants) < max_variants:
            _add(primary_subject, "primary_subject")
        for idx, alias in enumerate(retrieval_aliases, start=1):
            if len(variants) >= max_variants:
                break
            _add(alias, f"quota_alias_{idx}")
        quota_style_variants = self._build_quota_style_query_variants(
            query_features=query_features,
            primary_query_profile=primary_query_profile,
        )
        for idx, quota_style_variant in enumerate(quota_style_variants, start=1):
            if len(variants) >= max_variants:
                break
            _add(quota_style_variant, f"quota_style_{idx}")
        if decisive_terms and len(variants) < max_variants:
            merged_primary = " ".join(
                token for token in [primary_subject or primary_text, *decisive_terms[:2]]
                if token
            ).strip()
            _add(merged_primary, "primary_decisive")

        # V3.5: family-focused 变体
        family_variants = self._build_family_query_variants(
            query_features=query_features,
            route_profile=route_profile,
        )
        for idx, family_variant in enumerate(family_variants, start=1):
            if len(variants) >= max_variants:
                break
            _add(family_variant, f"family_focus_{idx}")

        # V4: 参数强化query（把关键规格参数显式再强调一遍）
        param_tokens = []
        normalized_lower = normalized.lower()
        for p in [
            r"DN\s*\d+",
            r"\d+\s*回路",
            r"\d+(\.\d+)?\s*(mm2|mm²|kva|kv|kw|a|m)",
            r"[A-Za-z]{1,8}[-_/]*\d+([*×xX/]\d+)*",
        ]:
            for m in re.finditer(p, query, flags=re.IGNORECASE):
                token = m.group(0).strip()
                if token and token not in param_tokens and token.lower() not in normalized_lower:
                    param_tokens.append(token)
        if param_tokens:
            _add(f"{normalized} {' '.join(param_tokens[:4])}", "param_boost")

        # V5: 核心名词变体（去掉动作词/修饰词，只保留核心实体+参数）
        # 目的：BM25对长query中的噪声词敏感，精简后匹配更精准
        # 例如："管道安装 焊接钢管 镀锌 DN25" → "焊接钢管 镀锌 DN25"
        core_noun = self._extract_core_noun_query(normalized)
        if core_noun:
            _add(core_noun, "core_noun")

        # V6（L7新增）: 同义词反向替换变体
        # query_builder已做"清单名→定额名"替换（如"白铁管"→"镀锌钢管"），
        # 这里做反向替换生成额外变体，覆盖定额库中可能存在的清单原始写法。
        if getattr(config, "BM25_SYNONYM_EXPANSION_ENABLED", False) and len(variants) < max_variants:
            syn_variant = self._build_synonym_variant(normalized)
            if syn_variant:
                _add(syn_variant, "synonym_expand")

        # 额外兜底：若仍不够且有知识库提示，再补“原query + hint”拼接变体
        if kb_hints and len(variants) < max_variants:
            _add(f"{query} {kb_hints[0]}", "kb_hint")

        return variants[:max_variants]

    @classmethod
    def _filter_kb_hints_for_query_features(cls,
                                            kb_hints: list[str],
                                            *,
                                            query_features: dict | None = None) -> list[str]:
        query_features = dict(query_features or {})
        family = str(query_features.get("family") or "").strip()
        entity = str(query_features.get("entity") or "").strip()
        system = str(query_features.get("system") or "").strip()

        if not kb_hints:
            return []

        filtered: list[str] = []
        should_block_fee_hints = family in cls._KB_HINT_OBJECT_FAMILIES
        for hint in kb_hints:
            text = str(hint or "").strip()
            if not text:
                continue
            if should_block_fee_hints and cls._is_fee_like_kb_hint(text):
                continue
            if (
                family == "conduit_raceway"
                and entity == "配管"
                and system == "电气"
                and
                any(word in text for word in cls._CONDUIT_HINT_BLOCK_WORDS)
                and not any(word in text for word in cls._CONDUIT_HINT_KEEP_WORDS)
            ):
                continue
            filtered.append(text)
        return filtered

    @classmethod
    def _is_fee_like_kb_hint(cls, text: str) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        if any(word in text for word in cls._KB_HINT_FEE_BLOCK_WORDS):
            return True
        if "超高" in text and any(word in text for word in cls._KB_HINT_FEE_CONTEXT_WORDS):
            return True
        return False

    @staticmethod
    def _format_numeric_variant_tokens(query_features: dict) -> list[str]:
        numeric_params = dict((query_features or {}).get("numeric_params") or {})
        tokens: list[str] = []
        if numeric_params.get("dn") is not None:
            tokens.append(f"DN{int(numeric_params['dn'])}")
        if numeric_params.get("circuits") is not None:
            tokens.append(f"{int(numeric_params['circuits'])}回路")
        if numeric_params.get("port_count") is not None:
            tokens.append(f"{int(numeric_params['port_count'])}口")
        if numeric_params.get("switch_gangs") is not None:
            tokens.append(f"{int(numeric_params['switch_gangs'])}联")
        if numeric_params.get("cable_section") is not None:
            value = numeric_params["cable_section"]
            value_text = int(value) if float(value).is_integer() else value
            tokens.append(f"{value_text}mm2")
        if numeric_params.get("half_perimeter") is not None:
            tokens.append(f"半周长{numeric_params['half_perimeter']}")
        if numeric_params.get("perimeter") is not None:
            tokens.append(f"周长{numeric_params['perimeter']}")
        if numeric_params.get("weight_t") is not None:
            kg = float(numeric_params["weight_t"]) * 1000.0
            kg_text = int(kg) if kg.is_integer() else round(kg, 2)
            tokens.append(f"{kg_text}kg")
        return tokens

    @staticmethod
    def _format_bucket_value(value) -> str:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return str(value or "").strip()
        if numeric.is_integer():
            return str(int(numeric))
        return f"{numeric:.2f}".rstrip("0").rstrip(".")

    @classmethod
    def _nearest_quota_bucket(cls, value, buckets: tuple[int, ...]) -> int | None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        for bucket in buckets:
            if numeric <= bucket:
                return int(bucket)
        return int(buckets[-1]) if buckets else None

    @classmethod
    def _looks_like_plastic_pipe_alias(cls, alias: str, primary_subject: str = "") -> bool:
        combined = f"{alias} {primary_subject}".upper()
        return any(marker in combined for marker in cls._PLASTIC_PIPE_MARKERS)

    @staticmethod
    def _looks_like_sweep_alias(alias: str, primary_subject: str = "") -> bool:
        combined = f"{alias} {primary_subject}"
        return "清扫口" in combined or "扫除口" in combined

    @staticmethod
    def _looks_like_inverter_alias(alias: str, primary_subject: str = "") -> bool:
        combined = f"{alias} {primary_subject}"
        return "逆变器" in combined

    @classmethod
    def _build_quota_style_query_variants(
        cls,
        *,
        query_features: dict | None = None,
        primary_query_profile: dict | None = None,
    ) -> list[str]:
        query_features = dict(query_features or {})
        primary_query_profile = dict(primary_query_profile or {})
        numeric_params = dict(query_features.get("numeric_params") or {})
        primary_subject = str(primary_query_profile.get("primary_subject") or "").strip()
        aliases = [
            str(value).strip()
            for value in list(primary_query_profile.get("quota_aliases") or [])
            if str(value).strip()
        ]
        alias_pool = cls._select_retrieval_aliases(aliases, max_count=2)
        if primary_subject and primary_subject not in alias_pool:
            alias_pool.append(primary_subject)
        if not alias_pool:
            return []

        system = str(query_features.get("system") or "").strip()
        variants: list[str] = []
        seen: set[str] = set()

        def _push(text: str):
            normalized = re.sub(r"\s+", " ", str(text or "").strip())
            if not normalized or normalized in seen:
                return
            variants.append(normalized)
            seen.add(normalized)

        dn_value = numeric_params.get("dn")
        if dn_value is not None:
            default_bucket = cls._nearest_quota_bucket(dn_value, cls._QUOTA_DN_BUCKETS)
            for alias in alias_pool[:2]:
                if default_bucket is None:
                    continue
                bucket = max(default_bucket, 50) if cls._looks_like_sweep_alias(alias, primary_subject) else default_bucket
                bucket_text = cls._format_bucket_value(bucket)
                if cls._looks_like_sweep_alias(alias, primary_subject):
                    _push(f"{alias} {bucket_text}mm以内")
                    continue
                if system and system not in alias:
                    if cls._looks_like_plastic_pipe_alias(alias, primary_subject):
                        _push(f"{system} {alias} 公称外径(mm以内) {bucket_text}")
                    else:
                        _push(f"{system} {alias} 公称直径(mm以内) {bucket_text}")
                if cls._looks_like_plastic_pipe_alias(alias, primary_subject):
                    _push(f"{alias} 公称外径(mm以内) {bucket_text}")
                    _push(f"{alias} 外径(mm以内) {bucket_text}")
                else:
                    _push(f"{alias} 公称直径(mm以内) {bucket_text}")
                    _push(f"{alias} 管外径(mm以内) {bucket_text}")
                raw_dn = cls._format_bucket_value(dn_value)
                if bucket_text != raw_dn:
                    _push(f"{alias} {bucket_text}mm以内")

        kva_value = numeric_params.get("kva")
        if kva_value is not None:
            kva_text = cls._format_bucket_value(kva_value)
            kva_bucket = cls._nearest_quota_bucket(kva_value, cls._QUOTA_CAPACITY_BUCKETS)
            for alias in alias_pool[:2]:
                if system and system not in alias:
                    _push(f"{system} {alias} 容量{kva_text}kVA")
                _push(f"{alias} 容量{kva_text}kVA")
                _push(f"{alias} 不间断电源容量{kva_text}kVA以下")
                if kva_bucket is not None:
                    _push(f"{alias} 容量{cls._format_bucket_value(kva_bucket)}kVA以下")

        kw_value = numeric_params.get("kw")
        if kw_value is not None:
            kw_text = cls._format_bucket_value(kw_value)
            kw_aliases = list(alias_pool[:2])
            kw_aliases.sort(
                key=lambda value: (
                    cls._looks_like_numeric_alias(value),
                    len(str(value or "")),
                )
            )
            for alias in kw_aliases:
                _push(f"{alias} 功率{kw_text}kW")
                if cls._looks_like_inverter_alias(alias, primary_subject):
                    for bucket in cls._INVERTER_POWER_BUCKETS:
                        if kw_value <= bucket:
                            _push(f"{alias} 功率≤{cls._format_bucket_value(bucket)}kW")

        return variants[:4]

    def _build_family_query_variants(self, *, query_features: dict,
                                     route_profile: dict) -> list[str]:
        family = str((query_features or {}).get("family") or "").strip()
        entity = str((query_features or {}).get("entity") or "").strip()
        canonical_name = str((query_features or {}).get("canonical_name") or "").strip()
        material = str((query_features or {}).get("material") or "").strip()
        connection = str((query_features or {}).get("connection") or "").strip()
        install_method = str((query_features or {}).get("install_method") or "").strip()
        support_scope = str((query_features or {}).get("support_scope") or "").strip()
        support_action = str((query_features or {}).get("support_action") or "").strip()
        system = str((query_features or {}).get("system") or "").strip()
        traits = [
            str(value).strip()
            for value in ((query_features or {}).get("traits") or [])
            if str(value).strip()
        ]
        numeric_params = dict((query_features or {}).get("numeric_params") or {})
        route = str((route_profile or {}).get("route") or "").strip()

        if not family:
            return []

        variants: list[str] = []
        numeric_tokens = self._format_numeric_variant_tokens(query_features)
        base_tokens = [token for token in (canonical_name, entity, material, connection, install_method) if token]
        trait_tokens = traits[:3]

        def _push(*tokens: str):
            text = " ".join(token for token in tokens if token).strip()
            if text and text not in variants:
                variants.append(text)

        if family == "bridge_support":
            _push("桥架支撑架", entity or "支吊架", *trait_tokens, *numeric_tokens)
        elif family == "pipe_support":
            if support_scope == "管道支架" and numeric_params.get("weight_t") is None:
                action_phrase = "制作安装"
                if support_action == "安装":
                    action_phrase = "安装"
                _push(f"{support_scope}{action_phrase} 一般管架")
                _push(f"室内管道{support_scope}{action_phrase} 一般管架")
            _push("管道支架", entity or "支吊架", *trait_tokens, *numeric_tokens)
        elif family == "valve_accessory":
            _push(entity or canonical_name, connection, *numeric_tokens)
            _push(system, entity or canonical_name, *numeric_tokens)
        elif family == "sanitary_fixture":
            _push(entity or canonical_name, *trait_tokens, install_method, *numeric_tokens)
        elif family in {"air_terminal", "air_valve", "air_device"}:
            _push(entity or canonical_name, *trait_tokens, *numeric_tokens)
            _push(system, entity or canonical_name, *trait_tokens)
        elif family == "electrical_box":
            _push(entity or canonical_name, install_method, *numeric_tokens)
        elif family == "protection_device":
            _push(entity or canonical_name, *trait_tokens, *numeric_tokens)
            _push(system, entity or canonical_name, *trait_tokens)
        elif family == "conduit_raceway":
            _push(material, entity or canonical_name, install_method, *numeric_tokens)
        elif family == "bridge_raceway":
            _push(canonical_name or entity, *trait_tokens, *numeric_tokens)
        elif family == "cable_family":
            _push(canonical_name or entity, material, *trait_tokens, *numeric_tokens)
        else:
            _push(*base_tokens, *trait_tokens, *numeric_tokens)

        if route in {"installation_spec", "spec_heavy"} and canonical_name and canonical_name != entity:
            _push(canonical_name, *numeric_tokens)

        return variants[:2]

    def _resolve_rank_window(self, *, top_k: int,
                             query_features: dict,
                             route_profile: dict,
                             adaptive_strategy: str | None = None) -> int:
        family = str((query_features or {}).get("family") or "").strip()
        route = str((route_profile or {}).get("route") or "").strip()
        spec_count = int((route_profile or {}).get("spec_signal_count", 0) or 0)
        strategy = str(adaptive_strategy or "standard").strip().lower()

        if strategy == "fast":
            return int(max(int(top_k), 8))

        rank_window = max(int(top_k), 10)
        if route in {"installation_spec", "spec_heavy"}:
            rank_window = max(rank_window, top_k * 3, 30)
        if family in self._FAMILY_WINDOW_FAMILIES:
            rank_window = max(rank_window, top_k * 4, 40)
        if spec_count >= 2 and family:
            rank_window = max(rank_window, top_k * 5, 50)
        if strategy == "deep":
            cap = int(getattr(config, "HYBRID_DEEP_RANK_WINDOW_CAP", 72) or 72)
        else:
            cap = int(getattr(config, "HYBRID_STANDARD_RANK_WINDOW_CAP", 50) or 50)
        if cap > 0:
            rank_window = min(rank_window, cap)
        return int(rank_window)

    @staticmethod
    def _resolve_engine_top_k(*, engine: str, top_k: int, rank_window: int,
                              adaptive_strategy: str | None = None) -> int:
        strategy = str(adaptive_strategy or "standard").strip().lower()
        if engine == "bm25":
            default_top_k = int(getattr(config, "BM25_TOP_K", top_k))
        else:
            default_top_k = int(getattr(config, "VECTOR_TOP_K", top_k))

        if strategy == "fast":
            return int(max(rank_window, min(default_top_k, max(int(top_k) + 4, 8))))
        return int(max(default_top_k, rank_window))

    @staticmethod
    def _extract_core_noun_query(query: str) -> str | None:
        """从查询中提取核心名词+参数，去掉动作词和修饰词

        例如：
          "管道安装 焊接钢管 镀锌 DN25" → "焊接钢管 镀锌 DN25"
          "配电箱墙上明装 规格 8回路"   → "配电箱 8回路"
          "电缆沿桥架敷设 截面 185"     → "电缆 桥架 截面 185"
        """
        # 去掉动作词（这些词在定额名称中通常是修饰性的）
        ACTION_WORDS = ["安装", "敷设", "制作", "施工", "铺设", "布线", "配置"]
        FILLER_WORDS = ["公称直径", "规格", "以内", "以上", "以下",
                        "墙上", "柱上", "明装", "暗装", "落地"]
        core = query
        for word in ACTION_WORDS + FILLER_WORDS:
            core = core.replace(word, " ")
        core = re.sub(r"\s+", " ", core).strip()

        # 如果核心文本太短（<3字符）或和原文相同，不生成变体
        if len(core) < 3 or core == query.strip():
            return None
        # 如果去掉的内容太少（长度只减少了<20%），变体没有价值
        if len(core) > len(query.strip()) * 0.8:
            return None
        return core

    # 同义词反向映射缓存（类级，避免每次搜索重建）
    _synonym_reverse_cache: dict | None = None
    _synonym_reverse_cache_id: int = 0  # 追踪源同义词表的id，变化时自动失效

    @staticmethod
    def _build_synonym_variant(query: str) -> str | None:
        """生成同义词反向替换变体（L7）

        _apply_synonyms 做"清单名→定额名"替换（如"镀锌钢管"→"焊接钢管 镀锌"），
        到达混合搜索时query中已经是定额名了。

        这里构建反向映射：定额名→清单名，生成一个用"清单原始写法"的变体。
        因为定额库的 search_text 可能包含清单常用写法。

        例如：query="焊接钢管 镀锌 DN25"（已被替换过）
             反向变体="镀锌钢管 DN25"（清单原始写法）
        """
        try:
            from src.query_builder import _load_synonyms
            synonyms = _load_synonyms()
        except ImportError:
            return None

        if not synonyms:
            return None

        # 使用类级缓存，避免每次调用都重建反向映射（批量场景下几百次调用只建一次）
        # 通过 id(synonyms) 检测源同义词表是否被重置（测试场景 _SYNONYMS_CACHE=None 后重加载）
        syn_id = id(synonyms)
        if HybridSearcher._synonym_reverse_cache is None or HybridSearcher._synonym_reverse_cache_id != syn_id:
            reverse_map = {}
            for bill_term, quota_term in synonyms.items():
                if quota_term not in reverse_map:
                    reverse_map[quota_term] = []
                reverse_map[quota_term].append(bill_term)
            # 按定额术语长度降序排列（预排序，避免每次调用排序）
            HybridSearcher._synonym_reverse_cache = dict(
                sorted(reverse_map.items(), key=lambda x: len(x[0]), reverse=True)
            )
            HybridSearcher._synonym_reverse_cache_id = syn_id

        # 在query中查找被替换过的定额术语
        variant = query
        for quota_term, bill_terms in HybridSearcher._synonym_reverse_cache.items():
            if quota_term in variant:
                # 用最短的清单术语替换（通常更通用）
                shortest = min(bill_terms, key=len)
                variant = variant.replace(quota_term, shortest, 1)
                break  # 只做一次替换

        return variant if variant != query else None

    def _merge_single_engine_runs(self, runs: list[dict], engine: str,
                                  k: int = 60) -> list[dict]:
        """
        单路搜索（仅BM25或仅向量）时的多查询RRF融合。
        """
        if engine not in ("bm25", "vector"):
            raise ValueError(f"unsupported engine: {engine}")

        score_key = "bm25_score" if engine == "bm25" else "vector_score"
        rank_key = "bm25_rank" if engine == "bm25" else "vector_rank"
        rrf_key = "bm25_rrf_score" if engine == "bm25" else "vector_rrf_score"

        score_map = {}
        best_rank_map = {}
        best_result_map = {}

        for run in runs:
            run_weight = max(float(run.get("weight", 1.0)), 0.05)
            for rank, result in enumerate(run.get("results", []), start=1):
                db_id = result["id"]
                score_map[db_id] = score_map.get(db_id, 0.0) + run_weight / (k + rank)
                if db_id not in best_rank_map or rank < best_rank_map[db_id]:
                    best_rank_map[db_id] = rank
                    best_result_map[db_id] = result

        merged = []
        for db_id, rrf_score in score_map.items():
            result = dict(best_result_map[db_id])
            result[rrf_key] = rrf_score
            result[rank_key] = best_rank_map[db_id]
            if score_key not in result:
                result[score_key] = None
            merged.append(result)

        merged.sort(key=lambda x: x[rrf_key], reverse=True)
        return merged

    @staticmethod
    def _rrf_result_identity(result: dict) -> str:
        source_province = str(
            result.get("source_province")
            or result.get("_source_province")
            or result.get("province")
            or ""
        ).strip()
        return f"{source_province}:{result['id']}"

    def _rrf_fusion_multi_query(self, bm25_runs: list[dict], vector_runs: list[dict],
                                bm25_weight: float, vector_weight: float,
                                k: int = 60) -> list[dict]:
        """
        多查询 + 双路引擎的加权RRF融合。
        """
        bm25_rank_maps = []
        vector_rank_maps = []
        all_ids = set()

        for run in bm25_runs:
            rank_map = {}
            for rank, result in enumerate(run.get("results", []), start=1):
                unified_id = self._rrf_result_identity(result)
                rank_map[unified_id] = (rank, result)
                all_ids.add(unified_id)
            bm25_rank_maps.append((max(float(run.get("weight", 1.0)), 0.05), rank_map))

        for run in vector_runs:
            rank_map = {}
            for rank, result in enumerate(run.get("results", []), start=1):
                unified_id = self._rrf_result_identity(result)
                rank_map[unified_id] = (rank, result)
                all_ids.add(unified_id)
            vector_rank_maps.append((max(float(run.get("weight", 1.0)), 0.05), rank_map))

        scored_results = []
        for unified_id in sorted(all_ids, key=lambda value: (str(type(value)), str(value))):
            bm25_rank = None
            vector_rank = None
            rrf_score = 0.0
            db_id = unified_id
            bm25_base_result = None
            vector_base_result = None
            bm25_score = None
            vector_score = None

            for run_weight, rank_map in bm25_rank_maps:
                if unified_id in rank_map:
                    rank, result = rank_map[unified_id]
                    rrf_score += (bm25_weight * run_weight) / (k + rank)
                    if bm25_rank is None or rank < bm25_rank:
                        bm25_rank = rank
                        bm25_score = result.get("bm25_score", 0)
                        bm25_base_result = result

            for run_weight, rank_map in vector_rank_maps:
                if unified_id in rank_map:
                    rank, result = rank_map[unified_id]
                    rrf_score += (vector_weight * run_weight) / (k + rank)
                    if vector_rank is None or rank < vector_rank:
                        vector_rank = rank
                        vector_score = result.get("vector_score", 0)
                        # Prefer the best-ranked vector variant as the base result.
                        vector_base_result = result

            # Prefer vector metadata when available, but only from the best-ranked
            # variant on that retrieval path to avoid cross-variant field mixing.
            base_result = vector_base_result or bm25_base_result

            if base_result is None:
                continue

            result = dict(base_result)
            result["hybrid_score"] = rrf_score
            result["bm25_rank"] = bm25_rank
            result["vector_rank"] = vector_rank
            result["bm25_score"] = bm25_score
            result["vector_score"] = vector_score
            scored_results.append(result)

        scored_results.sort(key=self._hybrid_result_sort_key)
        return scored_results

    def _rrf_fusion(self, bm25_results: list[dict], vector_results: list[dict],
                    bm25_weight: float, vector_weight: float,
                    k: int = 60) -> list[dict]:
        """
        RRF（Reciprocal Rank Fusion）融合算法

        原理：
        - 对于每条结果，根据它在BM25和向量搜索中的排名计算融合分数
        - RRF_score = bm25_weight / (k + bm25_rank) + vector_weight / (k + vector_rank)
        - k=60是标准值，作用是平滑排名差异，避免排名第1的结果权重过大

        参数:
            bm25_results: BM25搜索结果（已按分数降序排列）
            vector_results: 向量搜索结果（已按分数降序排列）
            bm25_weight: BM25权重（默认0.3）
            vector_weight: 向量权重（默认0.7）
            k: RRF常数（默认60，标准值）

        返回:
            融合排序后的结果列表
        """
        # 建立数据库ID → 排名的映射
        # rank从1开始（排名第1是最好的）
        bm25_rank_map = {}  # {db_id: (rank, result_dict)}
        for rank, result in enumerate(bm25_results, start=1):
            unified_id = self._rrf_result_identity(result)
            bm25_rank_map[unified_id] = (rank, result)

        vector_rank_map = {}  # {db_id: (rank, result_dict)}
        for rank, result in enumerate(vector_results, start=1):
            unified_id = self._rrf_result_identity(result)
            vector_rank_map[unified_id] = (rank, result)

        # 收集所有出现过的ID（去重合并）
        all_ids = set(bm25_rank_map.keys()) | set(vector_rank_map.keys())

        # 计算每条结果的RRF融合分数
        scored_results = []
        for unified_id in sorted(all_ids, key=lambda value: (str(type(value)), str(value))):
            bm25_rank = None
            vector_rank = None
            rrf_score = 0.0
            db_id = unified_id

            # BM25排名贡献
            if unified_id in bm25_rank_map:
                bm25_rank = bm25_rank_map[unified_id][0]
                rrf_score += bm25_weight / (k + bm25_rank)

            # 向量排名贡献
            if unified_id in vector_rank_map:
                vector_rank = vector_rank_map[unified_id][0]
                rrf_score += vector_weight / (k + vector_rank)

            # 选一个完整的结果字典作为基础（优先用向量的，因为它有更多语义信息）
            if db_id in vector_rank_map:
                result = dict(vector_rank_map[db_id][1])  # 复制一份，避免修改原数据
            else:
                result = dict(bm25_rank_map[db_id][1])

            # 添加融合信息
            result["hybrid_score"] = rrf_score
            result["bm25_rank"] = bm25_rank        # 在BM25中的排名（None表示未出现）
            result["vector_rank"] = vector_rank     # 在向量搜索中的排名（None表示未出现）
            result["bm25_score"] = bm25_rank_map[db_id][1].get("bm25_score", 0) if db_id in bm25_rank_map else None
            result["vector_score"] = vector_rank_map[db_id][1].get("vector_score", 0) if db_id in vector_rank_map else None

            scored_results.append(result)

        # 按RRF分数降序排列
        scored_results.sort(key=self._hybrid_result_sort_key)

        return scored_results

    @classmethod
    def _is_family_hard_conflict(cls, query_family: str, candidate_family: str) -> bool:
        if not query_family or not candidate_family or query_family == candidate_family:
            return False
        return frozenset((query_family, candidate_family)) in cls._FAMILY_GATE_HARD_CONFLICTS

    def _score_family_gate(self, query_features: dict, candidate: dict,
                           expected_books: list[str] | None = None) -> tuple[float, bool, str]:
        candidate_features = candidate.get("candidate_canonical_features") or candidate.get("canonical_features") or {}
        query_family = str((query_features or {}).get("family") or "").strip()
        candidate_family = str((candidate_features or {}).get("family") or "").strip()
        query_entity = str((query_features or {}).get("entity") or "").strip()
        candidate_entity = str((candidate_features or {}).get("entity") or "").strip()
        query_system = str((query_features or {}).get("system") or "").strip()
        candidate_system = str((candidate_features or {}).get("system") or "").strip()

        if not query_family:
            return 0.0, False, ""

        score = 0.0
        details: list[str] = []
        hard_conflict = False
        candidate_book = str(get_book_from_quota_id(candidate.get("quota_id", "")) or "").strip().upper()
        normalized_expected_books = [
            str(book or "").strip().upper()
            for book in (expected_books or [])
            if str(book or "").strip()
        ]

        if normalized_expected_books and candidate_book and candidate_book not in normalized_expected_books:
            score -= 3.0
            details.append(f"册号偏差:{candidate_book}")

        if self._is_family_hard_conflict(query_family, candidate_family):
            score -= 2.0
            hard_conflict = True
            details.append(f"家族冲突:{query_family}!={candidate_family}")
        elif query_family and candidate_family and query_family == candidate_family:
            score += 1.2
            details.append(f"同家族:{candidate_family}")
            if (
                query_family in self._FAMILY_GATE_STRICT_ENTITY_FAMILIES
                and query_entity and candidate_entity and query_entity != candidate_entity
            ):
                score -= 1.1
                hard_conflict = True
                details.append(f"家族内实体冲突:{query_entity}!={candidate_entity}")
            elif query_entity and candidate_entity and query_entity == candidate_entity:
                score += 0.6
                details.append(f"同实体:{candidate_entity}")
        elif query_entity and candidate_entity and query_entity == candidate_entity:
            score += 0.5
            details.append(f"同实体:{candidate_entity}")
        elif query_entity and candidate_entity:
            score -= 0.15
            details.append(f"实体偏差:{query_entity}!={candidate_entity}")

        if query_system and candidate_system:
            if query_system == candidate_system:
                score += 0.15
                details.append(f"同系统:{candidate_system}")
            else:
                score -= 0.10
                details.append(f"系统偏差:{query_system}!={candidate_system}")

        return score, hard_conflict, "; ".join(details)

    @staticmethod
    def _score_support_subtype_gate(query_text: str, query_features: dict, candidate: dict) -> tuple[float, str]:
        query_features = dict(query_features or {})
        if str(query_features.get("family") or "").strip() != "pipe_support":
            return 0.0, ""
        query_scope = str(query_features.get("support_scope") or "").strip()
        if not query_scope and "管道支架" in (query_text or ""):
            query_scope = "管道支架"
        if query_scope != "管道支架":
            return 0.0, ""

        traits = [
            str(value).strip()
            for value in (query_features.get("traits") or [])
            if str(value).strip()
        ]
        query_text = query_text or ""
        candidate_name = str(candidate.get("name") or "")
        has_general_support_shape = (
            "一般管架" in query_text
            or any("一般管架" in trait for trait in traits)
            or "按需制作" in query_text
        )
        has_weight_bucket = (
            dict(query_features.get("numeric_params") or {}).get("weight_t") is not None
            or any(token in query_text for token in ("单件重量", "每组重量"))
        )
        if not has_general_support_shape or has_weight_bucket or not candidate_name:
            return 0.0, ""

        score = 0.0
        details: list[str] = []
        if "一般管架" in candidate_name:
            score += 0.9
            details.append("泛型管架优先")

        for token in ("木垫式", "弹簧式", "侧向", "纵向", "门型", "单管", "多管"):
            if token in candidate_name and token not in query_text:
                score -= 1.1
                details.append(f"未声明子型:{token}")

        return score, "; ".join(details)

    def _apply_family_gate(self, query_text: str, candidates: list[dict],
                           expected_books: list[str] | None = None) -> list[dict]:
        if not candidates:
            return candidates
        query_features = text_parser.parse_canonical(query_text or "")
        query_family = str(query_features.get("family") or "").strip()
        query_entity = str(query_features.get("entity") or "").strip()
        if not query_family and not query_entity:
            return candidates

        for index, candidate in enumerate(candidates):
            gate_score, hard_conflict, gate_detail = self._score_family_gate(
                query_features,
                candidate,
                expected_books=expected_books,
            )
            support_subtype_score, support_subtype_detail = self._score_support_subtype_gate(
                query_text,
                query_features,
                candidate,
            )
            gate_score += support_subtype_score
            candidate["family_gate_score"] = gate_score
            candidate["family_gate_hard_conflict"] = hard_conflict
            detail_parts = [part for part in (gate_detail, support_subtype_detail) if part]
            candidate["family_gate_detail"] = "; ".join(detail_parts)
            candidate["support_subtype_gate_score"] = support_subtype_score
            candidate["_family_gate_index"] = index

        candidates.sort(key=self._stable_result_identity)
        candidates.sort(
            key=lambda candidate: (
                1 if not candidate.get("family_gate_hard_conflict") else 0,
                float(candidate.get("family_gate_score", 0.0)),
                float(candidate.get("hybrid_score", candidate.get("rerank_score", 0.0))),
                -int(candidate.get("_family_gate_index", 0)),
            ),
            reverse=True,
        )
        for candidate in candidates:
            candidate.pop("_family_gate_index", None)
        return candidates

    def _finalize_candidates(self, candidates: list[dict], query_text: str = "",
                             expected_books: list[str] | None = None) -> list[dict]:
        attach_candidate_canonical_features(candidates, province=self.province)
        if query_text:
            self._apply_family_gate(query_text, candidates, expected_books=expected_books)
        return candidates

    def search_bm25_only(self, query: str, top_k: int = None) -> list[dict]:
        """仅使用BM25搜索（调试用）"""
        top_k = top_k or config.BM25_TOP_K
        return self._finalize_candidates(self.bm25_engine.search(query, top_k=top_k), query_text=query)

    def search_vector_only(self, query: str, top_k: int = None) -> list[dict]:
        """仅使用向量搜索（调试用）"""
        top_k = top_k or config.VECTOR_TOP_K
        return self._finalize_candidates(self.vector_engine.search(query, top_k=top_k), query_text=query)

    def get_status(self) -> dict:
        """获取搜索引擎状态"""
        status = {
            "province": self.province,
            "bm25_ready": False,
            "vector_ready": False,
            "bm25_count": 0,
            "vector_count": 0,
        }

        try:
            self.bm25_engine.ensure_index()
            status["bm25_ready"] = self.bm25_engine.bm25 is not None
            status["bm25_count"] = len(self.bm25_engine.quota_ids) if self.bm25_engine.bm25 else 0
        except Exception as e:
            logger.debug(f"HybridSearcher状态检查-BM25失败: {e}")

        try:
            status["vector_count"] = self.vector_engine.get_index_count()
            status["vector_ready"] = status["vector_count"] > 0
        except Exception as e:
            logger.debug(f"HybridSearcher状态检查-向量引擎失败: {e}")

        return status


# ================================================================
# 命令行入口：测试混合搜索
# ================================================================

if __name__ == "__main__":
    searcher = HybridSearcher()

    # 检查引擎状态
    status = searcher.get_status()
    logger.info(f"搜索引擎状态: BM25={status['bm25_count']}条, 向量={status['vector_count']}条")

    # 测试搜索
    test_queries = [
        "镀锌钢管DN150沟槽连接管道安装",
        "干式变压器800kva安装",
        "电力电缆截面185敷设",
        "柔性防水套管DN125制作",
        "离心泵消防泵安装",
    ]

    for query in test_queries:
        results = searcher.search(query, top_k=5)
        logger.info(f"\n混合搜索: '{query}'")
        for r in results:
            bm25_info = f"BM25#{r['bm25_rank']}" if r.get('bm25_rank') else "BM25:无"
            vec_info = f"向量#{r['vector_rank']}" if r.get('vector_rank') else "向量:无"
            logger.info(
                f"  [{r['hybrid_score']:.6f}] {r['quota_id']} | "
                f"{r['name'][:50]} | {r['unit']} | "
                f"{bm25_info} {vec_info}"
            )
