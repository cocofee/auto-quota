from src.candidate_scoring import sort_candidates_with_stage_priority
from src.hybrid_searcher import HybridSearcher
from src.text_parser import TextParser
from src.vector_engine import VectorEngine


def test_material_field_does_not_drift_to_accessory_noise():
    parser = TextParser()
    text = (
        "塑料管 介质:给水 材质、规格:PPR给水管 S5系列 De25 "
        "连接形式:热熔连接 压力试验及吹、洗设计要求:水压试验、冲洗消毒满足设计要求 "
        "配套成品管卡安装"
    )

    result = parser.parse(text)
    canonical = parser.parse_canonical(text, specialty="C10", params=result)

    assert result["material"] == "PPR"
    assert canonical["material"] == "PPR管"
    assert canonical["canonical_name"] == "PPR管管道"


def test_rrf_fusion_uses_stable_tie_break_for_equal_scores():
    searcher = HybridSearcher.__new__(HybridSearcher)
    bm25_results = [
        {"id": 2, "quota_id": "B", "name": "Beta", "bm25_score": 1.0},
        {"id": 1, "quota_id": "A", "name": "Alpha", "bm25_score": 1.0},
    ]
    vector_results = [
        {"id": 1, "quota_id": "A", "name": "Alpha", "vector_score": 1.0},
        {"id": 2, "quota_id": "B", "name": "Beta", "vector_score": 1.0},
    ]

    ranked = HybridSearcher._rrf_fusion(
        searcher,
        bm25_results,
        vector_results,
        bm25_weight=0.5,
        vector_weight=0.5,
        k=60,
    )

    assert [row["quota_id"] for row in ranked[:2]] == ["A", "B"]


def test_stage_priority_sort_uses_stable_identity_for_equal_scores():
    candidates = [
        {"quota_id": "B", "name": "Beta", "rerank_score": 0.5, "hybrid_score": 0.5},
        {"quota_id": "A", "name": "Alpha", "rerank_score": 0.5, "hybrid_score": 0.5},
    ]

    ranked = sort_candidates_with_stage_priority(candidates)

    assert [row["quota_id"] for row in ranked] == ["A", "B"]


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self._selected_ids = []

    def execute(self, _sql, params):
        self._selected_ids = [int(value) for value in params]
        return self

    def fetchall(self):
        return [self._rows[db_id] for db_id in self._selected_ids if db_id in self._rows]


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        return None


class _FakeCollection:
    def __init__(self, ids, distances, count=100):
        self._ids = ids
        self._distances = distances
        self._count = count
        self.last_n_results = None

    def count(self):
        return self._count

    def query(self, *, query_embeddings, n_results, where=None):
        del query_embeddings, where
        self.last_n_results = n_results
        return {"ids": [self._ids], "distances": [self._distances]}

    def peek(self, limit=10):
        del limit
        return {"metadatas": [{"book": "C10"}]}


class _FakeVectorEngine(VectorEngine):
    def __init__(self, collection, rows):
        self._collection = collection
        self._rows = rows

    @property
    def collection(self):
        return self._collection

    @property
    def model(self):
        return None

    def _connect(self, row_factory=False):
        del row_factory
        return _FakeConn(self._rows)


def test_vector_search_overfetches_and_truncates_ties_stably():
    collection = _FakeCollection(
        ids=["2", "1", "4", "3"],
        distances=[0.1, 0.1, 0.1, 0.1],
    )
    rows = {
        1: {"id": 1, "quota_id": "A", "name": "Alpha", "unit": ""},
        2: {"id": 2, "quota_id": "B", "name": "Beta", "unit": ""},
        3: {"id": 3, "quota_id": "C", "name": "Gamma", "unit": ""},
        4: {"id": 4, "quota_id": "D", "name": "Delta", "unit": ""},
    }
    engine = _FakeVectorEngine(collection, rows)

    ranked = engine.search("test", top_k=2, precomputed_embedding=[0.1, 0.2])

    assert collection.last_n_results > 2
    assert [row["quota_id"] for row in ranked] == ["A", "B"]
