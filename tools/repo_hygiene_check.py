"""Check that generated assets are not tracked by Git."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


FORBIDDEN_PREFIXES = (
    "models/",
    "output/",
    "logs/",
    "tmp/",
    "temp/",
    "test_artifacts/",
    "raw_files/",
    "reports/agent_state/",
    "reports/attribution/",
    "data/experience/",
    "data/reference/",
    "data/quota_rules/",
    "data/quota_data/",
    "data/pdf_info_price/",
    "data/oss_samples/",
    "data/source_packs/",
    "data/goal_search/hard_pairs/",
    "db/provinces/",
    "db/chroma/",
    "db/chroma_cache/",
    ".codex_tmp",
    ".pytest_tmp",
    "pytest_tmp_",
    "2.计价/",
)

FORBIDDEN_EXACT = {
    "experience.db",
    "tests/benchmark_papers/_latest_result.json",
    "eval/v36_seed.json",
    "tools/.collect_history.json",
    "tools/.pull_history.json",
    "tools/download_oss_remaining.py",
}

FORBIDDEN_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".db-wal",
    ".db-shm",
    ".bak",
    ".tar",
    ".lpk",
)

LARGE_FILE_DEFAULT_MB = 50


def run_git(args: list[str]) -> bytes:
    return subprocess.check_output(["git", "-c", "core.quotepath=false", *args])


def tracked_files() -> list[str]:
    raw = run_git(["ls-files", "-z"])
    return [entry.decode("utf-8", errors="replace") for entry in raw.split(b"\0") if entry]


def normalize(path: str) -> str:
    return path.replace("\\", "/")


def is_forbidden(path: str) -> str | None:
    normalized = normalize(path)
    lower = normalized.lower()
    if normalized in FORBIDDEN_EXACT:
        return "forbidden generated file"
    if any(normalized.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
        return "forbidden generated path"
    if "/__pycache__/" in normalized or normalized.startswith("__pycache__/"):
        return "python bytecode cache"
    if any(lower.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES):
        return "forbidden generated suffix"
    if lower.startswith(".codex_tmp") or lower.startswith(".pytest_tmp") or lower.startswith("pytest_tmp_"):
        return "temporary workspace"
    if lower.startswith("db/common/") and lower.endswith(".db"):
        return "local database"
    if lower.startswith("data/goal_search/ltr_features_"):
        return "generated LTR feature data"
    if lower.startswith("data/goal_search/ltr_matrix_"):
        return "generated LTR matrix data"
    if lower.startswith("data/goal_search/ltr_group_"):
        return "generated LTR group data"
    if lower.startswith("data/") and lower.endswith(".pdf"):
        return "large source document"
    if lower.startswith("data/") and lower.endswith(".db"):
        return "local database"
    return None


def file_size_mb(path: str) -> float:
    try:
        return Path(path).stat().st_size / (1024 * 1024)
    except OSError:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-mb", type=float, default=LARGE_FILE_DEFAULT_MB)
    args = parser.parse_args()

    failures: list[str] = []
    for path in tracked_files():
        reason = is_forbidden(path)
        if reason:
            failures.append(f"{path}: {reason}")
            continue

        size_mb = file_size_mb(path)
        if size_mb > args.max_mb:
            failures.append(f"{path}: tracked file is {size_mb:.1f} MB > {args.max_mb:.1f} MB")

    if failures:
        print("Repository hygiene check failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("Repository hygiene check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
