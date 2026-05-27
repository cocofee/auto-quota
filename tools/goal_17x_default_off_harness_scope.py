from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_17_4_SUMMARY = AGENT_STATE / "goal_17x_top3_guarded_dev_oof_shadow_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_default_off_harness_scope"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["evidence_headline"]
    lines = [
        "# 17.5 Guarded OSS Multifield Default-Off Harness Scope",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Evidence Basis",
        "",
        f"- 17.4 rows evaluated: `{report['evidence_rows_evaluated']}` impacted dev/OOF rows.",
        f"- movement: Top1 `{h['delta_top1']}`, Top5 `{h['delta_top5']}`, Top20 `{h['delta_top20']}`, Top80 `{h['delta_top80']}`.",
        f"- loss: Top1 losses `{h['top1_losses']}`, Top80 losses `{h['top80_losses']}`.",
        f"- candidates: generated/positive/false `{h['prior_generated_candidates']}/{h['prior_positive_candidates']}/{h['prior_false_candidates']}`, false rate `{h['prior_false_candidate_rate']}`.",
        "",
        "## Implementation Boundary",
        "",
        "Future implementation, if explicitly approved, should only package the existing 17.x Top3 OSS recall prior behind an explicit default-off contract. It must not enable online behavior by default and must not change the 16.x safe defaults.",
        "",
        "## Config Contract",
        "",
        "| key | required future value | boundary |",
        "|---|---|---|",
    ]
    for row in report["config_contract"]:
        lines.append(f"| `{row['key']}` | `{row['required_value']}` | {row['boundary']} |")
    lines.extend(
        [
            "",
            "## Required Tests",
            "",
            "| check | command or assertion |",
            "|---|---|",
        ]
    )
    for row in report["acceptance_checks"]:
        lines.append(f"| {row['check']} | `{row['command_or_assertion']}` |")
    lines.extend(
        [
            "",
            "## Blocked Actions",
            "",
        ]
    )
    for row in report["blocked_actions"]:
        lines.append(f"- {row['action']}: {row['reason']}")
    lines.extend(
        [
            "",
            "## Anti-Drift",
            "",
            report["anti_drift_conclusion"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    summary = json.loads(DEFAULT_17_4_SUMMARY.read_text(encoding="utf-8"))
    headline = dict(summary["headline"])
    candidate = dict(summary["candidate"])

    code_scope = [
        {
            "file": "config.py",
            "allowed_change": "Add documentation or optional explicit preset wiring only if needed; keep OSS_RECALL_INDEX_ENABLED default false and keep current safe defaults unchanged.",
            "not_allowed": "Do not change default path, TopK, support thresholds, mode, core families, or enable flag to the 17.x broad setting.",
        },
        {
            "file": "src/goal_search/oss_recall_prior.py",
            "allowed_change": "Reuse existing default-off collector; only add audit metadata if implementation needs a clearer 17.x reason label.",
            "not_allowed": "Do not weaken conflict checks, province scoping, family scoping, overlap checks, or source support checks.",
        },
        {
            "file": "src/goal_search/searcher.py",
            "allowed_change": "Reuse existing _collect_oss_recall_priors path behind OSS_RECALL_INDEX_ENABLED; preserve audit reason fields.",
            "not_allowed": "Do not add unconditional candidates or bypass local quota_id lookup.",
        },
        {
            "file": "tools/goal_17x_*",
            "allowed_change": "Add a dev/OOF-only default-off harness command that sets the 17.5 contract in-process and writes scorecard/loss audit artifacts.",
            "not_allowed": "Do not read heldout/hard, do not tune from validation, and do not overwrite 16.x locked artifacts.",
        },
        {
            "file": "tests/test_goal_17x_*.py",
            "allowed_change": "Add regression tests for default-off behavior, explicit 17.x Top3 contract, no foreign quota emission, and audit metadata.",
            "not_allowed": "Do not make tests depend on heldout/hard or the full 14GB OSS XML corpus.",
        },
    ]
    config_contract = [
        {"key": "OSS_RECALL_INDEX_ENABLED", "required_value": "false by default", "boundary": "Only explicit test/harness env or in-process config may turn it on."},
        {"key": "OSS_RECALL_INDEX_PATH", "required_value": "data/goal_search/oss_recall_index_17x_multifield.jsonl", "boundary": "17.x harness only; do not replace the 16.x default path."},
        {"key": "OSS_RECALL_INDEX_TOP_K", "required_value": str(candidate["top_k"]), "boundary": "Top3 candidate budget selected by 17.3/17.4."},
        {"key": "OSS_RECALL_INDEX_MIN_SUPPORT", "required_value": str(candidate["min_support"]), "boundary": "Must remain at least the 17.4 value."},
        {"key": "OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES", "required_value": str(candidate["min_source_families"]), "boundary": "Must remain explicit and audited per row."},
        {"key": "OSS_RECALL_INDEX_MIN_OVERLAP", "required_value": str(candidate["min_overlap"]), "boundary": "Do not lower without a new dev/OOF guard redesign."},
        {"key": "OSS_RECALL_INDEX_INTERVENTION_MODE", "required_value": candidate["intervention_mode"], "boundary": "Broad mode is allowed only inside the default-off 17.x harness."},
        {"key": "OSS_RECALL_INDEX_CORE_FAMILIES", "required_value": ",".join(candidate["core_families"]), "boundary": "Do not expand families in the implementation package."},
    ]
    acceptance_checks = [
        {"check": "syntax", "command_or_assertion": "python -m py_compile tools\\goal_17x_default_off_harness_scope.py tools\\goal_17x_top3_guarded_shadow.py"},
        {"check": "default-off regression", "command_or_assertion": "OSS_RECALL_INDEX_ENABLED=false yields zero OSS recall candidates"},
        {"check": "explicit 17.x contract", "command_or_assertion": "TopK=3, broad mode, 17.x index path, concrete/pipe/pump/rebar/support only"},
        {"check": "dev/OOF shadow replay", "command_or_assertion": "python tools\\goal_17x_top3_guarded_shadow.py"},
        {"check": "loss budget", "command_or_assertion": "top1_losses == 0 and top80_losses == 0"},
        {"check": "candidate risk budget", "command_or_assertion": "prior_false_candidate_rate < 0.85"},
        {"check": "movement budget", "command_or_assertion": "delta_top1 > 0 and delta_top5 > 0"},
        {"check": "no heldout/hard", "command_or_assertion": "implementation artifacts must show heldout_hard_used=false"},
    ]
    blocked_actions = [
        {"action": "default-enable 17.x broad OSS recall", "reason": "17.4 is dev/OOF shadow only and still has non-trivial false candidate risk."},
        {"action": "run heldout/hard for design or tuning", "reason": "heldout/hard may only be used after a separate validation go, not for scope definition."},
        {"action": "overwrite 16.x support exact-name artifact or defaults", "reason": "16.x remains a parked default-off package with different safety assumptions."},
        {"action": "emit foreign province quota IDs", "reason": "OSS evidence may guide local candidates; output must remain local quota.db quota_id only."},
        {"action": "expand beyond the 17.4 family list or lower guards", "reason": "any expansion requires a new dev/OOF precision-guard redesign."},
    ]
    required_outputs = [
        {"artifact": "summary_json", "path": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json"))},
        {"artifact": "summary_md", "path": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md"))},
        {"artifact": "code_scope_csv", "path": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_code_scope.csv"))},
        {"artifact": "config_contract_csv", "path": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_config_contract.csv"))},
        {"artifact": "acceptance_checks_csv", "path": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_acceptance_checks.csv"))},
        {"artifact": "blocked_actions_csv", "path": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_blocked_actions.csv"))},
    ]
    report = {
        "stage": "17.5 guarded OSS multifield default-off harness scope",
        "decision": "conditional_go_request_explicit_implementation_for_default_off_harness",
        "evidence_source": str(DEFAULT_17_4_SUMMARY),
        "evidence_rows_evaluated": int(summary["rows_evaluated"]),
        "evidence_headline": headline,
        "candidate_contract": candidate,
        "code_scope": code_scope,
        "config_contract": config_contract,
        "acceptance_checks": acceptance_checks,
        "blocked_actions": blocked_actions,
        "required_outputs": required_outputs,
        "next_recommended_stage": "17.6 explicit implementation go/no-go for guarded OSS multifield default-off harness",
        "implementation_allowed_now": False,
        "training_allowed": False,
        "heldout_hard_allowed": False,
        "online_default_enable_allowed": False,
        "anti_drift_conclusion": (
            "17.5 only defined the future default-off harness scope from 17.4 dev/OOF evidence. "
            "It did not train, tune, run heldout/hard, implement the harness, enable online behavior, change GoalSearcher defaults, or overwrite 16.x artifacts."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

    summary_json = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")
    summary_md = DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_code_scope.csv"), code_scope, ["file", "allowed_change", "not_allowed"])
    _write_csv(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_config_contract.csv"), config_contract, ["key", "required_value", "boundary"])
    _write_csv(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_acceptance_checks.csv"), acceptance_checks, ["check", "command_or_assertion"])
    _write_csv(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_blocked_actions.csv"), blocked_actions, ["action", "reason"])
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
