from __future__ import annotations

import json
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import DatasetKind, ProviderStatus
from .coverage import summarize_dataset_coverage
from .datasets import load_dataset
from .metrics import aggregate_provider_metrics, compare_providers
from .providers import CandidateProvider
from .reporting import write_reports


UNION_SHADOW_PROVIDER = "production_goal_union_shadow"


def _git_value(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _path_metadata(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "size": path.stat().st_size if exists and path.is_file() else None,
        "modified_ns": path.stat().st_mtime_ns if exists else None,
    }


def _configured_artifacts() -> dict[str, Any]:
    import config

    provinces_root = Path(config.PROVINCES_DB_DIR).resolve()
    province_assets: list[dict[str, Any]] = []
    if provinces_root.is_dir():
        for manifest_path in sorted(provinces_root.glob("*/asset_manifest.json")):
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            province_assets.append(
                {
                    "asset_mode": str(payload.get("asset_mode") or ""),
                    "gate_passed": bool(payload.get("gate_passed")),
                    "manifest_path": str(manifest_path.resolve()),
                    "province": str(payload.get("province") or manifest_path.parent.name),
                }
            )
    paths: dict[str, Any] = {}
    for name in (
        "LTR_V2_MODEL_PATH",
        "LTR_V2_FEATURES_PATH",
        "OSS_RECALL_INDEX_PATH",
    ):
        value = getattr(config, name, "")
        paths[name] = _path_metadata(Path(value)) if value else {
            "path": "",
            "exists": False,
            "size": None,
            "modified_ns": None,
        }
    paths["NATIONAL_INDEX"] = _path_metadata(
        Path(config.DATA_DIR) / "goal_search" / "national_index.sqlite"
    )
    return {
        "provinces_db_dir": str(provinces_root),
        "province_assets": province_assets,
        "paths": paths,
        "flags": {
            name: getattr(config, name, None)
            for name in (
                "HYBRID_TOP_K",
                "RERANKER_TOP_K",
                "LTR_V2_ENABLED",
                "CONSTRAINED_GATED_RANKER_ENABLED",
                "UNIFIED_RANKING_ENABLED",
                "OSS_RECALL_INDEX_ENABLED",
            )
        },
    }


def _runtime_metadata() -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "git_revision": _git_value("rev-parse", "HEAD"),
        "git_status": _git_value("status", "--short"),
        "configured_artifacts": _configured_artifacts(),
    }


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _flatten_stage_rows(
    dataset_name: str,
    provider: str,
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {"dataset": dataset_name, "provider": provider, "stage": stage, **values}
        for stage, values in sorted((report.get("stage_flips") or {}).items())
    ]


def _flatten_slice_rows(
    dataset_name: str,
    provider: str,
    report: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {"dataset": dataset_name, "provider": provider, "slice": key, **values}
        for key, values in sorted((report.get("slices") or {}).items())
    ]


def _dataset_metric_view(
    dataset_kind: DatasetKind,
    report: dict[str, Any],
    cases,
    results,
) -> dict[str, Any]:
    if dataset_kind == DatasetKind.PRIMARY:
        return report
    if dataset_kind == DatasetKind.OSS_DIAGNOSTIC:
        allowed = {
            "total_cases",
            "valid_cases",
            "system_denominator",
            "exclusions",
            "provider_failure_count",
            "provider_failure_rate",
            "recall_at",
            "conditional_top1",
            "mrr",
            "stage_flips",
            "taxonomy_false_veto_count",
            "param_false_hard_fail_count",
            "route_filter_oracle_loss_count",
            "route_filter_oracle_loss_rate",
            "candidate_contract_coverage",
            "trace_complete_rate",
            "slices",
        }
        return {key: value for key, value in report.items() if key in allowed}

    case_by_id = {case.case_id: case for case in cases}
    valid = [
        result
        for result in results
        if result.status in {ProviderStatus.OK, ProviderStatus.TRACE_INCOMPLETE}
    ]
    repaired = sum(
        case_by_id[result.case_id].output_matches(result.final_quota_ids)
        for result in valid
    )
    regression_evaluable = [
        result
        for result in valid
        if bool(case_by_id[result.case_id].metadata.get("baseline_correct"))
    ]
    new_regressions = sum(
        not case_by_id[result.case_id].output_matches(result.final_quota_ids)
        for result in regression_evaluable
    )
    return {
        "total_cases": report["total_cases"],
        "valid_cases": report["valid_cases"],
        "system_denominator": report["system_denominator"],
        "exclusions": report["exclusions"],
        "provider_failure_count": report["provider_failure_count"],
        "provider_failure_rate": report["provider_failure_rate"],
        "repair_count": repaired,
        "repair_rate": round(repaired / len(valid), 6) if valid else 0.0,
        "new_regression_count": new_regressions,
        "new_regression_evaluable_count": len(regression_evaluable),
        "stage_flips": report["stage_flips"],
        "slices": report["slices"],
    }


