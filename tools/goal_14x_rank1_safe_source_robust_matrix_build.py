from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.goal_search.national_index import clean_text  # noqa: E402
from tools.goal_13x_oss_xml_source_aware_matrix_build import (  # noqa: E402
    DEFAULT_DASHBOARD,
    DEFAULT_INVENTORY,
    DEFAULT_WHITELIST,
    FORBIDDEN_TRAINING_FEATURES,
    _build_matrix as build_raw_oss_matrix,
    _clean_float,
    _format_number,
    _read_feature_whitelist,
    _safe_rel,
    _write_csv,
    _write_json,
    _write_jsonl,
)


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_OUTPUT_DIR = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix"
DEFAULT_REPORT_JSON = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix_build_summary.json"
DEFAULT_REPORT_MD = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix_build_summary.md"
DEFAULT_RAW_DIR = AGENT_STATE / "goal_14x_rank1_safe_source_robust_matrix_raw"

REQUIRED_GO_SCOPE = "14.2 rank1-safe source-robust balanced OSS matrix build"
FORBIDDEN_GATE_FIELDS = {
    "baseline_rank",
    "positive_rank",
    "expected_id",
    "expected_ids",
    "expected_quota_id",
    "expected_quota_ids",
    "label",
}
REQUIRED_SUPPORT_FEATURES = [
    "family_match",
    "book_match",
    "action_match",
    "material_match",
    "connection_match",
    "numeric_score",
    "current_score",
    "confidence",
    "reason_count",
    "has_domain_conflict",
    "has_family_conflict_reason",
    "has_book_conflict_reason",
    "has_unit_conflict_reason",
    "has_param_conflict_reason",
]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    _write_csv(path, rows, fields)


def clean_for_filename(value: str) -> str:
    value = clean_text(value)
    value = re.sub(r"[^0-9A-Za-z_.-]+", "_", value)
    return value.strip("_") or "blank"


def source_family_cap_count(target_groups: int, cap: float) -> int:
    return max(1, int(math.floor(target_groups * cap)))


def feasible_target(counts: Counter[str], requested: int, cap: float) -> int:
    upper = min(requested, sum(counts.values()))
    for target in range(upper, 0, -1):
        limit = source_family_cap_count(target, cap)
        if sum(min(count, limit) for count in counts.values()) >= target:
            return target
    return 0


