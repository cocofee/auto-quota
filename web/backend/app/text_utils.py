import re
from collections.abc import Mapping, Sequence


_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_PRIVATE_USE_RE = re.compile(r"[\ue000-\uf8ff]")
_REPLACEMENT_RE = re.compile(r"\ufffd")
_MOJIBAKE_CHARS = set(
    "ÀÁÂÃÄÅÆÇÈÉÊËÌÍÎÏÐÑÒÓÔÕÖØÙÚÛÜÝÞ"
    "àáâãäåæçèéêëìíîïðñòóôõöøùúûüýþÿ"
)


def _count_cjk(text: str) -> int:
    return len(_CJK_RE.findall(text))


def _count_mojibake_chars(text: str) -> int:
    return sum(1 for ch in text if ch in _MOJIBAKE_CHARS)


def _count_private_use_chars(text: str) -> int:
    return len(_PRIVATE_USE_RE.findall(text))


def _count_replacement_chars(text: str) -> int:
    return len(_REPLACEMENT_RE.findall(text))


def _clean_text(text: str, *, preserve_newlines: bool) -> str:
    cleaned = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    if preserve_newlines:
        return cleaned.strip(" \t")
    return cleaned.replace("\n", "").strip(" \t")


def _repair_candidates(text: str) -> list[str]:
    candidates = [text]
    for codec in ("latin1", "gb18030"):
        try:
            repaired = text.encode(codec).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        if repaired not in candidates:
            candidates.append(repaired)
    return candidates


def _score_text(text: str) -> tuple[int, int, int, int]:
    return (
        _count_cjk(text),
        -_count_mojibake_chars(text),
        -_count_private_use_chars(text),
        -_count_replacement_chars(text),
    )


def repair_mojibake_text(text: str | None, *, preserve_newlines: bool = False) -> str | None:
    """Repair common UTF-8-as-Latin-1 mojibake while leaving valid text unchanged."""
    if text is None:
        return None

    cleaned = _clean_text(text, preserve_newlines=preserve_newlines)
    if not cleaned:
        return cleaned

    candidates = _repair_candidates(cleaned)
    best = max(candidates, key=_score_text)
    return best if _score_text(best) > _score_text(cleaned) else cleaned


def repair_mojibake_data(value, *, preserve_newlines: bool = False):
    """Recursively repair mojibake strings inside JSON-like structures."""
    if isinstance(value, str):
        return repair_mojibake_text(value, preserve_newlines=preserve_newlines)
    if isinstance(value, Mapping):
        return {
            key: repair_mojibake_data(item, preserve_newlines=preserve_newlines)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [
            repair_mojibake_data(item, preserve_newlines=preserve_newlines)
            for item in value
        ]
    return value


def normalize_client_filename(filename: str | None, default: str = "upload.xlsx") -> str:
    """Strip unsafe path parts and repair mojibake in uploaded filenames."""
    raw = (filename or "").replace("\\", "/").split("/")[-1]
    raw = raw.replace("\x00", "").replace("\r", "").replace("\n", "").strip().strip(". ")
    normalized = repair_mojibake_text(raw) or default
    return normalized
