from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_search_similar_keeps_stale_exact_when_vector_query_empty():
    from src.experience_db import ExperienceDB

    db = ExperienceDB.__new__(ExperienceDB)
    db.province = "测试省份"

    stale_exact = {
        "id": 1,
        "bill_text": "排水塑料管安装 DN100",
        "quota_ids": '["C10-2-1"]',
        "quota_names": '["排水塑料管安装"]',
        "materials": "[]",
        "confidence": 90,
        "quota_db_version": "old-version",
        "layer": "authority",
    }

    mock_collection = MagicMock()
    mock_collection.count.return_value = 10
    # 第一次(按省份)和第二次(全库)都返回空
    mock_collection.query.side_effect = [
        {"ids": [[]], "distances": [[]]},
        {"ids": [[]], "distances": [[]]},
    ]

    mock_embedding = MagicMock()
    mock_embedding.tolist.return_value = [[0.1, 0.2]]
    mock_model = MagicMock()
    mock_model.encode.return_value = mock_embedding

    with patch("src.experience_db.config.get_current_quota_version", return_value="new-version"):
        with patch.object(db, "_find_exact_match", return_value=stale_exact):
            with patch.object(type(db), "collection",
                              new_callable=lambda: property(lambda self: mock_collection)):
                with patch.object(type(db), "model",
                                  new_callable=lambda: property(lambda self: mock_model)):
                    records = db.search_similar("排水塑料管安装 DN100", top_k=3, province="测试省份")

    assert len(records) == 1
    assert records[0]["id"] == 1
    assert records[0]["match_type"] == "stale"
