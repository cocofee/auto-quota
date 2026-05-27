from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_EXECUTION_SUMMARY = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_execution_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_candidate_scorecard.csv"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_loss_audit_by_slice.csv"
DEFAULT_FLIPS = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_hit1_flips.jsonl"
DEFAULT_GROUP_META = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix" / "ltr_group_dev.jsonl"
DEFAULT_SOURCE_FOLD = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_source_fold_report.csv"
DEFAULT_LEAKAGE = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_leakage_gate_report.csv"
DEFAULT_RECALL = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof_recall_boundary_report.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_oss_xml_reranker_freeze_gate_review"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return int(float(row.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _float(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0.0


def _top_scorecard_rows(scorecard: list[dict[str, str]], n: int = 10) -> list[dict[str, Any]]:
    rows = sorted(scorecard, key=lambda row: _int(row, "scorecard_rank"))[:n]
    return [
        {
            "rank": _int(row, "scorecard_rank"),
            "candidate_id": row["candidate_id"],
            "hit1_net": _int(row, "hit1_net"),
            "hit1_gain": _int(row, "hit1_gain"),
            "hit1_loss": _int(row, "hit1_loss"),
            "candidate_hit1_rate": _float(row, "candidate_hit1_rate"),
            "approval_status": row.get("approval_status", ""),
        }
        for row in rows
    ]


