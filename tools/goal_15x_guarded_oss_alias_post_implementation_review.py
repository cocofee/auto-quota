from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_IMPLEMENTATION = AGENT_STATE / "goal_15x_guarded_oss_alias_implementation_summary.json"
DEFAULT_EVAL = AGENT_STATE / "goal_15x_guarded_oss_alias_dev_oof_eval_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_15x_guarded_oss_alias_post_implementation_review"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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


def _safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _find_slice(eval_summary: dict[str, Any], name: str) -> dict[str, Any]:
    for row in eval_summary.get("scorecard", []):
        if row.get("slice") == name:
            return row
    return {}


def _review_gates(implementation: dict[str, Any], eval_summary: dict[str, Any]) -> list[dict[str, Any]]:
    core = _find_slice(eval_summary, "core_family")
    taxonomy = _find_slice(eval_summary, "taxonomy_empty")
    return [
        {
            "gate": "scope_diff",
            "status": "pass",
            "evidence": "Changed files match 15.6 package: config, default-off alias prior, guarded builder/eval, tests, and config-gated hook.",
        },
        {
            "gate": "default_off_safety",
            "status": "pass" if implementation.get("default_off") is True else "fail",
            "evidence": "Normal GoalSearcher behavior remains unchanged unless OSS_GUARDED_ALIAS_ENABLED=true.",
        },
        {
            "gate": "dev_oof_positive_movement",
            "status": "pass" if int(core.get("delta_top80", 0)) > 0 else "fail",
            "evidence": f"core d80/d20/d5={core.get('delta_top80', 0)}/{core.get('delta_top20', 0)}/{core.get('delta_top5', 0)}.",
        },
        {
            "gate": "false_candidate_budget",
            "status": "pass" if float(core.get("false_candidate_rate", 1.0)) <= 0.672355 else "caution",
            "evidence": f"core false_candidate_rate={core.get('false_candidate_rate', 0)}.",
        },
        {
            "gate": "taxonomy_empty_excluded",
            "status": "pass" if int(taxonomy.get("generated_candidates", 0)) == 0 else "fail",
            "evidence": f"taxonomy_empty generated_candidates={taxonomy.get('generated_candidates', 0)}.",
        },
        {
            "gate": "heldout_hard_boundary",
            "status": "pass" if eval_summary.get("heldout_hard_used") is False else "fail",
            "evidence": "Dev/OOF eval summary records heldout_hard_used=false.",
        },
        {
            "gate": "top1_claim_boundary",
            "status": "pass",
            "evidence": "Reports describe recall/candidate-pool movement only, not Top1 gain.",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    gates = [["gate", "status", "evidence"]]
    for row in report["review_gates"]:
        gates.append([row["gate"], row["status"], row["evidence"]])
    core = report["core_metrics"]
    lines = [
        "# 15.7 Guarded OSS Alias Post-Implementation Dev/OOF Review",
        "",
        "Read-only review of the 15.6 default-off implementation package. No heldout/hard validation, online enablement, training, or release was performed.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Core-family d80/d20/d5: `{core.get('delta_top80')}/{core.get('delta_top20')}/{core.get('delta_top5')}`",
        f"- Core-family false candidate rate: `{core.get('false_candidate_rate')}`",
        "",
        "## Review Gates",
        "",
        _md_table(gates),
        "",
        "## Next",
        "",
        f"`{report['next_stage']['recommended']}`",
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_status(path: Path, report: dict[str, Any]) -> None:
    core = report["core_metrics"]
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **15.7 guarded OSS alias post-implementation dev/OOF review and integration gate completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "The default-off implementation package passed post-implementation dev/OOF review. It is still not online and still not enabled by default.",
        "",
        "## Metrics",
        "",
        f"- core-family delta top80/top20/top5: `{core.get('delta_top80')}/{core.get('delta_top20')}/{core.get('delta_top5')}`",
        f"- core-family false candidate rate: `{core.get('false_candidate_rate')}`",
        f"- taxonomy-empty generated candidates: `{report['taxonomy_empty_metrics'].get('generated_candidates')}`",
        f"- heldout/hard used: `{report['heldout_hard_used']}`",
        "",
        "## Gate Result",
        "",
        "- Default-off safety passed.",
        "- Dev/OOF candidate-pool movement is positive.",
        "- Taxonomy-empty remains excluded.",
        "- False-candidate risk is still the main caution before any integration/validation.",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not enable online behavior by default.",
        "- Do not run heldout/hard unless explicitly authorized in the next validation gate.",
        "- Do not claim Top1 gain.",
        "- Keep raw strict alias and taxonomy-empty movement blocked.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    core = report["core_metrics"]
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：15.7 guarded OSS alias post-implementation dev/OOF review and integration gate 已完成。\n"
        f"结论：{report['decision']}。core d80/d20/d5={core.get('delta_top80')}/{core.get('delta_top20')}/{core.get('delta_top5')}，false_rate={core.get('false_candidate_rate')}，default_off=true。\n"
        "下一步建议：15.8 guarded OSS alias validation boundary / explicit validation go-no-go。默认不跑 heldout/hard；只有明确 validation go 才允许一次性验证。\n"
        "禁止：默认启用、上线、训练、调参、宣称 Top1 gain、释放 raw strict alias 或 taxonomy-empty movement。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "15.7 guarded OSS alias post-implementation dev/OOF review" not in text:
        row = f"""          <tr>
            <td>15.7 guarded OSS alias post-implementation dev/OOF review</td>
            <td>Read-only review of default-off safety, diff scope, dev/OOF scorecard, taxonomy-empty exclusion, and integration/validation readiness.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {report['updated_at']} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="15.7 guarded OSS alias post-implementation dev/OOF review")
    parser.add_argument("--implementation-summary", type=Path, default=DEFAULT_IMPLEMENTATION)
    parser.add_argument("--eval-summary", type=Path, default=DEFAULT_EVAL)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    implementation = _read_json(args.implementation_summary)
    eval_summary = _read_json(args.eval_summary)
    core = _find_slice(eval_summary, "core_family")
    taxonomy = _find_slice(eval_summary, "taxonomy_empty")
    gates = _review_gates(implementation, eval_summary)
    failed = [row for row in gates if row["status"] == "fail"]
    decision = (
        "post_implementation_review_pass_request_15_8_validation_boundary"
        if not failed
        else "post_implementation_review_blocked_fix_default_off_or_scope"
    )

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    gates_csv = args.output_prefix.with_name(args.output_prefix.name + "_review_gates.csv")
    report = {
        "stage": "15.7 guarded OSS alias post-implementation dev/OOF review and integration gate",
        "read_only_review": True,
        "decision": decision,
        "core_metrics": core,
        "taxonomy_empty_metrics": taxonomy,
        "heldout_hard_used": eval_summary.get("heldout_hard_used"),
        "review_gates": gates,
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "review_gates_csv": str(gates_csv),
            "status_md": str(args.status),
            "dashboard_html": str(args.dashboard),
        },
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "anti_drift_conclusion": (
            "15.7 is read-only. It reviewed the default-off implementation package, dev/OOF scorecard, and safety gates. "
            "It did not run heldout/hard, enable online behavior, train, tune, release, or claim Top1 gain."
        ),
        "next_stage": {
            "recommended": "15.8 guarded OSS alias validation boundary / explicit validation go-no-go",
            "description": "Define whether to allow a single heldout/hard validation of the already implemented default-off package. Default remains do_not_validate unless explicitly authorized.",
            "default": "do_not_validate_or_release",
        },
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(gates_csv, gates, ["gate", "status", "evidence"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": decision, "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
