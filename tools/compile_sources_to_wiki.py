from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.source_pack import SOURCE_PACKS_DIR, load_source_pack, normalize_source_pack_metadata, read_text_with_fallbacks, safe_text, slugify  # noqa: E402


OUTPUT_DIR = PROJECT_ROOT / "knowledge_wiki"
MANIFEST_NAME = ".generated_sources_manifest.json"


def dump_frontmatter(payload: dict[str, Any]) -> str:
    ordered_keys = [
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
    ]
    lines = ["---"]
    for key in ordered_keys:
        value = payload.get(key)
        if key in {"source_refs", "tags", "related"}:
            items = [safe_text(item) for item in (value or []) if safe_text(item)]
            if not items:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                for item in items:
                    escaped = item.replace("\\", "\\\\").replace('"', '\\"')
                    lines.append(f'  - "{escaped}"')
            continue
        if isinstance(value, (int, float)) and key == "confidence":
            lines.append(f"{key}: {int(value)}")
            continue
        escaped = safe_text(value).replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}: "{escaped}"')
    lines.append("---")
    return "\n".join(lines)


def markdown_list(items: list[str]) -> list[str]:
    values = [safe_text(item) for item in items if safe_text(item)]
    if not values:
        return ["- 无"]
    return [f"- {item}" for item in values]


def preview_text(path: str | Path, *, max_lines: int = 20) -> str:
    lines = [line.rstrip() for line in read_text_with_fallbacks(path).splitlines()]
    preview = "\n".join(lines[:max_lines]).strip()
    return preview or "无"


def build_source_page(pack: dict[str, Any]) -> tuple[str, str]:
    pack = normalize_source_pack_metadata(pack)
    source_id = safe_text(pack.get("source_id"))
    title = safe_text(pack.get("title")) or source_id
    relative_path = f"sources/source-{slugify(source_id)}.md"
    source_refs = [f"source_pack:{source_id}"] + [safe_text(item) for item in pack.get("evidence_refs") or []]
    frontmatter = dump_frontmatter(
        {
            "title": title,
            "type": "source",
            "status": "draft",
            "province": safe_text(pack.get("province")),
            "specialty": safe_text(pack.get("specialty")),
            "source_refs": source_refs,
            "source_kind": safe_text(pack.get("source_kind")),
            "created_at": safe_text(pack.get("created_at")) or datetime.now().strftime("%Y-%m-%d"),
            "updated_at": datetime.now().strftime("%Y-%m-%d"),
            "confidence": int(pack.get("confidence", 0) or 0),
            "owner": "codex",
            "tags": list(pack.get("tags") or []),
            "related": [],
        }
    )
    lines = [
        frontmatter,
        "",
        f"# {title}",
        "",
        "## Source Pack",
        f"- source_id: `{source_id}`",
        f"- source_kind: `{safe_text(pack.get('source_kind')) or 'unknown'}`",
        f"- full_text_path: `{safe_text(pack.get('full_text_path'))}`",
        "",
        "## Summary",
        safe_text(pack.get("summary")) or "无",
        "",
        "## Evidence Refs",
        *markdown_list(list(pack.get("evidence_refs") or [])),
        "",
        "## Text Preview",
        "```text",
        preview_text(pack.get("full_text_path")),
        "```",
    ]
    metadata = pack.get("metadata") or {}
    if metadata:
        lines.extend(
            [
                "",
                "## Metadata",
                "```json",
                json.dumps(metadata, ensure_ascii=False, indent=2),
                "```",
            ]
        )
    return relative_path, "\n".join(lines).strip() + "\n"


def build_sources_index(source_pages: list[dict[str, str]]) -> tuple[str, str]:
    relative_path = "sources/index.md"
    frontmatter = dump_frontmatter(
        {
            "title": "Sources Index",
            "type": "index",
            "status": "reviewed",
            "province": "",
            "specialty": "",
            "source_refs": ["source_pack:index"],
            "source_kind": "system",
            "created_at": datetime.now().strftime("%Y-%m-%d"),
            "updated_at": datetime.now().strftime("%Y-%m-%d"),
            "confidence": 100,
            "owner": "codex",
            "tags": ["sources", "index"],
            "related": [item["relative_path"] for item in source_pages],
        }
    )
    lines = [frontmatter, "", "# Sources Index", "", "## Generated Source Pages"]
    for item in source_pages:
        lines.append(f"- [[{Path(item['relative_path']).stem}]]")
    return relative_path, "\n".join(lines).strip() + "\n"


def compile_sources_to_wiki(*, packs_dir: Path = SOURCE_PACKS_DIR, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    sources_dir = output_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = output_dir / MANIFEST_NAME
    if manifest_path.exists():
        old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in old_manifest.get("files", []):
            rel = safe_text(item.get("relative_path"))
            if not rel:
                continue
            target = output_dir / rel
            if target.exists():
                target.unlink()

    source_pages: list[dict[str, str]] = []
    for pack_path in sorted(packs_dir.glob("*.json")):
        pack = normalize_source_pack_metadata(load_source_pack(pack_path))
        relative_path, content = build_source_page(pack)
        target = output_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        source_pages.append(
            {
                "relative_path": relative_path,
                "category": "sources",
                "obsidian_dir": "60-资料来源",
                "title": safe_text(pack.get("title")) or safe_text(pack.get("source_id")),
                "source_ref": f"source_pack:{safe_text(pack.get('source_id'))}",
            }
        )

    index_relative_path, index_content = build_sources_index(source_pages)
    (output_dir / index_relative_path).write_text(index_content, encoding="utf-8")
    source_pages.insert(
        0,
        {
            "relative_path": index_relative_path,
            "category": "sources",
            "obsidian_dir": "60-资料来源",
            "title": "Sources Index",
            "source_ref": "source_pack:index",
        },
    )

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "output_dir": str(output_dir),
        "packs_dir": str(packs_dir),
        "counts": {"sources": len(source_pages)},
        "files": source_pages,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile source packs into wiki source pages.")
    parser.add_argument("--packs-dir", default=str(SOURCE_PACKS_DIR))
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    manifest = compile_sources_to_wiki(packs_dir=Path(args.packs_dir), output_dir=Path(args.output_dir))
    print(json.dumps(manifest["counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())