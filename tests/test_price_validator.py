from src.match_pipeline import _finalize_search_result_payload
from src.price_validator import PriceValidator


class _FakePriceDB:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def get_composite_price_reference(self, **kwargs):
        self.calls += 1
        return dict(self.payload)


def test_price_validator_flags_large_deviation():
    validator = PriceValidator(
        _FakePriceDB(
            {
                "summary": {
                    "sample_count": 8,
                    "median_composite_unit_price": 100.0,
                }
            }
        )
    )

    result = validator.validate(
        {"name": "配电箱", "specialty": "C10", "unit_price": 180.0},
        {"quota_id": "C10-1-1"},
        confidence=72,
    )

    assert result["status"] == "price_mismatch"
    assert result["confidence_penalty"] == -10
    assert result["median_price"] == 100.0
    assert result["actual_price"] == 180.0


def test_price_validator_skips_when_samples_insufficient():
    validator = PriceValidator(
        _FakePriceDB(
            {
                "summary": {
                    "sample_count": 3,
                    "median_composite_unit_price": 100.0,
                }
            }
        )
    )

    result = validator.validate(
        {"name": "配电箱", "specialty": "C10", "unit_price": 180.0},
        {"quota_id": "C10-1-1"},
        confidence=72,
    )

    assert result["status"] == "insufficient_samples"


def test_price_validator_skips_without_bill_price_and_avoids_db_lookup():
    fake_db = _FakePriceDB(
        {
            "summary": {
                "sample_count": 8,
                "median_composite_unit_price": 100.0,
            }
        }
    )
    validator = PriceValidator(fake_db)

    result = validator.validate(
        {"name": "配电箱", "specialty": "C10"},
        {"quota_id": "C10-1-1"},
        confidence=72,
    )

    assert result["status"] == "skip"
    assert result["reason"] == "missing_bill_price"
    assert fake_db.calls == 0


def test_match_pipeline_price_validation_penalizes_confidence(monkeypatch):
    fake_validator = PriceValidator(
        _FakePriceDB(
            {
                "summary": {
                    "sample_count": 8,
                    "median_composite_unit_price": 100.0,
                }
            }
        )
    )
    monkeypatch.setattr("src.match_pipeline._get_price_validator", lambda: fake_validator)

    item = {
        "name": "配电箱",
        "specialty": "C10",
        "unit": "台",
        "quantity": 1,
        "unit_price": 180.0,
    }
    best = {
        "quota_id": "C10-1-1",
        "name": "成套配电箱",
        "unit": "台",
        "param_match": True,
    }
    result = {
        "bill_item": item,
        "quotas": [{"quota_id": "C10-1-1", "name": "成套配电箱", "unit": "台"}],
        "confidence": 72,
        "explanation": "selected from structured candidates",
        "reason_tags": [],
        "candidates_count": 1,
        "candidate_count": 1,
        "trace": {"steps": []},
    }

    finalized = _finalize_search_result_payload(
        result,
        item=item,
        candidates=[best],
        valid_candidates=[best],
        best=best,
        explanation="selected from structured candidates",
        reasoning_decision={},
    )

    assert finalized["confidence"] == 64.8
    assert finalized["confidence_score"] == 65
    assert finalized["price_validation"]["status"] == "price_mismatch"
    assert "price_mismatch" in (finalized.get("reason_tags") or [])
