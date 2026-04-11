from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.qmd_index import QMDIndex  # noqa: E402
from src.source_pack import safe_text  # noqa: E402
from tools.compile_sources_to_wiki import compile_sources_to_wiki  # noqa: E402
from tools.ingest_chat import ingest_chat, parse_chat_messages  # noqa: E402
from tools.ingest_document import ingest_document  # noqa: E402
from tools.ingest_image import ingest_image  # noqa: E402
from tools.ingest_video import ingest_video  # noqa: E402


DEFAULT_RAW_ROOT = Path(r"E:\Jarvis-Raw")
DEFAULT_DIRS = {
    "inbox": "00_inbox",
    "doc": "10_docs",
    "image": "20_images",
    "video": "30_videos",
    "chat": "40_chats",
    "done": "90_done",
}
DOC_EXTS = {".pdf", ".docx", ".xlsx", ".xlsm", ".xltx", ".xltm", ".csv"}
TEXT_DOC_EXTS = {".md", ".txt", ".text"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v", ".ts"}
CHAT_HINTS = ("chat", "dialog", "conversation", "wechat", "weixin", "qq", "閽夐拤", "椋炰功", "鑱婂ぉ", "瀵硅瘽")
VIDEO_TRANSCRIPT_SUFFIXES = (".srt", ".vtt", ".transcript.txt", ".transcript.md", ".txt", ".md")
IMAGE_OCR_SUFFIXES = (".ocr.txt", ".ocr.md", ".txt", ".md")
DEFAULT_CONFIDENCE = {
    "doc": 85,
    "image": 70,
    "video": 78,
    "chat": 80,
}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _ensure_inside_root(path: Path, root: Path) -> None:
    if not _is_relative_to(path, root):
        raise ValueError(f"path {path} is outside raw root {root}")


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _looks_like_chat_json(path: Path) -> bool:
    try:
        raw = _load_json(path)
    except Exception:
        return False
    if isinstance(raw, dict) and isinstance(raw.get("messages"), list):
        raw = raw["messages"]
    if not isinstance(raw, list) or not raw:
        return False
    first = raw[0]
    return isinstance(first, dict) and any(key in first for key in ("role", "speaker", "content", "text", "message"))


def _looks_like_chat_text(path: Path) -> bool:
    lowered_name = path.name.lower()
    if any(hint in lowered_name for hint in CHAT_HINTS):
        return True
    try:
        messages = parse_chat_messages(path)
    except Exception:
        return False
    if len(messages) < 2:
        return False
    roles = {safe_text(item.get("role")).lower() for item in messages if safe_text(item.get("role"))}
    return len(roles) >= 2


def _is_image_sidecar(path: Path) -> bool:
    lowered = path.name.lower()
    if any(lowered.endswith(suffix) for suffix in (".ocr.txt", ".ocr.md")):
        return True
    if path.suffix.lower() not in {".txt", ".md"}:
        return False
    for ext in IMAGE_EXTS:
        if path.with_suffix("").suffix.lower() == ext:
            return True
    return False


def _classify_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTS:
        return "image"
    if suffix in VIDEO_EXTS:
        return "video"
    if suffix in {".srt", ".vtt"}:
        return "sidecar"
    if _is_image_sidecar(path):
        return "sidecar"
    if suffix == ".json":
        return "chat" if _looks_like_chat_json(path) else "doc"
    if suffix in TEXT_DOC_EXTS:
        return "chat" if _looks_like_chat_text(path) else "doc"
    if suffix in DOC_EXTS:
        return "doc"
    return "unsupported"


def _find_first_existing(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _find_video_transcript(video_path: Path) -> Path | None:
    candidates = [video_path.with_suffix(suffix) for suffix in VIDEO_TRANSCRIPT_SUFFIXES]
    return _find_first_existing(candidates)


def _find_image_ocr_sidecar(image_path: Path) -> Path | None:
    candidates = [image_path.with_suffix(image_path.suffix + suffix) for suffix in (".txt", ".md")]
    candidates.extend(image_path.with_suffix(suffix) for suffix in IMAGE_OCR_SUFFIXES)
    return _find_first_existing(candidates)


def _unique_target_path(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = target.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _move_file(source: Path, destination_root: Path, inbox_root: Path) -> str:
    _ensure_inside_root(source, inbox_root)
    relative = source.resolve().relative_to(inbox_root.resolve())
    target = destination_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    final_target = _unique_target_path(target)
    shutil.move(str(source), str(final_target))
    return str(final_target)


def _write_report(done_dir: Path, payload: dict[str, Any]) -> Path:
    done_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = done_dir / f"ingest-report-{timestamp}.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report_path


def _rewrite_pack_evidence_refs(pack_path: str | Path, archived_paths: list[str]) -> None:
    if not archived_paths:
        return
    target = Path(pack_path)
    if not target.exists():
        return
    payload = _load_json(target)
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        return

    archived_map = {str(Path(item).name): item for item in archived_paths if safe_text(item)}
    rewritten: list[str] = []
    changed = False
    for ref in evidence_refs:
        text = safe_text(ref)
        replacement = archived_map.get(Path(text).name)
        if replacement:
            rewritten.append(replacement)
            changed = True
        else:
            rewritten.append(text)

    if not changed:
        return
    payload["evidence_refs"] = rewritten
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _iter_inbox_files(inbox_root: Path) -> list[Path]:
    if not inbox_root.exists():
        return []
    return sorted(path for path in inbox_root.rglob("*") if path.is_file())


def _tags_with_kind(tags: list[str], kind: str) -> list[str]:
    base = [safe_text(item) for item in tags if safe_text(item)]
    kind_tag = {
        "doc": "document",
        "image": "image",
        "video": "video",
        "chat": "chat",
    }[kind]
    if kind_tag not in base:
        base.append(kind_tag)
    return base


def run_batch_ingest(
    *,
    raw_root: Path = DEFAULT_RAW_ROOT,
    province: str = "",
    specialty: str = "",
    tags: list[str] | None = None,
    limit: int = 0,
    dry_run: bool = False,
    move_success: bool = True,
    compile_wiki: bool = False,
    build_qmd: bool = False,
) -> dict[str, Any]:
    raw_root = raw_root.resolve()
    dirs = {key: raw_root / name for key, name in DEFAULT_DIRS.items()}
    inbox_root = dirs["inbox"]
    files = _iter_inbox_files(inbox_root)
    if limit > 0:
        files = files[:limit]

    tag_list = [safe_text(item) for item in (tags or []) if safe_text(item)]
    summary = {
        "raw_root": str(raw_root),
        "inbox_root": str(inbox_root),
        "processed": 0,
        "imported": 0,
        "skipped": 0,
        "errors": 0,
        "by_kind": {"doc": 0, "image": 0, "video": 0, "chat": 0},
        "items": [],
    }

    handled_sidecars: set[Path] = set()

    for file_path in files:
        file_path = file_path.resolve()
        if file_path in handled_sidecars:
            continue

        summary["processed"] += 1
        kind = _classify_path(file_path)
        record: dict[str, Any] = {
            "path": str(file_path),
            "kind": kind,
            "status": "pending",
        }

        try:
            if kind == "sidecar":
                record["status"] = "skipped"
                record["reason"] = "sidecar_file"
                summary["skipped"] += 1
                summary["items"].append(record)
                continue
            if kind == "unsupported":
                record["status"] = "skipped"
                record["reason"] = "unsupported_extension"
                summary["skipped"] += 1
                summary["items"].append(record)
                continue

            ingest_kwargs = {
                "title": file_path.stem,
                "province": province,
                "specialty": specialty,
                "tags": _tags_with_kind(tag_list, kind),
                "confidence": DEFAULT_CONFIDENCE[kind],
            }
            related_files = [file_path]

            if kind == "doc":
                result = ingest_document(input_path=file_path, **ingest_kwargs) if not dry_run else {}
            elif kind == "chat":
                result = ingest_chat(input_path=file_path, **ingest_kwargs) if not dry_run else {}
            elif kind == "image":
                ocr_file = _find_image_ocr_sidecar(file_path)
                if ocr_file:
                    related_files.append(ocr_file.resolve())
                    handled_sidecars.add(ocr_file.resolve())
                result = ingest_image(input_path=file_path, ocr_file=ocr_file, **ingest_kwargs) if not dry_run else {}
            elif kind == "video":
                transcript = _find_video_transcript(file_path)
                if not transcript:
                    record["status"] = "skipped"
                    record["reason"] = "missing_transcript"
                    summary["skipped"] += 1
                    summary["items"].append(record)
                    continue
                related_files.append(transcript.resolve())
                handled_sidecars.add(transcript.resolve())
                result = ingest_video(
                    transcript_file=transcript,
                    video_file=file_path,
                    **ingest_kwargs,
                ) if not dry_run else {}
            else:
                record["status"] = "skipped"
                record["reason"] = "unknown_kind"
                summary["skipped"] += 1
                summary["items"].append(record)
                continue

            archived_paths: list[str] = []
            if move_success and not dry_run:
                for related_file in related_files:
                    archived_paths.append(_move_file(related_file, dirs[kind], inbox_root))
                _rewrite_pack_evidence_refs(result.get("pack_path", ""), archived_paths)

            record["status"] = "imported"
            record["archived_paths"] = archived_paths
            record["source_pack"] = result
            summary["imported"] += 1
            summary["by_kind"][kind] += 1
            summary["items"].append(record)
        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            summary["errors"] += 1
            summary["items"].append(record)

    if compile_wiki and not dry_run:
        summary["wiki_manifest"] = compile_sources_to_wiki()
    if build_qmd and not dry_run:
        summary["qmd_manifest"] = QMDIndex().rebuild_index()

    report_path = _write_report(dirs["done"], summary)
    summary["report_path"] = str(report_path)
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch ingest files from an external RAW inbox.")
    parser.add_argument("--raw-root", default=str(DEFAULT_RAW_ROOT))
    parser.add_argument("--province", default="")
    parser.add_argument("--specialty", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-move", action="store_true", help="Keep original files in 00_inbox after import")
    parser.add_argument("--compile-wiki", action="store_true")
    parser.add_argument("--build-qmd", action="store_true")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    summary = run_batch_ingest(
        raw_root=Path(args.raw_root),
        province=args.province,
        specialty=args.specialty,
        tags=[item.strip() for item in args.tags.split(",") if item.strip()],
        limit=args.limit,
        dry_run=args.dry_run,
        move_success=not args.no_move,
        compile_wiki=args.compile_wiki,
        build_qmd=args.build_qmd,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

