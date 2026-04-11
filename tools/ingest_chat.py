from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.source_pack import build_source_pack, make_source_id, read_text_with_fallbacks, safe_text, summarize_text, write_source_pack  # noqa: E402


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = safe_text(item.get("text") or item.get("content"))
            else:
                text = safe_text(item)
            if text:
                parts.append(text)
        return "\n".join(parts).strip()
    return safe_text(content)


def parse_chat_messages(path: str | Path) -> list[dict[str, str]]:
    source_path = Path(path)
    if source_path.suffix.lower() == ".json":
        raw = json.loads(read_text_with_fallbacks(source_path))
        if isinstance(raw, dict) and isinstance(raw.get("messages"), list):
            raw = raw["messages"]
        if not isinstance(raw, list):
            raise ValueError("chat json must be a list or contain a messages list")
        messages: list[dict[str, str]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            role = safe_text(item.get("role") or item.get("speaker") or "unknown") or "unknown"
            content = _flatten_content(item.get("content") or item.get("text") or item.get("message"))
            if content:
                messages.append({"role": role, "content": content})
        return messages

    text = read_text_with_fallbacks(source_path)
    pattern = re.compile(r"^(用户|助手|系统|user|assistant|system|甲方|乙方)\s*[:：]\s*(.+)$", re.IGNORECASE)
    messages: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        match = pattern.match(line.strip())
        if match:
            if current and safe_text(current.get("content")):
                messages.append({"role": current["role"], "content": current["content"].strip()})
            current = {"role": match.group(1), "content": match.group(2).strip()}
            continue
        if current is None:
            current = {"role": "unknown", "content": line.strip()}
            continue
        current["content"] += ("\n" + line.strip()) if line.strip() else "\n"
    if current and safe_text(current.get("content")):
        messages.append({"role": current["role"], "content": current["content"].strip()})
    return messages


def build_chat_transcript(messages: list[dict[str, str]]) -> str:
    blocks: list[str] = []
    for index, item in enumerate(messages, start=1):
        blocks.append(f"## Turn {index} [{safe_text(item.get('role')) or 'unknown'}]\n{safe_text(item.get('content'))}")
    return "\n\n".join(blocks).strip()


def build_chat_summary(messages: list[dict[str, str]]) -> str:
    highlights: list[str] = []
    for item in messages:
        role = safe_text(item.get("role"))
        content = safe_text(item.get("content"))
        if not content:
            continue
        prefix = "问" if role.lower() in {"user", "用户", "甲方"} else "答"
        highlights.append(f"{prefix}: {summarize_text(content, max_chars=90)}")
        if len(highlights) >= 4:
            break
    return " | ".join(highlights)


def ingest_chat(*, input_path: str | Path, title: str = "", province: str = "", specialty: str = "", tags: list[str] | None = None, confidence: int = 80) -> dict[str, str]:
    source_path = Path(input_path).resolve()
    messages = parse_chat_messages(source_path)
    transcript = build_chat_transcript(messages)
    resolved_title = safe_text(title) or source_path.stem
    source_id = make_source_id(source_kind="chat", title=resolved_title, source_path=source_path)
    pack = build_source_pack(
        source_id=source_id,
        source_kind="chat",
        title=resolved_title,
        summary=build_chat_summary(messages) or summarize_text(transcript),
        full_text_path="",
        evidence_refs=[str(source_path)],
        province=province,
        specialty=specialty,
        tags=(tags or []) + ["chat"],
        confidence=confidence,
        metadata={"turn_count": len(messages)},
    )
    pack_path, text_path = write_source_pack(pack=pack, full_text=transcript)
    return {"source_id": source_id, "pack_path": str(pack_path), "full_text_path": str(text_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest one chat transcript into source pack format.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--title", default="")
    parser.add_argument("--province", default="")
    parser.add_argument("--specialty", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--confidence", type=int, default=80)
    args = parser.parse_args()

    result = ingest_chat(
        input_path=args.input,
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
