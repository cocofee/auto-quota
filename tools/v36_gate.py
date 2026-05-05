import argparse
import csv
import hashlib
import json
import math
import os
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
    ACTION_BY_BUCKET,
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
LARGE_SOURCE_FILE_LINE_THRESHOLD = 1200
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
GENERATED_KNOWLEDGE_PREFIX = "data/province_plugins/generated/"
DATA_REVIEW_QUEUE_PATH = Path("reports/agent_state/v36_data_review_queue.json")
FLAKY_TRACKING_PATH = Path("reports/agent_state/flaky_tracking.json")
V36_SEED_PATH = Path("eval/v36_seed.json")
ACCURACY_GOAL_PROGRESS_PATH = Path("reports/agent_state/v36_accuracy_goal_progress.json")
ACCURACY_GOAL_TARGET_HIT_RATE = 75.0
ACCURACY_GOAL_HISTORY_LIMIT = 20
ACCURACY_GOAL_PLATEAU_WINDOW = 3
ACCURACY_GOAL_PLATEAU_MIN_DELTA = 1.0
PENDING_FULL_VALIDATION_LIMIT = 5
GOAL_NEXT_PATH = Path("reports/agent_state/v36_goal_next.json")
ACCURACY_GOAL_MILESTONES = (
    ("M1", 45.0),
    ("M2", 55.0),
    ("M3", 65.0),
    ("M4", 75.0),
)
GOAL_CONTRIBUTION_STAGES = {
    "parser",
    "router",
    "retriever",
    "candidate_pool",
    "validator",
    "ranker",
    "post_rank",
    "data_review",
    "diagnostics",
}
GOAL_CONTRIBUTION_BENEFITS = {
    "reduce_recall_miss",
    "reduce_rank_miss",
    "reduce_post_rank_flip",
    "improve_diagnostics",
    "isolate_data_issue",
}
GOAL_CONTRIBUTION_SPEED_BUDGETS = {
    "no_global_slow_path",
    "cached",
    "bounded_top_k",
    "not_applicable",
}
GOAL_CONTRIBUTION_COMPLEXITY_BUDGETS = {"decrease", "neutral"}
CODE_HEALTH_SOURCE_PREFIXES = ("src/", "tools/", "web/")
CODE_HEALTH_PARTIAL_STATUSES = {"failed", "local_behavior_pass", "candidate_lifecycle_pass", "blocked_by_next_stage"}
REDUNDANT_FILE_PATTERNS = (
    re.compile(r"(^|/)(?:old|backup|bak|tmp|temp)(?:_|-|/)", re.IGNORECASE),
    re.compile(r"(?:_|-)(?:old|backup|bak|tmp|temp|copy)(?=\.|_|-|$)", re.IGNORECASE),
    re.compile(r"\.(?:bak|tmp|orig|copy)$", re.IGNORECASE),
)
REQUIRED_VERSION_FIELDS = (
    "algorithm_commit",
    "knowledge_digest_hash",
    "quota_db_revision",
    "bill_corpus_revision",
    "vector_index_revision",
    "embedding_model_version",
    "model_profile_hash",
    "seed",
)
VALID_ROLLBACK_TYPES = {"config_flag", "isolated_module_call", "git_revert"}


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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _file_content_hash(root: Path, paths: list[Path]) -> str:
    existing = [path for path in paths if path.exists() and path.is_file()]
    if not existing:
        return ""
    digest = hashlib.sha256()
    for path in sorted(existing, key=lambda item: _rel(root, item)):
        digest.update(_rel(root, path).encode("utf-8", errors="replace"))
        digest.update(b"\0")
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
        digest.update(b"\0")
    return digest.hexdigest()


def _metadata_revision(root: Path, paths: list[Path]) -> str:
    existing = [path for path in paths if path.exists() and path.is_file()]
    if not existing:
        return ""
    digest = hashlib.sha256()
    for path in sorted(existing, key=lambda item: _rel(root, item)):
        try:
            stat = path.stat()
        except OSError:
            continue
        digest.update(f"{_rel(root, path)}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8", errors="replace"))
        digest.update(b"\0")
    return digest.hexdigest()


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


def _is_generated_knowledge_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return normalized.startswith(GENERATED_KNOWLEDGE_PREFIX) or normalized.endswith("knowledge_digest.md")


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


