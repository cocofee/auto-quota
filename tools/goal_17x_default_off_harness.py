from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_SHADOW_PREFIX = AGENT_STATE / "goal_17x_default_off_harness_dev_oof_shadow"
DEFAULT_SUMMARY = AGENT_STATE / "goal_17x_default_off_harness_implementation_summary.json"
DEFAULT_REPORT = AGENT_STATE / "goal_17x_default_off_harness_implementation_summary.md"

HARNESS_CONTRACT = {
    "enabled_by_default": False,
    "index_path": "data/goal_search/oss_recall_index_17x_multifield.jsonl",
    "top_k": 3,
    "min_support": 2,
    "min_source_families": 1,
    "min_overlap": 2,
    "intervention_mode": "broad",
    "core_families": ("concrete", "pipe", "pump", "rebar", "support"),
}


def _run_shadow(output_prefix: Path, progress_every: int) -> dict[str, Any]:
    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "tools" / "goal_17x_top3_guarded_shadow.py"),
        "--output-prefix",
        str(output_prefix),
        "--progress-every",
        str(progress_every),
    ]
    completed = subprocess.run(cmd, cwd=PROJECT_ROOT, check=True, text=True, capture_output=True)
    summary_path = output_prefix.with_name(output_prefix.name + "_summary.json")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    return {
        "command": " ".join(cmd),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "summary_path": str(summary_path),
        "summary": summary,
    }


def _gate_status(summary: dict[str, Any]) -> list[dict[str, str]]:
    headline = summary["headline"]
    gates = [
        ("default_off_contract", HARNESS_CONTRACT["enabled_by_default"] is False, "HARNESS_CONTRACT.enabled_by_default=false"),
        ("top1_loss_guard", int(headline.get("top1_losses", 0)) == 0, f"top1_losses={headline.get('top1_losses')}"),
        ("top80_loss_guard", int(headline.get("top80_losses", 0)) == 0, f"top80_losses={headline.get('top80_losses')}"),
        ("movement_guard", int(headline.get("delta_top1", 0)) > 0 and int(headline.get("delta_top5", 0)) > 0, f"delta_top1={headline.get('delta_top1')}; delta_top5={headline.get('delta_top5')}"),
        ("false_rate_guard", float(headline.get("prior_false_candidate_rate", 1)) < 0.85, f"false_rate={headline.get('prior_false_candidate_rate')}"),
        ("no_heldout_hard", not bool(summary.get("heldout_hard_used")), "heldout_hard_used=false"),
        ("online_default_unchanged", not bool(summary.get("online_default_changed")), "online_default_changed=false"),
    ]
    return [
        {"gate": gate, "status": "pass" if passed else "fail", "evidence": evidence}
        for gate, passed, evidence in gates
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["shadow_headline"]
    lines = [
        "# 17.6 Guarded OSS Multifield Default-Off Harness Implementation",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Contract",
        "",
        f"- default enabled: `{report['harness_contract']['enabled_by_default']}`",
        f"- index: `{report['harness_contract']['index_path']}`",
        f"- TopK/support/source/overlap: `{report['harness_contract']['top_k']}/{report['harness_contract']['min_support']}/{report['harness_contract']['min_source_families']}/{report['harness_contract']['min_overlap']}`",
        f"- mode/families: `{report['harness_contract']['intervention_mode']}` / `{','.join(report['harness_contract']['core_families'])}`",
        "",
        "## Dev/OOF Shadow",
        "",
        f"- rows: `{report['rows_evaluated']}`",
        f"- movement Top1/Top5/Top20/Top80: `{h['delta_top1']}/{h['delta_top5']}/{h['delta_top20']}/{h['delta_top80']}`",
        f"- losses Top1/Top80: `{h['top1_losses']}/{h['top80_losses']}`",
        f"- candidates generated/positive/false: `{h['prior_generated_candidates']}/{h['prior_positive_candidates']}/{h['prior_false_candidates']}`",
        f"- false rate: `{h['prior_false_candidate_rate']}`",
        "",
        "## Gates",
        "",
        "| gate | status | evidence |",
        "|---|---|---|",
    ]
    for gate in report["gates"]:
        lines.append(f"| {gate['gate']} | {gate['status']} | {gate['evidence']} |")
    lines.extend(["", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="17.6 default-off harness for the 17.x Top3 OSS multifield prior")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_SHADOW_PREFIX)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    shadow = _run_shadow(args.output_prefix, args.progress_every)
    shadow_summary = shadow["summary"]
    gates = _gate_status(shadow_summary)
    decision = (
        "default_off_harness_implemented_and_dev_oof_verified"
        if all(row["status"] == "pass" for row in gates)
        else "default_off_harness_implemented_but_gate_failed"
    )
    report = {
        "stage": "17.6 guarded OSS multifield default-off harness implementation",
        "decision": decision,
        "harness_contract": HARNESS_CONTRACT,
        "shadow_command": shadow["command"],
        "shadow_summary_path": shadow["summary_path"],
        "rows_evaluated": shadow_summary["rows_evaluated"],
        "shadow_headline": shadow_summary["headline"],
        "gates": gates,
        "trained": False,
        "tuned": False,
        "heldout_hard_used": False,
        "online_default_changed": False,
        "goal_searcher_defaults_changed": False,
        "anti_drift_conclusion": (
            "17.6 implemented only an explicit default-off dev/OOF harness wrapper for the frozen 17.5 contract. "
            "It did not train, tune, run heldout/hard, default-enable OSS recall, change GoalSearcher defaults, or overwrite 16.x artifacts."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(args.report, report)
    print(json.dumps({"summary": str(args.summary), "decision": decision}, ensure_ascii=False, indent=2))
    return 0 if decision.endswith("verified") else 1


if __name__ == "__main__":
    raise SystemExit(main())