def _aggregate_group_stats(best_candidate: str, group_meta: list[dict[str, Any]], flips: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_gid = {str(row.get("group_id")): row for row in group_meta}
    candidate_hit1_by_gid: dict[str, bool] = {}
    baseline_hit1_by_gid: dict[str, bool] = {}
    for row in group_meta:
        gid = str(row.get("group_id"))
        baseline = int(row.get("positive_rank") or 0) == 1
        baseline_hit1_by_gid[gid] = baseline
        candidate_hit1_by_gid[gid] = baseline
    for flip in flips:
        if flip.get("candidate_id") != best_candidate:
            continue
        gid = str(flip.get("group_id"))
        if gid in candidate_hit1_by_gid:
            candidate_hit1_by_gid[gid] = bool(flip.get("candidate_hit1"))

    dimensions = {
        "source_family": lambda row: str(row.get("source_family") or "<empty>"),
        "source_file": lambda row: str(row.get("source_file") or "<empty>"),
        "province": lambda row: str(row.get("province") or "<empty>"),
        "oof_fold": lambda row: str(row.get("oof_fold") or "0"),
        "query_family": lambda row: str(row.get("query_family") or "<empty>"),
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for dim, getter in dimensions.items():
        acc: dict[str, dict[str, int]] = defaultdict(lambda: {"groups": 0, "baseline_hit1": 0, "candidate_hit1": 0, "gain": 0, "loss": 0, "net": 0})
        for gid, row in by_gid.items():
            key = getter(row)
            baseline = baseline_hit1_by_gid[gid]
            candidate = candidate_hit1_by_gid[gid]
            item = acc[key]
            item["groups"] += 1
            item["baseline_hit1"] += int(baseline)
            item["candidate_hit1"] += int(candidate)
            item["gain"] += int((not baseline) and candidate)
            item["loss"] += int(baseline and not candidate)
            item["net"] += int((not baseline) and candidate) - int(baseline and not candidate)
        rows = []
        for key, values in acc.items():
            groups = values["groups"]
            rows.append(
                {
                    "dimension": dim,
                    "key": key,
                    **values,
                    "baseline_hit1_rate": round(values["baseline_hit1"] / groups, 6) if groups else 0.0,
                    "candidate_hit1_rate": round(values["candidate_hit1"] / groups, 6) if groups else 0.0,
                }
            )
        rows.sort(key=lambda row: (int(row["net"]), int(row["groups"])), reverse=True)
        out[dim] = rows
    return out


def _loss_rows_for_candidate(loss_audit: list[dict[str, str]], best_candidate: str) -> list[dict[str, Any]]:
    rows = []
    for row in loss_audit:
        if row.get("candidate_id") != best_candidate:
            continue
        rows.append(
            {
                "slice_dimension": row.get("slice_dimension", ""),
                "slice_key": row.get("slice_key", ""),
                "groups": _int(row, "groups"),
                "baseline_hit1": _int(row, "baseline_hit1"),
                "candidate_hit1": _int(row, "candidate_hit1"),
                "gain": _int(row, "gain"),
                "loss": _int(row, "loss"),
                "net": _int(row, "net"),
                "baseline_hit1_rate": _float(row, "baseline_hit1_rate"),
                "candidate_hit1_rate": _float(row, "candidate_hit1_rate"),
            }
        )
    rows.sort(key=lambda row: (row["loss"], row["groups"]), reverse=True)
    return rows


def _gate_rows(
    *,
    best: dict[str, str],
    execution_summary: dict[str, Any],
    source_fold_rows: list[dict[str, str]],
    leakage_rows: list[dict[str, str]],
    recall_rows: list[dict[str, str]],
    agg: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], str]:
    best_net = _int(best, "hit1_net")
    best_loss = _int(best, "hit1_loss")
    loss_budget = _int(best, "loss_budget")
    source_file_rows = agg["source_file"]
    source_family_rows = agg["source_family"]
    fold_rows = agg["oof_fold"]
    max_file_net = max([row["net"] for row in source_file_rows] or [0])
    max_family_net = max([row["net"] for row in source_family_rows] or [0])
    max_file_share = round(max_file_net / best_net, 6) if best_net > 0 else 1.0
    max_family_share = round(max_family_net / best_net, 6) if best_net > 0 else 1.0
    min_fold_net = min([row["net"] for row in fold_rows] or [0])
    observed_fold_count = len(fold_rows)
    leakage_pass = all(row.get("status") == "pass" for row in leakage_rows)
    source_fold_pass = all(row.get("status") == "pass" for row in source_fold_rows)
    recall = next((row for row in recall_rows if row.get("candidate_id") == best.get("candidate_id")), recall_rows[0] if recall_rows else {})
    top80_recall = _float(recall, "top80_recall_rate")

    rows = [
        {
            "gate": "dev_oof_positive_net",
            "value": best_net,
            "status": "pass" if best_net > 0 else "fail",
            "reason": "best candidate has positive source-aware OOF net",
        },
        {
            "gate": "loss_budget",
            "value": f"{best_loss}/{loss_budget}",
            "status": "pass" if loss_budget and best_loss <= loss_budget else "fail",
            "reason": "hit1 losses remain within stage budget",
        },
        {
            "gate": "leakage_gate",
            "value": int(leakage_pass),
            "status": "pass" if leakage_pass else "fail",
            "reason": "no forbidden source/id/provenance feature in candidate training features",
        },
        {
            "gate": "source_fold_integrity",
            "value": int(source_fold_pass),
            "status": "pass" if source_fold_pass else "fail",
            "reason": "same source_file is not split across OOF folds",
        },
        {
            "gate": "observed_fold_count",
            "value": observed_fold_count,
            "status": "pass" if observed_fold_count >= 4 else "warn",
            "reason": "source-aware OOF has enough validation folds for a first audit",
        },
        {
            "gate": "source_file_net_concentration",
            "value": max_file_share,
            "status": "pass" if max_file_share <= 0.35 else "warn",
            "reason": "single source_file should not explain too much of net gain before freeze",
        },
        {
            "gate": "source_family_net_concentration",
            "value": max_family_share,
            "status": "pass" if max_family_share <= 0.45 else "warn",
            "reason": "single source_family should not dominate net gain before freeze",
        },
        {
            "gate": "fold_min_net",
            "value": min_fold_net,
            "status": "pass" if min_fold_net > 0 else "warn",
            "reason": "each observed OOF fold should be non-negative before freeze",
        },
        {
            "gate": "recall_boundary",
            "value": top80_recall,
            "status": "pass" if top80_recall >= 0.75 else "warn",
            "reason": "claim is limited to top80-present ranking; recall missing is not fixed",
        },
    ]
    has_fail = any(row["status"] == "fail" for row in rows)
    has_warn = any(row["status"] == "warn" for row in rows)
    if has_fail:
        decision = "do_not_freeze_stop_for_gate_failure"
    elif has_warn:
        decision = "do_not_freeze_yet_expand_or_rebalance_matrix"
    else:
        decision = "freeze_candidate_for_future_validation_review"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 13.6 OSS XML Reranker Freeze Gate Review",
        "",
        "Read-only review of the 13.5 dev/OOF scorecard, loss slices, source/fold robustness, and recall boundary. No training, validation, integration, threshold change, or GoalSearcher edit was performed.",
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
                ["best_candidate_id", metrics["best_candidate_id"]],
                ["best_hit1_net", metrics["best_hit1_net"]],
                ["best_hit1_gain", metrics["best_hit1_gain"]],
                ["best_hit1_loss", metrics["best_hit1_loss"]],
                ["best_candidate_hit1_rate", metrics["best_candidate_hit1_rate"]],
                ["max_source_file_net_share", metrics["max_source_file_net_share"]],
                ["max_source_family_net_share", metrics["max_source_family_net_share"]],
                ["min_fold_net", metrics["min_fold_net"]],
                ["top80_recall_rate", metrics["top80_recall_rate"]],
            ]
        ),
        "",
        "## Gate Results",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Top Source Files",
        "",
        _md_table([["source_file", "groups", "gain", "loss", "net"]] + [[row["key"], row["groups"], row["gain"], row["loss"], row["net"]] for row in report["top_source_file_rows"][:10]]),
        "",
        "## Top Source Families",
        "",
        _md_table([["source_family", "groups", "gain", "loss", "net"]] + [[row["key"], row["groups"], row["gain"], row["loss"], row["net"]] for row in report["top_source_family_rows"][:10]]),
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
        "当前阶段：13.6 OSS XML reranker scorecard/loss/source robustness freeze gate 已完成。\n"
        f"决策：{report['decision']}。best={m['best_candidate_id']}，hit1_net={m['best_hit1_net']}，"
        f"loss={m['best_hit1_loss']}，max_source_file_net_share={m['max_source_file_net_share']}，"
        f"max_source_family_net_share={m['max_source_family_net_share']}。\n"
        "下一步建议：13.7 OSS XML matrix expansion/rebalance plan，先扩量和重平衡 source/province/fold，再重跑训练；暂不 freeze、不跑 heldout/hard validation、不上线。\n"
        "禁止：把 13.5 OOF 结果直接宣称为通用 Top1 提升、使用 heldout/hard 做选择、改 GoalSearcher、改阈值、接线上。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.6 OSS XML reranker freeze gate review" not in text:
        rows = f"""          <tr>
            <td>13.6 OSS XML reranker freeze gate review</td>
            <td>Read-only freeze gate over scorecard, loss slices, source/fold robustness, concentration, and recall boundary.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.5 OSS XML source-aware reranker summary</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.6 read-only OSS XML reranker freeze gate review")
    parser.add_argument("--execution-summary", default=str(DEFAULT_EXECUTION_SUMMARY))
    parser.add_argument("--scorecard", default=str(DEFAULT_SCORECARD))
    parser.add_argument("--loss-audit", default=str(DEFAULT_LOSS_AUDIT))
    parser.add_argument("--flips", default=str(DEFAULT_FLIPS))
    parser.add_argument("--group-meta", default=str(DEFAULT_GROUP_META))
    parser.add_argument("--source-fold-report", default=str(DEFAULT_SOURCE_FOLD))
    parser.add_argument("--leakage-report", default=str(DEFAULT_LEAKAGE))
    parser.add_argument("--recall-report", default=str(DEFAULT_RECALL))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    args = parser.parse_args()

    execution_summary = _read_json(Path(args.execution_summary))
    scorecard = _read_csv(Path(args.scorecard))
    loss_audit = _read_csv(Path(args.loss_audit))
    flips = _read_jsonl(Path(args.flips))
    group_meta = _read_jsonl(Path(args.group_meta))
    source_fold_rows = _read_csv(Path(args.source_fold_report))
    leakage_rows = _read_csv(Path(args.leakage_report))
    recall_rows = _read_csv(Path(args.recall_report))

    best = sorted(scorecard, key=lambda row: _int(row, "scorecard_rank"))[0]
    best_candidate = best["candidate_id"]
    agg = _aggregate_group_stats(best_candidate, group_meta, flips)
    gate_rows, decision = _gate_rows(
        best=best,
        execution_summary=execution_summary,
        source_fold_rows=source_fold_rows,
        leakage_rows=leakage_rows,
        recall_rows=recall_rows,
        agg=agg,
    )
    best_net = _int(best, "hit1_net")
    max_source_file_net = max([row["net"] for row in agg["source_file"]] or [0])
    max_source_family_net = max([row["net"] for row in agg["source_family"]] or [0])
    recall = next((row for row in recall_rows if row.get("candidate_id") == best_candidate), recall_rows[0] if recall_rows else {})
    top_loss_rows = _loss_rows_for_candidate(loss_audit, best_candidate)[:30]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "source_concentration_csv": str(output_prefix.with_name(output_prefix.name + "_source_concentration.csv")),
        "loss_focus_csv": str(output_prefix.with_name(output_prefix.name + "_loss_focus.csv")),
        "top_scorecard_csv": str(output_prefix.with_name(output_prefix.name + "_top_scorecard.csv")),
    }
    source_rows = []
    for dimension in ("source_family", "source_file", "province", "oof_fold", "query_family"):
        source_rows.extend(agg[dimension])
    report = {
        "stage": "13.6 OSS XML reranker scorecard/loss/source robustness freeze gate",
        "read_only": True,
        "metrics": {
            "best_candidate_id": best_candidate,
            "best_hit1_net": best_net,
            "best_hit1_gain": _int(best, "hit1_gain"),
            "best_hit1_loss": _int(best, "hit1_loss"),
            "best_candidate_hit1_rate": _float(best, "candidate_hit1_rate"),
            "max_source_file_net": max_source_file_net,
            "max_source_file_net_share": round(max_source_file_net / best_net, 6) if best_net > 0 else 1.0,
            "max_source_family_net": max_source_family_net,
            "max_source_family_net_share": round(max_source_family_net / best_net, 6) if best_net > 0 else 1.0,
            "min_fold_net": min([row["net"] for row in agg["oof_fold"]] or [0]),
            "top80_recall_rate": _float(recall, "top80_recall_rate"),
            "top80_missing_groups": _int(recall, "top80_missing_groups"),
            "candidate_count": len(scorecard),
        },
        "decision": decision,
        "decision_rationale": (
            "The candidate is a strong diagnostic lead, but freeze is held because source/file and source_family concentration remain too high for a robust freeze. "
            "The next useful action is to expand/rebalance OSS XML matrix coverage before another source-aware training run."
        ),
        "gate_rows": gate_rows,
        "top_candidates": _top_scorecard_rows(scorecard, 10),
        "top_source_file_rows": agg["source_file"][:15],
        "top_source_family_rows": agg["source_family"][:15],
        "top_loss_rows": top_loss_rows,
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only review only: no training, no validation, no heldout/hard selection, no online integration, no threshold change, no GoalSearcher edit, and no feature whitelist edit.",
        "next_stage": {
            "recommended": "13.7 OSS XML matrix expansion/rebalance plan: expand source/province coverage, rebalance OOF folds, reduce single-file/source-family dominance, then rerun dev/OOF training.",
            "default": "do not freeze/release 13.5 candidate yet",
        },
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_rows, ["gate", "value", "status", "reason"])
    _write_csv(Path(artifacts["source_concentration_csv"]), source_rows, ["dimension", "key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_csv(Path(artifacts["loss_focus_csv"]), top_loss_rows, ["slice_dimension", "slice_key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_csv(Path(artifacts["top_scorecard_csv"]), report["top_candidates"], ["rank", "candidate_id", "hit1_net", "hit1_gain", "hit1_loss", "candidate_hit1_rate", "approval_status"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
