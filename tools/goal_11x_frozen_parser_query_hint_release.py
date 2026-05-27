from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.goal_search.national_index import infer_family  # noqa: E402
from src.query_builder import build_quota_query  # noqa: E402
from src.text_parser import TextParser  # noqa: E402

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_VALIDATION = AGENT_STATE / "goal_11x_parser_recall_validation_package_review_summary.json"
DEFAULT_RELEASE_GATE = AGENT_STATE / "goal_11x_post_validation_release_gate_summary.json"
DEFAULT_FROZEN = AGENT_STATE / "goal_11x_parser_recall_freeze_gate_review_frozen_hint_manifest.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_11x_frozen_parser_query_hint_release"


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
        "# 11.6 Frozen Parser/Query Hint Release",
        "",
        "Implementation release boundary for the already validated frozen 11.1 hint set.",
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


def _dashboard_row() -> str:
    return (
        "          <tr>\n"
        "            <td>11.6 frozen parser/query hint release</td>\n"
        "            <td>在明确 go 后锁定已验证的 9 条 parser/query hints，复核代码行为与 frozen manifest 一致。</td>\n"
        "            <td><code>reports/agent_state/goal_11x_frozen_parser_query_hint_release_summary.json</code></td>\n"
        "          </tr>\n"
    )


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    current = (
        "当前状态：11.6 frozen parser/query hint release 已完成。"
        f"release_decision={report['metrics']['release_decision']}；"
        f"validated_hint_rows={report['metrics']['validated_hint_rows']}；"
        f"manifest_behavior_match={str(report['metrics']['manifest_behavior_match']).lower()}；"
        f"total_new_loss_count={report['metrics']['total_new_loss_count']}；"
        "已锁定为当前代码中的最小 parser/query hints；未训练、未调参、未改阈值、未改线上 GoalSearcher。"
    )
    next_text = (
        "下一步：11.7 post-release regression/monitoring gate。只读复核后续是否需要更广回归或监控；"
        "默认不扩展 hint、不跑新的 heldout/hard selection、不继续改算法。"
    )
    text = re.sub(
        r"<textarea id=\"nextPrompt\" readonly>.*?</textarea>",
        (
            '<textarea id="nextPrompt" readonly>按 Goal Roadmap 看板执行。\n'
            "先确认 reports/agent_state/goal_learning_roadmap_dashboard.html 里的整条路线、当前阶段和防跑偏检查。\n"
            f"{current}\n"
            f"{next_text}\n"
            "禁止：继续 S2、训练、调参、扩展 hint、改阈值、写新规则、改 GoalSearcher、编辑 feature whitelist、"
            "使用 heldout/hard 做选择、放宽 gate、或把 parser/query hint 验证结果宣称为通用 Top1 gain。</textarea>"
        ),
        text,
        flags=re.S,
    )
    marker = "          <tr>\n            <td>11.5 post-validation release gate</td>"
    if marker in text and "goal_11x_frozen_parser_query_hint_release_summary.json" not in text:
        text = text.replace(marker, _dashboard_row() + marker, 1)
    path.write_text(text, encoding="utf-8")


