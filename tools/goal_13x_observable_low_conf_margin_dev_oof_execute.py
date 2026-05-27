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

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from tools.goal_13x_top1_loss_guarded_dev_oof_execute import (  # noqa: E402
    _candidate_features,
    _clean,
    _first_positive_rank,
    _float,
    _int,
    _load_dev_matrix,
    _load_training_features,
    _md_table,
    _rank_bucket,
    _read_csv,
    _safe_rel,
    _source_fold_rows,
    _top1_margin,
    _train_source_oof_candidate,
    _write_csv,
    _write_json,
    _write_jsonl,
)

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DATA_DIR = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded"
DEFAULT_PLAN = AGENT_STATE / "goal_13x_observable_low_conf_margin_rewrite_plan_candidate_matrix.csv"
DEFAULT_FEATURE_WHITELIST = DEFAULT_DATA_DIR / "ltr_feature_whitelist_oss_source_aware_v1.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_observable_low_conf_margin_dev_oof"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _top1_stats(feature_rows: list[dict[str, Any]], groups: list[int]) -> dict[str, Any]:
    confidence_values: list[float] = []
    margin_values: list[float] = []
    reason_count_values: list[float] = []
    start = 0
    for size in groups:
        stop = start + size
        group_rows = feature_rows[start:stop]
        if group_rows:
            confidence_values.append(_float(group_rows[0].get("confidence")))
            margin_values.append(_top1_margin(group_rows))
            reason_count_values.append(_float(group_rows[0].get("reason_count")))
        start = stop
    conf = np.array(confidence_values, dtype=np.float64)
    margin = np.array(margin_values, dtype=np.float64)
    reason = np.array(reason_count_values, dtype=np.float64)
    return {
        "group_count": len(confidence_values),
        "confidence_min": round(float(np.min(conf)), 6) if len(conf) else 0.0,
        "confidence_q25": round(float(np.quantile(conf, 0.25)), 6) if len(conf) else 0.0,
        "confidence_q35": round(float(np.quantile(conf, 0.35)), 6) if len(conf) else 0.0,
        "confidence_median": round(float(np.quantile(conf, 0.50)), 6) if len(conf) else 0.0,
        "confidence_max": round(float(np.max(conf)), 6) if len(conf) else 0.0,
        "margin_min": round(float(np.min(margin)), 6) if len(margin) else 0.0,
        "margin_q25": round(float(np.quantile(margin, 0.25)), 6) if len(margin) else 0.0,
        "margin_q35": round(float(np.quantile(margin, 0.35)), 6) if len(margin) else 0.0,
        "margin_median": round(float(np.quantile(margin, 0.50)), 6) if len(margin) else 0.0,
        "margin_max": round(float(np.max(margin)), 6) if len(margin) else 0.0,
        "reason_count_q25": round(float(np.quantile(reason, 0.25)), 6) if len(reason) else 0.0,
        "confidence_scale": "0_100" if len(conf) and float(np.max(conf)) > 1.0 else "0_1_or_unknown",
        "calibration_split": "dev_oof_only",
    }


def _top1_has_conflict(top: dict[str, Any]) -> bool:
    return (
        _bool(top.get("family_conflict"))
        or _bool(top.get("book_conflict"))
        or _bool(top.get("unit_conflict"))
        or _int(top.get("domain_conflict_count")) > 0
    )


def _challenger_has_support(group_rows: list[dict[str, Any]]) -> bool:
    if not group_rows:
        return False
    top = group_rows[0]
    top_numeric = _float(top.get("numeric_score"))
    for row in group_rows[1:10]:
        if _bool(row.get("family_match")) or _bool(row.get("book_match")):
            return True
        if _float(row.get("numeric_score")) > top_numeric:
            return True
    return False


