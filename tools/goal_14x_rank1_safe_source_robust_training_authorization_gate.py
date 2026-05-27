from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"

DEFAULT_PLAN = AGENT_STATE / "goal_14x_rank1_safe_source_robust_experiment_plan_definition_summary.json"
DEFAULT_MATRIX_SUMMARY = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix_build_summary.json"
DEFAULT_MATRIX_DIR = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_14x_rank1_safe_source_robust_training_authorization_gate"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
REQUIRED_GO_TEXT = "go: run 14.3 rank1-safe source-robust dev/OOF training"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(cell) for cell in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(cell).replace("|", "/") for cell in row) + " |")
    return "\n".join(lines)


def statuses_clean(rows: list[dict[str, Any]]) -> bool:
    return all(row.get("status") in {"pass", "info", "excluded", "blocked", "present"} for row in rows)


def build_report(
    *,
    plan: dict[str, Any],
    matrix: dict[str, Any],
    matrix_dir: Path,
    output_prefix: Path,
    explicit_go: bool,
) -> dict[str, Any]:
    command_rows = plan.get("command_contract", [])
    training_command = next((row for row in command_rows if row.get("stage") == "14.3_if_explicit_go"), {})
    artifacts_14_3 = [row for row in plan.get("required_artifacts", []) if row.get("required_at") == "14.3"]
    stop_conditions = plan.get("stop_conditions", [])

    source_balance = read_csv_rows(matrix_dir / "source_balance_checks.csv") if (matrix_dir / "source_balance_checks.csv").exists() else []
    leakage = read_csv_rows(matrix_dir / "leakage_checks.csv") if (matrix_dir / "leakage_checks.csv").exists() else []
    feature_contract = read_csv_rows(matrix_dir / "feature_contract_report.csv") if (matrix_dir / "feature_contract_report.csv").exists() else []

    required_matrix_files = [
        matrix_dir / "ltr_matrix_dev.csv",
        matrix_dir / "ltr_features_dev.jsonl",
        matrix_dir / "ltr_group_dev.txt",
        matrix_dir / "ltr_group_dev.jsonl",
        matrix_dir / "source_balance_checks.csv",
        matrix_dir / "leakage_checks.csv",
        matrix_dir / "feature_contract_report.csv",
        matrix_dir / "taxonomy_empty_slice_manifest.csv",
        matrix_dir / "province_book_balance_checks.csv",
    ]
    missing_matrix_files = [safe_rel(path) for path in required_matrix_files if not path.exists() or path.stat().st_size <= 0]
    command_text = training_command.get("command", "")
    forbidden_text = training_command.get("forbidden", "")
    allowed_text = training_command.get("allowed", "")
    metrics = matrix.get("metrics", {})
    artifact_names = {row.get("artifact") for row in artifacts_14_3}

    gate_rows = [
        {
            "gate": "matrix_ready",
            "status": "pass" if matrix.get("decision") == "balanced_matrix_ready_for_14_3_authorization_gate" else "fail",
            "value": matrix.get("decision", ""),
            "reason": "14.3 can only train after 14.2 balanced matrix passed.",
        },
        {
            "gate": "matrix_files_present",
            "status": "pass" if not missing_matrix_files else "fail",
            "value": len(missing_matrix_files),
            "reason": "|".join(missing_matrix_files),
        },
        {
            "gate": "source_balance_clean",
            "status": "pass" if source_balance and statuses_clean(source_balance) else "fail",
            "value": metrics.get("max_source_family_group_share", ""),
            "reason": "source_family cap and fold balance must pass before training.",
        },
        {
            "gate": "leakage_checks_clean",
            "status": "pass" if leakage and statuses_clean(leakage) else "fail",
            "value": len([row for row in leakage if row.get("status") == "pass"]),
            "reason": "No label/source/id leakage, no heldout/hard, no cross-fold source_file.",
        },
        {
            "gate": "feature_contract_present",
            "status": "pass" if feature_contract and statuses_clean(feature_contract) else "fail",
            "value": len(feature_contract),
            "reason": "Strong challenger observable supports must be present or explicitly blocked as non-training rules.",
        },
        {
            "gate": "training_command_present",
            "status": "pass" if training_command else "fail",
            "value": command_text,
            "reason": "14.3 command boundary must be explicit.",
        },
        {
            "gate": "dev_oof_only_contract",
            "status": "pass" if "--dev-oof-only" in command_text and "dev/OOF" in allowed_text else "fail",
            "value": allowed_text,
            "reason": "Training may use only dev/OOF in this stage.",
        },
        {
            "gate": "heldout_hard_blocked",
            "status": "pass" if "heldout/hard" in forbidden_text and not metrics.get("heldout_used_for_selection") and not metrics.get("hard_used_for_selection") else "fail",
            "value": forbidden_text,
            "reason": "No heldout/hard selection or validation during 14.3.",
        },
        {
            "gate": "required_14_3_artifacts_defined",
            "status": "pass"
            if {
                "candidate_scorecard",
                "rank1_preservation_report",
                "strong_challenger_gate_coverage",
                "source_fold_robustness",
                "taxonomy_empty_separate_audit",
                "threshold_manifest",
            }.issubset(artifact_names)
            else "fail",
            "value": "|".join(sorted(artifact_names)),
            "reason": "Training execution must produce scorecard, rank1, gate, source/fold, taxonomy, and threshold artifacts.",
        },
        {
            "gate": "stop_conditions_present",
            "status": "pass" if len(stop_conditions) >= 8 else "fail",
            "value": len(stop_conditions),
            "reason": "Execution must stop on label-derived gates, low-conf-alone demotion, rank1 losses, source dominance, or heldout/hard use.",
        },
        {
            "gate": "explicit_training_go",
            "status": "pass" if explicit_go else "hold",
            "value": explicit_go,
            "reason": f"Training requires exact user authorization: {REQUIRED_GO_TEXT}",
        },
    ]
    hard_fail = any(row["status"] == "fail" for row in gate_rows)
    if hard_fail:
        decision = "do_not_train_fix_14_3_authorization_inputs"
    elif explicit_go:
        decision = "authorized_for_14_3_dev_oof_training"
    else:
        decision = "dev_oof_training_ready_but_held_without_explicit_go"

    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "training_contract_csv": str(output_prefix.with_name(output_prefix.name + "_training_contract.csv")),
        "required_outputs_csv": str(output_prefix.with_name(output_prefix.name + "_required_outputs.csv")),
    }
    training_contract = [
        {"item": "required_go_text", "value": REQUIRED_GO_TEXT},
        {"item": "data_dir", "value": safe_rel(matrix_dir)},
        {"item": "candidate_plan", "value": "reports/agent_state/goal_14x_rank1_safe_source_robust_experiment_plan_definition_candidate_matrix.csv"},
        {"item": "future_command", "value": command_text},
        {"item": "allowed", "value": "train/evaluate R14 candidate matrix on dev/OOF only"},
        {"item": "forbidden", "value": "heldout/hard selection; validation; release; GoalSearcher edits; online changes"},
        {"item": "default_without_go", "value": "do_not_train"},
    ]
    return {
        "stage": "14.3 rank1-safe source-robust dev/OOF training authorization gate",
        "read_only_review": True,
        "decision": decision,
        "required_go_text": REQUIRED_GO_TEXT,
        "explicit_training_go_present": explicit_go,
        "metrics": {
            "matrix_groups": metrics.get("accepted_groups"),
            "matrix_rows": metrics.get("matrix_rows"),
            "positive_rows": metrics.get("positive_rows"),
            "max_source_family_group_share": metrics.get("max_source_family_group_share"),
            "observed_oof_fold_count": metrics.get("observed_oof_fold_count"),
            "missing_matrix_files": len(missing_matrix_files),
            "required_14_3_artifact_count": len(artifacts_14_3),
            "stop_condition_count": len(stop_conditions),
        },
        "gate_rows": gate_rows,
        "training_contract": training_contract,
        "required_outputs": artifacts_14_3,
        "artifacts": artifacts,
        "next_stage": {
            "id": "14.3_execution",
            "name": "rank1-safe source-robust dev/OOF training",
            "recommended": f"If and only if the user says `{REQUIRED_GO_TEXT}`, execute 14.3 dev/OOF training. Otherwise keep do_not_train.",
            "default": "do_not_train",
        },
        "anti_drift_conclusion": (
            "14.3 authorization is read-only. It did not train, run heldout/hard, validate, release, edit GoalSearcher, "
            "or tune thresholds. It only checked whether the 14.2 balanced matrix and command contract are ready for a future explicit-go dev/OOF run."
        ),
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 14.3 Dev/OOF Training Authorization Gate",
        "",
        "Read-only go/no-go gate for rank1-safe source-robust reranker training.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Gate Checks",
        "",
        md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Training Contract",
        "",
        md_table([["item", "value"]] + [[row["item"], row["value"]] for row in report["training_contract"]]),
        "",
        "## Next",
        "",
        report["next_stage"]["recommended"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.3 rank1-safe source-robust dev/OOF training authorization gate 已完成。\n"
        f"结论：{report['decision']}。matrix_groups={report['metrics']['matrix_groups']}，"
        f"matrix_rows={report['metrics']['matrix_rows']}，max_source_family_share={report['metrics']['max_source_family_group_share']}。\n"
        f"下一步：只有明确说 `{REQUIRED_GO_TEXT}`，才允许执行 dev/OOF training；否则保持 do_not_train。\n"
        "禁止：无明确 go 训练、跑 heldout/hard、validation、release、改 GoalSearcher、调线上阈值。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>14.3 rank1-safe source-robust dev/OOF training authorization gate</td>
            <td>Read-only training go/no-go gate using the 14.2 balanced OSS matrix and locked command contract.</td>
            <td><code>{safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
    if "14.3 rank1-safe source-robust dev/OOF training authorization gate" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="14.3 rank1-safe source-robust dev/OOF training authorization gate")
    parser.add_argument("--plan-summary", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--matrix-summary", type=Path, default=DEFAULT_MATRIX_SUMMARY)
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    parser.add_argument("--explicit-training-go", action="store_true")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    report = build_report(
        plan=read_json(args.plan_summary),
        matrix=read_json(args.matrix_summary),
        matrix_dir=args.matrix_dir,
        output_prefix=args.output_prefix,
        explicit_go=args.explicit_training_go,
    )
    write_json(Path(report["artifacts"]["summary_json"]), report)
    write_markdown(Path(report["artifacts"]["summary_md"]), report)
    write_csv(Path(report["artifacts"]["gate_checks_csv"]), report["gate_rows"], ["gate", "status", "value", "reason"])
    write_csv(Path(report["artifacts"]["training_contract_csv"]), report["training_contract"], ["item", "value"])
    write_csv(Path(report["artifacts"]["required_outputs_csv"]), report["required_outputs"], ["artifact", "required_at", "fields"])
    update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": report["artifacts"]["summary_json"], "decision": report["decision"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
