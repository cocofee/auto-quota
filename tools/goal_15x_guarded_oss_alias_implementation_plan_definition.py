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
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_FREEZE = AGENT_STATE / "goal_15x_recall_shadow_freeze_gate_review_summary.json"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_15x_guarded_oss_alias_implementation_plan_definition"
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


def _code_touchpoints() -> list[dict[str, Any]]:
    return [
        {
            "file": "src/goal_search/oss_alias_prior.py",
            "change_type": "add",
            "purpose": "Default-off guarded OSS strict-alias source. Load a prebuilt alias index and return append-only quota ids when query/province/family/support guards pass.",
            "boundary": "No ranking override; candidate-source evidence only.",
        },
        {
            "file": "tools/goal_15x_build_guarded_oss_alias_index.py",
            "change_type": "add",
            "purpose": "Build dev/OOF-only guarded alias index from accepted OSS artifacts, excluding same fold/source file evidence for evaluation.",
            "boundary": "No heldout/hard input; no online side effects.",
        },
        {
            "file": "tools/goal_15x_guarded_oss_alias_dev_oof_eval.py",
            "change_type": "add",
            "purpose": "Run the frozen candidate as a reproducible dev/OOF shadow A/B and emit presence/loss/source audits.",
            "boundary": "Measurement only; no model training or threshold tuning.",
        },
        {
            "file": "config.py",
            "change_type": "add_default_off_flags",
            "purpose": "Add OSS_GUARDED_ALIAS_ENABLED=false, index path, top_k, core-family scope, min_support=2.",
            "boundary": "Default off means no behavior change unless explicitly enabled in offline command.",
        },
        {
            "file": "src/goal_search/searcher.py",
            "change_type": "future_optional_hook",
            "purpose": "If implementation go is granted, add a small hook beside existing experience/shadow/national priors to append guarded alias candidate indices.",
            "boundary": "Do not reorder/drop baseline candidates; hook remains config-gated and traceable.",
        },
        {
            "file": "tests/test_goal_15x_guarded_oss_alias_prior.py",
            "change_type": "add",
            "purpose": "Unit tests for normalization, core-family guard, support>=2 guard, taxonomy-empty exclusion, dedup, default-off behavior.",
            "boundary": "Local synthetic fixtures only.",
        },
    ]


def _implementation_contract(frozen: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "contract": "scope",
            "requirement": frozen["scope"],
            "acceptance_check": "Only concrete/rebar/pipe/pump/support rows are eligible; query_family_empty is excluded.",
        },
        {
            "contract": "strict_alias_key",
            "requirement": "normalized_query + province + query_family",
            "acceptance_check": "No query-only or cross-province matching in frozen implementation candidate.",
        },
        {
            "contract": "support_guard",
            "requirement": "support_count >= 2 after excluding same fold/source-file in dev/OOF evaluation",
            "acceptance_check": "Index rows expose support_count and supporting source families for audit.",
        },
        {
            "contract": "append_only_merge",
            "requirement": "Alias candidates can be appended as candidate-source evidence but cannot replace or demote baseline BM25/vector/GoalSearcher candidates.",
            "acceptance_check": "Baseline recall_topk_ids remain a prefix or separately preserved in output artifacts.",
        },
        {
            "contract": "score_authority",
            "requirement": "Candidate-source evidence only; no direct Top1 override.",
            "acceptance_check": "No code path returns alias candidate as final answer without normal scoring/ranking/validation.",
        },
        {
            "contract": "traceability",
            "requirement": "Every emitted candidate includes oss_alias_prior=true, alias_key, support_count, source_family_count, source_file_hashes.",
            "acceptance_check": "Loss audit can explain every moved group and false candidate.",
        },
    ]


def _artifact_manifest() -> list[dict[str, Any]]:
    return [
        {"artifact": "guarded_alias_index.jsonl", "owner": "15.6 implementation", "purpose": "Precomputed default-off alias index rows."},
        {"artifact": "index_build_manifest.json", "owner": "15.6 implementation", "purpose": "Input files, source counts, exclusion policy, provenance hash."},
        {"artifact": "dev_oof_presence_scorecard.json", "owner": "15.6 eval", "purpose": "Baseline vs alias-expanded top80/top20/top5 movement."},
        {"artifact": "dev_oof_false_candidate_audit.csv", "owner": "15.6 eval", "purpose": "False candidates by family/source/support bucket."},
        {"artifact": "dev_oof_source_robustness.csv", "owner": "15.6 eval", "purpose": "Movement by source_family/province/fold."},
        {"artifact": "dev_oof_loss_slices.csv", "owner": "15.6 eval", "purpose": "Blocked slices: taxonomy-empty, non-core family, high false-candidate slices."},
    ]


