from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_INVENTORY = AGENT_STATE / "goal_13x_oss_xml_mother_data_manifest_file_inventory.csv"
DEFAULT_FILE_SELECTION = AGENT_STATE / "goal_13x_oss_xml_source_aware_training_matrix" / "file_selection.csv"
DEFAULT_FREEZE_REVIEW = AGENT_STATE / "goal_13x_oss_xml_reranker_freeze_gate_review_summary.json"
DEFAULT_SOURCE_CONCENTRATION = AGENT_STATE / "goal_13x_oss_xml_reranker_freeze_gate_review_source_concentration.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_oss_xml_matrix_expansion_rebalance_plan"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


REGION_HINTS = {
    "FJ": ("福建", "fj"),
    "ZJ": ("浙江", "zj"),
    "JS": ("江苏", "js"),
    "BJ": ("北京", "bj"),
}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _int(row: dict[str, Any], key: str) -> int:
    try:
        return int(float(row.get(key) or 0))
    except (TypeError, ValueError):
        return 0


def _infer_region(row: dict[str, str]) -> str:
    province_dir = str(row.get("province_dir") or "").strip().upper()
    top_dir = str(row.get("top_dir") or "").strip().lower()
    rel = str(row.get("relative_path") or "").strip().lower()
    if province_dir in REGION_HINTS:
        return province_dir
    if top_dir.startswith("fj") or "\\fj" in rel or "/fj" in rel or "福建" in rel:
        return "FJ"
    if top_dir.startswith("zj") or "\\zj" in rel or "/zj" in rel or "浙江" in rel:
        return "ZJ"
    if top_dir.startswith("js") or "\\js" in rel or "/js" in rel or "江苏" in rel:
        return "JS"
    if top_dir.startswith("bj") or "\\bj" in rel or "/bj" in rel or "北京" in rel:
        return "BJ"
    return ""


def _source_family(row: dict[str, str], region: str) -> str:
    top = str(row.get("top_dir") or "<root>").strip() or "<root>"
    province = str(row.get("province_dir") or "-").strip() or "-"
    return f"{region}:{top}:{province}"


def _inventory_rollup(inventory: list[dict[str, str]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    region_source: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"region": "", "source_family": "", "file_count": 0, "dedupe_unique_name_size": 0, "total_gb": 0.0})
    unique_by_source: dict[tuple[str, str], set[str]] = defaultdict(set)
    region_counts: Counter[str] = Counter()
    unique_global = set()
    duplicate_global_count = 0
    empty_files = 0
    unknown_region = 0
    for row in inventory:
        size = _int(row, "size_bytes")
        if size <= 0:
            empty_files += 1
            continue
        region = _infer_region(row)
        if not region:
            unknown_region += 1
            continue
        source_family = _source_family(row, region)
        unique_key = str(row.get("unique_name_size_key") or f"{row.get('file_name')}::{size}")
        if unique_key in unique_global:
            duplicate_global_count += 1
        unique_global.add(unique_key)
        key = (region, source_family)
        acc = region_source[key]
        acc["region"] = region
        acc["source_family"] = source_family
        acc["file_count"] += 1
        acc["total_gb"] += size / (1024**3)
        unique_by_source[key].add(unique_key)
        region_counts[region] += 1
    rows = []
    for key, acc in region_source.items():
        acc = dict(acc)
        acc["dedupe_unique_name_size"] = len(unique_by_source[key])
        acc["total_gb"] = round(acc["total_gb"], 6)
        rows.append(acc)
    rows.sort(key=lambda row: (row["region"], -row["file_count"], row["source_family"]))
    metrics = {
        "known_region_file_count": sum(region_counts.values()),
        "empty_file_count": empty_files,
        "unknown_region_file_count": unknown_region,
        "global_duplicate_name_size_count": duplicate_global_count,
        "global_unique_name_size_count": len(unique_global),
        "region_counts": dict(region_counts),
        "source_family_count": len(rows),
    }
    return rows, metrics


def _current_selection_rollup(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"source_family": "", "selected_files": 0, "parsed_pairs": 0, "sampled_pairs": 0, "parse_errors": 0})
    for row in rows:
        key = str(row.get("source_family") or "<empty>")
        item = grouped[key]
        item["source_family"] = key
        item["selected_files"] += 1
        item["parsed_pairs"] += _int(row, "parsed_pairs")
        item["sampled_pairs"] += _int(row, "sampled_pairs")
        item["parse_errors"] += int(bool(row.get("parse_error")))
    result = list(grouped.values())
    result.sort(key=lambda row: (-row["sampled_pairs"], row["source_family"]))
    return result