def _observable_gate(candidate_id: str, group_rows: list[dict[str, Any]], thresholds: dict[str, Any]) -> tuple[bool, str]:
    top = group_rows[0]
    confidence = _float(top.get("confidence"))
    margin = _top1_margin(group_rows)
    low_q25 = confidence <= _float(thresholds["confidence_q25"])
    low_q35 = confidence <= _float(thresholds["confidence_q35"])
    margin_q25 = margin <= _float(thresholds["margin_q25"])
    margin_q35 = margin <= _float(thresholds["margin_q35"])
    challenger = _challenger_has_support(group_rows)
    conflict = _top1_has_conflict(top)
    if candidate_id == "T1G_A1_low_conf_q25":
        return low_q25, "low_conf_q25" if low_q25 else "protected_not_low_conf_q25"
    if candidate_id == "T1G_A2_small_margin_q25":
        return margin_q25, "small_margin_q25" if margin_q25 else "protected_not_small_margin_q25"
    if candidate_id == "T1G_A3_low_conf_or_small_margin_q25":
        applies = low_q25 or margin_q25
        return applies, "low_conf_or_small_margin_q25" if applies else "protected_confident_wide_margin"
    if candidate_id == "T1G_A4_low_conf_or_margin_plus_challenger":
        uncertainty = low_q25 or margin_q25
        applies = uncertainty and challenger
        if applies:
            return True, "uncertainty_q25_with_challenger_support"
        if uncertainty:
            return False, "uncertainty_q25_no_challenger_support"
        return False, "protected_confident_wide_margin"
    if candidate_id == "T1G_A5_low_conf_margin_conflict_precision":
        uncertainty = low_q35 or margin_q35
        applies = uncertainty and conflict and challenger
        if applies:
            return True, "uncertainty_q35_conflict_with_challenger_support"
        if uncertainty and conflict:
            return False, "uncertainty_q35_conflict_no_challenger_support"
        if uncertainty:
            return False, "uncertainty_q35_no_top1_conflict"
        return False, "protected_confident_wide_margin"
    return False, "unknown_candidate"


