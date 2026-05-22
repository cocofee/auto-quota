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

import goal_ltr_safety_gate_eval as gate_eval
import goal_ltr_safety_gate_whatif as base_whatif

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_GATE_CONFIG = PROJECT_ROOT / "data" / "goal_search" / "ltr_safety_gate_oof_v1.json"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_whatif_narrow_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_whatif_narrow_summary.md"
DEFAULT_VARIANTS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_ltr_safety_gate_whatif_narrow_variants.csv"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _numbers(text: str) -> list[float]:
    return [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text or "")]


def _has_digit(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


def _has_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _same_box_method_or_tier(query: str, ltr_name: str) -> tuple[bool, str]:
    if "配电箱" not in query or "配电箱" not in ltr_name:
        return False, ""

    if "落地" in query:
        return ("落地" in ltr_name, "electrical_box_ground")

    wants_hanging = "悬挂" in query or "嵌入" in query or "明装" in query or "暗装" in query
    if wants_hanging:
        method_ok = ("悬挂" in ltr_name or "嵌入" in ltr_name or "明装" in ltr_name or "暗装" in ltr_name)
        if not method_ok:
            return False, "electrical_box_mount_method_mismatch"

    if "半周长" in query:
        query_nums = _numbers(query)
        ltr_nums = _numbers(ltr_name)
        if query_nums and ltr_nums:
            return (any(abs(q - l) < 1e-6 for q in query_nums for l in ltr_nums), "electrical_box_half_perimeter")
        return False, "electrical_box_half_perimeter_missing"

    if wants_hanging:
        return True, "electrical_box_mount_method"

    return False, "electrical_box_no_tier_evidence"


def _narrow_subtype_or_tier_protection(
    *,
    query: str,
    baseline_name: str,
    ltr_name: str,
    facts: dict[str, Any],
) -> tuple[bool, str]:
    if not facts["strict_same_family_book_param"]:
        return False, ""

    if "引下线" in ltr_name and "引下线" not in query:
        return True, "subtype_unrequested_lightning_downlead"
    if "电磁阀" in ltr_name and "电磁" not in query:
        return True, "subtype_unrequested_solenoid_valve"
    if "门型" in ltr_name and "门型" not in query:
        return True, "subtype_unrequested_support_shape"
    if "螺纹" in ltr_name and "螺纹" not in query:
        return True, "subtype_unrequested_connection"

    if _has_digit(query):
        return False, ""

    # Narrowed from 2.7: do not apply numeric tier protection to every object.
    # Keep only high-risk tiered objects observed in residual losses, and only
    # block upward jumps from the baseline tier.
    if not _has_any(query + baseline_name + ltr_name, ("灯", "灯具", "灯罩", "光缆")):
        return False, ""
    base_nums = _numbers(baseline_name)
    ltr_nums = _numbers(ltr_name)
    if base_nums and ltr_nums and max(ltr_nums) > max(base_nums):
        return True, "tier_unrequested_numeric_upshift_narrow"

    return False, ""


def _narrow_strong_object_cross_book_allow(
    *,
    query: str,
    ltr_name: str,
    facts: dict[str, Any],
) -> tuple[bool, str]:
    if facts["no_param_conflict"] is not True:
        return False, ""

    box_ok, box_reason = _same_box_method_or_tier(query, ltr_name)
    if box_ok:
        return True, f"strong_object_cross_book_{box_reason}"

    if "止回阀" in query and "止回阀" in ltr_name:
        return True, "strong_object_cross_book_check_valve"
    if "防火阀" in query and "防火阀" in ltr_name:
        return True, "strong_object_cross_book_fire_damper"
    if _has_any(query, ("电源时序器", "时序器")) and _has_any(ltr_name, ("时序电源", "电源控制器")):
        return True, "strong_object_cross_book_sequence_power"

    return False, box_reason


def _narrow_decision(
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

    protect, protect_reason = _narrow_subtype_or_tier_protection(
        query=query,
        baseline_name=baseline_name,
        ltr_name=ltr_name,
        facts=facts,
    )
    if frozen_allow and protect:
        return False, f"A_narrow_block_{protect_reason}"
    if variant["mode"] == "whatif_a_narrow":
        return frozen_allow, frozen_reason

    if variant["mode"] == "whatif_a_plus_b_narrow":
        if frozen_allow:
            return True, frozen_reason
        allow_b, reason_b = _narrow_strong_object_cross_book_allow(query=query, ltr_name=ltr_name, facts=facts)
        if allow_b:
            return True, f"B_narrow_allow_{reason_b}"
        return False, frozen_reason

    raise ValueError(f"unknown narrow what-if variant: {variant['mode']}")


def _make_variants(margin: float) -> list[dict[str, Any]]:
    return [
        {"name": "raw_ltr", "mode": "raw", "margin": margin},
        {"name": f"frozen_oof_margin_{str(margin).replace('.', '_')}", "mode": "frozen", "margin": margin},
        {"name": "whatif_A_narrow_tier_subtype_protect", "mode": "whatif_a_narrow", "margin": margin},
        {"name": "whatif_A_plus_B_narrow", "mode": "whatif_a_plus_b_narrow", "margin": margin},
    ]


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


def _interesting_examples(rows: list[dict[str, Any]], variant: str, limit: int = 14) -> list[list[object]]:
    selected = [
        row
        for row in rows
        if row["variant"] == variant
        and (
            row["gate_reason"].startswith("A_narrow_block_")
            or row["gate_reason"].startswith("B_narrow_allow_")
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


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summaries = report["variant_summaries"]
    rows = report["all_details"]
    lines = [
        "# Goal LTR Safety Gate Narrow What-If",
        "",
        "Stage 2.8 offline narrow what-if. It only changes two simulated checks: A numeric tier protection is no longer global, and B electrical-box cross-book allow requires explicit mounting or half-perimeter evidence.",
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
                ["split", "variant", "top1_rate", "top1_net", "gain", "loss", "prevented_loss", "blocked_gain", "top5_rate", "top5_net"],
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
                ["split", "variant", "top1_net_delta", "gain_delta", "loss_delta", "blocked_gain_delta", "prevented_loss_delta", "top5_net_delta"],
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
        "## Narrow A Examples",
        "",
        _md_table([["split", "reason", "query", "baseline_top", "raw_ltr_top", "gated_hit1"]] + _interesting_examples(rows, "whatif_A_narrow_tier_subtype_protect")),
        "",
        "## Narrow A+B Examples",
        "",
        _md_table([["split", "reason", "query", "baseline_top", "raw_ltr_top", "gated_hit1"]] + _interesting_examples(rows, "whatif_A_plus_B_narrow")),
        "",
        "## Notes",
        "",
        "- A numeric tier protection now only applies to high-risk lamp/cable-like tiered objects, and only on upward tier jumps when the query has no digit.",
        "- B still allows fire-damper and sequence-power strong object matches, but generic `配电箱` no longer crosses book without mounting or half-perimeter evidence.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8-sig")


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline narrow what-if evaluator for Goal LTR safety gate enhancements")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--gate-config", default=str(DEFAULT_GATE_CONFIG))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--variants-csv", default=str(DEFAULT_VARIANTS_CSV))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    gate_payload = base_whatif._load_gate_config(Path(args.gate_config))
    selected_gate = gate_payload["selected_gate"]
    margin = float(selected_gate["margin"])
    model_path = Path(gate_payload["model_path"])
    whitelist_path = Path(gate_payload["whitelist"])
    features = gate_eval._load_feature_whitelist(whitelist_path)
    booster = lgb.Booster(model_file=str(model_path))
    variants = _make_variants(margin)

    old_decision = base_whatif._whatif_decision
    base_whatif._whatif_decision = _narrow_decision
    try:
        all_summaries: list[dict[str, Any]] = []
        all_details: list[dict[str, Any]] = []
        detail_paths: dict[str, str] = {}
        for split in args.splits:
            x, y, groups, feature_rows = gate_eval._load_split(Path(args.data_dir), split, features)
            preds = booster.predict(x, num_iteration=booster.current_iteration())
            details_path = Path(args.details_dir) / f"goal_ltr_safety_gate_whatif_narrow_details_{split}.jsonl"
            summaries, variant_rows = base_whatif._evaluate_split(
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
    finally:
        base_whatif._whatif_decision = old_decision

    report = {
        "stage": "Goal LTR v1 / stage 2.8 narrow safety gate what-if",
        "no_search_integration": True,
        "model_path": str(model_path),
        "gate_config": str(Path(args.gate_config)),
        "margin": margin,
        "splits": args.splits,
        "variants": variants,
        "variant_summaries": all_summaries,
        "comparison_vs_frozen": base_whatif._comparison_vs_frozen(all_summaries),
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
                "comparison_vs_frozen": report["comparison_vs_frozen"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
