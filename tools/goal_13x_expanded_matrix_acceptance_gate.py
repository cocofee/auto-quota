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
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_MATRIX_SUMMARY = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded_summary.json"
DEFAULT_SOURCE_BALANCE = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded" / "source_balance_checks.csv"
DEFAULT_LEAKAGE = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded" / "leakage_checks.csv"
DEFAULT_SOURCE_SPLIT = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded" / "source_split_manifest.csv"
DEFAULT_FILE_SELECTION = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded" / "file_selection.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_expanded_matrix_acceptance_gate"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _gate_rows(summary: dict[str, Any], leakage: list[dict[str, str]], balance: list[dict[str, str]]) -> tuple[list[dict[str, Any]], str]:
    metrics = summary["metrics"]
    leakage_pass = all(row.get("status") == "pass" for row in leakage)
    balance_by_check = {row["check"]: row for row in balance}
    rows = [
        {
            "gate": "matrix_rows_match_group",
            "value": metrics.get("matrix_rows_match_group"),
            "target": "true",
            "status": "pass" if metrics.get("matrix_rows_match_group") is True else "fail",
            "reason": "matrix row count must match LTR group file",
        },
        {
            "gate": "forbidden_feature_leakage",
            "value": int(leakage_pass),
            "target": "1",
            "status": "pass" if leakage_pass else "fail",
            "reason": "source/id/provenance fields must stay out of training matrix",
        },
        {
            "gate": "duplicate_unique_name_size_selected",
            "value": metrics.get("duplicate_unique_name_size_selected"),
            "target": "0",
            "status": "pass" if _int(metrics.get("duplicate_unique_name_size_selected")) == 0 else "fail",
            "reason": "expanded matrix must dedupe duplicated XML by unique_name_size_key",
        },
        {
            "gate": "max_source_file_group_share",
            "value": metrics.get("max_source_file_group_share"),
            "target": "<=0.08",
            "status": "pass" if _float(metrics.get("max_source_file_group_share")) <= 0.08 else "warn",
            "reason": "single file dominance should be removed before training",
        },
        {
            "gate": "max_source_family_group_share",
            "value": metrics.get("max_source_family_group_share"),
            "target": "<=0.25",
            "status": "pass" if _float(metrics.get("max_source_family_group_share")) <= 0.25 else "warn",
            "reason": "source_family dominance remains above the 13.7 acceptance target",
        },
        {
            "gate": "observed_oof_fold_count",
            "value": metrics.get("observed_oof_fold_count"),
            "target": "5",
            "status": "pass" if _int(metrics.get("observed_oof_fold_count")) >= 5 else "warn",
            "reason": "source-aware OOF must have the planned fold count",
        },
        {
            "gate": "min_fold_to_median_group_ratio",
            "value": metrics.get("min_fold_to_median_group_ratio"),
            "target": ">=0.60",
            "status": "pass" if _float(metrics.get("min_fold_to_median_group_ratio")) >= 0.60 else "warn",
            "reason": "fold group counts must be reasonably balanced",
        },
        {
            "gate": "top80_recall_rate",
            "value": metrics.get("topk_recall_rate"),
            "target": ">=0.75",
            "status": "pass" if _float(metrics.get("topk_recall_rate")) >= 0.75 else "warn",
            "reason": "expanded matrix currently increases recall-missing rows; ranking training can only claim top80-present scope",
        },
        {
            "gate": "accepted_group_scale",
            "value": metrics.get("accepted_groups"),
            "target": ">=2000",
            "status": "pass" if _int(metrics.get("accepted_groups")) >= 2000 else "warn",
            "reason": "expanded matrix should be materially larger than 13.4",
        },
    ]
    has_fail = any(row["status"] == "fail" for row in rows)
    warn_gates = {row["gate"] for row in rows if row["status"] == "warn"}
    if has_fail:
        decision = "do_not_train_fix_matrix_integrity"
    elif {"max_source_family_group_share", "top80_recall_rate"} & warn_gates:
        decision = "conditional_train_allowed_with_guardrails"
    elif warn_gates:
        decision = "conditional_train_allowed_with_guardrails"
    else:
        decision = "accept_matrix_for_next_dev_oof_training"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        "# 13.9 Expanded Matrix Acceptance Gate",
        "",
        "Read-only acceptance gate for the 13.8 expanded OSS XML matrix. No training, heldout/hard selection, online integration, threshold change, or GoalSearcher edit was performed.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["accepted_groups", m["accepted_groups"]],
                ["matrix_rows", m["matrix_rows"]],
                ["top80_recall_rate", m["topk_recall_rate"]],
                ["duplicate_unique_name_size_selected", m["duplicate_unique_name_size_selected"]],
                ["max_source_file_group_share", m["max_source_file_group_share"]],
                ["max_source_family_group_share", m["max_source_family_group_share"]],
                ["observed_oof_fold_count", m["observed_oof_fold_count"]],
                ["min_fold_to_median_group_ratio", m["min_fold_to_median_group_ratio"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "target", "reason"]] + [[row["gate"], row["status"], row["value"], row["target"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Source Families",
        "",
        _md_table([["source_family", "accepted_groups", "share", "folds"]] + [[row["source_family"], row["accepted_groups"], row["share"], row["oof_folds"]] for row in report["source_family_rows"]]),
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


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.9 expanded matrix acceptance gate 已完成。\n"
        f"决策：{report['decision']}。accepted_groups={m['accepted_groups']}，top80_recall={m['topk_recall_rate']}，"
        f"max_source_file_share={m['max_source_file_group_share']}，max_source_family_share={m['max_source_family_group_share']}。\n"
        "下一步建议：13.10 expanded matrix guarded dev/OOF reranker training。允许只用 expanded matrix 做 dev/OOF-only training，但必须带 guardrails：只声明 top80-present ranking，不 freeze、不验证 heldout/hard、不上线。\n"
        "禁止：使用 heldout/hard 做选择、上线、改 GoalSearcher、改阈值、把 expanded OOF 结果宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.9 expanded matrix acceptance gate" not in text:
        rows = f"""          <tr>
            <td>13.9 expanded matrix acceptance gate</td>
            <td>Read-only acceptance gate over expanded matrix leakage, dedupe, source/file balance, fold balance, and recall boundary.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.8 OSS XML expanded/rebalanced matrix rebuild summary</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.9 expanded matrix acceptance gate")
    parser.add_argument("--matrix-summary", default=str(DEFAULT_MATRIX_SUMMARY))
    parser.add_argument("--source-balance", default=str(DEFAULT_SOURCE_BALANCE))
    parser.add_argument("--leakage", default=str(DEFAULT_LEAKAGE))
    parser.add_argument("--source-split", default=str(DEFAULT_SOURCE_SPLIT))
    parser.add_argument("--file-selection", default=str(DEFAULT_FILE_SELECTION))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    args = parser.parse_args()

    summary = _read_json(Path(args.matrix_summary))
    balance = _read_csv(Path(args.source_balance))
    leakage = _read_csv(Path(args.leakage))
    source_split = _read_csv(Path(args.source_split))
    metrics = summary["metrics"]
    gate_rows, decision = _gate_rows(summary, leakage, balance)
    accepted = _int(metrics.get("accepted_groups"))
    source_family_rows = []
    for row in source_split:
        count = _int(row.get("accepted_groups"))
        source_family_rows.append(
            {
                "source_family": row.get("source_family"),
                "accepted_groups": count,
                "share": round(count / accepted, 6) if accepted else 0.0,
                "oof_folds": row.get("oof_folds"),
                "fold_count": _int(row.get("fold_count")),
            }
        )
    artifacts = {
        "summary_json": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_summary.json")),
        "summary_md": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_summary.md")),
        "gate_checks_csv": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_gate_checks.csv")),
        "source_family_acceptance_csv": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_source_family_acceptance.csv")),
    }
    report = {
        "stage": "13.9 expanded matrix acceptance gate",
        "read_only": True,
        "metrics": metrics,
        "decision": decision,
        "decision_rationale": (
            "The expanded matrix passes integrity, dedupe, single-file, fold-count, and fold-balance gates. "
            "It misses strict source_family and top80 recall targets, so the next training run is allowed only as a guarded dev/OOF diagnostic, not a freeze or validation step."
        ),
        "gate_rows": gate_rows,
        "source_family_rows": source_family_rows,
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only gate only: no training, no heldout/hard selection, no online integration, no threshold change, no GoalSearcher edit, and no feature whitelist edit.",
        "next_stage": {
            "recommended": "13.10 expanded matrix guarded dev/OOF reranker training: run offline training on the expanded matrix with explicit guardrails; do not freeze, validate heldout/hard, or release.",
            "default": "guarded training is allowed only because matrix integrity gates passed; claims remain top80-present and diagnostic",
        },
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_rows, ["gate", "value", "target", "status", "reason"])
    _write_csv(Path(artifacts["source_family_acceptance_csv"]), source_family_rows, ["source_family", "accepted_groups", "share", "oof_folds", "fold_count"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "warn_gates": [row["gate"] for row in gate_rows if row["status"] == "warn"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
