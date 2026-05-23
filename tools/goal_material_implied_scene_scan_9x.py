from __future__ import annotations

import argparse
import csv
import json
import re
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

DEFAULT_WRONG_RANK = PROJECT_ROOT / "reports" / "agent_state" / "goal_accuracy_gap_9x_decomposition_wrong_rank.csv"
DEFAULT_OUTPUT_PREFIX = PROJECT_ROOT / "reports" / "agent_state" / "goal_material_implied_scene_9x_scan"

PIPE_CONTEXT_TERMS = ["管", "管道", "塑料管", "给水管", "排水管", "雨水管", "冷水管", "UPVC管", "PVC管", "PPR管", "PP-R管"]
ELECTRICAL_PIPE_BLOCK_TERMS = ["线管", "电线管", "配管", "穿线", "电缆保护管", "导管", "桥架", "配线"]
OPERATION_BLOCK_TERMS = ["消毒", "冲洗", "试压", "压力试验", "水压试验", "保温", "防腐", "刷油", "支架", "支吊架"]
WATER_SUPPLY_TERMS = ["给水", "冷水", "供水", "生活给水"]
DRAINAGE_TERMS = ["排水", "雨水", "污水"]
HEATING_AC_TERMS = ["采暖", "空调", "冷热水", "凝结水", "冷凝水"]
OTHER_SYSTEM_TERMS = ["喷淋", "消防", "消火栓", "燃气", "煤气"]


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


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _has_any(text: str, terms: list[str]) -> bool:
    upper = text.upper()
    return any(term.upper() in upper for term in terms)


def _found_terms(text: str, terms: list[str]) -> list[str]:
    found: list[str] = []
    upper = text.upper()
    for term in terms:
        if term.upper() in upper and term not in found:
            found.append(term)
    return found


