from __future__ import annotations

from src.ltr_model_cache import LTRModelCache


def test_ltr_model_cache_reuses_loaded_model_for_same_path(monkeypatch):
    load_calls = []

    monkeypatch.setattr(LTRModelCache, "_model", None)
    monkeypatch.setattr(LTRModelCache, "_model_path", None)
    monkeypatch.setattr(
        LTRModelCache,
        "_load_model",
        classmethod(lambda cls, model_path: load_calls.append(model_path) or {"model_path": model_path}),
    )

    first = LTRModelCache.get_model("data/ltr_model.txt")
    second = LTRModelCache.get_model("data/ltr_model.txt")

    assert first is second
    assert len(load_calls) == 1


def test_ltr_model_cache_reloads_when_path_changes(monkeypatch):
    load_calls = []

    monkeypatch.setattr(LTRModelCache, "_model", None)
    monkeypatch.setattr(LTRModelCache, "_model_path", None)
    monkeypatch.setattr(
        LTRModelCache,
        "_load_model",
        classmethod(lambda cls, model_path: load_calls.append(model_path) or {"model_path": model_path}),
    )

    first = LTRModelCache.get_model("data/ltr_model_a.txt")
    second = LTRModelCache.get_model("data/ltr_model_b.txt")

    assert first != second
    assert len(load_calls) == 2
