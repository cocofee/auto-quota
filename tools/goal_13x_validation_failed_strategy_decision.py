from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"

PACKAGE_REVIEW_JSON = AGENT_STATE / "goal_13x_conflict_guard_validation_package_review_summary.json"
FREEZE_REVIEW_CSV = AGENT_STATE / "goal_13x_top1_loss_guarded_freeze_gate_review_candidate_classification.csv"
DEV_OOF_SCORECARD_CSV = AGENT_STATE / "goal_13x_top1_loss_guarded_dev_oof_candidate_scorecard.csv"
DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"

OUTPUT_PREFIX = AGENT_STATE / "goal_13x_validation_failed_strategy_decision"


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    output = [
        "| " + " | ".join(str(cell) for cell in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        output.append("| " + " | ".join(str(cell).replace("|", "/") for cell in row) + " |")
    return "\n".join(output)


def by_candidate(rows: list[dict[str, str]], candidate_id: str) -> dict[str, str]:
    for row in rows:
        if row.get("candidate_id") == candidate_id:
            return row
    return {}


def safe_rel(path: Path) -> str:
    return str(path.resolve().relative_to(PROJECT_ROOT))


def build_report() -> dict[str, Any]:
    package = read_json(PACKAGE_REVIEW_JSON)
    freeze_rows = read_csv(FREEZE_REVIEW_CSV)
    scorecard_rows = read_csv(DEV_OOF_SCORECARD_CSV)

    heldout = package["heldout_metrics"]
    hard = package["hard_metrics"]
    t1g_a = by_candidate(scorecard_rows, "T1G_A_low_conf_margin_guard")
    t1g_b = by_candidate(scorecard_rows, "T1G_B_conflict_guard")
    t1g_a_classification = by_candidate(freeze_rows, "T1G_A_low_conf_margin_guard")
    t1g_b_classification = by_candidate(freeze_rows, "T1G_B_conflict_guard")

    decision_rows = [
        {
            "item": "release_T1G_B_conflict_guard",
            "decision": "reject",
            "evidence": (
                f"heldout_top1_net={heldout['hit1_net']}; "
                f"hard_top1_net={hard['hit1_net']}; "
                f"hard_rank1_loss={hard['rank1_loss_count']}"
            ),
            "reason": "Validation is neutral on heldout and negative on hard; Hit5 gains cannot override Top1 failure.",
            "next_action": "park conflict-only release path",
        },
        {
            "item": "continue_conflict_only_threshold_search",
            "decision": "reject",
            "evidence": "heldout/hard validation already consumed for fixed candidate evaluation only",
            "reason": "Using validation failures to retune the conflict gate would contaminate validation.",
            "next_action": "return to dev/OOF-only redesign",
        },
        {
            "item": "rewrite_T1G_A_observable_low_conf_margin_gate",
            "decision": "recommend",
            "evidence": (
                f"dev_oof_hit1_net={t1g_a.get('hit1_net')}; "
                f"gain/loss={t1g_a.get('hit1_gain')}/{t1g_a.get('hit1_loss')}; "
                f"rank1_loss={t1g_a.get('rank1_loss_count')}; "
                f"classification={t1g_a_classification.get('deployability')}"
            ),
            "reason": "The low-confidence/margin part is online-observable; the label-derived baseline_rank branch must be removed.",
            "next_action": "define 13.23 rewrite plan before any execution",
        },
        {
            "item": "expand_OSS_source_aware_training_again",
            "decision": "defer",
            "evidence": "13.14 global expanded reranker produced Hit5 support but failed Top1 validation",
            "reason": "More OSS volume is useful, but the immediate bottleneck is Top1-safe intervention policy, not raw data size alone.",
            "next_action": "revisit after observable Top1 guard variants are tested on dev/OOF",
        },
    ]

    rewrite_plan = [
        {
            "variant_id": "A1_low_conf_strict",
            "allowed_gate": "apply only when baseline top1 confidence is below dev/OOF-calibrated low-confidence cutoff",
            "removed_branch": "baseline_rank != 1",
            "rank1_protection": "do not demote confident baseline rank_1",
            "success_gate": "dev/OOF Top1 net > 0, rank1 loss within budget, no source/fold concentration",
        },
        {
            "variant_id": "A2_small_margin_strict",
            "allowed_gate": "apply only when top1-top2 score margin is below dev/OOF-calibrated small-margin cutoff",
            "removed_branch": "baseline_rank != 1",
            "rank1_protection": "do not demote confident wide-margin rank_1",
            "success_gate": "dev/OOF Top1 net > 0 and rank1 demotion rate <= 1%",
        },
        {
            "variant_id": "A3_low_conf_or_small_margin",
            "allowed_gate": "apply when either low confidence or small margin is observed",
            "removed_branch": "baseline_rank != 1",
            "rank1_protection": "preserve baseline rank_1 unless observable uncertainty is present",
            "success_gate": "higher recall of near misses while keeping rank1 losses small",
        },
        {
            "variant_id": "A4_low_conf_margin_plus_conflict",
            "allowed_gate": "apply when low confidence/small margin is present and challenger has observable conflict-resolution evidence",
            "removed_branch": "baseline_rank != 1",
            "rank1_protection": "strictest intervention gate for release-oriented candidate",
            "success_gate": "lower coverage is acceptable if Top1 net and robustness improve",
        },
    ]

    stop_conditions = [
        {
            "condition": "candidate uses baseline_rank, positive_rank, hit5-rescue label, expected_id, or answer position",
            "action": "stop as label-derived diagnostic, not deployable",
        },
        {
            "condition": "dev/OOF Top1 net <= 0 or rank1 loss exceeds budget",
            "action": "stop without freeze",
        },
        {
            "condition": "one source_family/province/fold dominates gains or losses",
            "action": "stop for source robustness review",
        },
        {
            "condition": "heldout/hard is requested before dev/OOF freeze",
            "action": "stop to protect validation boundary",
        },
    ]

    artifacts = {
        "summary_json": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.json")),
        "summary_md": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.md")),
        "decision_matrix_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_decision_matrix.csv")),
        "rewrite_plan_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_observable_t1g_a_rewrite_plan.csv")),
        "stop_conditions_csv": safe_rel(OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_stop_conditions.csv")),
    }

    return {
        "stage": "13.22 validation-neutral/failed strategy decision",
        "read_only_review": True,
        "decision": "park_T1G_B_conflict_guard_and_pivot_to_observable_T1G_A_rewrite",
        "conflict_guard_validation": {
            "heldout_hit1_net": heldout["hit1_net"],
            "heldout_hit1_gain": heldout["hit1_gain"],
            "heldout_hit1_loss": heldout["hit1_loss"],
            "heldout_hit5_net": heldout["hit5_net"],
            "hard_hit1_net": hard["hit1_net"],
            "hard_hit1_gain": hard["hit1_gain"],
            "hard_hit1_loss": hard["hit1_loss"],
            "hard_hit5_net": hard["hit5_net"],
            "hard_rank1_loss": hard["rank1_loss_count"],
        },
        "dev_oof_rewrite_signal": {
            "candidate_id": "T1G_A_low_conf_margin_guard",
            "hit1_gain": t1g_a.get("hit1_gain"),
            "hit1_loss": t1g_a.get("hit1_loss"),
            "hit1_net": t1g_a.get("hit1_net"),
            "rank1_loss_count": t1g_a.get("rank1_loss_count"),
            "applied_group_rate": t1g_a.get("applied_group_rate"),
            "deployability": t1g_a_classification.get("deployability"),
            "rewrite_reason": t1g_a_classification.get("deployability_reason"),
        },
        "conflict_guard_dev_oof_signal": {
            "candidate_id": "T1G_B_conflict_guard",
            "hit1_gain": t1g_b.get("hit1_gain"),
            "hit1_loss": t1g_b.get("hit1_loss"),
            "hit1_net": t1g_b.get("hit1_net"),
            "rank1_loss_count": t1g_b.get("rank1_loss_count"),
            "deployability": t1g_b_classification.get("deployability"),
        },
        "decision_rows": decision_rows,
        "rewrite_plan": rewrite_plan,
        "stop_conditions": stop_conditions,
        "next_stage": {
            "id": "13.23",
            "name": "observable low-confidence/margin gate rewrite plan",
            "recommended": (
                "13.23：只读定义 T1G_A observable low-confidence/margin gate rewrite，"
                "删除 label-derived baseline_rank != 1 分支，只保留线上可观测的低置信/小分差/弱分离介入逻辑；"
                "仍不执行、不训练、不用 heldout/hard。"
            ),
        },
        "forbidden_actions": [
            "do not release T1G_B conflict guard",
            "do not tune on heldout/hard",
            "do not use label-derived baseline_rank/positive_rank gates as deployable logic",
            "do not edit GoalSearcher",
            "do not claim Hit5 gain as Top1 gain",
        ],
        "anti_drift_conclusion": (
            "13.22 is read-only. It parks the validation-neutral/failed conflict guard and selects a dev/OOF-only "
            "observable T1G_A rewrite path. It does not train, tune thresholds, use heldout/hard for selection, "
            "release, edit GoalSearcher, or expand hint/rule logic."
        ),
        "artifacts": artifacts,
    }


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    validation = report["conflict_guard_validation"]
    rewrite = report["dev_oof_rewrite_signal"]
    lines = [
        "# 13.22 Validation-Neutral/Failed Strategy Decision",
        "",
        "Read-only decision after heldout/hard validation of the frozen T1G_B conflict guard.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Why T1G_B Stops",
        "",
        md_table(
            [
                ["split", "Top1 gain/loss/net", "Hit5 net", "rank1 loss", "release decision"],
                [
                    "heldout",
                    f"{validation['heldout_hit1_gain']}/{validation['heldout_hit1_loss']}/{validation['heldout_hit1_net']}",
                    validation["heldout_hit5_net"],
                    0,
                    "not positive",
                ],
                [
                    "hard",
                    f"{validation['hard_hit1_gain']}/{validation['hard_hit1_loss']}/{validation['hard_hit1_net']}",
                    validation["hard_hit5_net"],
                    validation["hard_rank1_loss"],
                    "negative",
                ],
            ]
        ),
        "",
        "T1G_B is safer than the global reranker, but validation did not prove Top1 gain. It is parked, not released.",
        "",
        "## Why Pivot To T1G_A Rewrite",
        "",
        md_table(
            [
                ["candidate", "dev/OOF gain/loss/net", "rank1 loss", "status", "rewrite need"],
                [
                    rewrite["candidate_id"],
                    f"{rewrite['hit1_gain']}/{rewrite['hit1_loss']}/{rewrite['hit1_net']}",
                    rewrite["rank1_loss_count"],
                    rewrite["deployability"],
                    rewrite["rewrite_reason"],
                ],
            ]
        ),
        "",
        "The promising part is observable low confidence / small margin. The unsafe part is the label-derived `baseline_rank != 1` branch, so the next plan must remove it.",
        "",
        "## Decision Matrix",
        "",
        md_table(
            [["item", "decision", "evidence", "next_action"]]
            + [[row["item"], row["decision"], row["evidence"], row["next_action"]] for row in report["decision_rows"]]
        ),
        "",
        "## Rewrite Plan Preview",
        "",
        md_table(
            [["variant_id", "allowed_gate", "removed_branch", "rank1_protection"]]
            + [
                [row["variant_id"], row["allowed_gate"], row["removed_branch"], row["rank1_protection"]]
                for row in report["rewrite_plan"]
            ]
        ),
        "",
        "## Next",
        "",
        report["next_stage"]["recommended"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.22 validation-neutral/failed strategy decision 已完成。\n"
        "结论：park_T1G_B_conflict_guard_and_pivot_to_observable_T1G_A_rewrite。T1G_B heldout Top1 net=0、hard Top1 net=-1，不能 release；Hit5 正信号只能作为诊断。\n"
        "下一步建议：13.23 observable low-confidence/margin gate rewrite plan。大胆转向 T1G_A 的线上可观测低置信/小分差介入逻辑，删除 label-derived baseline_rank != 1 分支；仍不执行、不训练、不用 heldout/hard。\n"
        "禁止：release T1G_B、改 GoalSearcher、用 heldout/hard 调参、使用 label-derived positive-rank gate、把 Hit5 正收益宣称为 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )

    row = f"""          <tr>
            <td>13.22 validation-neutral/failed strategy decision</td>
            <td>Read-only closure of failed/neutral T1G_B validation and pivot decision for observable T1G_A rewrite.</td>
            <td><code>{report['artifacts']['summary_json']}</code></td>
          </tr>
"""
    if "13.22 validation-neutral/failed strategy decision" not in text:
        insert_at = text.find("        </tbody>")
        if insert_at != -1:
            text = text[:insert_at] + row + text[insert_at:]

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    report = build_report()
    summary_json = OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.json")
    summary_md = OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_summary.md")
    decision_csv = OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_decision_matrix.csv")
    rewrite_csv = OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_observable_t1g_a_rewrite_plan.csv")
    stop_csv = OUTPUT_PREFIX.with_name(OUTPUT_PREFIX.name + "_stop_conditions.csv")

    write_json(summary_json, report)
    write_markdown(summary_md, report)
    write_csv(decision_csv, report["decision_rows"], ["item", "decision", "evidence", "reason", "next_action"])
    write_csv(
        rewrite_csv,
        report["rewrite_plan"],
        ["variant_id", "allowed_gate", "removed_branch", "rank1_protection", "success_gate"],
    )
    write_csv(stop_csv, report["stop_conditions"], ["condition", "action"])
    update_dashboard(DASHBOARD, report)

    print(json.dumps({"decision": report["decision"], "summary": safe_rel(summary_json)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
