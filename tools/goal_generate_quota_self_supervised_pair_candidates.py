from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.goal_search.national_index import extract_signal, is_pipe_device_false_trigger  # noqa: E402

DEFAULT_PROVINCE_ROOT = PROJECT_ROOT / "db" / "provinces"
DEFAULT_PLAN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_sampling_plan_province_family.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates_summary.md"
DEFAULT_PAIRS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates.csv"
DEFAULT_PAIRS_JSONL = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates.jsonl"
DEFAULT_REJECTED_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates_rejected.csv"
DEFAULT_BUCKETS_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pair_candidates_buckets.csv"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _column_names(conn: sqlite3.Connection) -> set[str]:
    return {row[1] for row in conn.execute("pragma table_info(quotas)").fetchall()}


def _select_clause(columns: set[str]) -> str:
    wanted = [
        "quota_id",
        "name",
        "unit",
        "book",
        "chapter",
        "specialty",
        "work_type",
        "material",
        "connection",
        "dn",
        "cable_section",
        "circuits",
    ]
    return ", ".join(column if column in columns else f"NULL AS {column}" for column in wanted)


def _iter_quota_rows(db_path: Path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        table_names = {row[0] for row in conn.execute("select name from sqlite_master where type='table'")}
        if "quotas" not in table_names:
            return
        columns = _column_names(conn)
        sql = f"select {_select_clause(columns)} from quotas"
        for row in conn.execute(sql):
            yield dict(row)
    finally:
        conn.close()


def _book(row: dict[str, Any]) -> str:
    return _clean(row.get("book")) or _clean(row.get("specialty")) or _clean(row.get("work_type"))


def _param_value(signal: Any, row: dict[str, Any]) -> str:
    param_type = _clean(signal.param_type)
    if not param_type:
        return ""
    value = getattr(signal, param_type, None)
    if value is None and param_type in row:
        value = row.get(param_type)
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return _clean(value)


def _subtype_key(signal: Any) -> str:
    parts = [
        _clean(signal.action),
        _clean(signal.material),
        _clean(signal.connection),
        _clean(signal.install_method),
    ]
    return "|".join(part for part in parts if part)


def _record_from_quota(row: dict[str, Any], province: str, db_path: Path) -> dict[str, Any] | None:
    quota_id = _clean(row.get("quota_id"))
    name = _clean(row.get("name"))
    if not quota_id or not name or is_pipe_device_false_trigger(name):
        return None
    text = " ".join(
        part
        for part in (
            name,
            _clean(row.get("chapter")),
            _clean(row.get("specialty")),
            _clean(row.get("work_type")),
        )
        if part
    )
    signal = extract_signal(text)
    family = _clean(signal.family)
    if not family:
        return None
    return {
        "province": province,
        "quota_id": quota_id,
        "name": name,
        "unit": _clean(row.get("unit")),
        "book": _book(row),
        "chapter": _clean(row.get("chapter")),
        "specialty": _clean(row.get("specialty")),
        "work_type": _clean(row.get("work_type")),
        "family": family,
        "action": _clean(signal.action),
        "material": _clean(signal.material),
        "connection": _clean(signal.connection),
        "install_method": _clean(signal.install_method),
        "param_type": _clean(signal.param_type),
        "param_value": _param_value(signal, row),
        "subtype_key": _subtype_key(signal),
        "source_db_path": str(db_path),
    }


def _load_plan(path: Path, limit_province_families: int) -> dict[str, list[dict[str, Any]]]:
    rows = [row for row in _read_csv(path) if _int(row.get("planned_total_pairs")) > 0]
    if limit_province_families > 0:
        rows = rows[:limit_province_families]
    by_province: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_province[_clean(row.get("province"))].append(row)
    return by_province


def _possible_contrast_pairs(value_to_records: dict[str, list[dict[str, Any]]]) -> int:
    counts = [len(records) for records in value_to_records.values() if records]
    total = sum(counts)
    if total < 2 or len(counts) < 2:
        return 0
    same_value_pairs = sum(count * (count - 1) // 2 for count in counts)
    return total * (total - 1) // 2 - same_value_pairs


def _allocate_targets(target: int, buckets: list[dict[str, Any]], max_bucket_pairs: int) -> dict[int, int]:
    if target <= 0 or not buckets:
        return {}
    weights = {idx: math.sqrt(max(0, bucket["available_pairs"])) for idx, bucket in enumerate(buckets)}
    caps = {idx: min(max_bucket_pairs, bucket["available_pairs"]) for idx, bucket in enumerate(buckets)}
    total_weight = sum(weights.values())
    if total_weight <= 0:
        return {}
    allocations: dict[int, int] = {}
    remainders: list[tuple[float, int]] = []
    for idx, weight in weights.items():
        raw = target * weight / total_weight
        base = min(caps[idx], int(math.floor(raw)))
        allocations[idx] = base
        remainders.append((raw - math.floor(raw), idx))
    current = sum(allocations.values())
    for _remainder, idx in sorted(remainders, reverse=True):
        if current >= target:
            break
        room = caps[idx] - allocations[idx]
        if room <= 0:
            continue
        add = min(room, target - current)
        allocations[idx] += add
        current += add
    return {idx: value for idx, value in allocations.items() if value > 0}


def _pair_quality_flags(pair_type: str, positive: dict[str, Any], negative: dict[str, Any], contrast_field: str) -> str:
    flags = [
        "same_province",
        "same_family",
        "same_book",
        "same_unit",
        pair_type,
    ]
    if contrast_field:
        flags.append(f"contrast:{contrast_field}")
    if positive["quota_id"] == negative["quota_id"]:
        flags.append("invalid_same_quota")
    return "|".join(flags)


def _make_pair_row(
    *,
    pair_type: str,
    province: str,
    family: str,
    positive: dict[str, Any],
    negative: dict[str, Any],
    contrast_field: str,
    positive_value: str,
    negative_value: str,
) -> dict[str, Any]:
    pair_id = f"{province}:{family}:{pair_type}:{positive['quota_id']}>{negative['quota_id']}:{contrast_field}:{positive_value}>{negative_value}"
    return {
        "pair_id": pair_id,
        "province": province,
        "family": family,
        "pair_type": pair_type,
        "contrast_field": contrast_field,
        "positive_contrast_value": positive_value,
        "negative_contrast_value": negative_value,
        "positive_id": positive["quota_id"],
        "positive_name": positive["name"],
        "positive_unit": positive["unit"],
        "positive_book": positive["book"],
        "positive_chapter": positive["chapter"],
        "negative_id": negative["quota_id"],
        "negative_name": negative["name"],
        "negative_unit": negative["unit"],
        "negative_book": negative["book"],
        "negative_chapter": negative["chapter"],
        "positive_subtype_key": positive["subtype_key"],
        "negative_subtype_key": negative["subtype_key"],
        "quality_flags": _pair_quality_flags(pair_type, positive, negative, contrast_field),
        "source_db_path": positive["source_db_path"],
    }


def _sample_from_bucket(
    *,
    rng: random.Random,
    pair_type: str,
    province: str,
    family: str,
    bucket: dict[str, Any],
    target: int,
    seen_pairs: set[tuple[str, str, str, str]],
    max_attempt_factor: int,
) -> tuple[list[dict[str, Any]], int]:
    value_to_records: dict[str, list[dict[str, Any]]] = bucket["value_to_records"]
    values = [value for value, records in value_to_records.items() if records]
    if len(values) < 2 or target <= 0:
        return [], target
    rows: list[dict[str, Any]] = []
    attempts = 0
    max_attempts = max(500, target * max_attempt_factor)
    while len(rows) < target and attempts < max_attempts:
        attempts += 1
        left, right = rng.sample(values, 2)
        positive = rng.choice(value_to_records[left])
        negative = rng.choice(value_to_records[right])
        if positive["quota_id"] == negative["quota_id"]:
            continue
        unordered_ids = "|".join(sorted((positive["quota_id"], negative["quota_id"])))
        pair_key = (pair_type, province, family, unordered_ids)
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        rows.append(
            _make_pair_row(
                pair_type=pair_type,
                province=province,
                family=family,
                positive=positive,
                negative=negative,
                contrast_field=bucket["contrast_field"],
                positive_value=left,
                negative_value=right,
            )
        )
    return rows, max(0, target - len(rows))


def _build_buckets(records: list[dict[str, Any]], family: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    param_groups: dict[tuple[str, str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    subtype_groups: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for record in records:
        if record["family"] != family:
            continue
        if record["param_type"] and record["param_value"]:
            param_groups[(record["book"], record["unit"], record["param_type"])][record["param_value"]].append(record)
        if record["subtype_key"]:
            subtype_groups[(record["book"], record["unit"])][record["subtype_key"]].append(record)

    param_buckets: list[dict[str, Any]] = []
    for (book, unit, param_type), value_to_records in param_groups.items():
        available = _possible_contrast_pairs(value_to_records)
        if available:
            param_buckets.append(
                {
                    "book": book,
                    "unit": unit,
                    "contrast_field": param_type,
                    "value_to_records": value_to_records,
                    "available_pairs": available,
                }
            )

    subtype_buckets: list[dict[str, Any]] = []
    for (book, unit), value_to_records in subtype_groups.items():
        available = _possible_contrast_pairs(value_to_records)
        if available:
            subtype_buckets.append(
                {
                    "book": book,
                    "unit": unit,
                    "contrast_field": "subtype_key",
                    "value_to_records": value_to_records,
                    "available_pairs": available,
                }
            )
    return param_buckets, subtype_buckets


def _load_province_records(db_path: Path, province: str, needed_families: set[str]) -> tuple[list[dict[str, Any]], Counter[str]]:
    records: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    rows = _iter_quota_rows(db_path)
    if rows is None:
        skipped["missing_quotas_table"] += 1
        return records, skipped
    for row in rows:
        record = _record_from_quota(row, province, db_path)
        if record is None:
            skipped["unusable_quota_record"] += 1
            continue
        if record["family"] not in needed_families:
            continue
        records.append(record)
    return records, skipped


def _reject_row(province: str, family: str, reason: str, planned: int, generated: int = 0, detail: str = "") -> dict[str, Any]:
    return {
        "province": province,
        "family": family,
        "reject_reason": reason,
        "planned_pairs": planned,
        "generated_pairs": generated,
        "shortfall": max(0, planned - generated),
        "detail": detail,
    }


def _generate(args: argparse.Namespace) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    rng = random.Random(args.seed)
    plan_by_province = _load_plan(Path(args.plan_csv), args.limit_province_families)
    pairs: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()
    seen_pairs: set[tuple[str, str, str, str]] = set()
    province_root = Path(args.province_root)

    for province_index, (province, plan_rows) in enumerate(plan_by_province.items(), 1):
        db_path = province_root / province / "quota.db"
        if not db_path.exists():
            planned = sum(_int(row.get("planned_total_pairs")) for row in plan_rows)
            rejected.append(_reject_row(province, "", "quota_db_missing", planned, detail=str(db_path)))
            continue
        needed_families = {_clean(row.get("family")) for row in plan_rows}
        records, province_skipped = _load_province_records(db_path, province, needed_families)
        skipped.update(province_skipped)
        records_by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            records_by_family[record["family"]].append(record)

        for plan_row in plan_rows:
            family = _clean(plan_row.get("family"))
            family_records = records_by_family.get(family, [])
            planned_param = _int(plan_row.get("planned_param_pairs"))
            planned_subtype = _int(plan_row.get("planned_subtype_pairs"))
            if not family_records:
                rejected.append(_reject_row(province, family, "no_family_records_after_rescan", planned_param + planned_subtype))
                continue
            param_buckets, subtype_buckets = _build_buckets(family_records, family)

            for pair_type, target, buckets in (
                ("param_contrast", planned_param, param_buckets),
                ("subtype_contrast", planned_subtype, subtype_buckets),
            ):
                if target <= 0:
                    continue
                if not buckets:
                    rejected.append(_reject_row(province, family, f"no_{pair_type}_buckets", target))
                    continue
                allocations = _allocate_targets(target, buckets, args.max_bucket_pairs)
                generated = 0
                shortfall = 0
                for bucket_index, bucket_target in allocations.items():
                    bucket_rows, bucket_shortfall = _sample_from_bucket(
                        rng=rng,
                        pair_type=pair_type,
                        province=province,
                        family=family,
                        bucket=buckets[bucket_index],
                        target=bucket_target,
                        seen_pairs=seen_pairs,
                        max_attempt_factor=args.max_attempt_factor,
                    )
                    generated += len(bucket_rows)
                    shortfall += bucket_shortfall
                    pairs.extend(bucket_rows)
                if generated < target:
                    rejected.append(
                        _reject_row(
                            province,
                            family,
                            f"{pair_type}_sampling_shortfall",
                            target,
                            generated,
                            detail=f"buckets={len(buckets)} allocation_shortfall={shortfall}",
                        )
                    )

        if args.progress_every > 0 and province_index % args.progress_every == 0:
            print(f"processed {province_index}/{len(plan_by_province)} provinces; pairs={len(pairs)}", file=sys.stderr)

    return pairs, rejected, skipped


def _counter_items(counter: Counter[str], total: int, limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count, "rate": _rate(count, total)} for key, count in counter.most_common(limit)]


def _summarize(pairs: list[dict[str, Any]], rejected: list[dict[str, Any]], skipped: Counter[str], top_limit: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_family = Counter(row["family"] for row in pairs)
    by_province = Counter(row["province"] for row in pairs)
    by_pair_type = Counter(row["pair_type"] for row in pairs)
    by_reject_reason = Counter(row["reject_reason"] for row in rejected)
    largest_family = by_family.most_common(1)[0] if by_family else ("", 0)
    largest_province = by_province.most_common(1)[0] if by_province else ("", 0)
    total = len(pairs)
    buckets: list[dict[str, Any]] = []
    for dimension, counter in (
        ("family", by_family),
        ("province", by_province),
        ("pair_type", by_pair_type),
        ("reject_reason", by_reject_reason),
        ("skipped", skipped),
    ):
        denominator = total if dimension not in {"reject_reason", "skipped"} else sum(counter.values())
        for key, count in counter.most_common():
            buckets.append({"dimension": dimension, "key": key, "count": count, "rate": _rate(count, denominator)})
    summary = {
        "generated_pairs": total,
        "rejected_buckets": len(rejected),
        "rejected_shortfall_pairs": sum(_int(row.get("shortfall")) for row in rejected),
        "distinct_families": len(by_family),
        "distinct_provinces": len(by_province),
        "param_contrast_pairs": by_pair_type.get("param_contrast", 0),
        "subtype_contrast_pairs": by_pair_type.get("subtype_contrast", 0),
        "param_contrast_rate": _rate(by_pair_type.get("param_contrast", 0), total),
        "subtype_contrast_rate": _rate(by_pair_type.get("subtype_contrast", 0), total),
        "largest_family": largest_family[0],
        "largest_family_pairs": largest_family[1],
        "largest_family_rate": _rate(largest_family[1], total),
        "largest_province": largest_province[0],
        "largest_province_pairs": largest_province[1],
        "largest_province_rate": _rate(largest_province[1], total),
        "passes_generation_balance_gate": (
            total >= 50_000
            and len(by_family) >= 15
            and len(by_province) >= 50
            and _rate(largest_family[1], total) <= 0.18
            and _rate(largest_province[1], total) <= 0.05
            and _rate(by_pair_type.get("param_contrast", 0), total) >= 0.2
        ),
        "by_family": _counter_items(by_family, total, top_limit),
        "by_province": _counter_items(by_province, total, top_limit),
        "by_pair_type": _counter_items(by_pair_type, total, top_limit),
        "by_reject_reason": _counter_items(by_reject_reason, sum(by_reject_reason.values()), top_limit),
        "by_skipped": _counter_items(skipped, sum(skipped.values()), top_limit),
    }
    return summary, buckets


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


def _counter_table(items: list[dict[str, Any]]) -> list[list[object]]:
    return [["key", "count", "rate"], *[[item["key"], item["count"], item["rate"]] for item in items]]


def _sample_rows(rows: list[dict[str, Any]], limit: int) -> list[list[object]]:
    return [
        [
            row["pair_id"],
            row["family"],
            row["pair_type"],
            row["positive_id"],
            row["negative_id"],
            row["contrast_field"],
            row["positive_contrast_value"],
            row["negative_contrast_value"],
        ]
        for row in rows[:limit]
    ]


def _write_markdown(path: Path, report: dict[str, Any], pairs: list[dict[str, Any]]) -> None:
    summary = report["summary"]
    lines = [
        "# Goal Quota Self-Supervised Pair Candidates",
        "",
        "Stage 5.1 eval-only candidate generation. It creates balanced same-province same-family pair candidates from quota.db files. It does not train, tune, or change search ranking.",
        "",
        "## Summary",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["generated_pairs", summary["generated_pairs"]],
                ["rejected_buckets", summary["rejected_buckets"]],
                ["rejected_shortfall_pairs", summary["rejected_shortfall_pairs"]],
                ["distinct_families", summary["distinct_families"]],
                ["distinct_provinces", summary["distinct_provinces"]],
                ["param_contrast_pairs", summary["param_contrast_pairs"]],
                ["param_contrast_rate", summary["param_contrast_rate"]],
                ["subtype_contrast_pairs", summary["subtype_contrast_pairs"]],
                ["largest_family", summary["largest_family"]],
                ["largest_family_rate", summary["largest_family_rate"]],
                ["largest_province", summary["largest_province"]],
                ["largest_province_rate", summary["largest_province_rate"]],
                ["passes_generation_balance_gate", summary["passes_generation_balance_gate"]],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Pair Type",
        "",
        _md_table(_counter_table(summary["by_pair_type"])),
        "",
        "## Families",
        "",
        _md_table(_counter_table(summary["by_family"])),
        "",
        "## Provinces",
        "",
        _md_table(_counter_table(summary["by_province"])),
        "",
        "## Reject Reasons",
        "",
        _md_table(_counter_table(summary["by_reject_reason"])),
        "",
        "## Samples",
        "",
        _md_table([["pair_id", "family", "type", "positive", "negative", "field", "positive_value", "negative_value"], *_sample_rows(pairs, 12)]),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage 5.1 eval-only quota self-supervised pair candidate generator")
    parser.add_argument("--province-root", default=str(DEFAULT_PROVINCE_ROOT))
    parser.add_argument("--plan-csv", default=str(DEFAULT_PLAN_CSV))
    parser.add_argument("--seed", type=int, default=20260522)
    parser.add_argument("--limit-province-families", type=int, default=0)
    parser.add_argument("--max-bucket-pairs", type=int, default=80)
    parser.add_argument("--max-attempt-factor", type=int, default=80)
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--top-limit", type=int, default=30)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--pairs-csv", default=str(DEFAULT_PAIRS_CSV))
    parser.add_argument("--pairs-jsonl", default=str(DEFAULT_PAIRS_JSONL))
    parser.add_argument("--rejected-csv", default=str(DEFAULT_REJECTED_CSV))
    parser.add_argument("--buckets-csv", default=str(DEFAULT_BUCKETS_CSV))
    args = parser.parse_args()

    started = time.perf_counter()
    pairs, rejected, skipped = _generate(args)
    summary, buckets = _summarize(pairs, rejected, skipped, args.top_limit)

    pair_fields = [
        "pair_id",
        "province",
        "family",
        "pair_type",
        "contrast_field",
        "positive_contrast_value",
        "negative_contrast_value",
        "positive_id",
        "positive_name",
        "positive_unit",
        "positive_book",
        "positive_chapter",
        "negative_id",
        "negative_name",
        "negative_unit",
        "negative_book",
        "negative_chapter",
        "positive_subtype_key",
        "negative_subtype_key",
        "quality_flags",
        "source_db_path",
    ]
    rejected_fields = ["province", "family", "reject_reason", "planned_pairs", "generated_pairs", "shortfall", "detail"]
    _write_csv(Path(args.pairs_csv), pairs, pair_fields)
    _write_jsonl(Path(args.pairs_jsonl), pairs)
    _write_csv(Path(args.rejected_csv), rejected, rejected_fields)
    _write_csv(Path(args.buckets_csv), buckets, ["dimension", "key", "count", "rate"])

    artifacts = {
        "pairs_csv": args.pairs_csv,
        "pairs_jsonl": args.pairs_jsonl,
        "rejected_csv": args.rejected_csv,
        "buckets_csv": args.buckets_csv,
        "report_json": args.report_json,
        "report_md": args.report_md,
    }
    report = {
        "stage": "Goal LTR v1 / stage 5.1 quota self-supervised pair candidate generation",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "province_root": str(Path(args.province_root)),
        "plan_csv": args.plan_csv,
        "seed": args.seed,
        "summary": summary,
        "artifacts": artifacts,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report, pairs)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "no_training": True,
                    "elapsed_sec": report["elapsed_sec"],
                    **{key: summary[key] for key in (
                        "generated_pairs",
                        "rejected_buckets",
                        "rejected_shortfall_pairs",
                        "distinct_families",
                        "distinct_provinces",
                        "param_contrast_pairs",
                        "subtype_contrast_pairs",
                        "largest_family_rate",
                        "largest_province_rate",
                        "passes_generation_balance_gate",
                    )},
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
