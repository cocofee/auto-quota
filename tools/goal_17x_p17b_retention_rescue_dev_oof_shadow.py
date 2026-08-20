from __future__ import annotations

import argparse
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
from tools.goal_16x_local_assets_guarded_alias_ab_validation import DEFAULT_DB_DIR, _configure_db_root, _evaluate_split, _write_csv, _write_json
from tools.goal_17x_false_candidate_precision_guard_dev_oof_shadow import (
    CORE_FAMILIES,
    DEFAULT_BROAD_AUDIT,
    DEFAULT_INDEX,
    DEFAULT_OOF,
    _exact,
    _positive_family_count,
    _safe_int,
    _strong_multifield,
    _very_strong_challenger,
)
from tools.goal_17x_p17b_retention_gap_review import DEFAULT_PREFIX as RETENTION_PREFIX
from tools.goal_17x_precision_hardening_dev_oof_shadow import _normalize_oof_rows


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_REVIEW = AGENT_STATE / "goal_17x_p17b_retention_gap_review_summary.json"
DEFAULT_P17B_SUMMARY = AGENT_STATE / "goal_17x_false_candidate_precision_guard_dev_oof_p17_b_topk1_strong_guard_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_p17b_retention_rescue_dev_oof"
ALL_CANDIDATES = ("P17_E", "P17_F", "P17_G")


@dataclass(frozen=True)
class RescueSpec:
    candidate_id: str
    label: str
    top_k: int
    description: str


def _candidate_spec(candidate_id: str) -> RescueSpec:
    candidate_id = clean_text(candidate_id).upper()
    specs = {
        "P17_E": RescueSpec(
            candidate_id="P17_E",
            label="topk1_plus_rebar_rescue",
            top_k=1,
            description="P17_B trunk plus rebar rescue: exact_name OR support>=3 + overlap>=2.",
        ),
        "P17_F": RescueSpec(
            candidate_id="P17_F",
            label="topk1_plus_pump_rebar_exact_rescue",
            top_k=1,
            description="P17_B trunk plus pump/rebar exact-name rescue only.",
        ),
        "P17_G": RescueSpec(
            candidate_id="P17_G",
            label="p17b_plus_rank_position_cap",
            top_k=2,
            description="P17_B trunk with a tightly capped second candidate under very strong observable evidence.",
        ),
    }
    if candidate_id not in specs:
        raise ValueError(f"unknown candidate {candidate_id!r}; expected one of {', '.join(ALL_CANDIDATES)}")
    return specs[candidate_id]


def _family(row: dict[str, Any]) -> str:
    return clean_text(row.get("oss_recall_query_family") or row.get("query_family"))


def _base_ok(row: dict[str, Any]) -> bool:
    return _family(row) in CORE_FAMILIES


def _p17b_trunk(row: dict[str, Any]) -> bool:
    return _base_ok(row) and (_exact(row) or _strong_multifield(row))


def _rebar_rescue(row: dict[str, Any]) -> bool:
    return _family(row) == "rebar" and (_exact(row) or (_safe_int(row.get("oss_recall_support_count")) >= 3 and _safe_int(row.get("oss_recall_overlap")) >= 2))


def _pump_rebar_exact_rescue(row: dict[str, Any]) -> bool:
    return _family(row) in {"pump", "rebar"} and _exact(row)


def _passes_rescue_filter(spec: RescueSpec, row: dict[str, Any], *, accepted_count: int = 0) -> bool:
    if not _base_ok(row):
        return False
    if spec.candidate_id == "P17_E":
        return _p17b_trunk(row) or _rebar_rescue(row)
    if spec.candidate_id == "P17_F":
        return _p17b_trunk(row) or _pump_rebar_exact_rescue(row)
    if spec.candidate_id == "P17_G":
        if accepted_count == 0:
            return _p17b_trunk(row)
        return _very_strong_challenger(row)
    return False


class P17RetentionRescueSource:
    def __init__(self, delegate: OssRecallPriorSource, spec: RescueSpec) -> None:
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
            top_k=max(top_k * 16, 48),
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            enriched = dict(row)
            enriched["oss_recall_query_family"] = clean_text(query_family)
            if not _passes_rescue_filter(self.spec, enriched, accepted_count=len(output)):
                continue
            output.append(enriched)
            if len(output) >= top_k:
                break
        return output


