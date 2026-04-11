from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.classify_retriever_miss import quota_id_to_book


DEFAULT_INPUT = PROJECT_ROOT / "tests" / "benchmark_papers" / "_latest_result.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "diagnostics" / "recall_gap_report.json"

QUERY_DILUTION_HINTS = (
    "成套",
    "成品",
    "安装",
    "敷设",
    "调试",
    "主材",
    "辅材",
    "附件",
    "含",
    "不含",
    "综合",
    "一般",
    "以内",
    "以下",
    "套",
    "组",
    "台",
)
GENERIC_NOISE_TERMS = {
    "安装",
    "敷设",
    "调试",
    "成套",
    "成品",
    "附件",
    "主材",
    "辅材",
    "一般",
    "综合",
    "以内",
    "以下",
}


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def _compact_len(text: str) -> int:
    return len(re.sub(r"\s+", "", str(text or "")))


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _iter_latest_result_records(payload: dict) -> Iterable[dict]:
    for province_result in payload.get("results", []) or []:
        province = str(province_result.get("province", "") or "")
        for detail in province_result.get("details", []) or []:
            record = dict(detail)
            record.setdefault("province", province)
            yield record


def _normalize_record(record: dict) -> dict:
    expected_quota_ids = list(
        record.get("expected_quota_ids")
        or record.get("stored_ids")
        or []
    )
    expected_quota_names = list(
        record.get("expected_quota_names")
        or record.get("stored_names")
        or []
    )
    predicted_quota_id = str(
        record.get("predicted_quota_id")
        or record.get("algo_id")
        or ""
    ).strip()
    predicted_quota_name = str(
        record.get("predicted_quota_name")
        or record.get("algo_name")
        or ""
    ).strip()
    miss_category = str(record.get("miss_category", "") or "").strip()
    oracle_in_candidates = bool(record.get("oracle_in_candidates", False))
    if not miss_category and not oracle_in_candidates:
        miss_category = "recall_miss"

    return {
        "province": str(record.get("province", "") or "").strip(),
        "bill_name": _clean_text(record.get("bill_name", "")),
        "bill_text": _clean_text(record.get("bill_text", "")),
        "specialty": str(record.get("specialty", "") or "").strip(),
        "expected_quota_ids": expected_quota_ids,
        "expected_quota_names": [_clean_text(name) for name in expected_quota_names if _clean_text(name)],
        "predicted_quota_id": predicted_quota_id,
        "predicted_quota_name": predicted_quota_name,
        "cause": str(record.get("cause", "") or "").strip(),
        "oracle_in_candidates": oracle_in_candidates,
        "miss_category": miss_category,
        "error_stage": str(record.get("error_stage", "") or "").strip(),
        "error_type": str(record.get("error_type", "") or "").strip(),
        "trace_path": list(record.get("trace_path") or []),
    }


def load_records(input_path: str | Path) -> list[dict]:
    path = Path(input_path)
    if path.is_dir():
        manifest_path = path / "manifest.json"
        all_errors_path = path / "all_errors.jsonl"
        if all_errors_path.exists():
            return [_normalize_record(row) for row in _load_jsonl(all_errors_path)]
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = dict(manifest.get("files") or {})
            target = Path(files.get("all_errors", ""))
            if target.exists():
                return [_normalize_record(row) for row in _load_jsonl(target)]
        raise FileNotFoundError(f"no all_errors.jsonl found under {path}")

    if path.suffix.lower() == ".jsonl":
        return [_normalize_record(row) for row in _load_jsonl(path)]

    payload = json.loads(path.read_text(encoding="utf-8"))
    return [_normalize_record(row) for row in _iter_latest_result_records(payload)]


def filter_recall_records(records: Iterable[dict]) -> list[dict]:
    recall_rows: list[dict] = []
    for record in records:
        normalized = _normalize_record(record)
        if normalized["oracle_in_candidates"]:
            continue
        if normalized["miss_category"] and normalized["miss_category"] != "recall_miss":
            continue
        recall_rows.append(normalized)
    return recall_rows


