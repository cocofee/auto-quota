from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import lightgbm as lgb
import numpy as np

import goal_ltr_safety_gate_eval as gate_eval

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_GATE_CONFIG = PROJECT_ROOT / "data" / "goal_search" / "ltr_safety_gate_oof_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_whatif_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_whatif_summary.md"
DEFAULT_VARIANTS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_whatif_variants.csv"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _contains(text: str, token: str) -> bool:
    return token in text


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", text)


def _has_digit(text: str) -> bool:
    return bool(re.search(r"\d", text))


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _subtype_or_tier_protection(
    *,
    query: str,
    baseline_name: str,
    ltr_name: str,
    facts: dict[str, Any],
) -> tuple[bool, str]:
    if not facts["strict_same_family_book_param"]:
        return False, ""

    if _contains(ltr_name, "引下线") and not _contains(query, "引下线"):
        return True, "subtype_unrequested_lightning_downlead"
    if _contains(ltr_name, "电磁阀") and not _contains(query, "电磁"):
        return True, "subtype_unrequested_solenoid_valve"
    if _contains(ltr_name, "门型") and not _contains(query, "门型"):
        return True, "subtype_unrequested_support_shape"
    if _contains(ltr_name, "螺纹") and not _contains(query, "螺纹"):
        return True, "subtype_unrequested_connection"

    # If the query does not expose a numeric tier, do not let strict same-family
    # matching silently jump to another tier.
    if not _has_digit(query):
        base_numbers = _numbers(baseline_name)
        ltr_numbers = _numbers(ltr_name)
        if base_numbers and ltr_numbers and base_numbers != ltr_numbers:
            return True, "tier_unrequested_numeric_shift"

    return False, ""


def _strong_object_cross_book_allow(
    *,
    query: str,
    ltr_name: str,
    facts: dict[str, Any],
) -> tuple[bool, str]:
    if facts["no_param_conflict"] is not True:
        return False, ""

    if _contains(query, "配电箱") and _contains(ltr_name, "配电箱"):
        return True, "strong_object_cross_book_electrical_box"
    if _contains(query, "止回阀") and _contains(ltr_name, "止回阀"):
        return True, "strong_object_cross_book_check_valve"
    if _contains(query, "防火阀") and _contains(ltr_name, "防火阀"):
        return True, "strong_object_cross_book_fire_damper"
    if _has_any(query, ("电源时序器", "时序器")) and _has_any(ltr_name, ("时序电源", "电源控制器")):
        return True, "strong_object_cross_book_sequence_power"

    return False, ""


def _load_gate_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    selected_gate = payload.get("selected_gate") or {}
    if selected_gate.get("mode") != "strict_or_margin":
        raise ValueError(f"{path} selected gate must be strict_or_margin")
    if selected_gate.get("margin") is None:
        raise ValueError(f"{path} selected gate missing margin")
    return payload


def _whatif_decision(
    *,
    variant: dict[str, Any],
    ltr_top_idx: int,
    facts: dict[str, Any],
    query: str,
    baseline_name: str,
    ltr_name: str,
) -> tuple[bool, str]:
    if ltr_top_idx == 0:
        return True, "same_as_baseline"
    if variant["mode"] == "raw":
        return True, "raw_ltr"

    frozen_variant = {"name": "frozen", "mode": "strict_or_margin", "margin": variant["margin"]}
    frozen_allow, frozen_reason = gate_eval._gate_decision(
        variant=frozen_variant,
        ltr_top_idx=ltr_top_idx,
        facts=facts,
    )
    if variant["mode"] == "frozen":
        return frozen_allow, frozen_reason

    protect, protect_reason = _subtype_or_tier_protection(
        query=query,
        baseline_name=baseline_name,
        ltr_name=ltr_name,
        facts=facts,
    )
    if frozen_allow and protect:
        return False, f"A_block_{protect_reason}"
    if variant["mode"] == "whatif_a":
        return frozen_allow, frozen_reason

    if variant["mode"] == "whatif_a_plus_b":
        if frozen_allow:
            return True, frozen_reason
        allow_b, reason_b = _strong_object_cross_book_allow(query=query, ltr_name=ltr_name, facts=facts)
        if allow_b:
            return True, f"B_allow_{reason_b}"
        return False, frozen_reason

    raise ValueError(f"unknown what-if variant: {variant['mode']}")


