# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

from src.query_builder import build_primary_query_profile, extract_description_fields

PROJECT_ROOT = Path(__file__).resolve().parent.parent


NOISE_STARTERS = (
    "综合单价中含",
    "工作内容：",
    "工作内容:",
    "其他说明：",
    "其他说明:",
    "其他：",
    "其他:",
    "未尽事宜",
    "满足规范",
    "满足设计",
    "详见设计",
    "详见图纸",
    "详见招标",
    "详见相关",
    "符合设计",
    "按规范要求",
    "由投标人自行考虑",
    "具体详见",
    "应符合",
    "综合考虑完成该工艺",
    "配套",
)

SOFT_NOISE_STARTERS = (
    "包含",
    "包括",
    "含",
)

DECISIVE_FIELDS = (
    "名称",
    "材质",
    "规格",
    "连接形式",
    "连接方式",
    "安装部位",
    "介质",
    "类型",
    "配置形式",
    "敷设方式",
    "管径",
    "型号",
)

NOISE_FIELDS = (
    "工作内容",
    "未尽事宜",
    "其他",
    "其他说明",
    "备注",
    "压力试验及吹、洗设计要求",
)

TRIM_PHRASES = (
    "采购并安装",
    "制作及安装",
    "套管制作及安装",
    "支架制作安装",
    "防火封堵",
    "管道支架制作安装",
)

SPEC_PATTERNS = (
    r"DN\d+(?:\.\d+)?",
    r"De\d+(?:\.\d+)?",
    r"Φ\d+(?:\.\d+)?",
    r"φ\d+(?:\.\d+)?",
    r"\d+(?:\.\d+)?\s*(?:mm|MM|W|KW|kW|MPA|mm2|mm²)",
    r"\d+(?:\.\d+)?\s*[x×X*]\s*\d+(?:\.\d+)?(?:\s*[x×X*]\s*\d+(?:\.\d+)?)?",
)

FIELD_PATTERN = re.compile(
    r"(?P<label>[\u4e00-\u9fffA-Za-z0-9()（）/\-]{2,18})\s*[:：]\s*"
    r"(?P<value>.*?)(?=(?:\s+[\u4e00-\u9fffA-Za-z0-9()（）/\-]{2,18}\s*[:：])|//|$)"
)

BRACKET_PATTERN = re.compile(r"[（(][^（）()]{0,30}[）)]")


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def _combine_bill_text(bill_name: str, bill_text: str) -> str:
    parts: list[str] = []
    for part in (_clean_text(bill_name), _clean_text(bill_text)):
        if part and part not in parts:
            parts.append(part)
    return " ".join(parts).strip()


def _find_starter_index(text: str, starter: str) -> int:
    idx = text.find(starter)
    if idx < 0:
        return -1
    if idx <= 6:
        return -1
    prev = text[idx - 1] if idx > 0 else ""
    if starter in SOFT_NOISE_STARTERS and prev not in {" ", "，", ",", "；", ";", "。", ")", "）"}:
        return -1
    return idx


def split_noise_segment(text: str) -> tuple[str, str, str]:
    profile = build_primary_query_profile("", text)
    full_text = _clean_text(profile.get("full_text") or text)
    primary = _clean_text(profile.get("primary_text") or full_text)
    marker = _clean_text(profile.get("noise_marker") or "")
    noise = full_text[len(primary):].strip() if primary and full_text.startswith(primary) else ""
    return primary or full_text, noise, marker


def parse_structured_fields(text: str) -> dict[str, str]:
    return {
        str(label): _clean_text(value)
        for label, value in extract_description_fields(text).items()
        if _clean_text(value)
    }


def _cleanup_field_value(label: str, value: str) -> str:
    cleaned = _clean_text(value)
    cleaned = BRACKET_PATTERN.sub(" ", cleaned)
    cleaned = cleaned.strip(" ,，;；。/")
    if label in {"工作内容", "其他", "其他说明", "未尽事宜", "备注"}:
        return ""
    return _clean_text(cleaned)


def _strip_brackets(text: str) -> str:
    return _clean_text(BRACKET_PATTERN.sub(" ", text))


def _take_front_segment(text: str, max_chars: int = 48) -> str:
    clean = _clean_text(text)
    if len(clean) <= max_chars:
        return clean
    for punct in ("。", "；", ";", "，", ","):
        idx = clean.find(punct)
        if 8 <= idx <= max_chars:
            return clean[:idx]
    return clean[:max_chars]


def _extract_spec_terms(text: str) -> list[str]:
    specs: list[str] = []
    for pattern in SPEC_PATTERNS:
        for value in re.findall(pattern, text):
            token = _clean_text(value)
            if token and token not in specs:
                specs.append(token)
    return specs[:4]