def _text_terms(text: str) -> set[str]:
    terms = set(re.findall(r"[A-Za-z]{1,8}\d*|[\u4e00-\u9fff]{2,6}", text))
    return {term.strip().upper() if re.fullmatch(r"[A-Za-z]{1,8}\d*", term) else term.strip() for term in terms if term.strip()}


def _looks_like_short_alias(text: str) -> bool:
    compact = str(text or "").strip().upper()
    if not compact:
        return False
    return bool(re.fullmatch(r"[A-Z]{1,6}\d{0,4}", compact))


def classify_recall_issue(record: dict) -> str:
    cause = str(record.get("cause", "") or "").strip()
    expected_books = {
        quota_id_to_book(value)
        for value in record.get("expected_quota_ids", []) or []
        if quota_id_to_book(value)
    }
    predicted_book = quota_id_to_book(record.get("predicted_quota_id", ""))

    if cause == "wrong_book":
        return "rule_mislead"
    if predicted_book and expected_books and predicted_book not in expected_books:
        return "rule_mislead"
    if "router" in set(record.get("trace_path") or []) and expected_books and predicted_book and predicted_book not in expected_books:
        return "rule_mislead"
    if cause == "synonym_gap":
        return "synonym_gap"

    bill_name = str(record.get("bill_name", "") or "")
    expected_names = list(record.get("expected_quota_names", []) or [])
    bill_terms = _text_terms(bill_name)
    expected_terms = _text_terms(" ".join(expected_names))
    if _looks_like_short_alias(bill_name) and bill_terms and not (bill_terms & expected_terms):
        return "synonym_gap"

    return "query_dilution"


def _synonym_key(record: dict) -> str:
    bill_name = str(record.get("bill_name", "") or "").strip()
    expected_name = str((record.get("expected_quota_names") or [""])[0] or "").strip()
    source = bill_name or str(record.get("bill_text", "") or "")[:24].strip()
    return f"{source} -> {expected_name}".strip()


def _synonym_detail(record: dict) -> dict:
    source = str(record.get("bill_name", "") or "").strip()
    target = str((record.get("expected_quota_names") or [""])[0] or "").strip()
    if not source:
        source = str(record.get("bill_text", "") or "")[:24].strip()
    return {
        "source_term": source,
        "target_term": target,
        "suggested_fix": f"add_synonym:{source}->{target}" if source and target else "review_synonym_gap",
    }


def _query_dilution_terms(record: dict) -> list[str]:
    bill_name = str(record.get("bill_name", "") or "")
    bill_text = str(record.get("bill_text", "") or "")
    expected_name = str((record.get("expected_quota_names") or [""])[0] or "")

    residual = bill_text
    for value in (bill_name, expected_name):
        if value:
            residual = residual.replace(value, " ")
    residual = _clean_text(residual)

    terms: list[str] = []
    for hint in QUERY_DILUTION_HINTS:
        if hint in residual and hint not in bill_name:
            terms.append(hint)

    if terms:
        return list(dict.fromkeys(terms))

    fallback_terms = []
    for term in re.findall(r"[\u4e00-\u9fff]{2,6}", residual):
        term = term.strip()
        if not term or term in bill_name or term in expected_name:
            continue
        if term in {"控制电缆", "配电箱", "给水管", "电缆", "管道"}:
            continue
        fallback_terms.append(term)
    unique = []
    for term in fallback_terms:
        if term not in unique:
            unique.append(term)
        if len(unique) >= 3:
            break
    return unique


def _query_dilution_detail(record: dict) -> dict:
    bill_name = str(record.get("bill_name", "") or "")
    bill_text = str(record.get("bill_text", "") or "")
    expected_name = str((record.get("expected_quota_names") or [""])[0] or "")

    residual = bill_text
    for value in (bill_name, expected_name):
        if value:
            residual = residual.replace(value, " ")
    residual = _clean_text(residual)

    added_ratio = 0.0
    base_len = max(_compact_len(bill_name), 1)
    if residual:
        added_ratio = round(_compact_len(residual) / base_len, 3)

    return {
        "residual_text": residual,
        "added_length_ratio": added_ratio,
        "suggested_fix": "trim_query_builder_append_terms",
    }


