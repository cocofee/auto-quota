from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_ROWS = PROJECT_ROOT / "reports" / "agent_state" / "goal_pipe_wrong_rank_9x_audit_rows.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_pipe_system_scene_9x_audit"

SCENE_TERMS = ["室内", "室外", "给水", "冷水", "排水", "雨水", "采暖", "空调", "消火栓", "喷淋", "燃气", "市政"]
WEAK_GENERIC_QUERIES = {"塑料管", "钢管", "铸铁管", "金属骨架复合管"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _terms(text: str, terms: list[str]) -> list[str]:
    found: list[str] = []
    upper = text.upper()
    for term in terms:
        if term.upper() in upper and term not in found:
            found.append(term)
    return found


def _scene_set(value: str) -> set[str]:
    return {_clean(item) for item in _clean(value).split(",") if _clean(item)}


def _has_any(value: str, terms: list[str]) -> bool:
    text = value.upper()
    return any(term.upper() in text for term in terms)


def _implied_scene(query: str) -> list[str]:
    implied: list[str] = []
    upper = query.upper()
    if "冷水" in query or "PPR" in upper or "PP-R" in upper or "PP－R" in upper:
        implied.append("给水")
    if "UPVC" in upper or "PVC" in upper:
        implied.append("排水/雨水")
    if "雨水" in query:
        implied.append("雨水")
    if "排水" in query:
        implied.append("排水")
    if "空调" in query:
        implied.append("空调")
    return implied


def _classify(row: dict[str, Any]) -> tuple[str, list[str], dict[str, Any]]:
    query = _clean(row.get("query"))
    query_scene = _scene_set(row.get("query_scene"))
    top_scene = _scene_set(row.get("top_scene"))
    positive_scene = _scene_set(row.get("positive_scene"))
    implied = _implied_scene(query)
    flags: list[str] = []

    if query_scene or _terms(query, SCENE_TERMS):
        flags.append("explicit_query_scene_signal")
    if implied:
        flags.append("material_implied_scene_signal")
    if "给水" in implied and "给水" in positive_scene and ("采暖" in top_scene or "空调" in top_scene) and "给水" not in top_scene:
        flags.append("material_implied_water_supply_vs_heating_ac")
    if "排水/雨水" in implied and ({"排水", "雨水"} & positive_scene) and "空调" in top_scene:
        flags.append("material_implied_drainage_or_rainwater_vs_ac")
    if "室内" in top_scene and "室外" in positive_scene and not (query_scene & {"室内", "室外"}):
        flags.append("indoor_outdoor_ambiguous")
    if "室外" in top_scene and "室内" in positive_scene and not (query_scene & {"室内", "室外"}):
        flags.append("indoor_outdoor_ambiguous")
    if "消毒" in _clean(row.get("positive_name")) or "冲洗" in _clean(row.get("positive_name")):
        flags.append("operation_subtype_not_pipe_install")
    if query in WEAK_GENERIC_QUERIES or (len(query) <= 4 and not implied and not query_scene):
        flags.append("weak_generic_query")
    if top_scene and positive_scene and top_scene != positive_scene:
        flags.append("candidate_scene_conflict")
    if _clean(row.get("top_numbers")) != _clean(row.get("positive_numbers")):
        flags.append("size_or_tier_conflict")

    priority = [
        "operation_subtype_not_pipe_install",
        "material_implied_water_supply_vs_heating_ac",
        "material_implied_drainage_or_rainwater_vs_ac",
        "indoor_outdoor_ambiguous",
        "weak_generic_query",
        "candidate_scene_conflict",
    ]
    primary = next((flag for flag in priority if flag in flags), "other_system_scene_mismatch")
    context = {
        "explicit_query_scene_terms": ",".join(_terms(query, SCENE_TERMS)),
        "implied_scene_terms": ",".join(implied),
        "query_scene_present": int(bool(query_scene or _terms(query, SCENE_TERMS))),
        "implied_scene_present": int(bool(implied)),
        "top_scene_tokens": ",".join(sorted(top_scene)),
        "positive_scene_tokens": ",".join(sorted(positive_scene)),
    }
    return primary, flags, context


def _audit(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    audited: list[dict[str, Any]] = []
    for row in rows:
        primary, flags, context = _classify(row)
        out = {
            "split": _clean(row.get("split")),
            "group_id": _clean(row.get("group_id")),
            "sample_id": _clean(row.get("sample_id")),
            "source_file": _clean(row.get("source_file")),
            "province": _clean(row.get("province")),
            "query": _clean(row.get("query")),
            "rank_bucket": _clean(row.get("rank_bucket")),
            "positive_rank": _clean(row.get("positive_rank")),
            "primary_issue": primary,
            "flags": "|".join(flags),
            "top1_id": _clean(row.get("top1_id")),
            "top1_name": _clean(row.get("top1_name")),
            "positive_id": _clean(row.get("positive_id")),
            "positive_name": _clean(row.get("positive_name")),
            "query_material": _clean(row.get("query_material")),
            "top_material": _clean(row.get("top_material")),
            "positive_material": _clean(row.get("positive_material")),
            "query_numbers": _clean(row.get("query_numbers")),
            "top_numbers": _clean(row.get("top_numbers")),
            "positive_numbers": _clean(row.get("positive_numbers")),
            "top_reasons": _clean(row.get("top_reasons")),
            "positive_reasons": _clean(row.get("positive_reasons")),
        }
        out.update(context)
        audited.append(out)
    return audited


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for dimension in ("primary_issue", "rank_bucket", "province", "source_file", "query_scene_present", "implied_scene_present"):
            counters[dimension][_clean(row.get(dimension)) or "<empty>"] += 1
        for flag in _clean(row.get("flags")).split("|"):
            if flag:
                counters["flag"][flag] += 1
    total = len(rows)
    out: list[dict[str, Any]] = []
    for dimension, counter in sorted(counters.items()):
        for key, count in counter.most_common():
            out.append({"scope": "dev_pipe_system_scene", "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _md_table(rows: list[list[Any]]) -> str:
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
    lines = [
        "# Stage 9.3 Pipe System / Scene Audit",
        "",
        "Read-only audit of dev `pipe + system_scene_mismatch` rows. No training, tuning, rule patch, ranking change, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["target_rows", report["target_rows"]],
                ["explicit_query_scene_rows", report["metrics"]["explicit_query_scene_rows"]],
                ["material_implied_scene_rows", report["metrics"]["material_implied_scene_rows"]],
                ["weak_or_ambiguous_rows", report["metrics"]["weak_or_ambiguous_rows"]],
                ["selected_next_issue", report["next_candidate"]["primary_issue"]],
                ["selected_support", report["next_candidate"]["support"]],
                ["next_stage", report["next_candidate"]["next_stage"]],
            ]
        ),
        "",
        "## Primary Issue Buckets",
        "",
        _md_table([["issue", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in report["primary_issue_buckets"]]),
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.3 dev-only pipe system/scene audit")
    parser.add_argument("--rows", default=str(DEFAULT_ROWS))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    target = [
        row
        for row in _read_csv(Path(args.rows))
        if _clean(row.get("split")) == "dev"
        and _clean(row.get("primary_issue")) == "system_scene_mismatch"
    ]
    audited = _audit(target)
    buckets = _bucket_rows(audited)
    primary_buckets = [row for row in buckets if row["dimension"] == "primary_issue"]
    selected = next(
        (row for row in primary_buckets if row["key"] in {"material_implied_water_supply_vs_heating_ac", "material_implied_drainage_or_rainwater_vs_ac"}),
        primary_buckets[0] if primary_buckets else {"key": "", "count": 0},
    )
    explicit_rows = sum(_to_int(row.get("query_scene_present")) for row in audited)
    implied_rows = sum(_to_int(row.get("implied_scene_present")) for row in audited)
    weak_rows = sum(1 for row in audited if "weak_generic_query" in _clean(row.get("flags")) or "indoor_outdoor_ambiguous" in _clean(row.get("flags")))

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
    }
    report = {
        "stage": "Goal LTR v1 / stage 9.3 dev-only pipe system/scene audit",
        "read_only": True,
        "eval_only": True,
        "dev_only_selection": True,
        "heldout_not_used_for_selection": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_rule_patch": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "no_goal_searcher_change": True,
        "target_rows": len(audited),
        "metrics": {
            "explicit_query_scene_rows": explicit_rows,
            "material_implied_scene_rows": implied_rows,
            "weak_or_ambiguous_rows": weak_rows,
        },
        "primary_issue_buckets": primary_buckets,
        "next_candidate": {
            "selected_from": "dev_only_pipe_system_scene",
            "primary_issue": selected["key"],
            "support": selected["count"],
            "selection_policy": "prefer material-implied transferable scene buckets over weak generic indoor/outdoor ambiguity; heldout not used",
            "next_stage": "9.4 pipe material-implied scene design review; do not implement rules yet",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.3 only audits one dev bucket. Structured query_scene is absent in these rows, but material/lexical implied scene is visible; indoor/outdoor-only cases remain weak and should not become direct rules.",
    }

    row_fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "province",
        "query",
        "rank_bucket",
        "positive_rank",
        "primary_issue",
        "flags",
        "explicit_query_scene_terms",
        "implied_scene_terms",
        "query_scene_present",
        "implied_scene_present",
        "top_scene_tokens",
        "positive_scene_tokens",
        "top1_id",
        "top1_name",
        "positive_id",
        "positive_name",
        "query_material",
        "top_material",
        "positive_material",
        "query_numbers",
        "top_numbers",
        "positive_numbers",
        "top_reasons",
        "positive_reasons",
    ]
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _write_csv(Path(artifacts["rows_csv"]), audited, row_fields)
    _write_csv(Path(artifacts["buckets_csv"]), buckets, ["scope", "dimension", "key", "count", "rate"])

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "target_rows": report["target_rows"],
                    "metrics": report["metrics"],
                    "primary_issue_buckets": primary_buckets,
                    "next_candidate": report["next_candidate"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": artifacts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
