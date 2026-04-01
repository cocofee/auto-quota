# -*- coding: utf-8 -*-

from src.hybrid_searcher import HybridSearcher


def test_nonstandard_book_mapping_expands_broad_a_group():
    mapped = HybridSearcher._normalize_requested_books_for_nonstandard_db(
        ["A"],
        {"A1", "A2", "A14", "C4"},
    )

    assert mapped == ["A1", "A14", "A2"]


def test_nonstandard_book_mapping_drops_to_full_scan_for_numeric_civil_db():
    mapped = HybridSearcher._normalize_requested_books_for_nonstandard_db(
        ["A"],
        {"1", "5", "14"},
    )

    assert mapped is None


def test_nonstandard_book_mapping_keeps_numeric_install_projection():
    mapped = HybridSearcher._normalize_requested_books_for_nonstandard_db(
        ["C4", "C12"],
        {"4", "12", "15"},
    )

    assert mapped == ["4", "12"]


def test_nonstandard_book_mapping_expands_broad_d_group():
    mapped = HybridSearcher._normalize_requested_books_for_nonstandard_db(
        ["D", "A"],
        {"D1", "D2", "D5"},
    )

    assert mapped == ["D1", "D2", "D5"]


def test_nonstandard_book_mapping_matches_zero_padded_numeric_books():
    mapped = HybridSearcher._normalize_requested_books_for_nonstandard_db(
        ["C2", "C8", "C13"],
        {"02", "03", "05", "08", "13"},
    )

    assert mapped == ["02", "08", "13"]


def test_nonstandard_book_mapping_prefers_zero_padded_match_over_full_scan():
    mapped = HybridSearcher._normalize_requested_books_for_nonstandard_db(
        ["C10"],
        {"010", "011", "012"},
    )

    assert mapped == ["010"]
