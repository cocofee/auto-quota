from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_AUDIT = AGENT_STATE / "goal_12x_non_global_rank25_slice_audit_summary.json"
DEFAULT_ROW_AUDIT = AGENT_STATE / "goal_12x_non_global_rank25_slice_audit_row_audit.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_12x_numeric_spec_tier_minimal_plan_definition"


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


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# 12.4 Numeric/Spec Tier Minimal Plan Definition",
        "",
        "Read-only plan definition for the 9 same-family numeric/spec tier near misses.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key, value in report["metrics"].items():
        lines.append(f"| {key} | {value} |")
    lines.extend(["", "## Decision", "", report["decision"], "", "## Anti-drift", "", report["anti_drift_conclusion"]])
    return "\n".join(lines) + "\n"


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    current = (
        "当前状态：12.4 numeric/spec tier rank-position minimal plan definition 已完成。"
        f"plan_decision={report['metrics']['plan_decision']}；"
        f"plan_rows={report['metrics']['plan_rows']}；"
        f"implementation_allowed_now={str(report['metrics']['implementation_allowed_now']).lower()}；"
        f"future_whatif_allowed={str(report['metrics']['future_whatif_allowed']).lower()}。"
    )
    next_text = (
        "下一步：12.5 numeric/spec tier offline what-if authorization gate。只读判断是否请求 dev/OOF-only what-if；"
        "默认无明确 go 不执行。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：直接实现、训练、调参、改阈值、改 GoalSearcher、使用 heldout/hard 做选择、"
            "或把 9 条异构数值/规格行合成无保护的全局规则。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>12.3 non-global rank_2_5 slice audit</td>"
    row = (
        "          <tr>\n"
        "            <td>12.4 numeric/spec tier minimal plan definition</td>\n"
        "            <td>只读定义同族数值/规格档位近失误的最小 what-if 计划、保护条件和验收边界。</td>\n"
        "            <td><code>reports/agent_state/goal_12x_numeric_spec_tier_minimal_plan_definition_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_12x_numeric_spec_tier_minimal_plan_definition_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def _param_type(row: dict[str, str]) -> str:
    text = " ".join([row.get("query", ""), row.get("top1_name", ""), row.get("expected_names", ""), row.get("top1_reasons", "")])
    if "风量" in text or "m3/h" in text:
        return "air_volume_tier"
    if "周长" in text:
        return "perimeter_tier"
    if "截面" in text or "mm2" in text:
        return "section_tier"
    if "直径" in text or "公称直径" in text or "DN" in text:
        return "diameter_or_dn_tier"
    if "壁厚" in text or "≤" in text or "以内" in text or "mm" in text:
        return "dimension_or_thickness_tier"
    return "numeric_or_spec_tier_unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-summary", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--row-audit", type=Path, default=DEFAULT_ROW_AUDIT)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    audit_summary = _read_json(args.audit_summary)
    all_rows = _read_csv(args.row_audit)
    plan_rows = [row for row in all_rows if row.get("audit_bucket") == "same_family_numeric_or_spec_tier"]

    row_plan: list[dict[str, Any]] = []
    for row in plan_rows:
        param_type = _param_type(row)
        row_plan.append(
            {
                "group_id": row.get("group_id", ""),
                "source_file": row.get("source_file", ""),
                "province": row.get("province", ""),
                "query": row.get("query", ""),
                "query_family": row.get("query_family", ""),
                "positive_ranks": row.get("positive_ranks", ""),
                "top1_name": row.get("top1_name", ""),
                "top1_family": row.get("top1_family", ""),
                "param_type": param_type,
                "future_whatif_scope": "same-family same-book numeric/spec tier comparator only",
                "required_guard": "same_family && same_book_or_chapter && param_type_match && no_family_conflict && candidate_in_rank_2_5",
                "rollback_boundary": "disable numeric/spec tier bonus/comparator branch",
                "implementation_now": False,
            }
        )

    param_summary = [
        {"param_type": key, "rows": count, "share": round(count / len(row_plan), 6) if row_plan else 0}
        for key, count in Counter(row["param_type"] for row in row_plan).most_common()
    ]
    feature_contract = [
        {
            "component": "candidate_filter",
            "definition": "Only compare baseline top1 with positive/alternative candidates already in rank_2_5 within the same family.",
            "must_have": "same query_family/top1_family or explicitly same inferred family; no family conflict reason",
        },
        {
            "component": "numeric_spec_extractor",
            "definition": "Extract comparable param type and tier value from candidate names/reasons: DN/diameter, volume, perimeter, section, dimension/thickness.",
            "must_have": "param_type_match and parseable numeric/spec tier on both candidates",
        },
        {
            "component": "tier_distance_comparator",
            "definition": "Prefer closer or correct tier only when query contains enough numeric/spec evidence.",
            "must_have": "no effect when query lacks numeric/spec evidence or values are incomparable",
        },
        {
            "component": "fallback",
            "definition": "Keep current ranking untouched unless all guards pass.",
            "must_have": "default_off or no-op outside audited slice",
        },
    ]
    command_contract = [
        {
            "stage": "future_12_5_authorization",
            "command": "no execution in 12.4",
            "allowed": False,
            "output": "go/no-go only",
        },
        {
            "stage": "future_dev_oof_whatif_after_go",
            "command": "python tools/goal_12x_numeric_spec_tier_whatif.py --split dev --input <dev artifacts> --output-prefix reports/agent_state/goal_12x_numeric_spec_tier_whatif",
            "allowed": False,
            "output": "scorecard, row details, loss audit, guard coverage, rollback report",
        },
    ]
    loss_budget = [
        {"gate": "new_loss_count", "budget": "0 preferred; any >0 blocks implementation plan", "reason": "tiny 9-row evidence base"},
        {"gate": "source_dominance", "budget": "max source gain share <= 0.5 for any future claim", "reason": "avoid repeating 10.x/12.2 source artifact problem"},
        {"gate": "coverage", "budget": "must report no-op rows and guard-blocked rows separately", "reason": "prove fallback contract"},
        {"gate": "heldout_hard", "budget": "not used for selection; validation only after frozen candidate and explicit go", "reason": "preserve split policy"},
    ]
    stop_conditions = [
        {"condition": "param parser cannot reliably compare values", "action": "stop; keep as diagnostic artifact"},
        {"condition": "guards affect rows outside same-family numeric/spec tier", "action": "stop and narrow guard"},
        {"condition": "dev/OOF what-if creates any unexplained loss", "action": "stop; no implementation"},
        {"condition": "gain comes mainly from one source/province/family", "action": "hold for source robustness review"},
        {"condition": "requires threshold or GoalSearcher wiring", "action": "open separate explicit gate"},
    ]
    acceptance_checks = [
        {"check": "exact_file_boundary", "required": "future plan must name code files before implementation go"},
        {"check": "unit_tests", "required": "future implementation must include positive numeric-tier and negative cross-family/no-param tests"},
        {"check": "dev_oof_artifacts", "required": "future what-if must produce scorecard, loss audit, guard coverage, and row details"},
        {"check": "rollback", "required": "single branch/flag rollback documented"},
    ]

    plan_decision = "define_future_dev_oof_whatif_plan_request_go"
    metrics = {
        "plan_decision": plan_decision,
        "plan_rows": len(row_plan),
        "param_types": len(param_summary),
        "implementation_allowed_now": False,
        "training_allowed_now": False,
        "threshold_change_allowed_now": False,
        "goal_searcher_change_allowed_now": False,
        "future_whatif_allowed": False,
        "explicit_go_required_for_whatif": True,
        "heldout_hard_used": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "row_plan_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_row_plan.csv")),
        "param_summary_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_param_summary.csv")),
        "feature_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_feature_contract.csv")),
        "command_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_command_contract.csv")),
        "loss_budget_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_loss_budget.csv")),
        "stop_conditions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")),
        "acceptance_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_acceptance_checks.csv")),
    }
    decision = (
        "Define a future dev/OOF-only what-if plan for a tightly guarded same-family numeric/spec tier comparator. "
        "The 9-row evidence base is too small and heterogeneous for immediate implementation, but sufficient to request "
        "a future authorization gate. No what-if or code change is authorized in 12.4."
    )
    report = {
        "stage": "Goal LTR v1 / 12.4 numeric/spec tier rank-position minimal plan definition",
        "read_only": True,
        "source_artifacts": {
            "audit_summary": str(args.audit_summary),
            "row_audit": str(args.row_audit),
        },
        "metrics": metrics,
        "decision": decision,
        "audit_context": {
            "recommended_lane": audit_summary["metrics"]["recommended_lane"],
            "plan_candidate_rows": audit_summary["metrics"]["plan_candidate_rows"],
        },
        "anti_drift_conclusion": (
            "12.4 is read-only. It defines a future what-if plan only; it does not implement, train, tune, change thresholds, "
            "edit taxonomy rows, edit feature whitelists, reopen 11.x, wire GoalSearcher, or use heldout/hard for selection."
        ),
        "next_stage": {
            "stage": "12.5 numeric/spec tier offline what-if authorization gate",
            "default": "do_not_execute_without_explicit_go",
            "required_user_text": "go: run 12.6 dev/OOF-only numeric/spec tier what-if",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["row_plan_csv"]), row_plan, list(row_plan[0].keys()) if row_plan else ["group_id"])
    _write_csv(Path(artifacts["param_summary_csv"]), param_summary, list(param_summary[0].keys()) if param_summary else ["param_type"])
    _write_csv(Path(artifacts["feature_contract_csv"]), feature_contract, list(feature_contract[0].keys()))
    _write_csv(Path(artifacts["command_contract_csv"]), command_contract, list(command_contract[0].keys()))
    _write_csv(Path(artifacts["loss_budget_csv"]), loss_budget, list(loss_budget[0].keys()))
    _write_csv(Path(artifacts["stop_conditions_csv"]), stop_conditions, list(stop_conditions[0].keys()))
    _write_csv(Path(artifacts["acceptance_checks_csv"]), acceptance_checks, list(acceptance_checks[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