def _list_json_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
        entries = payload.get("entries")
        if isinstance(entries, list):
            return [item for item in entries if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _version_tuple(root: Path) -> dict[str, Any]:
    code, commit, _ = _run_git(root, ["rev-parse", "HEAD"])
    generated = root / "data" / "province_plugins" / "generated"
    knowledge_paths = list(generated.glob("knowledge*.json")) + [generated / "knowledge_digest.md"]
    quota_paths = list((root / "db").glob("**/*.sqlite3")) + list((root / "db").glob("**/*.db"))
    bill_paths = [
        root / "data" / "bill_library.db",
        root / "data" / "bill_library_all.json",
        root / "data" / "bill_features_2024.json",
        root / "data" / "bill_synonyms.json",
    ]
    vector_paths = list((root / "db" / "chroma").glob("**/*.sqlite3"))
    embedding_source = "|".join(sorted({_rel(root, path.parent) for path in vector_paths[:50]}))
    model_profile_hash = _sha256_text(embedding_source) if embedding_source else ""
    seed = _v36_seed(root)
    return {
        "algorithm_commit": commit.strip() if code == 0 else "",
        "knowledge_digest_hash": _file_content_hash(root, knowledge_paths),
        "quota_db_revision": _metadata_revision(root, quota_paths),
        "bill_corpus_revision": _metadata_revision(root, bill_paths),
        "vector_index_revision": _metadata_revision(root, vector_paths),
        "embedding_model_version": "qwen3" if vector_paths else "",
        "model_profile_hash": model_profile_hash,
        "seed": seed,
    }


def _v36_seed(root: Path) -> str:
    for name in ("V36_SEED", "BENCHMARK_SEED", "PYTHONHASHSEED"):
        value = str(os.environ.get(name, "")).strip()
        if value:
            return value
    seed_path = root / V36_SEED_PATH
    if seed_path.exists():
        payload = _read_json_file(seed_path)
        for key in ("seed", "default_seed"):
            value = str(payload.get(key, "") or "").strip()
            if value:
                return value
    return ""


def _version_tuple_status(version: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in REQUIRED_VERSION_FIELDS if not str(version.get(field) or "").strip()]
    return {
        "status": "complete" if not missing else "missing_fields",
        "missing_fields": missing,
    }


def _data_review_queue_summary(root: Path) -> dict[str, Any]:
    path = root / DATA_REVIEW_QUEUE_PATH
    if not path.exists():
        return {
            "status": "missing",
            "path": DATA_REVIEW_QUEUE_PATH.as_posix(),
            "total": 0,
            "open": 0,
            "fixed_in_corpus": 0,
            "wontfix": 0,
            "ambiguous_kept": 0,
            "closed_sample_ids": [],
            "open_sample_ids": [],
        }
    payload = _read_json_file(path)
    items = _list_json_items(payload)
    counts = Counter(str(item.get("status") or "open") for item in items)
    closed = [
        str(item.get("sample_id") or "").strip()
        for item in items
        if str(item.get("sample_id") or "").strip() and str(item.get("status") or "open") != "open"
    ]
    opened = [
        str(item.get("sample_id") or "").strip()
        for item in items
        if str(item.get("sample_id") or "").strip() and str(item.get("status") or "open") == "open"
    ]
    return {
        "status": "present",
        "path": DATA_REVIEW_QUEUE_PATH.as_posix(),
        "total": len(items),
        "open": counts.get("open", 0),
        "fixed_in_corpus": counts.get("fixed_in_corpus", 0),
        "wontfix": counts.get("wontfix", 0),
        "ambiguous_kept": counts.get("ambiguous_kept", 0),
        "closed_sample_ids": closed,
        "open_sample_ids": opened,
    }


def _flaky_tracking_summary(root: Path) -> dict[str, Any]:
    path = root / FLAKY_TRACKING_PATH
    if not path.exists():
        return {"status": "missing", "path": FLAKY_TRACKING_PATH.as_posix(), "total": 0, "triggered": 0}
    payload = _read_json_file(path)
    items = _list_json_items(payload)
    triggered = [
        item for item in items
        if int(item.get("count") or 0) >= 3 and str(item.get("status") or "open") not in {"fixed", "closed"}
    ]
    return {
        "status": "present",
        "path": FLAKY_TRACKING_PATH.as_posix(),
        "total": len(items),
        "triggered": len(triggered),
        "triggered_signatures": [str(item.get("signature") or "") for item in triggered[:20]],
    }


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


def _git_list_files(root: Path, prefixes: tuple[str, ...]) -> list[str]:
    code, stdout, _ = _run_git(root, ["ls-files", "--", *prefixes])
    if code != 0:
        return []
    return [line.replace("\\", "/") for line in stdout.splitlines() if line.strip()]


def _code_line_count(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return 0


def _is_redundant_file_candidate(path: str) -> bool:
    normalized = path.replace("\\", "/")
    name = Path(normalized).name
    if normalized.startswith(("reports/", "output/", "tmp/", "test_artifacts/")):
        return True
    return any(pattern.search(normalized) or pattern.search(name) for pattern in REDUNDANT_FILE_PATTERNS)


def _manifest_code_change_paths(manifest: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for key in ("code_changes", "changed_code_files", "changed_files", "affected_files"):
        value = manifest.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    result.append(item.replace("\\", "/"))
                elif isinstance(item, dict) and item.get("path"):
                    result.append(str(item["path"]).replace("\\", "/"))
    rollback = manifest.get("rollback_plan")
    if isinstance(rollback, dict):
        affected = rollback.get("affected_files")
        if isinstance(affected, list):
            result.extend(str(item).replace("\\", "/") for item in affected if isinstance(item, str))
    return sorted(set(path for path in result if path.startswith(CODE_HEALTH_SOURCE_PREFIXES)))


def _code_health_inventory(root: Path, entries: list[dict[str, str]]) -> dict[str, Any]:
    tracked_sources = set(_git_list_files(root, CODE_HEALTH_SOURCE_PREFIXES))
    changed_paths = {entry.get("path", "").replace("\\", "/") for entry in entries if entry.get("path")}
    source_paths = sorted(
        path for path in (tracked_sources | changed_paths)
        if path.startswith(CODE_HEALTH_SOURCE_PREFIXES)
    )

    large_files: list[dict[str, Any]] = []
    for rel_path in source_paths:
        abs_path = root / rel_path
        if not abs_path.exists() or not abs_path.is_file():
            continue
        loc = _code_line_count(abs_path)
        if loc >= LARGE_SOURCE_FILE_LINE_THRESHOLD or rel_path in GIANT_OWNER_FILES:
            large_files.append(
                {
                    "path": rel_path,
                    "lines": loc,
                    "bytes": abs_path.stat().st_size,
                    "category": "giant_owner" if rel_path in GIANT_OWNER_FILES else "large_source_file",
                    "recommended_p0_subtarget": "large_file_decomposition",
                }
            )

    logic_files: dict[str, dict[str, Any]] = {}
    manifest_dir = root / "reports" / "attribution"
    if manifest_dir.exists():
        for path in sorted(manifest_dir.glob("v36_round_manifest_*.json")):
            manifest = _read_json_file(path)
            if not manifest:
                continue
            partial_status = str(manifest.get("partial_validation_status") or "")
            failed_next = manifest.get("failed_slice_next_action")
            if partial_status not in CODE_HEALTH_PARTIAL_STATUSES and not isinstance(failed_next, dict):
                continue
            repair_unit = manifest.get("repair_unit") if isinstance(manifest.get("repair_unit"), dict) else {}
            target = manifest.get("target_common_issue") if isinstance(manifest.get("target_common_issue"), dict) else {}
            for rel_path in _manifest_code_change_paths(manifest):
                existing = logic_files.setdefault(
                    rel_path,
                    {
                        "path": rel_path,
                        "statuses": [],
                        "source_manifests": [],
                        "issue_keys": [],
                        "next_stages": [],
                        "recommended_p0_subtarget": "logic_error_triage",
                    },
                )
                if partial_status and partial_status not in existing["statuses"]:
                    existing["statuses"].append(partial_status)
                manifest_rel = _rel(root, path)
                if manifest_rel not in existing["source_manifests"]:
                    existing["source_manifests"].append(manifest_rel)
                issue_key = str(repair_unit.get("issue_key") or target.get("issue_key") or "")
                if issue_key and issue_key not in existing["issue_keys"]:
                    existing["issue_keys"].append(issue_key)
                next_stage = failed_next.get("next_failing_stage", "") if isinstance(failed_next, dict) else ""
                if next_stage and next_stage not in existing["next_stages"]:
                    existing["next_stages"].append(next_stage)

    redundant_candidates = [
        {
            "path": path,
            "status": next((entry.get("status", "") for entry in entries if entry.get("path", "").replace("\\", "/") == path), "tracked"),
            "recommended_p0_subtarget": "redundant_file_hygiene",
        }
        for path in sorted(set(_git_list_files(root, ("src/", "tools/", "docs/", "tests/")) + list(changed_paths)))
        if path and _is_redundant_file_candidate(path)
    ]

    recommended_subtargets: list[str] = []
    for candidate_list in (large_files, list(logic_files.values()), redundant_candidates):
        for item in candidate_list:
            subtarget = str(item.get("recommended_p0_subtarget") or "")
            if subtarget and subtarget not in recommended_subtargets:
                recommended_subtargets.append(subtarget)

    return {
        "status": "warn" if recommended_subtargets else "pass",
        "recommended_p0_remediation_target": "code_health_triage" if recommended_subtargets else "",
        "recommended_p0_subtargets": recommended_subtargets,
        "large_file_inventory": {
            "status": "warn" if large_files else "pass",
            "line_threshold": LARGE_SOURCE_FILE_LINE_THRESHOLD,
            "files": large_files[:50],
            "total": len(large_files),
        },
        "logic_error_file_inventory": {
            "status": "warn" if logic_files else "pass",
            "files": list(logic_files.values())[:50],
            "total": len(logic_files),
        },
        "redundant_file_inventory": {
            "status": "warn" if redundant_candidates else "pass",
            "files": redundant_candidates[:50],
            "total": len(redundant_candidates),
        },
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


def _normalize_v36_commonality(summary: dict[str, Any]) -> dict[str, Any]:
    wrong_total = int(summary.get("wrong_total") or 0)
    clusters = summary.get("common_issue_clusters")
    if not isinstance(clusters, list):
        return summary
    skipped_issue_keys = {
        str(cluster.get("issue_key") or "")
        for cluster in summary.get("skipped_pending_validation_clusters", [])
        if isinstance(cluster, dict) and cluster.get("issue_key")
    }
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        count = int(cluster.get("sample_count") or 0)
        ratio = (count / wrong_total) if wrong_total else 0.0
        cluster["sample_ratio"] = round(ratio, 4)
        if count >= 3 and ratio >= 0.01:
            cluster["commonality"] = "shared"
        elif count >= 2:
            cluster["commonality"] = "weak_shared"
        else:
            cluster["commonality"] = "singleton_only"
    selectable = [
        cluster
        for cluster in clusters
        if isinstance(cluster, dict) and str(cluster.get("issue_key") or "") not in skipped_issue_keys
    ]
    shared = [cluster for cluster in selectable if cluster.get("commonality") == "shared"]
    if shared:
        summary["target_common_issue"] = shared[0]
        if skipped_issue_keys:
            summary["cluster_selection_reason"] = "largest selectable shared cluster; pending_full_validation issue_keys skipped"
        else:
            summary["cluster_selection_reason"] = "largest shared cluster by sample_count, then bucket/key"
    elif selectable:
        summary["target_common_issue"] = selectable[0]
        summary["cluster_selection_reason"] = "no shared cluster; selected diagnostic target only"
    else:
        summary["target_common_issue"] = {}
        summary["cluster_selection_reason"] = "no selectable common_issue_cluster; all known repair units skipped"
    return summary


def _repair_unit_issue_key(payload: dict[str, Any]) -> str:
    for container_name in ("repair_unit", "target_common_issue"):
        container = payload.get(container_name)
        if isinstance(container, dict) and container.get("issue_key"):
            return str(container["issue_key"])
    if payload.get("issue_key"):
        return str(payload["issue_key"])
    return ""


def _repair_unit_cluster_id(payload: dict[str, Any]) -> str:
    for container_name in ("repair_unit", "target_common_issue"):
        container = payload.get(container_name)
        if isinstance(container, dict) and container.get("cluster_id"):
            return str(container["cluster_id"])
    if payload.get("cluster_id"):
        return str(payload["cluster_id"])
    return ""


def _repair_unit_mechanism(payload: dict[str, Any]) -> str:
    repair_unit = payload.get("repair_unit") if isinstance(payload.get("repair_unit"), dict) else {}
    for key in ("mechanism", "failing_stage"):
        if repair_unit.get(key):
            return str(repair_unit[key])
    if payload.get("mechanism"):
        return str(payload["mechanism"])
    if payload.get("action"):
        return str(payload["action"])
    failed_next = payload.get("failed_slice_next_action")
    if isinstance(failed_next, dict) and failed_next.get("action"):
        return str(failed_next["action"])
    return ""


def _repair_unit_owner_module(payload: dict[str, Any]) -> str:
    repair_unit = payload.get("repair_unit") if isinstance(payload.get("repair_unit"), dict) else {}
    if repair_unit.get("owner_module"):
        return str(repair_unit["owner_module"])
    scope = payload.get("suggested_validation_scope")
    if isinstance(scope, dict) and scope.get("owner_module"):
        return str(scope["owner_module"])
    if payload.get("owner_module"):
        return str(payload["owner_module"])
    if payload.get("action"):
        owner = _owner_for_action(str(payload["action"]))
        if owner:
            return owner
    return ""


def _explicit_repair_unit_id(payload: dict[str, Any]) -> str:
    repair_unit = payload.get("repair_unit") if isinstance(payload.get("repair_unit"), dict) else {}
    if repair_unit.get("repair_unit_id"):
        return str(repair_unit["repair_unit_id"])
    if payload.get("repair_unit_id"):
        return str(payload["repair_unit_id"])
    return ""


def _build_repair_unit_id(cluster_id: str, issue_key: str, mechanism: str, owner_module: str) -> str:
    parts = [cluster_id, issue_key, mechanism, owner_module]
    if not all(str(part or "").strip() for part in parts):
        return ""
    return "::".join(str(part).strip() for part in parts)


def _repair_unit_id(payload: dict[str, Any]) -> str:
    explicit = _explicit_repair_unit_id(payload)
    if explicit:
        return explicit
    return _build_repair_unit_id(
        _repair_unit_cluster_id(payload),
        _repair_unit_issue_key(payload),
        _repair_unit_mechanism(payload),
        _repair_unit_owner_module(payload),
    )


def _selector_entry(
    *,
    payload: dict[str, Any],
    reason: str,
    source_manifest: str,
    next_stage: str = "",
    force_issue_key_scope: bool = False,
) -> tuple[str, dict[str, Any]] | None:
    issue_key = _repair_unit_issue_key(payload)
    if not issue_key:
        return None
    repair_unit_id = _repair_unit_id(payload)
    key_type = "issue_key" if force_issue_key_scope or not repair_unit_id else "repair_unit_id"
    key = issue_key if key_type == "issue_key" else repair_unit_id
    entry = {
        "issue_key": issue_key,
        "repair_unit_id": repair_unit_id,
        "cluster_id": _repair_unit_cluster_id(payload),
        "mechanism": _repair_unit_mechanism(payload),
        "owner_module": _repair_unit_owner_module(payload),
        "selector_key": key,
        "selector_key_type": key_type,
        "reason": reason,
        "source_manifest": source_manifest,
        "next_stage": next_stage,
    }
    return key, entry


def _selector_state_units(
    root: Path,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    skipped: dict[str, dict[str, Any]] = {}
    blocked: dict[str, dict[str, Any]] = {}
    skipped_by_id: dict[str, dict[str, Any]] = {}
    blocked_by_id: dict[str, dict[str, Any]] = {}
    partial_skip_statuses = {"blocked_by_next_stage", "candidate_lifecycle_pass", "local_behavior_pass"}
    ledger_path = root / "reports" / "agent_state" / "v36_pending_full_validation.json"
    ledger = _read_json_file(ledger_path)
    for entry in _list_json_items(ledger):
        if entry.get("status") != "pending_full_validation":
            continue
        selector = _selector_entry(
            payload=entry,
            reason="pending_full_validation",
            source_manifest=_rel(root, ledger_path),
            force_issue_key_scope=True,
        )
        if not selector:
            continue
        key, value = selector
        if value["selector_key_type"] == "repair_unit_id":
            skipped_by_id[key] = value
        else:
            skipped[key] = value

    data_review_path = root / DATA_REVIEW_QUEUE_PATH
    data_review = _read_json_file(data_review_path)
    for item in _list_json_items(data_review):
        if item.get("status") != "open":
            continue
        selector = _selector_entry(
            payload=item,
            reason="data_review_open",
            source_manifest=_rel(root, data_review_path),
            next_stage="data_review",
            force_issue_key_scope=True,
        )
        if not selector:
            continue
        key, value = selector
        skipped[key] = value

    manifest_dir = root / "reports" / "attribution"
    if not manifest_dir.exists():
        return skipped, blocked, skipped_by_id, blocked_by_id
    for path in sorted(manifest_dir.glob("v36_round_manifest_*.json")):
        manifest = _read_json_file(path)
        if not manifest:
            continue
        full_status = str(manifest.get("full_validation_status") or "")
        pending = manifest.get("pending_full_validation")
        pending_status = pending.get("status") if isinstance(pending, dict) else ""
        if pending_status == "pending_full_validation":
            selector = _selector_entry(
                payload=manifest,
                reason="pending_full_validation",
                source_manifest=_rel(root, path),
                force_issue_key_scope=True,
            )
            if selector:
                key, value = selector
                if value["selector_key_type"] == "repair_unit_id":
                    skipped_by_id[key] = value
                else:
                    skipped[key] = value
        failed_next = manifest.get("failed_slice_next_action")
        partial_status = str(manifest.get("partial_validation_status") or "")
        failed_next_same_unit = failed_next.get("same_repair_unit") if isinstance(failed_next, dict) else None
        next_failing_stage = failed_next.get("next_failing_stage", "") if isinstance(failed_next, dict) else ""
        failed_next_action = failed_next.get("action", "") if isinstance(failed_next, dict) else ""
        data_review_stage = (
            next_failing_stage == "data_review"
            or failed_next_action == "convert_to_data_review"
            or manifest.get("action") == "review_data"
        )
        if partial_status == "failed" and data_review_stage:
            selector = _selector_entry(
                payload=manifest,
                reason="data_review_open",
                source_manifest=_rel(root, path),
                next_stage="data_review",
                force_issue_key_scope=True,
            )
            if selector:
                key, value = selector
                skipped[key] = value
        should_skip_partial = partial_status in partial_skip_statuses or failed_next_same_unit is False
        if should_skip_partial:
            selector = _selector_entry(
                payload=manifest,
                reason=partial_status or "failed_slice_next_action_same_repair_unit_false",
                source_manifest=_rel(root, path),
                next_stage=next_failing_stage,
            )
            if selector:
                key, value = selector
                if value["selector_key_type"] == "repair_unit_id":
                    skipped_by_id[key] = value
                else:
                    skipped[key] = value
        is_blocked = partial_status == "blocked_by_next_stage" or "blocked_by_next_stage" in str(manifest.get("status") or "")
        if is_blocked:
            next_stage = failed_next.get("next_failing_stage", "") if isinstance(failed_next, dict) else ""
            selector = _selector_entry(
                payload=manifest,
                reason="blocked_by_next_stage",
                source_manifest=_rel(root, path),
                next_stage=next_stage,
            )
            if selector:
                key, value = selector
                if value["selector_key_type"] == "repair_unit_id":
                    blocked_by_id[key] = value
                else:
                    blocked[key] = value
    return skipped, blocked, skipped_by_id, blocked_by_id


def _enrich_skipped_repair_units(
    summary: dict[str, Any],
    skipped_units_by_issue: dict[str, dict[str, Any]],
    skipped_units_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for cluster in summary.get("skipped_pending_validation_clusters", []):
        if not isinstance(cluster, dict):
            continue
        issue_key = str(cluster.get("issue_key") or "")
        if not issue_key or issue_key in seen:
            continue
        seen.add(issue_key)
        enriched = dict(skipped_units_by_issue.get(issue_key) or {})
        enriched.setdefault("issue_key", issue_key)
        enriched["cluster_id"] = str(enriched.get("cluster_id") or cluster.get("cluster_id") or "")
        enriched.setdefault("reason", str(cluster.get("reason") or "pending_full_validation"))
        enriched.setdefault("source_manifest", "")
        enriched.setdefault("next_stage", "")
        result.append(enriched)
    for key, entry in sorted((skipped_units_by_id or {}).items()):
        marker = f"repair_unit_id:{key}"
        if marker in seen:
            continue
        seen.add(marker)
        result.append(dict(entry))
    return result


def _owner_for_action(action: str) -> str:
    return {
        "improve_diagnostics": "tools/diagnostics",
        "fix_r1_recall": "src/search_routing|src/search_features",
        "fix_r2_ltr": "src/ranking_rules",
        "fix_r3_cgr": "src/ranking_rules",
        "fix_r4_picker": "src/ranking_rules",
        "fix_r5_validator": "src/validation_rules",
        "review_data": "reports/agent_state/v36_data_review_queue.json",
    }.get(action, "")


def _build_v36_next_action(summary: dict[str, Any], rows: list[dict[str, str]], *, pure_metrics_present: bool) -> dict[str, Any]:
    largest_bucket = summary.get("largest_bucket") or "R6"
    target_common_issue = summary.get("target_common_issue") or {}
    target_bucket = target_common_issue.get("bucket") or largest_bucket
    commonality = str(target_common_issue.get("commonality") or "")
    missing_rate = float(summary.get("missing_field_rate") or 0.0)
    if not rows:
        action = "improve_diagnostics"
        reason = "no actionable wrong samples after data_review exclusion"
    elif not target_common_issue:
        action = "improve_diagnostics"
        reason = "no selectable common_issue_cluster after selector state skips"
    elif missing_rate > 0.1:
        action = "improve_diagnostics"
        reason = "missing_field_rate > 10%"
    elif commonality == "weak_shared":
        action = "improve_diagnostics"
        reason = "target_common_issue.commonality=weak_shared; diagnostics only"
    elif commonality == "singleton_only":
        action = "review_data" if target_bucket in {"R6", "R6_known_data_issue"} else "improve_diagnostics"
        reason = f"target_common_issue.commonality=singleton_only; bucket={target_bucket}; diagnostics/data review only"
    elif target_bucket == "R6_known_data_issue":
        action = "review_data"
        reason = "known data issue bucket; update data review queue"
    else:
        bucket = target_bucket if target_bucket in ACTION_BY_BUCKET else "R6"
        action = ACTION_BY_BUCKET[bucket]
        reason = (
            f"target_common_issue={target_common_issue.get('cluster_id')}; "
            f"bucket={bucket}; samples={target_common_issue.get('sample_count')}; "
            f"commonality={target_common_issue.get('commonality')}"
        )
    if action == "fix_r1_recall" and not pure_metrics_present:
        action = "improve_diagnostics"
        reason = f"{reason}; pure_search_metrics missing for R1 recall action"

    representative_ids = list(target_common_issue.get("representative_sample_ids") or [])
    if not representative_ids and rows:
        representative_rows = [row for row in rows if row.get("common_issue_key") == target_common_issue.get("issue_key")]
        if not representative_rows:
            representative_rows = rows[:10]
        representative_ids = [row["sample_id"] for row in representative_rows[:10]]
    cluster_sample_count = int(target_common_issue.get("sample_count") or len(representative_ids))
    scope = {
        "latest_path": summary.get("input_latest_path", ""),
        "attribution_path": summary.get("input_attribution_path", ""),
        "filter_bucket": target_bucket,
        "filter_cluster_id": target_common_issue.get("cluster_id", ""),
        "filter_common_issue_key": target_common_issue.get("issue_key", ""),
        "sample_limit": min(50, cluster_sample_count or int(summary.get("wrong_total") or 0)),
        "owner_module": _owner_for_action(action),
    }
    repair_unit = {
        "cluster_id": target_common_issue.get("cluster_id", ""),
        "issue_key": target_common_issue.get("issue_key", ""),
        "mechanism": action,
        "owner_module": scope["owner_module"],
    }
    repair_unit["repair_unit_id"] = _build_repair_unit_id(
        str(repair_unit["cluster_id"]),
        str(repair_unit["issue_key"]),
        str(repair_unit["mechanism"]),
        str(repair_unit["owner_module"]),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "action": action,
        "reason": reason,
        "largest_bucket": largest_bucket,
        "sample_count": cluster_sample_count,
        "target_common_issue": target_common_issue,
        "repair_unit": repair_unit,
        "repair_unit_id": repair_unit["repair_unit_id"],
        "cluster_sample_ids": representative_ids,
        "representative_sample_ids": representative_ids,
        "suggested_validation_scope": scope,
        "input_latest_path": summary.get("input_latest_path", ""),
        "input_attribution_path": summary.get("input_attribution_path", ""),
        "full_validation_status": "pending",
        "deterministic": True,
        "llm_used": False,
    }


def _update_data_review_queue(
    root: Path,
    next_action: dict[str, Any],
    *,
    latest_path: Path,
    attribution_path: Path,
) -> dict[str, Any]:
    if next_action.get("action") != "review_data":
        return {"status": "not_applicable"}
    path = root / DATA_REVIEW_QUEUE_PATH
    payload = _read_json_file(path) if path.exists() else {
        "schema_version": "v36.data_review_queue.v1",
        "description": "Data issues isolated from V36 algorithm repair rounds.",
        "items": [],
    }
    items = payload.setdefault("items", [])
    if not isinstance(items, list):
        items = []
        payload["items"] = items
    existing = {str(item.get("sample_id") or ""): item for item in items if isinstance(item, dict)}
    added = 0
    updated = 0
    target = next_action.get("target_common_issue") if isinstance(next_action.get("target_common_issue"), dict) else {}
    for sample_id in next_action.get("representative_sample_ids") or []:
        sample_id = str(sample_id or "").strip()
        if not sample_id:
            continue
        entry = existing.get(sample_id)
        if entry is None:
            entry = {
                "sample_id": sample_id,
                "suspected_reason": "unknown",
                "evidence_paths": [_rel(root, latest_path), _rel(root, attribution_path)],
                "status": "open",
                "fix_revision": "",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "owner": "human_data_review",
                "target_common_issue": {
                    "cluster_id": target.get("cluster_id", ""),
                    "issue_key": target.get("issue_key", ""),
                    "bucket": target.get("bucket", ""),
                    "commonality": target.get("commonality", ""),
                },
            }
            items.append(entry)
            existing[sample_id] = entry
            added += 1
        else:
            paths = entry.setdefault("evidence_paths", [])
            if isinstance(paths, list):
                for evidence in (_rel(root, latest_path), _rel(root, attribution_path)):
                    if evidence and evidence not in paths:
                        paths.append(evidence)
            entry["updated_at"] = _now_iso()
            updated += 1
    payload["updated_at"] = _now_iso()
    _write_json(path, payload)
    return {"status": "updated", "path": DATA_REVIEW_QUEUE_PATH.as_posix(), "added": added, "updated": updated}


def build_preflight(root: Path | None = None) -> dict[str, Any]:
    root = _repo_root(root)
    entries = _git_status_entries(root)
    paths = [entry.get("path", "") for entry in entries if entry.get("path")]
    artifact_paths = [path for path in paths if _is_artifact_path(path)]
    generated_knowledge_paths = [path for path in paths if _is_generated_knowledge_path(path)]
    giant_paths = [path for path in paths if path in GIANT_OWNER_FILES]
    giant_change_summary = _giant_file_change_summary(root, giant_paths)
    owner_boundary = _find_owner_boundary_manifest(root)
    text_risks = _scan_changed_text_risks(root, entries)
    code_health = _code_health_inventory(root, entries)
    selected_input = _find_global_input(root)
    baseline = _find_baseline_snapshot(root)
    pending = _pending_summary(root)
    version = _version_tuple(root)
    version_status = _version_tuple_status(version)
    data_review = _data_review_queue_summary(root)
    flaky = _flaky_tracking_summary(root)
    pure_metrics_present = _has_complete_pure_search_metrics(root)

    hard_blocks: list[str] = []
    if selected_input["status"] == "missing":
        hard_blocks.append("no qualified full/global input")
    if text_risks["paths"]:
        hard_blocks.append("secret or mojibake risk in changed text files")
    if giant_paths and owner_boundary["status"] != "present":
        hard_blocks.append("giant owner files touched without owner_boundary governance manifest")
    if generated_knowledge_paths and pending.get("pending", 0):
        hard_blocks.append("generated knowledge changed while pending_full_validation entries remain")
    if flaky.get("triggered", 0):
        hard_blocks.append("flaky signatures reached governance threshold")
    giant_bridge_budget = int(owner_boundary.get("max_new_lines_in_any_giant_owner_file") or DEFAULT_GIANT_BRIDGE_LINE_BUDGET)
    giant_over_budget = [
        item for item in giant_change_summary["changed_files"]
        if int(item.get("added_lines", 0)) > giant_bridge_budget
    ]
    giant_missing_boundary = giant_paths and owner_boundary["status"] != "present"

    p0_status = "block" if hard_blocks else "warn" if (
        artifact_paths
        or giant_paths
        or pending.get("pending", 0)
        or generated_knowledge_paths
        or version_status["status"] != "complete"
        or data_review.get("open", 0)
        or code_health["status"] != "pass"
    ) else "pass"
    recommended_target = ""
    if flaky.get("triggered", 0):
        recommended_target = "diagnostic_completeness"
    elif generated_knowledge_paths and pending.get("pending", 0):
        recommended_target = "pending_validation_closure"
    elif giant_paths:
        recommended_target = "owner_boundary"
    elif artifact_paths:
        recommended_target = "artifact_hygiene"
    elif code_health["status"] != "pass":
        recommended_target = "code_health_triage"
    elif pending.get("pending", 0):
        recommended_target = "pending_validation_closure"
    elif selected_input["status"] == "missing":
        recommended_target = "baseline_freeze"
    elif version_status["status"] != "complete":
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
            "generated_knowledge_path_total": len(generated_knowledge_paths),
            "generated_knowledge_path_examples": generated_knowledge_paths[:20],
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
        "code_health_risk": code_health,
        "test_tier_plan": "targeted plus optional slice benchmark; full/global remains Step 5",
        "pure_search_risk": {
            "status": "pass" if pure_metrics_present else "missing",
            "pure_search_metrics_present": pure_metrics_present,
        },
        "baseline_snapshot": baseline,
        "version_tuple": version,
        "version_tuple_status": version_status,
        "selected_input": selected_input,
        "pending_full_validation_summary": pending,
        "data_review_queue_summary": {
            key: value for key, value in data_review.items()
            if key not in {"closed_sample_ids", "open_sample_ids"}
        },
        "flaky_tracking_summary": flaky,
        "full_validation_status": full_validation_status,
        "release_gate_status": "block" if pending.get("pending", 0) or generated_knowledge_paths else "warn",
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
    original_rows = build_rows(records)
    if not original_rows:
        raise SystemExit("no wrong samples found in latest input")
    data_review = _data_review_queue_summary(root)
    closed_review_ids = set(data_review.get("closed_sample_ids") or [])
    rows = [row for row in original_rows if row.get("sample_id") not in closed_review_ids]
    excluded_rows = [row for row in original_rows if row.get("sample_id") in closed_review_ids]
    skipped_units_by_issue, blocked_units_by_issue, skipped_units_by_id, blocked_units_by_id = _selector_state_units(root)
    skip_issue_keys = set(skipped_units_by_issue)
    summary: dict[str, Any]
    next_action: dict[str, Any]
    exact_skip_iterations = 0
    while True:
        if rows:
            summary = build_summary(rows, latest, attribution, skip_issue_keys=skip_issue_keys)
            summary = _normalize_v36_commonality(summary)
        else:
            summary = {
                "schema_version": SCHEMA_VERSION,
                "generated_at": _now_iso(),
                "input_latest_path": str(latest),
                "input_attribution_path": str(attribution),
                "wrong_total": 0,
                "stage_counts": {},
                "missing_field_rate": 0.0,
                "largest_bucket": "",
                "common_issue_clusters": [],
                "skipped_pending_validation_clusters": [],
                "target_common_issue": {},
                "cluster_selection_reason": "all wrong samples excluded by data review queue",
            }
        pure_metrics_present = _has_complete_pure_search_metrics(root)
        next_action = _build_v36_next_action(summary, rows, pure_metrics_present=pure_metrics_present)
        current_repair_unit_id = str(next_action.get("repair_unit_id") or "")
        current_skipped_unit = skipped_units_by_id.get(current_repair_unit_id)
        current_issue_key = _repair_unit_issue_key(next_action)
        if current_skipped_unit and current_issue_key and current_issue_key not in skip_issue_keys:
            skipped_units_by_issue[current_issue_key] = dict(current_skipped_unit)
            skip_issue_keys.add(current_issue_key)
            exact_skip_iterations += 1
            if exact_skip_iterations <= 20:
                continue
        break
    summary["wrong_total_before_data_review_exclusion"] = len(original_rows)
    summary["data_review_exclusion_summary"] = {
        "queue_path": DATA_REVIEW_QUEUE_PATH.as_posix(),
        "excluded_total": len(excluded_rows),
        "excluded_sample_ids": [row.get("sample_id", "") for row in excluded_rows[:50]],
        "open_total": data_review.get("open", 0),
    }
    if next_action.get("action") == "fix_r1_recall":
        next_action["pure_search_metrics_required"] = False
    elif "pure_search_metrics missing" in str(next_action.get("reason") or ""):
        next_action["pure_search_metrics_required"] = True
    next_action["selector_state_inputs"] = {
        "pending_full_validation_path": "reports/agent_state/v36_pending_full_validation.json",
        "round_manifest_glob": "reports/attribution/v36_round_manifest_*.json",
        "data_review_queue_path": DATA_REVIEW_QUEUE_PATH.as_posix(),
        "pending_issue_key_count": len(skip_issue_keys),
        "pending_repair_unit_id_count": len(skipped_units_by_id),
        "blocked_next_stage_issue_key_count": len(blocked_units_by_issue),
        "blocked_next_stage_repair_unit_id_count": len(blocked_units_by_id),
    }
    next_action["skipped_repair_units"] = _enrich_skipped_repair_units(
        summary,
        skipped_units_by_issue,
        skipped_units_by_id,
    )
    next_action["blocked_next_stage_repair_units"] = list(blocked_units_by_issue.values()) + list(blocked_units_by_id.values())
    next_action["data_review_queue_update"] = _update_data_review_queue(
        root,
        next_action,
        latest_path=latest_abs,
        attribution_path=attribution_abs,
    )
    next_action["accuracy_goal_context"] = _accuracy_goal_context(root)

    decision_abs = (root / decision_table_path).resolve() if not decision_table_path.is_absolute() else decision_table_path
    decision_abs.parent.mkdir(parents=True, exist_ok=True)
    with decision_abs.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(original_rows[0].keys()))
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
        "wrong_total_before_data_review_exclusion": len(original_rows),
        "data_review_exclusion_summary": summary["data_review_exclusion_summary"],
        "action": next_action["action"],
        "target_common_issue": next_action.get("target_common_issue", {}),
        "full_validation_status": next_action.get("full_validation_status", "pending"),
        "selector_state_inputs": next_action["selector_state_inputs"],
        "skipped_repair_units": next_action["skipped_repair_units"],
        "blocked_next_stage_repair_units": next_action["blocked_next_stage_repair_units"],
        "accuracy_goal_context": next_action["accuracy_goal_context"],
        "deterministic": True,
        "llm_used": False,
    }


def _as_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return None


def _numbers_match(left: Any, right: Any, *, tolerance: float = 0.05) -> bool:
    left_num = _as_number(left)
    right_num = _as_number(right)
    if left_num is None or right_num is None:
        return False
    return abs(left_num - right_num) <= tolerance


def _summary_overall(summary_payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(summary_payload, dict):
        return {}
    overall = summary_payload.get("json_overall")
    if isinstance(overall, dict):
        return overall
    overall = summary_payload.get("overall")
    if isinstance(overall, dict):
        return overall
    return summary_payload


def _int_metric(payload: dict[str, Any], *names: str) -> int:
    for name in names:
        number = _as_number(payload.get(name))
        if number is not None:
            return int(round(number))
    return 0


def _float_metric(payload: dict[str, Any], *names: str) -> float:
    for name in names:
        number = _as_number(payload.get(name))
        if number is not None:
            return float(number)
    return 0.0


def _remaining_wrong_by_stage(overall: dict[str, Any]) -> dict[str, int]:
    raw_counts = overall.get("error_stage_counts")
    if not isinstance(raw_counts, dict):
        raw_counts = overall.get("stage_counts")
    if not isinstance(raw_counts, dict):
        raw_counts = {}
    result: dict[str, int] = {}
    for key, value in raw_counts.items():
        number = _as_number(value)
        if number is not None:
            result[str(key)] = int(round(number))
    if result:
        return result
    fallback = {
        "retriever": overall.get("recall_miss_count"),
        "ranker": overall.get("rank_miss_count"),
        "post_rank": overall.get("post_rank_miss_count") or overall.get("confidence_miss_count"),
    }
    for key, value in fallback.items():
        number = _as_number(value)
        if number is not None and number > 0:
            result[key] = int(round(number))
    return result


def _largest_bottleneck(remaining_wrong_by_stage: dict[str, int]) -> str:
    if not remaining_wrong_by_stage:
        return ""
    return max(remaining_wrong_by_stage.items(), key=lambda item: (item[1], item[0]))[0]


def _goal_priority_hint(bottleneck: str) -> str:
    normalized = str(bottleneck or "").strip().lower()
    if normalized in {"retriever", "recall", "recall_miss", "route", "candidate_pool"}:
        return "prefer_recall_or_candidate_pool"
    if normalized in {"ranker", "rank", "rank_miss"}:
        return "prefer_rank_param_alignment"
    if normalized in {"ltr_ranker", "cgr_ranker", "final_validator", "post_rank"}:
        return "prefer_post_rank_guard"
    if normalized:
        return "inspect_largest_bottleneck"
    return "missing_full_global_progress"


def _milestone_for_rate(hit_rate: float) -> tuple[str, str, float]:
    for name, threshold in ACCURACY_GOAL_MILESTONES:
        if hit_rate < threshold:
            return name, "in_progress", threshold
    return ACCURACY_GOAL_MILESTONES[-1][0], "pass", ACCURACY_GOAL_MILESTONES[-1][1]


def _achieved_milestones(hit_rate: float) -> list[str]:
    return [name for name, threshold in ACCURACY_GOAL_MILESTONES if hit_rate >= threshold]


def _goal_history_entry(
    *,
    generated_at: str,
    summary_path: str,
    total: int,
    correct: int,
    hit_rate: float,
    remaining_wrong_by_stage: dict[str, int],
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "summary_path": summary_path,
        "total": total,
        "correct": correct,
        "hit_rate": hit_rate,
        "remaining_wrong_by_stage": remaining_wrong_by_stage,
    }


def _append_goal_history(previous: dict[str, Any], entry: dict[str, Any]) -> list[dict[str, Any]]:
    raw_history = previous.get("history")
    history = [item for item in raw_history if isinstance(item, dict)] if isinstance(raw_history, list) else []
    comparable_keys = ("summary_path", "total", "correct", "hit_rate")
    if history and all(history[-1].get(key) == entry.get(key) for key in comparable_keys):
        history[-1] = entry
    else:
        history.append(entry)
    return history[-ACCURACY_GOAL_HISTORY_LIMIT:]


def _goal_history_status(history: list[dict[str, Any]]) -> dict[str, Any]:
    rates = [_as_number(item.get("hit_rate")) for item in history if isinstance(item, dict)]
    rates = [float(rate) for rate in rates if rate is not None]
    if len(rates) < 2:
        return {
            "status": "insufficient_history",
            "history_count": len(rates),
            "plateau_window": ACCURACY_GOAL_PLATEAU_WINDOW,
            "plateau_min_delta": ACCURACY_GOAL_PLATEAU_MIN_DELTA,
        }
    latest_delta = round(rates[-1] - rates[-2], 4)
    regression = len(rates) >= 3 and rates[-1] < rates[-2] and rates[-2] < rates[-3]
    plateau = False
    plateau_delta = None
    if len(rates) >= ACCURACY_GOAL_PLATEAU_WINDOW:
        window = rates[-ACCURACY_GOAL_PLATEAU_WINDOW:]
        plateau_delta = round(window[-1] - window[0], 4)
        plateau = plateau_delta < ACCURACY_GOAL_PLATEAU_MIN_DELTA
    if regression:
        status = "regression_block"
    elif plateau:
        status = "plateau_block"
    else:
        status = "progressing"
    return {
        "status": status,
        "history_count": len(rates),
        "latest_delta_hit_rate": latest_delta,
        "plateau_window": ACCURACY_GOAL_PLATEAU_WINDOW,
        "plateau_min_delta": ACCURACY_GOAL_PLATEAU_MIN_DELTA,
        "plateau_window_delta": plateau_delta,
    }


def _load_accuracy_goal_progress(root: Path) -> dict[str, Any]:
    path = root / ACCURACY_GOAL_PROGRESS_PATH
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "missing",
            "target_hit_rate": ACCURACY_GOAL_TARGET_HIT_RATE,
            "progress_path": ACCURACY_GOAL_PROGRESS_PATH.as_posix(),
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "invalid",
            "target_hit_rate": ACCURACY_GOAL_TARGET_HIT_RATE,
            "progress_path": ACCURACY_GOAL_PROGRESS_PATH.as_posix(),
        }
    if not isinstance(payload, dict):
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "invalid",
            "target_hit_rate": ACCURACY_GOAL_TARGET_HIT_RATE,
            "progress_path": ACCURACY_GOAL_PROGRESS_PATH.as_posix(),
        }
    payload.setdefault("status", "present")
    payload.setdefault("progress_path", ACCURACY_GOAL_PROGRESS_PATH.as_posix())
    return payload


def _accuracy_goal_context(root: Path) -> dict[str, Any]:
    progress = _load_accuracy_goal_progress(root)
    bottleneck = str(progress.get("largest_remaining_bottleneck") or "")
    return {
        "status": progress.get("status", "present"),
        "progress_path": progress.get("progress_path", ACCURACY_GOAL_PROGRESS_PATH.as_posix()),
        "milestone": progress.get("milestone", ""),
        "milestone_status": progress.get("milestone_status", ""),
        "current_hit_rate": progress.get("current_hit_rate"),
        "target_hit_rate": progress.get("target_hit_rate", ACCURACY_GOAL_TARGET_HIT_RATE),
        "needed_net_new_correct": progress.get("needed_net_new_correct"),
        "largest_remaining_bottleneck": bottleneck,
        "next_priority_hint": progress.get("next_priority_hint") or _goal_priority_hint(bottleneck),
    }


def update_goal_progress(root: Path | None, summary_path: Path, out_path: Path) -> dict[str, Any]:
    root = _repo_root(root)
    summary_abs = (root / summary_path).resolve() if not summary_path.is_absolute() else summary_path
    if not summary_abs.exists():
        raise SystemExit(f"summary not found: {summary_path}")
    try:
        summary_payload = json.loads(summary_abs.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"summary is not valid JSON: {summary_path}") from exc
    overall = _summary_overall(summary_payload)
    total = _int_metric(overall, "total", "json_total")
    correct = _int_metric(overall, "correct", "correct_total")
    hit_rate = _float_metric(overall, "hit_rate", "overall_hit_rate")
    if not hit_rate and total:
        hit_rate = round(correct / max(total, 1) * 100, 1)
    target_correct = int(math.ceil(total * ACCURACY_GOAL_TARGET_HIT_RATE / 100.0)) if total else 0
    remaining_wrong = _remaining_wrong_by_stage(overall)
    bottleneck = _largest_bottleneck(remaining_wrong)
    milestone, milestone_status, milestone_threshold = _milestone_for_rate(hit_rate)

    out_abs = (root / out_path).resolve() if not out_path.is_absolute() else out_path
    previous: dict[str, Any] = {}
    if out_abs.exists():
        try:
            loaded = json.loads(out_abs.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (json.JSONDecodeError, OSError):
            previous = {}

    baseline_total = _int_metric(previous, "baseline_total") or total
    baseline_correct = _int_metric(previous, "baseline_correct") or correct
    baseline_hit_rate = _float_metric(previous, "baseline_hit_rate") or hit_rate

    generated_at = _now_iso()
    summary_rel = _rel(root, summary_abs)
    history_entry = _goal_history_entry(
        generated_at=generated_at,
        summary_path=summary_rel,
        total=total,
        correct=correct,
        hit_rate=hit_rate,
        remaining_wrong_by_stage=remaining_wrong,
    )
    history = _append_goal_history(previous, history_entry)
    history_status = _goal_history_status(history)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "command": "update-goal-progress",
        "status": "present",
        "target_hit_rate": ACCURACY_GOAL_TARGET_HIT_RATE,
        "baseline_total": baseline_total,
        "baseline_correct": baseline_correct,
        "baseline_hit_rate": baseline_hit_rate,
        "current_total": total,
        "current_correct": correct,
        "current_hit_rate": hit_rate,
        "target_correct": target_correct,
        "needed_net_new_correct": max(target_correct - correct, 0),
        "milestone": milestone,
        "milestone_threshold": milestone_threshold,
        "milestone_status": milestone_status,
        "achieved_milestones": _achieved_milestones(hit_rate),
        "remaining_wrong_by_stage": remaining_wrong,
        "largest_remaining_bottleneck": bottleneck,
        "next_priority_hint": _goal_priority_hint(bottleneck),
        "history": history,
        "history_status": history_status,
        "last_full_global_summary": summary_rel,
        "progress_path": _rel(root, out_abs),
        "notes": [
            "Progress is informational until release-check confirms release gates.",
            "If benchmark scope or version tuple changes, target_correct is recalculated from current_total.",
        ],
    }
    _write_json(out_abs, payload)
    return payload


def _accuracy_goal_release_gate(root: Path, release_gate_status: str) -> dict[str, Any]:
    progress = _load_accuracy_goal_progress(root)
    status = str(progress.get("status") or "")
    if status in {"missing", "invalid"}:
        return {
            "status": "missing_progress" if status == "missing" else "invalid_progress",
            "progress_path": progress.get("progress_path", ACCURACY_GOAL_PROGRESS_PATH.as_posix()),
            "release_gate_required": "update-goal-progress",
            "target_hit_rate": ACCURACY_GOAL_TARGET_HIT_RATE,
        }
    achieved = list(progress.get("achieved_milestones") or [])
    current_hit_rate = _float_metric(progress, "current_hit_rate")
    history_status = progress.get("history_status") if isinstance(progress.get("history_status"), dict) else {}
    history_gate_status = str(history_status.get("status") or "")
    if history_gate_status in {"plateau_block", "regression_block"}:
        gate_status = history_gate_status
    elif release_gate_status != "pass":
        gate_status = "blocked_by_release_gate"
    elif current_hit_rate >= ACCURACY_GOAL_TARGET_HIT_RATE:
        gate_status = "target_pass"
    elif achieved:
        gate_status = "milestone_pass"
    else:
        gate_status = "in_progress"
    return {
        "status": gate_status,
        "progress_path": progress.get("progress_path", ACCURACY_GOAL_PROGRESS_PATH.as_posix()),
        "target_hit_rate": progress.get("target_hit_rate", ACCURACY_GOAL_TARGET_HIT_RATE),
        "current_hit_rate": progress.get("current_hit_rate"),
        "current_correct": progress.get("current_correct"),
        "target_correct": progress.get("target_correct"),
        "needed_net_new_correct": progress.get("needed_net_new_correct"),
        "milestone": progress.get("milestone", ""),
        "milestone_status": progress.get("milestone_status", ""),
        "achieved_milestones": achieved,
        "latest_achieved_milestone": achieved[-1] if achieved else "",
        "largest_remaining_bottleneck": progress.get("largest_remaining_bottleneck", ""),
        "next_priority_hint": progress.get("next_priority_hint", ""),
        "history_status": history_status,
    }


def _summary_metrics(root: Path, artifact_path: str) -> dict[str, Any]:
    if not artifact_path:
        return {"status": "missing", "path": ""}
    path = (root / artifact_path).resolve()
    if not path.exists() or not path.is_file():
        return {"status": "missing", "path": artifact_path}
    payload = _read_json_file(path)
    if not payload:
        return {"status": "invalid_json", "path": artifact_path}
    overall = payload.get("json_overall") if isinstance(payload.get("json_overall"), dict) else payload
    adaptive = overall.get("adaptive_strategy") if isinstance(overall, dict) else {}
    return {
        "status": "present",
        "path": artifact_path,
        "total": _as_number(overall.get("total")),
        "correct": _as_number(overall.get("correct") or overall.get("correct_total")),
        "hit_rate": _as_number(overall.get("hit_rate") or overall.get("overall_hit_rate")),
        "recall_miss_count": _as_number(overall.get("recall_miss_count")),
        "rank_miss_count": _as_number(overall.get("rank_miss_count")),
        "avg_latency_sec": _as_number(adaptive.get("overall_avg_time_sec")) if isinstance(adaptive, dict) else None,
    }


def _compare_declared_delta(delta: dict[str, Any], before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    checks = [
        ("slice_total", before.get("total")),
        ("before_correct", before.get("correct")),
        ("after_correct", after.get("correct")),
        ("before_hit_rate", before.get("hit_rate")),
        ("after_hit_rate", after.get("hit_rate")),
    ]
    for field, computed in checks:
        if field in delta and computed is not None and not _numbers_match(delta.get(field), computed):
            failures.append({"field": field, "declared": delta.get(field), "computed": computed})
    before_hit = before.get("hit_rate")
    after_hit = after.get("hit_rate")
    if "delta_hit_rate" in delta and before_hit is not None and after_hit is not None:
        computed_delta = after_hit - before_hit
        if not _numbers_match(delta.get("delta_hit_rate"), computed_delta):
            failures.append({"field": "delta_hit_rate", "declared": delta.get("delta_hit_rate"), "computed": computed_delta})
    return failures


def _manifest_report_path(manifest: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = manifest.get(key)
        if value:
            return str(value)
    for container_name in ("artifacts", "reports", "round_artifact_manifest"):
        container = manifest.get(container_name)
        if not isinstance(container, dict):
            continue
        for key in keys:
            value = container.get(key)
            if value:
                return str(value)
    return ""


def _resolve_status_from_report(
    root: Path,
    manifest: dict[str, Any],
    *,
    manifest_status_field: str,
    report_path_keys: tuple[str, ...],
    report_status_fields: tuple[str, ...],
) -> dict[str, Any]:
    declared = str(manifest.get(manifest_status_field) or "missing")
    report_path = _manifest_report_path(manifest, report_path_keys)
    if not report_path:
        return {
            "status": declared,
            "source": "manifest_field",
            "path": "",
            "declared": declared,
            "integrity_status": "legacy_manifest_field",
            "failures": [],
        }

    report_abs = (root / report_path).resolve()
    report = _read_json_file(report_abs)
    if not report:
        return {
            "status": "missing",
            "source": "report",
            "path": report_path,
            "declared": declared,
            "integrity_status": "fail",
            "failures": [{"field": manifest_status_field, "reason": "report_missing_or_invalid", "path": report_path}],
        }

    value = ""
    for field in report_status_fields:
        if report.get(field) not in (None, ""):
            value = str(report[field])
            break
    failures: list[dict[str, Any]] = []
    if not value:
        value = "missing"
        failures.append({"field": manifest_status_field, "reason": "status_field_missing_in_report", "path": report_path})
    elif declared != "missing" and declared != value:
        failures.append(
            {
                "field": manifest_status_field,
                "reason": "manifest_status_mismatch",
                "declared": declared,
                "reported": value,
                "path": report_path,
            }
        )
    return {
        "status": value,
        "source": "report",
        "path": report_path,
        "declared": declared,
        "integrity_status": "fail" if failures else "pass",
        "failures": failures,
    }


def _non_empty_string_or_list(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(str(item or "").strip() for item in value)
    return False


def _rollback_plan_integrity(manifest: dict[str, Any], *, required: bool) -> dict[str, Any]:
    plan = manifest.get("rollback_plan")
    if not isinstance(plan, dict):
        if not required:
            return {"status": "not_required", "failures": [], "rollback_type": ""}
        return {
            "status": "fail",
            "failures": [{"field": "rollback_plan", "reason": "missing_or_not_object"}],
            "rollback_type": "",
        }

    failures: list[dict[str, Any]] = []
    rollback_type = str(plan.get("rollback_type") or "")
    if rollback_type not in VALID_ROLLBACK_TYPES:
        failures.append(
            {
                "field": "rollback_type",
                "reason": "invalid",
                "allowed": sorted(VALID_ROLLBACK_TYPES),
                "value": rollback_type,
            }
        )
    for field in ("rollback_target", "rollback_command_or_change", "post_rollback_validation"):
        if not _non_empty_string_or_list(plan.get(field)):
            failures.append({"field": field, "reason": "missing_or_empty"})
    affected = plan.get("affected_files")
    if not isinstance(affected, list) or not any(str(item or "").strip() for item in affected):
        failures.append({"field": "affected_files", "reason": "missing_or_empty"})
    return {
        "status": "fail" if failures else "pass",
        "failures": failures,
        "rollback_type": rollback_type,
    }


def _derive_step4_status(
    *,
    manifest: dict[str, Any],
    artifact_integrity_failures: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
    policy_status: str,
    regression_status: str,
) -> str:
    if artifact_integrity_failures:
        return "failed"
    if policy_status == "fail" or regression_status == "fail":
        return "failed"

    before_correct = before.get("correct")
    after_correct = after.get("correct")
    improved = before_correct is not None and after_correct is not None and after_correct > before_correct
    regressed = before_correct is not None and after_correct is not None and after_correct < before_correct
    if regressed:
        return "failed"

    failed_next = manifest.get("failed_slice_next_action")
    lifecycle = manifest.get("candidate_lifecycle_trace")
    lifecycle_status = lifecycle.get("status") if isinstance(lifecycle, dict) else ""
    declared = str(manifest.get("partial_validation_status") or "")

    if improved and not isinstance(failed_next, dict):
        return "benchmark_pass"
    if lifecycle_status == "candidate_lifecycle_pass" or declared == "candidate_lifecycle_pass" or improved:
        return "candidate_lifecycle_pass"
    if declared in {"local_behavior_pass", "blocked_by_next_stage", "diagnostic_pass"}:
        return declared
    return "failed"


def _goal_contribution_integrity(manifest: dict[str, Any], *, required: bool) -> dict[str, Any]:
    contribution = manifest.get("goal_contribution")
    if not isinstance(contribution, dict):
        return {
            "status": "missing" if required else "not_required",
            "required": required,
            "failures": [{"field": "goal_contribution", "reason": "missing"}] if required else [],
            "goal_contribution": contribution if contribution is not None else "missing",
        }

    failures: list[dict[str, Any]] = []

    stage = str(contribution.get("stage") or "")
    if stage not in GOAL_CONTRIBUTION_STAGES:
        failures.append({"field": "goal_contribution.stage", "reason": "invalid_or_missing", "value": stage})

    benefit = str(contribution.get("expected_benefit") or "")
    if benefit not in GOAL_CONTRIBUTION_BENEFITS:
        failures.append({"field": "goal_contribution.expected_benefit", "reason": "invalid_or_missing", "value": benefit})

    accuracy_budget = contribution.get("accuracy_budget")
    if not isinstance(accuracy_budget, str) or not accuracy_budget.strip():
        failures.append({"field": "goal_contribution.accuracy_budget", "reason": "missing"})

    speed_budget = str(contribution.get("speed_budget") or "")
    if speed_budget not in GOAL_CONTRIBUTION_SPEED_BUDGETS:
        failures.append({"field": "goal_contribution.speed_budget", "reason": "invalid_or_missing", "value": speed_budget})

    complexity_budget = str(contribution.get("complexity_budget") or "")
    if complexity_budget not in GOAL_CONTRIBUTION_COMPLEXITY_BUDGETS:
        failures.append(
            {"field": "goal_contribution.complexity_budget", "reason": "invalid_or_complexity_increase", "value": complexity_budget}
        )

    if contribution.get("forbidden_shortcut_checked") is not True:
        failures.append({"field": "goal_contribution.forbidden_shortcut_checked", "reason": "must_be_true"})

    return {
        "status": "fail" if failures else "pass",
        "required": required,
        "failures": failures,
        "goal_contribution": contribution,
    }


def validate_step4_manifest(
    root: Path | None,
    manifest_path: Path,
    out_path: Path | None = None,
) -> dict[str, Any]:
    root = _repo_root(root)
    manifest_abs = (root / manifest_path).resolve() if not manifest_path.is_absolute() else manifest_path
    manifest = _read_json_file(manifest_abs)
    if not manifest:
        raise SystemExit(f"manifest not found or invalid: {manifest_path}")

    delta = manifest.get("before_after_delta") if isinstance(manifest.get("before_after_delta"), dict) else {}
    before_artifact = str(delta.get("before_artifact") or "")
    after_artifact = str(delta.get("after_artifact") or "")
    before = _summary_metrics(root, before_artifact)
    after = _summary_metrics(root, after_artifact)

    artifact_failures: list[dict[str, Any]] = []
    if before_artifact or after_artifact:
        if before.get("status") != "present":
            artifact_failures.append({"field": "before_artifact", "reason": before.get("status"), "path": before_artifact})
        if after.get("status") != "present":
            artifact_failures.append({"field": "after_artifact", "reason": after.get("status"), "path": after_artifact})
    if before.get("status") == "present" and after.get("status") == "present":
        artifact_failures.extend(_compare_declared_delta(delta, before, after))

    policy_resolution = _resolve_status_from_report(
        root,
        manifest,
        manifest_status_field="policy_check_status",
        report_path_keys=("policy_check_report", "policy_check_path", "policy_check"),
        report_status_fields=("policy_check_status", "status"),
    )
    regression_resolution = _resolve_status_from_report(
        root,
        manifest,
        manifest_status_field="regression_golden_status",
        report_path_keys=("regression_golden_report", "regression_golden_path", "regression_golden"),
        report_status_fields=("regression_golden_status", "status"),
    )
    report_failures = list(policy_resolution["failures"]) + list(regression_resolution["failures"])
    policy_status = str(policy_resolution["status"] or "missing")
    regression_status = str(regression_resolution["status"] or "missing")

    derived_status = _derive_step4_status(
        manifest=manifest,
        artifact_integrity_failures=artifact_failures + report_failures,
        before=before,
        after=after,
        policy_status=policy_status,
        regression_status=regression_status,
    )
    declared_status = str(manifest.get("partial_validation_status") or "")
    agent_claim_mismatch = bool(declared_status and declared_status != derived_status)
    rollback_integrity = _rollback_plan_integrity(
        manifest,
        required=declared_status == "benchmark_pass" or derived_status == "benchmark_pass",
    )
    rollback_failures = list(rollback_integrity.get("failures") or [])
    goal_contribution_integrity = _goal_contribution_integrity(
        manifest,
        required=declared_status == "benchmark_pass" or derived_status == "benchmark_pass",
    )
    goal_contribution_failures = list(goal_contribution_integrity.get("failures") or [])

    before_correct = before.get("correct")
    after_correct = after.get("correct")
    before_hit = before.get("hit_rate")
    after_hit = after.get("hit_rate")
    before_latency = before.get("avg_latency_sec")
    after_latency = after.get("avg_latency_sec")
    before_recall_miss = before.get("recall_miss_count")
    after_recall_miss = after.get("recall_miss_count")
    has_failed_next = isinstance(manifest.get("failed_slice_next_action"), dict)

    top1_pass = (
        before_correct is not None
        and after_correct is not None
        and after_correct >= before_correct
    )
    recall_pass = (
        before_recall_miss is not None
        and after_recall_miss is not None
        and after_recall_miss <= before_recall_miss
    )
    latency_pass = (
        before_latency is not None
        and after_latency is not None
        and after_latency <= before_latency * 1.10
    )
    complexity_pass = policy_status == "pass"
    threshold_check = {
        "top1_pass": top1_pass,
        "recall_at_20_pass": recall_pass,
        "total_p95_pass": latency_pass,
        "stage_p95_pass": "missing",
        "complexity_pass": complexity_pass,
        "version_tuple_pass": "missing",
        "flaky_pass": "missing",
        "goal_contribution_pass": not goal_contribution_failures,
        "overall_pass": (
            derived_status == "benchmark_pass"
            and top1_pass
            and (recall_pass or before_recall_miss is None)
            and (latency_pass or before_latency is None)
            and complexity_pass
            and regression_status.startswith("pass")
            and not has_failed_next
            and not artifact_failures
            and not report_failures
            and not rollback_failures
            and not goal_contribution_failures
        ),
        "baseline_id": str(manifest.get("baseline_snapshot") or ""),
        "comparison_command": "validate-step4-manifest",
        "tradeoff_mode": str(manifest.get("tradeoff_mode") or "none"),
    }

    accuracy_delta = after_hit - before_hit if before_hit is not None and after_hit is not None else None
    speed_delta = after_latency - before_latency if before_latency is not None and after_latency is not None else None
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "command": "validate-step4-manifest",
        "manifest_path": _rel(root, manifest_abs),
        "declared_partial_validation_status": declared_status,
        "derived_partial_validation_status": derived_status,
        "partial_validation_status": derived_status,
        "agent_claim_mismatch": agent_claim_mismatch,
        "artifact_integrity": {
            "status": "fail" if artifact_failures else "pass" if before_artifact or after_artifact else "missing",
            "failures": artifact_failures,
            "before_metrics": before,
            "after_metrics": after,
        },
        "report_integrity": {
            "status": "fail" if report_failures else "pass",
            "failures": report_failures,
            "policy_check": policy_resolution,
            "regression_golden": regression_resolution,
        },
        "rollback_integrity": rollback_integrity,
        "goal_contribution_integrity": goal_contribution_integrity,
        "accuracy_impact": "improve" if accuracy_delta and accuracy_delta > 0 else "regress" if accuracy_delta and accuracy_delta < 0 else "neutral_or_missing",
        "accuracy_delta_hit_rate": accuracy_delta,
        "speed_impact": "slower" if speed_delta and speed_delta > 0 else "faster" if speed_delta and speed_delta < 0 else "neutral_or_missing",
        "speed_delta_avg_sec": speed_delta,
        "complexity_impact": "pass" if complexity_pass else "missing_or_fail",
        "complexity_delta": manifest.get("complexity_delta", "missing"),
        "threshold_check": threshold_check,
        "policy_check_status": policy_status,
        "regression_golden_status": regression_status,
        "failed_slice_next_action_present": has_failed_next,
        "register_validation_allowed": threshold_check["overall_pass"],
        "validation_status": "fail" if artifact_failures or report_failures or rollback_failures or goal_contribution_failures or (declared_status == "benchmark_pass" and derived_status != "benchmark_pass") else "warn" if agent_claim_mismatch else "pass",
    }
    if out_path is not None:
        out_abs = (root / out_path).resolve() if not out_path.is_absolute() else out_path
        _write_json(out_abs, payload)
    return payload


def register_validation(
    root: Path | None,
    fix_id: str,
    action: str,
    description: str,
    files: list[str],
    validation: list[str],
    status: str,
    ledger_path: Path,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    root = _repo_root(root)
    manifest_rel = ""
    if status == "pending_full_validation":
        if manifest_path is None:
            raise SystemExit("register-validation requires --manifest for pending_full_validation")
        manifest_abs = (root / manifest_path).resolve() if not manifest_path.is_absolute() else manifest_path
        manifest = _read_json_file(manifest_abs)
        if not manifest:
            raise SystemExit(f"manifest not found or invalid: {manifest_path}")
        manifest_rel = _rel(root, manifest_abs)
        validation_result = validate_step4_manifest(root, manifest_abs)
        partial_status = str(validation_result.get("derived_partial_validation_status") or "")
        if partial_status != "benchmark_pass" or not validation_result.get("register_validation_allowed"):
            raise SystemExit(
                "register-validation refused: validate-step4-manifest must derive benchmark_pass "
                f"(got {partial_status or 'missing'})"
            )
        if validation_result.get("agent_claim_mismatch"):
            raise SystemExit("register-validation refused: manifest claim mismatch")
        regression_status = str(validation_result.get("regression_golden_status") or "")
        if not regression_status.startswith("pass"):
            raise SystemExit(
                "register-validation refused: regression_golden_status must be pass "
                f"(got {regression_status or 'missing'})"
            )
        policy_status = str(validation_result.get("policy_check_status") or "")
        if policy_status != "pass":
            raise SystemExit(
                "register-validation refused: policy_check_status must be pass "
                f"(got {policy_status or 'missing'})"
            )
        if validation_result.get("failed_slice_next_action_present"):
            raise SystemExit("register-validation refused: failed_slice_next_action is present")

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
    if manifest_rel:
        entry["source_manifest"] = manifest_rel
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
        "source_manifest": manifest_rel,
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
    if int(pending.get("pending", 0) or 0) >= PENDING_FULL_VALIDATION_LIMIT:
        release_status = "block"
        reasons.append(f"pending_full_validation reaches limit {PENDING_FULL_VALIDATION_LIMIT}; run Step 5 full/global")
    if not ledger_abs.exists():
        release_status = "block"
        reasons.append("pending validation ledger is missing")
    accuracy_goal_gate = _accuracy_goal_release_gate(root, release_status)
    next_allowed_action = "release"
    if int(pending.get("pending", 0) or 0) >= PENDING_FULL_VALIDATION_LIMIT:
        next_allowed_action = "step5_full_global"
    elif accuracy_goal_gate["status"] in {"plateau_block", "regression_block"}:
        next_allowed_action = "diagnostics_or_governance"
    elif release_status == "block":
        next_allowed_action = "resolve_release_blocks"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "command": "release-check",
        "release_gate_status": release_status,
        "block_reasons": reasons,
        "selected_input": selected,
        "pending_full_validation_summary": pending,
        "accuracy_goal_progress": accuracy_goal_gate,
        "accuracy_goal_gate_status": accuracy_goal_gate["status"],
        "next_allowed_action": next_allowed_action,
    }


def goal_next(root: Path | None, out_path: Path) -> dict[str, Any]:
    root = _repo_root(root)
    selected = _find_global_input(root)
    preflight = build_preflight(root)

    progress: dict[str, Any] = _load_accuracy_goal_progress(root)
    summary_path = str(selected.get("summary_path") or "")
    if summary_path:
        summary_abs = root / summary_path
        if summary_abs.exists():
            progress = update_goal_progress(root, Path(summary_path), ACCURACY_GOAL_PROGRESS_PATH)

    release = release_check(root, Path("reports/agent_state/v36_pending_full_validation.json"))
    pending = release.get("pending_full_validation_summary") if isinstance(release, dict) else {}
    pending_count = int(pending.get("pending", 0) or 0) if isinstance(pending, dict) else 0
    progress_gate = str(release.get("accuracy_goal_gate_status") or "")

    next_action_result: dict[str, Any] | None = None
    decision = "choose_next_action"
    autonomous_allowed = True
    requires_user_confirmation = False
    stop_reason = ""
    execution_class = "step4_small_patch"

    if selected.get("status") != "present":
        decision = "prepare_full_global_input"
        autonomous_allowed = False
        requires_user_confirmation = True
        stop_reason = "qualified full/global input is missing"
        execution_class = "input_preparation"
    elif preflight.get("p0_gate_status") == "block":
        decision = "p0_remediation"
        autonomous_allowed = True
        execution_class = "governance_patch"
        stop_reason = "p0 gate blocks algorithm repair; only bounded P0 remediation is allowed"
    elif pending_count >= PENDING_FULL_VALIDATION_LIMIT:
        decision = "step5_full_global"
        execution_class = "long_validation"
        stop_reason = "pending_full_validation limit reached; run Step 5 before more Step 4 patches"
    elif progress_gate in {"plateau_block", "regression_block"}:
        decision = "diagnostics_or_governance"
        execution_class = "diagnostics"
        stop_reason = f"accuracy goal history status is {progress_gate}; refresh diagnostics before more patches"
    elif release.get("accuracy_goal_gate_status") == "target_pass" and release.get("release_gate_status") == "pass":
        decision = "release"
        autonomous_allowed = False
        requires_user_confirmation = True
        execution_class = "release"
        stop_reason = "75% target and release gate passed; release requires human approval"
    else:
        next_action_result = choose_next_action(
            root,
            None,
            None,
            Path("reports/attribution/global_repair_decision_table.csv"),
            Path("reports/attribution/global_repair_decision_summary.json"),
            Path("reports/attribution/global_repair_next_action.json"),
        )
        action = str(next_action_result.get("action") or "")
        decision = action or "choose_next_action"
        execution_class = "diagnostics" if action in {"improve_diagnostics", "review_data"} else "step4_small_patch"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "command": "goal-next",
        "mode": "autonomous_goal",
        "target_hit_rate": ACCURACY_GOAL_TARGET_HIT_RATE,
        "decision": decision,
        "execution_class": execution_class,
        "autonomous_allowed": autonomous_allowed,
        "requires_user_confirmation": requires_user_confirmation,
        "stop_reason": stop_reason,
        "selected_input": selected,
        "p0_gate_status": preflight.get("p0_gate_status"),
        "p0_block_reasons": preflight.get("block_reasons", []),
        "pending_full_validation_summary": pending,
        "accuracy_goal_progress": _accuracy_goal_context(root),
        "accuracy_goal_gate_status": release.get("accuracy_goal_gate_status"),
        "release_gate_status": release.get("release_gate_status"),
        "next_action_result": next_action_result or {},
        "next_action_path": "reports/attribution/global_repair_next_action.json" if next_action_result else "",
        "autonomy_budget": {
            "pending_full_validation_limit": PENDING_FULL_VALIDATION_LIMIT,
            "pending_full_validation_count": pending_count,
            "remaining_step4_before_step5": max(PENDING_FULL_VALIDATION_LIMIT - pending_count, 0),
            "one_repair_unit_per_round": True,
        },
        "agent_instruction": _goal_agent_instruction(decision, execution_class, autonomous_allowed),
        "stop_conditions": [
            "p0_gate_status=block and remediation is outside the reported P0 target",
            "validate-step4-manifest does not derive benchmark_pass",
            "goal_contribution is missing or exceeds speed/complexity budget",
            "pending_full_validation reaches the configured limit",
            "accuracy goal history reports plateau_block or regression_block",
            "release, destructive cleanup, generated knowledge publication, or trade-off approval is required",
        ],
    }
    out_abs = (root / out_path).resolve() if not out_path.is_absolute() else out_path
    _write_json(out_abs, payload)
    return payload


def _goal_agent_instruction(decision: str, execution_class: str, autonomous_allowed: bool) -> str:
    if not autonomous_allowed:
        return "Stop and report the requested approval boundary; do not modify algorithm code."
    if decision == "step5_full_global":
        return "Run Step 5 full/global validation according to V36, then update-goal-progress and release-check."
    if decision == "p0_remediation":
        return "Perform only the bounded P0 remediation reported by preflight, then rerun goal-next."
    if decision == "diagnostics_or_governance":
        return "Refresh diagnostics or governance artifacts only; do not add another algorithm patch yet."
    if execution_class == "diagnostics":
        return "Execute the diagnostic or data-review action from global_repair_next_action.json, then rerun goal-next."
    return "Execute exactly one Step 4 minimal repair from global_repair_next_action.json, include goal_contribution, validate, and register only if V36 gates pass."


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
    target_commonality = str(target.get("commonality") or "")
    if target_commonality and target_commonality != "shared":
        next_allowed = "improve_diagnostics"
    else:
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

    goal_parser = sub.add_parser("update-goal-progress")
    goal_parser.add_argument("--summary", type=Path, default=Path("reports/attribution/global_repair_v36_full_summary.json"))
    goal_parser.add_argument("--out", type=Path, default=ACCURACY_GOAL_PROGRESS_PATH)

    goal_next_parser = sub.add_parser("goal-next")
    goal_next_parser.add_argument("--out", type=Path, default=GOAL_NEXT_PATH)

    validate_step4_parser = sub.add_parser("validate-step4-manifest")
    validate_step4_parser.add_argument("--manifest", type=Path, required=True)
    validate_step4_parser.add_argument("--out", type=Path, default=Path("reports/attribution/v36_step4_validation.json"))

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
    register_parser.add_argument("--manifest", type=Path)

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
    elif args.command == "update-goal-progress":
        result = update_goal_progress(root, args.summary, args.out)
    elif args.command == "goal-next":
        result = goal_next(root, args.out)
    elif args.command == "validate-step4-manifest":
        result = validate_step4_manifest(root, args.manifest, args.out)
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
            args.manifest,
        )
    elif args.command == "release-check":
        result = release_check(root, args.ledger)
    else:
        raise SystemExit(f"unsupported command: {args.command}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
