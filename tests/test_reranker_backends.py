import sys
import types

from src.reranker import Reranker


class _FakeRankedDocument:
    def __init__(self, doc_id, score):
        self.document = type("Document", (), {"doc_id": str(doc_id)})()
        self.score = score


def test_rerankers_backend_reuses_loaded_model(monkeypatch):
    init_calls = []

    class FakeExternalReranker:
        def __init__(self, model_name, **kwargs):
            init_calls.append((model_name, dict(kwargs)))

        def rank(self, query, docs=None, doc_ids=None):
            ordered = [
                _FakeRankedDocument(doc_id, score)
                for doc_id, score in zip(doc_ids or [], [0.8, 0.2])
            ]
            return types.SimpleNamespace(results=ordered)

    Reranker.clear_backend_caches()
    monkeypatch.setitem(
        sys.modules,
        "rerankers",
        types.SimpleNamespace(Reranker=FakeExternalReranker),
    )

    first = Reranker(model_name="fake-model", backend="rerankers")
    second = Reranker(model_name="fake-model", backend="rerankers")

    assert first.model is second.model
    assert init_calls == [("fake-model", {})]


def test_unknown_backend_marks_candidates_as_failed():
    reranker = Reranker(model_name="fake", backend="missing-backend")
    candidates = [
        {"quota_id": "Q1", "name": "pipe-a"},
        {"quota_id": "Q2", "name": "pipe-b"},
    ]

    ranked = reranker.rerank("pipe-a", candidates, route_profile={"route": "default"})

    assert [candidate["quota_id"] for candidate in ranked] == ["Q1", "Q2"]
    assert all(candidate["reranker_failed"] for candidate in ranked)
    assert all(candidate["reranker_backend"] == "missing-backend" for candidate in ranked)


def test_disabled_backend_marks_candidates_as_failed_without_reordering():
    reranker = Reranker(model_name="fake", backend="disabled")
    candidates = [
        {"quota_id": "Q1", "name": "pipe-a"},
        {"quota_id": "Q2", "name": "pipe-b"},
    ]

    ranked = reranker.rerank("pipe-a", candidates, route_profile={"route": "default"})

    assert [candidate["quota_id"] for candidate in ranked] == ["Q1", "Q2"]
    assert all(candidate["reranker_failed"] for candidate in ranked)
    assert all(candidate["reranker_backend"] == "disabled" for candidate in ranked)