def _remove_tail_noise_phrases(text: str) -> str:
    clean = _clean_text(text)
    for phrase in TRIM_PHRASES:
        idx = clean.find(phrase)
        if idx >= 8:
            clean = clean[:idx].strip(" ，,；;。")
    return _clean_text(clean)


def rewrite_keyword_miss_query(
    *,
    bill_name: str,
    bill_text: str,
    old_query: str = "",
) -> dict:
    profile = build_primary_query_profile(bill_name, bill_text)
    full_text = _clean_text(profile.get("full_text") or _combine_bill_text(bill_name, bill_text))
    primary_text = _clean_text(profile.get("primary_text") or full_text)
    noise_text = full_text[len(primary_text):].strip() if primary_text and full_text.startswith(primary_text) else ""
    noise_marker = _clean_text(profile.get("noise_marker") or "")
    fields = dict(profile.get("fields") or {})

    terms: list[str] = []
    strategy = str(profile.get("strategy") or "front_segment")
    used_fields: list[str] = list(profile.get("used_fields") or [])

    for term in list(profile.get("decisive_terms") or []):
        token = _clean_text(term)
        if token and token not in terms:
            terms.append(token)

    if not terms:
        front = _take_front_segment(primary_text, max_chars=48)
        front = _strip_brackets(front)
        front = _remove_tail_noise_phrases(front)
        if front:
            terms.append(front)

    spec_terms = list(profile.get("key_specs") or []) or _extract_spec_terms(primary_text)
    for spec in spec_terms:
        if not any(spec in term for term in terms):
            terms.append(spec)

    new_query = _clean_text(" ".join(terms))
    new_query = _remove_tail_noise_phrases(new_query)
    if not new_query:
        new_query = _take_front_segment(_strip_brackets(full_text), max_chars=48)

    return {
        "old_query": _clean_text(old_query),
        "new_query": _clean_text(new_query),
        "full_text": full_text,
        "primary_text": _clean_text(primary_text),
        "noise_text": _clean_text(noise_text),
        "noise_marker": noise_marker,
        "strategy": strategy,
        "used_fields": used_fields,
        "spec_terms": spec_terms,
    }


def _load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _classification_from_router(router: dict) -> dict:
    router = dict(router or {})
    return {
        "primary": str(router.get("primary_book") or "").strip(),
        "fallbacks": list(router.get("fallback_books") or []),
        "candidate_books": list(router.get("candidate_books") or []),
        "search_books": list(router.get("search_books") or []),
        "hard_book_constraints": list(router.get("hard_book_constraints") or []),
        "route_mode": str(router.get("route_mode") or "balanced"),
        "allow_cross_book_escape": bool(router.get("allow_cross_book_escape", True)),
    }


def _candidate_payload(candidates: list[dict], top_n: int = 5) -> list[dict]:
    payload: list[dict] = []
    for candidate in candidates[:top_n]:
        payload.append(
            {
                "quota_id": str(candidate.get("quota_id", "") or ""),
                "name": str(candidate.get("name", "") or ""),
                "hybrid_score": float(candidate.get("hybrid_score", 0.0) or 0.0),
                "match_source": str(candidate.get("match_source", "") or ""),
            }
        )
    return payload


def _search_once(searcher, *, query: str, router: dict, top_k: int) -> list[dict]:
    from src.match_core import cascade_search

    classification = _classification_from_router(router)
    return cascade_search(searcher, query, classification, top_k=top_k)


def _oracle_hit(oracle_ids: list[str], candidates: list[dict]) -> bool:
    candidate_ids = {str(candidate.get("quota_id", "") or "").strip() for candidate in candidates}
    return any(oracle_id in candidate_ids for oracle_id in oracle_ids)