def _score_candidate_observable(
    *,
    candidate: dict[str, Any],
    preds: np.ndarray,
    labels: np.ndarray,
    groups: list[int],
    meta: list[dict[str, Any]],
    feature_rows: list[dict[str, Any]],
    thresholds: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    candidate_id = candidate["candidate_id"]
    baseline_hit1 = candidate_hit1 = hit1_gain = hit1_loss = 0
    baseline_hit5 = candidate_hit5 = hit5_gain = hit5_loss = 0
    applied_groups = vetoed_groups = rank1_demotion_count = baseline_rank1_groups = 0
    slice_stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "baseline_hit1": 0, "candidate_hit1": 0, "gain": 0, "loss": 0, "net": 0})
    fallback_rows: list[dict[str, Any]] = []
    flips: list[dict[str, Any]] = []
    rank1_rows: list[dict[str, Any]] = []
    gating_rows: list[dict[str, Any]] = []
    start = 0
    for group_idx, size in enumerate(groups):
        stop = start + size
        group_labels = labels[start:stop]
        group_preds = preds[start:stop]
        group_meta = meta[group_idx]
        group_rows = feature_rows[start:stop]
        baseline_order = np.arange(size)
        raw_candidate_order = np.lexsort((np.arange(size), -group_preds))
        baseline_rank = _first_positive_rank(group_labels, baseline_order)
        raw_candidate_rank = _first_positive_rank(group_labels, raw_candidate_order)
        gate_applies, gate_reason = _observable_gate(candidate_id, group_rows, thresholds)
        candidate_order = raw_candidate_order if gate_applies else baseline_order
        veto_applied = not gate_applies
        if baseline_rank == 1:
            baseline_rank1_groups += 1
        if gate_applies:
            applied_groups += 1
        if veto_applied:
            vetoed_groups += 1
        candidate_rank = _first_positive_rank(group_labels, candidate_order)
        base_h1 = baseline_rank == 1
        cand_h1 = candidate_rank == 1
        base_h5 = baseline_rank is not None and baseline_rank <= 5
        cand_h5 = candidate_rank is not None and candidate_rank <= 5
        gain_flag = (not base_h1) and cand_h1
        loss_flag = base_h1 and not cand_h1
        rank1_demotion_count += int(loss_flag)
        baseline_hit1 += int(base_h1)
        candidate_hit1 += int(cand_h1)
        hit1_gain += int(gain_flag)
        hit1_loss += int(loss_flag)
        baseline_hit5 += int(base_h5)
        candidate_hit5 += int(cand_h5)
        hit5_gain += int((not base_h5) and cand_h5)
        hit5_loss += int(base_h5 and not cand_h5)

        local_top_idx = int(candidate_order[0])
        top_row = group_rows[local_top_idx]
        candidate_top_family = _clean(top_row.get("candidate_family"))
        candidate_top_book = _clean(top_row.get("quota_book"))
        expected_book = _clean(next((row.get("quota_book") for row in group_rows if _int(row.get("label")) > 0), ""))
        dims = {
            "query_family": _clean(group_meta.get("query_family")),
            "top1_family": candidate_top_family,
            "source_file": _clean(group_meta.get("source_file")),
            "source_family": _clean(group_meta.get("source_family")),
            "province": _clean(group_meta.get("province")),
            "oof_fold": _clean(group_meta.get("oof_fold")),
            "gate_reason": gate_reason,
            "book_and_rank_bucket": f"expected={expected_book};top1={candidate_top_book};{_rank_bucket(baseline_rank)}",
        }
        for dimension, key in dims.items():
            item = slice_stats[(dimension, key)]
            item["groups"] += 1
            item["baseline_hit1"] += int(base_h1)
            item["candidate_hit1"] += int(cand_h1)
            item["gain"] += int(gain_flag)
            item["loss"] += int(loss_flag)
            item["net"] += int(gain_flag) - int(loss_flag)
        detail = {
            "candidate_id": candidate_id,
            "group_id": _clean(group_meta.get("group_id")),
            "query": _clean(group_meta.get("query")),
            "query_family": dims["query_family"],
            "source_file": dims["source_file"],
            "source_family": dims["source_family"],
            "province": dims["province"],
            "baseline_positive_rank": baseline_rank,
            "raw_candidate_positive_rank": raw_candidate_rank,
            "candidate_positive_rank": candidate_rank,
            "gate_applies": gate_applies,
            "gate_reason": gate_reason,
            "veto_applied": veto_applied,
            "baseline_hit1": base_h1,
            "candidate_hit1": cand_h1,
            "candidate_top_quota_id": _clean(top_row.get("quota_id")),
            "candidate_top_family": candidate_top_family,
            "candidate_top_book": candidate_top_book,
            "candidate_top_score": round(float(group_preds[local_top_idx]), 8),
            "top1_confidence": _float(group_rows[0].get("confidence")),
            "top1_margin": _top1_margin(group_rows),
            "flip_type": "gain" if gain_flag else "loss" if loss_flag else "neutral",
        }
        if gain_flag or loss_flag:
            flips.append(detail)
        if base_h1:
            rank1_rows.append(
                {
                    "candidate_id": candidate_id,
                    "group_id": detail["group_id"],
                    "gate_reason": gate_reason,
                    "veto_applied": veto_applied,
                    "raw_candidate_positive_rank": raw_candidate_rank,
                    "candidate_positive_rank": candidate_rank,
                    "rank1_demoted": loss_flag,
                    "top1_confidence": detail["top1_confidence"],
                    "top1_margin": detail["top1_margin"],
                }
            )
        gating_rows.append(
            {
                "candidate_id": candidate_id,
                "gate_reason": gate_reason,
                "gate_applies": gate_applies,
                "veto_applied": veto_applied,
                "baseline_rank_bucket": _rank_bucket(baseline_rank),
                "candidate_rank_bucket": _rank_bucket(candidate_rank),
                "gain": int(gain_flag),
                "loss": int(loss_flag),
            }
        )
        fallback_rows.append(
            {
                "candidate_id": candidate_id,
                "group_id": detail["group_id"],
                "baseline_hit1": base_h1,
                "raw_candidate_hit1": raw_candidate_rank == 1,
                "candidate_hit1": cand_h1,
                "candidate_override": bool(local_top_idx != 0),
                "gate_applies": gate_applies,
                "veto_applied": veto_applied,
                "override_outcome": detail["flip_type"],
                "no_gate_relaxation": True,
            }
        )
        start = stop
    total = len(groups)
    metrics = {
        "candidate_id": candidate_id,
        "objective_variant": candidate["objective_variant"],
        "feature_toggle": candidate["feature_toggle"],
        "groups": total,
        "baseline_hit1": baseline_hit1,
        "candidate_hit1": candidate_hit1,
        "baseline_hit1_rate": round(baseline_hit1 / total, 6) if total else 0.0,
        "candidate_hit1_rate": round(candidate_hit1 / total, 6) if total else 0.0,
        "hit1_gain": hit1_gain,
        "hit1_loss": hit1_loss,
        "hit1_net": hit1_gain - hit1_loss,
        "baseline_hit5": baseline_hit5,
        "candidate_hit5": candidate_hit5,
        "hit5_gain": hit5_gain,
        "hit5_loss": hit5_loss,
        "hit5_net": hit5_gain - hit5_loss,
        "applied_groups": applied_groups,
        "applied_group_rate": round(applied_groups / total, 6) if total else 0.0,
        "vetoed_groups": vetoed_groups,
        "baseline_rank1_groups": baseline_rank1_groups,
        "rank1_loss_count": rank1_demotion_count,
        "baseline_rank1_demotion_rate": round(rank1_demotion_count / baseline_rank1_groups, 6) if baseline_rank1_groups else 0.0,
    }
    slice_rows = [
        {
            "candidate_id": candidate_id,
            "slice_dimension": dimension,
            "slice_key": key,
            **values,
            "baseline_hit1_rate": round(values["baseline_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
            "candidate_hit1_rate": round(values["candidate_hit1"] / values["groups"], 6) if values["groups"] else 0.0,
        }
        for (dimension, key), values in slice_stats.items()
    ]
    return metrics, slice_rows, fallback_rows, flips, rank1_rows, gating_rows


def _summarise_gating(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"groups": 0, "applied": 0, "vetoed": 0, "gain": 0, "loss": 0, "net": 0})
    for row in rows:
        item = acc[(row["candidate_id"], row["gate_reason"])]
        item["groups"] += 1
        item["applied"] += int(row["gate_applies"])
        item["vetoed"] += int(row["veto_applied"])
        item["gain"] += int(row["gain"])
        item["loss"] += int(row["loss"])
        item["net"] += int(row["gain"]) - int(row["loss"])
    out = []
    for (candidate_id, gate_reason), values in acc.items():
        out.append({"candidate_id": candidate_id, "gate_reason": gate_reason, **values})
    out.sort(key=lambda row: (row["candidate_id"], -row["groups"]))
    return out


def _leakage_rows(candidates: list[dict[str, Any]], all_features: list[str]) -> list[dict[str, Any]]:
    forbidden = {
        "source_file",
        "source_family",
        "source_region",
        "province",
        "sample_id",
        "group_id",
        "quota_id",
        "expected_ids",
        "expected_id",
        "positive_id",
        "label",
        "baseline_rank",
        "baseline_positive_rank",
        "positive_rank",
    }
    rows = []
    for candidate in candidates:
        features = _candidate_features(candidate["feature_toggle"], all_features)
        present = sorted(set(features) & forbidden)
        gate_formula = str(candidate.get("gate_formula") or "")
        forbidden_gate_terms = [term for term in ("baseline_rank", "positive_rank", "expected_id", "label") if term in gate_formula]
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "feature_toggle": candidate["feature_toggle"],
                "feature_count": len(features),
                "forbidden_feature_present": "|".join(present),
                "forbidden_gate_term_present": "|".join(forbidden_gate_terms),
                "status": "fail" if present or forbidden_gate_terms else "pass",
            }
        )
    return rows