def pick_evenly(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    if len(rows) <= limit:
        return list(rows)
    ordered = sorted(
        rows,
        key=lambda row: (
            int(row.get("oof_fold") or 0),
            int(row.get("positive_rank") or 999),
            clean_text(row.get("source_file")),
            clean_text(row.get("group_id")),
        ),
    )
    picks = []
    for idx in range(limit):
        pos = round(idx * (len(ordered) - 1) / max(limit - 1, 1))
        picks.append(ordered[pos])
    seen: set[str] = set()
    result = []
    for row in picks:
        gid = clean_text(row.get("group_id"))
        if gid and gid not in seen:
            seen.add(gid)
            result.append(row)
    return result


def select_balanced_groups(group_rows: list[dict[str, Any]], target_groups: int, cap: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in group_rows:
        by_family[clean_text(row.get("source_family")) or "<empty>"].append(row)
    counts = Counter({family: len(rows) for family, rows in by_family.items()})
    target = feasible_target(counts, target_groups, cap)
    per_family_limit = source_family_cap_count(target, cap) if target else 0
    family_picks: dict[str, list[dict[str, Any]]] = {}
    for family, rows in by_family.items():
        family_picks[family] = pick_evenly(rows, min(len(rows), per_family_limit))

    selected_by_id: dict[str, dict[str, Any]] = {}
    family_order = sorted(family_picks, key=lambda key: (-len(family_picks[key]), key))
    cursor = 0
    while len(selected_by_id) < target:
        added = False
        for family in family_order:
            rows = family_picks[family]
            if cursor < len(rows):
                row = rows[cursor]
                gid = clean_text(row.get("group_id"))
                if gid and gid not in selected_by_id:
                    selected_by_id[gid] = row
                    added = True
                    if len(selected_by_id) >= target:
                        break
        if not added:
            break
        cursor += 1

    selected_ids = set(selected_by_id)
    selected = [row for row in group_rows if clean_text(row.get("group_id")) in selected_ids]
    diagnostics = {
        "raw_group_count": len(group_rows),
        "requested_target_groups": target_groups,
        "selected_group_count": len(selected),
        "effective_target_groups": target,
        "source_family_cap": cap,
        "per_family_limit": per_family_limit,
        "raw_source_family_counts": dict(counts),
        "selected_source_family_counts": dict(Counter(clean_text(row.get("source_family")) or "<empty>" for row in selected)),
    }
    return selected, diagnostics


def write_selected_features(
    *,
    raw_feature_jsonl: Path,
    feature_jsonl: Path,
    matrix_csv: Path,
    selected_group_ids: set[str],
    training_features: list[str],
) -> dict[str, Any]:
    feature_jsonl.parent.mkdir(parents=True, exist_ok=True)
    matrix_csv.parent.mkdir(parents=True, exist_ok=True)
    missing_features: Counter[str] = Counter()
    invalid_numeric: Counter[str] = Counter()
    row_count = 0
    positive_rows = 0
    top1_by_group: dict[str, dict[str, Any]] = {}
    positive_book_by_group: dict[str, str] = {}
    candidate_count_by_group: Counter[str] = Counter()

    with raw_feature_jsonl.open("r", encoding="utf-8") as source, feature_jsonl.open("w", encoding="utf-8") as feature_out, matrix_csv.open(
        "w", encoding="utf-8-sig", newline=""
    ) as matrix_handle:
        writer = csv.DictWriter(matrix_handle, fieldnames=["label", *training_features], extrasaction="ignore")
        writer.writeheader()
        for line in source:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            gid = clean_text(row.get("group_id"))
            if gid not in selected_group_ids:
                continue
            row_count += 1
            label = int(row.get("label") or 0)
            positive_rows += label
            candidate_count_by_group[gid] += 1
            if int(row.get("candidate_rank") or 0) == 1:
                top1_by_group[gid] = {
                    "top1_family": clean_text(row.get("candidate_family")),
                    "top1_quota_book": clean_text(row.get("quota_book")),
                    "top1_confidence": _clean_float(row.get("confidence")),
                    "top1_score": _clean_float(row.get("current_score")),
                }
            if label:
                positive_book_by_group[gid] = clean_text(row.get("quota_book"))
            feature_out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            matrix_row: dict[str, Any] = {"label": label}
            for feature in training_features:
                if feature not in row:
                    missing_features[feature] += 1
                value = row.get(feature, 0)
                try:
                    float(value)
                except (TypeError, ValueError):
                    invalid_numeric[feature] += 1
                matrix_row[feature] = _format_number(value)
            writer.writerow(matrix_row)

    return {
        "matrix_rows": row_count,
        "positive_rows": positive_rows,
        "top1_by_group": top1_by_group,
        "positive_book_by_group": positive_book_by_group,
        "candidate_count_by_group": dict(candidate_count_by_group),
        "matrix_issues": [
            *({"type": "missing_feature", "feature": key, "count": value} for key, value in missing_features.most_common()),
            *({"type": "invalid_numeric", "feature": key, "count": value} for key, value in invalid_numeric.most_common()),
        ],
    }


def build_source_split_manifest(group_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in group_rows:
        by_family[clean_text(row.get("source_family")) or "<empty>"].append(row)
    rows = []
    for family, items in by_family.items():
        files = {clean_text(row.get("source_file")) for row in items}
        folds = {str(row.get("oof_fold")) for row in items}
        rows.append(
            {
                "source_family": family,
                "accepted_groups": len(items),
                "source_files": len(files),
                "oof_folds": "|".join(sorted(folds)),
                "fold_count": len(folds),
            }
        )
    return sorted(rows, key=lambda row: (-int(row["accepted_groups"]), row["source_family"]))


def build_balance_checks(
    *,
    group_rows: list[dict[str, Any]],
    training_features: list[str],
    source_family_cap: float,
    hard_source_family_cap: float,
    max_source_file_group_share: float,
    min_fold_median_ratio: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    group_count = len(group_rows)
    source_file_counts = Counter(clean_text(row.get("source_file")) or "<empty>" for row in group_rows)
    source_family_counts = Counter(clean_text(row.get("source_family")) or "<empty>" for row in group_rows)
    fold_counts = Counter(str(row.get("oof_fold") or 0) for row in group_rows)
    same_file_folds: dict[str, set[str]] = defaultdict(set)
    for row in group_rows:
        same_file_folds[clean_text(row.get("source_file"))].add(str(row.get("oof_fold")))
    same_file_cross_fold_violations = sum(1 for folds in same_file_folds.values() if len(folds) > 1)
    observed_max_source_file_share = round(max(source_file_counts.values()) / group_count, 6) if group_count else 0.0
    max_source_family_share = round(max(source_family_counts.values()) / group_count, 6) if group_count else 0.0
    fold_values = sorted(fold_counts.values())
    median_fold = fold_values[len(fold_values) // 2] if fold_values else 0
    min_fold = min(fold_values) if fold_values else 0
    min_fold_ratio = round(min_fold / median_fold, 6) if median_fold else 0.0
    training_feature_set = set(training_features)
    forbidden_leaks = sorted(training_feature_set & (FORBIDDEN_TRAINING_FEATURES | FORBIDDEN_GATE_FIELDS))

    leakage_rows = [
        {"check": "forbidden_training_feature_intersection", "value": len(forbidden_leaks), "status": "pass" if not forbidden_leaks else "fail", "details": "|".join(forbidden_leaks)},
        {"check": "label_derived_gate_fields_absent", "value": len(sorted(training_feature_set & FORBIDDEN_GATE_FIELDS)), "status": "pass" if not (training_feature_set & FORBIDDEN_GATE_FIELDS) else "fail", "details": "|".join(sorted(training_feature_set & FORBIDDEN_GATE_FIELDS))},
        {"check": "same_source_file_cross_fold_violations", "value": same_file_cross_fold_violations, "status": "pass" if same_file_cross_fold_violations == 0 else "fail", "details": ""},
        {"check": "heldout_hard_used_for_selection", "value": 0, "status": "pass", "details": "dev/OOF-only matrix build"},
        {"check": "training_executed", "value": 0, "status": "pass", "details": "14.2 build only"},
        {"check": "goal_searcher_changed", "value": 0, "status": "pass", "details": "offline search calls only; no code/config change"},
    ]
    source_rows = [
        {
            "check": "max_source_file_group_share",
            "value": observed_max_source_file_share,
            "status": "pass" if observed_max_source_file_share <= max_source_file_group_share else "warn",
            "details": f"target<={max_source_file_group_share}",
        },
        {"check": "max_source_family_group_share_preferred", "value": max_source_family_share, "status": "pass" if max_source_family_share <= source_family_cap else "warn", "details": f"preferred<={source_family_cap}"},
        {"check": "max_source_family_group_share_hard", "value": max_source_family_share, "status": "pass" if max_source_family_share <= hard_source_family_cap else "fail", "details": f"hard<={hard_source_family_cap}"},
        {"check": "observed_oof_fold_count", "value": len(fold_counts), "status": "pass" if len(fold_counts) >= 5 else "warn", "details": "target=5"},
        {"check": "min_fold_to_median_group_ratio", "value": min_fold_ratio, "status": "pass" if min_fold_ratio >= min_fold_median_ratio else "warn", "details": f"min={min_fold}; median={median_fold}; target>={min_fold_median_ratio}"},
    ]
    return leakage_rows, source_rows


def build_province_book_checks(group_rows: list[dict[str, Any]], positive_book_by_group: dict[str, str]) -> list[dict[str, Any]]:
    group_count = len(group_rows)
    province_counts = Counter(clean_text(row.get("province")) or "<empty>" for row in group_rows)
    province_book_counts = Counter(
        f"{clean_text(row.get('province')) or '<empty>'}::{positive_book_by_group.get(clean_text(row.get('group_id')), '<empty>') or '<empty>'}"
        for row in group_rows
    )
    max_province_share = round(max(province_counts.values()) / group_count, 6) if group_count else 0.0
    max_province_book_share = round(max(province_book_counts.values()) / group_count, 6) if group_count else 0.0
    rows = [
        {"check": "max_province_group_share", "value": max_province_share, "status": "pass" if max_province_share <= 0.50 else "warn", "details": "diagnostic target<=0.50"},
        {"check": "max_province_positive_book_group_share", "value": max_province_book_share, "status": "pass" if max_province_book_share <= 0.35 else "warn", "details": "diagnostic target<=0.35"},
    ]
    rows.extend(
        {
            "check": f"province_count::{idx}",
            "value": count,
            "status": "info",
            "details": province,
        }
        for idx, (province, count) in enumerate(province_counts.most_common(10), 1)
    )
    rows.extend(
        {
            "check": f"province_book_count::{idx}",
            "value": count,
            "status": "info",
            "details": bucket,
        }
        for idx, (bucket, count) in enumerate(province_book_counts.most_common(10), 1)
    )
    return rows


def build_taxonomy_manifest(group_rows: list[dict[str, Any]], top1_by_group: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: Counter[str] = Counter()
    for row in group_rows:
        gid = clean_text(row.get("group_id"))
        query_family = clean_text(row.get("query_family"))
        top1_family = clean_text(top1_by_group.get(gid, {}).get("top1_family"))
        if not query_family and not top1_family:
            bucket = "query_family_empty_and_top1_family_empty"
        elif not query_family:
            bucket = "query_family_empty"
        elif not top1_family:
            bucket = "top1_family_empty"
        elif query_family == top1_family:
            bucket = "same_family"
        else:
            bucket = "cross_family"
        buckets[bucket] += 1
    total = sum(buckets.values())
    return [
        {
            "taxonomy_slice": key,
            "groups": count,
            "share": round(count / total, 6) if total else 0.0,
            "freeze_support_policy": "audit_only_not_sole_trigger" if "empty" in key else "eligible_with_other_evidence",
        }
        for key, count in buckets.most_common()
    ]


def build_feature_contract(training_features: list[str]) -> list[dict[str, Any]]:
    training_set = set(training_features)
    rows = []
    for feature in REQUIRED_SUPPORT_FEATURES:
        rows.append(
            {
                "feature_or_rule": feature,
                "contract": "online_observable_support_component",
                "status": "present" if feature in training_set else "missing",
                "details": "used by future strong challenger gate; not a label/source id",
            }
        )
    for feature in sorted(training_set & (FORBIDDEN_TRAINING_FEATURES | FORBIDDEN_GATE_FIELDS)):
        rows.append({"feature_or_rule": feature, "contract": "forbidden_leakage_field", "status": "fail", "details": "must not be in training whitelist"})
    rows.extend(
        [
            {"feature_or_rule": "source_family/province/source_file", "contract": "diagnostic_split_only", "status": "excluded", "details": "metadata may audit balance but cannot train/rank"},
            {"feature_or_rule": "low_confidence_alone_demotes_rank1", "contract": "forbidden_gate_logic", "status": "blocked", "details": "14.x requires rank1 veto plus strong challenger support"},
            {"feature_or_rule": "heldout/hard", "contract": "validation_only_after_future_go", "status": "blocked", "details": "not read or selected in 14.2"},
        ]
    )
    return rows


def copy_filtered_file_selection(raw_file_selection: Path, output_path: Path, selected_files: set[str]) -> None:
    if not raw_file_selection.exists():
        return
    with raw_file_selection.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [row for row in reader if clean_text(row.get("relative_path")) in selected_files]
        fields = list(reader.fieldnames or [])
    write_csv_rows(output_path, rows, fields)


def build_raw_args(args: argparse.Namespace, raw_dir: Path) -> argparse.Namespace:
    return SimpleNamespace(
        oss_xml_root=str(args.oss_root),
        inventory=str(args.inventory),
        output_dir=str(raw_dir),
        report_json=str(raw_dir / "raw_matrix_summary.json"),
        report_md=str(raw_dir / "raw_matrix_summary.md"),
        dashboard=str(args.dashboard),
        feature_whitelist=str(args.feature_whitelist),
        db_dir=args.db_dir,
        regions=args.regions,
        top_k=args.top_k,
        oof_folds=args.oof_folds,
        max_total_files=args.max_total_files,
        max_files_per_source_family=args.max_files_per_source_family,
        max_pairs_per_file=args.max_pairs_per_file,
        max_accepted_groups=args.raw_max_accepted_groups,
        dedupe_unique_name_size=True,
        max_source_file_group_share=args.max_source_file_group_share,
        max_source_family_group_share=args.hard_source_family_cap,
        min_fold_median_ratio=args.min_fold_median_ratio,
        progress_every=args.progress_every,
    )


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        "# 14.2 Rank1-Safe Source-Robust Balanced OSS Matrix Build",
        "",
        "Bounded build stage for OSS XML dev/OOF matrix. No model was trained, and heldout/hard were not read.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key in [
        "raw_accepted_groups",
        "accepted_groups",
        "matrix_rows",
        "positive_rows",
        "baseline_hit1_rate_on_matrix_groups",
        "topk_recall_rate",
        "max_source_family_group_share",
        "observed_oof_fold_count",
        "min_fold_to_median_group_ratio",
        "training_executed",
        "heldout_used_for_selection",
    ]:
        lines.append(f"| {key} | {m.get(key)} |")
    lines.extend(["", "## Source Balance Checks", "", "| check | status | value | details |", "| --- | --- | --- | --- |"])
    for row in report["diagnostics"]["source_balance_checks"]:
        lines.append(f"| {row['check']} | {row['status']} | {row['value']} | {row['details']} |")
    lines.extend(["", "## Leakage Checks", "", "| check | status | value | details |", "| --- | --- | --- | --- |"])
    for row in report["diagnostics"]["leakage_checks"]:
        lines.append(f"| {row['check']} | {row['status']} | {row['value']} | {row['details']} |")
    lines.extend(["", "## Decision", "", report["decision"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.2 rank1-safe source-robust balanced OSS matrix build 已完成。\n"
        f"结果：accepted_groups={m['accepted_groups']}，matrix_rows={m['matrix_rows']}，"
        f"topk_recall_rate={m['topk_recall_rate']}，baseline_hit1_rate={m['baseline_hit1_rate_on_matrix_groups']}，"
        f"max_source_family_share={m['max_source_family_group_share']}，folds={m['observed_oof_fold_count']}。\n"
        "下一步建议：14.3 rank1-safe source-robust dev/OOF training authorization gate。只有明确 go 才训练；默认不训练。\n"
        "禁止：heldout/hard 选择、上线、改 GoalSearcher、调阈值、把 source/provenance/expected_id/label 当训练或 gate 特征。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "14.2 rank1-safe source-robust balanced OSS matrix build summary" not in text:
        row = f"""          <tr>
            <td>14.2 rank1-safe source-robust balanced OSS matrix build summary</td>
            <td>Balanced accepted-OSS dev/OOF LTR matrix plus source/fold/province/taxonomy/feature-contract manifests.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
          <tr>
            <td>14.2 rank1-safe source-robust balanced OSS matrix build artifacts</td>
            <td>LTR matrix, group manifest, source split, balance checks, taxonomy audit, and feature contract.</td>
            <td><code>{_safe_rel(report['artifacts']['output_dir'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    if not args.dev_oof_only:
        raise ValueError("--dev-oof-only is required for 14.2")
    if not args.no_heldout:
        raise ValueError("--no-heldout is required for 14.2")
    if not Path(args.oss_root).exists():
        raise FileNotFoundError(f"OSS root not found: {args.oss_root}")

    output_dir = Path(args.output_dir)
    raw_dir = Path(args.raw_output_dir)
    if output_dir.exists() and args.force:
        shutil.rmtree(output_dir)
    if raw_dir.exists() and args.force and not args.reuse_raw:
        shutil.rmtree(raw_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if args.reuse_raw and (raw_dir / "ltr_group_dev.jsonl").exists() and (raw_dir / "ltr_features_dev.jsonl").exists():
        raw_summary_candidates = [
            raw_dir / "raw_matrix_summary.json",
            raw_dir.with_name(raw_dir.name + "_summary.json"),
            AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix_expanded_summary.json",
        ]
        raw_report = next((read_json(path) for path in raw_summary_candidates if path.exists()), {"metrics": {}})
    else:
        raw_report = build_raw_oss_matrix(build_raw_args(args, raw_dir))
        _write_json(raw_dir / "raw_matrix_summary.json", raw_report)

    raw_group_rows = read_jsonl(raw_dir / "ltr_group_dev.jsonl")
    selected_groups, selection_diagnostics = select_balanced_groups(raw_group_rows, args.target_groups, args.source_family_cap)
    if not selected_groups:
        raise RuntimeError("No balanced groups selected")

    selected_group_ids = {clean_text(row.get("group_id")) for row in selected_groups}
    training_features = _read_feature_whitelist(Path(args.feature_whitelist))
    feature_stats = write_selected_features(
        raw_feature_jsonl=raw_dir / "ltr_features_dev.jsonl",
        feature_jsonl=output_dir / "ltr_features_dev.jsonl",
        matrix_csv=output_dir / "ltr_matrix_dev.csv",
        selected_group_ids=selected_group_ids,
        training_features=training_features,
    )
    selected_groups_by_id = {clean_text(row.get("group_id")): row for row in selected_groups}
    for gid, top1 in feature_stats["top1_by_group"].items():
        if gid in selected_groups_by_id:
            selected_groups_by_id[gid].update(top1)
    selected_groups = [selected_groups_by_id[clean_text(row.get("group_id"))] for row in selected_groups]

    _write_jsonl(output_dir / "ltr_group_dev.jsonl", selected_groups)
    (output_dir / "ltr_group_dev.txt").write_text("\n".join(str(row.get("rows") or feature_stats["candidate_count_by_group"].get(clean_text(row.get("group_id")), 0)) for row in selected_groups) + "\n", encoding="utf-8")
    whitelist_out = output_dir / "ltr_feature_whitelist_oss_source_aware_v1.json"
    _write_json(
        whitelist_out,
        {
            "stage": REQUIRED_GO_SCOPE,
            "training_features": training_features,
            "label_column": "label",
            "group_column": "group_id",
            "fold_column": "oof_fold",
            "excluded_diagnostic_columns": sorted(set(FORBIDDEN_TRAINING_FEATURES) | FORBIDDEN_GATE_FIELDS),
            "source_whitelist": str(args.feature_whitelist),
            "notes": [
                "14.2 matrix build only; no model training or threshold tuning.",
                "Source/province/source_family fields are diagnostics and split metadata only.",
                "Strong challenger support is constructed later from online-observable feature components.",
            ],
        },
    )

    selected_files = {clean_text(row.get("source_file")) for row in selected_groups}
    copy_filtered_file_selection(raw_dir / "file_selection.csv", output_dir / "file_selection.csv", selected_files)
    if (raw_dir / "recall_gap_dev.jsonl").exists():
        shutil.copyfile(raw_dir / "recall_gap_dev.jsonl", output_dir / "recall_gap_dev.raw.jsonl")
    if (raw_dir / "anchor_excluded_dev.jsonl").exists():
        shutil.copyfile(raw_dir / "anchor_excluded_dev.jsonl", output_dir / "anchor_excluded_dev.raw.jsonl")

    source_split_rows = build_source_split_manifest(selected_groups)
    leakage_rows, source_balance_rows = build_balance_checks(
        group_rows=selected_groups,
        training_features=training_features,
        source_family_cap=args.source_family_cap,
        hard_source_family_cap=args.hard_source_family_cap,
        max_source_file_group_share=args.max_source_file_group_share,
        min_fold_median_ratio=args.min_fold_median_ratio,
    )
    province_book_rows = build_province_book_checks(selected_groups, feature_stats["positive_book_by_group"])
    taxonomy_rows = build_taxonomy_manifest(selected_groups, feature_stats["top1_by_group"])
    feature_contract_rows = build_feature_contract(training_features)

    write_csv_rows(output_dir / "source_split_manifest.csv", source_split_rows, ["source_family", "accepted_groups", "source_files", "oof_folds", "fold_count"])
    write_csv_rows(output_dir / "source_balance_checks.csv", source_balance_rows, ["check", "value", "status", "details"])
    write_csv_rows(output_dir / "leakage_checks.csv", leakage_rows, ["check", "value", "status", "details"])
    write_csv_rows(output_dir / "province_book_balance_checks.csv", province_book_rows, ["check", "value", "status", "details"])
    write_csv_rows(output_dir / "taxonomy_empty_slice_manifest.csv", taxonomy_rows, ["taxonomy_slice", "groups", "share", "freeze_support_policy"])
    write_csv_rows(output_dir / "feature_contract_report.csv", feature_contract_rows, ["feature_or_rule", "contract", "status", "details"])

    group_count = len(selected_groups)
    baseline_hit1 = sum(1 for row in selected_groups if int(row.get("positive_rank") or 0) == 1)
    baseline_hit5 = sum(1 for row in selected_groups if int(row.get("positive_rank") or 0) <= 5)
    source_family_counts = Counter(clean_text(row.get("source_family")) or "<empty>" for row in selected_groups)
    fold_counts = Counter(str(row.get("oof_fold") or 0) for row in selected_groups)
    fold_values = sorted(fold_counts.values())
    median_fold = fold_values[len(fold_values) // 2] if fold_values else 0
    min_fold = min(fold_values) if fold_values else 0
    max_source_family_share = round(max(source_family_counts.values()) / group_count, 6) if group_count else 0.0
    min_fold_ratio = round(min_fold / median_fold, 6) if median_fold else 0.0
    failed_checks = [row for row in [*source_balance_rows, *leakage_rows] if row["status"] == "fail"]
    decision = "balanced_matrix_ready_for_14_3_authorization_gate" if not failed_checks else "do_not_train_fix_matrix_or_leakage_failures"
    report = {
        "stage": REQUIRED_GO_SCOPE,
        "decision": decision,
        "metrics": {
            "raw_accepted_groups": raw_report.get("metrics", {}).get("accepted_groups", 0),
            "accepted_groups": group_count,
            "matrix_rows": feature_stats["matrix_rows"],
            "group_sum": sum(int(row.get("rows") or 0) for row in selected_groups),
            "positive_rows": feature_stats["positive_rows"],
            "topk_recall_rate": raw_report.get("metrics", {}).get("topk_recall_rate", 0),
            "baseline_hit1_groups": baseline_hit1,
            "baseline_hit1_rate_on_matrix_groups": round(baseline_hit1 / group_count, 6) if group_count else 0.0,
            "baseline_hit5_groups": baseline_hit5,
            "baseline_hit5_rate_on_matrix_groups": round(baseline_hit5 / group_count, 6) if group_count else 0.0,
            "max_source_family_group_share": max_source_family_share,
            "observed_oof_fold_count": len(fold_counts),
            "min_fold_to_median_group_ratio": min_fold_ratio,
            "training_executed": False,
            "heldout_used_for_selection": False,
            "hard_used_for_selection": False,
            "goal_searcher_changed": False,
        },
        "config": {
            "oss_root": str(args.oss_root),
            "source_family_cap": args.source_family_cap,
            "hard_source_family_cap": args.hard_source_family_cap,
            "target_groups": args.target_groups,
            "raw_output_dir": str(raw_dir),
            "output_dir": str(output_dir),
            "raw_build": vars(build_raw_args(args, raw_dir)),
        },
        "diagnostics": {
            "selection": selection_diagnostics,
            "source_family_counts": [{"key": key, "count": count} for key, count in source_family_counts.most_common()],
            "fold_group_counts": dict(sorted(fold_counts.items(), key=lambda item: int(item[0]))),
            "source_balance_checks": source_balance_rows,
            "leakage_checks": leakage_rows,
            "province_book_balance_checks": province_book_rows,
            "taxonomy_empty_slice_manifest": taxonomy_rows,
            "feature_contract_report": feature_contract_rows,
            "matrix_issue_count": len(feature_stats["matrix_issues"]),
            "matrix_issues": feature_stats["matrix_issues"][:20],
        },
        "artifacts": {
            "output_dir": str(output_dir),
            "raw_output_dir": str(raw_dir),
            "feature_jsonl": str(output_dir / "ltr_features_dev.jsonl"),
            "matrix_csv": str(output_dir / "ltr_matrix_dev.csv"),
            "group_txt": str(output_dir / "ltr_group_dev.txt"),
            "group_jsonl": str(output_dir / "ltr_group_dev.jsonl"),
            "source_split_manifest_csv": str(output_dir / "source_split_manifest.csv"),
            "source_balance_checks_csv": str(output_dir / "source_balance_checks.csv"),
            "leakage_checks_csv": str(output_dir / "leakage_checks.csv"),
            "province_book_balance_checks_csv": str(output_dir / "province_book_balance_checks.csv"),
            "taxonomy_empty_slice_manifest_csv": str(output_dir / "taxonomy_empty_slice_manifest.csv"),
            "feature_contract_report_csv": str(output_dir / "feature_contract_report.csv"),
            "feature_whitelist": str(whitelist_out),
            "summary_json": str(args.report_json),
            "summary_md": str(args.report_md),
        },
        "next_stage": {
            "recommended": "14.3 rank1-safe source-robust dev/OOF training authorization gate",
            "requires_explicit_go": True,
            "default_without_go": "do_not_train",
        },
        "anti_drift_conclusion": (
            "14.2 built only a dev/OOF OSS matrix and manifests. It did not train, did not read heldout/hard, "
            "did not release, did not edit GoalSearcher, and did not place source/provenance/expected_id/label fields in the training matrix."
        ),
    }
    _write_json(output_dir / "balanced_matrix_manifest.json", report)
    _write_json(Path(args.report_json), report)
    write_markdown(Path(args.report_md), report)
    update_dashboard(Path(args.dashboard), report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Build 14.2 rank1-safe source-robust balanced OSS dev/OOF matrix")
    parser.add_argument("--oss-root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--raw-output-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--report-json", type=Path, default=DEFAULT_REPORT_JSON)
    parser.add_argument("--report-md", type=Path, default=DEFAULT_REPORT_MD)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--feature-whitelist", type=Path, default=DEFAULT_WHITELIST)
    parser.add_argument("--db-dir", default="")
    parser.add_argument("--regions", default="FJ,ZJ,JS,BJ")
    parser.add_argument("--top-k", type=int, default=80)
    parser.add_argument("--oof-folds", type=int, default=5)
    parser.add_argument("--max-total-files", type=int, default=120)
    parser.add_argument("--max-files-per-source-family", type=int, default=20)
    parser.add_argument("--max-pairs-per-file", type=int, default=90)
    parser.add_argument("--raw-max-accepted-groups", type=int, default=3600)
    parser.add_argument("--target-groups", type=int, default=3000)
    parser.add_argument("--source-family-cap", type=float, default=0.22)
    parser.add_argument("--hard-source-family-cap", type=float, default=0.25)
    parser.add_argument("--max-source-file-group-share", type=float, default=0.08)
    parser.add_argument("--min-fold-median-ratio", type=float, default=0.60)
    parser.add_argument("--dev-oof-only", action="store_true")
    parser.add_argument("--no-heldout", action="store_true")
    parser.add_argument("--reuse-raw", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--progress-every", type=int, default=5)
    args = parser.parse_args()

    report = run(args)
    print(json.dumps({"summary": str(args.report_json), "decision": report["decision"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
