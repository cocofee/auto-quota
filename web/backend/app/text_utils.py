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


def text_has_irreversible_loss(text: str | None) -> bool:
    """Detect text that was already collapsed into literal question marks."""
    if text is None:
        return False

    cleaned = _clean_text(str(text), preserve_newlines=True)
    if not cleaned:
        return False

    question_marks = cleaned.count("?")
    if question_marks < 2:
        return False

    cjk_count = _count_cjk(cleaned)
    return cjk_count == 0 or question_marks >= cjk_count


def repair_quota_name_loss(
    quotas,
    *reference_groups,
    preserve_newlines: bool = True,
):
    """Repair damaged quota names by reusing clean names from the same quota_id."""
    if quotas is None:
        return None, False
    if not isinstance(quotas, Sequence) or isinstance(quotas, (bytes, bytearray, str)):
        return quotas, False

    reference_names: dict[str, str] = {}
    for group in reference_groups:
        if not isinstance(group, Sequence) or isinstance(group, (bytes, bytearray, str)):
            continue
        for item in group:
            if not isinstance(item, Mapping):
                continue
            quota_id = str(item.get("quota_id") or "").strip()
            name = repair_mojibake_text(
                str(item.get("name") or ""),
                preserve_newlines=preserve_newlines,
            ) or ""
            if quota_id and name and not text_has_irreversible_loss(name):
                reference_names.setdefault(quota_id, name)

    repaired_items = []
    changed = False
    for item in quotas:
        if not isinstance(item, Mapping):
            repaired_item = repair_mojibake_data(item, preserve_newlines=preserve_newlines)
            repaired_items.append(repaired_item)
            changed |= repaired_item != item
            continue

        repaired_item = repair_mojibake_data(dict(item), preserve_newlines=preserve_newlines)
        quota_id = str(repaired_item.get("quota_id") or "").strip()
        name = str(repaired_item.get("name") or "").strip()
        replacement = reference_names.get(quota_id)
        if replacement and text_has_irreversible_loss(name):
            repaired_item["name"] = replacement
        repaired_items.append(repaired_item)
        changed |= repaired_item != item

    return repaired_items, changed


def normalize_client_filename(filename: str | None, default: str = "upload.xlsx") -> str:
    """Strip unsafe path parts and repair mojibake in uploaded filenames."""
    raw = (filename or "").replace("\\", "/").split("/")[-1]
    raw = raw.replace("\x00", "").replace("\r", "").replace("\n", "").strip().strip(". ")
    normalized = repair_mojibake_text(raw) or default
    return normalized