def _numbers(text: str) -> list[int]:
    values: list[int] = []
    patterns = [
        r"(?:DN|DE|De|SC|Φ|φ)\s*([0-9]{1,4})",
        r"(?:公称直径|公称外径|管径|外径|直径)\D{0,8}([0-9]{1,4})",
        r"([0-9]{2,4})\s*mm",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            try:
                value = int(match.group(1))
            except ValueError:
                continue
            if value not in values:
                values.append(value)
    return values


def _split_positive_names(value: str) -> list[str]:
    cleaned = _clean(value)
    if not cleaned:
        return []
    return [item.strip() for item in cleaned.split(" || ") if item.strip()]


def _scene_terms(text: str) -> list[str]:
    terms: list[str] = []
    for group in (WATER_SUPPLY_TERMS, DRAINAGE_TERMS, HEATING_AC_TERMS, OTHER_SYSTEM_TERMS):
        terms.extend(_found_terms(text, group))
    return terms


def _detect_query_hints(query: str, query_family: str) -> list[dict[str, str]]:
    hints: list[dict[str, str]] = []
    upper = query.upper()
    pipe_context = query_family == "pipe" or _has_any(query, PIPE_CONTEXT_TERMS)
    electrical_pipe = _has_any(query, ELECTRICAL_PIPE_BLOCK_TERMS)

    water_sources: list[str] = []
    for term in ("PPR", "PP-R", "PP－R", "冷水管"):
        if term.upper() in upper:
            water_sources.append(term)
    if water_sources and pipe_context and not electrical_pipe:
        hints.append({"hint": "water_supply", "source": ",".join(water_sources), "source_type": "material_or_cold_water"})

    drainage_sources: list[str] = []
    for term in ("UPVC", "PVC"):
        if term.upper() in upper:
            drainage_sources.append(term)
    if drainage_sources and pipe_context and not electrical_pipe:
        hints.append({"hint": "drainage_rainwater", "source": ",".join(drainage_sources), "source_type": "material"})

    explicit_drainage = _found_terms(query, DRAINAGE_TERMS)
    if explicit_drainage and pipe_context and not electrical_pipe:
        hints.append({"hint": "drainage_rainwater", "source": ",".join(explicit_drainage), "source_type": "explicit_scene"})

    explicit_water = [term for term in _found_terms(query, WATER_SUPPLY_TERMS) if term != "冷水"]
    if explicit_water and pipe_context and not electrical_pipe:
        hints.append({"hint": "water_supply", "source": ",".join(explicit_water), "source_type": "explicit_scene"})

    deduped: dict[str, dict[str, str]] = {}
    for hint in hints:
        key = hint["hint"]
        if key not in deduped:
            deduped[key] = hint
        else:
            deduped[key]["source"] = ",".join(filter(None, [deduped[key]["source"], hint["source"]]))
            if hint["source_type"] == "explicit_scene":
                deduped[key]["source_type"] = "explicit_scene"
    return list(deduped.values())


def _has_scene_for_hint(text: str, hint: str) -> bool:
    if hint == "water_supply":
        return _has_any(text, WATER_SUPPLY_TERMS)
    if hint == "drainage_rainwater":
        return _has_any(text, DRAINAGE_TERMS)
    return False


def _has_competing_scene(text: str, hint: str) -> bool:
    if hint == "water_supply":
        return _has_any(text, HEATING_AC_TERMS + OTHER_SYSTEM_TERMS)
    if hint == "drainage_rainwater":
        return _has_any(text, HEATING_AC_TERMS + OTHER_SYSTEM_TERMS)
    return False


def _query_conflicts(query: str, hint: str) -> list[str]:
    conflicts: list[str] = []
    if hint == "water_supply":
        conflicts.extend(_found_terms(query, HEATING_AC_TERMS + OTHER_SYSTEM_TERMS + DRAINAGE_TERMS))
    elif hint == "drainage_rainwater":
        conflicts.extend(_found_terms(query, HEATING_AC_TERMS + OTHER_SYSTEM_TERMS + WATER_SUPPLY_TERMS))
    return conflicts


def _size_guard(query: str, top_text: str, positive_texts: list[str]) -> tuple[str, str]:
    query_numbers = _numbers(query)
    top_numbers = _numbers(top_text)
    positive_numbers = []
    for text in positive_texts:
        positive_numbers.extend(_numbers(text))
    positive_numbers = sorted(set(positive_numbers))

    if not query_numbers:
        return "unknown_no_query_number", ""
    if not positive_numbers:
        return "unknown_no_positive_number", ",".join(str(item) for item in query_numbers)

    exact = bool(set(query_numbers) & set(positive_numbers))
    if exact:
        return "exact", ",".join(str(item) for item in query_numbers)

    # 定额档位经常是 DN15 -> 20、DN20 -> 25 这种上档；这里只做审计标签，不做放行。
    tier_up = any(pos > query for query in query_numbers for pos in positive_numbers if (pos - query) <= max(10, query * 0.35))
    if tier_up:
        return "tier_up_possible", ",".join(str(item) for item in query_numbers)

    top_exact = bool(set(query_numbers) & set(top_numbers))
    return ("top_exact_positive_not_supported" if top_exact else "conflict_or_unknown", ",".join(str(item) for item in query_numbers))


def _classify_row(row: dict[str, Any], hint: dict[str, str]) -> dict[str, Any]:
    query = _clean(row.get("query"))
    query_family = _clean(row.get("query_family"))
    hint_name = hint["hint"]
    top_text = f"{_clean(row.get('top1_name'))} {_clean(row.get('top1_chapter'))}"
    positive_texts = _split_positive_names(_clean(row.get("positive_names_in_top80")))
    positive_text = " || ".join(positive_texts)
    top_has_hint = _has_scene_for_hint(top_text, hint_name)
    positive_has_hint = _has_scene_for_hint(positive_text, hint_name)
    top_competing = _has_competing_scene(top_text, hint_name)
    query_conflict_terms = _query_conflicts(query, hint_name)
    operation_blocks = _found_terms(" ".join([query, top_text, positive_text]), OPERATION_BLOCK_TERMS)
    family_scope = "pipe" if query_family == "pipe" else "non_pipe_or_empty"
    size_status, query_numbers = _size_guard(query, top_text, positive_texts)

    blocks: list[str] = []
    weak: list[str] = []
    flags: list[str] = []

    if family_scope != "pipe":
        blocks.append("query_family_not_pipe")
    if query_conflict_terms:
        blocks.append("query_explicit_conflicting_system")
    if operation_blocks:
        blocks.append("operation_subtype_block")
    if not positive_has_hint:
        weak.append("positive_lacks_implied_scene")
    if top_has_hint:
        weak.append("top_already_has_implied_scene")
    if not top_competing:
        weak.append("top_lacks_competing_scene")
    if size_status not in {"exact", "tier_up_possible"}:
        weak.append(f"size_guard_{size_status}")

    if positive_has_hint and not top_has_hint:
        flags.append("candidate_has_scene_advantage")
    if top_competing:
        flags.append("top_has_competing_scene")
    if size_status in {"exact", "tier_up_possible"}:
        flags.append("size_guard_visible")

    if blocks:
        classification = "blocked"
    elif positive_has_hint and not top_has_hint and top_competing and size_status in {"exact", "tier_up_possible"}:
        classification = "strong_transfer_candidate"
    elif positive_has_hint and not top_has_hint:
        classification = "weak_transfer_candidate"
    else:
        classification = "hint_present_but_not_supported"

    return {
        "split": _clean(row.get("split")),
        "group_id": _clean(row.get("group_id")),
        "sample_id": _clean(row.get("sample_id")),
        "source_file": _clean(row.get("source_file")),
        "project_name": _clean(row.get("project_name")),
        "province": _clean(row.get("province")),
        "query": query,
        "query_family": query_family,
        "reason": _clean(row.get("reason")),
        "rank_bucket": _clean(row.get("rank_bucket")),
        "positive_rank_min": _clean(row.get("positive_rank_min")),
        "hint": hint_name,
        "hint_source": hint["source"],
        "hint_source_type": hint["source_type"],
        "classification": classification,
        "family_scope": family_scope,
        "top_has_implied_scene": int(top_has_hint),
        "positive_has_implied_scene": int(positive_has_hint),
        "top_has_competing_scene": int(top_competing),
        "size_guard": size_status,
        "query_numbers": query_numbers,
        "query_conflict_terms": ",".join(query_conflict_terms),
        "operation_block_terms": ",".join(operation_blocks),
        "weak_reasons": "|".join(weak),
        "block_reasons": "|".join(blocks),
        "flags": "|".join(flags),
        "top_scene_terms": ",".join(_scene_terms(top_text)),
        "positive_scene_terms": ",".join(_scene_terms(positive_text)),
        "top1_id": _clean(row.get("top1_id")),
        "top1_name": _clean(row.get("top1_name")),
        "positive_ids_in_top80": _clean(row.get("positive_ids_in_top80")),
        "positive_names_in_top80": _clean(row.get("positive_names_in_top80")),
    }


def _scan(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        query = _clean(row.get("query"))
        query_family = _clean(row.get("query_family"))
        for hint in _detect_query_hints(query, query_family):
            out.append(_classify_row(row, hint))
    return out


def _bucket_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counters: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        for dimension in ("classification", "hint", "hint_source_type", "family_scope", "province", "source_file", "query_family", "reason", "rank_bucket", "size_guard"):
            counters[("material_scene_scan", dimension)][_clean(row.get(dimension)) or "<empty>"] += 1
        for reason in _clean(row.get("weak_reasons")).split("|"):
            if reason:
                counters[("material_scene_scan", "weak_reason")][reason] += 1
        for reason in _clean(row.get("block_reasons")).split("|"):
            if reason:
                counters[("material_scene_scan", "block_reason")][reason] += 1
    total = len(rows)
    out: list[dict[str, Any]] = []
    for (scope, dimension), counter in sorted(counters.items()):
        for key, count in counter.most_common():
            out.append({"scope": scope, "dimension": dimension, "key": key, "count": count, "rate": _rate(count, total)})
    return out


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


def _top_bucket_rows(buckets: list[dict[str, Any]], dimension: str, limit: int = 8) -> list[dict[str, Any]]:
    rows = [row for row in buckets if row["dimension"] == dimension]
    return rows[:limit]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    buckets = report["artifacts_preview"]["top_buckets"]
    lines = [
        "# Stage 9.5 Broader Material-implied Scene Scan",
        "",
        "Eval-only dev scan of material-to-scene hints inside wrong-rank rows. No training, tuning, rule patch, ranking change, heldout selection, or GoalSearcher change.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["scanned_dev_wrong_rank_rows", report["metrics"]["scanned_dev_wrong_rank_rows"]],
                ["hint_rows", report["metrics"]["hint_rows"]],
                ["unique_groups_with_hints", report["metrics"]["unique_groups_with_hints"]],
                ["strong_transfer_candidates", report["metrics"]["strong_transfer_candidates"]],
                ["weak_transfer_candidates", report["metrics"]["weak_transfer_candidates"]],
                ["blocked_rows", report["metrics"]["blocked_rows"]],
                ["strong_candidate_provinces", report["metrics"]["strong_candidate_provinces"]],
                ["strong_candidate_sources", report["metrics"]["strong_candidate_sources"]],
                ["decision", report["decision"]["recommendation"]],
            ]
        ),
        "",
        "## Classification",
        "",
        _md_table([["classification", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in buckets["classification"]]),
        "",
        "## Hints",
        "",
        _md_table([["hint", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in buckets["hint"]]),
        "",
        "## Top Provinces",
        "",
        _md_table([["province", "count", "rate"]] + [[row["key"], row["count"], row["rate"]] for row in buckets["province"]]),
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
        "",
        "## Next Stage",
        "",
        report["next_stage"]["stage"] + ": " + report["next_stage"]["goal"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 9.5 broader dev scan for material-implied scene hints")
    parser.add_argument("--wrong-rank", default=str(DEFAULT_WRONG_RANK))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    wrong_rank = [
        row
        for row in _read_csv(Path(args.wrong_rank))
        if _clean(row.get("split")) == "dev" and _clean(row.get("status")) == "top80_present_but_wrong_rank"
    ]
    scanned = _scan(wrong_rank)
    buckets = _bucket_rows(scanned)
    strong = [row for row in scanned if row["classification"] == "strong_transfer_candidate"]
    weak = [row for row in scanned if row["classification"] == "weak_transfer_candidate"]
    blocked = [row for row in scanned if row["classification"] == "blocked"]
    strong_provinces = {_clean(row.get("province")) for row in strong if _clean(row.get("province"))}
    strong_sources = {_clean(row.get("source_file")) for row in strong if _clean(row.get("source_file"))}
    strong_hints = Counter(_clean(row.get("hint")) for row in strong)

    enough_for_what_if_design = len(strong) >= 20 and len(strong_provinces) >= 3 and len(strong_sources) >= 2 and len(strong_hints) >= 2
    if enough_for_what_if_design:
        recommendation = "allow_eval_only_what_if_design"
        next_stage = {
            "stage": "9.6 material-implied scene what-if design",
            "goal": "design a strict eval-only what-if using the scanned strong evidence and block guards; still do not train or change GoalSearcher",
        }
    else:
        recommendation = "do_not_implement_hint_yet"
        next_stage = {
            "stage": "9.6 return to ranked gap table selection",
            "goal": "choose the next high-support dev wrong-rank bucket instead of forcing a low-support material scene hint",
        }

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "rows_csv": str(output_prefix.with_name(output_prefix.name + "_rows.csv")),
        "buckets_csv": str(output_prefix.with_name(output_prefix.name + "_buckets.csv")),
        "examples_jsonl": str(output_prefix.with_name(output_prefix.name + "_examples.jsonl")),
    }
    fields = [
        "split",
        "group_id",
        "sample_id",
        "source_file",
        "project_name",
        "province",
        "query",
        "query_family",
        "reason",
        "rank_bucket",
        "positive_rank_min",
        "hint",
        "hint_source",
        "hint_source_type",
        "classification",
        "family_scope",
        "top_has_implied_scene",
        "positive_has_implied_scene",
        "top_has_competing_scene",
        "size_guard",
        "query_numbers",
        "query_conflict_terms",
        "operation_block_terms",
        "weak_reasons",
        "block_reasons",
        "flags",
        "top_scene_terms",
        "positive_scene_terms",
        "top1_id",
        "top1_name",
        "positive_ids_in_top80",
        "positive_names_in_top80",
    ]
    bucket_fields = ["scope", "dimension", "key", "count", "rate"]
    report = {
        "stage": "Goal LTR v1 / stage 9.5 broader material-implied scene scan",
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
        "source_artifact": str(Path(args.wrong_rank)),
        "metrics": {
            "scanned_dev_wrong_rank_rows": len(wrong_rank),
            "hint_rows": len(scanned),
            "unique_groups_with_hints": len({_clean(row.get("group_id")) for row in scanned}),
            "strong_transfer_candidates": len(strong),
            "weak_transfer_candidates": len(weak),
            "blocked_rows": len(blocked),
            "strong_candidate_provinces": len(strong_provinces),
            "strong_candidate_sources": len(strong_sources),
            "strong_candidate_hints": dict(strong_hints),
        },
        "decision": {
            "enough_for_what_if_design": enough_for_what_if_design,
            "recommendation": recommendation,
            "gate": "strong>=20, provinces>=3, sources>=2, hints>=2",
        },
        "artifacts": artifacts,
        "artifacts_preview": {
            "top_buckets": {
                "classification": _top_bucket_rows(buckets, "classification"),
                "hint": _top_bucket_rows(buckets, "hint"),
                "province": _top_bucket_rows(buckets, "province"),
                "source_file": _top_bucket_rows(buckets, "source_file"),
                "weak_reason": _top_bucket_rows(buckets, "weak_reason"),
                "block_reason": _top_bucket_rows(buckets, "block_reason"),
            },
            "strong_examples": strong[:10],
            "weak_examples": weak[:10],
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "anti_drift_conclusion": "Stage 9.5 scans only dev wrong-rank rows and does not implement a PPR/UPVC rule. The gate requires support across rows, provinces, sources, and both hint types before even designing a what-if.",
        "next_stage": next_stage,
    }

    _write_csv(Path(artifacts["rows_csv"]), scanned, fields)
    _write_csv(Path(artifacts["buckets_csv"]), buckets, bucket_fields)
    _write_jsonl(Path(artifacts["examples_jsonl"]), strong[:25] + weak[:25])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)

    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"], "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