def _rule_key(record: dict) -> str:
    expected_books = sorted(
        {
            quota_id_to_book(value)
            for value in record.get("expected_quota_ids", []) or []
            if quota_id_to_book(value)
        }
    )
    predicted_book = quota_id_to_book(record.get("predicted_quota_id", ""))
    specialty = str(record.get("specialty", "") or "").strip().upper()

    if predicted_book and expected_books:
        return f"book:{predicted_book}->{'/'.join(expected_books)}"
    if specialty and expected_books:
        return f"specialty:{specialty}->{'/'.join(expected_books)}"
    if "router" in set(record.get("trace_path") or []):
        return "router:generic_mislead"
    return "route:unknown"


def _rule_detail(record: dict) -> dict:
    expected_books = sorted(
        {
            quota_id_to_book(value)
            for value in record.get("expected_quota_ids", []) or []
            if quota_id_to_book(value)
        }
    )
    predicted_book = quota_id_to_book(record.get("predicted_quota_id", ""))
    trace_path = [str(value).strip() for value in (record.get("trace_path") or []) if str(value).strip()]
    expected_path = "/".join(expected_books)
    rule_target = f"{predicted_book}->{expected_path}" if predicted_book and expected_path else "routing_rule"
    return {
        "predicted_book": predicted_book,
        "expected_books": expected_books,
        "trace_path": trace_path,
        "suggested_fix": f"review_rule:{rule_target}",
    }


def _top_entries(
    counter: Counter,
    examples: dict[str, list[dict]],
    limit: int,
    *,
    detail_builder=None,
) -> list[dict]:
    ranked: list[dict] = []
    for key, count in counter.most_common(limit):
        example_rows = examples.get(key, [])[:3]
        provinces = sorted(
            {
                str(row.get("province", "") or "")
                for row in example_rows
                if str(row.get("province", "") or "")
            }
        )
        entry = {
            "key": key,
            "count": count,
            "provinces": provinces,
            "examples": [
                {
                    "province": row.get("province", ""),
                    "bill_name": row.get("bill_name", ""),
                    "bill_text": row.get("bill_text", ""),
                    "expected_quota_name": (row.get("expected_quota_names") or [""])[0],
                    "predicted_quota_name": row.get("predicted_quota_name", ""),
                }
                for row in example_rows
            ],
        }
        if detail_builder is not None and example_rows:
            entry.update(detail_builder(example_rows[0]))
        ranked.append(entry)
    return ranked


def _build_priority_actions(report: dict) -> list[dict]:
    actions: list[dict] = []

    top_synonym = next(iter(report.get("top_missing_synonyms") or []), None)
    if top_synonym:
        actions.append(
            {
                "key": top_synonym.get("key", ""),
                "category": "synonym_gap",
                "priority": 1,
                "count": top_synonym.get("count", 0),
                "suggested_fix": top_synonym.get("suggested_fix", "review_synonym_gap"),
            }
        )

    top_dilution = next(iter(report.get("top_query_dilution_terms") or []), None)
    if top_dilution:
        actions.append(
            {
                "key": top_dilution.get("key", ""),
                "category": "query_dilution",
                "priority": 2,
                "count": top_dilution.get("count", 0),
                "suggested_fix": top_dilution.get("suggested_fix", "trim_query_builder_append_terms"),
            }
        )

    top_rule = next(iter(report.get("top_rule_misleads") or []), None)
    if top_rule:
        actions.append(
            {
                "key": top_rule.get("key", ""),
                "category": "rule_mislead",
                "priority": 3,
                "count": top_rule.get("count", 0),
                "suggested_fix": top_rule.get("suggested_fix", "review_rule"),
            }
        )

    return actions


