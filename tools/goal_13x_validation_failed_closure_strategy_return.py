from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter
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
DEFAULT_PACKAGE = AGENT_STATE / "goal_13x_expanded_reranker_validation_package_review_summary.json"
DEFAULT_HELDOUT_DETAILS = AGENT_STATE / "goal_13x_expanded_reranker_validation_heldout_details.jsonl"
DEFAULT_HARD_DETAILS = AGENT_STATE / "goal_13x_expanded_reranker_validation_hard_details.jsonl"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_validation_failed_closure_strategy_return"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


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


def _rank_bucket(rank: Any) -> str:
    if rank is None:
        return "missing"
    rank = int(rank)
    if rank == 1:
        return "rank_1"
    if 2 <= rank <= 5:
        return "rank_2_5"
    if 6 <= rank <= 10:
        return "rank_6_10"
    if 11 <= rank <= 20:
        return "rank_11_20"
    if 21 <= rank <= 40:
        return "rank_21_40"
    return "rank_41_80"


def _transition_rows(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counter: Counter[tuple[str, str, str]] = Counter()
    for row in details:
        key = (str(row.get("split")), _rank_bucket(row.get("baseline_rank")), _rank_bucket(row.get("candidate_rank")))
        counter[key] += 1
    rows = [
        {"split": split, "baseline_rank_bucket": base, "candidate_rank_bucket": cand, "count": count}
        for (split, base, cand), count in counter.most_common()
    ]
    rows.sort(key=lambda row: (row["split"], row["baseline_rank_bucket"], -row["count"]))
    return rows


def _loss_example_rows(details: list[dict[str, Any]], limit: int = 30) -> list[dict[str, Any]]:
    rows = []
    for row in details:
        if row.get("flip_type") != "loss":
            continue
        rows.append(
            {
                "split": row.get("split"),
                "sample_id": row.get("sample_id"),
                "province": row.get("province"),
                "bucket": row.get("bucket"),
                "query_family": row.get("query_family"),
                "candidate_top_family": row.get("candidate_top_family"),
                "baseline_rank": row.get("baseline_rank"),
                "candidate_rank": row.get("candidate_rank"),
                "candidate_top_quota_id": row.get("candidate_top_quota_id"),
                "query": row.get("query"),
            }
        )
    rows.sort(key=lambda row: (str(row["split"]), str(row["province"]), str(row["query_family"])))
    return rows[:limit]


def _strategy_rows() -> list[dict[str, Any]]:
    return [
        {
            "lane": "13R_top1_loss_guarded_reranker_redesign",
            "why": "Heldout/hard Top1 failed while Hit5 improved; next model must optimize first-place preservation, not just move positives upward.",
            "entry_condition": "Use validation losses only as failure evidence; define a new dev/OOF experiment with explicit Top1 loss guard before any heldout reuse.",
            "forbidden": "Do not tune current frozen candidate on heldout/hard; do not release.",
        },
        {
            "lane": "13D_validation_distribution_alignment",
            "why": "OSS XML training is human and valuable, but validation split is global_repair-dominated; compare OSS XML, global_repair, and accepted-source distributions before next training.",
            "entry_condition": "Read-only inventory of feature/source/query_family mismatch between expanded OSS matrix and validation failures.",
            "forbidden": "Do not treat global_repair validation losses as new labels for immediate training.",
        },
        {
            "lane": "13G_gated_reranker_application_scope",
            "why": "Candidate improves Hit5, so a future reranker may help only when baseline Top1 is weak; need a gate that preserves baseline rank_1.",
            "entry_condition": "Design a dev/OOF-only what-if that applies reranker only to non-rank_1/confidence-low candidates.",
            "forbidden": "Do not apply reranker globally over all top80 candidates.",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    hm = report["heldout_metrics"]
    hdm = report["hard_metrics"]
    lines = [
        "# 13.15 Validation-Failed Closure / Strategy Return",
        "",
        "Read-only closure after 13.14 heldout/hard validation failed for the frozen expanded OSS XML reranker candidate.",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Validation Result",
        "",
        _md_table(
            [
                ["split", "hit1 gain/loss/net", "hit5 gain/loss/net", "top80_present", "recall_missing"],
                ["heldout", f"{hm['hit1_gain']}/{hm['hit1_loss']}/{hm['hit1_net']}", f"{hm['hit5_gain']}/{hm['hit5_loss']}/{hm['hit5_net']}", hm["rerankable_top80_present_groups"], hm["recall_missing_groups"]],
                ["hard", f"{hdm['hit1_gain']}/{hdm['hit1_loss']}/{hdm['hit1_net']}", f"{hdm['hit5_gain']}/{hdm['hit5_loss']}/{hdm['hit5_net']}", hdm["rerankable_top80_present_groups"], hdm["recall_missing_groups"]],
            ]
        ),
        "",
        "## Diagnosis",
        "",
        _md_table([["finding", "evidence", "consequence"]] + [[row["finding"], row["evidence"], row["consequence"]] for row in report["diagnosis_rows"]]),
        "",
        "## Strategy Options",
        "",
        _md_table([["lane", "why", "entry_condition", "forbidden"]] + [[row["lane"], row["why"], row["entry_condition"], row["forbidden"]] for row in report["strategy_rows"]]),
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


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    hm = report["heldout_metrics"]
    hdm = report["hard_metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：13.15 validation-failed closure / strategy return 已完成。\n"
        f"结论：{report['decision']}。heldout Top1 net={hm['hit1_net']}，hard Top1 net={hdm['hit1_net']}；Hit5 为正但不能上线。\n"
        "下一步建议：13.16 Top1-loss-guarded reranker redesign gate。只读定义新一轮 dev/OOF 实验：必须保护 baseline rank_1，限制 reranker 应用范围，不能使用 heldout/hard 做选择。\n"
        "禁止：release 当前 frozen candidate、接 GoalSearcher、用 heldout/hard 调参或重新选候选、把 Hit5 正收益宣称为 Top1 提升。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.15 validation-failed closure / strategy return" not in text:
        rows = f"""          <tr>
            <td>13.15 validation-failed closure / strategy return</td>
            <td>Read-only closure after failed heldout/hard Top1 validation; parks current frozen candidate and selects redesign route.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
"""
        marker = "          <tr>\n            <td>13.14 heldout/hard A/B validation package review</td>"
        if marker in text:
            text = text.replace(marker, rows + marker, 1)
        else:
            text = text.replace("</tbody>", rows + "        </tbody>", 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="13.15 validation-failed closure / strategy return")
    parser.add_argument("--package-summary", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--heldout-details", type=Path, default=DEFAULT_HELDOUT_DETAILS)
    parser.add_argument("--hard-details", type=Path, default=DEFAULT_HARD_DETAILS)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    package = _read_json(args.package_summary)
    heldout_details = _read_jsonl(args.heldout_details)
    hard_details = _read_jsonl(args.hard_details)
    all_details = heldout_details + hard_details
    hm = package["heldout_metrics"]
    hdm = package["hard_metrics"]
    transition_rows = _transition_rows(all_details)
    loss_examples = _loss_example_rows(all_details)
    diagnosis_rows = [
        {
            "finding": "Top1 validation failed on both independent splits",
            "evidence": f"heldout hit1_net={hm['hit1_net']}; hard hit1_net={hdm['hit1_net']}",
            "consequence": "Current frozen candidate must not be released or integrated.",
        },
        {
            "finding": "Reranker improves near-rank ordering but harms first-place precision",
            "evidence": f"heldout hit5_net={hm['hit5_net']}; hard hit5_net={hdm['hit5_net']} while Top1 net is negative",
            "consequence": "Next experiment needs Top1 loss guard or gated application, not a global reranker.",
        },
        {
            "finding": "Losses concentrate in weak taxonomy / global repair validation slices",
            "evidence": "largest loss rows include source_file=global_repair_decision_table.csv, top1_family=<empty>, query_family=<empty>, recall_miss/rank_miss/confidence_miss",
            "consequence": "Before another release path, compare OSS training distribution against validation failure distribution.",
        },
        {
            "finding": "Heldout/hard remain validation-only evidence",
            "evidence": "13.14 recorded heldout_or_hard_used_for_selection=false",
            "consequence": "Do not tune this candidate on validation failures; start a new dev/OOF design gate.",
        },
    ]
    strategy_rows = _strategy_rows()
    output_prefix = args.output_prefix
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "diagnosis_csv": str(output_prefix.with_name(output_prefix.name + "_diagnosis.csv")),
        "strategy_options_csv": str(output_prefix.with_name(output_prefix.name + "_strategy_options.csv")),
        "rank_transition_csv": str(output_prefix.with_name(output_prefix.name + "_rank_transition.csv")),
        "loss_examples_csv": str(output_prefix.with_name(output_prefix.name + "_loss_examples.csv")),
    }
    report = {
        "stage": "13.15 validation-failed closure / strategy return",
        "read_only": True,
        "decision": "park_frozen_candidate_return_to_top1_loss_guarded_redesign",
        "heldout_metrics": hm,
        "hard_metrics": hdm,
        "diagnosis_rows": diagnosis_rows,
        "strategy_rows": strategy_rows,
        "rank_transition_rows": transition_rows,
        "loss_examples": loss_examples,
        "artifacts": artifacts,
        "anti_drift_conclusion": "Read-only closure only: no release, no GoalSearcher edit, no threshold change, no candidate reselection, no heldout/hard tuning, and no claim of general Top1 improvement.",
        "next_stage": {
            "recommended": "13.16 Top1-loss-guarded reranker redesign gate: define a new dev/OOF-only experiment that protects baseline rank_1 and gates reranker application; do not reuse heldout/hard for selection.",
            "default": "do_not_release_current_candidate",
        },
    }
    _write_csv(Path(artifacts["diagnosis_csv"]), diagnosis_rows, ["finding", "evidence", "consequence"])
    _write_csv(Path(artifacts["strategy_options_csv"]), strategy_rows, ["lane", "why", "entry_condition", "forbidden"])
    _write_csv(Path(artifacts["rank_transition_csv"]), transition_rows, ["split", "baseline_rank_bucket", "candidate_rank_bucket", "count"])
    _write_csv(Path(artifacts["loss_examples_csv"]), loss_examples, ["split", "sample_id", "province", "bucket", "query_family", "candidate_top_family", "baseline_rank", "candidate_rank", "candidate_top_quota_id", "query"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"summary": artifacts["summary_json"], "decision": report["decision"], "heldout_hit1_net": hm["hit1_net"], "hard_hit1_net": hdm["hit1_net"], "next": report["next_stage"]["recommended"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
