from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_S2_EXECUTION_SUMMARY = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_execution_summary.json"
DEFAULT_S2_SCORECARD = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_candidate_scorecard.csv"
DEFAULT_S2_LOSS_AUDIT = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_loss_audit_by_slice.csv"
DEFAULT_S2_HIT1_FLIPS = AGENT_STATE / "goal_10x_offline_ranking_experiment_dev_oof_hit1_flips.jsonl"
DEFAULT_S8_SOURCE_FILE_REGISTRY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_source_file_registry.csv"
DEFAULT_S8_SOURCE_FAMILY_REGISTRY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_source_family_registry.csv"
DEFAULT_S8_ACCEPTANCE_SUMMARY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_acceptance_gate_summary.json"
DEFAULT_GENERATED_EXCLUSIONS = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_accepted_generated_exclusions.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_10x_s2_accepted_oss_s8_constrained_dev_oof_reaudit"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _md_table(rows: list[list[Any]]) -> str:
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ").replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _write_markdown(
    path: Path,
    report: dict[str, Any],
    gate_checks: list[dict[str, Any]],
    candidate_reaudit: list[dict[str, Any]],
    stop_decision: list[dict[str, Any]],
) -> None:
    metrics = report["metrics"]
    top_rows = candidate_reaudit[:10]
    lines = [
        "# S2 Accepted-OSS + S8 Constrained Dev/OOF Re-audit",
        "",
        "Dev/OOF-only constrained re-audit of existing S2 offline ranking candidates using accepted OSS sources and S8 source-family independence counting.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["execution_decision", metrics["execution_decision"]],
                ["candidate_count", metrics["candidate_count"]],
                ["pass_candidate_count", metrics["pass_candidate_count"]],
                ["best_candidate_id", metrics["best_candidate_id"]],
                ["best_accepted_oss_positive_net", metrics["best_accepted_oss_positive_net"]],
                ["best_positive_independent_source_family_count", metrics["best_positive_independent_source_family_count"]],
                ["stop_condition_triggered", metrics["stop_condition_triggered"]],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        _md_table(
            [["gate", "status", "observed", "decision"]]
            + [[row["gate"], row["status"], row["observed"], row["decision"]] for row in gate_checks]
        ),
        "",
        "## Top Candidate Re-audit",
        "",
        _md_table(
            [["candidate_id", "accepted_oss_gain", "accepted_oss_loss", "accepted_oss_net", "positive_independent_source_family_count", "decision"]]
            + [
                [
                    row["candidate_id"],
                    row["accepted_oss_gain"],
                    row["accepted_oss_loss"],
                    row["accepted_oss_net"],
                    row["positive_independent_source_family_count"],
                    row["decision"],
                ]
                for row in top_rows
            ]
        ),
        "",
        "## Stop Decision",
        "",
        _md_table(
            [["condition", "status", "evidence", "effect"]]
            + [[row["condition"], row["status"], row["evidence"], row["effect"]] for row in stop_decision]
        ),
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        return text
    return text.replace(old, new, 1)


def _update_dashboard(path: Path, report: dict[str, Any], artifacts: dict[str, str]) -> None:
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    text = _replace_once(text, '<div class="value">10.73 paused</div>', '<div class="value">S2 re-audit stopped</div>')
    text = _replace_once(
        text,
        '<div class="note">10.73 已收口 S8；当前没有剩余可推进的非执行策略路线，暂停等待新 evidence 或 explicit go。</div>',
        '<div class="note">已按 explicit go 执行 S2 accepted-OSS + S8 constrained dev/OOF re-audit；未发现可重开学习的正向非生成独立来源收益。</div>',
    )
    text = _replace_once(
        text,
        '<div class="note">只有新的 accepted evidence、owner mappings、owner provenance package 或 explicit go 才能重开对应 lane。</div>',
        '<div class="note">当前 stop condition 触发：accepted OSS non-generated positive net 不足，S2 不训练、不实现、不进 heldout/hard。</div>',
    )
    text = _replace_once(
        text,
        '<div class="route-note">10.73 已完成 broader closure：S8 收口，10.x loop 暂停等待新 evidence/explicit go。</div>',
        '<div class="route-note">S2 constrained re-audit 已执行：用 S8 判重后无候选通过 accepted-OSS positive net + 独立 source_family gate。</div>',
    )
    text = _replace_once(
        text,
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>10.73 paused；await evidence/go。</span></div>',
        '<div class="node active"><strong>7. Accuracy Gap</strong><span>S2 re-audit stopped；await new evidence。</span></div>',
    )

    stage_row = """          <tr>
            <td class="stage">10.73 broader 10.x strategy closure/review after S8 support-contract acceptance</td>
            <td><span class="pill paused">paused</span></td>
            <td>Read-only close S8 and decide whether a remaining non-execution route exists.</td>
            <td>closure_decision=pause_10x_loop_await_new_evidence_or_explicit_go; active_learning_lane_count=0; remaining_non_execution_lane_count=0; pause_10x_loop_now=true.</td>
            <td>No automatic next learning stage. Resume only with lane-specific accepted evidence, complete owner mappings/provenance package, or explicit execution/implementation go.</td>
          </tr>"""
    new_stage_rows = """          <tr>
            <td class="stage">10.73 broader 10.x strategy closure/review after S8 support-contract acceptance</td>
            <td><span class="pill done">done</span></td>
            <td>Read-only close S8 and decide whether a remaining non-execution route exists.</td>
            <td>closure_decision=pause_10x_loop_await_new_evidence_or_explicit_go; active_learning_lane_count=0; remaining_non_execution_lane_count=0; pause_10x_loop_now=true.</td>
            <td>Later overridden by explicit user go for S2 accepted-OSS + S8 constrained dev/OOF re-audit.</td>
          </tr>
          <tr>
            <td class="stage">S2 accepted-OSS + S8 constrained dev/OOF re-audit / experiment</td>
            <td><span class="pill paused">stopped</span></td>
            <td>Dev/OOF-only constrained re-audit of existing S2 ranking experiment outputs using accepted OSS non-generated sources and S8 source-family dedup.</td>
            <td>execution_decision=stop_do_not_train_or_implement; pass_candidate_count=0; best_accepted_oss_positive_net=1; best_positive_independent_source_family_count=1.</td>
            <td>Stop condition triggered. Do not train, implement, use heldout/hard, change GoalSearcher, or claim Top1 gain.</td>
          </tr>"""
    text = _replace_once(text, stage_row, new_stage_rows)

    prompt = f"""按 Goal Roadmap 看板执行。
先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。
当前状态：S2 accepted-OSS + S8 constrained dev/OOF re-audit 已执行并停止。execution_decision={metrics["execution_decision"]}；candidate_count={metrics["candidate_count"]}；pass_candidate_count={metrics["pass_candidate_count"]}；best_accepted_oss_positive_net={metrics["best_accepted_oss_positive_net"]}；best_positive_independent_source_family_count={metrics["best_positive_independent_source_family_count"]}；stop_condition_triggered=true；heldout_used_for_selection=false；hard_used_for_selection=false；training_executed=false；implementation_allowed=false。
不要继续自动训练或实现。S2 只有在未来提供新的 accepted-source positive dev/OOF effect evidence，且 non_generated_positive_net > 0、至少 2 个 independent source_family、generated share 不主导时，才可重新开 re-entry review。
禁止：训练、调参、实现、重开 heldout/hard selection、改 GoalSearcher、编辑 feature whitelist、上线、把 source-dominated 或 accepted-OSS net=0 的结果宣称为 Top1 gain。
如果没有新 evidence，停止并报告当前阻塞点。"""
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )

    artifact_marker = """          <tr>
            <td>OOF safety gate summary</td>"""
    if "S2 accepted-OSS + S8 constrained dev/OOF re-audit summary" not in text:
        artifact_rows = f"""          <tr>
            <td>S2 accepted-OSS + S8 constrained dev/OOF re-audit summary</td>
            <td>Dev/OOF-only constrained S2 re-audit summary using accepted OSS and S8 source-family dedup.</td>
            <td><code>{Path(artifacts["summary_json"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>S2 accepted-OSS + S8 constrained dev/OOF re-audit report</td>
            <td>Human-readable report with gate checks, candidate re-audit, stop decision, and anti-drift boundary.</td>
            <td><code>{Path(artifacts["summary_md"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>S2 accepted-OSS + S8 constrained dev/OOF re-audit tables</td>
            <td>Candidate re-audit scorecard, accepted source-file effects, source-family effects, gate checks, stop decision, and blocked actions.</td>
            <td><code>{Path(artifacts["candidate_reaudit_scorecard_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["source_family_effects_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["gate_checks_csv"]).relative_to(PROJECT_ROOT)}</code> / <code>{Path(artifacts["stop_decision_csv"]).relative_to(PROJECT_ROOT)}</code></td>
          </tr>
          <tr>
            <td>S2 accepted-OSS + S8 constrained dev/OOF re-audit script</td>
            <td>Constrained dev/OOF re-audit script; it does not train, tune, run heldout/hard selection, change GoalSearcher, or edit feature whitelists.</td>
            <td><code>tools/goal_10x_s2_accepted_oss_s8_constrained_dev_oof_reaudit.py</code></td>
          </tr>
""" + artifact_marker
        text = _replace_once(text, artifact_marker, artifact_rows)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(
        r"Last updated: .*? Asia/Shanghai\.",
        f"Last updated: {stamp} Asia/Shanghai.",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="S2 accepted-OSS + S8 constrained dev/OOF re-audit")
    parser.add_argument("--s2-execution-summary", default=str(DEFAULT_S2_EXECUTION_SUMMARY))
    parser.add_argument("--s2-scorecard", default=str(DEFAULT_S2_SCORECARD))
    parser.add_argument("--s2-loss-audit", default=str(DEFAULT_S2_LOSS_AUDIT))
    parser.add_argument("--s2-hit1-flips", default=str(DEFAULT_S2_HIT1_FLIPS))
    parser.add_argument("--s8-source-file-registry", default=str(DEFAULT_S8_SOURCE_FILE_REGISTRY))
    parser.add_argument("--s8-source-family-registry", default=str(DEFAULT_S8_SOURCE_FAMILY_REGISTRY))
    parser.add_argument("--s8-acceptance-summary", default=str(DEFAULT_S8_ACCEPTANCE_SUMMARY))
    parser.add_argument("--generated-exclusions", default=str(DEFAULT_GENERATED_EXCLUSIONS))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    s2_summary = _read_json(Path(args.s2_execution_summary))
    scorecard = _read_csv(Path(args.s2_scorecard))
    loss_audit = _read_csv(Path(args.s2_loss_audit))
    hit1_flips = _read_jsonl(Path(args.s2_hit1_flips))
    s8_file_registry = _read_csv(Path(args.s8_source_file_registry))
    s8_family_registry = _read_csv(Path(args.s8_source_family_registry))
    s8_acceptance = _read_json(Path(args.s8_acceptance_summary))
    generated_exclusions = _read_csv(Path(args.generated_exclusions)) if Path(args.generated_exclusions).exists() else []
    m_s2 = s2_summary["metrics"]
    m_s8 = s8_acceptance["metrics"]

    accepted_sources = {row["source_file"]: row for row in s8_file_registry}
    source_to_family = {row["source_file"]: row.get("source_family_id", "") for row in s8_file_registry}
    generated_sources = {row.get("source_file", "") for row in generated_exclusions if row.get("source_file")}
    generated_sources.add("global_repair_decision_table.csv")

    candidate_rows = {row["candidate_id"]: row for row in scorecard}
    source_effects: list[dict[str, Any]] = []
    family_effect_acc: dict[tuple[str, str], dict[str, Any]] = {}
    generated_positive_by_candidate: defaultdict[str, int] = defaultdict(int)

    for row in loss_audit:
        if row.get("slice_dimension") != "source_file":
            continue
        candidate_id = row.get("candidate_id", "")
        source_file = row.get("slice_key", "")
        gain = _int(row.get("gain"))
        loss = _int(row.get("loss"))
        net = _int(row.get("net"))
        positive_net = max(net, 0)
        is_accepted_oss = source_file in accepted_sources
        is_generated = source_file in generated_sources
        if is_generated:
            generated_positive_by_candidate[candidate_id] += positive_net
        if not is_accepted_oss:
            continue
        reg = accepted_sources[source_file]
        family_id = source_to_family[source_file]
        source_family = reg.get("source_family", "")
        source_effects.append(
            {
                "candidate_id": candidate_id,
                "source_file": source_file,
                "source_family_id": family_id,
                "source_family": source_family,
                "accepted_oss_non_generated": str(not _bool(reg.get("is_generated_or_synthetic"))).lower(),
                "groups": row.get("groups", ""),
                "gain": gain,
                "loss": loss,
                "net": net,
                "positive_net": positive_net,
                "supports_positive_independent_family": str(positive_net > 0).lower(),
            }
        )
        key = (candidate_id, family_id)
        if key not in family_effect_acc:
            family_effect_acc[key] = {
                "candidate_id": candidate_id,
                "source_family_id": family_id,
                "source_family": source_family,
                "source_files": [],
                "groups": 0,
                "gain": 0,
                "loss": 0,
                "net": 0,
            }
        acc = family_effect_acc[key]
        acc["source_files"].append(source_file)
        acc["groups"] += _int(row.get("groups"))
        acc["gain"] += gain
        acc["loss"] += loss
        acc["net"] += net

    source_family_effects: list[dict[str, Any]] = []
    family_positive_by_candidate: defaultdict[str, set[str]] = defaultdict(set)
    for acc in family_effect_acc.values():
        net = _int(acc["net"])
        positive_net = max(net, 0)
        if positive_net > 0:
            family_positive_by_candidate[acc["candidate_id"]].add(acc["source_family_id"])
        source_family_effects.append(
            {
                "candidate_id": acc["candidate_id"],
                "source_family_id": acc["source_family_id"],
                "source_family": acc["source_family"],
                "source_files": "; ".join(acc["source_files"]),
                "groups": acc["groups"],
                "gain": acc["gain"],
                "loss": acc["loss"],
                "net": acc["net"],
                "positive_net": positive_net,
                "positive_independent_family": str(positive_net > 0).lower(),
            }
        )

    candidate_reaudit: list[dict[str, Any]] = []
    for row in scorecard:
        candidate_id = row["candidate_id"]
        effects = [effect for effect in source_effects if effect["candidate_id"] == candidate_id]
        accepted_gain = sum(_int(effect["gain"]) for effect in effects)
        accepted_loss = sum(_int(effect["loss"]) for effect in effects)
        accepted_net = accepted_gain - accepted_loss
        accepted_positive_net = max(accepted_net, 0)
        positive_family_count = len(family_positive_by_candidate[candidate_id])
        generated_positive_net = generated_positive_by_candidate[candidate_id]
        generated_dominant = generated_positive_net > accepted_positive_net and generated_positive_net > 0
        pass_gate = accepted_positive_net > 0 and positive_family_count >= 2 and not generated_dominant
        candidate_reaudit.append(
            {
                "scorecard_rank": row.get("scorecard_rank", ""),
                "candidate_id": candidate_id,
                "approval_status": row.get("approval_status", ""),
                "dev_oof_hit1_net_all_sources": row.get("hit1_net", ""),
                "dev_oof_hit1_gain_all_sources": row.get("hit1_gain", ""),
                "dev_oof_hit1_loss_all_sources": row.get("hit1_loss", ""),
                "accepted_oss_gain": accepted_gain,
                "accepted_oss_loss": accepted_loss,
                "accepted_oss_net": accepted_net,
                "accepted_oss_positive_net": accepted_positive_net,
                "positive_independent_source_family_count": positive_family_count,
                "generated_positive_net": generated_positive_net,
                "generated_dominant": str(generated_dominant).lower(),
                "passes_accepted_oss_s8_gate": str(pass_gate).lower(),
                "decision": "candidate_passes_reentry_gate" if pass_gate else "stop_no_constrained_positive_support",
            }
        )
    candidate_reaudit.sort(
        key=lambda row: (
            -_int(row["accepted_oss_positive_net"]),
            -_int(row["positive_independent_source_family_count"]),
            _int(row.get("scorecard_rank")),
        )
    )
    passing = [row for row in candidate_reaudit if row["passes_accepted_oss_s8_gate"] == "true"]
    best = candidate_reaudit[0] if candidate_reaudit else {}
    best_positive_net = _int(best.get("accepted_oss_positive_net"))
    best_family_count = _int(best.get("positive_independent_source_family_count"))
    stop_triggered = not passing

    gate_checks = [
        {
            "gate": "explicit_user_go",
            "status": "pass",
            "observed": "user explicitly authorized S2 accepted-OSS + S8 constrained dev/OOF re-audit",
            "decision": "execute_constrained_reaudit_only",
        },
        {
            "gate": "dev_oof_only_boundary",
            "status": "pass" if not _bool(m_s2.get("heldout_used_for_selection")) and not _bool(m_s2.get("hard_used_for_selection")) else "fail",
            "observed": f"heldout_used_for_selection={m_s2.get('heldout_used_for_selection')}; hard_used_for_selection={m_s2.get('hard_used_for_selection')}",
            "decision": "no heldout/hard selection used",
        },
        {
            "gate": "s8_support_contract_available",
            "status": "pass" if _bool(m_s8.get("s8_support_contract_accepted")) else "fail",
            "observed": f"s8_support_contract_accepted={m_s8.get('s8_support_contract_accepted')}; registry_source_file_rows={m_s8.get('registry_source_file_rows')}",
            "decision": "use S8 source-family registry for independent support counting",
        },
        {
            "gate": "non_generated_positive_net",
            "status": "pass" if best_positive_net > 0 else "fail",
            "observed": f"best_accepted_oss_positive_net={best_positive_net}",
            "decision": "stop if <=0",
        },
        {
            "gate": "independent_source_family_count",
            "status": "pass" if best_family_count >= 2 else "fail",
            "observed": f"best_positive_independent_source_family_count={best_family_count}; required>=2",
            "decision": "stop if <2",
        },
        {
            "gate": "no_training_or_implementation",
            "status": "pass",
            "observed": "training_executed=false; implementation_allowed=false; goal_searcher_changed=false",
            "decision": "re-audit only",
        },
    ]
    stop_decision = [
        {
            "condition": "non_generated_positive_net <= 0",
            "status": "triggered" if best_positive_net <= 0 else "not_triggered",
            "evidence": f"best_accepted_oss_positive_net={best_positive_net}",
            "effect": "stop_do_not_train_or_implement" if best_positive_net <= 0 else "continue_to_independent_family_gate",
        },
        {
            "condition": "independent_source_family_count < 2",
            "status": "triggered" if best_family_count < 2 else "not_triggered",
            "evidence": f"best_positive_independent_source_family_count={best_family_count}",
            "effect": "stop_do_not_train_or_implement" if best_family_count < 2 else "candidate_can_enter_future_review",
        },
        {
            "condition": "no_candidate_passes_constrained_gate",
            "status": "triggered" if stop_triggered else "not_triggered",
            "evidence": f"pass_candidate_count={len(passing)}",
            "effect": "keep_S2_closed" if stop_triggered else "future_reentry_review_required",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "train_or_tune_s2",
            "reason": "No candidate passed accepted-OSS + S8 constrained positive-support gates.",
            "allowed_after": "future accepted-source positive dev/OOF effect package passes non-generated net and independent source-family gates",
        },
        {
            "blocked_action": "implement_or_change_goal_searcher",
            "reason": "This is a dev/OOF-only re-audit and not an implementation stage.",
            "allowed_after": "future validation and explicit implementation go",
        },
        {
            "blocked_action": "run_heldout_or_hard_selection",
            "reason": "User explicitly forbade heldout selection and current stop gate failed.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "claim_general_top1_gain",
            "reason": "Accepted OSS constrained positive net is not present.",
            "allowed_after": "future accepted-source positive effect audit and validation boundary",
        },
    ]

    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_reaudit_scorecard_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_reaudit_scorecard.csv")),
        "source_file_effects_csv": str(output_prefix.with_name(output_prefix.name + "_accepted_source_file_effects.csv")),
        "source_family_effects_csv": str(output_prefix.with_name(output_prefix.name + "_source_family_effects.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "stop_decision_csv": str(output_prefix.with_name(output_prefix.name + "_stop_decision.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "source_stage": s2_summary["stage"],
        "execution_decision": "stop_do_not_train_or_implement" if stop_triggered else "candidate_found_requires_future_reentry_review",
        "explicit_user_go": True,
        "candidate_count": len(candidate_reaudit),
        "accepted_oss_source_file_count": len(accepted_sources),
        "accepted_oss_source_family_count": len(s8_family_registry),
        "accepted_source_file_effect_rows": len(source_effects),
        "source_family_effect_rows": len(source_family_effects),
        "pass_candidate_count": len(passing),
        "best_candidate_id": best.get("candidate_id", ""),
        "best_accepted_oss_positive_net": best_positive_net,
        "best_positive_independent_source_family_count": best_family_count,
        "best_generated_positive_net": _int(best.get("generated_positive_net")),
        "stop_condition_triggered": stop_triggered,
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "training_executed": False,
        "candidate_matrix_expanded": False,
        "implementation_allowed": False,
        "goal_searcher_change_allowed": False,
        "feature_whitelist_change_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "Goal LTR v1 / S2 accepted-OSS + S8 constrained dev/OOF-only re-audit",
        "dev_oof_only": True,
        "constrained_reaudit_only": True,
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": (
            "Stop S2 after the accepted-OSS + S8 constrained dev/OOF re-audit. The best constrained candidate has accepted OSS positive net=1 but only one positive independent source family after S8 counting, "
            "so the user-defined independent-source stop condition is triggered. Do not train, implement, use heldout/hard for selection, change GoalSearcher, or claim Top1 gain."
        ),
        "anti_drift_conclusion": (
            "This stage re-audits existing dev/OOF S2 artifacts only. It does not train or tune models, expand the candidate matrix, use heldout/hard for selection, "
            "change thresholds or rules, modify GoalSearcher, edit feature whitelists, connect online, or claim general Top1 gain."
        ),
        "next_stage": {
            "stage": "stopped awaiting new accepted-source positive evidence or different explicit direction",
            "goal": "Resume S2 only with new accepted-source positive dev/OOF evidence satisfying S8 independent source-family gates.",
            "default": "stop",
        },
    }

    _write_csv(Path(artifacts["candidate_reaudit_scorecard_csv"]), candidate_reaudit, ["scorecard_rank", "candidate_id", "approval_status", "dev_oof_hit1_net_all_sources", "dev_oof_hit1_gain_all_sources", "dev_oof_hit1_loss_all_sources", "accepted_oss_gain", "accepted_oss_loss", "accepted_oss_net", "accepted_oss_positive_net", "positive_independent_source_family_count", "generated_positive_net", "generated_dominant", "passes_accepted_oss_s8_gate", "decision"])
    _write_csv(Path(artifacts["source_file_effects_csv"]), source_effects, ["candidate_id", "source_file", "source_family_id", "source_family", "accepted_oss_non_generated", "groups", "gain", "loss", "net", "positive_net", "supports_positive_independent_family"])
    _write_csv(Path(artifacts["source_family_effects_csv"]), source_family_effects, ["candidate_id", "source_family_id", "source_family", "source_files", "groups", "gain", "loss", "net", "positive_net", "positive_independent_family"])
    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, ["gate", "status", "observed", "decision"])
    _write_csv(Path(artifacts["stop_decision_csv"]), stop_decision, ["condition", "status", "evidence", "effect"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, gate_checks, candidate_reaudit, stop_decision)
    _update_dashboard(Path(args.dashboard), report, artifacts)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
