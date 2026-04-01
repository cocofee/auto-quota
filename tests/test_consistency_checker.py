# -*- coding: utf-8 -*-

import pytest

import config
from src.consistency_checker import (
    _build_fingerprint,
    _compute_vote_weight,
    _normalize_core_name,
    _quota_signature,
    check_and_fix,
)


@pytest.fixture(autouse=True)
def _enable_reflection(monkeypatch):
    monkeypatch.setattr(config, "REFLECTION_ENABLED", True)
    monkeypatch.setattr(config, "REFLECTION_MIN_VOTE_RATIO", 1.5)
    monkeypatch.setattr(config, "REFLECTION_SKIP_HIGH_CONFIDENCE", 90)


def _make_result(
    name="给水管道",
    dn=None,
    material=None,
    connection=None,
    cable_section=None,
    cable_type=None,
    specialty="C10",
    quota_id="C10-3-15",
    quota_name="测试定额",
    confidence=85,
    source="agent",
):
    params = {}
    if dn is not None:
        params["dn"] = dn
    if material is not None:
        params["material"] = material
    if connection is not None:
        params["connection"] = connection
    if cable_section is not None:
        params["cable_section"] = cable_section

    item = {
        "name": name,
        "description": "",
        "specialty": specialty,
        "params": params,
    }
    if cable_type:
        item["cable_type"] = cable_type

    return {
        "bill_item": item,
        "quotas": [{"quota_id": quota_id, "name": quota_name}] if quota_id else [],
        "confidence": confidence,
        "match_source": source,
        "explanation": "测试",
    }


class TestNormalizeCoreName:
    def test_remove_location_prefix(self):
        assert _normalize_core_name("室内给水管道") == "给水管道"
        assert _normalize_core_name("室外排水管道") == "排水管道"

    def test_remove_action_suffix(self):
        assert _normalize_core_name("给水管道安装") == "给水管道"
        assert _normalize_core_name("电缆敷设") == "电缆"

    def test_remove_both(self):
        assert _normalize_core_name("室内给水管道安装") == "给水管道"

    def test_keep_short_name(self):
        assert _normalize_core_name("安装") == "安装"

    def test_empty(self):
        assert _normalize_core_name("") == ""
        assert _normalize_core_name(None) == ""


class TestBuildFingerprint:
    def test_same_item_same_fingerprint(self):
        item1 = {"name": "给水管道", "specialty": "C10", "params": {"dn": 25, "material": "PPR"}}
        item2 = {"name": "给水管道", "specialty": "C10", "params": {"dn": 25, "material": "PPR"}}
        assert _build_fingerprint(item1) == _build_fingerprint(item2)

    def test_different_dn_different_fingerprint(self):
        item1 = {"name": "给水管道", "specialty": "C10", "params": {"dn": 25}}
        item2 = {"name": "给水管道", "specialty": "C10", "params": {"dn": 50}}
        assert _build_fingerprint(item1) != _build_fingerprint(item2)

    def test_location_prefix_normalized(self):
        item1 = {"name": "给水管道", "specialty": "C10", "params": {"dn": 25}}
        item2 = {"name": "室内给水管道", "specialty": "C10", "params": {"dn": 25}}
        assert _build_fingerprint(item1) == _build_fingerprint(item2)


