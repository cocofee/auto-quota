import main as main_mod
from src import runtime_cache
from src.hybrid_searcher import HybridSearcher


class _FakeHybridSearcher:
    def __init__(self, province):
        self.province = province
        self.aux_searchers = []
        self._kb_keyword_cache = {"seen": True}
        self._session_cache = {"cached": True}
        self._kb_keyword_blocked_until = 123.0
        self.reset_calls = 0

    def reset_runtime_state(self, *, include_aux: bool = True) -> None:
        self.reset_calls += 1
        self._session_cache.clear()
        if include_aux:
            for aux in self.aux_searchers:
                aux.reset_runtime_state()


class _FakeExperienceDB:
    def __init__(self, province=None):
        self.province = province


def test_hybrid_searcher_reset_runtime_state_preserves_kb_state():
    searcher = HybridSearcher.__new__(HybridSearcher)
    aux = HybridSearcher.__new__(HybridSearcher)

    searcher._kb_keyword_cache = {"main": ["hint"]}
    searcher._kb_keyword_blocked_until = 9.5
    searcher._session_cache = {"main": ["candidate"]}
    searcher.aux_searchers = [aux]

    aux._kb_keyword_cache = {"aux": ["hint"]}
    aux._kb_keyword_blocked_until = 3.0
    aux._session_cache = {"aux": ["candidate"]}
    aux.aux_searchers = []

    HybridSearcher.reset_runtime_state(searcher)

    assert searcher._kb_keyword_cache == {"main": ["hint"]}
    assert searcher._kb_keyword_blocked_until == 9.5
    assert searcher._session_cache == {}
    assert aux._kb_keyword_cache == {"aux": ["hint"]}
    assert aux._kb_keyword_blocked_until == 3.0
    assert aux._session_cache == {}


def test_runtime_cache_keys_searcher_and_experience_by_province(monkeypatch):
    runtime_cache.clear_runtime_cache()
    monkeypatch.setattr(runtime_cache, "HybridSearcher", _FakeHybridSearcher)
    monkeypatch.setattr(runtime_cache, "ExperienceDB", _FakeExperienceDB)

    searcher_a = runtime_cache.get_search_bundle("PROV_A")
    searcher_a_again = runtime_cache.get_search_bundle("PROV_A")
    searcher_b = runtime_cache.get_search_bundle("PROV_B")
    searcher_with_aux = runtime_cache.get_search_bundle("PROV_A", ["AUX_1"])

    exp_a = runtime_cache.get_experience_db("PROV_A")
    exp_a_again = runtime_cache.get_experience_db("PROV_A")
    exp_b = runtime_cache.get_experience_db("PROV_B")

    assert searcher_a is searcher_a_again
    assert searcher_a is not searcher_b
    assert searcher_a is not searcher_with_aux
    assert exp_a is exp_a_again
    assert exp_a is not exp_b

    runtime_cache.clear_runtime_cache()


def test_run_resets_reused_searcher_runtime_state_each_call(monkeypatch, tmp_path):
    input_path = tmp_path / "input.xlsx"
    input_path.write_bytes(b"placeholder")

    searchers = {}

    def fake_init_search_components(province, aux_provinces):
        searcher = searchers.setdefault(province, _FakeHybridSearcher(province))
        return searcher, object()

    monkeypatch.setattr(main_mod, "_resolve_run_province", lambda province, **_: province)
    monkeypatch.setattr(main_mod, "_log_run_banner", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_mod, "_load_bill_items_for_run", lambda *args, **kwargs: [{"name": "item"}])
    monkeypatch.setattr(main_mod, "init_search_components", fake_init_search_components)
    monkeypatch.setattr(main_mod, "init_experience_db", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main_mod,
        "match_by_mode",
        lambda *args, **kwargs: [{"bill_item": {"name": "item"}, "quotas": [], "confidence": 0}],
    )
    monkeypatch.setattr(
        main_mod,
        "_build_run_stats",
        lambda results, elapsed: {"total": len(results), "elapsed": elapsed},
    )
    monkeypatch.setattr(main_mod, "_log_run_summary", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_mod, "_atomic_write_json", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        main_mod,
        "OutputWriter",
        lambda: type("W", (), {"write_results": lambda self, *a, **k: str(tmp_path / "out.xlsx")})(),
    )

    main_mod.run(str(input_path), province="PROV_A", interactive=False, json_output=False)
    main_mod.run(str(input_path), province="PROV_A", interactive=False, json_output=False)
    main_mod.run(str(input_path), province="PROV_B", interactive=False, json_output=False)

    assert searchers["PROV_A"].reset_calls == 2
    assert searchers["PROV_B"].reset_calls == 1
    assert searchers["PROV_A"]._kb_keyword_cache == {"seen": True}
    assert searchers["PROV_A"]._session_cache == {}
    assert searchers["PROV_A"]._kb_keyword_blocked_until == 123.0