def evaluate_keyword_miss_rewrite(
    *,
    input_path: Path,
    profile: str = "smoke",
    limit: int | None = None,
) -> tuple[list[dict], dict]:
    from tools.run_real_eval import _configure_logging, _runtime_profile
    from src.match_engine import init_search_components

    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TQDM_DISABLE", "1")
    _configure_logging("WARNING")

    records = _load_jsonl(input_path)
    if limit is not None and limit > 0:
        records = records[:limit]

    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[_clean_text(record.get("province"))].append(record)

    details: list[dict] = []
    by_strategy = Counter()
    by_marker = Counter()
    skipped: list[dict] = []

    with _runtime_profile(profile):
        for province in sorted(grouped):
            try:
                searcher, _validator = init_search_components(resolved_province=province)
            except Exception as exc:
                skipped.append({"province": province, "reason": str(exc), "count": len(grouped[province])})
                continue

            for record in grouped[province]:
                top_k = int((record.get("retriever") or {}).get("candidate_count") or 12)
                oracle_ids = [str(value).strip() for value in (record.get("oracle_quota_ids") or []) if str(value).strip()]
                rewrite = rewrite_keyword_miss_query(
                    bill_name=str(record.get("bill_name") or ""),
                    bill_text=str(record.get("bill_text") or ""),
                    old_query=str(record.get("search_query") or ""),
                )
                old_candidates = _search_once(
                    searcher,
                    query=rewrite["old_query"],
                    router=record.get("router") or {},
                    top_k=top_k,
                )
                new_candidates = _search_once(
                    searcher,
                    query=rewrite["new_query"],
                    router=record.get("router") or {},
                    top_k=top_k,
                )

                old_hit = _oracle_hit(oracle_ids, old_candidates)
                new_hit = _oracle_hit(oracle_ids, new_candidates)
                status = "unchanged"
                if (not old_hit) and new_hit:
                    status = "improved"
                elif old_hit and (not new_hit):
                    status = "regressed"

                by_strategy.update([rewrite["strategy"]])
                if rewrite["noise_marker"]:
                    by_marker.update([rewrite["noise_marker"]])

                details.append(
                    {
                        "sample_id": str(record.get("sample_id") or ""),
                        "province": province,
                        "specialty": str(record.get("specialty") or ""),
                        "bill_name": str(record.get("bill_name") or ""),
                        "bill_text": str(record.get("bill_text") or ""),
                        "oracle_quota_ids": oracle_ids,
                        "oracle_quota_names": list(record.get("oracle_quota_names") or []),
                        "top_k": top_k,
                        "status": status,
                        "old_hit": old_hit,
                        "new_hit": new_hit,
                        "old_query": rewrite["old_query"],
                        "new_query": rewrite["new_query"],
                        "primary_text": rewrite["primary_text"],
                        "noise_text": rewrite["noise_text"],
                        "noise_marker": rewrite["noise_marker"],
                        "strategy": rewrite["strategy"],
                        "used_fields": rewrite["used_fields"],
                        "spec_terms": rewrite["spec_terms"],
                        "router": {
                            "primary_book": str((record.get("router") or {}).get("primary_book") or ""),
                            "search_books": list((record.get("router") or {}).get("search_books") or []),
                            "route_mode": str((record.get("router") or {}).get("route_mode") or ""),
                        },
                        "old_candidates": _candidate_payload(old_candidates),
                        "new_candidates": _candidate_payload(new_candidates),
                    }
                )

    total = len(details)
    improved = sum(1 for row in details if row["status"] == "improved")
    regressed = sum(1 for row in details if row["status"] == "regressed")
    old_hits = sum(1 for row in details if row["old_hit"])
    new_hits = sum(1 for row in details if row["new_hit"])

    summary = {
        "input_path": str(input_path),
        "profile": profile,
        "total": total,
        "old_hits": old_hits,
        "new_hits": new_hits,
        "old_recall_at_k": round(old_hits / max(total, 1), 4),
        "new_recall_at_k": round(new_hits / max(total, 1), 4),
        "net_gain": new_hits - old_hits,
        "improved": improved,
        "regressed": regressed,
        "unchanged": total - improved - regressed,
        "strategy_counts": dict(sorted(by_strategy.items())),
        "noise_marker_counts": dict(sorted(by_marker.items())),
        "skipped_provinces": skipped,
    }
    return details, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline keyword-miss query rewrite evaluation")
    parser.add_argument(
        "--input",
        default=str(PROJECT_ROOT / "output" / "real_eval" / "keyword_miss_export.jsonl"),
        help="keyword miss export jsonl path",
    )
    parser.add_argument(
        "--details-out",
        default=str(PROJECT_ROOT / "output" / "real_eval" / "keyword_miss_query_rewrite_eval.details.jsonl"),
        help="details jsonl output",
    )
    parser.add_argument(
        "--summary-out",
        default=str(PROJECT_ROOT / "output" / "real_eval" / "keyword_miss_query_rewrite_eval.summary.json"),
        help="summary json output",
    )
    parser.add_argument("--profile", default="smoke", choices=["smoke", "dev", "full"])
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    details, summary = evaluate_keyword_miss_rewrite(
        input_path=Path(args.input),
        profile=args.profile,
        limit=args.limit or None,
    )

    details_path = Path(args.details_out)
    details_path.parent.mkdir(parents=True, exist_ok=True)
    with details_path.open("w", encoding="utf-8") as handle:
        for row in details:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary_path = Path(args.summary_out)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[keyword-miss-rewrite] total={summary['total']} "
        f"old_recall@k={summary['old_recall_at_k']:.4f} "
        f"new_recall@k={summary['new_recall_at_k']:.4f} "
        f"net_gain={summary['net_gain']} improved={summary['improved']} regressed={summary['regressed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
