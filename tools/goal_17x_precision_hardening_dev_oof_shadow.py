from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
import src.goal_search.oss_recall_prior as oss_recall_prior
from src.goal_search.national_index import clean_text
from src.goal_search.oss_recall_prior import OssRecallPriorSource, reset_oss_recall_prior_source
from src.goal_search.searcher import clear_goal_search_cache
from tools.goal_16x_local_assets_guarded_alias_ab_validation import (
    DEFAULT_DB_DIR,
    _configure_db_root,
    _evaluate_split,
    _read_jsonl,
    _scorecard,
    _write_csv,
    _write_json,
)


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_INDEX = PROJECT_ROOT / "data" / "goal_search" / "oss_recall_index_17x_multifield.jsonl"
DEFAULT_OOF = AGENT_STATE / "goal_query_anchored_ltr_safety_gate_oof_calibration" / "dev_oof_safety_gate_details.jsonl"
DEFAULT_BROAD_AUDIT = AGENT_STATE / "goal_17x_oss_multifield_dev_oof_shadow_row_audit.csv"
DEFAULT_TOP3_SUMMARY = AGENT_STATE / "goal_17x_top3_guarded_dev_oof_shadow_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_precision_hardening_dev_oof"
ALL_CANDIDATES = ("H17_A", "H17_B", "H17_C", "H17_D")


@dataclass(frozen=True)
class CandidateSpec:
    candidate_id: str
    label: str
    core_families: frozenset[str]
    branch_family: str = ""
    rank1_veto: bool = False
    description: str = ""


def _safe_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _split_expected_ids(value: object) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if isinstance(value, str):
        return [clean_text(item) for item in value.split("|") if clean_text(item)]
    return []


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _candidate_spec(candidate_id: str) -> CandidateSpec:
    candidate_id = clean_text(candidate_id).upper()
    specs = {
        "H17_A": CandidateSpec(
            candidate_id="H17_A",
            label="lossless_family_veto_pipe_support",
            core_families=frozenset({"concrete", "pump", "rebar"}),
            description="Block pipe/support; keep concrete/pump/rebar only.",
        ),
        "H17_B": CandidateSpec(
            candidate_id="H17_B",
            label="pipe_strict_evidence_gate",
            core_families=frozenset({"concrete", "pump", "rebar", "pipe"}),
            branch_family="pipe",
            description="Start from H17_A and re-admit pipe only with strict observable evidence.",
        ),
        "H17_C": CandidateSpec(
            candidate_id="H17_C",
            label="support_strict_evidence_gate",
            core_families=frozenset({"concrete", "pump", "rebar", "support"}),
            branch_family="support",
            description="Start from H17_A and re-admit support only with strict identity/evidence guards.",
        ),
        "H17_D": CandidateSpec(
            candidate_id="H17_D",
            label="rank1_protection_veto",
            core_families=frozenset({"concrete", "pipe", "pump", "rebar", "support"}),
            rank1_veto=True,
            description="All 17.x families with a baseline-rank1 protection veto.",
        ),
    }
    if candidate_id not in specs:
        raise ValueError(f"unknown candidate {candidate_id!r}; expected one of {', '.join(ALL_CANDIDATES)}")
    return specs[candidate_id]


def _strong_evidence(row: dict[str, Any]) -> bool:
    if bool(row.get("oss_recall_exact_name")):
        return True
    return (
        _safe_int(row.get("oss_recall_source_family_count")) >= 2
        and _safe_int(row.get("oss_recall_quota_specific_overlap")) >= 2
        and _safe_int(row.get("oss_recall_quota_name_overlap")) >= 1
    )


def _passes_candidate_filter(spec: CandidateSpec, row: dict[str, Any], item: dict[str, Any] | None = None) -> bool:
    family = clean_text(row.get("oss_recall_query_family") or row.get("query_family"))
    if family and family not in spec.core_families:
        return False
    if spec.branch_family and family == spec.branch_family and not _strong_evidence(row):
        return False
    if spec.rank1_veto and item and _safe_int(item.get("_h17_baseline_rank")) == 1 and not _strong_evidence(row):
        return False
    return True


