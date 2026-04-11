from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.extract_top_gaps import build_top_gap_report, load_records


DEFAULT_INPUT = PROJECT_ROOT / "output" / "diagnostics" / "stage3_top_gaps_report_full.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "diagnostics" / "parser_gap_action_plan.json"

PARAM_REMEDIATION_GUIDE: dict[str, dict[str, list[str]]] = {
    "dn": {
        "touchpoints": ["src/text_parser.py:_extract_dn", "src/text_parser.py:_extract_conduit_dn"],
        "focus": ["补规格/公称直径/外径上下文规则", "区分管道 DN 与非管道尺寸数字"],
    },
    "cable_section": {
        "touchpoints": ["src/text_parser.py:_extract_cable_section", "src/text_parser.py:_extract_cable_bundle_specs"],
        "focus": ["补型号前缀与截面组合格式", "过滤非电缆尺寸噪音"],
    },
    "cable_cores": {
        "touchpoints": ["src/text_parser.py:_extract_cable_cores", "src/text_parser.py:_extract_cable_bundle_specs"],
        "focus": ["补芯数表达式与 x/× 组合", "避免从普通尺寸中误提芯数"],
    },
    "kva": {
        "touchpoints": ["src/text_parser.py:_extract_kva"],
        "focus": ["补容量单位变体", "补上下限档位表达式"],
    },
    "kw": {
        "touchpoints": ["src/text_parser.py:_extract_kw"],
        "focus": ["补功率单位变体", "补中文功率描述"],
    },
    "ampere": {
        "touchpoints": ["src/text_parser.py:_extract_ampere"],
        "focus": ["补电流单位表达式", "避免把型号尾码误提成安培值"],
    },
    "circuits": {
        "touchpoints": ["src/text_parser.py:_extract_circuits"],
        "focus": ["补回路/路数表达式", "区分路由/路灯等伪命中"],
    },
    "port_count": {
        "touchpoints": ["src/text_parser.py:_extract_port_count"],
        "focus": ["补口/端口/接口数量表达式", "区分弱电设备端口与其他计数"],
    },
    "item_count": {
        "touchpoints": ["src/text_parser.py:_extract_item_count"],
        "focus": ["补数量阈值模式，例如 ≤50台/10个点位", "优先覆盖扬声器/终端/设备数量档"],
    },
    "item_length": {
        "touchpoints": ["src/text_parser.py:_extract_item_length"],
        "focus": ["补长度/高度/井深/桩长等工程量表达式", "要求显式 cue，避免裸数字误提"],
    },
    "perimeter": {
        "touchpoints": ["src/text_parser.py:_extract_perimeter"],
        "focus": ["补风口/风阀周长与宽高换算", "过滤非通风类尺寸"],
    },
    "half_perimeter": {
        "touchpoints": ["src/text_parser.py:_extract_half_perimeter"],
        "focus": ["补配电箱/接线箱半周长变体", "保持无规格默认不乱补"],
    },
    "large_side": {
        "touchpoints": ["src/text_parser.py:_extract_perimeter"],
        "focus": ["补大边长阈值表达式", "区分矩形风管尺寸与普通长宽"],
    },
    "switch_gangs": {
        "touchpoints": ["src/text_parser.py:_extract_switch_gangs"],
        "focus": ["补联数表达式", "区分插座极数与开关联数"],
    },
    "elevator_stops": {
        "touchpoints": ["src/text_parser.py:_extract_elevator_stops"],
        "focus": ["补层数/站数表达式", "避免建筑楼层说明误提"],
    },
    "ground_bar_width": {
        "touchpoints": ["src/text_parser.py:_extract_ground_bar_width"],
        "focus": ["补扁钢/母线尺寸变体", "区分电缆截面与扁钢尺寸"],
    },
    "bridge_wh_sum": {
        "touchpoints": ["src/text_parser.py:_extract_bridge_wh_sum", "src/text_parser.py:_extract_bridge_type"],
        "focus": ["补桥架宽高求和格式", "区分桥架尺寸与其他矩形尺寸"],
    },
}


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def _split_pattern_key(value: object) -> tuple[str, str]:
    text = _clean_text(value)
    if ":" not in text:
        return text, ""
    left, right = text.split(":", 1)
    return _clean_text(left), _clean_text(right)


