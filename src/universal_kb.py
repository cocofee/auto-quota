"""
通用知识库模块（Universal Knowledge Base）
功能：
1. 存储"清单描述→定额名称模式"的全国通用匹配知识
2. 不存定额编号（编号是省份专属的），只存定额名称关键词
3. 匹配时提供搜索提示：告诉搜索引擎"应该找什么类型的定额"
4. 支持两层机制：权威层（用户验证过）+ 候选层（自动导入/系统匹配）

核心思想：
- "清单描述→应该套什么类型的定额"这个知识是全国通用的
- 用一个省的经验，指导所有省份的匹配
- 用户每次纠正都让全国受益

数据结构：
- bill_pattern: 清单描述模式（项目名称+关键特征）
- quota_patterns: 应该匹配的定额名称模式列表（JSON数组）
- param_hints: 参数提示（JSON，如 {"材质": "镀锌钢管", "管径": "DN*"}）
- layer: 数据层级（authority=权威层/candidate=候选层）
"""

import json
import os
import sqlite3
import time
from pathlib import Path

from loguru import logger

from db.sqlite import connect as _db_connect, connect_init as _db_connect_init
import config


class UniversalKB:
    """通用知识库：存储和查询全国通用的清单→定额类型匹配知识"""

    def __init__(self):
        self.db_path = config.get_universal_kb_path()
        self.chroma_dir = config.get_chroma_universal_kb_dir()

        # 向量模型和ChromaDB（延迟加载，避免启动时占显存）
        self._model = None
        self._collection = None
        self._chroma_client = None

        # 确保数据库表存在
        self._init_db()

    def _init_db(self):
        """创建通用知识库SQLite表"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = _db_connect_init(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS knowledge (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                -- 清单侧（输入模式）
                bill_pattern TEXT NOT NULL,          -- 清单描述模式（如"室内给水管道安装 镀锌钢管"）
                bill_keywords TEXT,                  -- 清单关键词（JSON数组，用于快速搜索）

                -- 定额侧（输出模式，不含编号）
                quota_patterns TEXT NOT NULL,         -- 应匹配的定额名称模式列表（JSON数组）
                associated_patterns TEXT,             -- 关联定额名称模式（如试压、冲洗，JSON数组）
                param_hints TEXT,                     -- 参数提示（JSON，如{"材质":"镀锌钢管","管径":"DN*"}）

                -- 两层机制
                layer TEXT DEFAULT 'candidate',       -- authority=权威层 / candidate=候选层

                -- 专业分类
                specialty TEXT,                        -- 所属专业册号（如"C10"），用于按专业过滤

                -- 验证信息
                confidence INTEGER DEFAULT 50,        -- 置信度（0-100）
                confirm_count INTEGER DEFAULT 0,      -- 被验证次数
                province_list TEXT DEFAULT '[]',      -- 验证过的省份列表（JSON数组）
                source_province TEXT,                 -- 最初来源省份
                source_project TEXT,                  -- 最初来源项目

                -- 时间戳
                created_at REAL,
                updated_at REAL
            )
        """)

        # 兼容旧数据库：如果表已存在但缺少 specialty 列，自动加上
        try:
            cursor.execute("SELECT specialty FROM knowledge LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute("ALTER TABLE knowledge ADD COLUMN specialty TEXT")
            logger.info("通用知识库已升级：新增 specialty 字段")

        # 索引：加速关键词搜索（必须在 ALTER TABLE 之后，确保列存在）
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_kb_bill_pattern
            ON knowledge(bill_pattern)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_kb_layer
            ON knowledge(layer)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_kb_specialty
            ON knowledge(specialty)
        """)

        conn.commit()
        conn.close()
        logger.debug(f"通用知识库已初始化: {self.db_path}")

    @staticmethod
    def _safe_json_list(raw):
        """安全解析JSON数组，异常时返回空列表。"""
        if not raw:
            return []
        try:
            value = json.loads(raw)
            return value if isinstance(value, list) else []
        except Exception:
            return []

    @staticmethod
    def _safe_json_dict(raw):
        """安全解析JSON对象，异常时返回空字典。"""
        if not raw:
            return {}
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _connect(self, row_factory: bool = False):
        """统一SQLite连接参数"""
        return _db_connect(self.db_path, row_factory=row_factory)

    @property
    def model(self):
        """从全局 ModelCache 获取向量模型（与定额搜索、经验库共用同一个BGE模型）"""
        if self._model is None:
            from src.model_cache import ModelCache
            self._model = ModelCache.get_vector_model()
        return self._model

    @property
    def collection(self):
        """延迟初始化ChromaDB collection（通过全局ModelCache获取客户端，避免级联崩溃）

        自动修复：ChromaDB升级后旧索引格式不兼容时（如dimensionality错误），
        自动删除旧索引并从SQLite重建，用户无感。
        """
        from src.model_cache import ModelCache
        import config
        client = ModelCache.get_chroma_client(str(self.chroma_dir))
        # 客户端变了（被重建过），需要刷新collection
        if client is not self._chroma_client:
            self._chroma_client = client
            self._collection = client.get_or_create_collection(
                name="universal_kb",
                metadata={"hnsw:space": "cosine"}
            )
            # 健康探测：检测旧索引是否与当前ChromaDB版本兼容
            try:
                self._collection.count()
            except (AttributeError, Exception) as probe_err:
                if "dimensionality" in str(probe_err) or "has no attribute" in str(probe_err):
                    logger.warning(f"通用知识库向量索引格式不兼容（{probe_err}），自动重建...")
                    self._collection = self._auto_rebuild_collection(client)
                else:
                    raise
            # 校验向量模型版本一致性（模型变更后旧索引不可信）
            try:
                from src.model_profile import get_active_profile
                active_profile = get_active_profile()
                current_model_key = active_profile.key
                current_model_name = active_profile.model_name
                meta = self._collection.metadata or {}
                stored_model = meta.get("vector_model")
                current_aliases = {
                    current_model_key,
                    current_model_name,
                    getattr(config, "VECTOR_MODEL_NAME", current_model_name),
                }
                if stored_model and stored_model not in current_aliases:
                    logger.warning(
                        f"[universal_kb] 向量模型版本不一致！"
                        f"索引使用: {stored_model}, 当前激活: {current_model_key} ({current_model_name})。"
                        f"搜索质量可能下降，建议重建索引。"
                    )
                elif not stored_model and self._collection.count() > 0:
                    logger.info(
                        f"[universal_kb] 索引未记录模型版本，当前使用: {current_model_key} ({current_model_name})"
                    )
            except Exception:
                pass  # metadata 读取失败不影响正常使用
        return self._collection

    def _auto_rebuild_collection(self, client):
        """ChromaDB索引格式不兼容时，自动删旧索引并从SQLite重建"""
        import shutil
        # 删除旧索引目录
        chroma_path = Path(str(self.chroma_dir))
        if chroma_path.exists():
            shutil.rmtree(chroma_path, ignore_errors=True)
            logger.info(f"已删除旧索引目录: {chroma_path}")

        # 重新创建客户端和collection
        from src.model_cache import ModelCache
        path_str = str(self.chroma_dir)
        if path_str in ModelCache._chroma_clients:
            del ModelCache._chroma_clients[path_str]
        client = ModelCache.get_chroma_client(path_str)
        coll = client.get_or_create_collection(
            name="universal_kb",
            metadata={"hnsw:space": "cosine"}
        )
        self._chroma_client = client
        self._collection = coll

        # 从SQLite重建向量索引（后台执行，不阻塞当前请求）
        import threading
        def _rebuild_in_background():
            try:
                self.rebuild_vector_index()
                logger.info("通用知识库向量索引自动重建完成")
            except Exception as e:
                logger.error(f"通用知识库向量索引自动重建失败: {e}")
        threading.Thread(target=_rebuild_in_background, daemon=True).start()
        return coll

    # ================================================================
    # 写入知识
    # ================================================================

    def add_knowledge(self, bill_pattern: str,
                      quota_patterns: list[str],
                      associated_patterns: list[str] = None,
                      param_hints: dict = None,
                      bill_keywords: list[str] = None,
                      layer: str = "candidate",
                      confidence: int = 50,
                      source_province: str = None,
                      source_project: str = None,
                      specialty: str = None) -> int:
        """
        添加一条通用知识

        参数:
            bill_pattern: 清单描述模式（如"室内给水管道安装 镀锌钢管 DN25"）
            quota_patterns: 应匹配的定额名称模式列表（如["管道安装 镀锌钢管 DN25 丝接"]）
            associated_patterns: 关联定额名称模式（如["管卡安装", "水压试验"]）
            param_hints: 参数提示（如{"材质": "镀锌钢管", "管径": "DN25"}）
            bill_keywords: 清单关键词列表（如["给水", "管道", "镀锌钢管"]）
            layer: 数据层级（"authority" 或 "candidate"）
            confidence: 置信度
            source_province: 来源省份
            source_project: 来源项目
            specialty: 所属专业册号（如"C10"），由specialty_classifier判断

        返回:
            新记录的ID
        """
        now = time.time()

        # 第1步：精确文本匹配（最快）
        existing = self._find_exact(bill_pattern)
        if existing:
            return self._update_knowledge(
                existing["id"], quota_patterns, associated_patterns,
                param_hints, layer, confidence, source_province
            )

        # 第2步：语义相似度去重（>95%视为同一条知识）
        similar = self._find_similar(bill_pattern, threshold=0.95)
        if similar:
            # 合并定额模式：把新的quota_patterns并入已有记录（去重）
            merged_quotas = self._merge_patterns(
                self._safe_json_list(similar.get("quota_patterns")),
                quota_patterns
            )
            return self._update_knowledge(
                similar["id"], merged_quotas, associated_patterns,
                param_hints, layer, confidence, source_province
            )

        # 新建记录
        province_list = [source_province] if source_province else []

        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO knowledge
                (bill_pattern, bill_keywords, quota_patterns, associated_patterns,
                 param_hints, layer, confidence, confirm_count, province_list,
                 source_province, source_project, specialty, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bill_pattern,
                json.dumps(bill_keywords or [], ensure_ascii=False),
                json.dumps(quota_patterns, ensure_ascii=False),
                json.dumps(associated_patterns or [], ensure_ascii=False),
                json.dumps(param_hints or {}, ensure_ascii=False),
                layer,
                confidence,
                1 if layer == "authority" else 0,  # 权威层初始确认1次，候选层0次
                json.dumps(province_list, ensure_ascii=False),
                source_province,
                source_project,
                specialty,
                now, now,
            ))
            record_id = cursor.lastrowid
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        # 添加到向量索引
        self._add_to_vector_index(record_id, bill_pattern)

        logger.debug(f"通用知识库新增: [{layer}] '{bill_pattern[:50]}' → {len(quota_patterns)}条定额模式")
        return record_id

    def _update_knowledge(self, record_id: int,
                          quota_patterns: list[str],
                          associated_patterns: list[str],
                          param_hints: dict,
                          layer: str,
                          confidence: int,
                          source_province: str) -> int:
        """更新已有的知识记录"""
        now = time.time()
        conn = self._connect()
        conn.row_factory = sqlite3.Row

        try:
            cursor = conn.cursor()
            # 读取当前记录
            cursor.execute("SELECT * FROM knowledge WHERE id = ?", (record_id,))
            current_row = cursor.fetchone()
            if current_row is None:
                raise ValueError(f"知识记录不存在: id={record_id}")
            current = dict(current_row)

            # 更新省份列表（去重）
            province_list = self._safe_json_list(current.get("province_list"))
            if source_province and source_province not in province_list:
                province_list.append(source_province)

            # 层级晋升：candidate可以升为authority，但authority不降级
            new_layer = current["layer"]
            if layer == "authority":
                new_layer = "authority"

            # 置信度：取较高值
            new_confidence = max(current["confidence"], confidence)

            # 如果是权威层数据，更新定额模式
            if layer == "authority":
                cursor.execute("""
                    UPDATE knowledge SET
                        quota_patterns = ?,
                        associated_patterns = ?,
                        param_hints = ?,
                        layer = ?,
                        confidence = ?,
                        confirm_count = confirm_count + 1,
                        province_list = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    json.dumps(quota_patterns, ensure_ascii=False),
                    json.dumps(associated_patterns or [], ensure_ascii=False),
                    json.dumps(param_hints or {}, ensure_ascii=False),
                    new_layer,
                    min(new_confidence + 5, 100),
                    json.dumps(province_list, ensure_ascii=False),
                    now, record_id,
                ))
            else:
                # 候选层数据只更新省份列表和时间戳，不涨分
                cursor.execute("""
                    UPDATE knowledge SET
                        province_list = ?,
                        updated_at = ?
                    WHERE id = ?
                """, (
                    json.dumps(province_list, ensure_ascii=False),
                    now, record_id,
                ))

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return record_id

    def _find_exact(self, bill_pattern: str) -> dict:
        """精确查找相同清单模式的知识记录"""
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM knowledge WHERE bill_pattern = ? LIMIT 1",
                (bill_pattern,)
            )
            row = cursor.fetchone()
        finally:
            conn.close()
        return dict(row) if row else None

    def _find_similar(self, bill_pattern: str, threshold: float = 0.95) -> dict:
        """
        语义相似度去重：查找向量相似度超过阈值的已有记录

        参数:
            bill_pattern: 清单描述模式
            threshold: 相似度阈值（默认0.95，非常相似才合并）

        返回:
            最相似的记录dict，如果没有超过阈值的则返回None
        """
        try:
            if self.collection.count() == 0:
                return None

            # 向量模型不可用时快速跳过（通用知识库依赖向量，无模型则直接返回）
            if self.model is None:
                return None

            from src.model_profile import encode_queries
            query_embedding = encode_queries(self.model, [bill_pattern])

            results = self.collection.query(
                query_embeddings=query_embedding.tolist(),
                n_results=1,
            )

            if not results or not results["ids"][0]:
                return None

            # ChromaDB返回的是cosine距离，转为相似度
            distance = results["distances"][0][0]
            similarity = 1 - distance

            if similarity < threshold:
                return None

            # 从SQLite获取完整记录
            record_id = int(results["ids"][0][0])
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM knowledge WHERE id = ?", (record_id,))
                row = cursor.fetchone()
            finally:
                conn.close()

            if row:
                logger.debug(
                    f"语义去重命中(相似度{similarity:.3f}): "
                    f"'{bill_pattern[:40]}' ≈ '{row['bill_pattern'][:40]}'"
                )
                return dict(row)

        except Exception as e:
            logger.debug(f"语义去重查询失败（不影响导入）: {e}")

        return None

    @staticmethod
    def _merge_patterns(existing: list[str], new: list[str]) -> list[str]:
        """
        合并定额名称模式列表（去重）

        把新的模式并入已有的列表，去掉完全相同的，保留所有独特的模式。
        """
        merged = list(existing)  # 保留原有的
        existing_set = set(existing)
        for p in new:
            if p not in existing_set:
                merged.append(p)
                existing_set.add(p)
        return merged

    def _add_to_vector_index(self, record_id: int, bill_pattern: str):
        """将知识记录添加到向量索引"""
        try:
            from src.model_profile import encode_documents
            embedding = encode_documents(self.model, [bill_pattern])
            self.collection.upsert(
                ids=[str(record_id)],
                documents=[bill_pattern],
                embeddings=embedding.tolist(),
            )
        except Exception as e:
            logger.warning(f"通用知识库向量索引添加失败: {e}")

    # ================================================================
    # 查询知识（匹配时调用）
    # ================================================================

    def search_hints(self, query_text: str, top_k: int = 3,
                     authority_only: bool = True) -> list[dict]:
        """
        搜索匹配提示：输入清单描述，返回"应该找什么类型的定额"

        这是匹配流程中调用的核心方法：
        1. 先精确匹配清单文本
        2. 再向量相似搜索
        3. 返回定额名称模式，用于增强搜索词

        参数:
            query_text: 清单描述文本
            top_k: 返回前K条
            authority_only: 是否只查权威层（默认True，候选层不参与指导匹配）

        返回:
            [
                {
                    "bill_pattern": "清单模式",
                    "quota_patterns": ["定额名称模式1", "定额名称模式2"],
                    "associated_patterns": ["关联定额模式"],
                    "param_hints": {"材质": "镀锌钢管"},
                    "similarity": 0.95,
                    "confidence": 85,
                    "layer": "authority"
                },
                ...
            ]
        """
        results = []

        # 1. 精确匹配
        exact = self._find_exact(query_text)
        if exact:
            if not authority_only or exact["layer"] == "authority":
                results.append(self._format_result(exact, similarity=1.0))
                if len(results) >= top_k:
                    return results

        # 2. 向量相似搜索
        if self.model is None:
            return results  # 向量模型不可用，只返回精确匹配结果
        if self.collection.count() == 0:
            return results

        try:
            from src.model_profile import encode_queries
            query_embedding = encode_queries(self.model, [query_text])

            search_results = self.collection.query(
                query_embeddings=query_embedding.tolist(),
                n_results=min(top_k * 3, self.collection.count()),
            )

            if not search_results or not search_results.get("ids") or not search_results.get("ids")[0]:
                return results

            # 获取匹配的记录（防御性处理长度不一致/非法ID）
            raw_ids = search_results.get("ids", [[]])[0]
            raw_distances = search_results["distances"][0] if search_results.get("distances") else []
            if len(raw_distances) != len(raw_ids):
                logger.warning(
                    f"通用知识库向量检索返回长度不一致: ids={len(raw_ids)}, "
                    f"distances={len(raw_distances)}，已按最低相似度补齐/截断"
                )
            distances = list(raw_distances[:len(raw_ids)])
            if len(distances) < len(raw_ids):
                distances.extend([1.0] * (len(raw_ids) - len(distances)))

            matched_ids = []
            similarities = []
            for mid, dist in zip(raw_ids, distances):
                try:
                    db_id = int(mid)
                except (TypeError, ValueError):
                    logger.warning(f"通用知识库向量检索返回非法ID，已跳过: {mid!r}")
                    continue
                matched_ids.append(db_id)
                similarities.append(max(0.0, min(1.0, 1 - dist)))

            if not matched_ids:
                return results

            # 从SQLite获取完整记录
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                placeholders = ",".join(["?"] * len(matched_ids))
                if authority_only:
                    cursor.execute(f"""
                        SELECT * FROM knowledge
                        WHERE id IN ({placeholders}) AND layer = 'authority'
                    """, matched_ids)
                else:
                    cursor.execute(f"""
                        SELECT * FROM knowledge
                        WHERE id IN ({placeholders})
                    """, matched_ids)

                rows = {row["id"]: dict(row) for row in cursor.fetchall()}
            finally:
                conn.close()

            # 组装结果（按相似度排序，去掉已有的精确匹配）
            existing_ids = {r.get("id") for r in results}
            for db_id, sim in zip(matched_ids, similarities):
                if db_id in rows and db_id not in existing_ids:
                    # 相似度太低的不要（<0.7基本没参考价值）
                    if sim < 0.7:
                        continue
                    results.append(self._format_result(rows[db_id], similarity=sim))

            # 按相似度降序
            results.sort(key=lambda x: x["similarity"], reverse=True)

        except Exception as e:
            logger.warning(f"通用知识库向量搜索失败: {e}")

        return results[:top_k]

    def get_search_keywords(self, query_text: str) -> list[str]:
        """
        获取搜索增强关键词（简化接口）

        输入一条清单描述，返回应该用于搜索定额库的额外关键词。
        这是给 hybrid_searcher 用的便捷方法。

        参数:
            query_text: 清单描述

        返回:
            额外的搜索关键词列表，如 ["管道安装", "管卡", "水压试验"]
        """
        hints = self.search_hints(query_text, top_k=1, authority_only=True)
        if not hints:
            return []

        best = hints[0]
        keywords = []

        # 从定额名称模式中提取关键词
        for pattern in best.get("quota_patterns", []):
            keywords.append(pattern)

        # 从关联定额模式中提取
        for pattern in best.get("associated_patterns", []):
            keywords.append(pattern)

        return keywords

    def _format_result(self, record: dict, similarity: float) -> dict:
        """格式化查询结果"""
        return {
            "id": record["id"],
            "bill_pattern": record["bill_pattern"],
            "quota_patterns": self._safe_json_list(record.get("quota_patterns")),
            "associated_patterns": self._safe_json_list(record.get("associated_patterns")),
            "param_hints": self._safe_json_dict(record.get("param_hints")),
            "similarity": similarity,
            "confidence": record.get("confidence", 0),
            "confirm_count": record.get("confirm_count", 0),
            "layer": record.get("layer", "candidate"),
            "province_list": self._safe_json_list(record.get("province_list")),
        }

    # ================================================================
    # 从用户纠正中学习
    # ================================================================

    def learn_from_correction(self, bill_text: str,
                              quota_names: list[str],
                              associated_names: list[str] = None,
                              param_hints: dict = None,
                              province: str = None):
        """
        从用户纠正中学习，更新通用知识

        当用户在任意省份纠正了一条匹配时，调用此方法：
        - 如果已有对应知识 → 更新为权威层，涨分
        - 如果没有 → 新建一条权威层知识

        参数:
            bill_text: 清单描述
            quota_names: 正确的定额名称列表（不含编号，如["管道安装 镀锌钢管 DN25"]）
            associated_names: 关联定额名称列表
            param_hints: 参数提示
            province: 纠正发生的省份
        """
        self.add_knowledge(
            bill_pattern=bill_text,
            quota_patterns=quota_names,
            associated_patterns=associated_names,
            param_hints=param_hints,
            layer="authority",
            confidence=85,
            source_province=province or config.get_current_province(),
        )
        logger.info(f"通用知识库学习: '{bill_text[:50]}' → {len(quota_names)}条定额模式 [权威层]")

    # ================================================================
    # 批量导入（从造价Home等外部数据）
    # ================================================================

    def batch_import(self, records: list[dict],
                     source_province: str = None,
                     source_project: str = None,
                     skip_vector_dedup: bool = False) -> dict:
        """
        批量导入知识（人工验证过的预算数据，直接进权威层）

        参数:
            records: 知识记录列表，每条包含：
                {
                    "bill_pattern": "清单描述模式",
                    "quota_patterns": ["定额名称模式1", "定额名称模式2"],
                    "associated_patterns": ["关联定额模式"],  # 可选
                    "param_hints": {},                        # 可选
                }
            source_province: 来源省份
            source_project: 来源项目
            skip_vector_dedup: 跳过向量语义去重（大批量导入时用，只做精确文本去重，
                              导入完后统一rebuild_vector_index再处理语义去重）

        返回:
            {"total": 总数, "added": 新增数, "merged": 合并数(精确+语义去重), "skipped": 跳过数}
        """
        stats = {"total": len(records), "added": 0, "merged": 0, "skipped": 0}

        for i, record in enumerate(records):
            bill_pattern = record.get("bill_pattern", "").strip()
            quota_patterns = record.get("quota_patterns", [])

            if not bill_pattern or not quota_patterns:
                stats["skipped"] += 1
                continue

            if skip_vector_dedup:
                # 快速模式：只做精确文本去重，跳过向量语义去重和逐条向量写入
                self._batch_import_fast(
                    bill_pattern, quota_patterns, record,
                    source_province, source_project, stats
                )
            else:
                # 原有模式：精确+语义去重
                conn = self._connect()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM knowledge")
                    count_before = cursor.fetchone()[0]
                finally:
                    conn.close()

                self.add_knowledge(
                    bill_pattern=bill_pattern,
                    quota_patterns=quota_patterns,
                    associated_patterns=record.get("associated_patterns"),
                    param_hints=record.get("param_hints"),
                    specialty=record.get("specialty"),
                    layer="authority",
                    confidence=80,
                    source_province=source_province,
                    source_project=source_project,
                )

                conn = self._connect()
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM knowledge")
                    count_after = cursor.fetchone()[0]
                finally:
                    conn.close()

                if count_after > count_before:
                    stats["added"] += 1
                else:
                    stats["merged"] += 1

            # 每500条打印进度
            if (i + 1) % 500 == 0:
                logger.info(f"  知识库导入进度: {i+1}/{len(records)}")

        logger.info(f"通用知识库批量导入: 总{stats['total']}条, "
                    f"新增{stats['added']}, 合并{stats['merged']}(去重), 跳过{stats['skipped']}")
        return stats

    def _batch_import_fast(self, bill_pattern: str, quota_patterns: list[str],
                           record: dict, source_province: str,
                           source_project: str, stats: dict):
        """快速导入：只做精确文本去重，跳过向量语义去重"""
        import json as _json
        now = time.time()

        # 精确匹配
        existing = self._find_exact(bill_pattern)
        if existing:
            # 合并定额模式
            merged_quotas = self._merge_patterns(
                self._safe_json_list(existing.get("quota_patterns")),
                quota_patterns
            )
            self._update_knowledge(
                existing["id"], merged_quotas,
                record.get("associated_patterns"),
                record.get("param_hints"),
                "authority", 80, source_province
            )
            stats["merged"] += 1
            return

        # 新建（不做向量去重，不写向量索引）
        province_list = [source_province] if source_province else []
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO knowledge
                (bill_pattern, bill_keywords, quota_patterns, associated_patterns,
                 param_hints, layer, confidence, confirm_count, province_list,
                 source_province, source_project, specialty, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                bill_pattern,
                _json.dumps([], ensure_ascii=False),
                _json.dumps(quota_patterns, ensure_ascii=False),
                _json.dumps(record.get("associated_patterns") or [], ensure_ascii=False),
                _json.dumps(record.get("param_hints") or {}, ensure_ascii=False),
                "authority", 80, 1,
                _json.dumps(province_list, ensure_ascii=False),
                source_province, source_project,
                record.get("specialty"),
                now, now,
            ))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        stats["added"] += 1

    # ================================================================
    # 向量索引重建
    # ================================================================

    def rebuild_vector_index(self):
        """重建向量索引"""
        logger.info("重建通用知识库向量索引...")

        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT id, bill_pattern FROM knowledge")
            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            logger.info("通用知识库为空，无需重建")
            return

        # 清空旧索引
        import chromadb
        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._chroma_client = chromadb.PersistentClient(path=str(self.chroma_dir))
        try:
            self._chroma_client.delete_collection("universal_kb")
        except Exception as e:
            logger.debug(f"通用知识库旧向量集合删除跳过: {e}")
        self._collection = self._chroma_client.create_collection(
            name="universal_kb",
            metadata={
                "hnsw:space": "cosine",
                "vector_model": os.getenv("VECTOR_MODEL_KEY", "bge"),
            }
        )

        # 批量向量化
        batch_size = 256
        total = len(rows)
        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch = rows[start:end]

            ids = [str(row["id"]) for row in batch]
            texts = [row["bill_pattern"] for row in batch]

            from src.model_profile import encode_documents
            embeddings = encode_documents(
                self.model, texts,
                batch_size=batch_size,
                show_progress=False,
            )

            self.collection.add(
                ids=ids,
                documents=texts,
                embeddings=embeddings.tolist(),
            )

        logger.info(f"通用知识库向量索引重建完成: {total}条记录")

    # ================================================================
    # 统计信息
    # ================================================================

    def get_stats(self) -> dict:
        """获取通用知识库统计信息"""
        conn = self._connect()
        try:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM knowledge")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM knowledge WHERE layer = 'authority'")
            authority_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM knowledge WHERE layer = 'candidate'")
            candidate_count = cursor.fetchone()[0]

            cursor.execute("SELECT AVG(confidence) FROM knowledge WHERE layer = 'authority'")
            avg_authority_conf = cursor.fetchone()[0] or 0

            # 涉及的省份数
            cursor.execute("SELECT province_list FROM knowledge")
            all_provinces = set()
            for row in cursor.fetchall():
                try:
                    provinces = self._safe_json_list(row[0])
                    all_provinces.update(provinces)
                except Exception as e:
                    logger.debug(f"通用知识库省份列表解析失败，跳过该记录: {e}")
        finally:
            conn.close()

        # 向量索引数量
        try:
            vector_count = self.collection.count()
        except Exception as e:
            logger.debug(f"通用知识库向量索引计数失败，按0返回: {e}")
            vector_count = 0

        return {
            "total": total,
            "authority": authority_count,
            "candidate": candidate_count,
            "avg_authority_confidence": round(avg_authority_conf, 1),
            "province_count": len(all_provinces),
            "provinces": list(all_provinces),
            "vector_count": vector_count,
        }


# ================================================================
# 命令行入口：查看通用知识库状态
# ================================================================

if __name__ == "__main__":
    kb = UniversalKB()
    stats = kb.get_stats()

    print("=" * 50)
    print("通用知识库状态")
    print("=" * 50)
    print(f"  总记录数: {stats['total']}")
    print(f"  权威层: {stats['authority']}条")
    print(f"  候选层: {stats['candidate']}条")
    print(f"  权威层平均置信度: {stats['avg_authority_confidence']}")
    print(f"  涉及省份: {stats['province_count']}个 {stats['provinces']}")
    print(f"  向量索引: {stats['vector_count']}条")
