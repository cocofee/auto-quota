import json
import shutil
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