def _configure_candidate(spec: RescueSpec, index: Path) -> P17RetentionRescueSource:
    config.OSS_RECALL_INDEX_PATH = str(index)
    config.OSS_RECALL_INDEX_TOP_K = spec.top_k
    config.OSS_RECALL_INDEX_MIN_SUPPORT = 2
    config.OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES = 1
    config.OSS_RECALL_INDEX_MIN_OVERLAP = 2
    config.OSS_RECALL_INDEX_INTERVENTION_MODE = "broad"
    config.OSS_RECALL_INDEX_CORE_FAMILIES = tuple(sorted(CORE_FAMILIES))
    reset_oss_recall_prior_source()
    clear_goal_search_cache()
    delegate = OssRecallPriorSource(
        index,
        min_support=2,
        min_source_families=1,
        min_overlap=2,
        intervention_mode="broad",
        core_families=set(CORE_FAMILIES),
    )
    source = P17RetentionRescueSource(delegate, spec)
    oss_recall_prior._SOURCE = source
    return source


def _headline(scorecard: list[dict[str, Any]]) -> dict[str, Any]:
    return next(row for row in scorecard if row["slice"] == "all")


def _candidate_stop_conditions(spec: RescueSpec, headline: dict[str, Any], scorecard: list[dict[str, Any]]) -> list[dict[str, str]]:
    false_candidates = _safe_int(headline.get("prior_false_candidates"))
    positive_candidates = _safe_int(headline.get("prior_positive_candidates"))
    positive_family_count = _positive_family_count(scorecard)
    delta_top1 = _safe_int(headline.get("delta_top1"))
    delta_top5 = _safe_int(headline.get("delta_top5"))
    if spec.candidate_id == "P17_E":
        false_limit = 12
        top5_required = 0
    elif spec.candidate_id == "P17_F":
        false_limit = 10
        top5_required = 0
    else:
        false_limit = 15
        top5_required = 4
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
            "check": "top1_retention",
            "status": "pass" if delta_top1 >= 5 else "fail",
            "evidence": f"delta_top1={delta_top1}; required>=5",
        },
        {
            "candidate": spec.candidate_id,
            "check": "top5_retention",
            "status": "pass" if top5_required == 0 or delta_top5 >= top5_required else "fail",
            "evidence": f"delta_top5={delta_top5}; required>={top5_required}",
        },
        {
            "candidate": spec.candidate_id,
            "check": "false_candidate_budget",
            "status": "pass" if false_candidates <= false_limit else "fail",
            "evidence": f"false_candidates={false_candidates}; limit={false_limit}",
        },
        {
            "candidate": spec.candidate_id,
            "check": "positive_retention",
            "status": "pass" if positive_candidates >= 8 and positive_family_count >= 2 else "fail",
            "evidence": f"positive_candidates={positive_candidates}; positive_family_count={positive_family_count}",
        },
        {"candidate": spec.candidate_id, "check": "online_observable_only", "status": "pass", "evidence": "filter uses OSS candidate fields and query_family only; no expected_id or heldout/hard labels"},
        {"candidate": spec.candidate_id, "check": "default_off_boundary", "status": "pass", "evidence": "config changed in-process only; no GoalSearcher default change"},
    ]


def _decision_from_stops(spec: RescueSpec, stops: list[dict[str, str]]) -> str:
    failed = [row["check"] for row in stops if row["status"] != "pass"]
    if failed:
        return f"{spec.candidate_id.lower()}_no_go_failed_{'_'.join(failed)}"
    return f"{spec.candidate_id.lower()}_passes_dev_oof_retention_rescue_gate"


