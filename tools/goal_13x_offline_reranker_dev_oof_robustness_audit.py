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

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_EXPERIMENT_PREFIX = AGENT_STATE / "goal_13x_offline_reranker_dev_oof_experiment"
DEFAULT_S8_SOURCE_FILE_REGISTRY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_source_file_registry.csv"
DEFAULT_GENERATED_EXCLUSIONS = AGENT_STATE / "goal_10x_dq_source_provenance_owner_acceptance_review_accepted_generated_exclusions.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_offline_reranker_dev_oof_robustness_audit"


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


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None):
            return default
        return int(float(value))
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
        lines.append("| " + " | ".join(str(item).replace("|", "/") for item in row) + " |")
    return "\n".join(lines)


def _safe_rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return path


def _write_markdown(path: Path, report: dict[str, Any], robust_rows: list[dict[str, Any]], stop_rows: list[dict[str, Any]]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 13.1 Offline Reranker Dev/OOF Robustness Audit",
        "",
        "Executed dev/OOF-only offline reranker experiment, then audited whether raw gains survive non-global-repair, accepted OSS, and S8 independent source-family gates.",
        "",
        "## Metrics",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["raw_best_candidate_id", metrics["raw_best_candidate_id"]],
                ["raw_best_hit1_net", metrics["raw_best_hit1_net"]],
                ["raw_approval_candidate_count", metrics["raw_approval_candidate_count"]],
                ["robust_pass_candidate_count", metrics["robust_pass_candidate_count"]],
                ["best_robust_candidate_id", metrics["best_robust_candidate_id"]],
                ["best_accepted_oss_positive_net", metrics["best_accepted_oss_positive_net"]],
                ["best_positive_independent_source_family_count", metrics["best_positive_independent_source_family_count"]],
                ["decision", metrics["decision"]],
            ]
        ),
        "",
        "## Top Robust Rows",
        "",
        _md_table(
            [
                [
                    "candidate_id",
                    "raw_net",
                    "non_global_net",
                    "accepted_oss_net",
                    "independent_families",
                    "top_positive_source_share",
                    "decision",
                ]
            ]
            + [
                [
                    row["candidate_id"],
                    row["raw_hit1_net"],
                    row["non_global_net"],
                    row["accepted_oss_net"],
                    row["positive_independent_source_family_count"],
                    row["top_positive_source_share"],
                    row["decision"],
                ]
                for row in robust_rows[:10]
            ]
        ),
        "",
        "## Stop Checks",
        "",
        _md_table([["condition", "status", "evidence", "effect"]] + [[row["condition"], row["status"], row["evidence"], row["effect"]] for row in stop_rows]),
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


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前状态：13.1 offline reranker dev/OOF experiment 已执行，并完成 robustness audit。"
        f" raw_best_hit1_net={metrics['raw_best_hit1_net']}；raw_approval_candidate_count={metrics['raw_approval_candidate_count']}；"
        f"robust_pass_candidate_count={metrics['robust_pass_candidate_count']}；"
        f"best_accepted_oss_positive_net={metrics['best_accepted_oss_positive_net']}；"
        f"best_positive_independent_source_family_count={metrics['best_positive_independent_source_family_count']}；"
        "heldout_used_for_selection=false；hard_used_for_selection=false；online_integration=false；GoalSearcher_changed=false。\n"
        "结论：raw dev/OOF 有明显排序收益，但没有候选通过 accepted-OSS + S8 independent-source robustness gate，不能 freeze、不能上线、不能宣称通用 Top1 提升。\n"
        "下一步建议：13.2 offline reranker data/source redesign gate，改为设计可验证训练数据与 source split 方案；不要继续在当前 source-dominated scorecard 上做实现。\n"
        "禁止：使用 heldout/hard 做选择、上线、改 GoalSearcher、调线上阈值、扩展 feature whitelist、把 source-dominated dev/OOF gain 当作真实通用收益。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.1 offline reranker dev/OOF experiment" not in text:
        section = f"""
    <section>
      <h2>13.x Offline Reranker</h2>
      <div class="warnbox">
        <strong>13.1 已执行。</strong>
        dev/OOF raw best net={metrics['raw_best_hit1_net']}，但 robust_pass_candidate_count={metrics['robust_pass_candidate_count']}。
        当前不能 freeze/上线；下一步应转向 data/source redesign gate。
      </div>
      <table>
        <tbody>
          <tr>
            <td class="stage">13.1 offline reranker dev/OOF experiment</td>
            <td><span class="pill paused">stopped</span></td>
            <td>Dev/OOF-only reranker execution plus non-global, accepted OSS, and S8 source-family robustness audit.</td>
            <td>raw_best_hit1_net={metrics['raw_best_hit1_net']}; robust_pass_candidate_count={metrics['robust_pass_candidate_count']}; heldout/hard selection=false.</td>
            <td>Do not freeze or implement current candidate; design cleaner data/source split before the next execution.</td>
          </tr>
        </tbody>
      </table>
    </section>
"""
        text = text.replace("    <footer>", section + "\n    <footer>", 1)
    artifact_marker = "          <tr>\n            <td>12.19 no-active-lane pause / explicit-go intake gate</td>"
    if "13.1 offline reranker robustness audit summary" not in text and artifact_marker in text:
        artifact_rows = f"""          <tr>
            <td>13.1 offline reranker execution summary</td>
            <td>Dev/OOF-only offline reranker experiment outputs; no heldout/hard selection and no online integration.</td>
            <td><code>{_safe_rel(report['artifacts']['experiment_summary_json'])}</code></td>
          </tr>
          <tr>
            <td>13.1 offline reranker robustness audit summary</td>
            <td>Robust scorecard using non-global-repair, accepted OSS, source dominance, and S8 source-family gates.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
          <tr>
            <td>13.1 offline reranker robustness audit tables</td>
            <td>Robust candidate scorecard, source concentration, stop decisions, and blocked actions.</td>
            <td><code>{_safe_rel(report['artifacts']['robust_scorecard_csv'])}</code> / <code>{_safe_rel(report['artifacts']['source_concentration_csv'])}</code></td>
          </tr>
"""
        text = text.replace(artifact_marker, artifact_rows + artifact_marker, 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(
        r"Last updated: .*? Asia/Shanghai\.",
        f"Last updated: {stamp} Asia/Shanghai.",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit 13.1 dev/OOF offline reranker robustness")
    parser.add_argument("--experiment-prefix", default=str(DEFAULT_EXPERIMENT_PREFIX))
    parser.add_argument("--s8-source-file-registry", default=str(DEFAULT_S8_SOURCE_FILE_REGISTRY))
    parser.add_argument("--generated-exclusions", default=str(DEFAULT_GENERATED_EXCLUSIONS))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    exp_prefix = Path(args.experiment_prefix)
    summary_path = exp_prefix.with_name(exp_prefix.name + "_execution_summary.json")
    scorecard_path = exp_prefix.with_name(exp_prefix.name + "_candidate_scorecard.csv")
    loss_path = exp_prefix.with_name(exp_prefix.name + "_loss_audit_by_slice.csv")
    summary = _read_json(summary_path)
    scorecard = _read_csv(scorecard_path)
    loss_audit = _read_csv(loss_path)
    source_registry = _read_csv(Path(args.s8_source_file_registry))
    generated_exclusions = _read_csv(Path(args.generated_exclusions)) if Path(args.generated_exclusions).exists() else []

    accepted_sources = {row["source_file"]: row for row in source_registry}
    source_to_family = {row["source_file"]: row["source_family_id"] for row in source_registry}
    generated_sources = {row.get("source_file", "") for row in generated_exclusions if row.get("source_file")}
    generated_sources.add("global_repair_decision_table.csv")

    by_candidate_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_candidate_family: dict[tuple[str, str], dict[str, Any]] = {}
    for row in loss_audit:
        if row.get("slice_dimension") != "source_file":
            continue
        candidate_id = row["candidate_id"]
        source_file = row["slice_key"]
        gain = _int(row["gain"])
        loss = _int(row["loss"])
        net = _int(row["net"])
        effect = {
            "candidate_id": candidate_id,
            "source_file": source_file,
            "groups": _int(row["groups"]),
            "gain": gain,
            "loss": loss,
            "net": net,
            "positive_net": max(net, 0),
            "is_global_repair": source_file == "global_repair_decision_table.csv",
            "is_generated_or_excluded": source_file in generated_sources,
            "is_accepted_oss": source_file in accepted_sources,
        }
        by_candidate_source[candidate_id].append(effect)
        if source_file in accepted_sources:
            family_id = source_to_family[source_file]
            key = (candidate_id, family_id)
            if key not in by_candidate_family:
                by_candidate_family[key] = {
                    "candidate_id": candidate_id,
                    "source_family_id": family_id,
                    "source_family": accepted_sources[source_file].get("source_family", ""),
                    "gain": 0,
                    "loss": 0,
                    "net": 0,
                    "source_files": [],
                }
            acc = by_candidate_family[key]
            acc["gain"] += gain
            acc["loss"] += loss
            acc["net"] += net
            acc["source_files"].append(source_file)

    positive_families: defaultdict[str, set[str]] = defaultdict(set)
    for row in by_candidate_family.values():
        if _int(row["net"]) > 0:
            positive_families[row["candidate_id"]].add(row["source_family_id"])

    robust_rows: list[dict[str, Any]] = []
    concentration_rows: list[dict[str, Any]] = []
    for row in scorecard:
        candidate_id = row["candidate_id"]
        effects = by_candidate_source[candidate_id]
        raw_gain = _int(row["hit1_gain"])
        raw_loss = _int(row["hit1_loss"])
        raw_net = _int(row["hit1_net"])
        non_global_gain = sum(_int(e["gain"]) for e in effects if not e["is_global_repair"])
        non_global_loss = sum(_int(e["loss"]) for e in effects if not e["is_global_repair"])
        non_global_net = non_global_gain - non_global_loss
        accepted_gain = sum(_int(e["gain"]) for e in effects if e["is_accepted_oss"])
        accepted_loss = sum(_int(e["loss"]) for e in effects if e["is_accepted_oss"])
        accepted_net = accepted_gain - accepted_loss
        generated_positive_net = sum(_int(e["positive_net"]) for e in effects if e["is_generated_or_excluded"])
        total_positive_net = sum(_int(e["positive_net"]) for e in effects)
        top_source = max(effects, key=lambda e: (_int(e["positive_net"]), _int(e["gain"])), default={})
        top_positive_net = _int(top_source.get("positive_net"))
        top_share = round(top_positive_net / total_positive_net, 4) if total_positive_net else 0.0
        source_dominated = top_share >= 0.5 and total_positive_net > 0
        family_count = len(positive_families[candidate_id])
        robust_pass = (
            row.get("approval_status") == "pass_dev_oof_candidate"
            and _bool(row.get("loss_budget_pass"))
            and non_global_net > 0
            and accepted_net > 0
            and family_count >= 2
            and not source_dominated
            and generated_positive_net <= max(accepted_net, 0)
        )
        decision = "robust_candidate_ready_for_freeze_review" if robust_pass else "stop_no_robust_freeze_candidate"
        robust_rows.append(
            {
                "scorecard_rank": row.get("scorecard_rank", ""),
                "candidate_id": candidate_id,
                "approval_status": row.get("approval_status", ""),
                "loss_budget_pass": row.get("loss_budget_pass", ""),
                "raw_hit1_gain": raw_gain,
                "raw_hit1_loss": raw_loss,
                "raw_hit1_net": raw_net,
                "non_global_gain": non_global_gain,
                "non_global_loss": non_global_loss,
                "non_global_net": non_global_net,
                "accepted_oss_gain": accepted_gain,
                "accepted_oss_loss": accepted_loss,
                "accepted_oss_net": accepted_net,
                "accepted_oss_positive_net": max(accepted_net, 0),
                "positive_independent_source_family_count": family_count,
                "generated_positive_net": generated_positive_net,
                "top_positive_source_file": top_source.get("source_file", ""),
                "top_positive_source_net": top_positive_net,
                "total_positive_source_net": total_positive_net,
                "top_positive_source_share": top_share,
                "source_dominated": str(source_dominated).lower(),
                "passes_13x_robust_gate": str(robust_pass).lower(),
                "decision": decision,
            }
        )
        concentration_rows.append(
            {
                "candidate_id": candidate_id,
                "top_positive_source_file": top_source.get("source_file", ""),
                "top_positive_source_net": top_positive_net,
                "total_positive_source_net": total_positive_net,
                "top_positive_source_share": top_share,
                "generated_positive_net": generated_positive_net,
                "accepted_oss_positive_net": max(accepted_net, 0),
                "positive_independent_source_family_count": family_count,
            }
        )

    robust_rows.sort(
        key=lambda r: (
            r["passes_13x_robust_gate"] == "true",
            _int(r["accepted_oss_positive_net"]),
            _int(r["positive_independent_source_family_count"]),
            _int(r["non_global_net"]),
            -_int(r["scorecard_rank"]),
        ),
        reverse=True,
    )
    passing = [row for row in robust_rows if row["passes_13x_robust_gate"] == "true"]
    best = robust_rows[0]
    metrics_src = summary["metrics"]
    decision = "stop_no_robust_freeze_candidate" if not passing else "candidate_found_requires_freeze_review"
    stop_rows = [
        {
            "condition": "raw_dev_oof_gain_exists",
            "status": "passed" if _int(metrics_src["best_hit1_net"]) > 0 else "failed",
            "evidence": f"raw_best_hit1_net={metrics_src['best_hit1_net']}",
            "effect": "continue_to_robustness_audit",
        },
        {
            "condition": "accepted_oss_positive_net > 0",
            "status": "passed" if _int(best["accepted_oss_positive_net"]) > 0 else "triggered",
            "evidence": f"best_accepted_oss_positive_net={best['accepted_oss_positive_net']}",
            "effect": "stop_without_freeze" if _int(best["accepted_oss_positive_net"]) <= 0 else "continue",
        },
        {
            "condition": "independent_source_family_count >= 2",
            "status": "passed" if _int(best["positive_independent_source_family_count"]) >= 2 else "triggered",
            "evidence": f"best_positive_independent_source_family_count={best['positive_independent_source_family_count']}",
            "effect": "stop_without_freeze" if _int(best["positive_independent_source_family_count"]) < 2 else "continue",
        },
        {
            "condition": "no_source_dominated_candidate",
            "status": "triggered" if not passing else "not_triggered",
            "evidence": f"robust_pass_candidate_count={len(passing)}",
            "effect": "do_not_freeze_or_implement_current_reranker" if not passing else "freeze_review_required",
        },
    ]
    blocked_actions = [
        {
            "blocked_action": "freeze_current_reranker_candidate",
            "reason": "No candidate passed the 13.x accepted-OSS, S8 independent-source, loss-budget, and source-dominance gates.",
            "allowed_after": "future dev/OOF experiment with robust_pass_candidate_count > 0",
        },
        {
            "blocked_action": "use_heldout_or_hard_for_selection",
            "reason": "13.1 is dev/OOF-only and heldout/hard were explicitly outside selection.",
            "allowed_after": "never for selection",
        },
        {
            "blocked_action": "modify_goal_searcher_or_online_ranking",
            "reason": "Current evidence is not robust enough for implementation or online integration.",
            "allowed_after": "future validation and explicit implementation go",
        },
    ]
    artifacts = {
        "experiment_summary_json": str(summary_path),
        "experiment_scorecard_csv": str(scorecard_path),
        "summary_json": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_summary.json")),
        "summary_md": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_summary.md")),
        "robust_scorecard_csv": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_robust_scorecard.csv")),
        "source_concentration_csv": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_source_concentration.csv")),
        "stop_decision_csv": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_stop_decision.csv")),
        "blocked_actions_csv": str(Path(args.output_prefix).with_name(Path(args.output_prefix).name + "_blocked_actions.csv")),
    }
    report = {
        "stage": "13.1 offline reranker dev/OOF experiment + robustness audit",
        "dev_oof_only": True,
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "online_integration": False,
        "goal_searcher_changed": False,
        "metrics": {
            "raw_best_candidate_id": metrics_src["best_candidate_id"],
            "raw_best_hit1_net": metrics_src["best_hit1_net"],
            "raw_best_hit1_gain": metrics_src["best_hit1_gain"],
            "raw_best_hit1_loss": metrics_src["best_hit1_loss"],
            "raw_approval_candidate_count": metrics_src["approval_candidate_count"],
            "candidate_count": len(robust_rows),
            "robust_pass_candidate_count": len(passing),
            "best_robust_candidate_id": best["candidate_id"],
            "best_accepted_oss_positive_net": best["accepted_oss_positive_net"],
            "best_positive_independent_source_family_count": best["positive_independent_source_family_count"],
            "best_non_global_net": best["non_global_net"],
            "best_top_positive_source_share": best["top_positive_source_share"],
            "decision": decision,
            "elapsed_sec": round(time.perf_counter() - started, 3),
        },
        "artifacts": artifacts,
        "decision": (
            "13.1 produced real dev/OOF reranker movement, but it is not a freeze/release candidate. "
            "The raw best candidate has positive dev/OOF net, while the robust gate has zero passing candidates after requiring accepted OSS positive net, "
            "at least two S8 independent source families, loss-budget pass, and no single-source domination. "
            "Stop here and redesign the offline reranker data/source split before another execution."
        ),
        "anti_drift_conclusion": (
            "Heldout/hard were not used for selection, no online integration was done, GoalSearcher was not modified, thresholds were not changed, "
            "and the raw dev/OOF gain is not claimed as a general Top1 gain."
        ),
        "next_stage": {
            "recommended": "13.2 offline reranker data/source redesign gate",
            "goal": "Make the next reranker run less source-dominated by defining accepted OSS coverage, source-family split, and loss-budget requirements before execution.",
        },
    }

    _write_csv(Path(artifacts["robust_scorecard_csv"]), robust_rows, list(robust_rows[0].keys()))
    _write_csv(Path(artifacts["source_concentration_csv"]), concentration_rows, list(concentration_rows[0].keys()))
    _write_csv(Path(artifacts["stop_decision_csv"]), stop_rows, ["condition", "status", "evidence", "effect"])
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, ["blocked_action", "reason", "allowed_after"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, robust_rows, stop_rows)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": report["metrics"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
