from pathlib import Path

from src.vector_engine import VectorEngine


class _BrokenCollection:
    metadata = {}

    def count(self):
        raise RuntimeError(
            "Trying to instantiate configuration of type CollectionConfigurationInternal from JSON with type _type"
        )


class _HealthyCollection:
    metadata = {}

    def __init__(self):
        self.add_calls = []

    def count(self):
        return 0

    def add(self, **kwargs):
        self.add_calls.append(kwargs)


class _SchemaBrokenQueryCollection:
    metadata = {}

    def __init__(self):
        self.query_calls = 0

    def count(self):
        return 1

    def query(self, **kwargs):
        del kwargs
        self.query_calls += 1
        raise RuntimeError(
            "Database error: error returned from database: (code: 1) no such column: collections.schema_str"
        )


class _SearchHealthyCollection:
    metadata = {}

    def __init__(self):
        self.query_calls = []

    def count(self):
        return 1

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return {"ids": [["1"]], "distances": [[0.2]]}


class _BrokenClient:
    def __init__(self):
        self.delete_calls = 0

    def get_or_create_collection(self, name, metadata=None):
        del name, metadata
        return _BrokenCollection()

    def delete_collection(self, name):
        del name
        self.delete_calls += 1
        raise RuntimeError(
            "Trying to instantiate configuration of type CollectionConfigurationInternal from JSON with type _type"
        )

    def clear_system_cache(self):
        return None


class _HealthyClient:
    def __init__(self, collection):
        self.collection = collection
        self.created = 0

    def get_or_create_collection(self, name, metadata=None):
        del name, metadata
        return self.collection

    def create_collection(self, name, metadata=None):
        del name, metadata
        self.created += 1
        return self.collection

    def delete_collection(self, name):
        del name
        return None

    def clear_system_cache(self):
        return None


class _SystemProbe:
    def __init__(self):
        self.stopped = 0
        self.reset = 0

    def stop(self):
        self.stopped += 1

    def reset_state(self):
        self.reset += 1


class _ClosableClient:
    def __init__(self):
        self.cache_cleared = 0
        self._system = _SystemProbe()

    def clear_system_cache(self):
        self.cache_cleared += 1


class _FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.payload = rows

    def execute(self, sql):
        if "PRAGMA table_info(quotas)" in sql:
            self.payload = [
                (0, "id"),
                (1, "search_text"),
                (2, "book"),
                (3, "specialty"),
            ]
        elif "SELECT" in sql:
            self.payload = self.rows
        return self

    def fetchall(self):
        return self.payload


class _FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return _FakeCursor(self.rows)

    def close(self):
        return None


class _FakeEmbeddings:
    def tolist(self):
        return [[0.1, 0.2, 0.3]]


def _make_engine(tmp_path: Path) -> VectorEngine:
    engine = VectorEngine.__new__(VectorEngine)
    engine.province = "test-province"
    engine.db_path = tmp_path / "quota.db"
    engine.chroma_dir = tmp_path / "chroma"
    engine._model = object()
    engine._collection = None
    engine._chroma_client = None
    return engine


def test_vector_engine_collection_rebuilds_on_metadata_type_mismatch(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path)
    broken_client = _BrokenClient()
    healthy_collection = _HealthyCollection()
    healthy_client = _HealthyClient(healthy_collection)
    call_count = {"value": 0}

    from src.model_cache import ModelCache

    def _fake_get_chroma_client(path):
        del path
        call_count["value"] += 1
        return broken_client if call_count["value"] == 1 else healthy_client

    monkeypatch.setattr(ModelCache, "get_chroma_client", _fake_get_chroma_client)

    collection = engine.collection

    assert collection is healthy_collection
    assert healthy_client.created == 1


def test_vector_engine_build_index_rebuilds_on_type_metadata_mismatch(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path)
    engine._connect = lambda row_factory=False: _FakeConn(
        [{"id": 1, "search_text": "test quota", "book": "C10", "specialty": "install"}]
    )
    broken_client = _BrokenClient()
    healthy_collection = _HealthyCollection()
    healthy_client = _HealthyClient(healthy_collection)
    call_count = {"value": 0}

    from src import model_profile
    from src.model_cache import ModelCache

    def _fake_get_chroma_client(path):
        del path
        call_count["value"] += 1
        return broken_client if call_count["value"] == 1 else healthy_client

    monkeypatch.setattr(ModelCache, "get_chroma_client", _fake_get_chroma_client)
    monkeypatch.setattr(
        model_profile,
        "encode_documents",
        lambda model, texts, batch_size, show_progress: _FakeEmbeddings(),
    )

    engine.build_index(batch_size=8)

    assert healthy_client.created == 1
    assert len(healthy_collection.add_calls) == 1
    assert healthy_collection.add_calls[0]["ids"] == ["1"]


def test_vector_engine_detects_schema_str_mismatch(tmp_path):
    engine = _make_engine(tmp_path)

    assert engine._should_rebuild_vector_index(
        "Database error: error returned from database: (code: 1) no such column: collections.schema_str"
    )


def test_vector_engine_search_rebuilds_on_schema_str_query_failure(tmp_path, monkeypatch):
    engine = _make_engine(tmp_path)
    engine._connect = lambda row_factory=False: _FakeConn(
        [{"id": 1, "name": "test quota", "quota_id": "Q-1"}]
    )
    broken_collection = _SchemaBrokenQueryCollection()
    healthy_collection = _SearchHealthyCollection()
    shared_client = object()
    rebuild_calls = {"value": 0}

    from src.model_cache import ModelCache

    engine._collection = broken_collection
    engine._chroma_client = shared_client

    monkeypatch.setattr(ModelCache, "get_chroma_client", lambda path: shared_client)

    def _fake_build_index(batch_size=256):
        del batch_size
        rebuild_calls["value"] += 1
        engine._collection = healthy_collection

    monkeypatch.setattr(engine, "build_index", _fake_build_index)

    results = engine.search("test quota", top_k=1, precomputed_embedding=[0.1, 0.2, 0.3])

    assert rebuild_calls["value"] == 1
    assert broken_collection.query_calls == 1
    assert len(healthy_collection.query_calls) == 1
    assert results[0]["id"] == 1
    assert results[0]["vector_score"] == 0.8


def test_release_chroma_client_stops_underlying_system():
    client = _ClosableClient()

    VectorEngine._release_chroma_client(client)

    assert client.cache_cleared == 1
    assert client._system.stopped == 1
    assert client._system.reset == 1
