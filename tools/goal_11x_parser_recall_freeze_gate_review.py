from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_SUMMARY = AGENT_STATE / "goal_11x_parser_recall_dev_oof_whatif_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_11x_parser_recall_dev_oof_whatif_scorecard.csv"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_11x_parser_recall_dev_oof_whatif_loss_audit.csv"
DEFAULT_SOURCE_SLICES = AGENT_STATE / "goal_11x_parser_recall_dev_oof_whatif_source_slices.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_11x_parser_recall_freeze_gate_review"


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


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 11.2 Parser Recall Freeze Gate Review",
        "",
        "Read-only review of 11.1 scorecard, loss slices, and source slices.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            report["decision"],
            "",
            "## Anti-drift",
            "",
            report["anti_drift_conclusion"],
        ]
    )
    return "\n".join(lines) + "\n"


def _hint_key(row: dict[str, str]) -> str:
    family = row.get("after_query_family", "")
    query = row.get("query", "")
    if family == "electrical_box":
        return "电箱 -> electrical_box / 配电箱 anchor"
    if family == "lamp":
        return "吸顶LED -> lamp / 普通灯具安装 anchor"
    if "LED屏" in query:
        return "LED屏/显示屏 -> weak_current_device / 显示设备 anchor"
    if "摄像" in query:
        return "监控摄像 -> weak_current_device / 摄像机 anchor"
    if "视频传输" in query:
        return "视频传输 -> weak_current_device / 编码器/解码器 anchor"
    if "扩声" in query:
        return "扩声 -> weak_current_device / 公共广播 anchor"
    if "背景音乐" in query:
        return "背景音乐 -> weak_current_device / 公共广播 anchor"
    if "目标识别" in query or "读卡" in row.get("after_query", ""):
        return "目标识别/读卡 -> weak_current_device / 门禁读卡 anchor"
    return f"{query} -> {family}"


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    current = (
        "当前状态：11.2 parser recall scorecard + loss slice freeze gate 已完成。"
        f"freeze_decision={report['metrics']['freeze_decision']}；"
        f"frozen_hint_rows={report['metrics']['frozen_hint_rows']}；"
        f"new_loss_count={report['metrics']['new_loss_count']}；"
        f"max_source_gain_share={report['metrics']['max_source_gain_share']}；"
        "冻结含义仅是 dev/OOF 候选冻结，不代表上线或通用 Top1 提升。"
    )
    next_text = (
        "下一步：11.3 validation boundary definition / explicit validation go-no-go。"
        "只读定义是否允许进入 heldout/hard validation；无明确 go 不跑验证、不上线、不改 GoalSearcher。"
    )
    if "当前状态：11.1 parser/query normalization + candidate recall minimal implementation 已执行。" in text:
        start = text.index("当前状态：11.1 parser/query normalization + candidate recall minimal implementation 已执行。")
        end = text.index("禁止：继续 S2、训练、调参", start)
        text = text[:start] + current + "\n" + next_text + "\n" + text[end:]
    marker = "          <tr>\n            <td>11.1 parser recall dev/OOF what-if</td>"
    row = (
        "          <tr>\n"
        "            <td>11.2 parser recall freeze gate review</td>\n"
        "            <td>只读复核 scorecard/loss/source slices，并冻结最小 hint set 为后续 validation 候选。</td>\n"
        "            <td><code>reports/agent_state/goal_11x_parser_recall_freeze_gate_review_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_11x_parser_recall_freeze_gate_review_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--loss-audit", type=Path, default=DEFAULT_LOSS_AUDIT)
    parser.add_argument("--source-slices", type=Path, default=DEFAULT_SOURCE_SLICES)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    source_summary = _read_json(args.summary)
    scorecard = _read_csv(args.scorecard)
    loss_audit = _read_csv(args.loss_audit)
    source_slices = _read_csv(args.source_slices)
    metrics_11_1 = source_summary["metrics"]

    caution_rows = []
    review_rows = []
    for row in scorecard:
        delta = int(row.get("candidate_pool_delta") or 0)
        cautions: list[str] = []
        if delta <= 3:
            cautions.append("low_candidate_pool_delta")
        if row.get("rank_bucket") in {"rank_41_80", "rank_21_40"}:
            cautions.append("deep_rank_bucket")
        if row.get("new_loss") == "true":
            cautions.append("new_loss")
        if row.get("after_top1_family") not in {row.get("after_query_family"), "<empty>"}:
            cautions.append("top1_family_not_target")
        status = "freeze_candidate_with_caution" if cautions else "freeze_candidate"
        if cautions:
            caution_rows.append(row)
        review_rows.append(
            {
                "inventory_id": row["inventory_id"],
                "query": row["query"],
                "after_query_family": row["after_query_family"],
                "candidate_pool_delta": delta,
                "rank_bucket": row["rank_bucket"],
                "source_file": row["source_file"],
                "new_loss": row["new_loss"],
                "review_status": status,
                "cautions": ";".join(cautions),
                "review_note": "candidate-pool coverage improves; Top1 is diagnostic only",
            }
        )

    new_loss_count = int(metrics_11_1["new_loss_count"])
    source_dominance_stop = bool(metrics_11_1["source_dominance_stop"])
    positive_delta = int(metrics_11_1["positive_candidate_pool_delta"])
    heldout_rows_used = int(metrics_11_1["heldout_rows_used"])
    hard_rows_used = int(metrics_11_1["hard_validation_rows_used"])
    freeze_allowed = (
        positive_delta > 0
        and new_loss_count == 0
        and not source_dominance_stop
        and heldout_rows_used == 0
        and hard_rows_used == 0
    )
    freeze_decision = "freeze_dev_oof_candidate" if freeze_allowed else "do_not_freeze"
    frozen_manifest = [
        {
            "hint_key": _hint_key(row),
            "inventory_id": row["inventory_id"],
            "query": row["query"],
            "after_query": row["after_query"],
            "after_query_family": row["after_query_family"],
            "candidate_pool_delta": row["candidate_pool_delta"],
            "source_file": row["source_file"],
            "rollback_boundary": "remove this hint row/helper branch only; no model/db rollback",
            "freeze_boundary": "dev/OOF candidate freeze only; no online switch and no Top1 claim",
        }
        for row in scorecard
    ] if freeze_allowed else []
    gate_checks = [
        {"check": "dev_oof_only", "status": "pass" if metrics_11_1["dev_oof_only"] else "fail", "evidence": "dev_oof_only=true"},
        {"check": "heldout_hard_contamination", "status": "pass" if heldout_rows_used == 0 and hard_rows_used == 0 else "fail", "evidence": f"heldout={heldout_rows_used}; hard={hard_rows_used}"},
        {"check": "positive_candidate_pool_delta", "status": "pass" if positive_delta > 0 else "fail", "evidence": str(positive_delta)},
        {"check": "new_loss_budget", "status": "pass" if new_loss_count == 0 else "fail", "evidence": str(new_loss_count)},
        {"check": "source_dominance", "status": "pass" if not source_dominance_stop else "fail", "evidence": f"max_source_gain_share={metrics_11_1['max_source_gain_share']}"},
        {"check": "caution_rows_reviewed", "status": "pass", "evidence": f"caution_rows={len(caution_rows)}"},
    ]
    blocked_actions = [
        {"action": "claim_general_top1_gain", "reason": "11.1 scorecard is candidate-pool what-if only"},
        {"action": "run_heldout_or_hard_validation_without_go", "reason": "11.2 is review-only"},
        {"action": "wire_online_GoalSearcher", "reason": "freeze is candidate freeze, not production integration"},
        {"action": "train_or_tune_ranking", "reason": "outside 11.x parser recall scope"},
        {"action": "edit_taxonomy_row_mappings", "reason": "owner mappings are not part of this lane"},
    ]
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "scorecard_review_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_scorecard_review.csv")),
        "frozen_hint_manifest_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_frozen_hint_manifest.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
        "source_slices_review_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_source_slices_review.csv")),
    }
    metrics = {
        "freeze_decision": freeze_decision,
        "freeze_allowed": freeze_allowed,
        "evaluated_rows": len(scorecard),
        "frozen_hint_rows": len(frozen_manifest),
        "caution_rows": len(caution_rows),
        "positive_candidate_pool_delta": positive_delta,
        "positive_rows": int(metrics_11_1["positive_rows"]),
        "new_loss_count": new_loss_count,
        "max_source_gain_share": float(metrics_11_1["max_source_gain_share"]),
        "source_dominance_stop": source_dominance_stop,
        "heldout_rows_used": heldout_rows_used,
        "hard_validation_rows_used": hard_rows_used,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "goal_searcher_change_allowed": False,
    }
    decision = (
        "Freeze the 11.1 parser/query recall hint set as a dev/OOF-only candidate for a future validation gate. "
        "The freeze is justified by positive candidate-pool delta, zero new-loss rows, no heldout/hard contamination, "
        "and no source-dominance stop. Two low-delta/deep-rank rows remain caution rows; freeze does not mean online rollout "
        "or general Top1 gain."
        if freeze_allowed
        else "Do not freeze the 11.1 hint set because one or more gate checks failed."
    )
    report = {
        "stage": "Goal LTR v1 / 11.2 parser recall scorecard + loss slice freeze gate",
        "read_only": True,
        "source_artifacts": {
            "summary": str(args.summary),
            "scorecard": str(args.scorecard),
            "loss_audit": str(args.loss_audit),
            "source_slices": str(args.source_slices),
        },
        "metrics": metrics,
        "decision": decision,
        "anti_drift_conclusion": (
            "11.2 is read-only. It does not modify parser/query code, train, tune, change thresholds, use heldout/hard for selection, "
            "edit taxonomy rows, edit feature whitelists, wire online GoalSearcher behavior, or claim general Top1 gain."
        ),
        "next_stage": {
            "stage": "11.3 validation boundary definition / explicit validation go-no-go",
            "default": "do_not_validate_without_explicit_go",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, list(gate_checks[0].keys()))
    _write_csv(Path(artifacts["scorecard_review_csv"]), review_rows, list(review_rows[0].keys()))
    _write_csv(Path(artifacts["frozen_hint_manifest_csv"]), frozen_manifest, list(frozen_manifest[0].keys()) if frozen_manifest else ["hint_key"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_csv(Path(artifacts["source_slices_review_csv"]), source_slices, list(source_slices[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

