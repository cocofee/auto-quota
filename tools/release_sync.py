from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
MANIFEST_FILE = ROOT / "lzc-manifest.yml"
CHANGELOG_FILE = ROOT / "web" / "frontend" / "src" / "constants" / "changelog.ts"


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")


def get_manifest_version() -> str:
    content = read_text(MANIFEST_FILE)
    match = re.search(r"^version:\s*(.+)$", content, re.MULTILINE)
    if not match:
        raise RuntimeError("Could not read version from lzc-manifest.yml")
    return match.group(1).strip().strip('"')


def get_manifest_image_versions() -> dict[str, str]:
    content = read_text(MANIFEST_FILE)
    frontend_match = re.search(r"auto-quota-frontend:([^\s\"\r\n]+)", content)
    backend_match = re.search(r"auto-quota-app:([^\s\"\r\n]+)", content)
    if not frontend_match or not backend_match:
        raise RuntimeError("Could not read frontend/backend image tags from lzc-manifest.yml")
    return {
        "frontend": frontend_match.group(1).strip(),
        "backend": backend_match.group(1).strip(),
    }


def get_frontend_versions() -> dict[str, str]:
    content = read_text(CHANGELOG_FILE)
    app_match = re.search(r"export const APP_VERSION = '([^']+)';", content)
    head_match = re.search(
        r"export const CHANGELOG: ChangelogEntry\[\] = \[\n\s*{\n\s*version: '([^']+)'",
        content,
    )
    if not app_match or not head_match:
        raise RuntimeError("Could not read APP_VERSION or changelog head version")
    return {
        "app_version": app_match.group(1).strip(),
        "head_version": head_match.group(1).strip(),
    }


def bump_patch(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise RuntimeError(f"Unsupported semver version: {version}")
    major, minor, patch = map(int, parts)
    return f"{major}.{minor}.{patch + 1}"


def get_recent_commits() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "log", "--oneline", "-20"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except Exception:
        return []

    changes: list[str] = []
    for line in result.stdout.strip().splitlines():
        msg = line.split(" ", 1)[1] if " " in line else line
        if msg.startswith("deploy:"):
            break
        clean = re.sub(r"^(feat|fix|refactor|chore|docs|test|style|perf):\s*", "", msg)
        if clean and len(clean) > 3:
            changes.append(clean)
    return changes


def quote_ts_string(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def sync_manifest(content: str, version: str) -> str:
    content = re.sub(r"^version:\s*.+$", f"version: {version}", content, flags=re.MULTILINE)
    content = re.sub(r"(auto-quota-frontend:)[^\"\r\n]+", rf"\g<1>{version}", content)
    content = re.sub(r"(auto-quota-app:)[^\"\r\n]+", rf"\g<1>{version}", content)
    return content


def build_changelog_entry(version: str) -> str:
    changes = get_recent_commits()
    if not changes:
        changes = [f"v{version} 更新"]

    today = date.today().isoformat()
    changes_block = ",\n".join(
        f"      {{ type: 'admin', text: {quote_ts_string(change)} }}" for change in changes
    )
    return (
        "  {\n"
        f"    version: '{version}',\n"
        f"    date: '{today}',\n"
        "    changes: [\n"
        f"{changes_block},\n"
        "    ],\n"
        "  },\n"
    )


def sync_changelog(content: str, version: str) -> str:
    content = re.sub(
        r"export const APP_VERSION = '[^']+';",
        f"export const APP_VERSION = '{version}';",
        content,
        count=1,
    )

    head_match = re.search(
        r"export const CHANGELOG: ChangelogEntry\[\] = \[\n\s*{\n\s*version: '([^']+)'",
        content,
    )
    head_version = head_match.group(1) if head_match else None
    if head_version == version:
        return content

    entry = build_changelog_entry(version)
    return re.sub(
        r"(export const CHANGELOG: ChangelogEntry\[\] = \[\n)",
        r"\1" + entry,
        content,
        count=1,
    )


def apply_version(version: str) -> None:
    write_text(MANIFEST_FILE, sync_manifest(read_text(MANIFEST_FILE), version))
    write_text(CHANGELOG_FILE, sync_changelog(read_text(CHANGELOG_FILE), version))


def validate_version(version: str) -> None:
    manifest_version = get_manifest_version()
    image_versions = get_manifest_image_versions()
    frontend_versions = get_frontend_versions()

    mismatches: list[str] = []
    if manifest_version != version:
        mismatches.append(f"manifest version={manifest_version}, expected={version}")
    if image_versions["frontend"] != version:
        mismatches.append(f"frontend image tag={image_versions['frontend']}, expected={version}")
    if image_versions["backend"] != version:
        mismatches.append(f"backend image tag={image_versions['backend']}, expected={version}")
    if frontend_versions["app_version"] != version:
        mismatches.append(f"APP_VERSION={frontend_versions['app_version']}, expected={version}")
    if frontend_versions["head_version"] != version:
        mismatches.append(f"CHANGELOG head={frontend_versions['head_version']}, expected={version}")

    if mismatches:
        raise RuntimeError("Release files out of sync: " + "; ".join(mismatches))


def main() -> int:
    parser = argparse.ArgumentParser(description="Release version sync helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("current", help="Print current manifest version")
    subparsers.add_parser("next", help="Print next patch version from manifest")
    validate_parser = subparsers.add_parser("validate", help="Validate release files against a version")
    validate_parser.add_argument("version")

    apply_parser = subparsers.add_parser("apply", help="Sync manifest and changelog to a version")
    apply_parser.add_argument("version")

    args = parser.parse_args()

    try:
        if args.command == "current":
            print(get_manifest_version())
            return 0
        if args.command == "next":
            print(bump_patch(get_manifest_version()))
            return 0
        if args.command == "validate":
            validate_version(args.version)
            print(args.version)
            return 0
        if args.command == "apply":
            apply_version(args.version)
            print(args.version)
            return 0
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