def _write_candidate_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["headline"]
    lines = [
        f"# 17.22 {report['candidate']} Dev/OOF Retention Rescue Shadow",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        f"- Top1/Top5/Top20/Top80: `{h['delta_top1']}/{h['delta_top5']}/{h['delta_top20']}/{h['delta_top80']}`.",
        f"- wins/losses: `{h['top1_wins']}/{h['top1_losses']}`.",
        f"- generated/positive/false: `{h['prior_generated_candidates']}/{h['prior_positive_candidates']}/{h['prior_false_candidates']}`.",
        f"- false rate: `{h['prior_false_candidate_rate']}`.",
        "",
        "## Stop Conditions",
        "",
        "| check | status | evidence |",
        "|---|---|---|",
    ]
    for row in report["stop_conditions"]:
        lines.append(f"| {row['check']} | {row['status']} | {row['evidence']} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.22 P17_B Retention-Rescue Dev/OOF Matrix",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
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


def run_candidate(spec: RescueSpec, args: argparse.Namespace, rows: list[dict[str, Any]]) -> dict[str, Any]:
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
        "stage": "17.22 P17_B retention-rescue dev/OOF shadow",
        "candidate": spec.candidate_id,
        "label": spec.label,
        "description": spec.description,
        "decision": decision,
        "rows_evaluated": len(rows),
        "contract": {
            "top_k": spec.top_k,
            "min_support": 2,
            "min_source_families": 1,
            "min_overlap": 2,
            "intervention_mode": "broad",
            "core_families": sorted(CORE_FAMILIES),
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
            f"17.22 {spec.candidate_id} ran only dev/OOF baseline_only impacted rows with in-process default-off recall settings. "
            "It did not train, tune, read heldout/hard, default-enable OSS recall, integrate online behavior, or change GoalSearcher defaults."
        ),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    _write_candidate_markdown(summary_md, report)
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
    passed = [row for row in rows if str(row["decision"]).endswith("_passes_dev_oof_retention_rescue_gate")]
    if not passed:
        return "", "no_freeze_candidate_all_retention_rescue_candidates_failed_dev_oof_gate"
    passed.sort(
        key=lambda row: (
            _safe_int(row["delta_top1"]),
            _safe_int(row["delta_top5"]),
            -_safe_int(row["prior_false_candidates"]),
            _safe_int(row["prior_positive_candidates"]),
        ),
        reverse=True,
    )
    return str(passed[0]["candidate"]), "freeze_gate_ready_for_best_retention_rescue_dev_oof_candidate"


def main() -> int:
    parser = argparse.ArgumentParser(description="17.22 dev/OOF-only P17_B retention-rescue shadow matrix")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--broad-row-audit", type=Path, default=DEFAULT_BROAD_AUDIT)
    parser.add_argument("--review-summary", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--candidate", choices=ALL_CANDIDATES + ("all",), default="all")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_PREFIX)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    review = json.loads(args.review_summary.read_text(encoding="utf-8"))
    if review.get("decision") != "p17b_retention_gap_confirmed_redesign_scope_ready":
        raise ValueError(f"unexpected 17.21 decision: {review.get('decision')}")

    _configure_db_root(args.db_dir)
    rows = _normalize_oof_rows(args.oof, args.broad_row_audit)
    candidate_ids = list(ALL_CANDIDATES if args.candidate == "all" else (args.candidate,))
    reports = [run_candidate(_candidate_spec(candidate_id), args, rows) for candidate_id in candidate_ids]
    comparison = [_comparison_row(report) for report in reports]
    stop_rows = [row for report in reports for row in report["stop_conditions"]]
    lead, decision = _select_lead(reports)
    interpretation = (
        f"Best dev/OOF retention-rescue candidate is {lead}. A separate freeze gate is required before any validation request."
        if lead
        else "No retention-rescue candidate passed all dev/OOF gates; keep P17_B as a precision signal and redesign again on dev/OOF."
    )

    aggregate_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    aggregate_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    comparison_csv = args.output_prefix.with_name(args.output_prefix.name + "_comparison.csv")
    stop_csv = args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")
    aggregate = {
        "stage": "17.22 P17_B retention-rescue dev/OOF matrix",
        "decision": decision,
        "lead_candidate": lead,
        "rows_evaluated": len(rows),
        "candidate_reports": [report["artifacts"]["summary_json"] for report in reports],
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
            "17.22 implemented and ran only the fixed P17_E/F/G dev/OOF retention-rescue matrix. "
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
    config.OSS_RECALL_INDEX_ENABLED = False
    reset_oss_recall_prior_source()
    clear_goal_search_cache()
    print(json.dumps({"summary": str(aggregate_json), "decision": decision, "lead_candidate": lead, "comparison": comparison}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
