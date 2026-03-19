from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import _load_bill_items_for_run, _resolve_run_province
from src.bill_cleaner import analyze_project_context, format_project_overview
from src.context_builder import summarize_batch_context_for_trace
from src.match_engine import init_experience_db, init_search_components, match_by_mode


DEFAULT_INPUT_PATH = PROJECT_ROOT / "tests" / "fixtures" / "dirty_data_sample.xlsx"
INSTALL_ITEM_HINTS = (
    "安装",
    "配电箱",
    "配电柜",
    "桥架",
    "电缆",
    "电线",
    "配线",
    "配管",
    "照明",
    "灯具",
    "开关",
    "插座",
    "风管",
    "风口",
    "风阀",
    "喷淋",
    "消防",
    "消火栓",
    "给水",
    "排水",
    "阀门",
    "管道",
    "电机",
    "交换机",
    "网线",
)
TRACE_STEP_KEYS = {
    "stage",
    "selected_quota",
    "selected_reasoning",
    "candidates_count",
    "candidates",
    "quota_ids",
    "confidence",
    "reason",
    "error_type",
    "error_reason",
    "books",
    "threshold",
    "backup_confidence",
    "search_confidence",
    "replaced_source",
    "replaced_confidence",
    "materials_count",
    "experience_source",
    "quota_id",
    "degraded_confidence",
    "final_source",
    "final_confidence",
    "final_validation",
    "final_review_correction",
    "reasoning_engaged",
    "reasoning_conflicts",
    "reasoning_decision",
    "reasoning_compare_points",
    "query_route",
    "batch_context",
    "early_type",
}


def _looks_like_install_specialty(specialty: str) -> bool:
    specialty = str(specialty or "").strip().upper()
    return bool(re.match(r"^C\d+$", specialty))


def _item_install_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("name", "description", "specialty_name", "section", "sheet_name")
    ).strip()


def _looks_like_install_item(item: dict[str, Any]) -> bool:
    if _looks_like_install_specialty(item.get("specialty", "")):
        return True
    text = _item_install_text(item)
    return any(keyword in text for keyword in INSTALL_ITEM_HINTS)


def filter_install_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in (items or []) if _looks_like_install_item(item)]


def select_smoke_items(items: list[dict[str, Any]],
                       *,
                       limit: int | None = None,
                       item_index: int | None = None) -> list[dict[str, Any]]:
    selected = list(items or [])
    if item_index is not None:
        if item_index < 1 or item_index > len(selected):
            raise IndexError(
                f"item_index={item_index} 超出安装清单范围，当前仅有 {len(selected)} 条"
            )
        return [selected[item_index - 1]]
    if limit is not None and limit >= 0:
        return selected[:limit]
    return selected


def _compact_trace_steps(trace: dict[str, Any] | None,
                         *,
                         tail: int = 4) -> list[dict[str, Any]]:
    if not isinstance(trace, dict):
        return []

    steps_out: list[dict[str, Any]] = []
    for step in trace.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        compact = {
            key: value
            for key, value in step.items()
            if key in TRACE_STEP_KEYS and value not in (None, "", [], {})
        }
        if compact:
            steps_out.append(compact)
    if tail <= 0:
        return []
    return steps_out[-tail:]


def build_result_snapshot(result: dict[str, Any],
                          *,
                          index: int,
                          trace_tail: int = 4) -> dict[str, Any]:
    item = dict(result.get("bill_item") or {})
    trace = dict(result.get("trace") or {})
    quotas = []
    for quota in result.get("quotas", [])[:3]:
        if not isinstance(quota, dict):
            continue
        quotas.append({
            "quota_id": quota.get("quota_id", ""),
            "name": quota.get("name", ""),
            "unit": quota.get("unit", ""),
            "param_score": quota.get("param_score"),
            "rerank_score": quota.get("rerank_score"),
        })

    return {
        "index": index,
        "batch_index": item.get("_batch_index"),
        "code": item.get("code", ""),
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "specialty": item.get("specialty", ""),
        "specialty_name": item.get("specialty_name", ""),
        "params": item.get("params") or {},
        "context_prior": item.get("context_prior") or {},
        "batch_context": summarize_batch_context_for_trace(item),
        "match_source": result.get("match_source", ""),
        "confidence": result.get("confidence", 0),
        "quotas": quotas,
        "reasoning_decision": result.get("reasoning_decision") or {},
        "needs_reasoning": bool(result.get("needs_reasoning")),
        "require_final_review": bool(result.get("require_final_review")),
        "final_validation": result.get("final_validation") or {},
        "final_review_correction": result.get("final_review_correction") or {},
        "trace_path": trace.get("path", []),
        "trace_tail": _compact_trace_steps(trace, tail=trace_tail),
    }


