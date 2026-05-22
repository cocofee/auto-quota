from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_MODEL_PATH = DEFAULT_DATA_DIR / "goal_ltr_oof_full_dev_v1.txt"
DEFAULT_WHITELIST_PATH = DEFAULT_DATA_DIR / "ltr_feature_whitelist_v1.json"
DEFAULT_SAFETY_GATE_PATH = DEFAULT_DATA_DIR / "ltr_safety_gate_oof_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_eval_ltr_switch_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_eval_ltr_switch_summary.md"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"

PARAM_MATCH_FEATURES = (
    "dn_exact",
    "dn_tier_up",
    "cable_section_exact",
    "cable_section_tier_up",
    "cable_cores_exact",
    "circuits_exact",
    "circuits_tier_up",
    "concrete_grade_exact",
    "thickness_exact",
    "thickness_tier_up",
    "width_height_exact",
    "width_height_tier_match",
)


@dataclass
class SplitData:
    split: str
    labels: list[int]
    groups: list[int]
    feature_rows: list[dict[str, Any]]
    matrix: list[list[float]]
    feature_error: str = ""


@dataclass
class LtrRuntime:
    requested: bool
    active: bool
    model_path: Path
    whitelist_path: Path
    safety_gate_path: Path
    policy: str
    feature_version: str
    margin: float | None = None
    booster: Any | None = None
    decision: Callable[..., tuple[bool, str]] | None = None
    fallback_reason: str = ""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _read_group(path: Path) -> list[int]:
    return [int(line.strip()) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_feature_whitelist(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    features = payload.get("training_features")
    if not isinstance(features, list) or not features:
        raise ValueError(f"{path} does not contain training_features")
    return [str(feature) for feature in features]


def _finite_float(value: Any) -> float:
    result = float(value)
    if math.isnan(result) or math.isinf(result):
        raise ValueError("not finite")
    return result


def _load_matrix(path: Path, features: list[str]) -> tuple[list[int], list[list[float]], str]:
    labels: list[int] = []
    matrix: list[list[float]] = []
    feature_error = ""

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        if "label" not in fieldnames:
            raise ValueError(f"{path} missing label column")
        missing = [feature for feature in features if feature not in fieldnames]
        if missing:
            feature_error = f"whitelist_mismatch: missing features {missing[:8]}"

        for row_index, row in enumerate(reader, start=1):
            try:
                labels.append(int(float(row.get("label") or 0)))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path} invalid label at row {row_index}") from exc

            if not features or feature_error:
                continue

            values: list[float] = []
            for feature in features:
                try:
                    values.append(_finite_float(row.get(feature)))
                except (TypeError, ValueError):
                    feature_error = f"invalid_feature_value: row {row_index}, feature {feature}"
                    values = []
                    break
            if feature_error:
                matrix = []
                continue
            matrix.append(values)

    return labels, matrix, feature_error


def _load_split(data_dir: Path, split: str, features: list[str]) -> SplitData:
    matrix_path = data_dir / f"ltr_matrix_{split}.csv"
    group_path = data_dir / f"ltr_group_{split}.txt"
    features_path = data_dir / f"ltr_features_{split}.jsonl"

    groups = _read_group(group_path)
    feature_rows = _iter_jsonl(features_path)
    labels, matrix, feature_error = _load_matrix(matrix_path, features)

    expected_rows = sum(groups)
    if expected_rows != len(labels):
        raise ValueError(f"{split} group sum {expected_rows} != matrix rows {len(labels)}")
    if len(feature_rows) != len(labels):
        raise ValueError(f"{split} feature rows {len(feature_rows)} != matrix rows {len(labels)}")
    if features and not feature_error and len(matrix) != len(labels):
        raise ValueError(f"{split} feature matrix rows {len(matrix)} != matrix rows {len(labels)}")

    return SplitData(
        split=split,
        labels=labels,
        groups=groups,
        feature_rows=feature_rows,
        matrix=matrix,
        feature_error=feature_error,
    )


def _load_gate_margin(path: Path) -> float:
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected_gate = payload.get("selected_gate") or {}
    if selected_gate.get("mode") != "strict_or_margin":
        raise ValueError(f"{path} selected_gate.mode must be strict_or_margin")
    margin = selected_gate.get("margin")
    if margin is None:
        raise ValueError(f"{path} selected_gate.margin is missing")
    return float(margin)


def _load_runtime(
    *,
    requested: bool,
    model_path: Path,
    whitelist_path: Path,
    safety_gate_path: Path,
    policy: str,
    whitelist_error: str,
) -> LtrRuntime:
    runtime = LtrRuntime(
        requested=requested,
        active=False,
        model_path=model_path,
        whitelist_path=whitelist_path,
        safety_gate_path=safety_gate_path,
        policy=policy,
        feature_version=whitelist_path.name,
    )
    if not requested:
        runtime.fallback_reason = "switch_disabled"
        return runtime
    if whitelist_error:
        runtime.fallback_reason = whitelist_error
        return runtime

    try:
        margin = _load_gate_margin(safety_gate_path)
    except Exception as exc:  # noqa: BLE001
        runtime.fallback_reason = f"safety_gate_load_error: {exc}"
        return runtime

    if policy != "A_plus_B_narrow":
        runtime.fallback_reason = f"unsupported_policy: {policy}"
        return runtime

    try:
        import lightgbm as lgb  # noqa: PLC0415

        from goal_ltr_safety_gate_whatif_narrow import _narrow_decision  # noqa: PLC0415

        runtime.booster = lgb.Booster(model_file=str(model_path))
        runtime.decision = _narrow_decision
    except Exception as exc:  # noqa: BLE001
        runtime.fallback_reason = f"model_or_policy_load_error: {exc}"
        return runtime

    runtime.margin = margin
    runtime.active = True
    return runtime


def _dcg(labels: list[int], order: list[int], k: int) -> float:
    score = 0.0
    for rank, idx in enumerate(order[:k], start=1):
        rel = float(labels[idx])
        if rel <= 0:
            continue
        score += (2.0**rel - 1.0) / math.log2(rank + 1.0)
    return score


def _ndcg_at(labels: list[int], order: list[int], k: int) -> float:
    ideal = sorted(range(len(labels)), key=lambda idx: (-labels[idx], idx))
    ideal_score = _dcg(labels, ideal, k)
    if ideal_score <= 0:
        return 0.0
    return _dcg(labels, order, k) / ideal_score


def _first_positive_rank(labels: list[int], order: list[int]) -> int | None:
    for rank, idx in enumerate(order, start=1):
        if labels[idx] > 0:
            return rank
    return None


def _base_metrics(prefix: str, labels: list[int], order: list[int]) -> dict[str, Any]:
    positive_rank = _first_positive_rank(labels, order)
    return {
        f"{prefix}_positive_rank": positive_rank,
        f"{prefix}_hit1": bool(positive_rank == 1),
        f"{prefix}_hit5": bool(positive_rank is not None and positive_rank <= 5),
        f"{prefix}_ndcg1": _ndcg_at(labels, order, 1),
        f"{prefix}_ndcg5": _ndcg_at(labels, order, 5),
    }


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return int(float(row.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _same_non_empty(left: str, right: str) -> bool:
    return bool(left and right and left == right)


def _has_param_support(row: dict[str, Any]) -> bool:
    return any(_int(row, feature) > 0 for feature in PARAM_MATCH_FEATURES)


def _gate_facts(
    baseline_row: dict[str, Any],
    ltr_row: dict[str, Any],
    baseline_score: float,
    ltr_score: float,
) -> dict[str, Any]:
    baseline_family = _clean(baseline_row.get("candidate_family"))
    ltr_family = _clean(ltr_row.get("candidate_family"))
    baseline_book = _clean(baseline_row.get("quota_book"))
    ltr_book = _clean(ltr_row.get("quota_book"))

    same_family = _same_non_empty(baseline_family, ltr_family)
    same_book = _same_non_empty(baseline_book, ltr_book)
    no_family_conflict = _int(ltr_row, "family_conflict") == 0 and _int(ltr_row, "has_family_conflict_reason") == 0
    no_book_conflict = _int(ltr_row, "book_conflict") == 0 and _int(ltr_row, "has_book_conflict_reason") == 0
    no_param_conflict = _int(ltr_row, "param_conflict_count") == 0 and _int(ltr_row, "has_param_conflict_reason") == 0
    strict_same_family_book_param = same_family and same_book and no_family_conflict and no_book_conflict and no_param_conflict

    return {
        "baseline_family": baseline_family,
        "ltr_family": ltr_family,
        "baseline_book": baseline_book,
        "ltr_book": ltr_book,
        "same_family": same_family,
        "same_book": same_book,
        "no_family_conflict": no_family_conflict,
        "no_book_conflict": no_book_conflict,
        "no_param_conflict": no_param_conflict,
        "strict_same_family_book_param": strict_same_family_book_param,
        "ltr_param_support": _has_param_support(ltr_row),
        "score_margin": float(ltr_score - baseline_score),
    }


def _gated_order(raw_order: list[int], allow_override: bool) -> list[int]:
    if allow_override:
        return raw_order
    return [0, *[idx for idx in raw_order if idx != 0]]


def _candidate_label(row: dict[str, Any]) -> str:
    return f"{_clean(row.get('quota_id'))} {_clean(row.get('quota_name'))}".strip()


def _empty_facts() -> dict[str, Any]:
    return {
        "same_family": False,
        "same_book": False,
        "no_param_conflict": False,
        "score_margin": 0.0,
    }


def _predict_split(split_data: SplitData, runtime: LtrRuntime) -> tuple[list[float] | None, float, str]:
    if not runtime.active:
        return None, 0.0, runtime.fallback_reason
    if split_data.feature_error:
        return None, 0.0, split_data.feature_error
    if not split_data.matrix:
        return None, 0.0, "empty_feature_matrix"

    started = time.perf_counter()
    try:
        preds = runtime.booster.predict(split_data.matrix, num_iteration=runtime.booster.current_iteration())
        return [float(item) for item in preds], (time.perf_counter() - started) * 1000.0, ""
    except Exception as exc:  # noqa: BLE001
        return None, 0.0, f"ltr_predict_error: {exc}"


def _evaluate_split(
    *,
    split_data: SplitData,
    runtime: LtrRuntime,
    details_path: Path,
) -> dict[str, Any]:
    details_path.parent.mkdir(parents=True, exist_ok=True)
    preds, scoring_ms, split_fallback = _predict_split(split_data, runtime)
    ltr_active = preds is not None and not split_fallback
    score_ms_per_group = scoring_ms / len(split_data.groups) if ltr_active and split_data.groups else 0.0

    total = len(split_data.groups)
    positive_groups = 0
    fallback_count = 0
    baseline_hit1 = baseline_hit5 = 0
    raw_ltr_hit1 = raw_ltr_hit5 = 0
    final_hit1 = final_hit5 = 0
    hit1_gain = hit1_loss = hit5_gain = hit5_loss = 0
    raw_override_count = gated_override_count = 0
    final_ranks: list[float] = []
    latency_ms: list[float] = []
    gate_reasons: Counter[str] = Counter()
    fallback_reasons: Counter[str] = Counter()

    start = 0
    with details_path.open("w", encoding="utf-8") as handle:
        for group_idx, size in enumerate(split_data.groups):
            stop = start + size
            labels = split_data.labels[start:stop]
            group_rows = split_data.feature_rows[start:stop]
            group_preds = preds[start:stop] if ltr_active and preds is not None else []
            baseline_order = list(range(size))
            baseline_idx = 0
            baseline_row = group_rows[baseline_idx] if group_rows else {}
            baseline_metrics = _base_metrics("baseline", labels, baseline_order) if size else {}
            group_fallback = split_fallback
            gate_allowed = False
            gate_reason = group_fallback or "switch_disabled"
            facts = _empty_facts()
            raw_order = baseline_order
            ltr_top_idx = baseline_idx
            gate_started = time.perf_counter()

            if ltr_active and size > 1:
                raw_order = sorted(range(size), key=lambda idx: (-group_preds[idx], idx))
                ltr_top_idx = int(raw_order[0])
                ltr_row = group_rows[ltr_top_idx]
                facts = _gate_facts(
                    baseline_row,
                    ltr_row,
                    float(group_preds[baseline_idx]),
                    float(group_preds[ltr_top_idx]),
                )
                variant = {
                    "name": "whatif_A_plus_B_narrow",
                    "mode": "whatif_a_plus_b_narrow",
                    "margin": runtime.margin,
                }
                try:
                    gate_allowed, gate_reason = runtime.decision(
                        variant=variant,
                        ltr_top_idx=ltr_top_idx,
                        facts=facts,
                        query=_clean(baseline_row.get("query")),
                        baseline_name=_clean(baseline_row.get("quota_name")),
                        ltr_name=_clean(ltr_row.get("quota_name")),
                    )
                except Exception as exc:  # noqa: BLE001
                    group_fallback = f"gate_error: {exc}"
                    gate_allowed = False
                    gate_reason = group_fallback
                    raw_order = baseline_order
                    ltr_top_idx = baseline_idx
            elif ltr_active and size <= 1:
                group_fallback = "candidate_count_le_1"
                gate_reason = group_fallback

            gate_ms = (time.perf_counter() - gate_started) * 1000.0
            if group_fallback:
                fallback_count += 1
                fallback_reasons[group_fallback] += 1

            final_order = _gated_order(raw_order, bool(gate_allowed)) if not group_fallback else baseline_order
            raw_metrics = _base_metrics("raw_ltr", labels, raw_order) if size else {}
            final_metrics = _base_metrics("final", labels, final_order) if size else {}
            final_rank = final_metrics.get("final_positive_rank")
            if final_rank is not None:
                final_ranks.append(float(final_rank))

            base_h1 = bool(baseline_metrics.get("baseline_hit1"))
            base_h5 = bool(baseline_metrics.get("baseline_hit5"))
            raw_h1 = bool(raw_metrics.get("raw_ltr_hit1"))
            raw_h5 = bool(raw_metrics.get("raw_ltr_hit5"))
            final_h1 = bool(final_metrics.get("final_hit1"))
            final_h5 = bool(final_metrics.get("final_hit5"))
            has_positive = any(label > 0 for label in labels)

            positive_groups += int(has_positive)
            baseline_hit1 += int(base_h1)
            baseline_hit5 += int(base_h5)
            raw_ltr_hit1 += int(raw_h1)
            raw_ltr_hit5 += int(raw_h5)
            final_hit1 += int(final_h1)
            final_hit5 += int(final_h5)
            hit1_gain += int((not base_h1) and final_h1)
            hit1_loss += int(base_h1 and not final_h1)
            hit5_gain += int((not base_h5) and final_h5)
            hit5_loss += int(base_h5 and not final_h5)
            raw_override_count += int(ltr_top_idx != baseline_idx and ltr_active)
            gated_override_count += int(ltr_top_idx != baseline_idx and ltr_active and bool(gate_allowed) and not group_fallback)
            gate_reasons[gate_reason] += 1

            ltr_row = group_rows[ltr_top_idx] if group_rows else {}
            final_idx = final_order[0] if final_order else baseline_idx
            final_row = group_rows[final_idx] if group_rows else {}
            ltr_latency = score_ms_per_group + gate_ms if ltr_active else 0.0
            latency_ms.append(ltr_latency)
            detail = {
                "split": split_data.split,
                "group_index": group_idx + 1,
                "group_id": _clean(baseline_row.get("group_id")),
                "sample_id": _clean(baseline_row.get("sample_id")),
                "source_file": _clean(baseline_row.get("source_file")),
                "project_name": _clean(baseline_row.get("project_name")),
                "province": _clean(baseline_row.get("province")),
                "query": _clean(baseline_row.get("query")),
                "expected_ids": _clean(baseline_row.get("expected_ids")),
                "has_positive": has_positive,
                "positive_count": sum(1 for label in labels if label > 0),
                "goal_ltr_enabled": bool(runtime.requested),
                "goal_ltr_active": bool(ltr_active and not group_fallback),
                "goal_ltr_model_path": str(runtime.model_path),
                "goal_ltr_policy": runtime.policy,
                "goal_ltr_feature_version": runtime.feature_version,
                "baseline_top_id": _clean(baseline_row.get("quota_id")),
                "baseline_top_name": _clean(baseline_row.get("quota_name")),
                "baseline_top": _candidate_label(baseline_row),
                "ltr_top_id": _clean(ltr_row.get("quota_id")) if ltr_active else "",
                "ltr_top_name": _clean(ltr_row.get("quota_name")) if ltr_active else "",
                "ltr_top": _candidate_label(ltr_row) if ltr_active else "",
                "ltr_top_original_rank": int(ltr_top_idx + 1) if ltr_active else None,
                "final_top_id": _clean(final_row.get("quota_id")),
                "final_top_name": _clean(final_row.get("quota_name")),
                "final_top": _candidate_label(final_row),
                "ltr_score_margin": round(float(facts["score_margin"]), 8),
                "gate_allowed": bool(gate_allowed and not group_fallback),
                "gate_reason": gate_reason,
                "same_family": bool(facts["same_family"]),
                "same_book": bool(facts["same_book"]),
                "no_param_conflict": bool(facts["no_param_conflict"]),
                "fallback_reason": group_fallback,
                "top1_delta_vs_baseline": int(final_h1) - int(base_h1),
                "top5_delta_vs_baseline": int(final_h5) - int(base_h5),
                "latency_ltr_ms": round(ltr_latency, 4),
                **baseline_metrics,
                **raw_metrics,
                **final_metrics,
            }
            handle.write(json.dumps(detail, ensure_ascii=False, separators=(",", ":")) + "\n")
            start = stop

    return {
        "split": split_data.split,
        "groups": total,
        "positive_groups": positive_groups,
        "positive_group_rate": _rate(positive_groups, total),
        "goal_ltr_requested": bool(runtime.requested),
        "goal_ltr_active": bool(ltr_active),
        "fallback_count": fallback_count,
        "fallback_reasons": dict(fallback_reasons),
        "baseline_hit1": baseline_hit1,
        "baseline_hit1_rate": _rate(baseline_hit1, total),
        "baseline_hit5": baseline_hit5,
        "baseline_hit5_rate": _rate(baseline_hit5, total),
        "raw_ltr_hit1": raw_ltr_hit1 if ltr_active else None,
        "raw_ltr_hit1_rate": _rate(raw_ltr_hit1, total) if ltr_active else None,
        "raw_ltr_hit5": raw_ltr_hit5 if ltr_active else None,
        "raw_ltr_hit5_rate": _rate(raw_ltr_hit5, total) if ltr_active else None,
        "final_hit1": final_hit1,
        "final_hit1_rate": _rate(final_hit1, total),
        "final_hit5": final_hit5,
        "final_hit5_rate": _rate(final_hit5, total),
        "final_hit1_gain": hit1_gain,
        "final_hit1_loss": hit1_loss,
        "final_hit1_net": hit1_gain - hit1_loss,
        "final_hit5_gain": hit5_gain,
        "final_hit5_loss": hit5_loss,
        "final_hit5_net": hit5_gain - hit5_loss,
        "raw_override_count": raw_override_count,
        "gated_override_count": gated_override_count,
        "gated_override_rate": _rate(gated_override_count, raw_override_count),
        "gate_reasons": dict(gate_reasons),
        "final_rank_avg": _mean(final_ranks),
        "latency_ltr_ms_avg": _mean(latency_ms),
        "latency_ltr_ms_total": round(sum(latency_ms), 4),
        "details_jsonl": str(details_path),
    }


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summaries = report["split_summaries"]
    lines = [
        "# Goal Eval LTR Switch Summary",
        "",
        "Stage 3.1 eval-only switch skeleton. No production search integration and no default behavior change.",
        "",
        "## Inputs",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["goal_ltr_rerank_enabled", report["goal_ltr_rerank_enabled"]],
                ["model_path", report["model_path"]],
                ["feature_whitelist_path", report["feature_whitelist_path"]],
                ["safety_gate_path", report["safety_gate_path"]],
                ["policy", report["policy"]],
                ["splits", ", ".join(report["splits"])],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Metrics",
        "",
        _md_table(
            [
                [
                    "split",
                    "active",
                    "groups",
                    "baseline_top1",
                    "final_top1",
                    "net",
                    "gain",
                    "loss",
                    "baseline_top5",
                    "final_top5",
                    "top5_net",
                    "fallbacks",
                ],
                *[
                    [
                        item["split"],
                        item["goal_ltr_active"],
                        item["groups"],
                        item["baseline_hit1_rate"],
                        item["final_hit1_rate"],
                        item["final_hit1_net"],
                        item["final_hit1_gain"],
                        item["final_hit1_loss"],
                        item["baseline_hit5_rate"],
                        item["final_hit5_rate"],
                        item["final_hit5_net"],
                        item["fallback_count"],
                    ]
                    for item in summaries
                ],
            ]
        ),
        "",
        "## Details",
        "",
        _md_table([["split", "details_jsonl"]] + [[item["split"], item["details_jsonl"]] for item in summaries]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval-only Goal LTR rerank switch skeleton")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--split", action="append", help="Split to evaluate. Can be repeated.")
    parser.add_argument("--splits", nargs="+", default=None)
    parser.add_argument("--goal-ltr-rerank-enabled", action="store_true")
    parser.add_argument("--model-path", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--feature-whitelist-path", default=str(DEFAULT_WHITELIST_PATH))
    parser.add_argument("--safety-gate-path", default=str(DEFAULT_SAFETY_GATE_PATH))
    parser.add_argument("--policy", default="A_plus_B_narrow")
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    splits = args.split or args.splits or ["heldout", "hard"]
    data_dir = Path(args.data_dir)
    model_path = Path(args.model_path)
    whitelist_path = Path(args.feature_whitelist_path)
    safety_gate_path = Path(args.safety_gate_path)

    features: list[str] = []
    whitelist_error = ""
    try:
        features = _load_feature_whitelist(whitelist_path)
    except Exception as exc:  # noqa: BLE001
        whitelist_error = f"whitelist_load_error: {exc}"

    runtime = _load_runtime(
        requested=bool(args.goal_ltr_rerank_enabled),
        model_path=model_path,
        whitelist_path=whitelist_path,
        safety_gate_path=safety_gate_path,
        policy=args.policy,
        whitelist_error=whitelist_error,
    )

    split_summaries: list[dict[str, Any]] = []
    for split in splits:
        split_data = _load_split(data_dir, split, features if not whitelist_error else [])
        details_path = Path(args.details_dir) / f"goal_eval_ltr_switch_details_{split}.jsonl"
        split_summaries.append(_evaluate_split(split_data=split_data, runtime=runtime, details_path=details_path))

    report = {
        "stage": "Goal LTR v1 / stage 3.1 eval-only switch skeleton",
        "no_search_integration": True,
        "goal_ltr_rerank_enabled": bool(args.goal_ltr_rerank_enabled),
        "runtime_active": bool(runtime.active),
        "runtime_fallback_reason": runtime.fallback_reason,
        "model_path": str(model_path),
        "feature_whitelist_path": str(whitelist_path),
        "safety_gate_path": str(safety_gate_path),
        "policy": args.policy,
        "splits": splits,
        "feature_count": len(features),
        "whitelist_error": whitelist_error,
        "split_summaries": split_summaries,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_search_integration": True,
                    "goal_ltr_rerank_enabled": report["goal_ltr_rerank_enabled"],
                    "runtime_active": report["runtime_active"],
                    "runtime_fallback_reason": report["runtime_fallback_reason"],
                    "splits": splits,
                    "elapsed_sec": report["elapsed_sec"],
                },
                "split_summaries": split_summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