def _test_commands() -> list[dict[str, Any]]:
    return [
        {
            "stage": "compile",
            "command": "python -m py_compile src/goal_search/oss_alias_prior.py tools/goal_15x_build_guarded_oss_alias_index.py tools/goal_15x_guarded_oss_alias_dev_oof_eval.py",
            "expected": "All planned implementation files compile.",
        },
        {
            "stage": "unit",
            "command": "pytest tests/test_goal_15x_guarded_oss_alias_prior.py tests/test_goal_search.py",
            "expected": "Default-off behavior and existing GoalSearcher tests pass.",
        },
        {
            "stage": "index_build_dev_oof",
            "command": "python tools/goal_15x_build_guarded_oss_alias_index.py --dev-oof-only",
            "expected": "Build guarded_alias_index.jsonl and manifest without heldout/hard access.",
        },
        {
            "stage": "shadow_eval",
            "command": "python tools/goal_15x_guarded_oss_alias_dev_oof_eval.py --index data/goal_search/guarded_alias_index.jsonl",
            "expected": "Reproduce or exceed frozen planning movement without boundary breach.",
        },
    ]


def _acceptance_gates(frozen: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"gate": "dev_oof_only", "pass_condition": "No heldout/hard files opened by build/eval commands."},
        {"gate": "movement_floor", "pass_condition": f"Core-family delta_top80 >= {frozen['delta_top80']} or explained by stricter implementation guard."},
        {"gate": "top20_top5_floor", "pass_condition": f"Core-family delta_top20/top5 remain positive, target baseline {frozen['delta_top20']}/{frozen['delta_top5']}."},
        {"gate": "false_candidate_budget", "pass_condition": f"False candidate rate <= {frozen['false_candidate_rate']} or absolute generated-candidate count materially lower with positive movement."},
        {"gate": "source_robustness", "pass_condition": f"Max source_family share <= {max(0.4, float(frozen['source_family_max_share'])):.3f}."},
        {"gate": "default_off", "pass_condition": "Config default keeps alias source disabled in normal runs."},
        {"gate": "no_top1_claim", "pass_condition": "Report recall/candidate-pool movement only; no Top1 gain claim before later validation."},
    ]


def _rollback_plan() -> list[dict[str, Any]]:
    return [
        {"rollback_item": "config_switch", "action": "Set OSS_GUARDED_ALIAS_ENABLED=false; default already false."},
        {"rollback_item": "runtime_hook", "action": "Remove or bypass the single guarded alias hook in GoalSearcher/searcher candidate prior collection."},
        {"rollback_item": "data_artifact", "action": "Delete or ignore guarded_alias_index.jsonl; source code must tolerate missing index by returning no candidates."},
        {"rollback_item": "eval_outputs", "action": "Archive reports/agent_state/goal_15x_guarded_oss_alias_*; no production state depends on them."},
    ]


