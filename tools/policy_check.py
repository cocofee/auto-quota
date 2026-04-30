import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.v36_gate import (
    GENERATED_KNOWLEDGE_PREFIX,
    GIANT_OWNER_FILES,
    SECRET_PATTERNS,
    _find_owner_boundary_manifest,
    _rel,
)


SCHEMA_VERSION = "v36_policy_check.v1"
TRACKED_TEXT_SUFFIXES = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
}
SCAN_PATH_PREFIXES = ("src/", "web/", "tools/", "tests/", "docs/", "eval/")
OWNER_SCOPE_PRODUCTION_PREFIXES = ("src/", "web/")
MAX_UNTRACKED_SCAN_BYTES = 1_000_000
RESCUE_FUNCTION_PATTERN = re.compile(r"^\+\s*def\s+_apply_[A-Za-z0-9_]*_rescue\s*\(")
EXCEPT_PASS_PATTERN = re.compile(r"^\+\s*except\s+Exception\s*:\s*pass\b")
SSL_DISABLE_PATTERN = re.compile(r"^\+.*\bverify\s*=\s*False\b")
ROUTE_HINT_PATTERN = re.compile(
    r"^\+.*\b(synonym|synonyms|alias|aliases|keyword|keywords|route_hint|pattern|patterns)\b",
    re.IGNORECASE,
)
BRANCH_PATTERN = re.compile(r"^\+\s*(if|elif|match|case)\b")
PUBLIC_SYMBOL_PATTERN = re.compile(r"^\+\s*(def|class)\s+(?!_)([A-Za-z_][A-Za-z0-9_]*)")


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


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


def _git_status_paths(root: Path) -> list[str]:
    code, stdout, _ = _run_git(root, ["status", "--short", "-uall"])
    if code != 0:
        return []
    paths: list[str] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path.replace("\\", "/"))
    return paths


def _iter_added_lines(root: Path) -> list[tuple[str, str]]:
    added: list[tuple[str, str]] = []
    for args in (["diff", "--unified=0"], ["diff", "--cached", "--unified=0"]):
        code, stdout, _ = _run_git(root, args)
        if code != 0:
            continue
        current_path = ""
        for line in stdout.splitlines():
            if line.startswith("+++ b/"):
                current_path = line[6:].replace("\\", "/")
                continue
            if (
                not current_path
                or not current_path.startswith(SCAN_PATH_PREFIXES)
                or not line.startswith("+")
                or line.startswith("+++")
            ):
                continue
            added.append((current_path, line))
    for status_path in _git_status_paths(root):
        if not status_path.startswith(SCAN_PATH_PREFIXES):
            continue
        candidate = root / status_path
        if not candidate.exists() or not candidate.is_file() or candidate.suffix.lower() not in TRACKED_TEXT_SUFFIXES:
            continue
        try:
            if candidate.stat().st_size > MAX_UNTRACKED_SCAN_BYTES:
                continue
        except OSError:
            continue
        code, stdout, _ = _run_git(root, ["ls-files", "--error-unmatch", status_path])
        if code == 0:
            continue
        try:
            for line in candidate.read_text(encoding="utf-8", errors="replace").splitlines():
                added.append((status_path, f"+{line}"))
        except OSError:
            continue
    return added