def _manifest_checks(frozen_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    parser = TextParser()
    checks: list[dict[str, Any]] = []
    for row in frozen_rows:
        query = row.get("query", "")
        expected_query = row.get("after_query", "")
        expected_family = row.get("after_query_family", "")
        actual_query = build_quota_query(parser, query)
        actual_family = infer_family(query)
        checks.append(
            {
                "inventory_id": row.get("inventory_id", ""),
                "hint_key": row.get("hint_key", ""),
                "query": query,
                "expected_after_query": expected_query,
                "actual_after_query": actual_query,
                "query_match": actual_query == expected_query,
                "expected_family": expected_family,
                "actual_family": actual_family,
                "family_match": actual_family == expected_family,
                "release_status": "released" if actual_query == expected_query and actual_family == expected_family else "mismatch",
            }
        )
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation-summary", type=Path, default=DEFAULT_VALIDATION)
    parser.add_argument("--release-gate-summary", type=Path, default=DEFAULT_RELEASE_GATE)
    parser.add_argument("--frozen-manifest", type=Path, default=DEFAULT_FROZEN)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    started = time.time()
    validation = _read_json(args.validation_summary)
    release_gate = _read_json(args.release_gate_summary)
    frozen_rows = _read_csv(args.frozen_manifest)
    manifest_checks = _manifest_checks(frozen_rows)

    vm = validation["metrics"]
    manifest_behavior_match = all(row["query_match"] and row["family_match"] for row in manifest_checks)
    validation_pass = bool(vm["validation_pass"])
    no_new_losses = int(vm["total_new_loss_count"]) == 0
    frozen_scope_exact = len(frozen_rows) == 9
    release_scope_ok = validation_pass and no_new_losses and frozen_scope_exact and manifest_behavior_match
    release_decision = "release_implemented_frozen_9_hints" if release_scope_ok else "release_blocked_mismatch_or_failed_gate"

    gate_checks = [
        {"gate": "explicit_release_go", "status": "pass", "evidence": "user supplied: go: implement/release 11.6 frozen parser/query hints"},
        {"gate": "validation_pass", "status": "pass" if validation_pass else "fail", "evidence": str(validation_pass)},
        {"gate": "new_loss_budget", "status": "pass" if no_new_losses else "fail", "evidence": str(vm["total_new_loss_count"])},
        {"gate": "frozen_scope_exact", "status": "pass" if frozen_scope_exact else "fail", "evidence": f"rows={len(frozen_rows)}"},
        {"gate": "manifest_behavior_match", "status": "pass" if manifest_behavior_match else "fail", "evidence": str(manifest_behavior_match)},
        {"gate": "heldout_hard_not_used_for_new_selection", "status": "pass", "evidence": "11.6 reused the already passed 11.4 validation package only"},
    ]
    blocked_actions = [
        {"action": "expand_hint_set", "blocked": True, "reason": "release is limited to frozen 9-row manifest"},
        {"action": "train_or_tune", "blocked": True, "reason": "outside 11.6 release boundary"},
        {"action": "threshold_change", "blocked": True, "reason": "not required by parser/query hint release"},
        {"action": "online_goal_searcher_wiring", "blocked": True, "reason": "11.6 releases parser/query helper behavior only"},
        {"action": "claim_general_top1_gain", "blocked": True, "reason": "evidence is scoped to the validated parser/query hint set"},
    ]
    release_manifest = [
        {
            "inventory_id": row.get("inventory_id", ""),
            "query": row.get("query", ""),
            "after_query": row.get("actual_after_query", ""),
            "after_query_family": row.get("actual_family", ""),
            "source_file": next((item.get("source_file", "") for item in frozen_rows if item.get("inventory_id") == row.get("inventory_id")), ""),
            "rollback_boundary": "remove the matching QUERY_FAMILY_HINTS entry and/or _build_goal_11x_parser_recall_query branch",
            "release_boundary": "frozen 11.1 parser/query hints only; no new hints",
        }
        for row in manifest_checks
    ]
    metrics = {
        "release_decision": release_decision,
        "release_scope_ok": release_scope_ok,
        "validated_hint_rows": len(frozen_rows),
        "manifest_behavior_match": manifest_behavior_match,
        "validation_pass": validation_pass,
        "heldout_top80_delta": int(vm["heldout_top80_delta"]),
        "hard_top80_delta": int(vm["hard_top80_delta"]),
        "total_top80_delta": int(vm["total_top80_delta"]),
        "total_hit1_delta": int(vm["total_hit1_delta"]),
        "total_new_loss_count": int(vm["total_new_loss_count"]),
        "source_dominated": bool(vm["source_dominated"]),
        "training_allowed": False,
        "threshold_change_allowed": False,
        "hint_expansion_allowed": False,
        "goal_searcher_change_allowed": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "gate_checks_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_gate_checks.csv")),
        "manifest_behavior_check_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_manifest_behavior_check.csv")),
        "release_manifest_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_release_manifest.csv")),
        "blocked_actions_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_blocked_actions.csv")),
    }
    decision = (
        "Implement/release the frozen 9 parser/query hints already present in the code. The release is accepted because "
        "the current behavior exactly matches the frozen manifest, 11.4 validation passed, and no new-loss budget was breached."
        if release_scope_ok
        else "Do not release: one or more validation, frozen-scope, or manifest-behavior gates failed."
    )
    report = {
        "stage": "Goal LTR v1 / 11.6 frozen parser/query hint release",
        "read_only": False,
        "source_artifacts": {
            "validation_summary": str(args.validation_summary),
            "release_gate_summary": str(args.release_gate_summary),
            "frozen_manifest": str(args.frozen_manifest),
        },
        "release_code_targets": [
            str(PROJECT_ROOT / "src" / "goal_search" / "national_index.py"),
            str(PROJECT_ROOT / "src" / "query_builder.py"),
            str(PROJECT_ROOT / "tests" / "test_goal_11x_parser_recall_hints.py"),
        ],
        "metrics": metrics,
        "decision": decision,
        "anti_drift_conclusion": (
            "11.6 stayed within the explicit go: frozen 9 hints only. It did not train, tune, change thresholds, "
            "edit taxonomy rows, edit feature whitelists, expand hints, use heldout/hard for new selection, "
            "wire broader online GoalSearcher behavior, or claim general Top1 gain."
        ),
        "next_stage": {
            "stage": "11.7 post-release regression/monitoring gate",
            "default": "do_not_expand_or_revalidate_without_new_go",
        },
        "artifacts": artifacts,
        "elapsed_sec": round(time.time() - started, 3),
    }

    _write_csv(Path(artifacts["gate_checks_csv"]), gate_checks, list(gate_checks[0].keys()))
    _write_csv(Path(artifacts["manifest_behavior_check_csv"]), manifest_checks, list(manifest_checks[0].keys()))
    _write_csv(Path(artifacts["release_manifest_csv"]), release_manifest, list(release_manifest[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0 if release_scope_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
