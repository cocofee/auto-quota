from pathlib import Path
import uuid
import json

from src.candidate_feature_store import CandidateFeatureStore
from src.candidate_canonicalizer import build_candidate_canonical_features
from db.sqlite import connect as _db_connect


def _make_test_db_path() -> Path:
    base = Path("output") / "temp" / "pytest_candidate_store"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{uuid.uuid4().hex}.db"


def test_candidate_feature_store_round_trip():
    db_path = _make_test_db_path()
    store = CandidateFeatureStore(db_path=db_path)
    candidate = {
        "quota_id": "C9-1-1",
        "name": "喷淋管 DN100 丝扣连接",
    }
    features = {
        "entity": "管道",
        "system": "消防",
        "material": "喷淋钢管",
    }

    store.put("测试省", candidate, features)
    loaded = store.get("测试省", candidate)

    assert loaded == features


def test_build_candidate_canonical_features_uses_store_cache():
    db_path = _make_test_db_path()
    store = CandidateFeatureStore(db_path=db_path)
    candidate = {
        "quota_id": "C4-11-1",
        "name": "电缆桥架支架制作安装",
    }
    cached = {
        "entity": "桥架",
        "system": "电气",
        "canonical_name": "桥架",
    }
    store.put("缓存省", candidate, cached)

    from src import candidate_feature_store as store_module
    old_store = store_module._STORE
    store_module._STORE = store
    try:
        loaded = build_candidate_canonical_features(candidate, province="缓存省")
    finally:
        store_module._STORE = old_store

    assert loaded["entity"] == "桥架"
    assert loaded["system"] == "电气"


def test_candidate_feature_store_ignores_legacy_cache_keys():
    db_path = _make_test_db_path()
    store = CandidateFeatureStore(db_path=db_path)
    candidate = {
        "quota_id": "C4-12-177",
        "name": "波纹电线管敷设 内径(mm) ≤32",
    }
    conn = _db_connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO candidate_features (
                province, cache_key, quota_id, name, description,
                canonical_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "测试省",
                "quota:C4-12-177",
                candidate["quota_id"],
                candidate["name"],
                "",
                json.dumps({"entity": "电缆", "family": "cable_family"}, ensure_ascii=False),
                0.0,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert store.get("测试省", candidate) is None
