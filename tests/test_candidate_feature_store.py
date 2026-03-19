from pathlib import Path
import uuid

from src.candidate_feature_store import CandidateFeatureStore
from src.candidate_canonicalizer import build_candidate_canonical_features


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
