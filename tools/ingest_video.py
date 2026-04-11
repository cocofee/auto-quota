from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.source_pack import build_source_pack, make_source_id, read_text_with_fallbacks, safe_text, summarize_text, write_source_pack  # noqa: E402


_SRT_BLOCK_RE = re.compile(
    r"(?ms)^\s*(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2}[,.:]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.:]\d{3})\s*\n"
    r"(.*?)(?=\n\s*\n|\Z)"
)

_VTT_BLOCK_RE = re.compile(
    r"(?ms)^\s*(\d{2}:\d{2}:\d{2}[.]\d{3}|\d{2}:\d{2}[.]\d{3})\s*-->\s*"
    r"(\d{2}:\d{2}:\d{2}[.]\d{3}|\d{2}:\d{2}[.]\d{3}).*?\n"
    r"(.*?)(?=\n\s*\n|\Z)"
)


def _normalize_ts(value: str) -> str:
    text = safe_text(value).replace(",", ".")
    if re.match(r"^\d{2}:\d{2}\.\d{3}$", text):
        return f"00:{text}"
    return text


def _parse_srt(text: str) -> list[dict[str, str]]:
    cues: list[dict[str, str]] = []
    for match in _SRT_BLOCK_RE.finditer(text):
        start = _normalize_ts(match.group(2))
        end = _normalize_ts(match.group(3))
        content = "\n".join(line.strip() for line in match.group(4).splitlines() if line.strip())
        if content:
            cues.append({"start": start, "end": end, "text": content})
    return cues


def _parse_vtt(text: str) -> list[dict[str, str]]:
    cues: list[dict[str, str]] = []
    for match in _VTT_BLOCK_RE.finditer(text):
        start = _normalize_ts(match.group(1))
        end = _normalize_ts(match.group(2))
        content = "\n".join(line.strip() for line in match.group(3).splitlines() if line.strip())
        if content:
            cues.append({"start": start, "end": end, "text": content})
    return cues


def _plain_to_cues(text: str) -> list[dict[str, str]]:
    cues: list[dict[str, str]] = []
    pattern = re.compile(r"^\s*\[?(\d{2}:\d{2}:\d{2})\]?\s*(.+)$")
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        cues.append({"start": match.group(1), "end": "", "text": match.group(2).strip()})
    return cues


def parse_transcript(path: str | Path) -> tuple[str, list[dict[str, str]], str]:
    transcript_path = Path(path)
    raw = read_text_with_fallbacks(transcript_path)
    suffix = transcript_path.suffix.lower()
    if suffix == ".srt":
        cues = _parse_srt(raw)
        transcript_format = "srt"
    elif suffix == ".vtt":
        cues = _parse_vtt(raw)
        transcript_format = "vtt"
    else:
        cues = _plain_to_cues(raw)
        transcript_format = suffix.lstrip(".") or "text"
    return raw, cues, transcript_format


def build_transcript_doc(*, title: str, video_ref: str, raw_text: str, cues: list[dict[str, str]]) -> str:
    lines = [
        f"Video Title: {title}",
        f"Video Ref: {video_ref or 'unknown'}",
        "",
    ]
    if cues:
        lines.append("## Timeline")
        for cue in cues:
            end_part = f" -> {cue['end']}" if safe_text(cue.get("end")) else ""
            lines.append(f"[{cue['start']}{end_part}] {cue['text']}")
        lines.append("")
    lines.append("## Raw Transcript")
    lines.append(raw_text.strip())
    return "\n".join(lines).strip() + "\n"


def build_video_summary(title: str, cues: list[dict[str, str]], raw_text: str) -> str:
    if cues:
        parts = []
        for cue in cues[:3]:
            parts.append(f"{cue['start']} {summarize_text(cue['text'], max_chars=60)}")
        return " | ".join(parts)
    return summarize_text(raw_text or title)


def ingest_video(
    *,
    transcript_file: str | Path,
    title: str = "",
    province: str = "",
    specialty: str = "",
    tags: list[str] | None = None,
    confidence: int = 78,
    video_file: str | Path | None = None,
    video_ref: str = "",
) -> dict[str, str]:
    transcript_path = Path(transcript_file).resolve()
    raw_text, cues, transcript_format = parse_transcript(transcript_path)
    resolved_title = safe_text(title) or transcript_path.stem
    resolved_video_ref = safe_text(video_ref)
    evidence_refs = [str(transcript_path)]
    source_path_for_id = transcript_path
    if video_file:
        source_path_for_id = Path(video_file).resolve()
        evidence_refs.insert(0, str(source_path_for_id))
    elif resolved_video_ref:
        evidence_refs.insert(0, resolved_video_ref)
    source_id = make_source_id(source_kind="video", title=resolved_title, source_path=source_path_for_id)
    transcript_doc = build_transcript_doc(
        title=resolved_title,
        video_ref=evidence_refs[0] if evidence_refs else resolved_video_ref,
        raw_text=raw_text,
        cues=cues,
    )
    pack = build_source_pack(
        source_id=source_id,
        source_kind="video",
        title=resolved_title,
        summary=build_video_summary(resolved_title, cues, raw_text),
        full_text_path="",
        evidence_refs=evidence_refs,
        province=province,
        specialty=specialty,
        tags=(tags or []) + ["video", "transcript"],
        confidence=confidence,
        metadata={
            "transcript_format": transcript_format,
            "cue_count": len(cues),
            "has_timeline": bool(cues),
        },
    )
    pack_path, text_path = write_source_pack(pack=pack, full_text=transcript_doc)
    return {"source_id": source_id, "pack_path": str(pack_path), "full_text_path": str(text_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest one video transcript into source pack format.")
    parser.add_argument("--transcript-file", required=True)
    parser.add_argument("--video-file", default="")
    parser.add_argument("--video-ref", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--province", default="")
    parser.add_argument("--specialty", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--confidence", type=int, default=78)
    args = parser.parse_args()

    result = ingest_video(
        transcript_file=args.transcript_file,
        video_file=args.video_file or None,
        video_ref=args.video_ref,
        title=args.title,
        province=args.province,
        specialty=args.specialty,
        tags=[item.strip() for item in args.tags.split(",") if item.strip()],
        confidence=args.confidence,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
