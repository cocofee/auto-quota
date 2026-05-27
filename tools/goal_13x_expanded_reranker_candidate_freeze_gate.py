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
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_REVIEW = AGENT_STATE / "goal_13x_expanded_training_scorecard_robustness_review_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_candidate_scorecard.csv"
DEFAULT_FEATURE_IMPORTANCE = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_feature_importance.csv"
DEFAULT_RECALL = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_recall_boundary_report.csv"
DEFAULT_LEAKAGE = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_leakage_gate_report.csv"
DEFAULT_SOURCE_FOLD = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_source_fold_report.csv"
DEFAULT_FALLBACK_MD = AGENT_STATE / "goal_13x_expanded_matrix_guarded_reranker_dev_oof_fallback_contract_report.md"
DEFAULT_MATRIX_SUMMARY = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_expanded_reranker_candidate_freeze_gate"
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


def _int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _best_row(scorecard: list[dict[str, str]]) -> dict[str, str]:
    return sorted(scorecard, key=lambda row: _int(row.get("scorecard_rank")))[0]


def _feature_rows(feature_importance: list[dict[str, str]], candidate_id: str, limit: int = 15) -> list[dict[str, Any]]:
    rows = [
        {
            "candidate_id": row.get("candidate_id", ""),
            "feature": row.get("feature", ""),
            "gain_sum": _float(row.get("gain_sum")),
        }
        for row in feature_importance
        if row.get("candidate_id") == candidate_id
    ]
    rows.sort(key=lambda row: row["gain_sum"], reverse=True)
    return rows[:limit]


def _fallback_metrics(path: Path) -> dict[str, int]:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    metrics: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"\| ([a-zA-Z0-9_]+) \| ([0-9]+) \|", line.strip())
        if match:
            metrics[match.group(1)] = int(match.group(2))
    return metrics


