from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    from app.text_utils import repair_mojibake_data, repair_mojibake_text
except Exception:  # pragma: no cover
    def repair_mojibake_text(text: str | None, *, preserve_newlines: bool = False) -> str | None:
        return text

    def repair_mojibake_data(value, *, preserve_newlines: bool = False):
        return value


SOURCE_PACK_ROOT = PROJECT_ROOT / "data" / "source_packs"
SOURCE_PACKS_DIR = SOURCE_PACK_ROOT / "packs"
SOURCE_TEXTS_DIR = SOURCE_PACK_ROOT / "texts"

_PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")
_REPLACEMENT_RE = re.compile(r"\ufffd")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_MOJIBAKE_HINTS = set("脌脕脗脙脛脜脝脟脠脡脢脣脤脥脦脧脨脩脪脫脭脮脰脴脵脷脹脺脻脼脿谩芒茫盲氓忙莽猫茅锚毛矛铆卯茂冒帽貌贸么玫枚酶霉煤没眉媒镁每")


def ensure_source_pack_dirs() -> None:
    SOURCE_PACKS_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_TEXTS_DIR.mkdir(parents=True, exist_ok=True)


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").strip()


def resolve_runtime_path(path: str | Path) -> Path:
    raw_path = safe_text(path)
    if not raw_path:
        return Path("")

    candidate = Path(raw_path)
    if candidate.exists():
        return candidate

    normalized = raw_path.replace("\\", "/")
    marker = "data/source_packs/"
    idx = normalized.lower().find(marker)
    if idx >= 0:
        relative = normalized[idx + len(marker):].lstrip("/")
        remapped = SOURCE_PACK_ROOT / Path(relative)
        if remapped.exists():
            return remapped

    if candidate.name:
        for remapped in (SOURCE_TEXTS_DIR / candidate.name, SOURCE_PACKS_DIR / candidate.name):
            if remapped.exists():
                return remapped

    return candidate


def read_text_with_fallbacks(path: str | Path) -> str:
    source_path = resolve_runtime_path(path)
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "cp936"):
        try:
            return source_path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return source_path.read_text(encoding="utf-8", errors="ignore")


def _repair_text(value: Any) -> str:
    text = safe_text(value)
    if not text:
        return ""
    repaired = repair_mojibake_text(text, preserve_newlines=True)
    return safe_text(repaired) or text


def _count_cjk(text: str) -> int:
    return len(_CJK_RE.findall(text))


def _cjk_overlap(left: str, right: str) -> int:
    return len(set(_CJK_RE.findall(left)) & set(_CJK_RE.findall(right)))


def _looks_like_mojibake(text: str) -> bool:
    value = safe_text(text)
    if not value:
        return False
    if _PRIVATE_USE_RE.search(value) or _REPLACEMENT_RE.search(value):
        return True
    if sum(1 for ch in value if ch in _MOJIBAKE_HINTS) >= 2:
        return True
    question_marks = value.count("?")
    if question_marks >= 2 and question_marks >= _count_cjk(value):
        return True
    return False


def _first_meaningful_line(path: str | Path) -> str:
    text_path = safe_text(path)
    if not text_path:
        return ""
    try:
        raw = read_text_with_fallbacks(text_path)
    except Exception:
        return ""
    for line in raw.splitlines():
        candidate = safe_text(line.lstrip("#").strip())
        if candidate and candidate not in {"---", "```"}:
            return candidate
    return ""


def _first_existing_match_in_parent(ref_text: str, heading: str, title_hint: str) -> str:
    candidate_path = Path(ref_text)
    try:
        parent = candidate_path.parent
    except Exception:
        return ref_text
    if not parent.exists() or not parent.is_dir() or not candidate_path.suffix:
        return ref_text

    probes = [safe_text(heading), safe_text(title_hint)]
    for child in parent.glob(f"*{candidate_path.suffix}"):
        stem = safe_text(child.stem)
        if any(probe and probe in stem for probe in probes):
            return str(child)
    return ref_text


