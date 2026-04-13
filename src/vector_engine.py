"""
Vector search engine backed by ChromaDB.
"""

import gc
import os
import shutil
import time
from pathlib import Path

from loguru import logger

import config
from db.sqlite import connect as _db_connect


class VectorEngine:
    """BGE vector search engine."""

    _model_unavailable_warned = False
    _model_skip_count = 0

    def __init__(self, province: str = None):
        self.province = province or config.get_current_province()
        self.db_path = config.get_quota_db_path(self.province)
        self.chroma_dir = config.get_chroma_quota_dir(self.province)
        self._model = None
        self._collection = None
        self._chroma_client = None

    @staticmethod
    def _stable_result_identity(candidate: dict) -> tuple[str, str, str]:
        return (
            str(candidate.get("quota_id", "") or "").strip(),
            str(candidate.get("name", "") or "").strip(),
            str(candidate.get("id", "") or "").strip(),
        )

    @classmethod
    def _vector_result_sort_key(cls, candidate: dict) -> tuple:
        return (float(candidate.get("vector_score", 0.0) or 0.0),)

    def _connect(self, row_factory: bool = False):
        return _db_connect(self.db_path, row_factory=row_factory)

    @staticmethod
    def _vector_rebuild_keywords() -> tuple[str, ...]:
        return (
            "_type",
            "schema_str",
            "no such column: collections.schema_str",
            "dimensionality",
            "dimension",
            "mismatch",
            "incompatible",
            "has no attribute",
            "corrupt",
            "invalid",
            "segment",
            "metadata segment",
            "compactor",
            "blob",
            "decoding column",
            "index",
        )

    def _should_rebuild_vector_index(self, exc: Exception | str | None) -> bool:
        message = str(exc or "").strip().lower()
        return any(keyword in message for keyword in self._vector_rebuild_keywords())

    def _heal_vector_index(self, exc: Exception | str | None) -> bool:
        if not self._should_rebuild_vector_index(exc):
            return False
        logger.warning(f"Broken vector index detected, rebuilding from quota DB: {exc}")
        try:
            self.build_index()
            return True
        except Exception as rebuild_exc:
            logger.error(f"Vector index rebuild failed: {rebuild_exc}")
            return False

    @staticmethod
    def _release_chroma_client(client) -> None:
        if client is None:
            return
        try:
            system = getattr(client, "_system", None)
        except Exception:
            system = None
        if system is not None:
            try:
                system.stop()
            except Exception:
                pass
            try:
                system.reset_state()
            except Exception:
                pass
        try:
            client.clear_system_cache()
        except Exception:
            pass

    def _reset_cached_chroma_client(self) -> None:
        from src.model_cache import ModelCache

        path_str = str(self.chroma_dir)
        cached_client = ModelCache._chroma_clients.get(path_str)
        for client in (self._chroma_client, cached_client):
            self._release_chroma_client(client)
        if path_str in ModelCache._chroma_clients:
            del ModelCache._chroma_clients[path_str]
        self._chroma_client = None
        self._collection = None

    def _recreate_collection(self):
        from src.model_cache import ModelCache

        self._reset_cached_chroma_client()
        gc.collect()

        chroma_path = Path(str(self.chroma_dir))
        if chroma_path.exists():
            removed = False
            last_exc = None
            for attempt in range(3):
                try:
                    shutil.rmtree(chroma_path)
                    removed = True
                    break
                except Exception as exc:
                    last_exc = exc
                    gc.collect()
                    time.sleep(0.2 * (attempt + 1))
            if not removed:
                logger.warning(f"Failed to remove broken vector index dir: {last_exc}")
            else:
                logger.info(f"Removed broken vector index dir: {chroma_path}")

        client = ModelCache.get_chroma_client(str(self.chroma_dir))
        collection = client.create_collection(
            name="quotas",
            metadata={
                "hnsw:space": "cosine",
                "vector_model": os.getenv("VECTOR_MODEL_KEY", "bge"),
            },
        )
        self._chroma_client = client
        self._collection = collection
        return collection

    @property
    def model(self):
        if self._model is None:
            from src.model_cache import ModelCache

            self._model = ModelCache.get_vector_model()
        return self._model

    @property
    def collection(self):
        from src.model_cache import ModelCache

        client = ModelCache.get_chroma_client(str(self.chroma_dir))
        needs_refresh = client is not self._chroma_client or self._collection is None
        if needs_refresh:
            try:
                collection = client.get_or_create_collection(
                    name="quotas",
                    metadata={"hnsw:space": "cosine"},
                )
                collection.count()
            except Exception as exc:
                if not self._heal_vector_index(exc):
                    raise
                client = ModelCache.get_chroma_client(str(self.chroma_dir))
                collection = client.get_or_create_collection(
                    name="quotas",
                    metadata={"hnsw:space": "cosine"},
                )

            self._chroma_client = client
            self._collection = collection
        return self._collection

    def build_index(self, batch_size: int = 256):
        logger.info("Start building vector index...")

        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            col_info = {row[1] for row in cursor.execute("PRAGMA table_info(quotas)").fetchall()}
            has_book_col = "book" in col_info
            has_specialty_col = "specialty" in col_info

            select_cols = "id, search_text"
            if has_book_col:
                select_cols += ", book"
            if has_specialty_col:
                select_cols += ", specialty"
            cursor.execute(f"SELECT {select_cols} FROM quotas WHERE search_text IS NOT NULL")
            rows = cursor.fetchall()
        finally:
            conn.close()

        if not rows:
            logger.error("No quota data found in database, import quotas first")
            return

        total = len(rows)
        logger.info(f"Need to vectorize {total} quotas")

        from src.model_cache import ModelCache

        self.chroma_dir.mkdir(parents=True, exist_ok=True)
        self._reset_cached_chroma_client()
        client = ModelCache.get_chroma_client(str(self.chroma_dir))

        rebuild_required = False
        try:
            client.delete_collection("quotas")
        except Exception as exc:
            if self._should_rebuild_vector_index(exc):
                rebuild_required = True
                logger.warning(f"Vector collection metadata mismatch, rebuilding dir: {exc}")
            else:
                logger.debug(f"Skip deleting old vector collection: {exc}")

        if rebuild_required:
            try:
                client.clear_system_cache()
            except Exception:
                pass
            self._recreate_collection()
        else:
            try:
                self._collection = client.create_collection(
                    name="quotas",
                    metadata={
                        "hnsw:space": "cosine",
                        "vector_model": os.getenv("VECTOR_MODEL_KEY", "bge"),
                    },
                )
                self._chroma_client = client
            except Exception as exc:
                if not self._should_rebuild_vector_index(exc):
                    raise
                try:
                    client.clear_system_cache()
                except Exception:
                    pass
                logger.warning(f"Failed to create vector collection, rebuilding dir: {exc}")
                self._recreate_collection()

        for start in range(0, total, batch_size):
            end = min(start + batch_size, total)
            batch_rows = rows[start:end]
            ids = [str(row["id"]) for row in batch_rows]
            texts = [row["search_text"] for row in batch_rows]

            from src.model_profile import encode_documents

            embeddings = encode_documents(
                self.model,
                texts,
                batch_size=batch_size,
                show_progress=False,
            )

            metadatas = []
            for row in batch_rows:
                meta = {}
                meta["book"] = (row["book"] or "") if has_book_col else ""
                meta["specialty"] = (row["specialty"] or "") if has_specialty_col else ""
                metadatas.append(meta)

            self.collection.add(
                ids=ids,
                documents=texts,
                embeddings=embeddings.tolist(),
                metadatas=metadatas,
            )

            logger.info(f"Vector build progress: {end}/{total} ({end * 100 // total}%)")

        logger.info(f"Vector index build complete: {total} -> {self.chroma_dir}")

    def encode_queries(self, queries: list[str]) -> list:
        if self.model is None:
            if not VectorEngine._model_unavailable_warned:
                logger.warning(
                    "[VectorEngine] vector model unavailable, all vector searches "
                    "will be skipped for this run"
                )
                VectorEngine._model_unavailable_warned = True
            VectorEngine._model_skip_count += 1
            return [None] * len(queries)

        from src.model_profile import encode_queries

        return encode_queries(self.model, queries)

    def search(
        self,
        query: str,
        top_k: int = None,
        books: list[str] = None,
        specialty: str = None,
        precomputed_embedding=None,
    ) -> list[dict]:
        top_k = top_k or config.VECTOR_TOP_K
        try:
            collection_size = self.collection.count()
        except Exception as exc:
            if not self._heal_vector_index(exc):
                raise
            collection_size = self.collection.count()
        fetch_k = min(
            max(int(top_k), max(int(top_k) * 10, int(top_k) + 32, 64)),
            max(collection_size, int(top_k)),
        )

        if collection_size == 0:
            logger.debug("Vector index empty, skip vector search")
            return []

        if precomputed_embedding is not None:
            import numpy as np

            query_embedding = np.array(precomputed_embedding).reshape(1, -1)
        elif self.model is not None:
            from src.model_profile import encode_queries

            query_embedding = encode_queries(self.model, [query])
        else:
            VectorEngine._model_skip_count += 1
            return []

        where_filter = None
        conditions = []
        if specialty:
            conditions.append({"specialty": specialty})
        if books:
            books_with_empty = list(books) + [""]
            if len(books_with_empty) == 1:
                conditions.append({"book": books_with_empty[0]})
            else:
                conditions.append({"book": {"$in": books_with_empty}})

        if len(conditions) == 1:
            where_filter = conditions[0]
        elif len(conditions) > 1:
            where_filter = {"$and": conditions}

        def _query_collection(active_filter):
            return self.collection.query(
                query_embeddings=query_embedding.tolist(),
                n_results=fetch_k,
                where=active_filter,
            )

        try:
            results = _query_collection(where_filter)
        except Exception as exc:
            if self._heal_vector_index(exc):
                results = _query_collection(where_filter)
            elif where_filter:
                logger.warning(f"Vector search with filter failed, fallback to full index: {exc}")
                results = _query_collection(None)
            else:
                raise

        if where_filter and (not results or not results.get("ids") or not results.get("ids")[0]):
            try:
                sample = self.collection.peek(limit=10)
                sample_metas = sample.get("metadatas", []) if sample else []
                valid_book_count = sum(1 for meta in sample_metas if meta and meta.get("book", "").strip())
                is_old_index = valid_book_count == 0
            except Exception as exc:
                logger.debug(f"Vector metadata sampling failed, assume old index: {exc}")
                is_old_index = True

            if is_old_index:
                logger.warning("Old vector index missing book metadata, fallback to full index")
                results = _query_collection(None)
            else:
                logger.info(f"Vector search returned no match after book filter: {books}")

        if not results or not results.get("ids") or not results.get("ids")[0]:
            return []

        matched_ids = results.get("ids", [[]])[0]
        raw_distances = results["distances"][0] if results.get("distances") else []

        if len(raw_distances) != len(matched_ids):
            logger.warning(
                "Vector search returned mismatched ids/distances lengths: "
                f"ids={len(matched_ids)}, distances={len(raw_distances)}"
            )
        distances = list(raw_distances[: len(matched_ids)])
        if len(distances) < len(matched_ids):
            distances.extend([1.0] * (len(matched_ids) - len(distances)))

        scores = [max(0.0, min(1.0, 1 - distance)) for distance in distances]

        db_ids = []
        score_map = {}
        for matched_id, score in zip(matched_ids, scores):
            try:
                db_id = int(matched_id)
            except (TypeError, ValueError):
                logger.warning(f"Skip invalid vector result id: {matched_id!r}")
                continue
            db_ids.append(db_id)
            score_map[db_id] = score

        if not db_ids:
            return []

        conn = self._connect(row_factory=True)
        try:
            cursor = conn.cursor()
            placeholders = ",".join(["?"] * len(db_ids))
            cursor.execute(f"SELECT * FROM quotas WHERE id IN ({placeholders})", db_ids)
            rows = {row["id"]: dict(row) for row in cursor.fetchall()}
        finally:
            conn.close()

        merged = []
        for db_id in db_ids:
            if db_id in rows:
                result = rows[db_id]
                result["vector_score"] = score_map[db_id]
                merged.append(result)

        merged.sort(key=self._stable_result_identity)
        merged.sort(key=self._vector_result_sort_key, reverse=True)
        return merged[:top_k]

    def get_index_count(self) -> int:
        try:
            return self.collection.count()
        except Exception as exc:
            logger.debug(f"Vector index count failed, fallback to 0: {exc}")
            return 0


if __name__ == "__main__":
    engine = VectorEngine()
    engine.build_index()