def _forbidden_actions() -> list[dict[str, Any]]:
    return [
        {"action": "do_not_enable_online_by_default", "reason": "15.5 is planning; future implementation must be default-off."},
        {"action": "do_not_use_heldout_hard", "reason": "Heldout/hard reserved for later explicit validation after freeze."},
        {"action": "do_not_release_raw_strict_alias", "reason": "15.4 blocked raw freeze due high false-candidate rate and taxonomy-empty dominance."},
        {"action": "do_not_include_taxonomy_empty", "reason": "Taxonomy-empty movement is diagnostic only."},
        {"action": "do_not_train_or_tune", "reason": "This lane is candidate generation, not model training."},
        {"action": "do_not_claim_top1_gain", "reason": "15.x evidence is candidate-pool movement until later ranking/validation stages."},
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    touch = [["file", "change", "purpose"]]
    for row in report["code_touchpoints"]:
        touch.append([row["file"], row["change_type"], row["purpose"]])
    gates = [["gate", "pass condition"]]
    for row in report["acceptance_gates"]:
        gates.append([row["gate"], row["pass_condition"]])
    lines = [
        "# 15.5 Guarded OSS Strict-Alias Implementation Plan Definition",
        "",
        "Read-only implementation plan. No code implementation, training, heldout/hard access, release, or GoalSearcher behavior change was performed.",
        "",
        "## Decision",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Planned candidate: `{report['frozen_planning_candidate']['candidate_id']}`",
        "",
        "## Code Touchpoints",
        "",
        _md_table(touch),
        "",
        "## Acceptance Gates",
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
    frozen = report["frozen_planning_candidate"]
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **15.5 guarded OSS strict-alias implementation plan definition completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "The frozen 15A guarded core strict-alias candidate now has a concrete implementation plan, but implementation is still blocked until explicit go.",
        "",
        "## Planned Candidate",
        "",
        f"- candidate: `{frozen['candidate_id']}`",
        f"- scope: `{frozen['scope']}`",
        f"- guard: `{frozen['guard']}`",
        f"- frozen dev/OOF movement: `{frozen['delta_top80']}/{frozen['delta_top20']}/{frozen['delta_top5']}`",
        "",
        "## Planned Code Boundary",
        "",
        "- Add default-off guarded OSS alias prior source.",
        "- Add offline index builder and dev/OOF eval harness.",
        "- Future hook must be append-only and traceable.",
        "- Normal GoalSearcher behavior must not change unless explicitly enabled.",
        "",
        "## Next Recommended Stage",
        "",
        f"**{report['next_stage']['recommended']}**",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not implement without explicit go.",
        "- Do not run heldout/hard.",
        "- Do not release raw strict alias or taxonomy-empty movement.",
        "- Do not claim Top1 gain from recall movement.",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    frozen = report["frozen_planning_candidate"]
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：15.5 guarded OSS strict-alias implementation plan definition 已完成。\n"
        f"结论：{report['decision']}。candidate={frozen['candidate_id']}，frozen d80/d20/d5={frozen['delta_top80']}/{frozen['delta_top20']}/{frozen['delta_top5']}。\n"
        "下一步建议：15.6 guarded OSS alias implementation authorization gate。默认不实现，除非明确 go。\n"
        "若 go，只允许实现 default-off OSS alias prior source、index builder、dev/OOF eval harness 和单测；不跑 heldout/hard、不上线、不启用线上默认行为、不训练、不宣称 Top1 gain。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "15.5 guarded OSS strict-alias implementation plan definition" not in text:
        row = f"""          <tr>
            <td>15.5 guarded OSS strict-alias implementation plan definition</td>
            <td>Read-only implementation plan for the frozen guarded OSS strict-alias candidate, including code touchpoints, contracts, tests, artifacts, rollback, and explicit go requirements.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {report['updated_at']} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="15.5 guarded OSS strict-alias implementation plan definition")
    parser.add_argument("--freeze-summary", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    freeze = _read_json(args.freeze_summary)
    frozen = dict(freeze["frozen_planning_candidate"])
    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    summary_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    artifacts = {
        "summary_json": str(summary_json),
        "summary_md": str(summary_md),
        "code_touchpoints_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_code_touchpoints.csv")),
        "implementation_contract_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_implementation_contract.csv")),
        "artifact_manifest_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_artifact_manifest.csv")),
        "test_commands_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_test_commands.csv")),
        "acceptance_gates_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_acceptance_gates.csv")),
        "rollback_plan_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_rollback_plan.csv")),
        "forbidden_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_forbidden_actions.csv")),
        "status_md": str(args.status),
        "dashboard_html": str(args.dashboard),
    }
    report = {
        "stage": "15.5 guarded OSS strict-alias implementation plan definition",
        "read_only_plan": True,
        "decision": "implementation_plan_ready_request_15_6_explicit_go_no_go",
        "frozen_planning_candidate": frozen,
        "code_touchpoints": _code_touchpoints(),
        "implementation_contract": _implementation_contract(frozen),
        "artifact_manifest": _artifact_manifest(),
        "test_commands": _test_commands(),
        "acceptance_gates": _acceptance_gates(frozen),
        "rollback_plan": _rollback_plan(),
        "forbidden_actions": _forbidden_actions(),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "artifacts": artifacts,
        "anti_drift_conclusion": (
            "15.5 is read-only. It did not implement code, train, tune, read heldout/hard, enable online behavior, "
            "change GoalSearcher runtime behavior, release raw strict alias, include taxonomy-empty movement, or claim Top1 gain."
        ),
        "next_stage": {
            "recommended": "15.6 guarded OSS alias implementation authorization gate",
            "description": (
                "Collect explicit go/no-go for implementing the default-off guarded OSS alias prior source, index builder, "
                "dev/OOF eval harness, and unit tests within the 15.5 scope."
            ),
            "default": "do_not_implement_without_explicit_go",
        },
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, report)
    _write_csv(Path(artifacts["code_touchpoints_csv"]), report["code_touchpoints"], ["file", "change_type", "purpose", "boundary"])
    _write_csv(Path(artifacts["implementation_contract_csv"]), report["implementation_contract"], ["contract", "requirement", "acceptance_check"])
    _write_csv(Path(artifacts["artifact_manifest_csv"]), report["artifact_manifest"], ["artifact", "owner", "purpose"])
    _write_csv(Path(artifacts["test_commands_csv"]), report["test_commands"], ["stage", "command", "expected"])
    _write_csv(Path(artifacts["acceptance_gates_csv"]), report["acceptance_gates"], ["gate", "pass_condition"])
    _write_csv(Path(artifacts["rollback_plan_csv"]), report["rollback_plan"], ["rollback_item", "action"])
    _write_csv(Path(artifacts["forbidden_actions_csv"]), report["forbidden_actions"], ["action", "reason"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": str(summary_json), "decision": report["decision"], "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
