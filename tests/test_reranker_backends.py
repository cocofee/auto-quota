import sys
import types
from pathlib import Path

from src.reranker import Reranker


class _FakeRankedDocument:
    def __init__(self, doc_id, score):
        self.document = type("Document", (), {"doc_id": str(doc_id)})()
        self.score = score


def _reset_reranker_model_cache(monkeypatch):
    from src.model_cache import ModelCache

    monkeypatch.setattr(ModelCache, "_reranker_model", None)
    monkeypatch.setattr(ModelCache, "_reranker_fail_count", 0)
    monkeypatch.setattr(ModelCache, "_reranker_fail_time", 0.0)


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


def test_cross_encoder_backend_prefers_bundled_local_model(monkeypatch):
    import config
    from src.model_cache import ModelCache

    _reset_reranker_model_cache(monkeypatch)
    init_calls = []

    class FakeCrossEncoder:
        def __init__(self, model_name, **kwargs):
            init_calls.append((model_name, dict(kwargs)))

    monkeypatch.setattr(config, "RERANKER_MODEL_NAME", "BAAI/bge-reranker-v2-m3")
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(CrossEncoder=FakeCrossEncoder),
    )

    model = ModelCache.get_reranker_model()
    bundled_path = str(config.PROJECT_ROOT / "models" / "bge-reranker-v2-m3")

    assert isinstance(model, FakeCrossEncoder)
    assert init_calls == [(
        bundled_path,
        {"max_length": 512, "device": "cuda"},
    )]


def test_cross_encoder_backend_rejects_missing_explicit_local_path(monkeypatch):
    import config
    from src.model_cache import ModelCache

    _reset_reranker_model_cache(monkeypatch)
    init_calls = []

    class FakeCrossEncoder:
        def __init__(self, model_name, **kwargs):
            init_calls.append((model_name, dict(kwargs)))

    missing_path = Path.cwd() / "test_artifacts" / "missing-reranker-model"
    monkeypatch.setattr(config, "RERANKER_MODEL_NAME", str(missing_path))
    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        types.SimpleNamespace(CrossEncoder=FakeCrossEncoder),
    )

    model = ModelCache.get_reranker_model()

    assert model is None
    assert init_calls == []


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