def analyze_recall_gaps(
    records: Iterable[dict],
    *,
    top_synonyms: int = 100,
    top_terms: int = 50,
    top_rules: int = 20,
) -> dict:
    recall_records = filter_recall_records(records)
    issue_counter: Counter[str] = Counter()
    synonym_counter: Counter[str] = Counter()
    dilution_counter: Counter[str] = Counter()
    rule_counter: Counter[str] = Counter()
    synonym_examples: dict[str, list[dict]] = defaultdict(list)
    dilution_examples: dict[str, list[dict]] = defaultdict(list)
    rule_examples: dict[str, list[dict]] = defaultdict(list)
    province_counter: Counter[str] = Counter()
    province_issue_counter: dict[str, Counter[str]] = defaultdict(Counter)
    dilution_ratio_accumulator: dict[str, list[float]] = defaultdict(list)

    for record in recall_records:
        issue_type = classify_recall_issue(record)
        issue_counter[issue_type] += 1
        province = str(record.get("province", "") or "").strip()
        if province:
            province_counter[province] += 1
            province_issue_counter[province][issue_type] += 1

        if issue_type == "synonym_gap":
            key = _synonym_key(record)
            synonym_counter[key] += 1
            synonym_examples[key].append(record)
            continue

        if issue_type == "rule_mislead":
            key = _rule_key(record)
            rule_counter[key] += 1
            rule_examples[key].append(record)
            continue

        dilution_detail = _query_dilution_detail(record)
        for term in _query_dilution_terms(record):
            if term not in GENERIC_NOISE_TERMS and term not in QUERY_DILUTION_HINTS:
                continue
            dilution_counter[term] += 1
            dilution_examples[term].append(record)
            dilution_ratio_accumulator[term].append(float(dilution_detail.get("added_length_ratio", 0.0) or 0.0))

    report = {
        "recall_miss_total": len(recall_records),
        "category_counts": dict(issue_counter),
        "province_recall_miss_counts": dict(province_counter.most_common()),
        "province_category_counts": {
            province: dict(counter)
            for province, counter in sorted(
                province_issue_counter.items(),
                key=lambda item: (-sum(item[1].values()), item[0]),
            )
        },
        "top_missing_synonyms": _top_entries(
            synonym_counter,
            synonym_examples,
            top_synonyms,
            detail_builder=_synonym_detail,
        ),
        "top_query_dilution_terms": _top_entries(
            dilution_counter,
            dilution_examples,
            top_terms,
            detail_builder=_query_dilution_detail,
        ),
        "top_rule_misleads": _top_entries(
            rule_counter,
            rule_examples,
            top_rules,
            detail_builder=_rule_detail,
        ),
    }
    for row in report["top_query_dilution_terms"]:
        ratios = dilution_ratio_accumulator.get(str(row.get("key", "")), [])
        if ratios:
            row["avg_added_length_ratio"] = round(sum(ratios) / len(ratios), 3)
    report["priority_actions"] = _build_priority_actions(report)
    return report


def _print_report(report: dict) -> None:
    print(f"recall_miss_total: {report['recall_miss_total']}")
    print("category_counts:")
    for key, value in sorted((report.get("category_counts") or {}).items()):
        print(f"  {key}: {value}")
    print("province_recall_miss_counts:")
    for key, value in list((report.get("province_recall_miss_counts") or {}).items())[:10]:
        print(f"  {key}: {value}")

    def _print_bucket(title: str, rows: list[dict]) -> None:
        print(f"\n{title}:")
        if not rows:
            print("  (empty)")
            return
        for row in rows:
            provinces = ",".join(row.get("provinces", []))
            print(f"  {row['key']}  x{row['count']}  [{provinces}]")

    _print_bucket("top_missing_synonyms", report.get("top_missing_synonyms", []))
    _print_bucket("top_query_dilution_terms", report.get("top_query_dilution_terms", []))
    _print_bucket("top_rule_misleads", report.get("top_rule_misleads", []))
    _print_bucket("priority_actions", report.get("priority_actions", []))


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断 benchmark 中 oracle 不在候选池的召回缺口")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="输入 latest_result.json / all_errors.jsonl / asset dir")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 JSON 报告路径")
    parser.add_argument("--top-synonyms", type=int, default=100, help="同义词缺口榜单数量")
    parser.add_argument("--top-terms", type=int, default=50, help="query 稀释词榜单数量")
    parser.add_argument("--top-rules", type=int, default=20, help="rule 误导榜单数量")
    args = parser.parse_args()

    records = load_records(args.input)
    report = analyze_recall_gaps(
        records,
        top_synonyms=args.top_synonyms,
        top_terms=args.top_terms,
        top_rules=args.top_rules,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_report(report)
    print(f"\nreport_written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
