from src.experience_db import ExperienceDB


def test_cascade_recall_parallel_collects_channels():
    db = ExperienceDB.__new__(ExperienceDB)
    db._recall_exact = lambda *args, **kwargs: [{"id": 1}]
    db._recall_bm25 = lambda *args, **kwargs: [{"id": 2, "bm25_score": 0.8}]
    db._recall_structural = lambda *args, **kwargs: [{"id": 3, "structural_score": 1.0}]
    db._recall_vector_records = lambda *args, **kwargs: [{"id": 4, "vector_score": 0.9, "similarity": 0.9}]

    results = db._cascade_recall_parallel(
        {"normalized_text": "test", "province": "TestProvince"},
        query_text="test",
        layer="authority",
        province_mode="local",
        top_k=5,
        min_confidence=60,
        province="TestProvince",
        include_vector=True,
        timeout=1,
    )

    assert [item["id"] for item in results["exact"]] == [1]
    assert results["exact"][0]["_exact_match"] is True
    assert results["exact"][0]["similarity"] == 1.0
    assert [item["id"] for item in results["bm25"]] == [2]
    assert [item["id"] for item in results["structural"]] == [3]
    assert [item["id"] for item in results["vector"]] == [4]
