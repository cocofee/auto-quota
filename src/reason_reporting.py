from __future__ import annotations

from collections import Counter, defaultdict


def normalize_reason_tags(result: dict | None) -> list[str]:
    if not isinstance(result, dict):
        return []
    raw = result.get("reason_tags") or []
    normalized: list[str] = []
    for tag in raw:
        text = str(tag or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def primary_reason_of(result: dict | None) -> str:
    if not isinstance(result, dict):
        return ""
    primary = str(result.get("primary_reason") or "").strip()
    if primary:
        return primary
    tags = normalize_reason_tags(result)
    return tags[0] if tags else ""


def update_reason_counters(counter: Counter,
                           result: dict | None,
                           *,
                           include_primary: bool = True,
                           include_tags: bool = True) -> Counter:
    if not isinstance(counter, Counter):
        counter = Counter()
    if not isinstance(result, dict):
        return counter

    if include_primary:
        primary = primary_reason_of(result)
        if primary:
            counter[primary] += 1

    if include_tags:
        for tag in normalize_reason_tags(result):
            counter[f"tag:{tag}"] += 1

    return counter


def summarize_counter(counter: Counter,
                      *,
                      total: int = 0,
                      top_n: int = 10) -> list[dict]:
    if not counter:
        return []
    summary = []
    for key, count in counter.most_common(top_n):
        row = {"key": key, "count": count}
        if total > 0:
            row["rate"] = round(count / total * 100, 1)
        summary.append(row)
    return summary


def reason_bucket() -> dict:
    return {
        "primary": Counter(),
        "tags": Counter(),
    }


def update_reason_bucket(bucket: dict, result: dict | None) -> dict:
    if not isinstance(bucket, dict):
        bucket = reason_bucket()
    if not isinstance(result, dict):
        return bucket

    primary = primary_reason_of(result)
    if primary:
        bucket.setdefault("primary", Counter())[primary] += 1

    for tag in normalize_reason_tags(result):
        bucket.setdefault("tags", Counter())[tag] += 1
    return bucket


def summarize_reason_bucket(bucket: dict,
                            *,
                            total: int = 0,
                            top_n: int = 10) -> dict:
    if not isinstance(bucket, dict):
        bucket = reason_bucket()
    return {
        "primary": summarize_counter(bucket.get("primary", Counter()), total=total, top_n=top_n),
        "tags": summarize_counter(bucket.get("tags", Counter()), total=total, top_n=top_n),
    }


def nested_reason_buckets() -> defaultdict:
    return defaultdict(reason_bucket)