class TestCheckAndFix:
    def test_consistent_group_no_correction(self):
        results = [
            _make_result(dn=25, quota_id="C10-3-15", confidence=90),
            _make_result(dn=25, quota_id="C10-3-15", confidence=85),
            _make_result(dn=25, quota_id="C10-3-15", confidence=80),
        ]
        check_and_fix(results)
        assert not any(r.get("reflection_corrected") for r in results)
        assert all(r.get("reflection_correction") == {} for r in results)

    def test_majority_correction_becomes_advisory(self):
        results = [
            _make_result(dn=25, quota_id="C10-3-15", confidence=90, source="agent"),
            _make_result(dn=25, quota_id="C10-3-15", confidence=85, source="agent"),
            _make_result(name="室内给水管道", dn=25, quota_id="C10-3-18", confidence=65, source="search"),
        ]
        check_and_fix(results)

        assert results[2]["quotas"][0]["quota_id"] == "C10-3-18"
        assert results[2].get("reflection_corrected") is False
        assert results[2].get("reflection_old_quota") == "C10-3-18"
        assert results[2]["reflection_correction"]["action"] == "group_vote_advisory"
        assert results[2]["reflection_correction"]["applied"] is False
        assert results[2]["reflection_correction"]["quota_id"] == "C10-3-15"
        assert not results[0].get("reflection_corrected")
        assert not results[1].get("reflection_corrected")

    def test_high_confidence_protection(self):
        results = [
            _make_result(dn=25, quota_id="C10-3-15", confidence=92, source="experience_exact"),
            _make_result(dn=25, quota_id="C10-3-18", confidence=60, source="search"),
            _make_result(dn=25, quota_id="C10-3-18", confidence=55, source="search"),
        ]
        check_and_fix(results)

        assert results[0]["quotas"][0]["quota_id"] == "C10-3-15"
        assert not results[0].get("reflection_corrected")
        assert results[0].get("reflection_correction") == {}

    def test_different_params_different_group(self):
        results = [
            _make_result(dn=25, quota_id="C10-3-15", confidence=90),
            _make_result(dn=50, quota_id="C10-3-16", confidence=90),
        ]
        check_and_fix(results)
        assert not any(r.get("reflection_corrected") for r in results)
        assert all(r.get("reflection_correction") == {} for r in results)

    def test_graceful_failure(self):
        results = [
            {"bill_item": {}, "quotas": [], "confidence": 0, "match_source": "search"},
            {"bill_item": None, "quotas": [], "confidence": 0, "match_source": "search"},
        ]
        returned = check_and_fix(results)
        assert len(returned) == 2

    def test_single_item_no_check(self):
        results = [_make_result(dn=25, quota_id="C10-3-15")]
        check_and_fix(results)
        assert not results[0].get("reflection_corrected")
        assert results[0].get("reflection_correction") is None or results[0].get("reflection_correction") == {}

    def test_empty_results(self):
        assert check_and_fix([]) == []

    def test_low_margin_marks_conflict_only(self):
        results = [
            _make_result(dn=25, quota_id="C10-3-15", confidence=80, source="search"),
            _make_result(dn=25, quota_id="C10-3-18", confidence=75, source="search"),
        ]
        check_and_fix(results)
        assert results[0]["reflection_conflict"] is True
        assert results[1]["reflection_conflict"] is True
        assert results[0]["reflection_correction"] == {}
        assert results[1]["reflection_correction"] == {}


class TestVoteWeight:
    def test_experience_highest(self):
        r_exp = _make_result(source="experience_exact", confidence=90)
        r_search = _make_result(source="search", confidence=90)
        assert _compute_vote_weight(r_exp) > _compute_vote_weight(r_search)

    def test_confidence_matters(self):
        r_high = _make_result(source="agent", confidence=95)
        r_low = _make_result(source="agent", confidence=50)
        assert _compute_vote_weight(r_high) > _compute_vote_weight(r_low)

    def test_long_prefix_matched_first(self):
        r_fastpath = _make_result(source="agent_fastpath", confidence=100)
        r_agent = _make_result(source="agent", confidence=100)
        assert _compute_vote_weight(r_fastpath) == 1.5
        assert _compute_vote_weight(r_agent) == 2.0

    def test_experience_confirmed_distinct(self):
        r_confirmed = _make_result(source="experience_exact_confirmed", confidence=100)
        r_exact = _make_result(source="experience_exact", confidence=100)
        assert _compute_vote_weight(r_confirmed) == 3.5
        assert _compute_vote_weight(r_exact) == 5.0


class TestQuotaSignature:
    def test_extracts_ids(self):
        result = {
            "quotas": [
                {"quota_id": "C10-1-1", "name": "A"},
                {"quota_id": "C10-1-2", "name": "B"},
            ]
        }
        assert _quota_signature(result) == ("C10-1-1", "C10-1-2")

    def test_empty_when_missing(self):
        assert _quota_signature({"quotas": []}) == ()
