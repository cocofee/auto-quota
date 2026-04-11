from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WIKI_ROOT = PROJECT_ROOT / "knowledge_wiki"

PAGE_DIRS = {
    "sources",
    "rules",
    "cases",
    "methods",
    "concepts",
    "entities",
    "reviews",
    "daily",
    "inbox",
}
EXPECTED_TYPES_BY_DIR = {
    "sources": {"source", "index"},
    "rules": {"rule", "index"},
    "cases": {"case", "index"},
    "methods": {"method", "index"},
    "concepts": {"concept", "index"},
    "entities": {"entity", "index"},
    "reviews": {"review", "index"},
    "daily": {"daily_summary", "index"},
    "inbox": {"inbox", "index"},
}
REQUIRED_FRONTMATTER_KEYS = {
    "title",
    "type",
    "status",
    "province",
    "specialty",
    "source_refs",
    "source_kind",
    "created_at",
    "updated_at",
    "confidence",
    "owner",
    "tags",
    "related",
}
ALLOWED_STATUSES = {
    "draft",
    "reviewed",
    "promoted",
    "active",
    "archived",
    "deprecated",
}
MANIFEST_FILES = {".generated_manifest.json", ".generated_sources_manifest.json"}
FRONTMATTER_DELIMITER = "---"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _decode_scalar(value: str) -> Any:
    raw = value.strip()
    if raw == "":
        return ""
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return json.loads(raw)
        except Exception:
            return raw.strip('"')
    if raw in {"[]", "[ ]"}:
        return []
    if raw.isdigit():
        return int(raw)
    return raw


def parse_frontmatter(markdown_text: str) -> tuple[dict[str, Any], str]:
    text = markdown_text.lstrip("\ufeff")
    if not text.startswith(f"{FRONTMATTER_DELIMITER}\n"):
        return {}, markdown_text

    end_marker = f"\n{FRONTMATTER_DELIMITER}\n"
    end_index = text.find(end_marker, len(FRONTMATTER_DELIMITER) + 1)
    if end_index < 0:
        return {}, markdown_text

    raw_meta = text[len(FRONTMATTER_DELIMITER) + 1:end_index]
    body = text[end_index + len(end_marker):]
    metadata: dict[str, Any] = {}
    current_list_key: str | None = None
    current_list: list[Any] | None = None

    for line in raw_meta.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_list_key and current_list is not None:
            current_list.append(_decode_scalar(line[4:]))
            continue
        if ":" not in line:
            current_list_key = None
            current_list = None
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value == "":
            current_list_key = key
            current_list = []
            metadata[key] = current_list
            continue
        metadata[key] = _decode_scalar(value)
        current_list_key = None
        current_list = None

    return metadata, body


def _issue(severity: str, code: str, path: str, message: str) -> dict[str, str]:
    return {
        "severity": severity,
        "code": code,
        "path": path,
        "message": message,
    }


def _all_markdown_files(wiki_root: Path) -> list[Path]:
    return sorted(path for path in wiki_root.rglob("*.md") if path.is_file())


def _iter_related_values(meta: dict[str, Any], body: str) -> list[str]:
    related: list[str] = []
    raw_related = meta.get("related")
    if isinstance(raw_related, list):
        related.extend(_safe_text(item) for item in raw_related if _safe_text(item))
    elif _safe_text(raw_related):
        related.append(_safe_text(raw_related))

    for match in re.finditer(r"\[\[([^\]]+)\]\]", body):
        text = _safe_text(match.group(1))
        if text:
            related.append(text)

    seen: set[str] = set()
    deduped: list[str] = []
    for item in related:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _path_exists_from_related(wiki_root: Path, value: str, stem_map: dict[str, list[str]]) -> bool:
    text = _safe_text(value)
    if not text:
        return True
    if text.endswith(".md") or "/" in text or "\\" in text:
        normalized = text.replace("\\", "/")
        candidate = (wiki_root / normalized).resolve()
        try:
            candidate.relative_to(wiki_root.resolve())
        except Exception:
            return False
        return candidate.exists()
    if "#" in text:
        text = text.split("#", 1)[0].strip()
    return text in stem_map


def _check_source_ref_exists(source_ref: str, project_root: Path) -> bool | None:
    text = _safe_text(source_ref)
    if not text:
        return False
    if text.startswith("source_pack:"):
        source_id = text.split(":", 1)[1].strip()
        if not source_id:
            return False
        if source_id == "index":
            return None
        return (project_root / "data" / "source_packs" / "packs" / f"{source_id}.json").exists()
    if text.startswith(("staging:", "task:", "result:", "openclaw:", "experience_db:", "rule_knowledge:", "method_cards:")):
        return None
    if re.match(r"^[A-Za-z]:\\", text):
        return Path(text).exists()
    if text.startswith(("/", "\\")):
        return Path(text).exists()
    return None


