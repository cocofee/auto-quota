import argparse
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.build_global_repair_decision import (
    build_next_action,
    build_rows,
    build_summary,
    _expected_ids,
    _id_prefix,
    _iter_latest_records,
    _is_wrong,
    _sample_id,
    _selected_id,
)


SCHEMA_VERSION = "v36_gate.v1"
ARTIFACT_PREFIXES = (
    "reports/attribution/",
    "reports/agent_state/",
    "output/",
    "models/",
    "tmp/",
    "test_artifacts/",
)
ARTIFACT_NAMES = {
    "diff_code.txt",
}
GIANT_OWNER_FILES = {
    "src/ltr_ranker.py",
    "src/query_builder.py",
    "src/param_validator.py",
    "src/match_engine.py",
    "web/backend/app/api/openclaw.py",
    "web/backend/app/api/material_price.py",
}
DEFAULT_GIANT_BRIDGE_LINE_BUDGET = 25
OWNER_BOUNDARY_PATTERN = "v36_p0_owner_boundary_*.json"
MOJIBAKE_MARKERS = (
    "\u951b", "\u9225", "\u9346", "\u7ee0", "\u7f01", "\u934a", "\u7459", "\u9422",
    "\u5a34", "\u95ab", "\u95c0", "\u95bf", "\u9363", "\u7035", "\u95bd", "\u942a",
    "\u942a", "\u942a", "\u9359", "\u5bee", "\u93c1", "\u93c9", "\u8113",
)
SECRET_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*=\s*['\"][^'\"]{8,}['\"]"),
    re.compile(r"(?i)verify\s*=\s*false"),
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _repo_root(root: Path | None = None) -> Path:
    return (root or Path.cwd()).resolve()


