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
from tools.goal_17x_precision_hardening_dev_oof_shadow import DEFAULT_BROAD_AUDIT, DEFAULT_INDEX, DEFAULT_OOF, _normalize_oof_rows


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_SCOPE = AGENT_STATE / "goal_17x_false_candidate_precision_guard_redesign_scope_summary.json"
DEFAULT_H17A_SUMMARY = AGENT_STATE / "goal_17x_precision_hardening_dev_oof_h17_a_lossless_family_veto_pipe_support_summary.json"
DEFAULT_PREFIX = AGENT_STATE / "goal_17x_false_candidate_precision_guard_dev_oof"
ALL_CANDIDATES = ("P17_A", "P17_B", "P17_C", "P17_D")
CORE_FAMILIES = frozenset({"concrete", "pump", "rebar"})


@dataclass(frozen=True)
class P17Spec:
    candidate_id: str
    label: str
    top_k: int
    description: str


def _safe_int(value: object) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _candidate_spec(candidate_id: str) -> P17Spec:
    candidate_id = clean_text(candidate_id).upper()
    specs = {
        "P17_A": P17Spec(
            candidate_id="P17_A",
            label="strong_multifield_guard",
            top_k=3,
            description="H17_A families with exact-name or strong multi-field evidence.",
        ),
        "P17_B": P17Spec(
            candidate_id="P17_B",
            label="topk1_strong_guard",
            top_k=1,
            description="P17_A evidence guard with at most one injected OSS candidate per row.",
        ),
        "P17_C": P17Spec(
            candidate_id="P17_C",
            label="family_specific_guard",
            top_k=3,
            description="Family-specific concrete/pump/rebar precision guard.",
        ),
        "P17_D": P17Spec(
            candidate_id="P17_D",
            label="observable_rank1_veto_proxy",
            top_k=3,
            description="Strict online-observable challenger evidence proxy for rank1 protection.",
        ),
    }
    if candidate_id not in specs:
        raise ValueError(f"unknown candidate {candidate_id!r}; expected one of {', '.join(ALL_CANDIDATES)}")
    return specs[candidate_id]


def _base_family_ok(row: dict[str, Any]) -> bool:
    family = clean_text(row.get("oss_recall_query_family") or row.get("query_family"))
    return family in CORE_FAMILIES


def _exact(row: dict[str, Any]) -> bool:
    return bool(row.get("oss_recall_exact_name"))


def _strong_multifield(row: dict[str, Any]) -> bool:
    return (
        _safe_int(row.get("oss_recall_source_family_count")) >= 2
        and _safe_int(row.get("oss_recall_support_count")) >= 4
        and _safe_int(row.get("oss_recall_overlap")) >= 3
        and _safe_int(row.get("oss_recall_quota_name_overlap")) >= 1
    )


def _very_strong_challenger(row: dict[str, Any]) -> bool:
    return _exact(row) or (
        _safe_int(row.get("oss_recall_source_family_count")) >= 2
        and _safe_int(row.get("oss_recall_support_count")) >= 6
        and _safe_int(row.get("oss_recall_overlap")) >= 4
    )


def _family_specific(row: dict[str, Any]) -> bool:
    family = clean_text(row.get("oss_recall_query_family") or row.get("query_family"))
    if family == "concrete":
        return (
            _safe_int(row.get("oss_recall_source_family_count")) >= 2
            and _safe_int(row.get("oss_recall_support_count")) >= 4
            and _safe_int(row.get("oss_recall_overlap")) >= 3
        )
    if family in {"pump", "rebar"}:
        return _exact(row) or (
            _safe_int(row.get("oss_recall_support_count")) >= 3
            and _safe_int(row.get("oss_recall_overlap")) >= 2
        )
    return False


def _passes_p17_filter(spec: P17Spec, row: dict[str, Any]) -> bool:
    if not _base_family_ok(row):
        return False
    if spec.candidate_id in {"P17_A", "P17_B"}:
        return _exact(row) or _strong_multifield(row)
    if spec.candidate_id == "P17_C":
        return _family_specific(row)
    if spec.candidate_id == "P17_D":
        return _very_strong_challenger(row)
    return False


class P17PrecisionRecallSource:
    def __init__(self, delegate: OssRecallPriorSource, spec: P17Spec) -> None:
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
            top_k=max(top_k * 12, 36),
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            enriched = dict(row)
            enriched["oss_recall_query_family"] = clean_text(query_family)
            if not _passes_p17_filter(self.spec, enriched):
                continue
            output.append(enriched)
            if len(output) >= top_k:
                break
        return output


def _configure_candidate(spec: P17Spec, index: Path) -> P17PrecisionRecallSource:
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
    source = P17PrecisionRecallSource(delegate, spec)
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


def _candidate_stop_conditions(
    spec: P17Spec,
    headline: dict[str, Any],
    scorecard: list[dict[str, Any]],
    *,
    h17a_false: int,
) -> list[dict[str, str]]:
    false_candidates = _safe_int(headline.get("prior_false_candidates"))
    positive_candidates = _safe_int(headline.get("prior_positive_candidates"))
    positive_family_count = _positive_family_count(scorecard)
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
            "check": "h17a_lift_preservation",
            "status": "pass" if _safe_int(headline.get("delta_top1")) >= 2 and _safe_int(headline.get("delta_top5")) >= 3 else "fail",
            "evidence": f"delta_top1={headline.get('delta_top1')}; delta_top5={headline.get('delta_top5')}",
        },
        {
            "candidate": spec.candidate_id,
            "check": "false_candidate_reduction",
            "status": "pass" if false_candidates < h17a_false and false_candidates <= 25 else "fail",
            "evidence": f"false_candidates={false_candidates}; h17a_false={h17a_false}; preferred_max=25",
        },
        {
            "candidate": spec.candidate_id,
            "check": "positive_evidence_retention",
            "status": "pass" if positive_candidates >= 7 and positive_family_count >= 2 else "fail",
            "evidence": f"positive_candidates={positive_candidates}; positive_family_count={positive_family_count}",
        },
        {"candidate": spec.candidate_id, "check": "online_observable_only", "status": "pass", "evidence": "filter uses OSS candidate fields and query_family only; no expected_id or heldout/hard labels"},
        {"candidate": spec.candidate_id, "check": "default_off_boundary", "status": "pass", "evidence": "config changed in-process only; no GoalSearcher default change"},
    ]