def _lint_one_markdown(path: Path, wiki_root: Path, project_root: Path, stem_map: dict[str, list[str]]) -> list[dict[str, str]]:
    rel = str(path.relative_to(wiki_root)).replace("\\", "/")
    issues: list[dict[str, str]] = []
    raw_text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(raw_text)

    if not meta:
        return [_issue("error", "missing_frontmatter", rel, "missing YAML frontmatter")]

    missing_keys = sorted(REQUIRED_FRONTMATTER_KEYS - set(meta.keys()))
    for key in missing_keys:
        issues.append(_issue("error", "missing_field", rel, f"missing frontmatter field: {key}"))

    parent = path.parent.name
    if parent in PAGE_DIRS:
        expected_types = EXPECTED_TYPES_BY_DIR.get(parent, set())
        page_type = _safe_text(meta.get("type"))
        if expected_types and page_type and page_type not in expected_types:
            issues.append(_issue("error", "type_mismatch", rel, f"type '{page_type}' is not valid for directory '{parent}'"))

    status = _safe_text(meta.get("status")).lower()
    if status and status not in ALLOWED_STATUSES:
        issues.append(_issue("warning", "unknown_status", rel, f"unknown status '{status}'"))

    confidence = meta.get("confidence")
    if confidence not in (None, ""):
        try:
            numeric_confidence = int(confidence)
            if numeric_confidence < 0 or numeric_confidence > 100:
                issues.append(_issue("error", "invalid_confidence", rel, "confidence must be between 0 and 100"))
        except Exception:
            issues.append(_issue("error", "invalid_confidence", rel, "confidence must be an integer"))

    source_refs = meta.get("source_refs")
    if not isinstance(source_refs, list):
        issues.append(_issue("error", "invalid_source_refs", rel, "source_refs must be a list"))
    else:
        if parent in PAGE_DIRS and parent not in {"daily", "inbox"} and path.name != "index.md" and not source_refs:
            issues.append(_issue("warning", "empty_source_refs", rel, "source_refs is empty"))
        seen_refs: set[str] = set()
        for item in source_refs:
            ref = _safe_text(item)
            if not ref:
                issues.append(_issue("warning", "blank_source_ref", rel, "source_refs contains blank value"))
                continue
            if ref in seen_refs:
                issues.append(_issue("warning", "duplicate_source_ref", rel, f"duplicate source_ref '{ref}'"))
                continue
            seen_refs.add(ref)
            exists = _check_source_ref_exists(ref, project_root)
            if exists is False:
                issues.append(_issue("warning", "missing_source_ref_target", rel, f"source_ref target not found: {ref}"))

    tags = meta.get("tags")
    if tags is not None and not isinstance(tags, list):
        issues.append(_issue("error", "invalid_tags", rel, "tags must be a list"))

    for related_item in _iter_related_values(meta, body):
        if not _path_exists_from_related(wiki_root, related_item, stem_map):
            issues.append(_issue("warning", "missing_related_target", rel, f"related target not found: {related_item}"))

    if parent in PAGE_DIRS and not _safe_text(meta.get("title")):
        issues.append(_issue("error", "empty_title", rel, "title is empty"))

    if not body.strip():
        issues.append(_issue("warning", "empty_body", rel, "page body is empty"))

    return issues


def _lint_manifest(manifest_path: Path, wiki_root: Path) -> list[dict[str, str]]:
    rel = str(manifest_path.relative_to(wiki_root)).replace("\\", "/")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [_issue("error", "invalid_manifest", rel, f"failed to parse manifest: {exc}")]

    issues: list[dict[str, str]] = []
    files = payload.get("files")
    if not isinstance(files, list):
        return [_issue("error", "invalid_manifest", rel, "manifest.files must be a list")]

    for item in files:
        if not isinstance(item, dict):
            continue
        relative_path = _safe_text(item.get("relative_path"))
        if not relative_path:
            issues.append(_issue("warning", "manifest_missing_path", rel, "manifest item missing relative_path"))
            continue
        if not (wiki_root / relative_path).exists():
            issues.append(_issue("warning", "manifest_missing_file", rel, f"manifest references missing file: {relative_path}"))
    return issues


def lint_wiki(*, wiki_root: Path = DEFAULT_WIKI_ROOT, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    wiki_root = wiki_root.resolve()
    project_root = project_root.resolve()
    if not wiki_root.exists():
        raise FileNotFoundError(f"wiki root not found: {wiki_root}")

    files = _all_markdown_files(wiki_root)
    stem_map: dict[str, list[str]] = {}
    for path in files:
        stem_map.setdefault(path.stem, []).append(str(path.relative_to(wiki_root)).replace("\\", "/"))

    issues: list[dict[str, str]] = []
    for file_path in files:
        issues.extend(_lint_one_markdown(file_path, wiki_root, project_root, stem_map))

    for manifest_name in MANIFEST_FILES:
        manifest_path = wiki_root / manifest_name
        if manifest_path.exists():
            issues.extend(_lint_manifest(manifest_path, wiki_root))

    return {
        "wiki_root": str(wiki_root),
        "project_root": str(project_root),
        "page_count": len(files),
        "issue_count": len(issues),
        "error_count": sum(1 for item in issues if item["severity"] == "error"),
        "warning_count": sum(1 for item in issues if item["severity"] == "warning"),
        "issues": issues,
    }


def _print_text_report(report: dict[str, Any]) -> None:
    print(f"Wiki root: {report['wiki_root']}")
    print(f"Pages: {report['page_count']}")
    print(f"Errors: {report['error_count']}")
    print(f"Warnings: {report['warning_count']}")
    if not report["issues"]:
        print("No issues found.")
        return
    print("")
    for item in report["issues"]:
        print(f"[{item['severity']}] {item['code']} {item['path']}: {item['message']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Lint knowledge_wiki pages and generated manifests.")
    parser.add_argument("--wiki-root", default=str(DEFAULT_WIKI_ROOT))
    parser.add_argument("--project-root", default=str(PROJECT_ROOT))
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--report", default="", help="optional path to save the JSON report")
    parser.add_argument("--fail-on-warn", action="store_true")
    args = parser.parse_args()

    report = lint_wiki(wiki_root=Path(args.wiki_root), project_root=Path(args.project_root))
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text_report(report)

    if report["error_count"] > 0:
        return 1
    if args.fail_on_warn and report["warning_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

