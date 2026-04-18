from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import experience_db, param_validator, vector_engine
from src.fallback_logger import FallbackLogger
from src.param_validator import ParamValidator
from src.vector_engine import VectorEngine


class _FakeLogger:
    def __init__(self):
        self.events = []
        self._exception = None

    def debug(self, message):
        self.events.append(("debug", message))

    def warning(self, message):
        self.events.append(("warning", message))

    def opt(self, *, exception=None):
        self._exception = exception
        return self

    def error(self, message):
        self.events.append(("error", message, self._exception))


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


class _HealthyCollection:
    metadata = {}

    def add(self, **kwargs):
        del kwargs
        return None


class _HealthyClient:
    def __init__(self, collection):
        self.collection = collection

    def delete_collection(self, name):
        del name
        return None

    def create_collection(self, name, metadata=None):
        del name, metadata
        return self.collection


def test_fallback_logger_warning_and_critical_paths():
    fake_logger = _FakeLogger()
    alerts = []
    metric_events = []
    fallback = FallbackLogger(base_logger=fake_logger)
    fallback.configure_pagerduty(lambda exc, payload: alerts.append((exc, payload)))
    fallback.configure_metric_hook(lambda severity, component: metric_events.append((severity, component)))

    fallback.maybe_alert(
        RuntimeError("warn"),
        severity="warning",
        component="test.warning",
        message="warning path",
    )

    assert fallback.snapshot_counts()[("warning", "test.warning")] == 1
    assert metric_events == [("warning", "test.warning")]
    assert fake_logger.events[0][0] == "warning"

    critical_error = RuntimeError("critical")
    with pytest.raises(RuntimeError, match="critical"):
        fallback.maybe_alert(
            critical_error,
            severity="critical",
            component="test.critical",
            message="critical path",
        )

    assert fallback.snapshot_counts()[("critical", "test.critical")] == 1
    assert metric_events[-1] == ("critical", "test.critical")
    assert alerts[0][0] is critical_error
    assert alerts[0][1]["component"] == "test.critical"
    assert fake_logger.events[-1][0] == "error"


def test_param_validator_load_failure_uses_warning_severity(monkeypatch):
    alerts = []
    original_exists = Path.exists

    monkeypatch.setattr(param_validator.config, "PARAM_VALIDATOR_LEGACY_LTR_ENABLED", True, raising=False)
    monkeypatch.setattr(ParamValidator, "_ltr_model_loaded", False)
    monkeypatch.setattr(ParamValidator, "_ltr_model", None)
    monkeypatch.setattr(
        param_validator.Path,
        "exists",
        lambda self: self.name == "ltr_model.txt" or original_exists(self),
    )
    monkeypatch.setitem(
        sys.modules,
        "lightgbm",
        SimpleNamespace(Booster=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("load boom"))),
    )
    monkeypatch.setattr(
        param_validator.fallback_logger,
        "maybe_alert",
        lambda exception, **kwargs: alerts.append((exception, kwargs)),
    )

    ParamValidator._load_ltr_model()

    assert alerts
    assert alerts[0][1]["severity"] == "warning"
    assert alerts[0][1]["component"] == "param_validator.ltr_model_load"
    assert ParamValidator._ltr_model is None


def test_experience_vector_recall_failure_escalates_to_warning(monkeypatch):
    alerts = []
    disable_calls = []
    db = experience_db.ExperienceDB.__new__(experience_db.ExperienceDB)

    monkeypatch.setattr(type(db), "collection", property(lambda self: object()))
    monkeypatch.setattr(type(db), "model", property(lambda self: object()))
    monkeypatch.setattr(db, "_safe_collection_count", lambda coll: 1)
    monkeypatch.setattr(db, "_should_rebuild_vector_index", lambda exc: False)
    monkeypatch.setattr(
        db,
        "_disable_vector_index",
        lambda exc, schedule_rebuild=False: disable_calls.append((exc, schedule_rebuild)),
    )
    monkeypatch.setattr(
        experience_db.fallback_logger,
        "maybe_alert",
        lambda exception, **kwargs: alerts.append((exception, kwargs)),
    )

    from src import model_profile

    monkeypatch.setattr(
        model_profile,
        "encode_queries",
        lambda model, texts: (_ for _ in ()).throw(RuntimeError("embed boom")),
    )

    results = db._recall_vector_candidates("test", top_k=5, province="test-province")

    assert results == []
    assert len(disable_calls) == 1
    assert alerts
    assert alerts[0][1]["severity"] == "warning"
    assert alerts[0][1]["component"] == "experience_db.vector_recall"


def test_vector_engine_build_index_encoding_failure_is_critical(tmp_path, monkeypatch):
    alerts = []
    engine = VectorEngine.__new__(VectorEngine)
    engine.province = "test-province"
    engine.db_path = tmp_path / "quota.db"
    engine.chroma_dir = tmp_path / "chroma"
    engine._model = object()
    engine._collection = None
    engine._chroma_client = None
    engine._connect = lambda row_factory=False: _FakeConn(
        [{"id": 1, "search_text": "test quota", "book": "C10", "specialty": "install"}]
    )
    engine._reset_cached_chroma_client = lambda: None

    from src import model_profile
    from src.model_cache import ModelCache

    monkeypatch.setattr(ModelCache, "get_chroma_client", lambda path: _HealthyClient(_HealthyCollection()))
    monkeypatch.setattr(
        model_profile,
        "encode_documents",
        lambda model, texts, batch_size, show_progress: (_ for _ in ()).throw(RuntimeError("encode fail")),
    )

    def _capture_and_raise(exception, **kwargs):
        alerts.append((exception, kwargs))
        raise exception

    monkeypatch.setattr(vector_engine.fallback_logger, "maybe_alert", _capture_and_raise)

    with pytest.raises(RuntimeError, match="encode fail"):
        engine.build_index(batch_size=8)

    assert alerts
    assert alerts[0][1]["severity"] == "critical"
    assert alerts[0][1]["component"] == "vector_engine.build_index_encoding"
