from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.text_parser import parser as text_parser
from tools.diagnose_recall_gap import analyze_recall_gaps, load_records


DEFAULT_INPUT = PROJECT_ROOT / "tests" / "benchmark_papers" / "_latest_result.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "diagnostics" / "top_gaps_report.json"

PRIMARY_PARAM_KEYS = (
    "dn",
    "cable_section",
    "cable_cores",
    "kva",
    "kw",
    "ampere",
    "circuits",
    "port_count",
    "item_count",
    "item_length",
    "perimeter",
    "half_perimeter",
    "large_side",
    "switch_gangs",
    "elevator_stops",
    "ground_bar_width",
    "bridge_wh_sum",
)

PARAM_CUE_PATTERNS: dict[str, tuple[str, ...]] = {
    "dn": (
        r"[Dd][Nn]\s*[-_]?\s*\d+",
        r"[Dd][Ee]\s*\d+",
        r"(?:公称直径|管径|直径|规格[：:])\s*[≤≥<>]?\s*\d+",
        r"(?:钢管|碳钢管|镀锌钢管|涂塑钢管|不锈钢管|铸铁管|PE管|PPR管|PVC管|HDPE管)\s*[-_：:]?\s*\d+",
    ),
    "cable_section": (
        r"(?:电缆|电线|导线|配线|穿线).*(?:\d+\s*[*×xX]\s*\d+(?:\.\d+)?)",
        r"(?:截面|规格)\s*[：:]?\s*\d+(?:\.\d+)?",
        r"(?:BV|BVR|BYJ|YJV|YJY|RVS|RVV)\s*[-]?\s*\d+(?:\.\d+)?",
    ),
    "cable_cores": (
        r"(?:\d+\s*[*×xX]\s*\d+(?:\.\d+)?)",
        r"(?:\d+)\s*芯",
    ),
    "kva": (r"\d+(?:\.\d+)?\s*kVA",),
    "kw": (r"\d+(?:\.\d+)?\s*kW",),
    "ampere": (r"\d+(?:\.\d+)?\s*A(?![A-Za-z])",),
    "circuits": (r"(?:回路|回路数[：:]?\s*\d+|\d+\s*回路)",),
    "port_count": (r"\d+\s*口",),
    "item_count": (
        r"(?:数量|台数|只数|套数|樘数|点位|终端数量|扬声器数量)",
        r"[≤≥<>]?\s*\d+\s*(?:台|个|只|套|樘|点)",
    ),
    "item_length": (
        r"(?:长度|延长米|平均桩长|桩长|管长)",
        r"[≤≥<>]?\s*\d+(?:\.\d+)?\s*(?:m|米)(?!2)",
    ),
    "perimeter": (
        r"(?:周长|风口|风阀|消声器)",
        r"\d+\s*[*×xX]\s*\d+",
    ),
    "half_perimeter": (
        r"半周长",
        r"(?:配电箱|控制箱|接线箱|弱电箱|端子箱).*(?:\d+\s*[*×xX]\s*\d+)",
    ),
    "large_side": (
        r"大边长",
        r"\d+\s*[*×xX]\s*\d+",
    ),
    "switch_gangs": (r"(?:单联|双联|三联|四联|\d+\s*联)",),
    "elevator_stops": (r"(?:停靠层数|停靠站数|站数|层数)\s*[：:]?\s*-?\d+",),
    "ground_bar_width": (r"(?:扁钢|接地母线|接地母带).*\d+\s*[*×xX]\s*\d+",),
    "bridge_wh_sum": (r"(?:桥架|线槽).*\d+\s*[*×xX]\s*\d+",),
}

PARAM_DOMAIN_PATTERNS: dict[str, tuple[str, ...]] = {
    "dn": (
        r"(?:[Dd][Nn]\s*[-_]?\s*\d+|[Dd][Ee]\s*\d+)",
        r"(?:管|配管|线管|电线管|套管|钢管|镀锌|JDG|KBG|SC管|RC管|PVC管|PPR管|PE管|HDPE管|阀|阀门|法兰|喷头|龙头|水嘴|分配器|冷媒)",
    ),
    "cable_section": (
        r"(?:电缆|电线|导线|配线|穿线|线缆|控制电缆|动力电缆|电力电缆)",
        r"(?:BV|BVR|BYJ|YJV|YJY|RVS|RVV|KVV|KVVP|KVVR|WDZ|NH-|ZR-)",
    ),
    "perimeter": (
        r"(?:周长|风口|百叶|散流器|喷口|消声器|消声弯头|静压箱|风阀|防火阀|止回阀|调节阀)",
    ),
}