def _load_gap_report(input_path: Path) -> dict:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "parser_gaps" in payload:
        return payload
    records = load_records(str(input_path))
    return build_top_gap_report(records)


def build_parser_gap_action_plan(
    report: dict,
    *,
    top_patterns_per_param: int = 5,
    examples_per_pattern: int = 2,
) -> dict:
    parser_report = dict(report.get("parser_gaps") or {})
    missing_params = {
        _clean_text(row.get("key")): int(row.get("count") or 0)
        for row in parser_report.get("top_parser_missing_params", [])
    }
    grouped_patterns: dict[str, list[dict]] = defaultdict(list)

    for row in parser_report.get("top_parser_missing_patterns", []):
        param_key, expected_value = _split_pattern_key(row.get("key"))
        if not param_key:
            continue
        grouped_patterns[param_key].append(
            {
                "pattern_key": _clean_text(row.get("key")),
                "expected_value": expected_value,
                "count": int(row.get("count") or 0),
                "examples": list(row.get("examples") or [])[:examples_per_pattern],
            }
        )

    action_items: list[dict] = []
    for param_key, patterns in grouped_patterns.items():
        patterns.sort(key=lambda item: (-int(item["count"]), item["pattern_key"]))
        remediation = PARAM_REMEDIATION_GUIDE.get(param_key, {})
        action_items.append(
            {
                "param_key": param_key,
                "missing_case_count": int(missing_params.get(param_key, sum(item["count"] for item in patterns))),
                "pattern_count": len(patterns),
                "parser_touchpoints": list(remediation.get("touchpoints") or ["src/text_parser.py"]),
                "remediation_focus": list(remediation.get("focus") or ["补该参数的显式提取规则", "补对应负样本，避免误提取"]),
                "top_patterns": patterns[:top_patterns_per_param],
            }
        )

    action_items.sort(
        key=lambda item: (
            -int(item["missing_case_count"]),
            -int(item["pattern_count"]),
            item["param_key"],
        )
    )

    next_actions = [
        {
            "rank": index + 1,
            "param_key": item["param_key"],
            "missing_case_count": item["missing_case_count"],
            "first_touchpoint": item["parser_touchpoints"][0],
            "top_pattern_key": item["top_patterns"][0]["pattern_key"] if item["top_patterns"] else "",
        }
        for index, item in enumerate(action_items[:10])
    ]

    return {
        "meta": {
            "parser_gap_case_count": int(parser_report.get("parser_gap_case_count") or 0),
            "param_count": len(action_items),
            "top_patterns_per_param": top_patterns_per_param,
            "examples_per_pattern": examples_per_pattern,
        },
        "action_items": action_items,
        "next_actions": next_actions,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build parser-gap remediation plan from a top-gaps report or benchmark result.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="stage3_top_gaps_report*.json or latest_result/all_errors input")
    parser.add_argument(
        "--output",
        nargs="?",
        const=str(DEFAULT_OUTPUT),
        default=str(DEFAULT_OUTPUT),
        help="Output JSON path. Passing bare --output falls back to the default diagnostics path.",
    )
    parser.add_argument("--top-patterns-per-param", type=int, default=5, help="Max patterns to keep per param key.")
    parser.add_argument("--examples-per-pattern", type=int, default=2, help="Max examples to keep for each pattern.")
    parser.add_argument("--preview", action="store_true", help="Print summary without writing file.")
    return parser


def _print_plan(plan: dict) -> None:
    meta = dict(plan.get("meta") or {})
    print(f"parser_gap_case_count: {meta.get('parser_gap_case_count', 0)}")
    print(f"param_count: {meta.get('param_count', 0)}")
    print("top_actions:")
    for row in plan.get("next_actions", [])[:10]:
        print(
            f"  #{row['rank']} {row['param_key']} cases={row['missing_case_count']} "
            f"touchpoint={row['first_touchpoint']} pattern={row['top_pattern_key']}"
        )


def main() -> int:
    args = build_arg_parser().parse_args()
    report = _load_gap_report(Path(args.input))
    plan = build_parser_gap_action_plan(
        report,
        top_patterns_per_param=args.top_patterns_per_param,
        examples_per_pattern=args.examples_per_pattern,
    )

    _print_plan(plan)
    if not args.preview:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"plan_written: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
