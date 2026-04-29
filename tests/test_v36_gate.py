import json
import shutil
import subprocess
import uuid
from pathlib import Path

from tools.v36_gate import (
    build_preflight,
    choose_next_action,
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


def test_v36_preflight_blocks_giant_file_over_bridge_budget():
    tmp_path = _workspace()
    try:
        _init_git_workspace(tmp_path)
        _write_legacy_full_input(tmp_path)
        _write_owner_boundary(tmp_path, budget=25)
        added_lines = "\n".join(f"# added {index}" for index in range(30))
        (tmp_path / "src" / "query_builder.py").write_text(f"# base\n{added_lines}\n", encoding="utf-8")

        result = build_preflight(tmp_path)

        assert result["p0_gate_status"] == "block"
        assert result["recommended_p0_remediation_target"] == "owner_boundary"
        assert result["giant_file_touch_risk"]["over_budget"][0]["path"] == "src/query_builder.py"
        assert "giant owner file changes exceed bridge-only line budget" in result["block_reasons"]
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
