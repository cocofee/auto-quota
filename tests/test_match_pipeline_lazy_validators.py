import threading
import time
from concurrent.futures import ThreadPoolExecutor

import src.match_pipeline as match_pipeline
import src.price_reference_db as price_reference_db
import src.price_validator as price_validator


def test_rule_injection_validator_is_thread_safe_singleton(monkeypatch):
    class FakeValidator:
        init_calls = 0

        def __init__(self):
            type(self).init_calls += 1
            time.sleep(0.02)

    monkeypatch.setattr(match_pipeline, "ParamValidator", FakeValidator)
    monkeypatch.setattr(match_pipeline, "_RULE_INJECTION_VALIDATOR", None)
    monkeypatch.setattr(match_pipeline, "_RULE_INJECTION_VALIDATOR_LOCK", threading.Lock())

    start = threading.Event()

    def worker(_):
        start.wait()
        return match_pipeline._get_rule_injection_validator()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(worker, idx) for idx in range(8)]
        start.set()
        validators = [future.result() for future in futures]

    assert FakeValidator.init_calls == 1
    assert len({id(validator) for validator in validators}) == 1


def test_price_validator_retries_after_failed_load(monkeypatch):
    class FakePriceReferenceDB:
        pass

    class FakePriceValidator:
        init_calls = 0

        def __init__(self, db):
            type(self).init_calls += 1
            if type(self).init_calls == 1:
                raise RuntimeError("db unavailable")
            self.db = db

    monkeypatch.setattr(
        match_pipeline.config,
        "QUOTA_MATCH_PRICE_VALIDATION_ENABLED",
        True,
        raising=False,
    )
    monkeypatch.setattr(price_reference_db, "PriceReferenceDB", FakePriceReferenceDB)
    monkeypatch.setattr(price_validator, "PriceValidator", FakePriceValidator)
    monkeypatch.setattr(match_pipeline, "_PRICE_VALIDATOR", None)
    monkeypatch.setattr(match_pipeline, "_PRICE_VALIDATOR_LOCK", threading.Lock())
    monkeypatch.setattr(match_pipeline, "_PRICE_VALIDATOR_LAST_FAILURE_AT", None)
    monkeypatch.setattr(match_pipeline, "_PRICE_VALIDATOR_RETRY_INTERVAL_SECONDS", 0.0)

    assert match_pipeline._get_price_validator() is None

    validator = match_pipeline._get_price_validator()

    assert isinstance(validator, FakePriceValidator)
    assert FakePriceValidator.init_calls == 2
    assert match_pipeline._PRICE_VALIDATOR_LAST_FAILURE_AT is None
