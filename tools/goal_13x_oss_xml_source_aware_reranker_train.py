from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from tools.goal_10x_offline_ranking_experiment_dev_oof_execute import (  # noqa: E402
    _candidate_features,
    _group_weights,
    _leakage_rows,
    _load_dev_matrix,
    _load_training_features,
    _md_table,
    _objective_params,
    _score_predictions,
    _take_groups,
    _write_csv,
    _write_json,
    _write_jsonl,
)


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DATA_DIR = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix"
DEFAULT_MATRIX_SUMMARY = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_summary.json"
DEFAULT_SCOPE_LOCK = AGENT_STATE / "goal_10x_offline_ranking_experiment_execution_scope_lock_summary.json"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_oss_xml_source_aware_reranker_dev_oof"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _source_fold_assignments(meta: list[dict[str, Any]]) -> list[tuple[int, list[int]]]:
    by_fold: dict[int, list[int]] = defaultdict(list)
    for idx, row in enumerate(meta):
        fold = int(row.get("oof_fold") or 0)
        by_fold[fold].append(idx)
    return [(fold, by_fold[fold]) for fold in sorted(by_fold)]


def _source_fold_checks(meta: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_file_folds: dict[str, set[int]] = defaultdict(set)
    source_family_folds: dict[str, set[int]] = defaultdict(set)
    for row in meta:
        fold = int(row.get("oof_fold") or 0)
        source_file_folds[str(row.get("source_file") or "")].add(fold)
        source_family_folds[str(row.get("source_family") or "")].add(fold)
    same_file_violations = {key: folds for key, folds in source_file_folds.items() if len(folds) > 1}
    return [
        {
            "check": "same_source_file_single_oof_fold",
            "value": len(same_file_violations),
            "status": "pass" if not same_file_violations else "fail",
            "details": "|".join(sorted(same_file_violations)[:20]),
        },
        {
            "check": "source_family_count",
            "value": len(source_family_folds),
            "status": "pass" if source_family_folds else "fail",
            "details": "",
        },
        {
            "check": "observed_oof_fold_count",
            "value": len({int(row.get("oof_fold") or 0) for row in meta}),
            "status": "pass" if len({int(row.get("oof_fold") or 0) for row in meta}) >= 2 else "fail",
            "details": "",
        },
    ]


def _train_source_oof_candidate(
    *,
    candidate: dict[str, Any],
    df,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    features: list[str],
    num_boost_round: int,
    seed: int,
) -> tuple[np.ndarray, list[dict[str, Any]], list[dict[str, Any]]]:
    preds = np.zeros(len(labels), dtype=np.float32)
    fold_rows: list[dict[str, Any]] = []
    importance_counter: Counter[str] = Counter()
    group_indices = list(range(len(groups)))
    fold_assignments = _source_fold_assignments(meta)
    params = _objective_params(candidate["objective_variant"], seed)
    for fold_value, valid_group_indices in fold_assignments:
        valid_set = set(valid_group_indices)
        train_group_indices = [idx for idx in group_indices if idx not in valid_set]
        if not train_group_indices or not valid_group_indices:
            continue
        train_df, train_y, train_groups, _ = _take_groups(df, labels, groups, train_group_indices)
        valid_df, _valid_y, valid_groups, valid_row_indices = _take_groups(df, labels, groups, valid_group_indices)
        train_weights = _group_weights(candidate["objective_variant"], train_y, train_groups)
        train_data = lgb.Dataset(
            train_df[features].astype(np.float32).to_numpy(),
            label=train_y,
            group=train_groups,
            weight=train_weights,
            feature_name=features,
            free_raw_data=False,
        )
        booster = lgb.train(
            params,
            train_data,
            num_boost_round=num_boost_round,
            valid_sets=[train_data],
            valid_names=["dev_train"],
            callbacks=[],
        )
        preds[valid_row_indices] = booster.predict(
            valid_df[features].astype(np.float32).to_numpy(),
            num_iteration=booster.current_iteration(),
        )
        for feature, gain in zip(features, booster.feature_importance(importance_type="gain"), strict=True):
            importance_counter[feature] += float(gain)
        valid_sources = sorted({str(meta[idx].get("source_family") or "") for idx in valid_group_indices})
        fold_rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "fold": fold_value,
                "train_groups": len(train_group_indices),
                "valid_groups": len(valid_group_indices),
                "train_rows": int(sum(train_groups)),
                "valid_rows": int(sum(valid_groups)),
                "valid_source_family_count": len(valid_sources),
                "valid_source_families": "|".join(valid_sources[:20]),
                "num_boost_round": num_boost_round,
                "feature_count": len(features),
            }
        )
    importance_rows = [
        {"candidate_id": candidate["candidate_id"], "feature": feature, "gain_sum": round(gain, 6)}
        for feature, gain in importance_counter.most_common(40)
    ]
    return preds, fold_rows, importance_rows