def _gate_rows(
    *,
    review: dict[str, Any],
    best: dict[str, str],
    recall_rows: list[dict[str, str]],
    leakage_rows: list[dict[str, str]],
    source_fold_rows: list[dict[str, str]],
    matrix_summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    expanded = review["expanded_best"]
    candidate_id = expanded["best_candidate_id"]
    recall = next((row for row in recall_rows if row.get("candidate_id") == candidate_id), recall_rows[0] if recall_rows else {})
    leakage_pass = all(row.get("status") == "pass" for row in leakage_rows)
    source_fold_pass = all(row.get("status") == "pass" for row in source_fold_rows)
    top80_recall = _float(recall.get("top80_recall_rate"))
    matrix_source_family_share = _float(matrix_summary.get("metrics", {}).get("max_source_family_group_share"))
    rows = [
        {
            "gate": "lead_candidate_rank",
            "status": "pass" if _int(best.get("scorecard_rank")) == 1 and best.get("candidate_id") == candidate_id else "fail",
            "value": f"rank={best.get('scorecard_rank')}; candidate={best.get('candidate_id')}",
            "reason": "Freeze may only select the 13.10 lead candidate reviewed in 13.11.",
        },
        {
            "gate": "dev_oof_positive_net",
            "status": "pass" if expanded["hit1_net"] > 0 and expanded["hit1_gain"] > expanded["hit1_loss"] else "fail",
            "value": f"gain/loss/net={expanded['hit1_gain']}/{expanded['hit1_loss']}/{expanded['hit1_net']}",
            "reason": "Candidate must have positive dev/OOF hit1 net before freeze.",
        },
        {
            "gate": "loss_budget",
            "status": "pass" if expanded["hit1_loss_rate"] <= 0.02 else "warn",
            "value": expanded["hit1_loss_rate"],
            "reason": "Hit1 loss rate should stay under 2% of expanded groups.",
        },
        {
            "gate": "fold_robustness",
            "status": "pass" if expanded["min_fold_net"] > 0 and expanded["negative_fold_count"] == 0 else "fail",
            "value": f"min_fold_net={expanded['min_fold_net']}; negative_fold_count={expanded['negative_fold_count']}",
            "reason": "Freeze requires positive OOF net in every observed fold.",
        },
        {
            "gate": "source_file_concentration",
            "status": "pass" if expanded["max_source_file_net_share"] <= 0.15 else "warn",
            "value": expanded["max_source_file_net_share"],
            "reason": "Single-file net dominance must be low enough for candidate freeze.",
        },
        {
            "gate": "source_family_net_concentration",
            "status": "pass" if expanded["max_source_family_net_share"] <= 0.35 else "warn",
            "value": expanded["max_source_family_net_share"],
            "reason": "Candidate net should not be dominated by one source_family.",
        },
        {
            "gate": "candidate_stability",
            "status": "pass" if review["metrics"]["top10_overlap_count"] >= 6 else "warn",
            "value": review["metrics"]["top10_overlap_count"],
            "reason": "Top candidate family should be stable across 13.5 and 13.10.",
        },
        {
            "gate": "leakage_boundary",
            "status": "pass" if leakage_pass else "fail",
            "value": leakage_pass,
            "reason": "Forbidden source/id/provenance fields must not enter training features.",
        },
        {
            "gate": "source_fold_boundary",
            "status": "pass" if source_fold_pass else "fail",
            "value": source_fold_pass,
            "reason": "Source-aware fold checks must pass before freeze.",
        },
        {
            "gate": "top80_present_scope_recorded",
            "status": "warn" if top80_recall < 0.75 else "pass",
            "value": top80_recall,
            "reason": "Low recall does not block ranking freeze, but it limits claims to top80-present scope.",
        },
        {
            "gate": "expanded_matrix_guardrail_recorded",
            "status": "warn" if matrix_source_family_share > 0.25 else "pass",
            "value": matrix_source_family_share,
            "reason": "Matrix source_family share remains a validation risk and must travel with the frozen candidate.",
        },
        {
            "gate": "no_validation_or_release_in_this_gate",
            "status": "pass",
            "value": "no heldout/hard; no online release",
            "reason": "13.12 is freeze-only and cannot validate, tune thresholds, or integrate online.",
        },
    ]
    has_fail = any(row["status"] == "fail" for row in rows)
    if has_fail:
        decision = "do_not_freeze_candidate"
    else:
        decision = "freeze_candidate_for_future_explicit_validation_go_no_go"
    return rows, decision


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        "# 13.12 Expanded Reranker Candidate Freeze Gate",
        "",
        "Read-only freeze gate for the 13.10 expanded OSS XML dev/OOF lead candidate. No training, heldout/hard validation, online integration, threshold change, or GoalSearcher edit was performed.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Frozen Candidate",
        "",
        _md_table(
            [
                ["field", "value"],
                ["candidate_id", report["frozen_candidate"]["candidate_id"]],
                ["objective_variant", report["frozen_candidate"]["objective_variant"]],
                ["feature_toggle", report["frozen_candidate"]["feature_toggle"]],
                ["groups", m["groups"]],
                ["hit1 gain/loss/net", f"{m['hit1_gain']}/{m['hit1_loss']}/{m['hit1_net']}"],
                ["hit1_loss_rate", m["hit1_loss_rate"]],
                ["min_fold_net", m["min_fold_net"]],
                ["max_source_file_net_share", m["max_source_file_net_share"]],
                ["max_source_family_net_share", m["max_source_family_net_share"]],
                ["top80_recall_rate", m["top80_recall_rate"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Top Features",
        "",
        _md_table([["feature", "gain_sum"]] + [[row["feature"], round(row["gain_sum"], 6)] for row in report["top_features"][:12]]),
        "",
        "## Freeze Contract",
        "",
        _md_table([["item", "value"]] + [[row["item"], row["value"]] for row in report["freeze_contract"]]),
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
        "当前阶段：13.12 expanded reranker candidate freeze gate 已完成。\n"
        f"结论：{report['decision']}。frozen_candidate={report['frozen_candidate']['candidate_id']}，hit1_net={m['hit1_net']}，loss={m['hit1_loss']}，"
        f"min_fold_net={m['min_fold_net']}，max_source_file_net_share={m['max_source_file_net_share']}，max_source_family_net_share={m['max_source_family_net_share']}。\n"
        "下一步建议：13.13 validation boundary / explicit validation go-no-go。只读定义是否允许对 frozen candidate 跑 heldout/hard A/B validation；默认无明确 go 就不跑。\n"
        "禁止：在 13.13 前跑 heldout/hard、重新选择候选、重新训练、上线、改 GoalSearcher、改阈值、把 OSS XML dev/OOF freeze 宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.12 expanded reranker candidate freeze gate" not in text:
        rows = f"""          <tr>
            <td>13.12 expanded reranker candidate freeze gate</td>
            <td>Read-only freeze gate for the 13.10 expanded OSS XML lead candidate and future validation contract.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.11 expanded training scorecard comparison and robustness review</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.12 read-only expanded reranker candidate freeze gate")
    parser.add_argument("--review-summary", default=str(DEFAULT_REVIEW))
    parser.add_argument("--scorecard", default=str(DEFAULT_SCORECARD))
    parser.add_argument("--feature-importance", default=str(DEFAULT_FEATURE_IMPORTANCE))
    parser.add_argument("--recall-report", default=str(DEFAULT_RECALL))
    parser.add_argument("--leakage-report", default=str(DEFAULT_LEAKAGE))
    parser.add_argument("--source-fold-report", default=str(DEFAULT_SOURCE_FOLD))
    parser.add_argument("--fallback-md", default=str(DEFAULT_FALLBACK_MD))
    parser.add_argument("--matrix-summary", default=str(DEFAULT_MATRIX_SUMMARY))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    args = parser.parse_args()

    review = _read_json(Path(args.review_summary))
    scorecard = _read_csv(Path(args.scorecard))
    feature_importance = _read_csv(Path(args.feature_importance))
    recall_rows = _read_csv(Path(args.recall_report))
    leakage_rows = _read_csv(Path(args.leakage_report))
    source_fold_rows = _read_csv(Path(args.source_fold_report))
    matrix_summary = _read_json(Path(args.matrix_summary))
    best = _best_row(scorecard)
    candidate_id = review["expanded_best"]["best_candidate_id"]
    gate_rows, decision = _gate_rows(
        review=review,
        best=best,
        recall_rows=recall_rows,
        leakage_rows=leakage_rows,
        source_fold_rows=source_fold_rows,
        matrix_summary=matrix_summary,
    )
    top_features = _feature_rows(feature_importance, candidate_id)
    fallback_metrics = _fallback_metrics(Path(args.fallback_md))
    recall = next((row for row in recall_rows if row.get("candidate_id") == candidate_id), recall_rows[0] if recall_rows else {})
    expanded = review["expanded_best"]
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "frozen_candidate_manifest_json": str(output_prefix.with_name(output_prefix.name + "_frozen_candidate_manifest.json")),
        "frozen_candidate_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_frozen_candidate_manifest.csv")),
        "top_features_csv": str(output_prefix.with_name(output_prefix.name + "_top_features.csv")),
    }
    frozen_candidate = {
        "candidate_id": candidate_id,
        "objective_variant": best.get("objective_variant", ""),
        "feature_toggle": best.get("feature_toggle", ""),
        "selection_source": best.get("selection_source", ""),
        "frozen_from_stage": "13.10 expanded matrix guarded dev/OOF reranker training",
        "frozen_by_gate": "13.12 expanded reranker candidate freeze gate",
        "freeze_scope": "OSS XML dev/OOF top80-present reranker candidate only",
        "not_a_release": True,
        "not_validated_on_heldout_or_hard": True,
    }
    freeze_contract = [
        {"item": "allowed_next", "value": "13.13 read-only validation boundary / explicit validation go-no-go"},
        {"item": "candidate_selection", "value": "fixed to the frozen 13.10 lead candidate; no candidate reselection without a new gate"},
        {"item": "validation_boundary", "value": "heldout/hard A/B may run only after explicit user go"},
        {"item": "claim_scope", "value": "OSS XML dev/OOF top80-present ranking signal only"},
        {"item": "release_boundary", "value": "no online integration, no GoalSearcher edit, no threshold change in this freeze"},
        {"item": "known_warnings", "value": "top80_recall_rate below 0.75; expanded matrix source_family share above 0.25"},
    ]
    metrics = {
        "groups": expanded["groups"],
        "matrix_rows": expanded["matrix_rows"],
        "candidate_id": candidate_id,
        "hit1_gain": expanded["hit1_gain"],
        "hit1_loss": expanded["hit1_loss"],
        "hit1_net": expanded["hit1_net"],
        "hit1_loss_rate": expanded["hit1_loss_rate"],
        "hit1_net_rate": expanded["hit1_net_rate"],
        "candidate_hit1_rate": expanded["candidate_hit1_rate"],
        "min_fold_net": expanded["min_fold_net"],
        "negative_fold_count": expanded["negative_fold_count"],
        "max_source_file_net_share": expanded["max_source_file_net_share"],
        "max_source_family_net_share": expanded["max_source_family_net_share"],
        "top80_recall_rate": _float(recall.get("top80_recall_rate")),
        "top80_missing_groups": _int(recall.get("top80_missing_groups")),
        "fallback_gain_rows": fallback_metrics.get("gain_rows", 0),
        "fallback_loss_rows": fallback_metrics.get("loss_rows", 0),
    }
    report = {
        "stage": "13.12 expanded reranker candidate freeze gate",
        "read_only": True,
        "decision": decision,
        "metrics": metrics,
        "frozen_candidate": frozen_candidate,
        "gate_rows": gate_rows,
        "top_features": top_features,
        "fallback_metrics": fallback_metrics,
        "freeze_contract": freeze_contract,
        "artifacts": artifacts,
        "decision_rationale": (
            "The 13.10 lead candidate passes freeze checks on positive dev/OOF net, loss budget, fold robustness, source/file concentration, leakage boundary, and source-fold boundary. "
            "The freeze is scoped: it does not validate on heldout/hard and does not authorize release."
        ),
        "anti_drift_conclusion": "Read-only freeze gate only: no training, no matrix rebuild, no heldout/hard validation, no online integration, no threshold change, no GoalSearcher edit, and no feature whitelist edit.",
        "next_stage": {
            "recommended": "13.13 validation boundary / explicit validation go-no-go: read-only define whether to run heldout/hard A/B validation for the frozen candidate; default without explicit go is do_not_validate.",
            "default": "do not run heldout/hard validation yet",
        },
    }
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_rows, ["gate", "status", "value", "reason"])
    _write_csv(Path(artifacts["frozen_candidate_manifest_csv"]), [frozen_candidate], list(frozen_candidate.keys()))
    _write_json(Path(artifacts["frozen_candidate_manifest_json"]), frozen_candidate)
    _write_csv(Path(artifacts["top_features_csv"]), top_features, ["candidate_id", "feature", "gain_sum"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "frozen_candidate": frozen_candidate, "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