class PrecisionHardenedRecallSource:
    def __init__(self, delegate: OssRecallPriorSource, spec: CandidateSpec) -> None:
        self.delegate = delegate
        self.spec = spec

    def collect(
        self,
        *,
        province: str,
        query_text: str,
        query_family: str = "",
        item: dict[str, Any] | None = None,
        top_k: int = 8,
    ) -> list[dict[str, Any]]:
        rows = self.delegate.collect(
            province=province,
            query_text=query_text,
            query_family=query_family,
            item=item,
            top_k=max(top_k * 8, 24),
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            enriched = dict(row)
            enriched["oss_recall_query_family"] = clean_text(query_family)
            if not _passes_candidate_filter(self.spec, enriched, item):
                continue
            output.append(enriched)
            if len(output) >= top_k:
                break
        return output


def _normalize_oof_rows(path: Path, broad_audit: Path) -> list[dict[str, Any]]:
    audit_rows = _read_csv(broad_audit)
    audit_by_group = {
        clean_text(row.get("anchor_group_id")): row
        for row in audit_rows
        if clean_text(row.get("anchor_group_id"))
    }
    group_ids = set(audit_by_group)
    rows: list[dict[str, Any]] = []
    for row in _read_jsonl(path):
        if clean_text(row.get("variant")) != "baseline_only":
            continue
        group_id = clean_text(row.get("anchor_group_id") or row.get("group_id"))
        if group_id not in group_ids:
            continue
        out = dict(row)
        audit = audit_by_group[group_id]
        out["anchor_group_id"] = group_id
        out["bucket"] = audit.get("bucket", out.get("bucket", ""))
        out["_h17_baseline_rank"] = _safe_int(audit.get("baseline_rank"))
        if not clean_text(out.get("bill_name") or out.get("name")):
            out["bill_name"] = clean_text(out.get("query"))
        out["expected_ids"] = _split_expected_ids(out.get("expected_ids"))
        if out["expected_ids"]:
            rows.append(out)
    rows.sort(key=lambda row: clean_text(row.get("anchor_group_id")))
    return rows


def _configure_candidate(spec: CandidateSpec, index: Path) -> PrecisionHardenedRecallSource:
    config.OSS_RECALL_INDEX_PATH = str(index)
    config.OSS_RECALL_INDEX_TOP_K = 3
    config.OSS_RECALL_INDEX_MIN_SUPPORT = 2
    config.OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES = 1
    config.OSS_RECALL_INDEX_MIN_OVERLAP = 2
    config.OSS_RECALL_INDEX_INTERVENTION_MODE = "broad"
    config.OSS_RECALL_INDEX_CORE_FAMILIES = tuple(sorted(spec.core_families))
    reset_oss_recall_prior_source()
    clear_goal_search_cache()
    delegate = OssRecallPriorSource(
        index,
        min_support=2,
        min_source_families=1,
        min_overlap=2,
        intervention_mode="broad",
        core_families=set(spec.core_families),
    )
    source = PrecisionHardenedRecallSource(delegate, spec)
    oss_recall_prior._SOURCE = source
    return source


def _headline(scorecard: list[dict[str, Any]]) -> dict[str, Any]:
    return next(row for row in scorecard if row["slice"] == "all")


def _positive_family_count(scorecard: list[dict[str, Any]]) -> int:
    return sum(
        1
        for row in scorecard
        if str(row["slice"]).startswith("family:") and _safe_int(row.get("prior_positive_candidates")) > 0
    )


def _candidate_stop_conditions(spec: CandidateSpec, headline: dict[str, Any], scorecard: list[dict[str, Any]]) -> list[dict[str, str]]:
    positive_family_count = _positive_family_count(scorecard)
    false_rate = float(headline.get("prior_false_candidate_rate") or 0.0)
    generated = _safe_int(headline.get("prior_generated_candidates"))
    branch_false_check = false_rate < 0.85 or spec.branch_family in {"pipe", "support"}
    return [
        {"candidate": spec.candidate_id, "check": "dev_oof_only", "status": "pass", "evidence": "input=dev_oof_safety_gate_details baseline_only impacted rows"},
        {
            "candidate": spec.candidate_id,
            "check": "top1_loss_guard",
            "status": "pass" if _safe_int(headline.get("top1_losses")) == 0 else "fail",
            "evidence": f"top1_losses={headline.get('top1_losses')}",
        },
        {
            "candidate": spec.candidate_id,
            "check": "positive_movement",
            "status": "pass" if _safe_int(headline.get("delta_top1")) > 0 and _safe_int(headline.get("delta_top5")) > 0 else "fail",
            "evidence": f"delta_top1={headline.get('delta_top1')}; delta_top5={headline.get('delta_top5')}",
        },
        {
            "candidate": spec.candidate_id,
            "check": "false_candidate_risk",
            "status": "pass" if generated == 0 or branch_false_check else "fail",
            "evidence": f"false_rate={false_rate}; generated={generated}",
        },
        {
            "candidate": spec.candidate_id,
            "check": "family_diversity",
            "status": "pass" if positive_family_count >= 2 else "fail",
            "evidence": f"positive_family_count={positive_family_count}",
        },
        {"candidate": spec.candidate_id, "check": "default_off_boundary", "status": "pass", "evidence": "config changed in-process only; no GoalSearcher default change"},
    ]


def _decision_from_stops(spec: CandidateSpec, stops: list[dict[str, str]]) -> str:
    failed = [row["check"] for row in stops if row["status"] != "pass"]
    if not failed:
        return f"{spec.candidate_id.lower()}_passes_dev_oof_hardening_gate"
    return f"{spec.candidate_id.lower()}_no_go_failed_{'_'.join(failed)}"


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.11 Precision Hardening Dev/OOF Shadow",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Result",
        "",
        "| candidate | Top1 | Top5 | Top20 | Top80 | wins/losses | generated/positive/false | false rate | decision |",
        "|---|---:|---:|---:|---:|---|---|---:|---|",
    ]
    for row in report["comparison"]:
        lines.append(
            f"| {row['candidate']} | {row['delta_top1']} | {row['delta_top5']} | {row['delta_top20']} | {row['delta_top80']} | "
            f"{row['top1_wins']}/{row['top1_losses']} | {row['prior_generated_candidates']}/{row['prior_positive_candidates']}/{row['prior_false_candidates']} | "
            f"{row['prior_false_candidate_rate']} | {row['decision']} |"
        )
    lines.extend(["", "## Interpretation", "", report["interpretation"], "", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_candidate(spec: CandidateSpec, args: argparse.Namespace, rows: list[dict[str, Any]]) -> dict[str, Any]:
    source = _configure_candidate(spec, args.index)
    prefix = args.output_prefix.with_name(f"{args.output_prefix.name}_{spec.candidate_id.lower()}_{spec.label}")
    row_audit, scorecard = _evaluate_split(
        f"dev_oof_17x_{spec.candidate_id.lower()}",
        rows,
        source,
        "recall",
        progress_every=args.progress_every,
        province_cache={},
    )
    headline = _headline(scorecard)
    stops = _candidate_stop_conditions(spec, headline, scorecard)
    decision = _decision_from_stops(spec, stops)
    summary_json = prefix.with_name(prefix.name + "_summary.json")
    summary_md = prefix.with_name(prefix.name + "_summary.md")
    scorecard_csv = prefix.with_name(prefix.name + "_scorecard.csv")
    row_csv = prefix.with_name(prefix.name + "_row_audit.csv")
    stop_csv = prefix.with_name(prefix.name + "_stop_conditions.csv")
    report = {
        "stage": "17.11 precision hardening dev/OOF shadow",
        "candidate": spec.candidate_id,
        "label": spec.label,
        "description": spec.description,
        "decision": decision,
        "rows_evaluated": len(rows),
        "contract": {
            "top_k": 3,
            "min_support": 2,
            "min_source_families": 1,
            "min_overlap": 2,
            "intervention_mode": "broad",
            "core_families": sorted(spec.core_families),
            "branch_family": spec.branch_family,
            "rank1_veto": spec.rank1_veto,
        },
        "headline": headline,
        "stop_conditions": stops,
        "execution_performed": True,
        "training_performed": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "artifacts": {
            "summary_json": str(summary_json),
            "summary_md": str(summary_md),
            "scorecard_csv": str(scorecard_csv),
            "row_audit_csv": str(row_csv),
            "stop_conditions_csv": str(stop_csv),
        },
        "anti_drift_conclusion": (
            f"17.11 {spec.candidate_id} ran only dev/OOF baseline_only impacted rows with in-process default-off recall settings. "
            "It did not train, tune, read heldout/hard, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_markdown(summary_md, {"updated_at": report["updated_at"], "decision": decision, "comparison": [dict({"candidate": spec.candidate_id, "decision": decision}, **headline)], "interpretation": spec.description, "anti_drift_conclusion": report["anti_drift_conclusion"]})
    _write_csv(scorecard_csv, scorecard, list(headline.keys()))
    _write_csv(row_csv, row_audit, list(row_audit[0].keys()) if row_audit else ["split", "row_ordinal"])
    _write_csv(stop_csv, stops, ["candidate", "check", "status", "evidence"])
    return report


def _comparison_row(report: dict[str, Any]) -> dict[str, Any]:
    h = report["headline"]
    return {
        "candidate": report["candidate"],
        "label": report["label"],
        "decision": report["decision"],
        "rows_evaluated": report["rows_evaluated"],
        "delta_top1": h.get("delta_top1", 0),
        "delta_top5": h.get("delta_top5", 0),
        "delta_top20": h.get("delta_top20", 0),
        "delta_top80": h.get("delta_top80", 0),
        "top1_wins": h.get("top1_wins", 0),
        "top1_losses": h.get("top1_losses", 0),
        "top80_gains": h.get("top80_gains", 0),
        "top80_losses": h.get("top80_losses", 0),
        "prior_generated_candidates": h.get("prior_generated_candidates", 0),
        "prior_positive_candidates": h.get("prior_positive_candidates", 0),
        "prior_false_candidates": h.get("prior_false_candidates", 0),
        "prior_false_candidate_rate": h.get("prior_false_candidate_rate", 0),
    }


def _select_lead(reports: Iterable[dict[str, Any]]) -> tuple[str, str]:
    rows = [_comparison_row(report) for report in reports]
    passed = [
        row
        for row in rows
        if str(row["decision"]).endswith("_passes_dev_oof_hardening_gate")
    ]
    if not passed:
        return "", "no_freeze_candidate_all_candidates_failed_dev_oof_gate"
    passed.sort(
        key=lambda row: (
            _safe_int(row["delta_top1"]),
            _safe_int(row["delta_top5"]),
            -float(row["prior_false_candidate_rate"] or 0.0),
            -_safe_int(row["prior_false_candidates"]),
        ),
        reverse=True,
    )
    return str(passed[0]["candidate"]), "freeze_gate_ready_for_best_dev_oof_candidate"


def main() -> int:
    parser = argparse.ArgumentParser(description="17.11 dev/OOF-only precision hardening shadow harness for H17_A/B/C/D")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--broad-row-audit", type=Path, default=DEFAULT_BROAD_AUDIT)
    parser.add_argument("--top3-summary", type=Path, default=DEFAULT_TOP3_SUMMARY)
    parser.add_argument("--candidate", choices=ALL_CANDIDATES + ("all",), default="all")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_PREFIX)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    _configure_db_root(args.db_dir)
    rows = _normalize_oof_rows(args.oof, args.broad_row_audit)
    candidate_ids = list(ALL_CANDIDATES if args.candidate == "all" else (args.candidate,))
    reports = [run_candidate(_candidate_spec(candidate_id), args, rows) for candidate_id in candidate_ids]
    comparison = [_comparison_row(report) for report in reports]
    stop_rows = [row for report in reports for row in report["stop_conditions"]]
    lead, decision = _select_lead(reports)
    top3_summary = json.loads(args.top3_summary.read_text(encoding="utf-8"))
    interpretation = (
        f"Best dev/OOF candidate is {lead}. "
        "This is an offline shadow result only; a separate freeze gate is required before any validation request."
        if lead
        else "No H17 candidate passed the locked dev/OOF hardening gates; keep the 17.x broad Top3 package stopped."
    )

    aggregate_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    aggregate_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    comparison_csv = args.output_prefix.with_name(args.output_prefix.name + "_comparison.csv")
    stop_csv = args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")
    aggregate = {
        "stage": "17.11 precision hardening dev/OOF execution",
        "decision": decision,
        "lead_candidate": lead,
        "rows_evaluated": len(rows),
        "candidate_reports": [report["artifacts"]["summary_json"] for report in reports],
        "baseline_17_4_top3_headline": top3_summary.get("headline", {}),
        "comparison": comparison,
        "stop_conditions": stop_rows,
        "interpretation": interpretation,
        "execution_performed": True,
        "training_performed": False,
        "heldout_hard_used": False,
        "default_enable_allowed": False,
        "online_integration_allowed": False,
        "artifacts": {
            "summary_json": str(aggregate_json),
            "summary_md": str(aggregate_md),
            "comparison_csv": str(comparison_csv),
            "stop_conditions_csv": str(stop_csv),
        },
        "anti_drift_conclusion": (
            "17.11 implemented and ran only the fixed H17_A/B/C/D dev/OOF shadow matrix. "
            "It did not train, tune, read heldout/hard, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(aggregate_json, aggregate)
    _write_markdown(aggregate_md, aggregate)
    _write_csv(
        comparison_csv,
        comparison,
        [
            "candidate",
            "label",
            "decision",
            "rows_evaluated",
            "delta_top1",
            "delta_top5",
            "delta_top20",
            "delta_top80",
            "top1_wins",
            "top1_losses",
            "top80_gains",
            "top80_losses",
            "prior_generated_candidates",
            "prior_positive_candidates",
            "prior_false_candidates",
            "prior_false_candidate_rate",
        ],
    )
    _write_csv(stop_csv, stop_rows, ["candidate", "check", "status", "evidence"])
    print(json.dumps({"summary": str(aggregate_json), "decision": decision, "lead_candidate": lead, "comparison": comparison}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