def run_install_smoke(input_file: str | Path | None = None,
                      *,
                      province: str | None = None,
                      mode: str = "search",
                      limit: int | None = 3,
                      item_index: int | None = None,
                      no_experience: bool = True,
                      sheet: str | None = None,
                      trace_tail: int = 4,
                      agent_llm: str | None = None) -> dict[str, Any]:
    input_path = Path(input_file) if input_file else DEFAULT_INPUT_PATH
    if not input_path.exists():
        raise FileNotFoundError(
            f"输入文件不存在: {input_path}. 可显式传入文件，或先生成 {DEFAULT_INPUT_PATH.name}"
        )

    resolved_province = _resolve_run_province(
        province,
        interactive=False,
        json_output=None,
    )

    started = time.perf_counter()
    loaded_items = _load_bill_items_for_run(
        input_path,
        sheet=sheet,
        limit=None,
        province=resolved_province,
    )
    install_items = filter_install_items(loaded_items)
    selected_items = select_smoke_items(
        install_items,
        limit=limit,
        item_index=item_index,
    )
    if not selected_items:
        raise RuntimeError("未筛出任何安装类清单项，无法执行安装 smoke。")

    project_overview = ""
    if mode == "agent":
        project_overview = format_project_overview(
            analyze_project_context(selected_items)
        )

    searcher, validator = init_search_components(resolved_province)
    experience_db = init_experience_db(
        no_experience=no_experience,
        province=resolved_province,
    )
    results = match_by_mode(
        mode,
        selected_items,
        searcher,
        validator,
        experience_db,
        resolved_province,
        agent_llm=agent_llm,
        project_overview=project_overview,
        progress_callback=None,
    )

    elapsed = time.perf_counter() - started
    snapshots = [
        build_result_snapshot(result, index=index + 1, trace_tail=trace_tail)
        for index, result in enumerate(results)
    ]
    return {
        "input_file": str(input_path),
        "province": resolved_province,
        "mode": mode,
        "elapsed_sec": round(elapsed, 3),
        "loaded_items": len(loaded_items),
        "install_items": len(install_items),
        "selected_items": len(selected_items),
        "items": snapshots,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="安装链路轻量 smoke：验证 clean/context/retrieve/validate/trace 主链。"
    )
    parser.add_argument(
        "input_file",
        nargs="?",
        default=str(DEFAULT_INPUT_PATH),
        help="待测清单文件路径；默认 tests/fixtures/dirty_data_sample.xlsx",
    )
    parser.add_argument("--province", default=None, help="省份定额库名称")
    parser.add_argument(
        "--mode",
        choices=("search", "agent"),
        default="search",
        help="匹配模式",
    )
    parser.add_argument("--limit", type=int, default=3, help="安装项筛选后仅跑前 N 条")
    parser.add_argument("--item-index", type=int, default=None, help="仅跑第 N 条安装项（1-based）")
    parser.add_argument("--sheet", default=None, help="指定 sheet")
    parser.add_argument("--trace-tail", type=int, default=4, help="每条结果保留最后 N 个 trace step")
    parser.add_argument("--agent-llm", default=None, help="agent 模式指定模型")
    parser.add_argument("--with-experience", action="store_true", help="启用经验库")
    parser.add_argument("--output", default=None, help="把 JSON 结果写入文件")
    return parser


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    report = run_install_smoke(
        input_file=args.input_file,
        province=args.province,
        mode=args.mode,
        limit=args.limit,
        item_index=args.item_index,
        no_experience=not args.with_experience,
        sheet=args.sheet,
        trace_tail=args.trace_tail,
        agent_llm=args.agent_llm,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