def _source_net_stats(loss_rows: list[dict[str, Any]], scorecard_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    net_by_candidate = {row["candidate_id"]: max(0, int(row["hit1_net"])) for row in scorecard_rows}
    out = []
    for row in loss_rows:
        if row["slice_dimension"] not in {"source_family", "province", "oof_fold"}:
            continue
        total_net = net_by_candidate.get(row["candidate_id"], 0)
        net = int(row["net"])
        share = round(abs(net) / total_net, 6) if total_net > 0 else 0.0
        out.append(
            {
                "candidate_id": row["candidate_id"],
                "slice_dimension": row["slice_dimension"],
                "slice_key": row["slice_key"],
                "groups": row["groups"],
                "gain": row["gain"],
                "loss": row["loss"],
                "net": net,
                "abs_net_share_of_positive_total": share,
                "material_negative": net < 0 and int(row["loss"]) >= 2,
            }
        )
    out.sort(key=lambda item: (item["candidate_id"], item["slice_dimension"], -item["abs_net_share_of_positive_total"], -abs(int(item["net"]))))
    return out


def _approval_status(row: dict[str, Any], robustness_rows: list[dict[str, Any]]) -> str:
    if int(row["hit1_net"]) <= 0:
        return "fail_non_positive_top1_net"
    if int(row["rank1_loss_count"]) > max(3, int(row["hit1_gain"]) * 0.08):
        return "fail_rank1_loss_budget"
    if float(row["baseline_rank1_demotion_rate"]) > 0.01:
        return "fail_rank1_demotion_rate"
    candidate_robustness = [item for item in robustness_rows if item["candidate_id"] == row["candidate_id"]]
    max_source_share = max(
        (float(item["abs_net_share_of_positive_total"]) for item in candidate_robustness if item["slice_dimension"] == "source_family"),
        default=0.0,
    )
    if max_source_share > 0.35:
        return "warn_source_family_net_concentration"
    if any(item["slice_dimension"] == "oof_fold" and item["material_negative"] for item in candidate_robustness):
        return "warn_negative_oof_fold"
    return "pass_dev_oof_candidate"


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 13.25 Observable Low-Confidence/Margin Dev/OOF Execution",
        "",
        "Explicitly authorized dev/OOF-only execution for rewritten observable T1G_A candidates. Heldout/hard are not used, and no online GoalSearcher behavior is changed.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["candidate_count", report["metrics"]["candidate_count"]],
                ["group_count", report["metrics"]["group_count"]],
                ["matrix_rows", report["metrics"]["matrix_rows"]],
                ["best_candidate_id", report["metrics"]["best_candidate_id"]],
                ["best_hit1_net", report["metrics"]["best_hit1_net"]],
                ["best_hit1_gain", report["metrics"]["best_hit1_gain"]],
                ["best_hit1_loss", report["metrics"]["best_hit1_loss"]],
                ["approval_candidate_count", report["metrics"]["approval_candidate_count"]],
                ["heldout_used_for_selection", report["metrics"]["heldout_used_for_selection"]],
            ]
        ),
        "",
        "## Threshold Manifest",
        "",
        _md_table(
            [
                ["confidence_scale", "confidence_q25", "confidence_q35", "margin_q25", "margin_q35", "calibration_split"],
                [
                    report["threshold_manifest"]["confidence_scale"],
                    report["threshold_manifest"]["confidence_q25"],
                    report["threshold_manifest"]["confidence_q35"],
                    report["threshold_manifest"]["margin_q25"],
                    report["threshold_manifest"]["margin_q35"],
                    report["threshold_manifest"]["calibration_split"],
                ],
            ]
        ),
        "",
        "## Candidates",
        "",
        _md_table(
            [["rank", "candidate_id", "hit1 gain/loss/net", "rank1_loss", "demotion_rate", "applied_rate", "approval_status"]]
            + [
                [
                    row["scorecard_rank"],
                    row["candidate_id"],
                    f"{row['hit1_gain']}/{row['hit1_loss']}/{row['hit1_net']}",
                    row["rank1_loss_count"],
                    row["baseline_rank1_demotion_rate"],
                    row["applied_group_rate"],
                    row["approval_status"],
                ]
                for row in report["scorecard_rows"]
            ]
        ),
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
        "当前阶段：13.25 observable low-confidence/margin dev/OOF execution 已完成。\n"
        f"结果：best={m['best_candidate_id']}，hit1_net={m['best_hit1_net']}，gain/loss={m['best_hit1_gain']}/{m['best_hit1_loss']}，approval_candidate_count={m['approval_candidate_count']}。\n"
        "下一步建议：13.26 observable low-confidence/margin scorecard/loss review and freeze gate。只读复核 threshold manifest、rank1 preservation、source/fold robustness、loss slices，决定是否 freeze；仍不跑 heldout/hard。\n"
        "禁止：用 heldout/hard 做选择、上线、改 GoalSearcher、调线上阈值、重新引入 label-derived gate、把 dev/OOF 结果宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    row = f"""          <tr>
            <td>13.25 observable low-confidence/margin dev/OOF execution</td>
            <td>Dev/OOF-only execution of five observable T1G_A rewrite candidates with threshold, rank1, source/fold, and leakage audits.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
    if "13.25 observable low-confidence/margin dev/OOF execution" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.25 observable low-confidence/margin dev/OOF execution")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--feature-whitelist", type=Path, default=DEFAULT_FEATURE_WHITELIST)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--dev-oof-only", action="store_true", required=True)
    parser.add_argument("--no-heldout-selection", action="store_true", required=True)
    parser.add_argument("--emit-loss-audit", action="store_true", required=True)
    parser.add_argument("--num-boost-round", type=int, default=120)
    parser.add_argument("--seed", type=int, default=20240526)
    args = parser.parse_args()

    started = time.perf_counter()
    all_features = _load_training_features(args.feature_whitelist)
    df, labels, groups, meta, feature_rows = _load_dev_matrix(args.data_dir, all_features)
    candidates = _read_csv(args.plan)
    thresholds = _top1_stats(feature_rows, groups)
    leakage = _leakage_rows(candidates, all_features)
    if any(row["status"] == "fail" for row in leakage):
        raise ValueError("forbidden training feature or gate term present")

    scorecard_rows: list[dict[str, Any]] = []
    all_loss_rows: list[dict[str, Any]] = []
    all_fallback_rows: list[dict[str, Any]] = []
    all_flips: list[dict[str, Any]] = []
    all_rank1_rows: list[dict[str, Any]] = []
    all_gating_detail_rows: list[dict[str, Any]] = []
    all_fold_rows: list[dict[str, Any]] = []
    all_importance_rows: list[dict[str, Any]] = []

    for candidate in candidates:
        features = _candidate_features(candidate["feature_toggle"], all_features)
        preds, fold_rows, importance_rows = _train_source_oof_candidate(
            candidate=candidate,
            df=df,
            labels=labels,
            groups=groups,
            meta=meta,
            features=features,
            num_boost_round=args.num_boost_round,
            seed=args.seed,
        )
        metrics, loss_rows, fallback_rows, flips, rank1_rows, gating_rows = _score_candidate_observable(
            candidate=candidate,
            preds=preds,
            labels=labels,
            groups=groups,
            meta=meta,
            feature_rows=feature_rows,
            thresholds=thresholds,
        )
        metrics["feature_count"] = len(features)
        scorecard_rows.append(metrics)
        all_loss_rows.extend(loss_rows)
        all_fallback_rows.extend(fallback_rows)
        all_flips.extend(flips)
        all_rank1_rows.extend(rank1_rows)
        all_gating_detail_rows.extend(gating_rows)
        all_fold_rows.extend(fold_rows)
        all_importance_rows.extend(importance_rows)

    robustness_rows = _source_net_stats(all_loss_rows, scorecard_rows)
    for row in scorecard_rows:
        row["approval_status"] = _approval_status(row, robustness_rows)
    scorecard_rows.sort(key=lambda row: (row["approval_status"] != "pass_dev_oof_candidate", -int(row["hit1_net"]), int(row["hit1_loss"]), int(row["rank1_loss_count"])))
    for idx, row in enumerate(scorecard_rows, 1):
        row["scorecard_rank"] = idx
    best = scorecard_rows[0] if scorecard_rows else {}
    approval_count = sum(1 for row in scorecard_rows if row["approval_status"] == "pass_dev_oof_candidate")

    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_execution_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_execution_summary.md")),
        "candidate_scorecard_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_scorecard.csv")),
        "threshold_manifest_json": str(output_prefix.with_name(output_prefix.name + "_threshold_manifest.json")),
        "threshold_manifest_csv": str(output_prefix.with_name(output_prefix.name + "_threshold_manifest.csv")),
        "rank1_preservation_report_csv": str(output_prefix.with_name(output_prefix.name + "_rank1_preservation_report.csv")),
        "gating_coverage_report_csv": str(output_prefix.with_name(output_prefix.name + "_gating_coverage_report.csv")),
        "loss_audit_by_slice_csv": str(output_prefix.with_name(output_prefix.name + "_loss_audit_by_slice.csv")),
        "source_fold_robustness_csv": str(output_prefix.with_name(output_prefix.name + "_source_fold_robustness.csv")),
        "source_split_report_csv": str(output_prefix.with_name(output_prefix.name + "_source_split_report.csv")),
        "leakage_gate_report_csv": str(output_prefix.with_name(output_prefix.name + "_leakage_gate_report.csv")),
        "fallback_contract_report_csv": str(output_prefix.with_name(output_prefix.name + "_fallback_contract_report.csv")),
        "feature_importance_csv": str(output_prefix.with_name(output_prefix.name + "_feature_importance.csv")),
        "hit1_flips_jsonl": str(output_prefix.with_name(output_prefix.name + "_hit1_flips.jsonl")),
    }
    report = {
        "stage": "13.25 observable low-confidence/margin dev/OOF execution",
        "explicit_user_go": True,
        "dev_oof_only": True,
        "heldout_not_used_for_selection": True,
        "hard_not_used_for_selection": True,
        "threshold_manifest": thresholds,
        "metrics": {
            "candidate_count": len(scorecard_rows),
            "group_count": len(groups),
            "matrix_rows": len(df),
            "best_candidate_id": best.get("candidate_id", ""),
            "best_hit1_net": best.get("hit1_net", 0),
            "best_hit1_gain": best.get("hit1_gain", 0),
            "best_hit1_loss": best.get("hit1_loss", 0),
            "best_rank1_loss_count": best.get("rank1_loss_count", 0),
            "approval_candidate_count": approval_count,
            "heldout_used_for_selection": False,
            "hard_used_for_selection": False,
            "goal_searcher_changed": False,
            "elapsed_sec": round(time.perf_counter() - started, 3),
        },
        "scorecard_rows": scorecard_rows,
        "source_fold_checks": _source_fold_rows(meta),
        "artifacts": artifacts,
        "decision": "dev_oof_execution_completed_review_required",
        "anti_drift_conclusion": "13.25 dev/OOF execution only: no heldout/hard selection, no online integration, no threshold change, no GoalSearcher edit, no release, and no label-derived gate.",
        "next_stage": {
            "recommended": "13.26 observable low-confidence/margin scorecard/loss review and freeze gate: read-only decide whether any approved candidate can freeze; do not run heldout/hard.",
            "default": "do_not_validate_yet",
        },
    }

    _write_csv(Path(artifacts["candidate_scorecard_csv"]), scorecard_rows, ["scorecard_rank", "candidate_id", "objective_variant", "feature_toggle", "feature_count", "groups", "baseline_hit1", "candidate_hit1", "baseline_hit1_rate", "candidate_hit1_rate", "hit1_gain", "hit1_loss", "hit1_net", "baseline_hit5", "candidate_hit5", "hit5_gain", "hit5_loss", "hit5_net", "applied_groups", "applied_group_rate", "vetoed_groups", "baseline_rank1_groups", "rank1_loss_count", "baseline_rank1_demotion_rate", "approval_status"])
    _write_json(Path(artifacts["threshold_manifest_json"]), thresholds)
    _write_csv(Path(artifacts["threshold_manifest_csv"]), [thresholds], list(thresholds.keys()))
    _write_csv(Path(artifacts["rank1_preservation_report_csv"]), all_rank1_rows, ["candidate_id", "group_id", "gate_reason", "veto_applied", "raw_candidate_positive_rank", "candidate_positive_rank", "rank1_demoted", "top1_confidence", "top1_margin"])
    _write_csv(Path(artifacts["gating_coverage_report_csv"]), _summarise_gating(all_gating_detail_rows), ["candidate_id", "gate_reason", "groups", "applied", "vetoed", "gain", "loss", "net"])
    _write_csv(Path(artifacts["loss_audit_by_slice_csv"]), all_loss_rows, ["candidate_id", "slice_dimension", "slice_key", "groups", "baseline_hit1", "candidate_hit1", "gain", "loss", "net", "baseline_hit1_rate", "candidate_hit1_rate"])
    _write_csv(Path(artifacts["source_fold_robustness_csv"]), robustness_rows, ["candidate_id", "slice_dimension", "slice_key", "groups", "gain", "loss", "net", "abs_net_share_of_positive_total", "material_negative"])
    _write_csv(Path(artifacts["source_split_report_csv"]), all_fold_rows, ["candidate_id", "fold", "train_groups", "valid_groups", "train_rows", "valid_rows", "valid_source_family_count", "valid_source_families", "num_boost_round", "feature_count"])
    _write_csv(Path(artifacts["leakage_gate_report_csv"]), leakage, ["candidate_id", "feature_toggle", "feature_count", "forbidden_feature_present", "forbidden_gate_term_present", "status"])
    _write_csv(Path(artifacts["fallback_contract_report_csv"]), all_fallback_rows, ["candidate_id", "group_id", "baseline_hit1", "raw_candidate_hit1", "candidate_hit1", "candidate_override", "gate_applies", "veto_applied", "override_outcome", "no_gate_relaxation"])
    _write_csv(Path(artifacts["feature_importance_csv"]), all_importance_rows, ["candidate_id", "feature", "gain_sum"])
    _write_jsonl(Path(artifacts["hit1_flips_jsonl"]), all_flips)
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"], "best": best}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