PARAM_REQUIRE_CUE_WITH_DOMAIN = {
    "dn",
}


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def _record_text(record: dict) -> str:
    return _clean_text(
        " ".join(
            part
            for part in (
                record.get("bill_name", ""),
                record.get("bill_text", ""),
            )
            if str(part or "").strip()
        )
    )


def _expected_name(record: dict) -> str:
    expected_names = list(record.get("expected_quota_names") or [])
    return _clean_text(expected_names[0] if expected_names else "")


def _matches_any_pattern(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def _has_explicit_cable_section_value(text: str) -> bool:
    return bool(
        re.search(r"\d+\s*[*×xX]\s*\d+(?:\.\d+)?", text)
        or re.search(r"(?:截面|规格)\s*[：:()（）]?\s*-?\s*\d+(?:\.\d+)?(?:\s*mm[²2])?", text, flags=re.IGNORECASE)
        or re.search(
            r"(?:BV|BVR|BYJ|BYJR|BYP|BLV|RVS|RVV|YJV|YJY|KVV|KVVP|KVVR|KYJY|KYJV)\s*-?\s*\d+(?:\.\d+)?",
            text,
            flags=re.IGNORECASE,
        )
        or re.search(r"\d+(?:\.\d+)?\s*mm[²2]", text, flags=re.IGNORECASE)
    )


def _has_explicit_perimeter_value(text: str) -> bool:
    return bool(
        re.search(r"周长[^\d]{0,8}\d{2,5}", text)
        or re.search(r"[Φφ]\s*\d{2,4}", text)
        or re.search(r"\d{2,4}\s*[*×xX]\s*(?:\(\s*)?\d{2,4}(?:\s*[+＋]\s*\d{2,4})?(?:\s*\))?", text)
    )


def _has_explicit_item_length_value(text: str) -> bool:
    return bool(
        re.search(r"(?:长度|延长米|平均桩长|桩长|管长|桩深|檐口高度|建筑檐高|基础标高|井深|平均井深|内周长)[^0-9]{0,10}\d", text)
        or re.search(r"(?:柱高|杆高|灯高)[^0-9]{0,10}\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?\s*(?:mm|MM|m|M|米)", text)
        or re.search(r"(?:喷洒半径|喷射半径)[^0-9]{0,10}\d+(?:\.\d+)?(?:-\d+(?:\.\d+)?)?\s*(?:m|M|米)", text)
        or re.search(r"[≤≥<>]\s*\d+(?:\.\d+)?\s*(?:m|M|米)(?![mM2㎡])", text)
        or re.search(r"\d+(?:\.\d+)?\s*(?:m|M|米)(?![mM2㎡])(?:以下|以内|及以下|及以内)", text)
    )


def _bill_likely_mentions_param(bill_text: str, key: str) -> bool:
    if key == "item_length":
        explicit_cues = (
            "长度",
            "延长米",
            "平均桩长",
            "管长",
            "桩长",
            "桩深",
            "挡墙高度",
            "墙高",
            "檐口高度",
            "建筑檐高",
            "基础标高",
            "井深",
            "平均井深",
            "柱高",
            "杆高",
            "灯高",
            "喷洒半径",
            "喷射半径",
            "内周长",
        )
        if any(cue in bill_text for cue in explicit_cues):
            return _has_explicit_item_length_value(bill_text)
        return _has_explicit_item_length_value(bill_text)

    if key == "cable_section":
        return _matches_any_pattern(bill_text, PARAM_DOMAIN_PATTERNS.get(key, ())) and _has_explicit_cable_section_value(bill_text)

    if key == "perimeter":
        return _matches_any_pattern(bill_text, PARAM_DOMAIN_PATTERNS.get(key, ())) and _has_explicit_perimeter_value(bill_text)

    domain_patterns = PARAM_DOMAIN_PATTERNS.get(key, ())
    if domain_patterns:
        if not _matches_any_pattern(bill_text, domain_patterns):
            return False
        if key not in PARAM_REQUIRE_CUE_WITH_DOMAIN:
            return True

    patterns = PARAM_CUE_PATTERNS.get(key, ())
    if not patterns:
        return True
    return _matches_any_pattern(bill_text, patterns)


def _is_param_effectively_present(key: str, parsed_bill: dict, bill_text: str, expected_name: str) -> bool:
    value = parsed_bill.get(key)
    if value is not None:
        return True

    if key != "dn":
        return False

    conduit_dn = parsed_bill.get("conduit_dn")
    if conduit_dn is None:
        return False

    conduit_context_tokens = ("配管", "导管", "电线管", "穿线管", "JDG", "KBG", "SC", "RC", "PVC管", "暗配", "明配", "暗敷", "明敷")
    combined_text = f"{bill_text} {expected_name}"
    return any(token in combined_text for token in conduit_context_tokens)


def analyze_parser_gaps(records: list[dict], *, top_n: int = 50) -> dict:
    param_counter: Counter[str] = Counter()
    pattern_counter: Counter[str] = Counter()
    param_examples: dict[str, list[dict]] = defaultdict(list)
    pattern_examples: dict[str, list[dict]] = defaultdict(list)
    total_cases = 0

    for record in records:
        if record.get("is_match"):
            continue

        bill_text = _record_text(record)
        expected_name = _expected_name(record)
        if not bill_text or not expected_name:
            continue

        parsed_bill = text_parser.parse(bill_text)
        parsed_expected = text_parser.parse(expected_name)
        missing_keys = [
            key
            for key in PRIMARY_PARAM_KEYS
            if (
                parsed_expected.get(key) is not None
                and not _is_param_effectively_present(key, parsed_bill, bill_text, expected_name)
                and _bill_likely_mentions_param(bill_text, key)
            )
        ]
        if not missing_keys:
            continue

        total_cases += 1
        for key in missing_keys:
            param_counter[key] += 1
            param_examples[key].append(record)
            pattern_key = f"{key}:{parsed_expected.get(key)}"
            pattern_counter[pattern_key] += 1
            pattern_examples[pattern_key].append(record)

    def _top_rows(counter: Counter[str], examples: dict[str, list[dict]]) -> list[dict]:
        rows: list[dict] = []
        for key, count in counter.most_common(top_n):
            sample_rows = examples.get(key, [])[:3]
            rows.append(
                {
                    "key": key,
                    "count": count,
                    "examples": [
                        {
                            "province": row.get("province", ""),
                            "bill_name": row.get("bill_name", ""),
                            "bill_text": row.get("bill_text", ""),
                            "expected_quota_name": _expected_name(row),
                        }
                        for row in sample_rows
                    ],
                }
            )
        return rows

    return {
        "parser_gap_case_count": total_cases,
        "top_parser_missing_params": _top_rows(param_counter, param_examples),
        "top_parser_missing_patterns": _top_rows(pattern_counter, pattern_examples),
    }


def build_top_gap_report(
    records: list[dict],
    *,
    top_parser: int = 50,
    top_synonyms: int = 100,
    top_terms: int = 50,
    top_rules: int = 20,
) -> dict:
    parser_report = analyze_parser_gaps(records, top_n=top_parser)
    recall_report = analyze_recall_gaps(
        records,
        top_synonyms=top_synonyms,
        top_terms=top_terms,
        top_rules=top_rules,
    )
    return {
        "parser_gaps": parser_report,
        "recall_gaps": recall_report,
    }


def _print_report(report: dict) -> None:
    parser_report = dict(report.get("parser_gaps") or {})
    recall_report = dict(report.get("recall_gaps") or {})

    print(f"parser_gap_case_count: {parser_report.get('parser_gap_case_count', 0)}")
    print(f"recall_miss_total: {recall_report.get('recall_miss_total', 0)}")

    print("\ntop_parser_missing_params:")
    for row in parser_report.get("top_parser_missing_params", [])[:10]:
        print(f"  {row['key']}  x{row['count']}")

    print("\ntop_missing_synonyms:")
    for row in recall_report.get("top_missing_synonyms", [])[:10]:
        print(f"  {row['key']}  x{row['count']}")

    print("\ntop_query_dilution_terms:")
    for row in recall_report.get("top_query_dilution_terms", [])[:10]:
        print(f"  {row['key']}  x{row['count']}")

    print("\ntop_rule_misleads:")
    for row in recall_report.get("top_rule_misleads", [])[:10]:
        print(f"  {row['key']}  x{row['count']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="从 benchmark 错题中提取 parser/recall top gaps")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="latest_result.json / all_errors.jsonl / asset dir")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 JSON 路径")
    parser.add_argument("--top-parser", type=int, default=50, help="parser 缺口榜单数量")
    parser.add_argument("--top-synonyms", type=int, default=100, help="同义词缺口榜单数量")
    parser.add_argument("--top-terms", type=int, default=50, help="query 稀释词榜单数量")
    parser.add_argument("--top-rules", type=int, default=20, help="rule 误导榜单数量")
    args = parser.parse_args()

    records = load_records(args.input)
    report = build_top_gap_report(
        records,
        top_parser=args.top_parser,
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
