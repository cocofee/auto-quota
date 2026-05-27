from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
VALIDATION_CLOSURE = AGENT_STATE / "goal_17x_default_off_harness_validation_closure_summary.json"
VALIDATION_ROW_AUDIT = AGENT_STATE / "goal_17x_default_off_harness_validation_row_audit.csv"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_precision_hardening_plan"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _as_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _family_signal(closure: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in closure["family_signal_rows"]:
        family = str(row["slice"]).replace("family:", "")
        out[family] = row
    return out


def _loss_rows() -> list[dict[str, Any]]:
    rows = []
    for row in _read_csv(VALIDATION_ROW_AUDIT):
        if _as_int(row.get("top1_loss")) <= 0:
            continue
        rows.append(
            {
                "split": row.get("split", ""),
                "anchor_group_id": row.get("anchor_group_id", ""),
                "bucket": row.get("bucket", ""),
                "query_family": row.get("query_family", ""),
                "expected_ids": row.get("expected_ids", ""),
                "baseline_rank": row.get("baseline_rank", ""),
                "treatment_rank": row.get("treatment_rank", ""),
                "prior_generated_candidates": row.get("prior_generated_candidates", ""),
                "prior_positive_candidates": row.get("prior_positive_candidates", ""),
                "prior_false_candidates": row.get("prior_false_candidates", ""),
                "diagnostic_use_only": "yes",
            }
        )
    return rows


