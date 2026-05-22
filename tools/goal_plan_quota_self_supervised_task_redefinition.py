from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

DEFAULT_LABEL_DIRECTION_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_label_direction_summary.json"
DEFAULT_NUMERIC_MATRIX_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_numeric_matrix_dry_run_summary.json"
DEFAULT_PRETRAIN_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_pretrain_dry_run_summary.json"
DEFAULT_DATA_LINES_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_task_redefinition_data_lines.csv"
DEFAULT_GATES_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_task_redefinition_gates.csv"
DEFAULT_FORBIDDEN_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_task_redefinition_forbidden_actions.csv"
DEFAULT_NEXT_STAGE_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_task_redefinition_next_stage.csv"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_task_redefinition_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_quota_self_supervised_task_redefinition_summary.md"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "path": str(path)}
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["available"] = True
    payload["path"] = str(path)
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _value(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _data_lines(label_summary: dict[str, Any], matrix_summary: dict[str, Any]) -> list[dict[str, Any]]:
    pairs = _value(label_summary, "summary", "pairs", default=0)
    matrix_rows = _value(matrix_summary, "summary", "matrix_rows", default=0)
    feature_count = _value(matrix_summary, "summary", "feature_count", default=0)
    return [
        {
            "data_line": "undirected_contrast_pairs",
            "source": "current_quota_self_supervised_pair_whitelist",
            "current_support": str(pairs),
            "task_type": "undirected_pair_contrast",
            "label_policy": "no_label_1_0; preserve pair_type and contrast_field only",
            "allowed_use": "conflict_detection; subtype_or_param_difference_features; hard_negative_pool_mining; coverage_audit",
            "forbidden_use": "LambdaRank ranking target; Top1 supervision; online rerank model training",
            "required_fields": "province; family; pair_type; contrast_field; candidate_a; candidate_b; subtype_key; param_value",
            "output_artifact": "contrast/conflict matrix or pair feature table",
            "promotion_gate": "pair_order_invariant_features_only",
            "status": "keep_but_relabel",
        },
        {
            "data_line": "query_anchored_ranking_matrix",
            "source": "OSS samples or generated query targets with local quota candidates",
            "current_support": "not_built_in_6_2",
            "task_type": "directed_ranking",
            "label_policy": "label=1 only when expected_id or explicit query target identifies the better local candidate",
            "allowed_use": "LTR training; safety gate calibration; heldout evaluation",
            "forbidden_use": "using unanchored pair order; using foreign province quota_id as target; using bill_name expected_id prior as feature",
            "required_fields": "query_text; province; candidate_top80; expected_id_or_target_param; source_file; project_name; sample_id",
            "output_artifact": "ltr_matrix_<split>.csv + group + group_meta + feature_whitelist",
            "promotion_gate": "query_anchor_present_and_leakage_safe_split",
            "status": "next_build_target",
        },
    ]


def _gates(label_summary: dict[str, Any], pretrain_summary: dict[str, Any]) -> list[dict[str, Any]]:
    direction_verdict = _value(label_summary, "summary", "direction_verdict", default="")
    ranking_allowed = _value(label_summary, "summary", "ranking_supervision_allowed", default=None)
    valid_hit1 = _value(pretrain_summary, "summary", "valid_eval", "hit1_rate", default=None)
    static_random = _value(label_summary, "summary", "static_random_assignment_detected", default=False)
    numeric_balance = _value(label_summary, "summary", "numeric_direction_balance", default=None)
    return [
        {
            "gate": "random_pair_ranking_label_gate",
            "rule": "If direction_verdict is random_order_not_rank_supervision, current positive/negative labels must not enter ranking training.",
            "current_evidence": f"direction_verdict={direction_verdict}; ranking_supervision_allowed={ranking_allowed}",
            "status": "pass_block_ranking_label" if ranking_allowed is False else "needs_review",
            "action": "retire current label=1/0 as ranking target",
        },
        {
            "gate": "source_direction_gate",
            "rule": "Self-supervised generator must not assign label direction from random left/right pair order.",
            "current_evidence": f"static_random_assignment_detected={static_random}",
            "status": "pass_detected_random_source" if static_random else "needs_review",
            "action": "require explicit query or target before directed label generation",
        },
        {
            "gate": "numeric_direction_balance_gate",
            "rule": "Param-direction labels are invalid when positive greater/less is near balanced.",
            "current_evidence": f"numeric_direction_balance={numeric_balance}",
            "status": "pass_detected_unstable_direction" if isinstance(numeric_balance, (int, float)) and abs(float(numeric_balance)) <= 0.1 else "needs_review",
            "action": "keep param pairs as undirected contrast pairs",
        },
        {
            "gate": "pretrain_reuse_gate",
            "rule": "Self-supervised pretrain model is reusable only if validation is clearly above random and direction labels pass audit.",
            "current_evidence": f"valid_hit1_rate={valid_hit1}",
            "status": "fail_do_not_reuse_model" if isinstance(valid_hit1, (int, float)) and float(valid_hit1) < 0.6 else "needs_review",
            "action": "do not wire stage 6.0 model into search",
        },
        {
            "gate": "query_anchor_gate",
            "rule": "Directed ranking rows require query_text plus expected_id or explicit target parameter/tier.",
            "current_evidence": "not_built_in_stage_6_2",
            "status": "required_for_next_stage",
            "action": "build query-anchored matrix generator before next ranking train",
        },
        {
            "gate": "leakage_safe_split_gate",
            "rule": "Directed ranking train/dev/heldout must split by source_file/project_name/sample_id and exclude answer priors.",
            "current_evidence": "policy_from_goal_oss_learning_v1",
            "status": "required_for_next_stage",
            "action": "reuse OSS leakage-safe split discipline",
        },
    ]


def _forbidden_actions() -> list[dict[str, Any]]:
    return [
        {
            "forbidden_action": "train_lambdarank_on_current_pair_label",
            "reason": "Current positive/negative is random generation order, not ranking direction.",
            "replacement": "Use undirected contrast/conflict objective or wait for query-anchored labels.",
        },
        {
            "forbidden_action": "wire_stage_6_0_model_to_search",
            "reason": "Validation Hit1 is random-like and pretrain reuse gate failed.",
            "replacement": "Treat the model as a failed diagnostic artifact only.",
        },
        {
            "forbidden_action": "use_quota_id_or_province_as_training_feature",
            "reason": "Quota ids are province-local and can leak source identity.",
            "replacement": "Keep ids/province in group_meta diagnostics only.",
        },
        {
            "forbidden_action": "generate_directed_label_without_query_anchor",
            "reason": "Without a query or explicit target tier there is no reason A should rank above B.",
            "replacement": "Emit undirected pair or add explicit query target.",
        },
    ]


def _next_stage() -> list[dict[str, Any]]:
    return [
        {
            "stage": "6.3",
            "name": "undirected contrast matrix schema",
            "scope": "Define pair-order-invariant features and conflict labels from existing pairs.",
            "train": "false",
            "output": "schema + coverage report",
        },
        {
            "stage": "6.4",
            "name": "query anchored ranking matrix design",
            "scope": "Map OSS/query samples to Top80 candidate rows with expected_id or explicit target param.",
            "train": "false",
            "output": "generator design + leakage gates",
        },
        {
            "stage": "6.5",
            "name": "query anchored matrix dry run",
            "scope": "Generate dev/heldout/hard numeric LTR matrix from real query samples.",
            "train": "false",
            "output": "loader-readable matrix + diagnostics",
        },
    ]


def _summary(label_summary: dict[str, Any], matrix_summary: dict[str, Any], pretrain_summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "decision": "redefine_self_supervised_pairs_as_undirected_contrast_data",
        "keep_matrix_infrastructure": True,
        "retire_current_pair_label_for_ranking": True,
        "current_pairs": _value(label_summary, "summary", "pairs", default=0),
        "current_matrix_rows": _value(matrix_summary, "summary", "matrix_rows", default=0),
        "current_feature_count": _value(matrix_summary, "summary", "feature_count", default=0),
        "label_direction_verdict": _value(label_summary, "summary", "direction_verdict", default=""),
        "ranking_supervision_allowed": _value(label_summary, "summary", "ranking_supervision_allowed", default=None),
        "pretrain_valid_hit1_rate": _value(pretrain_summary, "summary", "valid_eval", "hit1_rate", default=None),
        "task_lines": 2,
        "passes_task_redefinition_gate": (
            _value(label_summary, "summary", "direction_verdict", default="") == "random_order_not_rank_supervision"
            and _value(label_summary, "summary", "ranking_supervision_allowed", default=True) is False
        ),
        "recommended_next_stage": "Stage 6.3 eval-only undirected contrast matrix schema; no training.",
    }


def _md_table(rows: list[list[Any]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    lines = [
        "| " + " | ".join(str(value) for value in header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(value).replace("\n", " ") for value in row) + " |")
    return "\n".join(lines)


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summary = report["summary"]
    data_lines = report["data_lines"]
    gates = report["gates"]
    forbidden = report["forbidden_actions"]
    next_stage = report["next_stage"]
    lines = [
        "# Goal Self-Supervised Task Redefinition",
        "",
        "Stage 6.2 eval-only draft. It redefines how quota self-supervised pairs may be used after the label-direction audit showed current positive/negative labels are random generation order.",
        "",
        "## Decision",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["decision", summary["decision"]],
                ["keep_matrix_infrastructure", summary["keep_matrix_infrastructure"]],
                ["retire_current_pair_label_for_ranking", summary["retire_current_pair_label_for_ranking"]],
                ["current_pairs", summary["current_pairs"]],
                ["current_matrix_rows", summary["current_matrix_rows"]],
                ["current_feature_count", summary["current_feature_count"]],
                ["label_direction_verdict", summary["label_direction_verdict"]],
                ["pretrain_valid_hit1_rate", summary["pretrain_valid_hit1_rate"]],
                ["passes_task_redefinition_gate", summary["passes_task_redefinition_gate"]],
            ]
        ),
        "",
        "## Data Lines",
        "",
        _md_table(
            [
                ["data_line", "task_type", "label_policy", "allowed_use", "status"],
                *[
                    [row["data_line"], row["task_type"], row["label_policy"], row["allowed_use"], row["status"]]
                    for row in data_lines
                ],
            ]
        ),
        "",
        "## Gates",
        "",
        _md_table(
            [
                ["gate", "status", "action"],
                *[[row["gate"], row["status"], row["action"]] for row in gates],
            ]
        ),
        "",
        "## Forbidden Actions",
        "",
        _md_table(
            [
                ["forbidden_action", "replacement"],
                *[[row["forbidden_action"], row["replacement"]] for row in forbidden],
            ]
        ),
        "",
        "## Next Stage",
        "",
        _md_table(
            [
                ["stage", "name", "train", "output"],
                *[[row["stage"], row["name"], row["train"], row["output"]] for row in next_stage],
            ]
        ),
        "",
        "## Artifacts",
        "",
        _md_table([["artifact", "path"], *[[key, value] for key, value in report["artifacts"].items()]]),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage 6.2 eval-only quota self-supervised task redefinition draft")
    parser.add_argument("--label-direction-json", default=str(DEFAULT_LABEL_DIRECTION_JSON))
    parser.add_argument("--numeric-matrix-json", default=str(DEFAULT_NUMERIC_MATRIX_JSON))
    parser.add_argument("--pretrain-json", default=str(DEFAULT_PRETRAIN_JSON))
    parser.add_argument("--data-lines-csv", default=str(DEFAULT_DATA_LINES_CSV))
    parser.add_argument("--gates-csv", default=str(DEFAULT_GATES_CSV))
    parser.add_argument("--forbidden-csv", default=str(DEFAULT_FORBIDDEN_CSV))
    parser.add_argument("--next-stage-csv", default=str(DEFAULT_NEXT_STAGE_CSV))
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    args = parser.parse_args()

    started = time.perf_counter()
    label_summary = _read_json(Path(args.label_direction_json))
    matrix_summary = _read_json(Path(args.numeric_matrix_json))
    pretrain_summary = _read_json(Path(args.pretrain_json))

    data_lines = _data_lines(label_summary, matrix_summary)
    gates = _gates(label_summary, pretrain_summary)
    forbidden = _forbidden_actions()
    next_stage = _next_stage()
    summary = _summary(label_summary, matrix_summary, pretrain_summary)

    _write_csv(
        Path(args.data_lines_csv),
        data_lines,
        [
            "data_line",
            "source",
            "current_support",
            "task_type",
            "label_policy",
            "allowed_use",
            "forbidden_use",
            "required_fields",
            "output_artifact",
            "promotion_gate",
            "status",
        ],
    )
    _write_csv(Path(args.gates_csv), gates, ["gate", "rule", "current_evidence", "status", "action"])
    _write_csv(Path(args.forbidden_csv), forbidden, ["forbidden_action", "reason", "replacement"])
    _write_csv(Path(args.next_stage_csv), next_stage, ["stage", "name", "scope", "train", "output"])

    report = {
        "stage": "Goal LTR v1 / stage 6.2 quota self-supervised task redefinition draft",
        "eval_only": True,
        "no_training": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "inputs": {
            "label_direction_json": str(Path(args.label_direction_json)),
            "numeric_matrix_json": str(Path(args.numeric_matrix_json)),
            "pretrain_json": str(Path(args.pretrain_json)),
        },
        "summary": summary,
        "data_lines": data_lines,
        "gates": gates,
        "forbidden_actions": forbidden,
        "next_stage": next_stage,
        "artifacts": {
            "data_lines_csv": str(Path(args.data_lines_csv)),
            "gates_csv": str(Path(args.gates_csv)),
            "forbidden_csv": str(Path(args.forbidden_csv)),
            "next_stage_csv": str(Path(args.next_stage_csv)),
            "summary_json": str(Path(args.report_json)),
            "summary_md": str(Path(args.report_md)),
        },
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    _write_json(Path(args.report_json), report)
    _write_markdown(Path(args.report_md), report)
    print(
        json.dumps(
            {
                "summary": {
                    "decision": summary["decision"],
                    "keep_matrix_infrastructure": summary["keep_matrix_infrastructure"],
                    "retire_current_pair_label_for_ranking": summary["retire_current_pair_label_for_ranking"],
                    "current_pairs": summary["current_pairs"],
                    "current_matrix_rows": summary["current_matrix_rows"],
                    "task_lines": summary["task_lines"],
                    "passes_task_redefinition_gate": summary["passes_task_redefinition_gate"],
                    "elapsed_sec": report["elapsed_sec"],
                },
                "artifacts": report["artifacts"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