def _rel(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_git(root: Path, args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
    except FileNotFoundError:
        return 127, "", "git executable not found"
    return proc.returncode, proc.stdout, proc.stderr


def _parse_numstat(stdout: str) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for line in stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added_raw, deleted_raw, path = parts[0], parts[1], parts[-1]
        if " => " in path:
            path = path.split(" => ", 1)[1].strip("{}")
        normalized = path.replace("\\", "/").strip('"')
        if normalized not in GIANT_OWNER_FILES:
            continue
        added = 0 if added_raw == "-" else int(added_raw or 0)
        deleted = 0 if deleted_raw == "-" else int(deleted_raw or 0)
        current = changes.setdefault(normalized, {"path": normalized, "added_lines": 0, "deleted_lines": 0})
        current["added_lines"] += added
        current["deleted_lines"] += deleted
    return changes


def _giant_file_change_summary(root: Path, giant_paths: list[str]) -> dict[str, Any]:
    changes: dict[str, dict[str, Any]] = {}
    for args in (["diff", "--numstat", "--", *sorted(GIANT_OWNER_FILES)], ["diff", "--cached", "--numstat", "--", *sorted(GIANT_OWNER_FILES)]):
        code, stdout, _ = _run_git(root, args)
        if code != 0:
            continue
        for path, change in _parse_numstat(stdout).items():
            current = changes.setdefault(path, {"path": path, "added_lines": 0, "deleted_lines": 0})
            current["added_lines"] += int(change.get("added_lines", 0))
            current["deleted_lines"] += int(change.get("deleted_lines", 0))

    for path in giant_paths:
        changes.setdefault(path, {"path": path, "added_lines": 0, "deleted_lines": 0})
    changed = sorted(changes.values(), key=lambda item: item["path"])
    return {
        "changed_files": changed,
        "max_added_lines": max((int(item.get("added_lines", 0)) for item in changed), default=0),
    }


def _find_owner_boundary_manifest(root: Path) -> dict[str, Any]:
    candidates = sorted((root / "reports" / "attribution").glob(OWNER_BOUNDARY_PATTERN), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if payload.get("p0_remediation_target") != "owner_boundary":
            continue
        allowed = payload.get("allowed_bridge_changes") if isinstance(payload, dict) else {}
        budget = DEFAULT_GIANT_BRIDGE_LINE_BUDGET
        if isinstance(allowed, dict):
            try:
                budget = int(allowed.get("max_new_lines_in_any_giant_owner_file", budget))
            except (TypeError, ValueError):
                budget = DEFAULT_GIANT_BRIDGE_LINE_BUDGET
        return {
            "status": "present",
            "path": _rel(root, path),
            "max_new_lines_in_any_giant_owner_file": budget,
        }
    return {
        "status": "missing",
        "path": "",
        "max_new_lines_in_any_giant_owner_file": DEFAULT_GIANT_BRIDGE_LINE_BUDGET,
    }


def _git_status_entries(root: Path) -> list[dict[str, str]]:
    top_code, top_stdout, _ = _run_git(root, ["rev-parse", "--show-toplevel"])
    if top_code != 0:
        return []
    try:
        if Path(top_stdout.strip()).resolve() != root.resolve():
            return []
    except OSError:
        return []
    code, stdout, stderr = _run_git(root, ["status", "--short"])
    if code != 0:
        return [{"status": "!!", "path": "", "error": stderr.strip() or stdout.strip()}]
    entries: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        status = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        entries.append({"status": status, "path": path.replace("\\", "/")})
    return entries


def _is_artifact_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = Path(normalized).name
    return (
        normalized.startswith(ARTIFACT_PREFIXES)
        or name in ARTIFACT_NAMES
        or normalized.endswith((".pid", ".stdout.log", ".stderr.log"))
        or normalized.startswith("data/ltr_")
    )


def _scan_changed_text_risks(root: Path, entries: list[dict[str, str]]) -> dict[str, Any]:
    risky_paths: list[str] = []
    artifact_mojibake_paths: list[str] = []
    skipped_large: list[str] = []
    for entry in entries:
        raw_path = entry.get("path") or ""
        if not raw_path:
            continue
        path = root / raw_path
        if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".json", ".yaml", ".yml", ".txt"}:
            continue
        try:
            if path.stat().st_size > 1_000_000:
                skipped_large.append(raw_path)
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        has_secret = any(pattern.search(text) for pattern in SECRET_PATTERNS)
        has_mojibake = any(marker in text for marker in MOJIBAKE_MARKERS)
        if has_secret or (has_mojibake and not _is_artifact_path(raw_path)):
            risky_paths.append(raw_path)
        elif has_mojibake:
            artifact_mojibake_paths.append(raw_path)
    return {
        "status": "warn" if risky_paths else "pass",
        "paths": risky_paths,
        "artifact_mojibake_paths": artifact_mojibake_paths[:20],
        "skipped_large_text_files": skipped_large[:20],
    }


def _latest_existing(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.exists()]
    if not existing:
        return None
    return max(existing, key=lambda path: path.stat().st_mtime)


def _find_baseline_snapshot(root: Path) -> dict[str, Any]:
    candidates = list((root / "eval" / "baselines").glob("*.json")) if (root / "eval" / "baselines").exists() else []
    candidates.extend((root / "reports" / "attribution").glob("baseline_*.json"))
    latest = _latest_existing(candidates)
    code, commit, _ = _run_git(root, ["rev-parse", "--short", "HEAD"])
    return {
        "status": "present" if latest else "missing",
        "path": _rel(root, latest) if latest else "",
        "commit": commit.strip() if code == 0 else "",
    }


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _full_global_result_summary(attr_payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "total": int(attr_payload.get("total", 0) or 0),
        "wrong_total": int(attr_payload.get("wrong_total", 0) or 0),
        "overall_hit_rate": float(attr_payload.get("overall_hit_rate", 0.0) or 0.0),
        "recall_hit_rate": float(attr_payload.get("recall_hit_rate", 0.0) or 0.0),
        "r_counts": dict(attr_payload.get("counts") or {}),
    }


def _find_full_asset_error_input(root: Path, attribution: Path, summary: Path) -> dict[str, Any] | None:
    asset_root = root / "output" / "benchmark_assets" / "global_repair_v36_full"
    manifest = asset_root / "manifest.json"
    if not attribution.exists() or not manifest.exists():
        return None
    attr_payload = _read_json_file(attribution)
    manifest_payload = _read_json_file(manifest)
    counts = manifest_payload.get("counts") if isinstance(manifest_payload.get("counts"), dict) else {}
    files = manifest_payload.get("files") if isinstance(manifest_payload.get("files"), dict) else {}
    all_errors_raw = str(files.get("all_errors", "") or "")
    if all_errors_raw:
        all_errors = (root / all_errors_raw).resolve() if not Path(all_errors_raw).is_absolute() else Path(all_errors_raw)
    else:
        all_errors = asset_root / "all_errors.jsonl"
    if not all_errors.exists():
        all_errors = asset_root / "all_errors.jsonl"
    try:
        wrong_total = int(attr_payload.get("wrong_total", -1))
        all_errors_count = int(counts.get("all_errors", -2))
    except (TypeError, ValueError):
        return None
    if wrong_total < 0 or all_errors_count != wrong_total or not all_errors.exists():
        return None
    return {
        "status": "present",
        "input_freshness": "fresh_asset",
        "latest_path": _rel(root, all_errors),
        "attribution_path": _rel(root, attribution),
        "summary_path": _rel(root, summary) if summary.exists() else "",
        "asset_manifest_path": _rel(root, manifest),
        "reason": "latest_missing_using_asset_all_errors",
        "full_global_result": _full_global_result_summary(attr_payload),
    }


def _find_global_input(root: Path) -> dict[str, Any]:
    latest = root / "reports" / "attribution" / "global_repair_v36_full_latest.json"
    attribution = root / "reports" / "attribution" / "global_repair_v36_full_attribution.json"
    summary = root / "reports" / "attribution" / "global_repair_v36_full_summary.json"
    if latest.exists() and attribution.exists():
        attr_payload = _read_json_file(attribution)
        return {
            "status": "present",
            "input_freshness": "fresh",
            "latest_path": _rel(root, latest),
            "attribution_path": _rel(root, attribution),
            "summary_path": _rel(root, summary) if summary.exists() else "",
            "reason": "found v36 full/global output",
            "full_global_result": _full_global_result_summary(attr_payload) if attr_payload else {},
        }

    full_asset_input = _find_full_asset_error_input(root, attribution, summary)
    if full_asset_input:
        return full_asset_input

    legacy_latest = root / "output" / "benchmark_assets" / "ltr_v2_full_20260422" / "all_errors.jsonl"
    legacy_attr = root / "reports" / "attribution" / "ltr_v2_full_20260422.json"
    if legacy_latest.exists() and legacy_attr.exists():
        return {
            "status": "present",
            "input_freshness": "stale",
            "latest_path": _rel(root, legacy_latest),
            "attribution_path": _rel(root, legacy_attr),
            "summary_path": "",
            "reason": "using legacy full benchmark input; v36 full output not found",
        }

    return {
        "status": "missing",
        "input_freshness": "missing",
        "latest_path": "",
        "attribution_path": "",
        "summary_path": "",
        "reason": "no qualified full/global latest plus attribution pair found",
    }


def _pending_summary(root: Path) -> dict[str, Any]:
    path = root / "reports" / "agent_state" / "v36_pending_full_validation.json"
    if not path.exists():
        return {"status": "none", "path": _rel(root, path), "total": 0, "pending": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "invalid", "path": _rel(root, path), "error": str(exc), "total": 0, "pending": 0}
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return {"status": "invalid", "path": _rel(root, path), "error": "entries is not a list", "total": 0, "pending": 0}
    pending = [entry for entry in entries if isinstance(entry, dict) and entry.get("status") == "pending_full_validation"]
    return {"status": "present", "path": _rel(root, path), "total": len(entries), "pending": len(pending)}


def _has_complete_pure_search_metrics(root: Path) -> bool:
    path = root / "reports" / "attribution" / "pure_search_diagnosis.json"
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    metrics = payload.get("pure_search_metrics") if isinstance(payload, dict) else None
    if not isinstance(metrics, dict):
        return False
    required = (
        "recall_at_k",
        "rank_at_k",
        "validator_veto_rate",
        "route_filter_loss",
        "prior_candidates_delta",
        "latency_breakdown_ms",
    )
    return all(metrics.get(key) is not None for key in required)


def _as_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _record_candidate_ids(record: dict[str, Any]) -> list[str]:
    explicit = _as_string_list(record.get("all_candidate_ids") or record.get("recall_topk_ids"))
    if explicit:
        return explicit
    snapshots = record.get("candidate_snapshots")
    if isinstance(snapshots, list):
        ids = [str(item.get("quota_id") or "").strip() for item in snapshots if isinstance(item, dict)]
        if any(ids):
            return [item for item in ids if item]
    retrieved = record.get("retrieved_candidates")
    if isinstance(retrieved, list):
        ids = [str(item.get("quota_id") or "").strip() for item in retrieved if isinstance(item, dict)]
        return [item for item in ids if item]
    return []


def _candidate_snapshots(record: dict[str, Any]) -> list[dict[str, Any]]:
    snapshots = record.get("candidate_snapshots")
    if isinstance(snapshots, list):
        return [item for item in snapshots if isinstance(item, dict)]
    retrieved = record.get("retrieved_candidates")
    if isinstance(retrieved, list):
        return [item for item in retrieved if isinstance(item, dict)]
    return []


def _rank_in_ids(candidate_ids: list[str], expected: set[str]) -> int:
    for index, candidate_id in enumerate(candidate_ids, start=1):
        if candidate_id in expected:
            return index
    return -1


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _snapshot_rank(snapshot: dict[str, Any], names: tuple[str, ...]) -> int | None:
    feature_snapshot = snapshot.get("ltr_feature_snapshot")
    candidates = [snapshot]
    if isinstance(feature_snapshot, dict):
        candidates.append(feature_snapshot)
    for candidate in candidates:
        for name in names:
            value = candidate.get(name)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
    return None


def _expected_snapshots(record: dict[str, Any], expected: set[str]) -> list[dict[str, Any]]:
    return [
        snapshot
        for snapshot in _candidate_snapshots(record)
        if str(snapshot.get("quota_id") or "").strip() in expected
    ]


def _has_validator_veto(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("param_match") is False:
        return True
    if snapshot.get("hard_conflict") is True:
        return True
    reasoning = snapshot.get("reasoning")
    if isinstance(reasoning, dict):
        if reasoning.get("param_match") is False:
            return True
        layers = reasoning.get("layers")
        if isinstance(layers, dict):
            for layer in layers.values():
                if isinstance(layer, dict) and layer.get("hard_conflict") is True:
                    return True
    breakdown = snapshot.get("rank_score_breakdown")
    if isinstance(breakdown, dict):
        structured = breakdown.get("structured")
        if isinstance(structured, dict):
            flags = structured.get("flags")
            if isinstance(flags, dict) and (
                flags.get("hard_conflict") is True or flags.get("fatal_rank_conflict") is True
            ):
                return True
    return False


def _summarize_pure_search_scope(pairs: list[tuple[dict[str, Any], dict[str, str]]]) -> dict[str, Any]:
    total = len(pairs)
    raw_ranks: list[int] = []
    hybrid_ranks: list[int] = []
    bm25_ranks: list[int] = []
    ltr_ranks: list[int] = []
    selected_prefixes: Counter[str] = Counter()
    expected_prefixes: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    stage_top1_hits: Counter[str] = Counter()
    validator_present = 0
    validator_veto = 0
    zero_candidate_count = 0
    records_with_prior_candidate = 0
    examples: list[dict[str, Any]] = []

    for record, row in pairs:
        expected = set(_expected_ids(record))
        selected = _selected_id(record)
        candidate_ids = _record_candidate_ids(record)
        if not candidate_ids:
            zero_candidate_count += 1
        raw_rank = _rank_in_ids(candidate_ids, expected)
        raw_ranks.append(raw_rank)
        selected_prefix = _id_prefix(selected) or "unknown"
        expected_prefix = "|".join(sorted({_id_prefix(item) for item in expected if _id_prefix(item)})) or "unknown"
        selected_prefixes[selected_prefix] += 1
        expected_prefixes[expected_prefix] += 1
        transitions[f"{selected_prefix}->{expected_prefix}"] += 1

        for stage in ("pre_ltr_top1_id", "post_ltr_top1_id", "post_final_top1_id"):
            if str(record.get(stage) or "").strip() in expected:
                stage_top1_hits[stage] += 1

        expected_candidates = _expected_snapshots(record, expected)
        if expected_candidates:
            validator_present += 1
            if any(_has_validator_veto(snapshot) for snapshot in expected_candidates):
                validator_veto += 1
            for names, ranks in (
                (("hybrid_rank",), hybrid_ranks),
                (("bm25_rank",), bm25_ranks),
                (("ltr_rank", "rank"), ltr_ranks),
            ):
                found = [_snapshot_rank(snapshot, names) for snapshot in expected_candidates]
                found = [rank for rank in found if rank is not None]
                if found:
                    ranks.append(min(found))

        if any(
            "prior" in str(snapshot.get("match_source") or "").lower()
            or "prior" in str(snapshot.get("knowledge_prior_sources") or "").lower()
            for snapshot in _candidate_snapshots(record)
        ):
            records_with_prior_candidate += 1

        if len(examples) < 10:
            examples.append(
                {
                    "sample_id": row.get("sample_id", ""),
                    "expected_ids": sorted(expected),
                    "selected_id": selected,
                    "raw_rank": raw_rank,
                    "candidate_count": len(candidate_ids),
                    "error_stage": row.get("error_stage", ""),
                    "attribution_category": row.get("attribution_category", ""),
                }
            )

    def _rate(count: int, denom: int = total) -> float:
        return round(count / denom, 4) if denom else 0.0

    def _rank_counts(ranks: list[int]) -> dict[str, int]:
        return {
            "at_1": sum(1 for rank in ranks if rank == 1),
            "at_5": sum(1 for rank in ranks if 1 <= rank <= 5),
            "at_10": sum(1 for rank in ranks if 1 <= rank <= 10),
            "at_20": sum(1 for rank in ranks if 1 <= rank <= 20),
            "missing": sum(1 for rank in ranks if rank < 1),
        }

    raw_counts = _rank_counts(raw_ranks)
    missing_raw = raw_counts["missing"]
    return {
        "sample_total": total,
        "recall_at_k": {
            "raw_candidate_top1": {"hit_count": raw_counts["at_1"], "hit_rate": _rate(raw_counts["at_1"])},
            "raw_candidate_top5": {"hit_count": raw_counts["at_5"], "hit_rate": _rate(raw_counts["at_5"])},
            "raw_candidate_top10": {"hit_count": raw_counts["at_10"], "hit_rate": _rate(raw_counts["at_10"])},
            "raw_candidate_top20": {"hit_count": raw_counts["at_20"], "hit_rate": _rate(raw_counts["at_20"])},
            "missing_count": missing_raw,
            "missing_rate": _rate(missing_raw),
        },
        "rank_at_k": {
            "raw_candidate_rank": raw_counts,
            "hybrid_rank": _rank_counts(hybrid_ranks),
            "bm25_rank": _rank_counts(bm25_ranks),
            "ltr_rank": _rank_counts(ltr_ranks),
            "stage_top1_hits": dict(stage_top1_hits),
        },
        "validator_veto_rate": {
            "checked_candidate_count": validator_present,
            "veto_count": validator_veto,
            "veto_rate": _rate(validator_veto, validator_present),
        },
        "route_filter_loss": {
            "missing_candidate_count": missing_raw,
            "missing_candidate_rate": _rate(missing_raw),
            "zero_candidate_count": zero_candidate_count,
            "top_selected_prefixes": dict(selected_prefixes.most_common(10)),
            "top_expected_prefixes": dict(expected_prefixes.most_common(10)),
            "top_prefix_transitions": dict(transitions.most_common(10)),
        },
        "prior_candidates_delta": {
            "status": "not_available_from_static_latest",
            "records_with_prior_candidate": records_with_prior_candidate,
            "note": "Run paired benchmark variants to compute an A/B prior delta.",
        },
        "latency_breakdown_ms": {
            "status": "not_available_in_static_latest",
            "available": False,
            "note": "The selected latest artifact has no per-stage timing fields.",
        },
        "examples": examples,
    }


def _classify_bottleneck(metrics: dict[str, Any]) -> str:
    recall = metrics.get("recall_at_k", {})
    route_loss = metrics.get("route_filter_loss", {})
    veto = metrics.get("validator_veto_rate", {})
    missing_rate = _number(recall.get("missing_rate") or route_loss.get("missing_candidate_rate")) or 0.0
    veto_rate = _number(veto.get("veto_rate")) or 0.0
    if missing_rate >= 0.7:
        return "candidate_recall_or_route_filter_loss"
    if veto_rate >= 0.3:
        return "validator_veto"
    if missing_rate >= 0.3:
        return "mixed_recall_and_ranking"
    return "ranking_or_final_selection"


def build_preflight(root: Path | None = None) -> dict[str, Any]:
    root = _repo_root(root)
    entries = _git_status_entries(root)
    paths = [entry.get("path", "") for entry in entries if entry.get("path")]
    artifact_paths = [path for path in paths if _is_artifact_path(path)]
    giant_paths = [path for path in paths if path in GIANT_OWNER_FILES]
    giant_change_summary = _giant_file_change_summary(root, giant_paths)
    owner_boundary = _find_owner_boundary_manifest(root)
    text_risks = _scan_changed_text_risks(root, entries)
    selected_input = _find_global_input(root)
    baseline = _find_baseline_snapshot(root)
    pending = _pending_summary(root)

    hard_blocks: list[str] = []
    if selected_input["status"] == "missing":
        hard_blocks.append("no qualified full/global input")
    if text_risks["paths"]:
        hard_blocks.append("secret or mojibake risk in changed text files")
    if giant_paths and owner_boundary["status"] != "present":
        hard_blocks.append("giant owner files touched without owner_boundary governance manifest")
    giant_bridge_budget = int(owner_boundary.get("max_new_lines_in_any_giant_owner_file") or DEFAULT_GIANT_BRIDGE_LINE_BUDGET)
    giant_over_budget = [
        item for item in giant_change_summary["changed_files"]
        if int(item.get("added_lines", 0)) > giant_bridge_budget
    ]
    giant_missing_boundary = giant_paths and owner_boundary["status"] != "present"

    p0_status = "block" if hard_blocks else "warn" if artifact_paths or giant_paths or pending.get("pending", 0) else "pass"
    recommended_target = ""
    if giant_paths:
        recommended_target = "owner_boundary"
    elif artifact_paths:
        recommended_target = "artifact_hygiene"
    elif pending.get("pending", 0):
        recommended_target = "pending_validation_closure"
    elif selected_input["status"] == "missing":
        recommended_target = "baseline_freeze"

    input_freshness = str(selected_input.get("input_freshness", "") or "")
    full_validation_status = (
        "pending" if input_freshness == "stale"
        else "failed" if input_freshness == "fresh_asset"
        else "unknown"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "command": "preflight",
        "p0_gate_status": p0_status,
        "block_reasons": hard_blocks,
        "recommended_p0_remediation_target": recommended_target,
        "next_allowed_action": "p0_remediation" if p0_status == "block" else "diagnose_or_p0_remediation",
        "git_status_summary": {
            "changed_total": len(entries),
            "untracked_total": sum(1 for entry in entries if entry.get("status") == "??"),
            "artifact_path_total": len(artifact_paths),
            "artifact_path_examples": artifact_paths[:20],
        },
        "dirty_artifact_risk": {
            "status": "warn" if artifact_paths else "pass",
            "paths": artifact_paths[:50],
        },
        "giant_file_touch_risk": {
            "status": "block" if giant_missing_boundary else "warn" if giant_paths or giant_over_budget else "pass",
            "paths": giant_paths,
            "owner_boundary_manifest": owner_boundary,
            "bridge_line_budget": giant_bridge_budget,
            "change_summary": giant_change_summary,
            "over_budget": giant_over_budget,
            "over_budget_policy": "warn_with_owner_boundary" if giant_over_budget and not giant_missing_boundary else "block_without_owner_boundary" if giant_missing_boundary else "within_budget",
        },
        "secret_or_mojibake_risk": text_risks,
        "test_tier_plan": "targeted plus optional slice benchmark; full/global remains Step 5",
        "pure_search_risk": {
            "status": "not_applicable",
            "pure_search_metrics_present": False,
        },
        "baseline_snapshot": baseline,
        "selected_input": selected_input,
        "pending_full_validation_summary": pending,
        "full_validation_status": full_validation_status,
        "release_gate_status": "blocked_pending_full_validation",
    }


def freeze_baseline(
    root: Path | None,
    latest_path: Path,
    attribution_path: Path,
    out_path: Path,
    command: str,
) -> dict[str, Any]:
    root = _repo_root(root)
    attr_abs = (root / attribution_path).resolve() if not attribution_path.is_absolute() else attribution_path
    latest_abs = (root / latest_path).resolve() if not latest_path.is_absolute() else latest_path
    if not latest_abs.exists():
        raise SystemExit(f"latest not found: {latest_path}")
    if not attr_abs.exists():
        raise SystemExit(f"attribution not found: {attribution_path}")
    try:
        attr = json.loads(attr_abs.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        attr = {}
    code, commit, _ = _run_git(root, ["rev-parse", "HEAD"])
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "command": "freeze-baseline",
        "commit": commit.strip() if code == 0 else "",
        "benchmark_command": command,
        "latest_path": _rel(root, latest_abs),
        "attribution_path": _rel(root, attr_abs),
        "profile": attr.get("profile", ""),
        "scoring_mode": attr.get("scoring_mode", ""),
        "total": attr.get("total"),
        "correct_total": attr.get("correct_total"),
        "wrong_total": attr.get("wrong_total"),
        "overall_hit_rate": attr.get("overall_hit_rate"),
        "recall_hit_rate": attr.get("recall_hit_rate"),
        "stage_counts": attr.get("counts", {}),
        "no_materialize_learning": "--no-materialize-learning" in command,
    }
    out_abs = (root / out_path).resolve() if not out_path.is_absolute() else out_path
    _write_json(out_abs, payload)
    return payload


def choose_next_action(
    root: Path | None,
    latest_path: Path | None,
    attribution_path: Path | None,
    decision_table_path: Path,
    summary_path: Path,
    next_action_path: Path,
) -> dict[str, Any]:
    root = _repo_root(root)
    selected = _find_global_input(root)
    latest = latest_path or Path(selected["latest_path"])
    attribution = attribution_path or Path(selected["attribution_path"])
    latest_abs = (root / latest).resolve() if not latest.is_absolute() else latest
    attribution_abs = (root / attribution).resolve() if not attribution.is_absolute() else attribution
    if not latest_abs.exists():
        raise SystemExit(f"latest not found: {latest}")
    if not attribution_abs.exists():
        raise SystemExit(f"attribution not found: {attribution}")

    records = _iter_latest_records(latest_abs)
    rows = build_rows(records)
    if not rows:
        raise SystemExit("no wrong samples found in latest input")
    summary = build_summary(rows, latest, attribution)
    next_action = build_next_action(summary, rows)
    if next_action.get("action") == "fix_r1_recall" and not _has_complete_pure_search_metrics(root):
        next_action["action"] = "improve_diagnostics"
        next_action["reason"] = (
            f"{next_action.get('reason', '')}; pure_search_metrics missing for R1 recall action"
        ).strip("; ")
        next_action["pure_search_metrics_required"] = True

    decision_abs = (root / decision_table_path).resolve() if not decision_table_path.is_absolute() else decision_table_path
    decision_abs.parent.mkdir(parents=True, exist_ok=True)
    with decision_abs.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    _write_json((root / summary_path).resolve() if not summary_path.is_absolute() else summary_path, summary)
    _write_json((root / next_action_path).resolve() if not next_action_path.is_absolute() else next_action_path, next_action)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "command": "choose-next-action",
        "decision_table": _rel(root, decision_abs),
        "summary": _rel(root, (root / summary_path).resolve() if not summary_path.is_absolute() else summary_path),
        "next_action": _rel(root, (root / next_action_path).resolve() if not next_action_path.is_absolute() else next_action_path),
        "wrong_total": len(rows),
        "action": next_action["action"],
        "target_common_issue": next_action.get("target_common_issue", {}),
        "full_validation_status": next_action.get("full_validation_status", "pending"),
    }


def register_validation(
    root: Path | None,
    fix_id: str,
    action: str,
    description: str,
    files: list[str],
    validation: list[str],
    status: str,
    ledger_path: Path,
) -> dict[str, Any]:
    root = _repo_root(root)
    ledger_abs = (root / ledger_path).resolve() if not ledger_path.is_absolute() else ledger_path
    if ledger_abs.exists():
        try:
            payload = json.loads(ledger_abs.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {"schema_version": SCHEMA_VERSION, "entries": []}
    else:
        payload = {"schema_version": SCHEMA_VERSION, "entries": []}
    entries = payload.setdefault("entries", [])
    if not isinstance(entries, list):
        entries = []
        payload["entries"] = entries
    entry = {
        "fix_id": fix_id,
        "registered_at": _now_iso(),
        "action": action,
        "description": description,
        "files": files,
        "validation": validation,
        "status": status,
    }
    for index, existing in enumerate(entries):
        if isinstance(existing, dict) and existing.get("fix_id") == fix_id:
            entries[index] = entry
            break
    else:
        entries.append(entry)
    payload["updated_at"] = _now_iso()
    _write_json(ledger_abs, payload)
    return {
        "schema_version": SCHEMA_VERSION,
        "command": "register-validation",
        "ledger_path": _rel(root, ledger_abs),
        "fix_id": fix_id,
        "status": status,
        "pending_full_validation_summary": _pending_summary(root),
    }


def release_check(root: Path | None, ledger_path: Path) -> dict[str, Any]:
    root = _repo_root(root)
    selected = _find_global_input(root)
    pending = _pending_summary(root)
    ledger_abs = (root / ledger_path).resolve() if not ledger_path.is_absolute() else ledger_path
    release_status = "pass"
    reasons: list[str] = []
    if selected["status"] != "present" or selected["input_freshness"] != "fresh":
        release_status = "block"
        reasons.append("fresh v36 full/global input is missing")
    if pending.get("pending", 0):
        release_status = "block"
        reasons.append("pending_full_validation entries remain")
    if not ledger_abs.exists():
        release_status = "block"
        reasons.append("pending validation ledger is missing")
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "command": "release-check",
        "release_gate_status": release_status,
        "block_reasons": reasons,
        "selected_input": selected,
        "pending_full_validation_summary": pending,
    }


def diagnose_pure_search(root: Path | None, out_path: Path) -> dict[str, Any]:
    root = _repo_root(root)
    selected = _find_global_input(root)
    if selected["status"] != "present":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now_iso(),
            "command": "diagnose-pure-search",
            "status": "missing_input",
            "selected_input": selected,
            "pure_search_metrics": {
                "recall_at_k": {},
                "rank_at_k": {},
                "validator_veto_rate": {},
                "route_filter_loss": {},
                "prior_candidates_delta": {},
                "latency_breakdown_ms": {},
            },
            "bottleneck_classification": "unknown",
            "next_allowed_action": "prepare_full_global_input",
        }
        out_abs = (root / out_path).resolve() if not out_path.is_absolute() else out_path
        _write_json(out_abs, payload)
        return payload

    latest = (root / selected["latest_path"]).resolve()
    attribution = (root / selected["attribution_path"]).resolve()
    records = _iter_latest_records(latest)
    rows = build_rows(records)
    wrong_pairs: list[tuple[dict[str, Any], dict[str, str]]] = []
    row_index = 0
    for index, record in enumerate(records, start=1):
        expected_ids = _expected_ids(record)
        selected_id = _selected_id(record)
        if not _is_wrong(record, expected_ids, selected_id):
            continue
        if row_index >= len(rows):
            break
        row = rows[row_index]
        row.setdefault("sample_id", _sample_id(record, index))
        wrong_pairs.append((record, row))
        row_index += 1

    summary = build_summary(rows, Path(selected["latest_path"]), Path(selected["attribution_path"])) if rows else {}
    target = {}
    next_action_path = root / "reports" / "attribution" / "global_repair_next_action.json"
    if next_action_path.exists():
        try:
            existing_next = json.loads(next_action_path.read_text(encoding="utf-8"))
            if isinstance(existing_next, dict):
                target = existing_next.get("target_common_issue") or {}
        except json.JSONDecodeError:
            target = {}
    if not target and rows:
        target = build_next_action(summary, rows).get("target_common_issue") or {}

    target_issue_key = str(target.get("issue_key") or "")
    target_cluster_id = str(target.get("cluster_id") or "")
    target_pairs = [
        pair for pair in wrong_pairs if target_issue_key and pair[1].get("common_issue_key") == target_issue_key
    ]
    if not target_pairs:
        target_pairs = wrong_pairs

    target_metrics = _summarize_pure_search_scope(target_pairs)
    all_wrong_metrics = _summarize_pure_search_scope(wrong_pairs)
    bottleneck = _classify_bottleneck(target_metrics)
    next_allowed = "fix_r1_recall" if bottleneck == "candidate_recall_or_route_filter_loss" else "improve_diagnostics"
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "command": "diagnose-pure-search",
        "status": "complete_static_diagnosis",
        "selected_input": selected,
        "input_latest_path": _rel(root, latest),
        "input_attribution_path": _rel(root, attribution),
        "target_common_issue": target,
        "filter_cluster_id": target_cluster_id,
        "filter_common_issue_key": target_issue_key,
        "pure_search_metrics": target_metrics,
        "all_wrong_metrics": all_wrong_metrics,
        "bottleneck_classification": bottleneck,
        "next_allowed_action": next_allowed,
        "notes": [
            "Metrics are computed from the selected static full/global latest artifact.",
            "prior_candidates_delta and latency_breakdown_ms require paired or timed benchmark artifacts for numeric deltas.",
        ],
    }
    out_abs = (root / out_path).resolve() if not out_path.is_absolute() else out_path
    _write_json(out_abs, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="V36 governance gate")
    sub = parser.add_subparsers(dest="command", required=True)

    preflight_parser = sub.add_parser("preflight")
    preflight_parser.add_argument("--out", type=Path, default=Path("reports/attribution/v36_preflight.json"))

    freeze_parser = sub.add_parser("freeze-baseline")
    freeze_parser.add_argument("--latest", type=Path, required=True)
    freeze_parser.add_argument("--attribution", type=Path, required=True)
    freeze_parser.add_argument("--command-line", default="")
    freeze_parser.add_argument("--out", type=Path, default=Path("eval/baselines/v36_baseline_latest.json"))

    diagnose_parser = sub.add_parser("diagnose-pure-search")
    diagnose_parser.add_argument("--out", type=Path, default=Path("reports/attribution/pure_search_diagnosis.json"))

    choose_parser = sub.add_parser("choose-next-action")
    choose_parser.add_argument("--latest", type=Path)
    choose_parser.add_argument("--attribution", type=Path)
    choose_parser.add_argument("--decision-table", type=Path, default=Path("reports/attribution/global_repair_decision_table.csv"))
    choose_parser.add_argument("--summary", type=Path, default=Path("reports/attribution/global_repair_decision_summary.json"))
    choose_parser.add_argument("--next-action", type=Path, default=Path("reports/attribution/global_repair_next_action.json"))

    register_parser = sub.add_parser("register-validation")
    register_parser.add_argument("--fix-id", required=True)
    register_parser.add_argument("--action", required=True)
    register_parser.add_argument("--description", default="")
    register_parser.add_argument("--file", action="append", default=[])
    register_parser.add_argument("--validation", action="append", default=[])
    register_parser.add_argument("--status", default="pending_full_validation")
    register_parser.add_argument("--ledger", type=Path, default=Path("reports/agent_state/v36_pending_full_validation.json"))

    release_parser = sub.add_parser("release-check")
    release_parser.add_argument("--ledger", type=Path, default=Path("reports/agent_state/v36_pending_full_validation.json"))

    args = parser.parse_args()
    root = Path.cwd()
    if args.command == "preflight":
        result = build_preflight(root)
        _write_json(root / args.out, result)
    elif args.command == "freeze-baseline":
        result = freeze_baseline(root, args.latest, args.attribution, args.out, args.command_line)
    elif args.command == "diagnose-pure-search":
        result = diagnose_pure_search(root, args.out)
    elif args.command == "choose-next-action":
        result = choose_next_action(root, args.latest, args.attribution, args.decision_table, args.summary, args.next_action)
    elif args.command == "register-validation":
        result = register_validation(
            root,
            args.fix_id,
            args.action,
            args.description,
            args.file,
            args.validation,
            args.status,
            args.ledger,
        )
    elif args.command == "release-check":
        result = release_check(root, args.ledger)
    else:
        raise SystemExit(f"unsupported command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