def run_accuracy_baseline(
    *,
    datasets: dict[str, str | Path],
    output_dir: str | Path,
    providers: Sequence[CandidateProvider],
    min_slice_size: int = 20,
    coverage_requirements: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    dataset_kind_map = {
        "primary": DatasetKind.PRIMARY,
        "oss_diagnostic": DatasetKind.OSS_DIAGNOSTIC,
        "historical_stress": DatasetKind.HISTORICAL_STRESS,
    }
    summary: dict[str, Any] = {"runtime": _runtime_metadata(), "datasets": {}}
    case_rows: list[dict[str, Any]] = []
    stage_rows: list[dict[str, Any]] = []
    slice_rows: list[dict[str, Any]] = []
    comparison_rows: list[dict[str, Any]] = []

    for dataset_name, path in datasets.items():
        loaded = load_dataset(path, dataset_kind_map[dataset_name])
        dataset_coverage_requirements = None
        if coverage_requirements and dataset_name == "primary":
            nested = coverage_requirements.get(dataset_name)
            dataset_coverage_requirements = (
                nested if isinstance(nested, Mapping) else coverage_requirements
            )
        coverage = summarize_dataset_coverage(
            loaded.cases,
            loaded.dataset_kind,
            dataset_coverage_requirements,
        )
        if loaded.rejection_counts:
            rejection_reasons = [
                f"dataset_rejection:{reason}={count}"
                for reason, count in sorted(loaded.rejection_counts.items())
                if count
            ]
            coverage = {
                **coverage,
                "scope": "slice",
                "system_baseline_eligible": False,
                "gate_status": (
                    "invalid_contract"
                    if coverage.get("gate_status") == "invalid_contract"
                    else "failed"
                ),
                "reasons": [*coverage.get("reasons", []), *rejection_reasons],
            }
        provider_results = {
            provider.name: provider.run(loaded.cases) for provider in providers
        }
        full_provider_metrics = {
            name: aggregate_provider_metrics(
                loaded.cases,
                results,
                min_slice_size=min_slice_size,
            )
            for name, results in provider_results.items()
        }
        provider_metrics = {
            name: _dataset_metric_view(
                loaded.dataset_kind,
                full_provider_metrics[name],
                loaded.cases,
                results,
            )
            for name, results in provider_results.items()
        }
        if UNION_SHADOW_PROVIDER in provider_results:
            from .union_shadow import aggregate_union_shadow_metrics

            provider_metrics[UNION_SHADOW_PROVIDER]["union_shadow_diagnostics"] = (
                aggregate_union_shadow_metrics(
                    loaded.cases,
                    provider_results[UNION_SHADOW_PROVIDER],
                )
            )
        comparison = compare_providers(loaded.cases, provider_results)
        summary["datasets"][dataset_name] = {
            "path": str(loaded.path),
            "content_sha256": loaded.content_sha256,
            "total_rows": loaded.total_rows,
            "accepted_cases": len(loaded.cases),
            "rejection_counts": loaded.rejection_counts,
            "coverage": coverage,
            "system_baseline_eligible": coverage["system_baseline_eligible"],
            "headline_metrics_allowed": coverage["system_baseline_eligible"],
            "providers": provider_metrics,
            "provider_comparison": {
                key: value for key, value in comparison.items() if key != "cases"
            },
        }
        case_by_id = {case.case_id: case for case in loaded.cases}
        for provider_name, results in provider_results.items():
            stage_rows.extend(
                _flatten_stage_rows(
                    dataset_name,
                    provider_name,
                    full_provider_metrics[provider_name],
                )
            )
            slice_rows.extend(
                _flatten_slice_rows(
                    dataset_name,
                    provider_name,
                    full_provider_metrics[provider_name],
                )
            )
            for result in results:
                case = case_by_id.get(result.case_id)
                if case is None:
                    continue
                case_rows.append(
                    {
                        "dataset": dataset_name,
                        "case": _jsonable(asdict(case)),
                        "provider_result": _jsonable(asdict(result)),
                    }
                )
        comparison_rows.extend(
            {"dataset": dataset_name, **row} for row in comparison["cases"]
        )

    payload = {
        "summary": summary,
        "cases": case_rows,
        "stage_attribution": stage_rows,
        "slice_metrics": slice_rows,
        "provider_comparison": comparison_rows,
    }
    write_reports(output_dir, payload)
    return payload