def _recall_boundary_rows(candidate_matrix: list[dict[str, Any]], groups: list[int], matrix_summary: dict[str, Any]) -> list[dict[str, Any]]:
    metrics = matrix_summary.get("metrics", {})
    eligible = int(metrics.get("eligible_pairs") or len(groups))
    missing = int(metrics.get("recall_gap_groups") or max(0, eligible - len(groups)))
    return [
        {
            "candidate_id": candidate["candidate_id"],
            "split": "dev",
            "top80_present_groups": len(groups),
            "top80_missing_groups": missing,
            "top80_recall_rate": round(len(groups) / eligible, 6) if eligible else 0.0,
            "ranking_claim_scope": "oss_xml_top80_present_source_oof_only",
            "recall_missing_claim": "unchanged_not_claimed",
        }
        for candidate in candidate_matrix
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    top_rows = report["top_candidates"][:10]
    lines = [
        f"# {report['stage']}",
        "",
        "Explicitly authorized OSS XML dev/OOF-only offline training. Heldout/hard are not used for selection, and no online GoalSearcher or ranking code is changed.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_count", metrics["candidate_count"]],
                ["group_count", metrics["group_count"]],
                ["matrix_rows", metrics["matrix_rows"]],
                ["observed_oof_folds", metrics["observed_oof_folds"]],
                ["best_candidate_id", metrics["best_candidate_id"]],
                ["best_hit1_net", metrics["best_hit1_net"]],
                ["best_hit1_gain", metrics["best_hit1_gain"]],
                ["best_hit1_loss", metrics["best_hit1_loss"]],
                ["best_candidate_hit1_rate", metrics["best_candidate_hit1_rate"]],
                ["heldout_used_for_selection", metrics["heldout_used_for_selection"]],
            ]
        ),
        "",
        "## Top Candidates",
        "",
        _md_table(
            [["rank", "candidate_id", "hit1_net", "hit1_gain", "hit1_loss", "candidate_hit1_rate", "approval_status"]]
            + [
                [
                    row["scorecard_rank"],
                    row["candidate_id"],
                    row["hit1_net"],
                    row["hit1_gain"],
                    row["hit1_loss"],
                    row["candidate_hit1_rate"],
                    row["approval_status"],
                ]
                for row in top_rows
            ]
        ),
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
        f"当前阶段：{report['stage']} 已完成。\n"
        f"结果：best={m['best_candidate_id']}，hit1_net={m['best_hit1_net']}，"
        f"gain={m['best_hit1_gain']}，loss={m['best_hit1_loss']}，"
        f"candidate_hit1_rate={m['best_candidate_hit1_rate']}。\n"
        f"下一步建议：{report['next_stage']['recommended']}。\n"
        "禁止：用 heldout/hard 做选择、上线、改 GoalSearcher、改阈值、把本轮 OOF 结果宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if f"{report['stage']} summary" not in text:
        rows = f"""          <tr>
            <td>{report['stage']} summary</td>
            <td>Dev/OOF-only offline reranker training on OSS XML matrix with source-aware folds.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
          <tr>
            <td>{report['stage']} audit artifacts</td>
            <td>Scorecard, loss slices, fold manifest, feature importance, flips, leakage, and recall boundary reports.</td>
            <td><code>{_safe_rel(report['artifacts']['candidate_scorecard_csv'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.4 OSS XML source-aware training matrix summary</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 13.5 OSS XML source-aware dev/OOF offline reranker training")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--matrix-summary", default=str(DEFAULT_MATRIX_SUMMARY))
    parser.add_argument("--scope-lock-summary", default=str(DEFAULT_SCOPE_LOCK))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--dev-oof-only", action="store_true", required=True)
    parser.add_argument("--no-heldout-selection", action="store_true", required=True)
    parser.add_argument("--emit-loss-audit", action="store_true", required=True)
    parser.add_argument("--emit-leakage-report", action="store_true", required=True)
    parser.add_argument("--num-boost-round", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20260526)
    args = parser.parse_args()

    started = time.perf_counter()
    is_expanded_guarded = "expanded" in str(args.output_prefix).lower() or "expanded" in str(args.data_dir).lower()
    stage = (
        "13.10 expanded matrix guarded dev/OOF reranker training"
        if is_expanded_guarded
        else "13.5 OSS XML source-aware dev/OOF offline reranker training"
    )
    data_dir = Path(args.data_dir)
    scope_lock = _read_json(Path(args.scope_lock_summary))
    matrix_summary = _read_json(Path(args.matrix_summary))
    candidate_matrix = list(scope_lock.get("candidate_matrix", []))
    if not candidate_matrix:
        raise ValueError("scope lock summary missing candidate_matrix")
    all_features = _load_training_features(data_dir / "ltr_feature_whitelist_oss_source_aware_v1.json")
    df, labels, groups, meta, feature_rows = _load_dev_matrix(data_dir, all_features)
    source_fold_rows = _source_fold_checks(meta)
    if any(row["status"] == "fail" for row in source_fold_rows):
        raise RuntimeError(f"source fold checks failed: {source_fold_rows}")

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_execution_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_execution_summary.md")),
        "candidate_scorecard_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_scorecard.csv")),
        "candidate_scorecard_json": str(output_prefix.with_name(output_prefix.name + "_candidate_scorecard.json")),
        "loss_audit_by_slice_csv": str(output_prefix.with_name(output_prefix.name + "_loss_audit_by_slice.csv")),
        "leakage_gate_report_json": str(output_prefix.with_name(output_prefix.name + "_leakage_gate_report.json")),
        "leakage_gate_report_csv": str(output_prefix.with_name(output_prefix.name + "_leakage_gate_report.csv")),
        "source_fold_report_csv": str(output_prefix.with_name(output_prefix.name + "_source_fold_report.csv")),
        "fallback_contract_report_csv": str(output_prefix.with_name(output_prefix.name + "_fallback_contract_report.csv")),
        "fallback_contract_report_md": str(output_prefix.with_name(output_prefix.name + "_fallback_contract_report.md")),
        "recall_boundary_report_csv": str(output_prefix.with_name(output_prefix.name + "_recall_boundary_report.csv")),
        "recall_boundary_report_json": str(output_prefix.with_name(output_prefix.name + "_recall_boundary_report.json")),
        "fold_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_fold_manifest.csv")),
        "feature_importance_csv": str(output_prefix.with_name(output_prefix.name + "_feature_importance.csv")),
        "hit1_flips_jsonl": str(output_prefix.with_name(output_prefix.name + "_hit1_flips.jsonl")),
    }

    leakage_rows, leakage_passed = _leakage_rows(candidate_matrix, all_features)
    _write_csv(Path(artifacts["leakage_gate_report_csv"]), leakage_rows, ["candidate_id", "feature_toggle", "feature_count", "excluded_feature_count", "forbidden_feature_present", "status", "decision"])
    _write_json(Path(artifacts["leakage_gate_report_json"]), {"leakage_gate_passed": leakage_passed, "candidate_count": len(candidate_matrix), "rows": leakage_rows})
    _write_csv(Path(artifacts["source_fold_report_csv"]), source_fold_rows, ["check", "value", "status", "details"])
    if not leakage_passed:
        raise RuntimeError("leakage gate failed; execution stopped before training")

    scorecard_rows: list[dict[str, Any]] = []
    loss_audit_rows: list[dict[str, Any]] = []
    fallback_rows: list[dict[str, Any]] = []
    flip_rows: list[dict[str, Any]] = []
    fold_manifest_rows: list[dict[str, Any]] = []
    feature_importance_rows: list[dict[str, Any]] = []

    for idx, candidate in enumerate(candidate_matrix, start=1):
        features = _candidate_features(candidate["feature_toggle"], all_features)
        preds, fold_rows, importance_rows = _train_source_oof_candidate(
            candidate=candidate,
            df=df,
            labels=labels,
            groups=groups,
            meta=meta,
            features=features,
            num_boost_round=args.num_boost_round,
            seed=args.seed + idx,
        )
        metrics, candidate_loss_rows, candidate_fallback_rows, candidate_flips = _score_predictions(
            candidate_id=candidate["candidate_id"],
            preds=preds,
            labels=labels,
            groups=groups,
            meta=meta,
            feature_rows=feature_rows,
        )
        loss_budget = max(18, round(len(groups) * 0.04))
        net_gate = max(20, round(len(groups) * 0.04))
        approval_status = "pass_dev_oof_candidate" if metrics["hit1_loss"] <= loss_budget and metrics["hit1_net"] > net_gate else "hold_for_review"
        scorecard_rows.append(
            {
                **candidate,
                "feature_count": len(features),
                **metrics,
                "loss_budget": loss_budget,
                "net_gate": net_gate,
                "loss_budget_pass": metrics["hit1_loss"] <= loss_budget,
                "net_gain_above_selected_gate": metrics["hit1_net"] > net_gate,
                "approval_status": approval_status,
                "selection_source": "expanded_matrix_guarded_dev_oof_only" if is_expanded_guarded else "oss_xml_source_aware_dev_oof_only",
                "heldout_used_for_selection": False,
            }
        )
        loss_audit_rows.extend(candidate_loss_rows)
        fallback_rows.extend(candidate_fallback_rows)
        flip_rows.extend(candidate_flips)
        fold_manifest_rows.extend(fold_rows)
        feature_importance_rows.extend(importance_rows)
        print(
            f"[{'13.10' if is_expanded_guarded else '13.5'}] {idx}/{len(candidate_matrix)} {candidate['candidate_id']} "
            f"net={metrics['hit1_net']} gain={metrics['hit1_gain']} loss={metrics['hit1_loss']}",
            file=sys.stderr,
        )

    scorecard_rows.sort(key=lambda row: (int(row["hit1_net"]), -int(row["hit1_loss"]), float(row["candidate_hit1_rate"])), reverse=True)
    for rank, row in enumerate(scorecard_rows, start=1):
        row["scorecard_rank"] = rank
    approved_rows = [row for row in scorecard_rows if row["approval_status"] == "pass_dev_oof_candidate"]
    best_row = scorecard_rows[0]
    recall_rows = _recall_boundary_rows(candidate_matrix, groups, matrix_summary)

    _write_csv(
        Path(artifacts["candidate_scorecard_csv"]),
        scorecard_rows,
        [
            "scorecard_rank",
            "candidate_id",
            "objective_variant",
            "feature_toggle",
            "role",
            "feature_count",
            "groups",
            "baseline_hit1",
            "candidate_hit1",
            "baseline_hit1_rate",
            "candidate_hit1_rate",
            "hit1_gain",
            "hit1_loss",
            "hit1_net",
            "baseline_hit5",
            "candidate_hit5",
            "baseline_hit5_rate",
            "candidate_hit5_rate",
            "hit5_gain",
            "hit5_loss",
            "hit5_net",
            "candidate_mrr",
            "loss_budget",
            "net_gate",
            "loss_budget_pass",
            "net_gain_above_selected_gate",
            "approval_status",
            "selection_source",
            "heldout_used_for_selection",
        ],
    )
    _write_json(Path(artifacts["candidate_scorecard_json"]), {"candidate_scorecard": scorecard_rows})
    _write_csv(Path(artifacts["loss_audit_by_slice_csv"]), loss_audit_rows, ["candidate_id", "slice_dimension", "slice_key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_csv(Path(artifacts["fallback_contract_report_csv"]), fallback_rows, ["candidate_id", "group_id", "baseline_hit1", "raw_candidate_hit1", "candidate_override", "override_outcome", "no_gate_relaxation"])
    _write_jsonl(Path(artifacts["hit1_flips_jsonl"]), flip_rows)
    _write_csv(Path(artifacts["recall_boundary_report_csv"]), recall_rows, ["candidate_id", "split", "top80_present_groups", "top80_missing_groups", "top80_recall_rate", "ranking_claim_scope", "recall_missing_claim"])
    _write_json(Path(artifacts["recall_boundary_report_json"]), {"recall_boundary": recall_rows})
    _write_csv(Path(artifacts["fold_manifest_csv"]), fold_manifest_rows, ["candidate_id", "fold", "train_groups", "valid_groups", "train_rows", "valid_rows", "valid_source_family_count", "valid_source_families", "num_boost_round", "feature_count"])
    _write_csv(Path(artifacts["feature_importance_csv"]), feature_importance_rows, ["candidate_id", "feature", "gain_sum"])
    Path(artifacts["fallback_contract_report_md"]).write_text(
        "\n".join(
            [
                f"# {stage} Fallback Contract Report",
                "",
                "No online fallback behavior is changed. This report records raw candidate override outcomes against baseline top1 order.",
                "",
                _md_table(
                    [
                        ["metric", "value"],
                        ["rows", len(fallback_rows)],
                        ["loss_rows", sum(1 for row in fallback_rows if row.get("override_outcome") == "loss")],
                        ["gain_rows", sum(1 for row in fallback_rows if row.get("override_outcome") == "gain")],
                    ]
                ),
            ]
        ),
        encoding="utf-8",
    )

    observed_folds = sorted({int(row.get("oof_fold") or 0) for row in meta})
    metrics = {
        "candidate_count": len(candidate_matrix),
        "group_count": len(groups),
        "matrix_rows": len(labels),
        "observed_oof_folds": "|".join(str(fold) for fold in observed_folds),
        "best_candidate_id": best_row["candidate_id"],
        "best_hit1_net": best_row["hit1_net"],
        "best_hit1_gain": best_row["hit1_gain"],
        "best_hit1_loss": best_row["hit1_loss"],
        "best_candidate_hit1_rate": best_row["candidate_hit1_rate"],
        "approval_candidate_count": len(approved_rows),
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "offline_training_executed": True,
        "goal_searcher_changed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": stage,
        "explicit_user_go": True,
        "dev_oof_only": True,
        "heldout_not_used_for_selection": True,
        "hard_not_used_for_selection": True,
        "offline_training_executed": True,
        "no_online_integration": True,
        "no_goal_searcher_change": True,
        "source_aware_folds": True,
        "guarded_diagnostic_only": is_expanded_guarded,
        "freeze_allowed": False if is_expanded_guarded else None,
        "validation_allowed": False if is_expanded_guarded else None,
        "metrics": metrics,
        "top_candidates": scorecard_rows[:10],
        "approval_candidates": approved_rows,
        "source_fold_checks": source_fold_rows,
        "artifacts": artifacts,
        "decision": (
            "Executed expanded matrix guarded dev/OOF reranker training as a diagnostic only. Evidence is limited to expanded OSS XML top80-present ranking scorecard, loss slices, source-fold checks, leakage report, fallback report, and recall-boundary report."
            if is_expanded_guarded
            else "Executed OSS XML source-aware dev/OOF offline reranker training. Evidence is limited to OSS XML dev/OOF scorecard, loss slices, source-fold checks, leakage report, fallback report, and recall-boundary report."
        ),
        "anti_drift_conclusion": (
            "This guarded execution trains offline OOF candidate models only from the 13.8 expanded OSS XML matrix. It does not freeze a candidate, does not use heldout/hard for selection, does not tune online thresholds, does not relax gates, does not edit the feature whitelist, does not patch rules, does not modify GoalSearcher, and does not connect online."
            if is_expanded_guarded
            else "This execution trains offline OOF candidate models only from the 13.4 OSS XML matrix. It does not use heldout/hard for selection, does not tune online thresholds, does not relax gates, does not edit the feature whitelist, does not patch rules, does not modify GoalSearcher, and does not connect online."
        ),
        "next_stage": {
            "recommended": (
                "13.11 expanded training scorecard comparison and robustness review"
                if is_expanded_guarded
                else "13.6 OSS XML reranker scorecard/loss/source robustness freeze gate"
            ),
            "default": "read-only review before any freeze, validation, or integration",
        },
    }
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics, "best_candidate": best_row}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
