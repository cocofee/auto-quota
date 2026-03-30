# -*- coding: utf-8 -*-

from tools.classify_wrong_book import WrongBookEvidence, classify_wrong_book_record


def test_classify_wrong_book_record_marks_index_miss_first():
    record = {
        "sample_id": "exp:1",
        "province": "测试省份",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "01-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=False,
        oracle_books=["C3"],
        selected_book="C1",
        search_books=["C3"],
        router_search_books=["C3"],
        resolved_main_books=["C3"],
        candidate_books=["C1"],
        oracle_in_candidates=False,
        used_open_search=False,
        error_stage="retriever",
        miss_stage="recall_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "index_miss"


def test_classify_wrong_book_record_marks_routing_scope_miss():
    record = {
        "sample_id": "exp:2",
        "province": "测试省份",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "01-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        selected_book="C1",
        search_books=["C1", "C10"],
        router_search_books=["C1", "C10"],
        resolved_main_books=["C1", "C10"],
        candidate_books=["C1"],
        oracle_in_candidates=False,
        used_open_search=False,
        error_stage="retriever",
        miss_stage="recall_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "routing_scope_miss"
    assert result["scope_miss"] is True


def test_classify_wrong_book_record_marks_post_rank_wrong_book():
    record = {
        "sample_id": "exp:3",
        "province": "测试省份",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "01-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        selected_book="C1",
        search_books=["C1", "C3"],
        router_search_books=["C1", "C3"],
        resolved_main_books=["C1", "C3"],
        candidate_books=["C1", "C3"],
        oracle_in_candidates=True,
        used_open_search=False,
        error_stage="final_validator",
        miss_stage="post_rank_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "post_rank_wrong_book"


def test_classify_wrong_book_record_marks_rank_wrong_book():
    record = {
        "sample_id": "exp:4",
        "province": "测试省份",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "01-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        selected_book="C1",
        search_books=["C1", "C3"],
        router_search_books=["C1", "C3"],
        resolved_main_books=["C1", "C3"],
        candidate_books=["C1", "C3"],
        oracle_in_candidates=True,
        used_open_search=False,
        error_stage="ranker",
        miss_stage="rank_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "rank_wrong_book"
    assert result["oracle_book_in_candidates"] is True


def test_classify_wrong_book_record_marks_open_search_drift_before_in_scope_recall():
    record = {
        "sample_id": "exp:5",
        "province": "测试省份",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "01-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        selected_book="C1",
        search_books=[],
        router_search_books=[],
        resolved_main_books=[],
        candidate_books=["C1"],
        oracle_in_candidates=False,
        used_open_search=True,
        error_stage="retriever",
        miss_stage="recall_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "open_search_drift"


def test_classify_wrong_book_record_falls_back_to_in_scope_recall_miss():
    record = {
        "sample_id": "exp:6",
        "province": "测试省份",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "01-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        selected_book="C1",
        search_books=["C1", "C3"],
        router_search_books=["C1", "C3"],
        resolved_main_books=["C1", "C3"],
        candidate_books=["C1"],
        oracle_in_candidates=False,
        used_open_search=False,
        error_stage="retriever",
        miss_stage="recall_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "in_scope_recall_miss"
    assert result["secondary_bucket"] == "book_not_materialized"


def test_classify_wrong_book_record_marks_true_out_of_scope_leakage_inside_in_scope_recall():
    record = {
        "sample_id": "exp:7",
        "province": "测试省份",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "01-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        selected_book="C1",
        search_books=["C3", "C10"],
        router_search_books=["C3", "C10"],
        resolved_main_books=["C3", "C10"],
        candidate_books=["C1", "C10"],
        oracle_in_candidates=False,
        used_open_search=False,
        error_stage="retriever",
        miss_stage="recall_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "in_scope_recall_miss"
    assert result["secondary_bucket"] == "true_out_of_scope_leakage"
    assert result["tertiary_bucket"] == "candidate_merge_leakage"


def test_classify_wrong_book_record_marks_borrow_scope_pollution_inside_in_scope_recall():
    record = {
        "sample_id": "exp:7b",
        "province": "娴嬭瘯鐪佷唤",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "08-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        selected_book="C8",
        search_books=["C3"],
        router_search_books=["C3", "C8", "C10"],
        resolved_main_books=["C3"],
        candidate_books=["C8", "C10"],
        oracle_in_candidates=False,
        used_open_search=False,
        error_stage="retriever",
        miss_stage="recall_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "in_scope_recall_miss"
    assert result["secondary_bucket"] == "borrow_scope_pollution"


def test_classify_wrong_book_record_marks_resolved_scope_drift_inside_true_out_of_scope():
    record = {
        "sample_id": "exp:7c",
        "province": "娴嬭瘯鐪佷唤",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "10-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        selected_book="C10",
        search_books=["C10", "C3"],
        router_search_books=["C3", "C4"],
        resolved_main_books=["C10", "C3"],
        candidate_books=["C10", "C4"],
        oracle_in_candidates=False,
        used_open_search=False,
        error_stage="retriever",
        miss_stage="recall_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "in_scope_recall_miss"
    assert result["secondary_bucket"] == "true_out_of_scope_leakage"
    assert result["tertiary_bucket"] == "resolved_scope_drift"


def test_classify_wrong_book_record_marks_book_not_materialized_inside_in_scope_recall():
    record = {
        "sample_id": "exp:8",
        "province": "测试省份",
        "oracle_quota_ids": ["03-1-1-1"],
        "algo_id": "10-1-1-1",
    }
    evidence = WrongBookEvidence(
        oracle_present_in_quota_db=True,
        oracle_books=["C3"],
        selected_book="C10",
        search_books=["C3", "C10"],
        router_search_books=["C3", "C10"],
        resolved_main_books=["C3", "C10"],
        candidate_books=["C10"],
        oracle_in_candidates=False,
        used_open_search=False,
        error_stage="retriever",
        miss_stage="recall_miss",
    )

    result = classify_wrong_book_record(record, evidence)

    assert result["primary_bucket"] == "in_scope_recall_miss"
    assert result["secondary_bucket"] == "book_not_materialized"
