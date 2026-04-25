from src.match_pipeline import orchestrator


def test_search_mode_candidate_neighbors_clone_same_book_adjacent_ids():
    candidates = [
        {"quota_id": "9-91", "name": "seed waterproof", "unit": "m2", "hybrid_score": 0.8},
        {"quota_id": "9-90", "name": "peer waterproof", "unit": "m2", "hybrid_score": 0.7},
    ]

    merged = orchestrator._merge_existing_candidate_neighbors_for_search_mode(
        {"specialty": "C9"},
        candidates,
        top_k=4,
    )

    neighbor = next(candidate for candidate in merged if candidate["quota_id"] == "9-92")
    assert neighbor["match_source"] == "existing_candidate_neighbor"
    assert neighbor["candidate_neighbor_seed"] == "9-91"
    assert neighbor["knowledge_prior_sources"] == ["candidate_neighbor"]


def test_search_mode_candidate_neighbors_respect_item_book_scope():
    candidates = [
        {"quota_id": "9-91", "name": "seed waterproof", "unit": "m2", "hybrid_score": 0.8},
    ]

    merged = orchestrator._merge_existing_candidate_neighbors_for_search_mode(
        {"specialty": "C6"},
        candidates,
        top_k=4,
    )

    assert [candidate["quota_id"] for candidate in merged] == ["9-91"]
