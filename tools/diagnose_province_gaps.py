from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = ROOT / "tests" / "cross_province_baseline.json"
DEFAULT_RECALL_REPORT = ROOT / "output" / "diagnostics" / "stage3_top_gaps_report_full.json"
DEFAULT_OUTPUT = ROOT / "output" / "diagnostics" / "province_gap_report.json"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _safe_rate(value: object) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def recommend_focus(*, baseline_diag: dict, recall_categories: dict, rate: float) -> list[str]:
    actions: list[str] = []

    synonym_total = _safe_int(baseline_diag.get("synonym_gap")) + _safe_int(recall_categories.get("synonym_gap"))
    query_total = _safe_int(recall_categories.get("query_dilution"))
    wrong_tier = _safe_int(baseline_diag.get("wrong_tier"))
    wrong_book = _safe_int(baseline_diag.get("wrong_book")) + _safe_int(recall_categories.get("rule_mislead"))

    if synonym_total >= max(wrong_tier, wrong_book, 1):
        actions.append("priority_synonym_recall")
    if query_total >= 10:
        actions.append("priority_query_dilution")
    if wrong_tier >= max(_safe_int(baseline_diag.get("synonym_gap")), 8):
        actions.append("priority_tier_ranking")
    if wrong_book >= 5:
        actions.append("priority_book_routing")
    if rate <= 15:
        actions.append("province_red_alert")
    elif rate <= 30:
        actions.append("province_watchlist")

    if not actions:
        actions.append("monitor_only")
    return actions


def _priority_score(
    *,
    rate: float | None,
    recall_miss: int,
    baseline_diag: dict,
    recall_categories: dict,
) -> float:
    rate_penalty = max(0.0, 60.0 - rate) * 10 if rate is not None else 150.0
    return (
        rate_penalty
        + recall_miss
        + _safe_int(baseline_diag.get("synonym_gap")) * 3
        + _safe_int(baseline_diag.get("wrong_tier")) * 2
        + _safe_int(baseline_diag.get("wrong_book")) * 2
        + _safe_int(recall_categories.get("query_dilution")) * 2
        + _safe_int(recall_categories.get("rule_mislead")) * 2
    )


def build_province_gap_report(
    baseline_data: dict,
    recall_report: dict,
    *,
    top_n: int = 10,
) -> dict:
    baseline_provinces = dict(baseline_data.get("provinces") or {})
    recall_gaps = dict(recall_report.get("recall_gaps") or {})
    recall_counts = dict(recall_gaps.get("province_recall_miss_counts") or {})
    recall_category_counts = dict(recall_gaps.get("province_category_counts") or {})

    province_rows: list[dict] = []
    all_names = sorted(set(baseline_provinces) | set(recall_counts) | set(recall_category_counts))
    for province in all_names:
        baseline_row = dict(baseline_provinces.get(province) or {})
        baseline_diag = dict(baseline_row.get("diagnosis") or {})
        recall_categories = dict(recall_category_counts.get(province) or {})
        has_baseline = bool(baseline_row)
        rate = _safe_rate(baseline_row.get("rate")) if has_baseline else None
        recall_miss = _safe_int(recall_counts.get(province))
        total = _safe_int(baseline_row.get("total"))

        row = {
            "province": province,
            "rate": rate,
            "has_baseline": has_baseline,
            "total": total,
            "recall_miss_total": recall_miss,
            "baseline_diagnosis": baseline_diag,
            "recall_categories": recall_categories,
            "recommended_actions": recommend_focus(
                baseline_diag=baseline_diag,
                recall_categories=recall_categories,
                rate=rate or 0.0,
            ),
        }
        row["priority_score"] = _priority_score(
            rate=rate,
            recall_miss=recall_miss,
            baseline_diag=baseline_diag,
            recall_categories=recall_categories,
        )
        province_rows.append(row)

    province_rows.sort(
        key=lambda row: (
            -float(row["priority_score"]),
            float(row["rate"]) if row["rate"] is not None else -1.0,
            -int(row["recall_miss_total"]),
            row["province"],
        )
    )

    return {
        "meta": {
            "baseline_province_count": len(baseline_provinces),
            "recall_province_count": len(recall_counts),
            "top_n": top_n,
        },
        "priority_provinces": province_rows[:top_n],
        "all_provinces": province_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Combine province baseline and recall-gap reports into a province priority list.")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE), help="Cross-province baseline JSON path.")
    parser.add_argument("--recall-report", default=str(DEFAULT_RECALL_REPORT), help="Recall gap report JSON path.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    parser.add_argument("--top", type=int, default=10, help="Top province count to keep.")
    parser.add_argument("--preview", action="store_true", help="Print summary without writing file.")
    args = parser.parse_args()

    report = build_province_gap_report(
        load_json(Path(args.baseline)),
        load_json(Path(args.recall_report)),
        top_n=args.top,
    )

    print("priority_provinces:")
    for row in report["priority_provinces"]:
        rate_text = f"{row['rate']:>5.1f}" if row["rate"] is not None else "  n/a"
        print(
            f"  rate={rate_text} recall={row['recall_miss_total']:>4} score={row['priority_score']:>6.1f} "
            f"actions={','.join(row['recommended_actions'])} {row['province']}"
        )

    if not args.preview:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report_written: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
