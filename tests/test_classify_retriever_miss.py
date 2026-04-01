# -*- coding: utf-8 -*-

from tools.classify_retriever_miss import (
    RetrieverEvidence,
    classify_retriever_record,
    extract_search_books,
    normalize_book_code,
    quota_id_to_book,
)


def test_normalize_book_code_and_quota_id_to_book():
    assert normalize_book_code("C03") == "C3"
    assert normalize_book_code("03") == "C3"
    assert normalize_book_code("A") == "A"
    assert quota_id_to_book("03-4-5-56") == "C3"
    assert quota_id_to_book("C10-6-30") == "C10"
    assert quota_id_to_book("A10-5-99") == "A10"
    assert quota_id_to_book("46074") == ""


def test_classify_retriever_record_prioritizes_index_miss():
    record = {
        "sample_id": "exp:1",
        "province": "测试省份",
        "bill_name": "样本",
        "search_query": "样本 query",
        "oracle_quota_ids": ["03-1-1-1"],
        "oracle_quota_names": ["正确定额"],
        "retriever": {"authority_hit": False, "kb_hit": False},
        "router": {"primary_book": "C10"},
    }
    evidence = RetrieverEvidence(
        oracle_present_in_quota_db=False,
        oracle_books=["C3"],
        search_books=["C3"],
        authority_hit=False,
        kb_hit=False,
        experience_exact_hit=True,
        universal_kb_exact_hit=True,
    )

    result = classify_retriever_record(record, evidence)

    assert result["primary_bucket"] == "index_miss"
    assert result["scope_miss"] is False
    assert result["asset_not_used"] is True


def test_classify_retriever_record_prioritizes_routing_before_asset_overlap():
    record = {
        "sample_id": "exp:2",
        "province": "测试省份",
        "bill_name": "样本",
        "search_query": "样本 query",
        "oracle_quota_ids": ["03-1-1-1"],
        "oracle_quota_names": ["正确定额"],
        "retriever": {"authority_hit": False, "kb_hit": False},
        "router": {"primary_book": "C10"},
    }
    evidence = RetrieverEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        search_books=["C10"],
        authority_hit=False,
        kb_hit=False,
        experience_exact_hit=True,
        universal_kb_exact_hit=False,
    )

    result = classify_retriever_record(record, evidence)

    assert result["primary_bucket"] == "routing_miss"
    assert result["scope_miss"] is True


def test_classify_retriever_record_marks_knowledge_not_used_when_scope_ok():
    record = {
        "sample_id": "exp:3",
        "province": "测试省份",
        "bill_name": "样本",
        "search_query": "样本 query",
        "oracle_quota_ids": ["03-1-1-1"],
        "oracle_quota_names": ["正确定额"],
        "retriever": {"authority_hit": False, "kb_hit": False},
        "router": {"primary_book": "C3"},
    }
    evidence = RetrieverEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        search_books=["C3", "C10"],
        authority_hit=False,
        kb_hit=False,
        experience_exact_hit=True,
        universal_kb_exact_hit=False,
    )

    result = classify_retriever_record(record, evidence)

    assert result["primary_bucket"] == "knowledge_not_used"
    assert result["asset_not_used"] is True


def test_classify_retriever_record_falls_back_to_keyword_miss():
    record = {
        "sample_id": "exp:4",
        "province": "测试省份",
        "bill_name": "样本",
        "search_query": "样本 query",
        "oracle_quota_ids": ["03-1-1-1"],
        "oracle_quota_names": ["正确定额"],
        "retriever": {"authority_hit": False, "kb_hit": False},
        "router": {"primary_book": "C3"},
    }
    evidence = RetrieverEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        search_books=["C3"],
        authority_hit=False,
        kb_hit=False,
        experience_exact_hit=False,
        universal_kb_exact_hit=False,
    )

    result = classify_retriever_record(record, evidence)

    assert result["primary_bucket"] == "keyword_miss"


def test_classify_retriever_record_does_not_treat_empty_search_books_as_routing_miss():
    record = {
        "sample_id": "exp:4b",
        "province": "测试省份",
        "bill_name": "样本",
        "search_query": "样本 query",
        "oracle_quota_ids": ["03-1-1-1"],
        "oracle_quota_names": ["正确定额"],
        "retriever": {"authority_hit": False, "kb_hit": False},
        "router": {},
    }
    evidence = RetrieverEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        search_books=[],
        authority_hit=False,
        kb_hit=False,
        experience_exact_hit=False,
        universal_kb_exact_hit=False,
    )

    result = classify_retriever_record(record, evidence)

    assert result["scope_miss"] is False
    assert result["primary_bucket"] == "keyword_miss"


def test_classify_retriever_record_normalizes_oracle_books_before_scope_check():
    record = {
        "sample_id": "exp:5",
        "province": "测试省份",
        "bill_name": "样本",
        "search_query": "样本 query",
        "oracle_quota_ids": ["03-1-1-1"],
        "oracle_quota_names": ["正确定额"],
        "retriever": {"authority_hit": False, "kb_hit": False},
        "router": {"primary_book": "C3"},
    }
    evidence = RetrieverEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["03"],
        search_books=["C3"],
        authority_hit=False,
        kb_hit=False,
        experience_exact_hit=True,
        universal_kb_exact_hit=False,
    )

    result = classify_retriever_record(record, evidence)

    assert result["scope_miss"] is False
    assert result["primary_bucket"] == "knowledge_not_used"
    assert result["evidence"]["oracle_books"] == ["C3"]


def test_extract_search_books_falls_back_to_router_classification_and_unified_plan():
    record = {
        "router": {
            "classification": {
                "search_books": ["03", "C10"],
            },
            "unified_plan": {
                "hard_books": ["C12"],
                "preferred_books": ["C10", "C13"],
                "primary_book": "C8",
            },
        }
    }

    books = extract_search_books(record)

    assert books == ["C3", "C10", "C12", "C13", "C8"]