def _decision_from_stops(spec: P17Spec, stops: list[dict[str, str]]) -> str:
    failed = [row["check"] for row in stops if row["status"] != "pass"]
    if failed:
        return f"{spec.candidate_id.lower()}_no_go_failed_{'_'.join(failed)}"
    return f"{spec.candidate_id.lower()}_passes_dev_oof_precision_guard_gate"


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    lines = [
        "# 17.20 P17 Dev/OOF Precision Guard Shadow Matrix",
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


def _write_candidate_markdown(path: Path, report: dict[str, Any]) -> None:
    h = report["headline"]
    lines = [
        f"# 17.20 {report['candidate']} Dev/OOF Precision Guard Shadow",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Decision",
        "",
        f"**{report['decision']}**",
        "",
        "## Headline",
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
    lines.extend(["", "## Anti-Drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_candidate(spec: P17Spec, args: argparse.Namespace, rows: list[dict[str, Any]], h17a_false: int) -> dict[str, Any]:
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
    stops = _candidate_stop_conditions(spec, headline, scorecard, h17a_false=h17a_false)
    decision = _decision_from_stops(spec, stops)
    summary_json = prefix.with_name(prefix.name + "_summary.json")
    summary_md = prefix.with_name(prefix.name + "_summary.md")
    scorecard_csv = prefix.with_name(prefix.name + "_scorecard.csv")
    row_csv = prefix.with_name(prefix.name + "_row_audit.csv")
    stop_csv = prefix.with_name(prefix.name + "_stop_conditions.csv")
    report = {
        "stage": "17.20 fixed P17 dev/OOF precision guard shadow",
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
            f"17.20 {spec.candidate_id} ran only dev/OOF baseline_only impacted rows with in-process default-off recall settings. "
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
    passed = [row for row in rows if str(row["decision"]).endswith("_passes_dev_oof_precision_guard_gate")]
    if not passed:
        return "", "no_freeze_candidate_all_p17_candidates_failed_dev_oof_gate"
    passed.sort(
        key=lambda row: (
            _safe_int(row["delta_top1"]),
            _safe_int(row["delta_top5"]),
            -_safe_int(row["prior_false_candidates"]),
            _safe_int(row["prior_positive_candidates"]),
        ),
        reverse=True,
    )
    return str(passed[0]["candidate"]), "freeze_gate_ready_for_best_p17_dev_oof_candidate"


def main() -> int:
    parser = argparse.ArgumentParser(description="17.20 dev/OOF-only P17 false-candidate precision guard shadow matrix")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--broad-row-audit", type=Path, default=DEFAULT_BROAD_AUDIT)
    parser.add_argument("--scope-summary", type=Path, default=DEFAULT_SCOPE)
    parser.add_argument("--h17a-summary", type=Path, default=DEFAULT_H17A_SUMMARY)
    parser.add_argument("--candidate", choices=ALL_CANDIDATES + ("all",), default="all")
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_PREFIX)
    parser.add_argument("--progress-every", type=int, default=10)
    args = parser.parse_args()

    scope = json.loads(args.scope_summary.read_text(encoding="utf-8"))
    if scope.get("decision") != "scope_locked_request_explicit_dev_oof_precision_guard_execution_go":
        raise ValueError(f"unexpected 17.19 scope decision: {scope.get('decision')}")
    h17a = json.loads(args.h17a_summary.read_text(encoding="utf-8"))
    h17a_false = _safe_int(h17a["headline"].get("prior_false_candidates"))

    _configure_db_root(args.db_dir)
    rows = _normalize_oof_rows(args.oof, args.broad_row_audit)
    candidate_ids = list(ALL_CANDIDATES if args.candidate == "all" else (args.candidate,))
    reports = [run_candidate(_candidate_spec(candidate_id), args, rows, h17a_false) for candidate_id in candidate_ids]
    comparison = [_comparison_row(report) for report in reports]
    stop_rows = [row for report in reports for row in report["stop_conditions"]]
    lead, decision = _select_lead(reports)
    interpretation = (
        f"Best dev/OOF P17 candidate is {lead}. A separate freeze gate is required before any validation request."
        if lead
        else "No P17 candidate passed all dev/OOF precision guard gates. Keep H17_A stopped for release and redesign again on dev/OOF."
    )

    aggregate_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    aggregate_md = args.output_prefix.with_name(args.output_prefix.name + "_summary.md")
    comparison_csv = args.output_prefix.with_name(args.output_prefix.name + "_comparison.csv")
    stop_csv = args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")
    aggregate = {
        "stage": "17.20 fixed P17 dev/OOF precision guard shadow matrix",
        "decision": decision,
        "lead_candidate": lead,
        "rows_evaluated": len(rows),
        "h17a_baseline_headline": h17a["headline"],
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
            "17.20 implemented and ran only the fixed P17_A/B/C/D dev/OOF precision-guard shadow matrix. "
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