def _plan_rows(source_inventory: list[dict[str, Any]], current_selection: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected_sources = {row["source_family"]: row for row in current_selection}
    rows = []
    for row in source_inventory:
        source_family = row["source_family"]
        unique_files = int(row["dedupe_unique_name_size"])
        current = selected_sources.get(source_family, {})
        if unique_files >= 100:
            target_files = 16
        elif unique_files >= 20:
            target_files = 10
        elif unique_files >= 5:
            target_files = min(5, unique_files)
        else:
            target_files = unique_files
        if row["region"] in {"JS", "BJ"}:
            target_files = min(unique_files, max(target_files, 3))
        rows.append(
            {
                "region": row["region"],
                "source_family": source_family,
                "available_files": row["file_count"],
                "dedupe_unique_name_size": unique_files,
                "current_selected_files": int(current.get("selected_files") or 0),
                "current_sampled_pairs": int(current.get("sampled_pairs") or 0),
                "target_files_next_matrix": target_files,
                "target_pairs_per_file_cap": 80 if unique_files >= 10 else 60,
                "priority": (
                    "high_rebalance"
                    if source_family in {"FJ:by_province:FJ", "FJ:fj_other:-", "ZJ:zj_batch:-"}
                    else "expand"
                    if target_files > int(current.get("selected_files") or 0)
                    else "keep_guarded"
                ),
            }
        )
    rows.sort(key=lambda row: (row["region"], row["priority"] != "high_rebalance", -row["target_files_next_matrix"]))
    return rows


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 13.7 OSS XML Matrix Expansion/Rebalance Plan",
        "",
        "Read-only planning stage. No matrix rebuild, training, validation, online integration, threshold change, or GoalSearcher edit was performed.",
        "",
        "## Why",
        "",
        "13.5 produced a strong dev/OOF lead, but 13.6 blocked freeze because single file/source-family contribution and one weak fold made the result too concentrated.",
        "",
        "## Current Risk",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["best_hit1_net", metrics["best_hit1_net"]],
                ["max_source_file_net_share", metrics["max_source_file_net_share"]],
                ["max_source_family_net_share", metrics["max_source_family_net_share"]],
                ["min_fold_net", metrics["min_fold_net"]],
                ["global_duplicate_name_size_count", metrics["global_duplicate_name_size_count"]],
                ["source_family_count_available", metrics["source_family_count_available"]],
            ]
        ),
        "",
        "## Next Matrix Contract",
        "",
        _md_table([["requirement", "target"]] + [[row["requirement"], row["target"]] for row in report["matrix_contract"]]),
        "",
        "## Command Contract",
        "",
        "```powershell",
        report["command_contract"]["matrix_rebuild_command"],
        "```",
        "",
        "## Acceptance Checks Before Rerun Training",
        "",
        _md_table([["check", "target"]] + [[row["check"], row["target"]] for row in report["acceptance_checks"]]),
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.7 OSS XML matrix expansion/rebalance plan 已完成。\n"
        f"目标：去重 duplicate XML、扩展到更多 source/province 文件、降低单文件/source_family 支配。"
        f" 当前风险 max_source_file_net_share={m['max_source_file_net_share']}，"
        f"max_source_family_net_share={m['max_source_family_net_share']}，min_fold_net={m['min_fold_net']}。\n"
        "下一步建议：13.8 OSS XML expanded/rebalanced matrix rebuild。允许重建 dev/OOF matrix，但仍不训练、不用 heldout/hard、不上线、不改 GoalSearcher。\n"
        "禁止：直接 freeze 13.5 candidate、直接跑 heldout/hard validation、把 OOF 结果宣称为通用 Top1 提升、改阈值或接线上。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.7 OSS XML matrix expansion/rebalance plan" not in text:
        rows = f"""          <tr>
            <td>13.7 OSS XML matrix expansion/rebalance plan</td>
            <td>Plan to dedupe duplicate XML, expand source/province coverage, rebalance folds, and reduce source concentration before rerunning dev/OOF.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.6 OSS XML reranker freeze gate review</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.7 OSS XML matrix expansion/rebalance plan")
    parser.add_argument("--inventory", default=str(DEFAULT_INVENTORY))
    parser.add_argument("--file-selection", default=str(DEFAULT_FILE_SELECTION))
    parser.add_argument("--freeze-review", default=str(DEFAULT_FREEZE_REVIEW))
    parser.add_argument("--source-concentration", default=str(DEFAULT_SOURCE_CONCENTRATION))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    args = parser.parse_args()

    inventory = _read_csv(Path(args.inventory))
    selection = _read_csv(Path(args.file_selection))
    freeze_review = _read_json(Path(args.freeze_review))
    source_concentration = _read_csv(Path(args.source_concentration))
    source_inventory, inventory_metrics = _inventory_rollup(inventory)
    current_selection = _current_selection_rollup(selection)
    plan = _plan_rows(source_inventory, current_selection)
    metrics_13_6 = freeze_review.get("metrics", {})
    max_current_selected = max([row["current_selected_files"] for row in plan] or [0])
    target_total_files = sum(row["target_files_next_matrix"] for row in plan if row["target_files_next_matrix"] > 0)
    target_total_files = min(max(target_total_files, 64), 96)
    artifacts = {
        "summary_json": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_summary.json")),
        "summary_md": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_summary.md")),
        "source_inventory_csv": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_source_inventory.csv")),
        "current_selection_rollup_csv": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_current_selection_rollup.csv")),
        "expansion_plan_csv": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_expansion_plan.csv")),
    }
    matrix_contract = [
        {"requirement": "dedupe XML before selection", "target": "unique_name_size_key global dedupe; later upgrade to content hash if needed"},
        {"requirement": "increase file coverage", "target": f"{target_total_files} XML files, selected by source_family round-robin"},
        {"requirement": "reduce per-file dominance", "target": "cap sampled pairs per file at 80; prefer more files over deeper sampling"},
        {"requirement": "source family cap", "target": "accepted groups from any source_family <= 25% of matrix"},
        {"requirement": "single file cap", "target": "accepted groups from any source_file <= 8% of matrix"},
        {"requirement": "fold balance", "target": "5 source-aware folds, min fold groups >= 60% of median fold groups"},
        {"requirement": "recall boundary", "target": "top80 recall >= 0.75; report missing groups separately"},
    ]
    acceptance_checks = [
        {"check": "matrix_rows_match_group", "target": "true"},
        {"check": "forbidden training feature intersection", "target": "0"},
        {"check": "same source file cross fold violations", "target": "0"},
        {"check": "max accepted source_file share", "target": "<= 0.08"},
        {"check": "max accepted source_family share", "target": "<= 0.25"},
        {"check": "observed OOF folds", "target": "5"},
        {"check": "min fold groups / median fold groups", "target": ">= 0.60"},
        {"check": "duplicate unique_name_size selected", "target": "0"},
    ]
    command_contract = {
        "matrix_rebuild_command": (
            "python tools\\goal_13x_oss_xml_source_aware_matrix_build.py "
            "--max-total-files 80 --max-files-per-source-family 12 --max-pairs-per-file 80 "
            "--max-accepted-groups 2400 --top-k 80 --progress-every 5 "
            "--output-dir reports\\agent_state\\goal_13x_oss_xml_source_aware_training_matrix_expanded "
            "--report-json reports\\agent_state\\goal_13x_oss_xml_source_aware_training_matrix_expanded_summary.json "
            "--report-md reports\\agent_state\\goal_13x_oss_xml_source_aware_training_matrix_expanded_summary.md"
        ),
        "requires_builder_patch_before_execution": [
            "dedupe by unique_name_size_key before selection",
            "emit duplicate-selected check",
            "assign folds with balanced source_file round-robin rather than hash-only if needed",
            "emit source_file/source_family accepted group share checks",
        ],
    }
    report = {
        "stage": "13.7 OSS XML matrix expansion/rebalance plan",
        "read_only": True,
        "metrics": {
            "best_hit1_net": metrics_13_6.get("best_hit1_net"),
            "max_source_file_net_share": metrics_13_6.get("max_source_file_net_share"),
            "max_source_family_net_share": metrics_13_6.get("max_source_family_net_share"),
            "min_fold_net": metrics_13_6.get("min_fold_net"),
            "top80_recall_rate": metrics_13_6.get("top80_recall_rate"),
            "available_known_region_files": inventory_metrics["known_region_file_count"],
            "global_duplicate_name_size_count": inventory_metrics["global_duplicate_name_size_count"],
            "global_unique_name_size_count": inventory_metrics["global_unique_name_size_count"],
            "source_family_count_available": inventory_metrics["source_family_count"],
            "current_selected_max_files_per_source_family": max_current_selected,
            "target_total_files_next_matrix": target_total_files,
        },
        "matrix_contract": matrix_contract,
        "acceptance_checks": acceptance_checks,
        "command_contract": command_contract,
        "decision": "Proceed to 13.8 expanded/rebalanced matrix rebuild before any candidate freeze or validation. The 13.5 lead remains promising but must be retested on a less concentrated OSS XML matrix.",
        "source_inventory_top": source_inventory[:30],
        "current_selection_rollup": current_selection,
        "expansion_plan_top": plan[:40],
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only planning only: no matrix rebuild, no training, no heldout/hard selection, no online integration, no threshold change, no GoalSearcher edit, and no feature whitelist edit.",
        "next_stage": {
            "recommended": "13.8 OSS XML expanded/rebalanced matrix rebuild",
            "default": "rebuild matrix only; still no training until matrix checks pass",
        },
    }
    _write_csv(Path(artifacts["source_inventory_csv"]), source_inventory, ["region", "source_family", "file_count", "dedupe_unique_name_size", "total_gb"])
    _write_csv(Path(artifacts["current_selection_rollup_csv"]), current_selection, ["source_family", "selected_files", "parsed_pairs", "sampled_pairs", "parse_errors"])
    _write_csv(Path(artifacts["expansion_plan_csv"]), plan, ["region", "source_family", "available_files", "dedupe_unique_name_size", "current_selected_files", "current_sampled_pairs", "target_files_next_matrix", "target_pairs_per_file_cap", "priority"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"], "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
