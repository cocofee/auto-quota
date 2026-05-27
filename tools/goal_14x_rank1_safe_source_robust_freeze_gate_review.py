from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"

DEFAULT_SUMMARY = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_execution_summary.json"
DEFAULT_SCORECARD = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_candidate_scorecard.csv"
DEFAULT_LOSS_AUDIT = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_loss_audit_by_slice.csv"
DEFAULT_RANK1 = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_rank1_preservation_report.csv"
DEFAULT_GATE_COVERAGE = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_strong_challenger_gate_coverage.csv"
DEFAULT_SOURCE_ROBUSTNESS = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_source_fold_robustness.csv"
DEFAULT_TAXONOMY = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_taxonomy_empty_separate_audit.csv"
DEFAULT_LEAKAGE = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_leakage_gate_report.csv"
DEFAULT_FEATURE_IMPORTANCE = AGENT_STATE / "goal_14x_rank1_safe_source_robust_dev_oof_feature_importance.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_14x_rank1_safe_source_robust_freeze_gate"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_STATUS_MD = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"

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


def safe_rel(path: str | Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(cell) for cell in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(cell).replace("|", "/") for cell in row) + " |")
    return "\n".join(lines)


def to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def top_net_share(loss_rows: list[dict[str, str]], candidate_id: str, dimension: str) -> tuple[float, str, int, int, int]:
    rows = [
        row for row in loss_rows
        if row.get("candidate_id") == candidate_id
        and row.get("slice_dimension") == dimension
    ]
    positive = [row for row in rows if to_int(row.get("net")) > 0]
    total = sum(to_int(row.get("net")) for row in positive)
    if not positive or total <= 0:
        return 0.0, "", 0, 0, 0
    top = max(positive, key=lambda row: to_int(row.get("net")))
    return (
        round(to_int(top.get("net")) / total, 6),
        str(top.get("slice_key") or ""),
        to_int(top.get("gain")),
        to_int(top.get("loss")),
        to_int(top.get("net")),
    )


def source_robust_status(source_rows: list[dict[str, str]], candidate_id: str) -> dict[str, Any]:
    row = next((item for item in source_rows if item.get("candidate_id") == candidate_id), {})
    return {
        "positive_source_fold_net": to_int(row.get("positive_source_fold_net")),
        "max_positive_net_share": to_float(row.get("max_positive_net_share")),
        "negative_source_fold_slices": to_int(row.get("negative_source_fold_slices")),
        "status": row.get("status", ""),
    }


def taxonomy_status(taxonomy_rows: list[dict[str, str]], candidate_id: str) -> dict[str, Any]:
    rows = [row for row in taxonomy_rows if row.get("candidate_id") == candidate_id]
    empty = next((row for row in rows if row.get("taxonomy_slice") == "taxonomy_empty"), {})
    present = next((row for row in rows if row.get("taxonomy_slice") == "taxonomy_present"), {})
    return {
        "taxonomy_empty_net": to_int(empty.get("net")),
        "taxonomy_empty_loss": to_int(empty.get("loss")),
        "taxonomy_present_net": to_int(present.get("net")),
        "taxonomy_present_loss": to_int(present.get("loss")),
    }


def gate_status(gate_rows: list[dict[str, str]], candidate_id: str) -> dict[str, Any]:
    rows = [row for row in gate_rows if row.get("candidate_id") == candidate_id]
    applied = sum(to_int(row.get("applied")) for row in rows)
    gain = sum(to_int(row.get("gain")) for row in rows)
    loss = sum(to_int(row.get("loss")) for row in rows)
    best_reason = max(rows, key=lambda row: to_int(row.get("net")), default={})
    return {
        "gate_applied": applied,
        "gate_gain": gain,
        "gate_loss": loss,
        "gate_net": gain - loss,
        "top_gate_reason": best_reason.get("gate_reason", ""),
        "top_gate_support_score": best_reason.get("challenger_support_score", ""),
    }


def feature_rows(feature_importance: list[dict[str, str]], candidate_id: str, limit: int = 12) -> list[dict[str, Any]]:
    rows = [
        {"candidate_id": candidate_id, "feature": row.get("feature", ""), "gain_sum": to_float(row.get("gain_sum"))}
        for row in feature_importance
        if row.get("candidate_id") == candidate_id
    ]
    rows.sort(key=lambda row: row["gain_sum"], reverse=True)
    return rows[:limit]