def _load_json_file(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _owner_module_prefixes(next_action: dict[str, Any]) -> list[str]:
    scope = next_action.get("suggested_validation_scope")
    owner_raw = ""
    if isinstance(scope, dict):
        owner_raw = str(scope.get("owner_module") or "")
    repair_unit = next_action.get("repair_unit")
    if not owner_raw and isinstance(repair_unit, dict):
        owner_raw = str(repair_unit.get("owner_module") or "")
    if not owner_raw:
        owner_raw = str(next_action.get("owner_module") or "")
    prefixes: list[str] = []
    for item in re.split(r"[|,\s]+", owner_raw):
        normalized = item.strip().replace("\\", "/").strip("/")
        if normalized:
            prefixes.append(normalized)
    return prefixes


def _path_within_prefix(path: str, prefix: str) -> bool:
    normalized_path = path.replace("\\", "/").strip("/")
    normalized_prefix = prefix.replace("\\", "/").strip("/")
    return normalized_path == normalized_prefix or normalized_path.startswith(f"{normalized_prefix}/")


def _owner_scope_result(root: Path, status_paths: list[str], next_action_path: Path | None) -> dict[str, Any]:
    if next_action_path is None:
        return {"status": "not_configured", "path": "", "allowed_owner_modules": [], "violations": []}
    action_abs = (root / next_action_path).resolve() if not next_action_path.is_absolute() else next_action_path
    next_action = _load_json_file(action_abs)
    allowed = _owner_module_prefixes(next_action)
    production_paths = sorted(path for path in status_paths if path.startswith(OWNER_SCOPE_PRODUCTION_PREFIXES))
    violations = [
        path
        for path in production_paths
        if not any(_path_within_prefix(path, prefix) for prefix in allowed)
    ]
    return {
        "status": "fail" if violations else "pass",
        "path": _rel(root, action_abs),
        "repair_unit_id": str(next_action.get("repair_unit_id") or ""),
        "action": str(next_action.get("action") or ""),
        "allowed_owner_modules": allowed,
        "production_paths_checked": production_paths,
        "violations": violations,
    }


def run_policy_check(root: Path | None = None, next_action_path: Path | None = None) -> dict[str, Any]:
    root = (root or Path.cwd()).resolve()
    status_paths = _git_status_paths(root)
    added_lines = _iter_added_lines(root)
    owner_boundary = _find_owner_boundary_manifest(root)
    owner_scope = _owner_scope_result(root, status_paths, next_action_path)
    failures: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    generated_paths = [
        path for path in status_paths
        if path.startswith(GENERATED_KNOWLEDGE_PREFIX) or path.endswith("knowledge_digest.md")
    ]
    for path in generated_paths:
        failures.append({"check": "generated_knowledge_modified", "path": path})

    if owner_scope["status"] == "fail":
        failures.append(
            {
                "check": "owner_scope_violation",
                "next_action": owner_scope.get("path", ""),
                "repair_unit_id": owner_scope.get("repair_unit_id", ""),
                "allowed_owner_modules": owner_scope.get("allowed_owner_modules", []),
                "paths": owner_scope.get("violations", []),
            }
        )

    giant_touched = sorted(path for path in status_paths if path in GIANT_OWNER_FILES)
    if giant_touched and owner_boundary.get("status") != "present":
        failures.append(
            {
                "check": "giant_owner_without_owner_boundary",
                "paths": giant_touched,
            }
        )
    elif giant_touched:
        warnings.append(
            {
                "check": "giant_owner_bridge_requires_review",
                "paths": giant_touched,
                "owner_boundary_manifest": owner_boundary.get("path", ""),
            }
        )

    route_hint_lines: list[dict[str, Any]] = []
    branch_count = 0
    public_symbols: list[dict[str, Any]] = []
    production_added_loc = 0
    for path, line in added_lines:
        normalized = path.replace("\\", "/")
        if normalized.startswith("tests/") or normalized.startswith("docs/"):
            continue
        if normalized.startswith(("src/", "web/", "tools/")) and line.strip() != "+":
            production_added_loc += 1
        if RESCUE_FUNCTION_PATTERN.search(line):
            failures.append({"check": "new_apply_rescue_function", "path": normalized, "line": line[1:].strip()})
        if EXCEPT_PASS_PATTERN.search(line):
            failures.append({"check": "silent_exception_pass", "path": normalized, "line": line[1:].strip()})
        if SSL_DISABLE_PATTERN.search(line):
            failures.append({"check": "ssl_verification_disabled", "path": normalized, "line": line[1:].strip()})
        if any(pattern.search(line) for pattern in SECRET_PATTERNS):
            failures.append({"check": "secret_like_assignment", "path": normalized, "line": line[1:].strip()})
        if ROUTE_HINT_PATTERN.search(line):
            route_hint_lines.append({"path": normalized, "line": line[1:].strip()})
        if BRANCH_PATTERN.search(line):
            branch_count += 1
        symbol_match = PUBLIC_SYMBOL_PATTERN.search(line)
        if symbol_match:
            public_symbols.append({"path": normalized, "symbol": symbol_match.group(2)})

    if route_hint_lines:
        warnings.append({"check": "new_route_hint_or_keyword_terms", "matches": route_hint_lines[:20]})

    complexity_delta = {
        "production_added_loc": production_added_loc,
        "new_branch_count": branch_count,
        "new_public_symbols": public_symbols[:50],
        "new_public_symbol_count": len(public_symbols),
    }
    status = "fail" if failures else "pass"
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "policy_check_status": status,
        "failures": failures,
        "warnings": warnings,
        "owner_boundary_manifest": owner_boundary,
        "owner_scope": owner_scope,
        "complexity_delta": complexity_delta,
        "changed_path_count": len(status_paths),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="V36 deterministic Step 4 policy check")
    parser.add_argument("--out", type=Path, default=Path("reports/attribution/policy_check.json"))
    parser.add_argument("--next-action", type=Path, default=None)
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()
    root = Path.cwd()
    result = run_policy_check(root, next_action_path=args.next_action)
    out_path = root / args.out if not args.out.is_absolute() else args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json_only:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["policy_check_status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