def _make_variants(margin: float) -> list[dict[str, Any]]:
    return [
        {"name": "raw_ltr", "mode": "raw", "margin": margin},
        {"name": f"frozen_oof_margin_{str(margin).replace('.', '_')}", "mode": "frozen", "margin": margin},
        {"name": "whatif_A_implicit_tier_subtype_protect", "mode": "whatif_a", "margin": margin},
        {"name": "whatif_A_plus_B_strong_object_cross_book", "mode": "whatif_a_plus_b", "margin": margin},
    ]


def _evaluate_split(
    *,
    split: str,
    labels: np.ndarray,
    groups: list[int],
    feature_rows: list[dict[str, Any]],
    preds: np.ndarray,
    variants: list[dict[str, Any]],
    details_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    details_path.parent.mkdir(parents=True, exist_ok=True)
    variant_rows: dict[str, list[dict[str, Any]]] = {variant["name"]: [] for variant in variants}
    start = 0
    with details_path.open("w", encoding="utf-8") as handle:
        for group_idx, size in enumerate(groups):
            stop = start + size
            group_labels = labels[start:stop]
            group_preds = preds[start:stop]
            group_rows = feature_rows[start:stop]
            baseline_order = np.arange(size)
            raw_order = np.lexsort((np.arange(size), -group_preds))
            baseline_idx = 0
            ltr_top_idx = int(raw_order[0])
            baseline_row = group_rows[baseline_idx]
            ltr_top_row = group_rows[ltr_top_idx]
            facts = gate_eval._gate_facts(
                baseline_row,
                ltr_top_row,
                float(group_preds[baseline_idx]),
                float(group_preds[ltr_top_idx]),
            )
            baseline_metrics = gate_eval._base_metrics("baseline", group_labels, baseline_order)
            raw_metrics = gate_eval._base_metrics("raw_ltr", group_labels, raw_order)
            query = _clean(baseline_row.get("query"))
            baseline_name = _clean(baseline_row.get("quota_name"))
            ltr_name = _clean(ltr_top_row.get("quota_name"))
            has_positive = bool(np.any(group_labels > 0))

            for variant in variants:
                allow_override, gate_reason = _whatif_decision(
                    variant=variant,
                    ltr_top_idx=ltr_top_idx,
                    facts=facts,
                    query=query,
                    baseline_name=baseline_name,
                    ltr_name=ltr_name,
                )
                order = gate_eval._gated_order(raw_order, allow_override)
                gated_metrics = gate_eval._base_metrics("gated", group_labels, order)
                gated_top_idx = int(order[0])
                gated_top_row = group_rows[gated_top_idx]
                detail = {
                    "split": split,
                    "variant": variant["name"],
                    "group_index": group_idx + 1,
                    "group_id": _clean(baseline_row.get("group_id")),
                    "sample_id": _clean(baseline_row.get("sample_id")),
                    "source_file": _clean(baseline_row.get("source_file")),
                    "project_name": _clean(baseline_row.get("project_name")),
                    "province": _clean(baseline_row.get("province")),
                    "query": query,
                    "expected_ids": _clean(baseline_row.get("expected_ids")),
                    "has_positive": has_positive,
                    "positive_count": int(np.sum(group_labels > 0)),
                    "raw_same_as_baseline": ltr_top_idx == baseline_idx,
                    "gate_allowed": bool(allow_override),
                    "gate_reason": gate_reason,
                    "score_margin": round(float(facts["score_margin"]), 8),
                    "same_family": bool(facts["same_family"]),
                    "same_book": bool(facts["same_book"]),
                    "no_family_conflict": bool(facts["no_family_conflict"]),
                    "no_book_conflict": bool(facts["no_book_conflict"]),
                    "no_param_conflict": bool(facts["no_param_conflict"]),
                    "strict_same_family_book_param": bool(facts["strict_same_family_book_param"]),
                    "ltr_param_support": bool(facts["ltr_param_support"]),
                    "baseline_top_original_rank": 1,
                    "baseline_top_score": round(float(group_preds[baseline_idx]), 8),
                    "baseline_top_id": _clean(baseline_row.get("quota_id")),
                    "baseline_top_name": baseline_name,
                    "baseline_top_family": _clean(baseline_row.get("candidate_family")),
                    "baseline_top_book": _clean(baseline_row.get("quota_book")),
                    "baseline_top": gate_eval._candidate_label(baseline_row),
                    "raw_ltr_top_original_rank": int(ltr_top_idx + 1),
                    "raw_ltr_top_score": round(float(group_preds[ltr_top_idx]), 8),
                    "raw_ltr_top_id": _clean(ltr_top_row.get("quota_id")),
                    "raw_ltr_top_name": ltr_name,
                    "raw_ltr_top_family": _clean(ltr_top_row.get("candidate_family")),
                    "raw_ltr_top_book": _clean(ltr_top_row.get("quota_book")),
                    "raw_ltr_top": gate_eval._candidate_label(ltr_top_row),
                    "gated_top_original_rank": int(gated_top_idx + 1),
                    "gated_top_score": round(float(group_preds[gated_top_idx]), 8),
                    "gated_top_id": _clean(gated_top_row.get("quota_id")),
                    "gated_top_name": _clean(gated_top_row.get("quota_name")),
                    "gated_top_family": _clean(gated_top_row.get("candidate_family")),
                    "gated_top_book": _clean(gated_top_row.get("quota_book")),
                    "gated_top": gate_eval._candidate_label(gated_top_row),
                    **baseline_metrics,
                    **raw_metrics,
                    **gated_metrics,
                }
                detail.update(
                    {
                        "hit1_delta_vs_baseline": int(detail["gated_hit1"]) - int(detail["baseline_hit1"]),
                        "hit5_delta_vs_baseline": int(detail["gated_hit5"]) - int(detail["baseline_hit5"]),
                        "raw_hit1_delta_vs_baseline": int(detail["raw_ltr_hit1"]) - int(detail["baseline_hit1"]),
                        "raw_hit5_delta_vs_baseline": int(detail["raw_ltr_hit5"]) - int(detail["baseline_hit5"]),
                    }
                )
                variant_rows[variant["name"]].append(detail)
                handle.write(json.dumps(detail, ensure_ascii=False, separators=(",", ":")) + "\n")
            start = stop

    summaries = [gate_eval._summarize_variant(split, variant, variant_rows[variant["name"]]) for variant in variants]
    return summaries, variant_rows


def _write_variants_csv(path: Path, summaries: list[dict[str, Any]]) -> None:
    fields = [
        "split",
        "variant",
        "mode",
        "margin",
        "groups",
        "baseline_hit1_rate",
        "raw_ltr_hit1_rate",
        "gated_hit1_rate",
        "gated_hit1_net",
        "gated_hit1_gain",
        "gated_hit1_loss",
        "prevented_raw_hit1_loss",
        "blocked_raw_hit1_gain",
        "gated_hit5_rate",
        "gated_hit5_net",
        "raw_override_count",
        "gated_override_count",
        "gated_override_rate",
        "gated_ndcg5",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(summaries)


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


def _interesting_examples(rows: list[dict[str, Any]], variant: str, limit: int = 12) -> list[list[object]]:
    selected = [
        row
        for row in rows
        if row["variant"] == variant
        and (
            row["gate_reason"].startswith("A_block_")
            or row["gate_reason"].startswith("B_allow_")
        )
    ]
    return [
        [
            row["split"],
            row["gate_reason"],
            row["query"],
            row["baseline_top"],
            row["raw_ltr_top"],
            row["gated_hit1"],
        ]
        for row in selected[:limit]
    ]


def _comparison_vs_frozen(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_split_variant = {(item["split"], item["variant"]): item for item in summaries}
    frozen_by_split = {
        split: item
        for (split, variant), item in by_split_variant.items()
        if variant.startswith("frozen_oof_margin_")
    }
    comparisons: list[dict[str, Any]] = []
    for (split, variant), item in sorted(by_split_variant.items()):
        if variant == "raw_ltr" or variant.startswith("frozen_oof_margin_"):
            continue
        frozen = frozen_by_split[split]
        comparisons.append(
            {
                "split": split,
                "variant": variant,
                "top1_net_delta_vs_frozen": int(item["gated_hit1_net"]) - int(frozen["gated_hit1_net"]),
                "top1_gain_delta_vs_frozen": int(item["gated_hit1_gain"]) - int(frozen["gated_hit1_gain"]),
                "top1_loss_delta_vs_frozen": int(item["gated_hit1_loss"]) - int(frozen["gated_hit1_loss"]),
                "blocked_gain_delta_vs_frozen": int(item["blocked_raw_hit1_gain"]) - int(frozen["blocked_raw_hit1_gain"]),
                "prevented_loss_delta_vs_frozen": int(item["prevented_raw_hit1_loss"]) - int(frozen["prevented_raw_hit1_loss"]),
                "top5_net_delta_vs_frozen": int(item["gated_hit5_net"]) - int(frozen["gated_hit5_net"]),
            }
        )
    return comparisons


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summaries = report["variant_summaries"]
    rows = report["all_details"]
    lines = [
        "# Goal LTR Safety Gate What-If",
        "",
        "Stage 2.7 offline what-if for Candidate A and A+B. It only simulates gate decisions on existing Top80 rows. No training, no search integration.",
        "",
        "## Inputs",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["model_path", report["model_path"]],
                ["gate_config", report["gate_config"]],
                ["margin", report["margin"]],
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
                    "variant",
                    "top1_rate",
                    "top1_net",
                    "gain",
                    "loss",
                    "prevented_loss",
                    "blocked_gain",
                    "top5_rate",
                    "top5_net",
                ],
                *[
                    [
                        item["split"],
                        item["variant"],
                        item["gated_hit1_rate"],
                        item["gated_hit1_net"],
                        item["gated_hit1_gain"],
                        item["gated_hit1_loss"],
                        item["prevented_raw_hit1_loss"],
                        item["blocked_raw_hit1_gain"],
                        item["gated_hit5_rate"],
                        item["gated_hit5_net"],
                    ]
                    for item in summaries
                ],
            ]
        ),
        "",
        "## Delta Vs Frozen",
        "",
        _md_table(
            [
                [
                    "split",
                    "variant",
                    "top1_net_delta",
                    "gain_delta",
                    "loss_delta",
                    "blocked_gain_delta",
                    "prevented_loss_delta",
                    "top5_net_delta",
                ],
                *[
                    [
                        item["split"],
                        item["variant"],
                        item["top1_net_delta_vs_frozen"],
                        item["top1_gain_delta_vs_frozen"],
                        item["top1_loss_delta_vs_frozen"],
                        item["blocked_gain_delta_vs_frozen"],
                        item["prevented_loss_delta_vs_frozen"],
                        item["top5_net_delta_vs_frozen"],
                    ]
                    for item in report["comparison_vs_frozen"]
                ],
            ]
        ),
        "",
        "## A Examples",
        "",
        _md_table([["split", "reason", "query", "baseline_top", "raw_ltr_top", "gated_hit1"]] + _interesting_examples(rows, "whatif_A_implicit_tier_subtype_protect")),
        "",
        "## A+B Examples",
        "",
        _md_table([["split", "reason", "query", "baseline_top", "raw_ltr_top", "gated_hit1"]] + _interesting_examples(rows, "whatif_A_plus_B_strong_object_cross_book")),
        "",
        "## Notes",
        "",
        "- A blocks strict same-family/book overrides when the LTR top introduces an unrequested tier or subtype.",
        "- B only adds a tiny strong-object cross-book allow after A; it does not override A blocks.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline what-if evaluator for Goal LTR safety gate enhancements")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--gate-config", default=str(DEFAULT_GATE_CONFIG))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--variants-csv", default=str(DEFAULT_VARIANTS_CSV))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    gate_payload = _load_gate_config(Path(args.gate_config))
    selected_gate = gate_payload["selected_gate"]
    margin = float(selected_gate["margin"])
    model_path = Path(gate_payload["model_path"])
    whitelist_path = Path(gate_payload["whitelist"])
    features = gate_eval._load_feature_whitelist(whitelist_path)
    booster = lgb.Booster(model_file=str(model_path))
    variants = _make_variants(margin)

    all_summaries: list[dict[str, Any]] = []
    all_details: list[dict[str, Any]] = []
    detail_paths: dict[str, str] = {}
    for split in args.splits:
        x, y, groups, feature_rows = gate_eval._load_split(Path(args.data_dir), split, features)
        preds = booster.predict(x, num_iteration=booster.current_iteration())
        details_path = Path(args.details_dir) / f"goal_ltr_safety_gate_whatif_details_{split}.jsonl"
        summaries, variant_rows = _evaluate_split(
            split=split,
            labels=y,
            groups=groups,
            feature_rows=feature_rows,
            preds=preds,
            variants=variants,
            details_path=details_path,
        )
        all_summaries.extend(summaries)
        for rows in variant_rows.values():
            all_details.extend(rows)
        detail_paths[split] = str(details_path)

    report = {
        "stage": "Goal LTR v1 / stage 2.7 safety gate feature what-if",
        "no_search_integration": True,
        "model_path": str(model_path),
        "gate_config": str(Path(args.gate_config)),
        "margin": margin,
        "splits": args.splits,
        "variants": variants,
        "variant_summaries": all_summaries,
        "comparison_vs_frozen": _comparison_vs_frozen(all_summaries),
        "details_jsonl": detail_paths,
        "all_details": all_details,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    json_report = {key: value for key, value in report.items() if key != "all_details"}
    report_json.write_text(json.dumps(json_report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_variants_csv(Path(args.variants_csv), all_summaries)
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_search_integration": True,
                    "margin": margin,
                    "splits": args.splits,
                    "elapsed_sec": report["elapsed_sec"],
                },
                "variant_summaries": all_summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