def classify_candidates(
    *,
    scorecard: list[dict[str, str]],
    loss_rows: list[dict[str, str]],
    source_rows: list[dict[str, str]],
    taxonomy_rows: list[dict[str, str]],
    gate_rows: list[dict[str, str]],
    leakage_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    reviews: list[dict[str, Any]] = []
    for row in scorecard:
        cid = row["candidate_id"]
        source_share, source_key, source_gain, source_loss, source_net = top_net_share(loss_rows, cid, "source_family")
        province_share, province_key, _, _, province_net = top_net_share(loss_rows, cid, "province")
        fold_share, fold_key, _, _, fold_net = top_net_share(loss_rows, cid, "oof_fold")
        robust = source_robust_status(source_rows, cid)
        taxonomy = taxonomy_status(taxonomy_rows, cid)
        gate = gate_status(gate_rows, cid)
        leakage = next((item for item in leakage_rows if item.get("candidate_id") == cid), {})
        hit1_net = to_int(row.get("hit1_net"))
        hit1_loss = to_int(row.get("hit1_loss"))
        rank1_loss = to_int(row.get("rank1_loss_count"))
        approval = row.get("approval_status", "")
        if approval != "pass_dev_oof_candidate":
            freeze_status = "reject_not_approved"
            freeze_reason = approval
        elif hit1_net <= 0:
            freeze_status = "reject_non_positive_top1_net"
            freeze_reason = "Top1 net must be positive"
        elif hit1_loss > 0 or rank1_loss > 0:
            freeze_status = "reject_rank1_loss_budget"
            freeze_reason = "14.x freeze requires zero Top1/rank1 loss"
        elif leakage.get("status") != "pass":
            freeze_status = "reject_leakage"
            freeze_reason = "leakage gate failed"
        elif taxonomy["taxonomy_empty_net"] > 0 and taxonomy["taxonomy_present_net"] <= 0:
            freeze_status = "reject_taxonomy_empty_driven"
            freeze_reason = "taxonomy-empty cannot be sole freeze support"
        else:
            warnings = []
            if hit1_net < 5:
                warnings.append("small_dev_oof_net")
            if to_float(row.get("applied_group_rate")) < 0.005:
                warnings.append("narrow_gate_coverage")
            if source_share > 0.50:
                warnings.append("source_family_net_concentration")
            if province_share > 0.50:
                warnings.append("province_net_concentration")
            if fold_share > 0.50:
                warnings.append("fold_net_concentration")
            freeze_status = "freeze_with_risk_notes" if warnings else "freeze_preferred"
            freeze_reason = "|".join(warnings) if warnings else "positive zero-loss robust candidate"
        reviews.append(
            {
                "scorecard_rank": to_int(row.get("scorecard_rank")),
                "candidate_id": cid,
                "objective_variant": row.get("objective_variant", ""),
                "feature_toggle": row.get("feature_toggle", ""),
                "hit1_gain": to_int(row.get("hit1_gain")),
                "hit1_loss": hit1_loss,
                "hit1_net": hit1_net,
                "hit5_net": to_int(row.get("hit5_net")),
                "rank1_loss_count": rank1_loss,
                "baseline_rank1_demotion_rate": to_float(row.get("baseline_rank1_demotion_rate")),
                "applied_groups": to_int(row.get("applied_groups")),
                "applied_group_rate": to_float(row.get("applied_group_rate")),
                "approval_status": approval,
                "source_family_top_share": source_share,
                "source_family_top_key": source_key,
                "source_family_top_gain": source_gain,
                "source_family_top_loss": source_loss,
                "source_family_top_net": source_net,
                "province_top_share": province_share,
                "province_top_key": province_key,
                "province_top_net": province_net,
                "fold_top_share": fold_share,
                "fold_top_key": fold_key,
                "fold_top_net": fold_net,
                **robust,
                **taxonomy,
                **gate,
                "leakage_status": leakage.get("status", ""),
                "freeze_status": freeze_status,
                "freeze_reason": freeze_reason,
            }
        )
    reviews.sort(key=lambda item: (
        item["freeze_status"] not in {"freeze_preferred", "freeze_with_risk_notes"},
        item["freeze_status"] == "freeze_with_risk_notes",
        -item["hit1_net"],
        item["rank1_loss_count"],
    ))
    return reviews


def select_frozen(reviews: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [row for row in reviews if row["freeze_status"] in {"freeze_preferred", "freeze_with_risk_notes"}]
    candidates.sort(key=lambda row: (row["freeze_status"] == "freeze_with_risk_notes", -row["hit1_net"], -row["applied_groups"]))
    return candidates[0] if candidates else {}


def build_gate_rows(summary: dict[str, Any], frozen: dict[str, Any], reviews: list[dict[str, Any]], required_files: list[Path]) -> tuple[list[dict[str, Any]], str]:
    missing_files = [safe_rel(path) for path in required_files if not path.exists() or path.stat().st_size <= 0]
    rejected_d = next((row for row in reviews if row["candidate_id"] == "R14_D_near_miss_proxy_no_clean_rank1"), {})
    rows = [
        {
            "gate": "dev_oof_execution_complete",
            "status": "pass" if summary.get("decision") == "dev_oof_training_completed_freeze_gate_required" else "fail",
            "value": summary.get("decision", ""),
            "reason": "Freeze gate requires completed 14.3 dev/OOF training.",
        },
        {
            "gate": "required_artifacts_present",
            "status": "pass" if not missing_files else "fail",
            "value": len(missing_files),
            "reason": "|".join(missing_files),
        },
        {
            "gate": "freeze_candidate_selected",
            "status": "pass" if frozen else "fail",
            "value": frozen.get("candidate_id", ""),
            "reason": "A freeze candidate must be positive, approved, zero-loss, and non-leaky.",
        },
        {
            "gate": "positive_top1_net",
            "status": "pass" if to_int(frozen.get("hit1_net")) > 0 else "fail",
            "value": frozen.get("hit1_net", 0),
            "reason": "Frozen candidate must have positive dev/OOF Top1 net.",
        },
        {
            "gate": "zero_top1_and_rank1_loss",
            "status": "pass" if to_int(frozen.get("hit1_loss")) == 0 and to_int(frozen.get("rank1_loss_count")) == 0 else "fail",
            "value": f"hit1_loss={frozen.get('hit1_loss', 0)}; rank1_loss={frozen.get('rank1_loss_count', 0)}",
            "reason": "14.x protects baseline rank1; freeze requires zero observed Top1/rank1 losses.",
        },
        {
            "gate": "taxonomy_empty_not_driver",
            "status": "pass" if to_int(frozen.get("taxonomy_empty_net")) <= 0 and to_int(frozen.get("taxonomy_present_net")) > 0 else "fail",
            "value": f"empty_net={frozen.get('taxonomy_empty_net', 0)}; present_net={frozen.get('taxonomy_present_net', 0)}",
            "reason": "Taxonomy-empty rows cannot be the source of freeze support.",
        },
        {
            "gate": "source_fold_robustness",
            "status": "pass" if frozen.get("status") == "pass" else "warn",
            "value": f"max_positive_net_share={frozen.get('max_positive_net_share', 0)}; negative_slices={frozen.get('negative_source_fold_slices', 0)}",
            "reason": "14.3 source/fold robustness summary should pass.",
        },
        {
            "gate": "coverage_risk_recorded",
            "status": "warn" if to_float(frozen.get("applied_group_rate")) < 0.005 else "pass",
            "value": frozen.get("applied_group_rate", 0),
            "reason": "R14_A is safe but narrow; this must travel with the frozen candidate.",
        },
        {
            "gate": "net_size_risk_recorded",
            "status": "warn" if to_int(frozen.get("hit1_net")) < 5 else "pass",
            "value": frozen.get("hit1_net", 0),
            "reason": "Small dev/OOF gain is valid for freeze but weak evidence for release.",
        },
        {
            "gate": "aggressive_candidate_blocked",
            "status": "pass" if rejected_d.get("freeze_status") == "reject_not_approved" and to_int(rejected_d.get("rank1_loss_count")) > 1 else "fail",
            "value": f"R14_D status={rejected_d.get('freeze_status', '')}; rank1_loss={rejected_d.get('rank1_loss_count', '')}; net={rejected_d.get('hit1_net', '')}",
            "reason": "R14_D has attractive net but violates rank1 loss budget, so it must not freeze.",
        },
        {
            "gate": "heldout_hard_not_used",
            "status": "pass" if not summary.get("metrics", {}).get("heldout_used_for_selection") and not summary.get("metrics", {}).get("hard_used_for_selection") else "fail",
            "value": f"heldout={summary.get('metrics', {}).get('heldout_used_for_selection')}; hard={summary.get('metrics', {}).get('hard_used_for_selection')}",
            "reason": "14.4 is freeze-only and cannot validate or tune on heldout/hard.",
        },
    ]
    if any(row["status"] == "fail" for row in rows):
        decision = "do_not_freeze_fix_gate_failures"
    elif any(row["status"] == "warn" for row in rows):
        decision = "freeze_R14_A_for_future_validation_with_risk_notes"
    else:
        decision = "freeze_R14_A_for_future_validation"
    return rows, decision


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    frozen = report["frozen_candidate"]
    lines = [
        "# 14.4 Rank1-Safe Source-Robust Freeze Gate",
        "",
        "Read-only freeze gate after 14.3 dev/OOF training. No heldout/hard validation, online release, GoalSearcher edit, or threshold change was performed.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Frozen Candidate",
        "",
        md_table(
            [
                ["field", "value"],
                ["candidate_id", frozen.get("candidate_id", "")],
                ["hit1 gain/loss/net", f"{frozen.get('hit1_gain')}/{frozen.get('hit1_loss')}/{frozen.get('hit1_net')}"],
                ["rank1_loss_count", frozen.get("rank1_loss_count", "")],
                ["hit5_net", frozen.get("hit5_net", "")],
                ["applied_groups", frozen.get("applied_groups", "")],
                ["applied_group_rate", frozen.get("applied_group_rate", "")],
                ["freeze_reason", frozen.get("freeze_reason", "")],
            ]
        ),
        "",
        "## Gate Checks",
        "",
        md_table([["gate", "status", "value", "reason"]] + [[row["gate"], row["status"], row["value"], row["reason"]] for row in report["gate_rows"]]),
        "",
        "## Candidate Review",
        "",
        md_table(
            [["candidate", "hit1_net", "hit1_loss", "rank1_loss", "applied", "freeze_status", "freeze_reason"]]
            + [
                [row["candidate_id"], row["hit1_net"], row["hit1_loss"], row["rank1_loss_count"], row["applied_groups"], row["freeze_status"], row["freeze_reason"]]
                for row in report["candidate_reviews"]
            ]
        ),
        "",
        "## Top Features",
        "",
        md_table([["feature", "gain_sum"]] + [[row["feature"], round(row["gain_sum"], 6)] for row in report["top_features"]]),
        "",
        "## Next",
        "",
        report["next_stage"]["recommended"],
        "",
        "## Anti-Drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    frozen = report["frozen_candidate"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：14.4 rank1-safe source-robust freeze gate 已完成。\n"
        f"结论：{report['decision']}。冻结候选={frozen.get('candidate_id')}，"
        f"Top1 net={frozen.get('hit1_net')}，loss={frozen.get('hit1_loss')}，rank1_loss={frozen.get('rank1_loss_count')}。\n"
        "下一步建议：14.5 validation boundary / explicit validation go-no-go，只读决定是否允许 future heldout/hard A/B validation；默认不跑验证。\n"
        "禁止：无明确 validation go 跑 heldout/hard、上线、改 GoalSearcher、改阈值、把 dev/OOF freeze 宣称为通用 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    replacements = {
        '<div class="value">14.3 done</div>': '<div class="value">14.4 freeze</div>',
        'rank1-safe source-robust dev/OOF 训练已完成；当前停在 14.4 freeze gate 前，不再自动训练或验证。': '14.4 freeze gate 已完成；R14_A 被冻结为未来 validation candidate，但尚未验证、未上线。',
        '<div class="value">14.x freeze gate</div>': '<div class="value">14.5 validation gate</div>',
        '下一步只读复核 scorecard、loss slices、source/fold robustness，决定是否 freeze R14_A 或继续 redesign。': '下一步只读定义是否允许 future heldout/hard A/B validation；默认不跑验证。',
        '14.2 balanced OSS matrix 已建；14.3 dev/OOF 已训练。最佳 R14_A：Top1 net +3、loss 0。下一步 14.4 只读 freeze gate。': '14.4 已冻结 R14_A 作为未来 validation candidate：Top1 net +3、loss 0、rank1_loss 0；下一步 14.5 validation go/no-go。',
        '<text x="939" y="317" class="pointer-text">现在在这里：14.4 freeze gate</text>': '<text x="939" y="317" class="pointer-text">现在在这里：14.5 validation gate</text>',
        '<text x="939" y="333" class="pointer-note">14.3 dev/OOF 已完成</text>': '<text x="939" y="333" class="pointer-note">R14_A frozen; no validation yet</text>',
        '<text x="1030" y="378" class="map-label">14.4 Gate</text>': '<text x="1030" y="378" class="map-label">14.5 Gate</text>',
        '<text x="1030" y="400" class="map-note">freeze?</text>': '<text x="1030" y="400" class="map-note">validate?</text>',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    if "14.4 rank1-safe source-robust freeze gate summary" not in text:
        row = f"""          <tr>
            <td>14.4 rank1-safe source-robust freeze gate summary</td>
            <td>Read-only freeze decision for R14_A after scorecard, loss, source/fold, taxonomy, and rank1 preservation review.</td>
            <td><code>{safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def update_status_md(path: Path, report: dict[str, Any]) -> None:
    frozen = report["frozen_candidate"]
    text = f"""# Current Goal Roadmap Status

Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')} Asia/Shanghai

## Where We Are

Current stage: **14.4 freeze gate completed**.

Next gate: **14.5 validation boundary / explicit validation go-no-go**.

Meaning: `R14_A_rank1_veto_strong_challenger` is frozen as a future validation candidate. We have **not** run heldout/hard validation, released, changed thresholds, or changed GoalSearcher.

## Frozen Candidate

Candidate: `{frozen.get('candidate_id')}`

- Dev/OOF Top1 net: `{frozen.get('hit1_net')}`
- Top1 gains: `{frozen.get('hit1_gain')}`
- Top1 losses: `{frozen.get('hit1_loss')}`
- Rank1 loss count: `{frozen.get('rank1_loss_count')}`
- Hit5 net: `{frozen.get('hit5_net')}`
- Applied groups: `{frozen.get('applied_groups')} / 2155`
- Freeze status: `{frozen.get('freeze_status')}`
- Risk notes: `{frozen.get('freeze_reason')}`

`R14_D_near_miss_proxy_no_clean_rank1` remains blocked: it had higher dev/OOF Top1 net but rank1_loss `3`, above the 14.x freeze budget.

## Current Boundary

Allowed next: read-only 14.5 validation boundary / explicit validation go-no-go.

Blocked until future explicit validation go:

- heldout/hard A/B validation
- release
- GoalSearcher integration
- online threshold changes
- changing the frozen candidate using validation feedback

## Recommended Next Step

Run **14.5 validation boundary / explicit validation go-no-go**.

Default without explicit go: `do_not_validate`.
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="14.4 rank1-safe source-robust freeze gate review")
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--scorecard", type=Path, default=DEFAULT_SCORECARD)
    parser.add_argument("--loss-audit", type=Path, default=DEFAULT_LOSS_AUDIT)
    parser.add_argument("--rank1", type=Path, default=DEFAULT_RANK1)
    parser.add_argument("--gate-coverage", type=Path, default=DEFAULT_GATE_COVERAGE)
    parser.add_argument("--source-robustness", type=Path, default=DEFAULT_SOURCE_ROBUSTNESS)
    parser.add_argument("--taxonomy", type=Path, default=DEFAULT_TAXONOMY)
    parser.add_argument("--leakage", type=Path, default=DEFAULT_LEAKAGE)
    parser.add_argument("--feature-importance", type=Path, default=DEFAULT_FEATURE_IMPORTANCE)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--status-md", type=Path, default=DEFAULT_STATUS_MD)
    args = parser.parse_args()

    summary = read_json(args.summary)
    scorecard = read_csv(args.scorecard)
    loss_rows = read_csv(args.loss_audit)
    gate_rows_in = read_csv(args.gate_coverage)
    source_rows = read_csv(args.source_robustness)
    taxonomy_rows = read_csv(args.taxonomy)
    leakage_rows = read_csv(args.leakage)
    feature_importance = read_csv(args.feature_importance)
    required = [args.summary, args.scorecard, args.loss_audit, args.rank1, args.gate_coverage, args.source_robustness, args.taxonomy, args.leakage, args.feature_importance]

    reviews = classify_candidates(
        scorecard=scorecard,
        loss_rows=loss_rows,
        source_rows=source_rows,
        taxonomy_rows=taxonomy_rows,
        gate_rows=gate_rows_in,
        leakage_rows=leakage_rows,
    )
    frozen = select_frozen(reviews)
    checks, decision = build_gate_rows(summary, frozen, reviews, required)
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "candidate_review_csv": str(output_prefix.with_name(output_prefix.name + "_candidate_review.csv")),
        "gate_checks_csv": str(output_prefix.with_name(output_prefix.name + "_gate_checks.csv")),
        "frozen_candidate_json": str(output_prefix.with_name(output_prefix.name + "_frozen_candidate.json")),
        "freeze_contract_csv": str(output_prefix.with_name(output_prefix.name + "_freeze_contract.csv")),
    }
    freeze_contract = [
        {"item": "frozen_candidate_id", "value": frozen.get("candidate_id", "")},
        {"item": "frozen_scope", "value": "future validation candidate only; not release; not online"},
        {"item": "validation_boundary", "value": "heldout/hard A/B only after explicit 14.5/14.6 validation go"},
        {"item": "risk_notes", "value": frozen.get("freeze_reason", "")},
        {"item": "blocked_candidate", "value": "R14_D blocked because rank1_loss_count=3"},
        {"item": "default_next", "value": "do_not_validate"},
    ]
    report = {
        "stage": "14.4 rank1-safe source-robust scorecard/loss/source robustness freeze gate",
        "read_only_review": True,
        "decision": decision,
        "frozen_candidate": frozen,
        "candidate_reviews": reviews,
        "gate_rows": checks,
        "top_features": feature_rows(feature_importance, frozen.get("candidate_id", "")),
        "freeze_contract": freeze_contract,
        "artifacts": artifacts,
        "next_stage": {
            "recommended": "14.5 validation boundary / explicit validation go-no-go: read-only decide whether to allow future heldout/hard A/B validation for frozen R14_A. Default is do_not_validate.",
            "default": "do_not_validate",
        },
        "anti_drift_conclusion": (
            "14.4 is read-only. It froze a dev/OOF candidate for possible future validation only; it did not run heldout/hard, "
            "did not release, did not edit GoalSearcher, did not tune thresholds, and did not claim a general Top1 lift."
        ),
    }
    write_json(Path(artifacts["summary_json"]), report)
    write_markdown(Path(artifacts["summary_md"]), report)
    write_csv(Path(artifacts["candidate_review_csv"]), reviews, [
        "scorecard_rank", "candidate_id", "hit1_gain", "hit1_loss", "hit1_net", "hit5_net", "rank1_loss_count",
        "applied_groups", "applied_group_rate", "approval_status", "source_family_top_share", "source_family_top_key",
        "province_top_share", "province_top_key", "fold_top_share", "fold_top_key", "taxonomy_empty_net",
        "taxonomy_present_net", "gate_applied", "gate_gain", "gate_loss", "gate_net", "freeze_status", "freeze_reason",
    ])
    write_csv(Path(artifacts["gate_checks_csv"]), checks, ["gate", "status", "value", "reason"])
    write_json(Path(artifacts["frozen_candidate_json"]), frozen)
    write_csv(Path(artifacts["freeze_contract_csv"]), freeze_contract, ["item", "value"])
    update_dashboard(args.dashboard, report)
    update_status_md(args.status_md, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": decision, "frozen_candidate": frozen}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