def _candidate_matrix(families: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    clean = ["concrete", "pump", "rebar"]
    clean_generated = sum(_as_int(families[item]["prior_generated_candidates"]) for item in clean if item in families)
    clean_positive = sum(_as_int(families[item]["prior_positive_candidates"]) for item in clean if item in families)
    clean_false = sum(_as_int(families[item]["prior_false_candidates"]) for item in clean if item in families)
    clean_top1 = sum(_as_int(families[item]["delta_top1"]) for item in clean if item in families)
    clean_loss = sum(_as_int(families[item]["top1_losses"]) for item in clean if item in families)
    clean_false_rate = round(clean_false / clean_generated, 6) if clean_generated else 0.0
    return [
        {
            "candidate_id": "H17_A_lossless_family_veto_pipe_support",
            "idea": "Block pipe/support from the 17.x broad prior; keep concrete/pump/rebar only.",
            "validation_diagnostic": f"concrete+pump+rebar diagnostic Top1 +{clean_top1}, loss {clean_loss}, false_rate {clean_false_rate}",
            "dev_oof_test": "Replay 17.4 OOF rows with core_families=concrete,pump,rebar and same TopK/support/source/overlap contract.",
            "expected_risk": "May give up pipe/support Top5/Top80 movement; should remove the two observed validation Top1 losses.",
            "go_condition": "dev/OOF Top1 loss=0, Top1 net remains positive, false rate < 0.85, and positive groups not collapsed to one family.",
        },
        {
            "candidate_id": "H17_B_pipe_strict_evidence_gate",
            "idea": "Re-admit pipe only under stronger observable evidence, such as exact bill/quota name compatibility or higher quota-specific overlap/source support.",
            "validation_diagnostic": "pipe produced Top1 +1 but loss 1 and false_rate 0.937853; hard pipe was net negative.",
            "dev_oof_test": "Define pipe-only strict variants on dev/OOF; compare against H17_A, not against heldout/hard.",
            "expected_risk": "Can overfit if thresholds are copied from validation losses; must derive from dev/OOF row audit.",
            "go_condition": "pipe dev/OOF loss=0 and false rate materially below broad pipe; otherwise keep pipe vetoed.",
        },
        {
            "candidate_id": "H17_C_support_strict_evidence_gate",
            "idea": "Re-admit support only under stronger support identity/evidence guards; support is useful but has a validation Top1 loss.",
            "validation_diagnostic": "support produced Top1 +1 with loss 1; false_rate 0.752941 is lower than pipe but still loss-bearing.",
            "dev_oof_test": "Support-only strict variants on dev/OOF: exact-name, higher support, source_family>=2, or baseline-rank protection.",
            "expected_risk": "May regress the support Top80 gain; preserve as optional branch after H17_A.",
            "go_condition": "support dev/OOF loss=0 and keeps positive Top1/Top5 movement.",
        },
        {
            "candidate_id": "H17_D_rank1_protection_veto",
            "idea": "Protect baseline rank1 unless the OSS challenger has strong multi-field evidence and baseline is weak/conflicted.",
            "validation_diagnostic": "Both observed Top1 losses were rank_miss rows where baseline_rank=1 and generated priors were all false.",
            "dev_oof_test": "Use dev/OOF row audit to define observable baseline-rank and evidence-strength vetoes.",
            "expected_risk": "May reduce Top1 wins if the veto is too broad.",
            "go_condition": "Top1 losses remain 0 while retaining most H17_A dev/OOF Top1 gain.",
        },
    ]


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.9 Validation-Failed Precision Hardening Plan",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Diagnostic Basis",
        "",
        f"- validation all Top1/Top5/Top20/Top80: `{report['validation_headline']['delta_top1']}/{report['validation_headline']['delta_top5']}/{report['validation_headline']['delta_top20']}/{report['validation_headline']['delta_top80']}`.",
        f"- failed gates: `{', '.join(row['check'] for row in report['failed_stop_conditions'])}`.",
        "- heldout/hard is diagnostic only; hardening must be designed and selected on dev/OOF.",
        "",
        "## Candidate Matrix",
        "",
        "| candidate | idea | dev/OOF go condition |",
        "|---|---|---|",
    ]
    for row in report["candidate_matrix"]:
        lines.append(f"| {row['candidate_id']} | {row['idea']} | {row['go_condition']} |")
    lines.extend(
        [
            "",
            "## Loss Diagnostics",
            "",
            "| split | anchor | family | bucket | baseline->treatment | generated/positive/false |",
            "|---|---|---|---|---|---|",
        ]
    )
    for row in report["top1_loss_rows"]:
        lines.append(
            f"| {row['split']} | {row['anchor_group_id']} | {row['query_family']} | {row['bucket']} | {row['baseline_rank']}->{row['treatment_rank']} | {row['prior_generated_candidates']}/{row['prior_positive_candidates']}/{row['prior_false_candidates']} |"
        )
    lines.extend(
        [
            "",
            "## Next Boundary",
            "",
            report["next_boundary"],
            "",
            "## Anti-Drift",
            "",
            report["anti_drift_conclusion"],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    closure = json.loads(VALIDATION_CLOSURE.read_text(encoding="utf-8"))
    families = _family_signal(closure)
    top1_loss_rows = _loss_rows()
    candidate_matrix = _candidate_matrix(families)
    hardening_commands = [
        {
            "stage": "17.10",
            "command": "python tools\\goal_17x_top3_guarded_shadow.py --output-prefix reports\\agent_state\\goal_17x_h17a_lossless_family_dev_oof_shadow",
            "note": "Future command must be implemented/parameterized to use concrete,pump,rebar only before execution.",
        },
        {
            "stage": "17.10",
            "command": "python tools\\goal_17x_precision_guard_redesign.py",
            "note": "Use only dev/OOF row audit for candidate redesign; heldout/hard rows remain diagnostic labels only.",
        },
    ]
    report = {
        "stage": "17.9 validation-failed precision hardening plan",
        "decision": "define_dev_oof_precision_hardening_candidates_request_execution_scope",
        "validation_headline": closure["headline"]["all"],
        "failed_stop_conditions": closure["failed_stop_conditions"],
        "family_signal_rows": closure["family_signal_rows"],
        "top1_loss_rows": top1_loss_rows,
        "candidate_matrix": candidate_matrix,
        "hardening_commands": hardening_commands,
        "next_boundary": (
            "Next is 17.10 dev/OOF-only precision hardening execution scope. It may implement/parameterize a dev/OOF shadow harness for H17_A/H17_B/H17_C/H17_D, "
            "but must not run heldout/hard, must not default-enable, and must not tune thresholds from validation rows."
        ),
        "validation_used_for_selection": False,
        "heldout_hard_rerun_allowed": False,
        "implementation_allowed_now": False,
        "training_allowed": False,
        "default_enable_allowed": False,
        "anti_drift_conclusion": (
            "17.9 used heldout/hard only as diagnostic evidence from the already completed 17.8 validation. "
            "It did not train, tune, implement new guards, run validation, default-enable OSS recall, or change GoalSearcher defaults."
        ),
        "artifacts": {
            "summary_json": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.json")),
            "summary_md": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_summary.md")),
            "candidate_matrix_csv": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_candidate_matrix.csv")),
            "loss_rows_csv": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_top1_loss_rows.csv")),
            "commands_csv": str(DEFAULT_PREFIX.with_name(DEFAULT_PREFIX.name + "_commands.csv")),
        },
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(Path(report["artifacts"]["summary_json"]), report)
    _write_markdown(Path(report["artifacts"]["summary_md"]), report)
    _write_csv(
        Path(report["artifacts"]["candidate_matrix_csv"]),
        candidate_matrix,
        ["candidate_id", "idea", "validation_diagnostic", "dev_oof_test", "expected_risk", "go_condition"],
    )
    _write_csv(
        Path(report["artifacts"]["loss_rows_csv"]),
        top1_loss_rows,
        [
            "split",
            "anchor_group_id",
            "bucket",
            "query_family",
            "expected_ids",
            "baseline_rank",
            "treatment_rank",
            "prior_generated_candidates",
            "prior_positive_candidates",
            "prior_false_candidates",
            "diagnostic_use_only",
        ],
    )
    _write_csv(Path(report["artifacts"]["commands_csv"]), hardening_commands, ["stage", "command", "note"])
    print(json.dumps({"summary": report["artifacts"]["summary_json"], "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