def _normalize_evidence_refs(evidence_refs: list[str], *, heading: str, title_hint: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for item in evidence_refs:
        text = _repair_text(item)
        if not text:
            continue
        if re.match(r"^[A-Za-z]:\\", text) or text.startswith(("/", "\\")):
            if not Path(text).exists():
                text = _first_existing_match_in_parent(text, heading, title_hint)
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _first_evidence_stem(evidence_refs: list[str]) -> str:
    for item in evidence_refs:
        stem = safe_text(Path(item).stem)
        if stem and not _looks_like_mojibake(stem):
            return stem
    return ""


def normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    if not tags:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for item in tags:
        text = _repair_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def summarize_text(text: str, *, max_chars: int = 220) -> str:
    repaired = _repair_text(text)
    lines = [line.strip() for line in safe_text(repaired).splitlines() if line.strip()]
    if not lines:
        return ""
    summary = " ".join(lines[:3]).strip()
    if len(summary) <= max_chars:
        return summary
    return summary[: max_chars - 3].rstrip() + "..."


def normalize_source_pack_metadata(pack: dict[str, Any]) -> dict[str, Any]:
    payload = repair_mojibake_data(pack or {}, preserve_newlines=True)
    if not isinstance(payload, dict):
        return pack or {}

    normalized = dict(payload)
    normalized["source_id"] = safe_text(normalized.get("source_id"))
    normalized["source_kind"] = _repair_text(normalized.get("source_kind"))
    normalized["full_text_path"] = str(resolve_runtime_path(normalized.get("full_text_path"))) if safe_text(normalized.get("full_text_path")) else ""
    normalized["province"] = _repair_text(normalized.get("province"))
    normalized["specialty"] = _repair_text(normalized.get("specialty"))
    normalized["tags"] = normalize_tags(normalized.get("tags"))
    normalized["metadata"] = repair_mojibake_data(normalized.get("metadata") or {}, preserve_newlines=True)

    heading = _first_meaningful_line(normalized.get("full_text_path"))
    title = _repair_text(normalized.get("title"))
    evidence_refs = _normalize_evidence_refs(
        [_repair_text(item) for item in (normalized.get("evidence_refs") or []) if _repair_text(item)],
        heading=heading,
        title_hint=title,
    )
    normalized["evidence_refs"] = evidence_refs

    fallback_title = heading or _first_evidence_stem(evidence_refs) or normalized["source_id"]
    if not title or _looks_like_mojibake(title):
        title = fallback_title
    elif heading and _count_cjk(heading) >= 4 and _cjk_overlap(title, heading) == 0:
        title = fallback_title
    normalized["title"] = title or normalized["source_id"]

    summary = _repair_text(normalized.get("summary"))
    if not summary or _looks_like_mojibake(summary):
        if normalized["full_text_path"]:
            summary = summarize_text(read_text_with_fallbacks(normalized["full_text_path"]))
    elif heading and _count_cjk(heading) >= 4 and _cjk_overlap(summary, heading) == 0:
        summary = summarize_text(read_text_with_fallbacks(normalized["full_text_path"]))
    normalized["summary"] = summary
    return normalized


def make_source_id(*, source_kind: str, title: str, source_path: str | Path) -> str:
    base = f"{source_kind}|{safe_text(title)}|{Path(source_path).resolve()}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"{safe_text(source_kind) or 'source'}-{digest}"


def choose_text_suffix(source_kind: str) -> str:
    if source_kind == "chat":
        return ".md"
    if source_kind == "image":
        return ".txt"
    return ".md"


def build_source_pack(
    *,
    source_id: str,
    source_kind: str,
    title: str,
    summary: str,
    full_text_path: str,
    evidence_refs: list[str],
    province: str = "",
    specialty: str = "",
    tags: list[str] | None = None,
    confidence: int = 80,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "source_id": safe_text(source_id),
        "source_kind": _repair_text(source_kind),
        "title": _repair_text(title),
        "summary": _repair_text(summary),
        "full_text_path": str(Path(full_text_path)) if safe_text(full_text_path) else "",
        "evidence_refs": [_repair_text(item) for item in evidence_refs if _repair_text(item)],
        "province": _repair_text(province),
        "specialty": _repair_text(specialty),
        "tags": normalize_tags(tags),
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "confidence": int(confidence),
        "metadata": repair_mojibake_data(metadata or {}, preserve_newlines=True),
    }


def write_source_pack(*, pack: dict[str, Any], full_text: str) -> tuple[Path, Path]:
    ensure_source_pack_dirs()
    source_id = safe_text(pack.get("source_id"))
    text_path = SOURCE_TEXTS_DIR / f"{source_id}{choose_text_suffix(safe_text(pack.get('source_kind')))}"
    text_path.write_text(full_text.strip() + "\n", encoding="utf-8")

    pack_copy = dict(pack)
    pack_copy["full_text_path"] = str(text_path)
    pack_copy = normalize_source_pack_metadata(pack_copy)
    pack_path = SOURCE_PACKS_DIR / f"{source_id}.json"
    pack_path.write_text(json.dumps(pack_copy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return pack_path, text_path


def load_source_pack(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def list_source_pack_files(packs_dir: str | Path | None = None) -> list[Path]:
    root = Path(packs_dir) if packs_dir else SOURCE_PACKS_DIR
    if not root.exists():
        return []
    return sorted(root.glob("*.json"))


def slugify(text: str, *, max_len: int = 64) -> str:
    value = safe_text(text)
    value = re.sub(r'[\\/:*?"<>|]+', "-", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-._ ")
    if not value:
        return "item"
    return value[:max_len].rstrip("-._ ")
