from pathlib import Path

from src.universal_kb import UniversalKB


class _ZeroCollection:
    metadata = {}

    def count(self):
        return 0


class _BrokenCountCollection:
    metadata = {}

    def count(self):
        raise RuntimeError(
            "Error reading from metadata segment reader: error occurred while decoding column 0: "
            "mismatched types; Rust type `u64` (as SQL type `INTEGER`) is not compatible with SQL type `BLOB`"
        )


class _BrokenQueryCollection:
    metadata = {}

    def count(self):
        return 1

    def query(self, **kwargs):
        raise RuntimeError(
            "Error sending backfill request to compactor: Error reading from metadata segment reader: "
            "error occurred while decoding column 0: mismatched types"
        )


class _FakeClient:
    def __init__(self, collection):
        self._collection = collection

    def get_or_create_collection(self, name, metadata=None):
        return self._collection


class _FakeEmbedding:
    def tolist(self):
        return [[0.1, 0.2, 0.3]]


def _make_kb(tmp_path: Path) -> UniversalKB:
    kb = UniversalKB.__new__(UniversalKB)
    kb.db_path = tmp_path / "universal_kb.db"
    kb.chroma_dir = tmp_path / "chroma"
    kb._model = object()
    kb._collection = None
    kb._chroma_client = None
    return kb


def test_universal_kb_collection_rebuilds_on_metadata_type_mismatch(tmp_path, monkeypatch):
    kb = _make_kb(tmp_path)
    broken_client = _FakeClient(_BrokenCountCollection())
    rebuilt_collection = _ZeroCollection()
    rebuilt = {"called": False}

    from src.model_cache import ModelCache

    monkeypatch.setattr(ModelCache, "get_chroma_client", lambda path: broken_client)

    def _fake_rebuild(client):
        rebuilt["called"] = True
        return rebuilt_collection

    monkeypatch.setattr(kb, "_auto_rebuild_collection", _fake_rebuild)

    collection = kb.collection

    assert rebuilt["called"] is True
    assert collection is rebuilt_collection


def test_universal_kb_search_hints_rebuilds_on_query_metadata_mismatch(tmp_path, monkeypatch):
    kb = _make_kb(tmp_path)
    shared_client = object()
    kb._collection = _BrokenQueryCollection()
    kb._chroma_client = shared_client
    rebuilt = {"called": False}

    from src.model_cache import ModelCache
    from src import model_profile

    monkeypatch.setattr(ModelCache, "get_chroma_client", lambda path: shared_client)
    monkeypatch.setattr(model_profile, "encode_queries", lambda model, texts: _FakeEmbedding())
    monkeypatch.setattr(kb, "_find_exact", lambda text: None)

    def _fake_rebuild(client):
        rebuilt["called"] = True
        return _ZeroCollection()

    monkeypatch.setattr(kb, "_auto_rebuild_collection", _fake_rebuild)

    results = kb.search_hints("测试查询", top_k=3, authority_only=True)

    assert results == []
    assert rebuilt["called"] is True
