import json
import shutil
import subprocess
import uuid
from pathlib import Path

from tools.v36_gate import (
    build_preflight,
    choose_next_action,
    diagnose_pure_search,
    register_validation,
    release_check,
)


def _workspace() -> Path:
    root = Path.cwd() / ".codex_stage" / "v36_gate_tests" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _init_git_workspace(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    query_builder = src / "query_builder.py"
    query_builder.write_text("# base\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/query_builder.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=v36@example.invalid",
            "-c",
            "user.name=V36 Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _write_legacy_full_input(root: Path) -> None:
    latest = root / "output" / "benchmark_assets" / "ltr_v2_full_20260422" / "all_errors.jsonl"
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text("{}\n", encoding="utf-8")
    attr = root / "reports" / "attribution" / "ltr_v2_full_20260422.json"
    attr.parent.mkdir(parents=True, exist_ok=True)
    attr.write_text("{}", encoding="utf-8")


def _write_v36_full_asset_input(root: Path, *, wrong_total: int = 2, manifest_count: int | None = None) -> None:
    reports = root / "reports" / "attribution"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "global_repair_v36_full_attribution.json").write_text(
        json.dumps(
            {
                "profile": "full",
                "total": 10,
                "correct_total": 8,
                "wrong_total": wrong_total,
                "overall_hit_rate": 80.0,
                "recall_hit_rate": 90.0,
                "counts": {"R2_LTR选错": wrong_total},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (reports / "global_repair_v36_full_summary.json").write_text("{}", encoding="utf-8")
    assets = root / "output" / "benchmark_assets" / "global_repair_v36_full"
    assets.mkdir(parents=True, exist_ok=True)
    (assets / "all_errors.jsonl").write_text("{}\n{}\n", encoding="utf-8")
    (assets / "manifest.json").write_text(
        json.dumps(
            {
                "counts": {"all_errors": manifest_count if manifest_count is not None else wrong_total},
                "files": {"all_errors": "output/benchmark_assets/global_repair_v36_full/all_errors.jsonl"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_owner_boundary(root: Path, budget: int = 25) -> None:
    path = root / "reports" / "attribution" / "v36_p0_owner_boundary_test.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "v36_owner_boundary.v1",
                "p0_remediation_target": "owner_boundary",
                "allowed_bridge_changes": {
                    "max_new_lines_in_any_giant_owner_file": budget,
                },
            }
        ),
        encoding="utf-8",
    )


def test_v36_preflight_selects_legacy_full_input():
    tmp_path = _workspace()
    latest = tmp_path / "output" / "benchmark_assets" / "ltr_v2_full_20260422" / "all_errors.jsonl"
    latest.parent.mkdir(parents=True)
    latest.write_text("{}\n", encoding="utf-8")
    attr = tmp_path / "reports" / "attribution" / "ltr_v2_full_20260422.json"
    attr.parent.mkdir(parents=True)
    attr.write_text("{}", encoding="utf-8")

    try:
        result = build_preflight(tmp_path)
    finally:
        _cleanup(tmp_path)

    assert result["selected_input"]["status"] == "present"
    assert result["selected_input"]["input_freshness"] == "stale"
    assert result["p0_gate_status"] in {"pass", "warn"}


def test_v36_preflight_selects_full_asset_when_latest_missing():
    tmp_path = _workspace()
    try:
        _write_legacy_full_input(tmp_path)
        _write_v36_full_asset_input(tmp_path, wrong_total=2)

        result = build_preflight(tmp_path)
    finally:
        _cleanup(tmp_path)

    selected = result["selected_input"]
    assert selected["status"] == "present"
    assert selected["input_freshness"] == "fresh_asset"
    assert selected["latest_path"] == "output/benchmark_assets/global_repair_v36_full/all_errors.jsonl"
    assert selected["asset_manifest_path"] == "output/benchmark_assets/global_repair_v36_full/manifest.json"
    assert selected["reason"] == "latest_missing_using_asset_all_errors"
    assert selected["full_global_result"]["wrong_total"] == 2
    assert result["full_validation_status"] == "failed"


def test_v36_preflight_rejects_mismatched_full_asset_and_uses_legacy():
    tmp_path = _workspace()
    try:
        _write_legacy_full_input(tmp_path)
        _write_v36_full_asset_input(tmp_path, wrong_total=2, manifest_count=3)

        result = build_preflight(tmp_path)
    finally:
        _cleanup(tmp_path)

    assert result["selected_input"]["input_freshness"] == "stale"
    assert result["selected_input"]["reason"] == "using legacy full benchmark input; v36 full output not found"


def test_v36_preflight_blocks_giant_file_without_owner_boundary():
    tmp_path = _workspace()
    try:
        _init_git_workspace(tmp_path)
        _write_legacy_full_input(tmp_path)
        (tmp_path / "src" / "query_builder.py").write_text("# base\n# new bridge\n", encoding="utf-8")

        result = build_preflight(tmp_path)

        assert result["p0_gate_status"] == "block"
        assert result["recommended_p0_remediation_target"] == "owner_boundary"
        assert result["giant_file_touch_risk"]["status"] == "block"
        assert "giant owner files touched without owner_boundary governance manifest" in result["block_reasons"]
    finally:
        _cleanup(tmp_path)


def test_v36_preflight_warns_giant_file_over_bridge_budget_with_owner_boundary():
    tmp_path = _workspace()
    try:
        _init_git_workspace(tmp_path)
        _write_legacy_full_input(tmp_path)
        _write_owner_boundary(tmp_path, budget=25)
        added_lines = "\n".join(f"# added {index}" for index in range(30))
        (tmp_path / "src" / "query_builder.py").write_text(f"# base\n{added_lines}\n", encoding="utf-8")

        result = build_preflight(tmp_path)

        assert result["p0_gate_status"] == "warn"
        assert result["recommended_p0_remediation_target"] == "owner_boundary"
        assert result["giant_file_touch_risk"]["status"] == "warn"
        assert result["giant_file_touch_risk"]["over_budget"][0]["path"] == "src/query_builder.py"
        assert result["giant_file_touch_risk"]["over_budget_policy"] == "warn_with_owner_boundary"
        assert "giant owner file changes exceed bridge-only line budget" not in result["block_reasons"]
    finally:
        _cleanup(tmp_path)


def test_v36_preflight_allows_owner_boundary_bridge_budget():
    tmp_path = _workspace()
    try:
        _init_git_workspace(tmp_path)
        _write_legacy_full_input(tmp_path)
        _write_owner_boundary(tmp_path, budget=25)
        (tmp_path / "src" / "query_builder.py").write_text("# base\n# bridge call\n", encoding="utf-8")

        result = build_preflight(tmp_path)

        assert result["p0_gate_status"] == "warn"
        assert result["recommended_p0_remediation_target"] == "owner_boundary"
        assert result["giant_file_touch_risk"]["status"] == "warn"
        assert result["giant_file_touch_risk"]["over_budget"] == []
    finally:
        _cleanup(tmp_path)


def test_v36_choose_next_action_writes_contract_outputs():
    tmp_path = _workspace()
    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps(
            [
                {
                    "sample_id": "s1",
                    "is_match": False,
                    "stored_ids": ["Q1"],
                    "algo_id": "W1",
                    "error_stage": "retriever",
                    "miss_category": "recall_miss",
                    "recall_rank": -1,
                    "pre_ltr_top1_id": "W1",
                    "post_ltr_top1_id": "W1",
                    "post_final_top1_id": "W1",
                }
            ]
        ),
        encoding="utf-8",
    )
    attr = tmp_path / "attr.json"
    attr.write_text("{}", encoding="utf-8")

    try:
        result = choose_next_action(
            tmp_path,
            latest,
            attr,
            Path("decision.csv"),
            Path("summary.json"),
            Path("next_action.json"),
        )

        assert result["action"] == "improve_diagnostics"
        assert (tmp_path / "decision.csv").exists()
        assert json.loads((tmp_path / "next_action.json").read_text(encoding="utf-8"))["full_validation_status"] == "pending"
    finally:
        _cleanup(tmp_path)


def test_v36_choose_next_action_demotes_weak_shared_cluster():
    tmp_path = _workspace()
    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps(
            [
                {
                    "sample_id": f"weak-{index}",
                    "is_match": False,
                    "stored_ids": [f"C4-4-{index}"],
                    "algo_id": "C4-10-114",
                    "error_stage": "ltr_ranker",
                    "miss_category": "confidence_miss",
                    "recall_rank": 1,
                    "pre_ltr_top1_id": f"C4-4-{index}",
                    "post_ltr_top1_id": "C4-10-114",
                    "post_final_top1_id": "C4-10-114",
                }
                for index in range(1, 3)
            ]
        ),
        encoding="utf-8",
    )
    attr = tmp_path / "attr.json"
    attr.write_text("{}", encoding="utf-8")

    try:
        result = choose_next_action(
            tmp_path,
            latest,
            attr,
            Path("decision.csv"),
            Path("summary.json"),
            Path("next_action.json"),
        )
        summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
        next_action = json.loads((tmp_path / "next_action.json").read_text(encoding="utf-8"))

        assert summary["target_common_issue"]["commonality"] == "weak_shared"
        assert result["action"] == "improve_diagnostics"
        assert next_action["action"] == "improve_diagnostics"
        assert "weak_shared" in next_action["reason"]
    finally:
        _cleanup(tmp_path)


def test_v36_choose_next_action_allows_strong_shared_cluster():
    tmp_path = _workspace()
    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps(
            [
                {
                    "sample_id": f"shared-{index}",
                    "is_match": False,
                    "stored_ids": [f"C4-4-{index}"],
                    "algo_id": "C4-10-114",
                    "error_stage": "ltr_ranker",
                    "miss_category": "confidence_miss",
                    "recall_rank": 1,
                    "pre_ltr_top1_id": f"C4-4-{index}",
                    "post_ltr_top1_id": "C4-10-114",
                    "post_final_top1_id": "C4-10-114",
                }
                for index in range(1, 4)
            ]
        ),
        encoding="utf-8",
    )
    attr = tmp_path / "attr.json"
    attr.write_text("{}", encoding="utf-8")

    try:
        result = choose_next_action(
            tmp_path,
            latest,
            attr,
            Path("decision.csv"),
            Path("summary.json"),
            Path("next_action.json"),
        )
        summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

        assert summary["target_common_issue"]["commonality"] == "shared"
        assert result["action"] == "fix_r2_ltr"
        assert result["target_common_issue"]["sample_count"] == 3
    finally:
        _cleanup(tmp_path)


def test_v36_choose_next_action_excludes_closed_data_review_samples():
    tmp_path = _workspace()
    latest = tmp_path / "latest.json"
    latest.write_text(
        json.dumps(
            [
                {
                    "sample_id": "closed-data",
                    "is_match": False,
                    "stored_ids": ["Q1"],
                    "algo_id": "W1",
                    "error_stage": "expected_wrong",
                    "miss_category": "data",
                    "recall_rank": -1,
                    "pre_ltr_top1_id": "W1",
                    "post_ltr_top1_id": "W1",
                    "post_final_top1_id": "W1",
                },
                {
                    "sample_id": "open-actionable",
                    "is_match": False,
                    "stored_ids": ["C4-4-1"],
                    "algo_id": "C4-10-114",
                    "error_stage": "ltr_ranker",
                    "miss_category": "confidence_miss",
                    "recall_rank": 1,
                    "pre_ltr_top1_id": "C4-4-1",
                    "post_ltr_top1_id": "C4-10-114",
                    "post_final_top1_id": "C4-10-114",
                },
            ]
        ),
        encoding="utf-8",
    )
    attr = tmp_path / "attr.json"
    attr.write_text("{}", encoding="utf-8")
    queue = tmp_path / "reports" / "agent_state" / "v36_data_review_queue.json"
    queue.parent.mkdir(parents=True)
    queue.write_text(
        json.dumps({"items": [{"sample_id": "closed-data", "status": "fixed_in_corpus"}]}),
        encoding="utf-8",
    )

    try:
        result = choose_next_action(
            tmp_path,
            latest,
            attr,
            Path("decision.csv"),
            Path("summary.json"),
            Path("next_action.json"),
        )
        summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

        assert result["wrong_total_before_data_review_exclusion"] == 2
        assert result["wrong_total"] == 1
        assert summary["data_review_exclusion_summary"]["excluded_sample_ids"] == ["closed-data"]
        assert summary["target_common_issue"]["representative_sample_ids"] == ["open-actionable"]
    finally:
        _cleanup(tmp_path)


def test_v36_diagnose_pure_search_populates_required_metrics():
    tmp_path = _workspace()
    latest = tmp_path / "output" / "benchmark_assets" / "ltr_v2_full_20260422" / "all_errors.jsonl"
    latest.parent.mkdir(parents=True)
    records = [
        {
            "sample_id": "missing",
            "is_match": False,
            "expected_quota_ids": ["4-9-1"],
            "predicted_quota_id": "4-11-1",
            "error_stage": "retriever",
            "miss_category": "recall_miss",
            "all_candidate_ids": ["4-11-1", "4-11-2"],
            "specialty": "C4",
            "match_source": "search",
        },
        {
            "sample_id": "veto",
            "is_match": False,
            "expected_quota_ids": ["4-9-2"],
            "predicted_quota_id": "4-11-2",
            "error_stage": "retriever",
            "miss_category": "recall_miss",
            "all_candidate_ids": ["4-11-2", "4-9-2"],
            "candidate_snapshots": [
                {"quota_id": "4-9-2", "param_match": False, "ltr_feature_snapshot": {"hybrid_rank": 2, "bm25_rank": 2}}
            ],
            "specialty": "C4",
            "match_source": "search",
        },
    ]
    latest.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    attr = tmp_path / "reports" / "attribution" / "ltr_v2_full_20260422.json"
    attr.parent.mkdir(parents=True)
    attr.write_text("{}", encoding="utf-8")

    try:
        result = diagnose_pure_search(tmp_path, Path("reports/attribution/pure_search_diagnosis.json"))

        metrics = result["pure_search_metrics"]
        assert result["status"] == "complete_static_diagnosis"
        assert metrics["recall_at_k"]["raw_candidate_top20"]["hit_count"] == 1
        assert metrics["route_filter_loss"]["missing_candidate_count"] == 1
        assert metrics["validator_veto_rate"]["veto_count"] == 1
        assert metrics["prior_candidates_delta"] is not None
        assert metrics["latency_breakdown_ms"] is not None
    finally:
        _cleanup(tmp_path)


def test_v36_register_validation_updates_ledger_and_blocks_release():
    tmp_path = _workspace()
    ledger = Path("reports/agent_state/v36_pending_full_validation.json")

    try:
        registered = register_validation(
            tmp_path,
            fix_id="fix-1",
            action="fix_r2_ltr",
            description="targeted local validation",
            files=["src/example.py"],
            validation=["pytest tests/test_example.py"],
            status="pending_full_validation",
            ledger_path=ledger,
        )
        release = release_check(tmp_path, ledger)

        assert registered["pending_full_validation_summary"]["pending"] == 1
        assert release["release_gate_status"] == "block"
    finally:
        _cleanup(tmp_path)
