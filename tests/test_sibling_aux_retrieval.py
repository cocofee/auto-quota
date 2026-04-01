# -*- coding: utf-8 -*-

import sys
import types

import config
from src import match_core, match_engine
from src.param_validator import ParamValidator


def test_get_sibling_provinces_groups_same_region_year(monkeypatch):
    provinces = [
        "上海市园林工程预算定额(2016)",
        "上海市安装工程预算定额(2016)",
        "上海市市政工程预算定额(2016)",
        "上海市建筑和装饰工程预算定额(2016)",
        "福建省通用安装工程预算定额(2017)",
    ]
    monkeypatch.setattr(config, "list_db_provinces", lambda: provinces)

    siblings = config.get_sibling_provinces("上海市园林工程预算定额(2016)")

    assert siblings == [
        "上海市安装工程预算定额(2016)",
        "上海市市政工程预算定额(2016)",
        "上海市建筑和装饰工程预算定额(2016)",
    ]


def test_init_search_components_auto_mounts_sibling_provinces(monkeypatch):
    created = []
    injected = []

    class FakeSearcher:
        def __init__(self, province):
            self.province = province
            self.aux_searchers = []
            created.append(province)

        def get_status(self):
            return {"bm25_ready": True, "bm25_count": 10, "vector_count": 0}

        def set_experience_db(self, experience_db):
            injected.append((self.province, experience_db))
            for aux in self.aux_searchers:
                aux.set_experience_db(experience_db)

    class FakeValidator:
        pass

    fake_model_cache = types.SimpleNamespace(
        ModelCache=types.SimpleNamespace(preload_all=lambda: None)
    )

    monkeypatch.setattr(match_engine, "HybridSearcher", FakeSearcher)
    monkeypatch.setattr(match_engine, "ParamValidator", FakeValidator)
    monkeypatch.setattr(
        config,
        "get_sibling_provinces",
        lambda province: ["上海市安装工程预算定额(2016)", "上海市市政工程预算定额(2016)"],
    )
    monkeypatch.setitem(sys.modules, "src.model_cache", fake_model_cache)

    searcher, validator = match_engine.init_search_components("上海市园林工程预算定额(2016)")

    assert isinstance(validator, FakeValidator)
    assert created == [
        "上海市园林工程预算定额(2016)",
        "上海市安装工程预算定额(2016)",
        "上海市市政工程预算定额(2016)",
    ]
    assert [aux.province for aux in searcher.aux_searchers] == [
        "上海市安装工程预算定额(2016)",
        "上海市市政工程预算定额(2016)",
    ]
    searcher.set_experience_db("EXP")
    assert injected == [
        ("上海市园林工程预算定额(2016)", "EXP"),
        ("上海市安装工程预算定额(2016)", "EXP"),
        ("上海市市政工程预算定额(2016)", "EXP"),
    ]


def test_cascade_search_aux_merges_target_classified_books_for_nonstandard_library():
    aux_calls = []

    class FakeAux:
        province = "上海市安装工程预算定额(2016)"
        uses_standard_books = False

        class bm25_engine:
            quota_books = {1: "2", 2: "4", 3: "8", 4: "13"}

            @staticmethod
            def classify_to_books(_query, top_k=3):
                return ["4"]

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            aux_calls.append(books)
            return [{"quota_id": "03-4-5-56", "name": "光伏逆变器安装 功率≤1000kW", "hybrid_score": 0.9}]

    class FakeSearcher:
        aux_searchers = [FakeAux()]
        uses_standard_books = False

        class bm25_engine:
            quota_books = {"dummy": "2"}

        def search(self, query, top_k=None, books=None, item=None, context_prior=None):
            return [{"quota_id": "LY6-1-2", "name": "主库候选", "hybrid_score": 0.1}]

    result = match_core.cascade_search(
        FakeSearcher(),
        "组串式逆变器 150KW 光伏场区",
        {
            "primary": "C2",
            "fallbacks": ["C8", "C13"],
            "search_books": ["C2", "C8", "C13"],
            "route_mode": "moderate",
            "allow_cross_book_escape": True,
        },
        top_k=5,
    )

    assert len(aux_calls) == 1
    assert aux_calls[0][0] == "4"
    assert set(aux_calls[0]) == {"2", "4", "8", "13"}
    assert result[0]["quota_id"] == "03-4-5-56"


def test_cross_library_candidate_gets_neutral_book_match_score():
    score = ParamValidator._compute_book_match(
        {
            "quota_id": "03-4-5-56",
            "_source_province": "上海市安装工程预算定额(2016)",
        },
        ["C2", "C8", "C13"],
    )

    assert score == 0.5
