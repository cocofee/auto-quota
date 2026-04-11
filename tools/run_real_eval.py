from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_DATASET_PATH = PROJECT_ROOT / "output" / "real_eval" / "real_eval.jsonl"
DEFAULT_SUMMARY_PATH = PROJECT_ROOT / "output" / "real_eval" / "real_eval_summary.json"
PROFILE_DEFAULTS = {
    "smoke": {
        "max_per_province": 20,
        "hybrid_top_k": 12,
        "bm25_top_k": 16,
        "vector_top_k": 16,
        "reranker_top_k": 12,
        "hybrid_query_variants": 3,
        "bm25_synonym_expansion": False,
    },
    "dev": {
        "max_per_province": 100,
        "hybrid_top_k": 16,
        "bm25_top_k": 20,
        "vector_top_k": 20,
        "reranker_top_k": 16,
        "hybrid_query_variants": 3,
        "bm25_synonym_expansion": True,
    },
    "full": {
        "max_per_province": None,
        "hybrid_top_k": None,
        "bm25_top_k": None,
        "vector_top_k": None,
        "reranker_top_k": None,
        "hybrid_query_variants": None,
        "bm25_synonym_expansion": None,
    },
}


def _read_jsonl(path: str | Path) -> list[dict]:
    path = Path(path)
    records: list[dict] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def _configure_logging(level: str = "WARNING") -> None:
    try:
        from loguru import logger
    except Exception:
        return
    try:
        logger.remove()
        logger.add(sys.stderr, level=str(level or "WARNING").upper())
    except Exception:
        pass


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").strip().split())


def _bill_item_from_record(record: dict, seq: int) -> dict:
    bill_name = _clean_text(record.get("bill_name"))
    bill_text = _clean_text(record.get("bill_text"))
    if not bill_name:
        bill_name = bill_text[:40] or f"item_{seq}"
    project_name = _clean_text(record.get("project_name"))
    source_file_name = _clean_text(record.get("source_file_name"))
    source_file_stem = _clean_text(record.get("source_file_stem"))
    context_prior = record.get("context_prior")
    if not isinstance(context_prior, dict):
        context_prior = {}
    context_prior = dict(context_prior)
    if project_name and not context_prior.get("project_name"):
        context_prior["project_name"] = project_name
    if bill_name and not context_prior.get("bill_name"):
        context_prior["bill_name"] = bill_name
    if source_file_name and not context_prior.get("source_file_name"):
        context_prior["source_file_name"] = source_file_name
    if source_file_stem and not context_prior.get("source_file_stem"):
        context_prior["source_file_stem"] = source_file_stem
    return {
        "name": bill_name,
        "description": bill_text,
        "unit": "",
        "quantity": 1,
        "seq": seq,
        "specialty": _clean_text(record.get("specialty")),
        "project_name": project_name,
        "bill_name": bill_name,
        "section": _clean_text(record.get("section")),
        "sheet_name": _clean_text(record.get("sheet_name")),
        "source_file_name": source_file_name,
        "source_file_stem": source_file_stem,
        "context_prior": dict(context_prior),
    }


def _get_quota_book(qid: str) -> str:
    qid = str(qid or "").strip()
    if len(qid) >= 2 and qid[0] == "C" and qid[1].isalpha():
        letter_map = {
            "A": "C1",
            "B": "C2",
            "C": "C3",
            "D": "C4",
            "E": "C5",
            "F": "C6",
            "G": "C7",
            "H": "C8",
            "I": "C9",
            "J": "C10",
            "K": "C11",
            "L": "C12",
        }
        return letter_map.get(qid[1], "")
    match = re.match(r"(C\d+)-", qid)
    if match:
        return match.group(1)
    match = re.match(r"(\d+)-", qid)
    if match:
        return f"C{match.group(1)}"
    return ""


def _diagnose_cause(record: dict, algo_id: str, algo_name: str, quotas: list[dict]) -> str:
    if not quotas:
        return "no_result"

    stored_first = str((record.get("oracle_quota_names") or [""])[0] or "")
    stored_keywords = set(stored_first.replace("(", " ").replace(")", " ").split())
    algo_keywords = set(str(algo_name or "").replace("(", " ").replace(")", " ").split())
    ignore = {"安装", "制作", "周长", "mm", "m2", "以内", "≤"}
    stored_keywords -= ignore
    algo_keywords -= ignore

    stored_id = str((record.get("oracle_quota_ids") or [""])[0] or "")
    if stored_id and algo_id:
        stored_book = _get_quota_book(stored_id)
        algo_book = _get_quota_book(algo_id)
        if stored_book and algo_book and stored_book != algo_book:
            return "wrong_book"

    if stored_keywords & algo_keywords:
        return "wrong_tier"
    return "synonym_gap"


def _trace_step(result: dict, stage: str) -> dict:
    trace = result.get("trace") or {}
    for step in reversed(list(trace.get("steps") or [])):
        if isinstance(step, dict) and step.get("stage") == stage:
            return step
    return {}


_ORACLE_DB_NAME_CACHE: dict[tuple[str, str], str | None] = {}


def _lookup_quota_name_from_db(province: str, quota_id: str) -> str | None:
    province = _clean_text(province)
    quota_id = str(quota_id or "").strip()
    if not province or not quota_id:
        return None
    cache_key = (province, quota_id)
    if cache_key in _ORACLE_DB_NAME_CACHE:
        return _ORACLE_DB_NAME_CACHE[cache_key]
    try:
        import config

        db_path = config.get_quota_db_path(province)
        if not Path(db_path).exists():
            _ORACLE_DB_NAME_CACHE[cache_key] = None
            return None
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT name FROM quotas WHERE quota_id = ? LIMIT 1",
                (quota_id,),
            ).fetchone()
    except Exception:
        row = None
    value = str(row[0]).strip() if row and row[0] else None
    _ORACLE_DB_NAME_CACHE[cache_key] = value
    return value


def _build_oracle_alignment(record: dict) -> dict:
    province = _clean_text(record.get("province"))
    oracle_ids = [str(value).strip() for value in (record.get("oracle_quota_ids") or []) if str(value).strip()]
    oracle_names = [str(value or "").strip() for value in (record.get("oracle_quota_names") or [])]
    rows: list[dict] = []
    status = "ok"
    for idx, quota_id in enumerate(oracle_ids):
        stored_name = oracle_names[idx] if idx < len(oracle_names) else ""
        db_name = _lookup_quota_name_from_db(province, quota_id)
        row = {
            "quota_id": quota_id,
            "oracle_name": stored_name,
            "db_name": db_name or "",
            "id_exists": bool(db_name),
            "name_matches": bool(db_name) and (not stored_name or stored_name == db_name),
        }
        rows.append(row)
        if not db_name:
            status = "id_missing"
            break
        if stored_name and stored_name != db_name:
            status = "name_mismatch"
            break
    return {
        "status": status,
        "rows": rows,
    }


def _diagnose_error_stage(result: dict, oracle_ids: list[str], *, algo_id: str, is_match: bool, oracle_found: bool) -> tuple[str, str]:
    if is_match:
        return "correct", ""
    if not oracle_found:
        return "retriever", "oracle_not_in_candidates"

    pre_ltr_top1_id = str(result.get("pre_ltr_top1_id", "") or "")
    post_ltr_top1_id = str(result.get("post_ltr_top1_id", "") or "")
    post_cgr_top1_id = str(result.get("post_cgr_top1_id", "") or "")
    post_arbiter_top1_id = str(result.get("post_arbiter_top1_id", "") or "")
    post_explicit_top1_id = str(result.get("post_explicit_top1_id", "") or "")
    post_anchor_top1_id = str(result.get("post_anchor_top1_id", "") or "")
    post_final_top1_id = str(result.get("post_final_top1_id", algo_id) or algo_id or "")

    final_step = _trace_step(result, "final_validate")
    final_validation = dict(result.get("final_validation") or final_step.get("final_validation") or {})
    vetoed = bool(final_validation.get("vetoed")) or str(final_validation.get("status") or "").strip() == "vetoed"

    if pre_ltr_top1_id and pre_ltr_top1_id in oracle_ids and post_ltr_top1_id and post_ltr_top1_id not in oracle_ids:
        return "ltr_ranker", "pre_ltr_correct_but_ltr_changed"
    if post_ltr_top1_id and post_ltr_top1_id in oracle_ids and post_cgr_top1_id and post_cgr_top1_id not in oracle_ids:
        return "cgr_ranker", "post_ltr_correct_but_cgr_changed"
    if post_cgr_top1_id and post_cgr_top1_id in oracle_ids and post_arbiter_top1_id and post_arbiter_top1_id not in oracle_ids:
        return "candidate_arbiter", "post_ltr_correct_but_arbiter_changed"
    if post_arbiter_top1_id and post_arbiter_top1_id in oracle_ids and post_explicit_top1_id and post_explicit_top1_id not in oracle_ids:
        return "explicit_override", "post_arbiter_correct_but_explicit_changed"
    if post_anchor_top1_id and post_anchor_top1_id in oracle_ids and post_final_top1_id and post_final_top1_id not in oracle_ids:
        if vetoed or final_validation:
            return "final_validator", "post_anchor_correct_but_final_changed"
        return "postprocess", "post_anchor_correct_but_final_changed"
    if post_ltr_top1_id and post_ltr_top1_id in oracle_ids and post_final_top1_id and post_final_top1_id not in oracle_ids:
        if vetoed or final_validation:
            return "final_validator", "post_ltr_correct_but_final_changed"
        return "postprocess", "post_ltr_correct_but_final_changed"
    return "ranker", "oracle_in_candidates_but_not_top1"


def _detail_from_result(record: dict, result: dict) -> dict:
    quotas = list(result.get("quotas") or [])
    algo_id = str((quotas[0].get("quota_id", "") if quotas else "") or "")
    algo_name = str((quotas[0].get("name", "") if quotas else "") or "")
    all_candidate_ids = [str(value).strip() for value in (result.get("all_candidate_ids") or []) if str(value).strip()]
    oracle_ids = [str(value).strip() for value in (record.get("oracle_quota_ids") or []) if str(value).strip()]
    oracle_alignment = _build_oracle_alignment(record)
    oracle_found = any(value in all_candidate_ids for value in oracle_ids) if oracle_ids else False
    is_match = algo_id in oracle_ids if algo_id and oracle_ids else False
    cause = "" if is_match else _diagnose_cause(record, algo_id, algo_name, quotas)
    reasoning = dict(result.get("reasoning_decision") or {})
    accept_reason = str(reasoning.get("reason") or "")
    accepted = accept_reason == "accept_head_confident"
    search_step = _trace_step(result, "search_select")
    experience_review_rejected_step = _trace_step(result, "experience_review_rejected")
    parser_trace = dict(search_step.get("parser") or {})
    router_trace = dict(search_step.get("router") or {})
    retriever_trace = dict(search_step.get("retriever") or {})
    ranker_trace = dict(search_step.get("ranker") or {})

    if is_match:
        miss_stage = ""
    elif not oracle_found:
        miss_stage = "recall_miss"
    else:
        stage_hits = [
            str(result.get("post_ltr_top1_id", "") or ""),
            str(result.get("post_cgr_top1_id", "") or ""),
            str(result.get("post_arbiter_top1_id", "") or ""),
            str(result.get("post_explicit_top1_id", "") or ""),
            str(result.get("post_anchor_top1_id", "") or ""),
        ]
        post_final_top1_id = str(result.get("post_final_top1_id", algo_id) or algo_id or "")
        miss_stage = (
            "post_rank_miss"
            if any(stage_id and stage_id in oracle_ids for stage_id in stage_hits) and post_final_top1_id not in oracle_ids
            else "rank_miss"
        )

    error_stage, error_type = _diagnose_error_stage(
        result,
        oracle_ids,
        algo_id=algo_id,
        is_match=is_match,
        oracle_found=oracle_found,
    )

    return {
        "sample_id": str(record.get("sample_id") or ""),
        "province": _clean_text(record.get("province")),
        "source": _clean_text(record.get("source")),
        "project_name": _clean_text(record.get("project_name")),
        "bill_name": _clean_text(record.get("bill_name")),
        "bill_text": _clean_text(record.get("bill_text")),
        "section": _clean_text(record.get("section")),
        "sheet_name": _clean_text(record.get("sheet_name")),
        "specialty": _clean_text(record.get("specialty")),
        "oracle_quota_ids": oracle_ids,
        "oracle_quota_names": list(record.get("oracle_quota_names") or []),
        "oracle_status": str(oracle_alignment.get("status") or "ok"),
        "oracle_db_rows": list(oracle_alignment.get("rows") or []),
        "algo_id": algo_id,
        "algo_name": algo_name,
        "is_match": bool(is_match),
        "cause": cause,
        "oracle_in_candidates": bool(oracle_found),
        "all_candidate_ids": all_candidate_ids[:20],
        "candidate_count": int(result.get("candidate_count", result.get("candidates_count", len(all_candidate_ids))) or len(all_candidate_ids)),
        "match_source": str(result.get("match_source", "") or ""),
        "confidence": float(result.get("confidence", 0.0) or 0.0),
        "no_match_reason": str(result.get("no_match_reason", "") or ""),
        "reasoning_decision": reasoning,
        "accepted": accepted,
        "accept_reason": accept_reason,
        "miss_stage": miss_stage,
        "error_stage": error_stage,
        "error_type": error_type,
        "experience_review_rejected": bool(experience_review_rejected_step),
        "experience_review_rejected_type": str(experience_review_rejected_step.get("error_type", "") or ""),
        "experience_review_rejected_reason": str(experience_review_rejected_step.get("error_reason", "") or ""),
        "experience_review_rejected_quota_id": str(experience_review_rejected_step.get("quota_id", "") or ""),
        "experience_review_rejected_source": str(experience_review_rejected_step.get("experience_source", "") or ""),
        "search_query": str(
            parser_trace.get("search_query")
            or parser_trace.get("validation_query")
            or parser_trace.get("route_query")
            or ""
        ),
        "parser": parser_trace,
        "router": router_trace,
        "retriever": retriever_trace,
        "ranker": ranker_trace,
        "candidate_snapshots": list(result.get("candidate_snapshots") or [])[:20],
        "pre_ltr_top1_id": str(result.get("pre_ltr_top1_id", "") or ""),
        "post_ltr_top1_id": str(result.get("post_ltr_top1_id", "") or ""),
        "post_cgr_top1_id": str(result.get("post_cgr_top1_id", "") or ""),
        "post_arbiter_top1_id": str(result.get("post_arbiter_top1_id", "") or ""),
        "post_explicit_top1_id": str(result.get("post_explicit_top1_id", "") or ""),
        "post_anchor_top1_id": str(result.get("post_anchor_top1_id", "") or ""),
        "post_final_top1_id": str(result.get("post_final_top1_id", algo_id) or algo_id or ""),
        "rank_decision_owner": str(result.get("rank_decision_owner", "") or ""),
        "rank_top1_flip_count": int(result.get("rank_top1_flip_count", 0) or 0),
    }


@contextlib.contextmanager
def _runtime_profile(profile: str):
    import config

    settings = PROFILE_DEFAULTS.get(profile, PROFILE_DEFAULTS["full"])
    overrides = {
        "HYBRID_TOP_K": settings.get("hybrid_top_k"),
        "BM25_TOP_K": settings.get("bm25_top_k"),
        "VECTOR_TOP_K": settings.get("vector_top_k"),
        "RERANKER_TOP_K": settings.get("reranker_top_k"),
        "HYBRID_QUERY_VARIANTS": settings.get("hybrid_query_variants"),
        "BM25_SYNONYM_EXPANSION_ENABLED": settings.get("bm25_synonym_expansion"),
    }
    original = {}
    try:
        for name, value in overrides.items():
            original[name] = getattr(config, name)
            if value is not None:
                setattr(config, name, value)
        yield settings
    finally:
        for name, value in original.items():
            setattr(config, name, value)


def summarize_real_eval_details(province: str, details: list[dict], elapsed: float) -> dict:
    total = len(details)
    correct = sum(1 for detail in details if detail.get("is_match"))
    oracle_in = sum(1 for detail in details if detail.get("oracle_in_candidates"))
    accepted = sum(1 for detail in details if detail.get("accepted"))
    accepted_correct = sum(1 for detail in details if detail.get("accepted") and detail.get("is_match"))
    diagnosis = Counter(detail.get("cause", "") for detail in details if detail.get("cause"))
    error_stage_counts = Counter(
        detail.get("error_stage", "")
        for detail in details
        if detail.get("error_stage") and detail.get("error_stage") != "correct"
    )
    error_type_counts = Counter(detail.get("error_type", "") for detail in details if detail.get("error_type"))
    by_source = Counter(detail.get("source", "") for detail in details)
    oracle_status_counts = Counter(detail.get("oracle_status", "ok") or "ok" for detail in details)
    aligned_details = [detail for detail in details if str(detail.get("oracle_status") or "ok") == "ok"]
    aligned_total = len(aligned_details)
    aligned_correct = sum(1 for detail in aligned_details if detail.get("is_match"))
    aligned_oracle_in = sum(1 for detail in aligned_details if detail.get("oracle_in_candidates"))
    aligned_recall_miss = sum(1 for detail in aligned_details if detail.get("miss_stage") == "recall_miss")
    aligned_rank_miss = sum(1 for detail in aligned_details if detail.get("miss_stage") == "rank_miss")
    aligned_post_rank_miss = sum(1 for detail in aligned_details if detail.get("miss_stage") == "post_rank_miss")
    recall_miss = sum(1 for detail in details if detail.get("miss_stage") == "recall_miss")
    rank_miss = sum(1 for detail in details if detail.get("miss_stage") == "rank_miss")
    post_rank_miss = sum(1 for detail in details if detail.get("miss_stage") == "post_rank_miss")
    severe_error = sum(1 for detail in details if detail.get("cause") in {"wrong_book", "no_result"})
    return {
        "province": province,
        "total": total,
        "correct": correct,
        "wrong": total - correct,
        "hit_rate": round(correct / max(total, 1) * 100, 1),
        "oracle_in_candidates": oracle_in,
        "oracle_not_in_candidates": total - correct - sum(1 for detail in details if (not detail.get("is_match")) and detail.get("oracle_in_candidates")),
        "in_pool_top1_acc": round(correct / max(oracle_in, 1), 4) if oracle_in else 0.0,
        "accept_coverage": round(accepted / max(total, 1), 4),
        "accept_precision": round(accepted_correct / max(accepted, 1), 4),
        "accept_count": accepted,
        "accept_correct": accepted_correct,
        "recall_miss_count": recall_miss,
        "rank_miss_count": rank_miss,
        "post_rank_miss_count": post_rank_miss,
        "error_stage_counts": dict(sorted(error_stage_counts.items())),
        "error_type_counts": dict(sorted(error_type_counts.items())),
        "severe_error_count": severe_error,
        "diagnosis": dict(diagnosis),
        "by_source": dict(sorted(by_source.items())),
        "oracle_alignment": dict(sorted(oracle_status_counts.items())),
        "aligned_total": aligned_total,
        "aligned_correct": aligned_correct,
        "aligned_hit_rate": round(aligned_correct / max(aligned_total, 1) * 100, 1) if aligned_total else 0.0,
        "aligned_oracle_in_candidates": aligned_oracle_in,
        "aligned_oracle_not_in_candidates": (
            aligned_total
            - aligned_correct
            - sum(
                1
                for detail in aligned_details
                if (not detail.get("is_match")) and detail.get("oracle_in_candidates")
            )
        ),
        "aligned_in_pool_top1_acc": round(aligned_correct / max(aligned_oracle_in, 1), 4) if aligned_oracle_in else 0.0,
        "aligned_recall_miss_count": aligned_recall_miss,
        "aligned_rank_miss_count": aligned_rank_miss,
        "aligned_post_rank_miss_count": aligned_post_rank_miss,
        "elapsed": round(elapsed, 1),
        "details": details,
    }


def evaluate_province_records(
    province: str,
    records: list[dict],
    *,
    with_experience: bool = False,
) -> dict:
    from src.experience_db import ExperienceDB
    from src.match_engine import init_search_components, match_search_only

    searcher, validator = init_search_components(resolved_province=province)
    experience_db = ExperienceDB(province=province) if with_experience else None
    bill_items = [_bill_item_from_record(record, index) for index, record in enumerate(records, start=1)]
    start = time.time()
    results = match_search_only(
        bill_items,
        searcher,
        validator,
        experience_db=experience_db,
        province=province,
    )
    elapsed = time.time() - start
    details = [_detail_from_result(record, result) for record, result in zip(records, results)]
    return summarize_real_eval_details(province, details, elapsed)


def run_real_eval(
    dataset_path: str | Path,
    *,
    profile: str = "dev",
    with_experience: bool = False,
    province_filters: list[str] | None = None,
    limit: int | None = None,
    max_per_province: int | None = None,
    log_level: str = "WARNING",
    skip_unavailable_provinces: bool = False,
) -> dict:
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("TQDM_DISABLE", "1")
    _configure_logging(log_level)

    records = _read_jsonl(dataset_path)
    if province_filters:
        allowed = set(province_filters)
        records = [record for record in records if _clean_text(record.get("province")) in allowed]
    if limit is not None and int(limit) > 0:
        records = records[: int(limit)]

    settings = PROFILE_DEFAULTS.get(profile, PROFILE_DEFAULTS["dev"])
    if max_per_province is None:
        max_per_province = settings.get("max_per_province")

    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        province = _clean_text(record.get("province"))
        if not province:
            continue
        if max_per_province is not None and int(max_per_province) > 0 and len(grouped[province]) >= int(max_per_province):
            continue
        grouped[province].append(record)

    province_results: list[dict] = []
    skipped_provinces: list[dict] = []
    with _runtime_profile(profile) as runtime_settings:
        for province in sorted(grouped):
            try:
                province_results.append(
                    evaluate_province_records(
                        province,
                        grouped[province],
                        with_experience=with_experience,
                    )
                )
            except Exception as exc:
                if not skip_unavailable_provinces:
                    raise
                skipped_provinces.append({
                    "province": province,
                    "reason": str(exc),
                    "sample_count": len(grouped[province]),
                })

    total = sum(result["total"] for result in province_results)
    correct = sum(result["correct"] for result in province_results)
    wrong = total - correct
    oracle_in = sum(result["oracle_in_candidates"] for result in province_results)
    oracle_not_in = sum(result["oracle_not_in_candidates"] for result in province_results)
    accept_count = sum(result["accept_count"] for result in province_results)
    accept_correct = sum(result["accept_correct"] for result in province_results)
    severe_error_count = sum(result["severe_error_count"] for result in province_results)
    diagnosis = Counter()
    by_source = Counter()
    oracle_alignment = Counter()
    aligned_total = sum(int(result.get("aligned_total", 0) or 0) for result in province_results)
    aligned_correct = sum(int(result.get("aligned_correct", 0) or 0) for result in province_results)
    aligned_oracle_in = sum(int(result.get("aligned_oracle_in_candidates", 0) or 0) for result in province_results)
    aligned_oracle_not_in = sum(int(result.get("aligned_oracle_not_in_candidates", 0) or 0) for result in province_results)
    aligned_recall_miss = sum(int(result.get("aligned_recall_miss_count", 0) or 0) for result in province_results)
    aligned_rank_miss = sum(int(result.get("aligned_rank_miss_count", 0) or 0) for result in province_results)
    aligned_post_rank_miss = sum(int(result.get("aligned_post_rank_miss_count", 0) or 0) for result in province_results)
    for result in province_results:
        diagnosis.update(result.get("diagnosis") or {})
        by_source.update(result.get("by_source") or {})
        oracle_alignment.update(result.get("oracle_alignment") or {})

    return {
        "dataset_path": str(Path(dataset_path)),
        "profile": profile,
        "eval_mode": "with_memory" if with_experience else "closed_book",
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "hit_rate": round(correct / max(total, 1) * 100, 1),
        "oracle_in_candidates": oracle_in,
        "oracle_not_in_candidates": oracle_not_in,
        "accept_count": accept_count,
        "accept_coverage": round(accept_count / max(total, 1), 4),
        "accept_precision": round(accept_correct / max(accept_count, 1), 4),
        "severe_error_count": severe_error_count,
        "diagnosis": dict(diagnosis),
        "by_source": dict(sorted(by_source.items())),
        "oracle_alignment": dict(sorted(oracle_alignment.items())),
        "aligned_total": aligned_total,
        "aligned_correct": aligned_correct,
        "aligned_hit_rate": round(aligned_correct / max(aligned_total, 1) * 100, 1) if aligned_total else 0.0,
        "aligned_oracle_in_candidates": aligned_oracle_in,
        "aligned_oracle_not_in_candidates": aligned_oracle_not_in,
        "aligned_in_pool_top1_acc": round(aligned_correct / max(aligned_oracle_in, 1), 4) if aligned_oracle_in else 0.0,
        "aligned_recall_miss_count": aligned_recall_miss,
        "aligned_rank_miss_count": aligned_rank_miss,
        "aligned_post_rank_miss_count": aligned_post_rank_miss,
        "runtime_settings": {
            "max_per_province": max_per_province,
            "hybrid_top_k": settings.get("hybrid_top_k"),
            "bm25_top_k": settings.get("bm25_top_k"),
            "vector_top_k": settings.get("vector_top_k"),
            "reranker_top_k": settings.get("reranker_top_k"),
            "hybrid_query_variants": settings.get("hybrid_query_variants"),
            "bm25_synonym_expansion": settings.get("bm25_synonym_expansion"),
        },
        "skipped_provinces": skipped_provinces,
        "province_results": province_results,
    }


def _strip_details(payload: dict) -> dict:
    stripped = dict(payload)
    stripped["province_results"] = [
        {key: value for key, value in result.items() if key != "details"}
        for result in payload.get("province_results", [])
    ]
    return stripped


def _build_keyword_miss_export_rows(
    payload: dict,
    *,
    cause: str = "synonym_gap",
    miss_stage: str = "recall_miss",
) -> list[dict]:
    def _compact_router(detail: dict) -> dict:
        router = dict(detail.get("router") or {})
        classification = dict(router.get("classification") or {})
        unified_plan = dict(router.get("unified_plan") or {})
        return {
            "primary_book": str(
                classification.get("primary")
                or unified_plan.get("primary_book")
                or router.get("primary_book")
                or ""
            ),
            "search_books": list(classification.get("search_books") or router.get("search_books") or []),
            "hard_search_books": list(classification.get("hard_search_books") or []),
            "advisory_search_books": list(classification.get("advisory_search_books") or []),
            "route_mode": str(
                classification.get("route_mode")
                or unified_plan.get("route_mode")
                or router.get("route_mode")
                or ""
            ),
            "advisory_owner": str(router.get("advisory_owner") or ""),
            "effective_owner": str(router.get("effective_owner") or ""),
            "allow_cross_book_escape": bool(
                classification.get("allow_cross_book_escape")
                if "allow_cross_book_escape" in classification
                else router.get("allow_cross_book_escape", True)
            ),
        }

    def _compact_retriever(detail: dict) -> dict:
        retriever = dict(detail.get("retriever") or {})
        return {
            "candidate_count": int(retriever.get("candidate_count", detail.get("candidate_count", 0)) or 0),
            "matched_candidate_count": int(retriever.get("matched_candidate_count", 0) or 0),
            "candidate_ids": list(retriever.get("candidate_ids") or [])[:20],
            "authority_hit": bool(retriever.get("authority_hit")),
            "kb_hit": bool(retriever.get("kb_hit")),
            "scope_owner": str(retriever.get("scope_owner") or ""),
            "escape_owner": str(retriever.get("escape_owner") or ""),
            "used_open_search": bool(retriever.get("used_open_search")),
            "resolved_main_books": list(retriever.get("resolved_main_books") or []),
        }

    def _compact_ranker(detail: dict) -> dict:
        ranker = dict(detail.get("ranker") or {})
        return {
            "selected_quota": str(ranker.get("selected_quota") or detail.get("algo_id") or ""),
            "score_gap": ranker.get("score_gap"),
            "decision_owner": str(ranker.get("decision_owner") or detail.get("rank_decision_owner") or ""),
            "top1_flip_count": int(ranker.get("top1_flip_count", detail.get("rank_top1_flip_count", 0)) or 0),
        }

    rows: list[dict] = []
    for result in payload.get("province_results", []) or []:
        for detail in result.get("details", []) or []:
            if cause and str(detail.get("cause") or "") != cause:
                continue
            if miss_stage and str(detail.get("miss_stage") or "") != miss_stage:
                continue
            rows.append(
                {
                    "sample_id": str(detail.get("sample_id") or ""),
                    "province": _clean_text(detail.get("province")),
                    "source": _clean_text(detail.get("source")),
                    "project_name": _clean_text(detail.get("project_name")),
                    "bill_name": _clean_text(detail.get("bill_name")),
                    "bill_text": _clean_text(detail.get("bill_text")),
                    "specialty": _clean_text(detail.get("specialty")),
                    "oracle_quota_ids": list(detail.get("oracle_quota_ids") or []),
                    "oracle_quota_names": list(detail.get("oracle_quota_names") or []),
                    "search_query": str(detail.get("search_query") or ""),
                    "parser": dict(detail.get("parser") or {}),
                    "router": _compact_router(detail),
                    "retriever": _compact_retriever(detail),
                    "ranker": _compact_ranker(detail),
                    "candidate_count": int(detail.get("candidate_count", 0) or 0),
                    "cause": str(detail.get("cause") or ""),
                    "miss_stage": str(detail.get("miss_stage") or ""),
                    "error_stage": str(detail.get("error_stage") or ""),
                    "error_type": str(detail.get("error_type") or ""),
                    "algo_id": str(detail.get("algo_id") or ""),
                    "algo_name": str(detail.get("algo_name") or ""),
                    "match_source": str(detail.get("match_source") or ""),
                    "confidence": float(detail.get("confidence", 0.0) or 0.0),
                }
            )
    return rows


def _build_mode_comparison(closed_payload: dict, with_memory_payload: dict) -> dict:
    closed = _strip_details(closed_payload)
    memory = _strip_details(with_memory_payload)
    closed_by_province = {
        str(item.get("province") or ""): item
        for item in closed.get("province_results", []) or []
        if str(item.get("province") or "")
    }
    memory_by_province = {
        str(item.get("province") or ""): item
        for item in memory.get("province_results", []) or []
        if str(item.get("province") or "")
    }
    province_names = sorted(set(closed_by_province) | set(memory_by_province))
    province_deltas = []
    for province in province_names:
        closed_item = closed_by_province.get(province, {})
        memory_item = memory_by_province.get(province, {})
        province_deltas.append({
            "province": province,
            "closed_book_total": int(closed_item.get("total", 0) or 0),
            "with_memory_total": int(memory_item.get("total", 0) or 0),
            "closed_book_hit_rate": float(closed_item.get("hit_rate", 0.0) or 0.0),
            "with_memory_hit_rate": float(memory_item.get("hit_rate", 0.0) or 0.0),
            "hit_rate_gain": round(
                float(memory_item.get("hit_rate", 0.0) or 0.0)
                - float(closed_item.get("hit_rate", 0.0) or 0.0),
                1,
            ),
            "closed_book_correct": int(closed_item.get("correct", 0) or 0),
            "with_memory_correct": int(memory_item.get("correct", 0) or 0),
            "correct_gain": int(memory_item.get("correct", 0) or 0) - int(closed_item.get("correct", 0) or 0),
        })

    return {
        "comparison_mode": "closed_book_vs_with_memory",
        "profile": str(closed.get("profile") or memory.get("profile") or ""),
        "dataset_path": str(closed.get("dataset_path") or memory.get("dataset_path") or ""),
        "closed_book": closed,
        "with_memory": memory,
        "delta": {
            "total": int(memory.get("total", 0) or 0),
            "closed_book_hit_rate": float(closed.get("hit_rate", 0.0) or 0.0),
            "with_memory_hit_rate": float(memory.get("hit_rate", 0.0) or 0.0),
            "hit_rate_gain": round(
                float(memory.get("hit_rate", 0.0) or 0.0)
                - float(closed.get("hit_rate", 0.0) or 0.0),
                1,
            ),
            "closed_book_correct": int(closed.get("correct", 0) or 0),
            "with_memory_correct": int(memory.get("correct", 0) or 0),
            "correct_gain": int(memory.get("correct", 0) or 0) - int(closed.get("correct", 0) or 0),
        },
        "province_deltas": province_deltas,
    }


def _print_summary(payload: dict) -> None:
    print(
        f"[REAL-EVAL] profile={payload['profile']} mode={payload['eval_mode']} total={payload['total']} "
        f"hit_rate={payload['hit_rate']}% aligned_hit_rate={payload.get('aligned_hit_rate', 0.0)}% "
        f"accept_cov={payload['accept_coverage']:.4f} accept_prec={payload['accept_precision']:.4f}"
    )
    if payload.get("oracle_alignment"):
        print(f"  oracle_alignment={payload['oracle_alignment']}")
    if payload.get("skipped_provinces"):
        skipped = ", ".join(
            f"{item['province']}({item['sample_count']})"
            for item in payload.get("skipped_provinces", [])
        )
        print(f"  skipped: {skipped}")
    for result in payload.get("province_results", []):
        print(
            f"  - {result['province']}: total={result['total']} hit={result['hit_rate']}% "
            f"aligned_hit={result.get('aligned_hit_rate', 0.0)}% "
            f"oracle_in={result['oracle_in_candidates']} severe={result['severe_error_count']} "
            f"oracle_alignment={result.get('oracle_alignment', {})}"
        )


def _print_comparison_summary(payload: dict) -> None:
    delta = dict(payload.get("delta") or {})
    print(
        f"[REAL-EVAL-COMPARE] profile={payload.get('profile', '')} total={delta.get('total', 0)} "
        f"closed_book={delta.get('closed_book_hit_rate', 0.0)}% "
        f"with_memory={delta.get('with_memory_hit_rate', 0.0)}% "
        f"gain={delta.get('hit_rate_gain', 0.0)}"
    )
    for item in payload.get("province_deltas", []) or []:
        print(
            f"  - {item['province']}: closed_book={item['closed_book_hit_rate']}% "
            f"with_memory={item['with_memory_hit_rate']}% gain={item['hit_rate_gain']}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real-world evaluation on exported experience samples")
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET_PATH), help="real eval jsonl path")
    parser.add_argument("--summary-out", default=str(DEFAULT_SUMMARY_PATH), help="summary json path")
    parser.add_argument("--details-out", default="", help="optional details jsonl path")
    parser.add_argument("--profile", choices=sorted(PROFILE_DEFAULTS), default="dev", help="runtime preset for daily eval")
    parser.add_argument("--province", action="append", dest="provinces", help="province filter, repeatable")
    parser.add_argument("--limit", type=int, default=None, help="global record limit")
    parser.add_argument("--max-per-province", type=int, default=None, help="cap per province")
    parser.add_argument("--with-experience", action="store_true", help="enable experience DB during eval")
    parser.add_argument("--compare-modes", action="store_true", help="run both closed_book and with_memory, emit a comparison summary")
    parser.add_argument(
        "--keyword-miss-export-out",
        default="",
        help="optional filtered jsonl export for closed-book synonym_gap recall misses",
    )
    parser.add_argument("--log-level", default="WARNING", help="loguru log level")
    parser.add_argument("--skip-unavailable-provinces", action="store_true", help="skip provinces whose local index is unavailable")
    args = parser.parse_args()

    if args.compare_modes:
        closed_payload = run_real_eval(
            args.dataset,
            profile=args.profile,
            with_experience=False,
            province_filters=list(args.provinces or []),
            limit=args.limit,
            max_per_province=args.max_per_province,
            log_level=args.log_level,
            skip_unavailable_provinces=args.skip_unavailable_provinces,
        )
        with_memory_payload = run_real_eval(
            args.dataset,
            profile=args.profile,
            with_experience=True,
            province_filters=list(args.provinces or []),
            limit=args.limit,
            max_per_province=args.max_per_province,
            log_level=args.log_level,
            skip_unavailable_provinces=args.skip_unavailable_provinces,
        )
        payload = _build_mode_comparison(closed_payload, with_memory_payload)
    else:
        payload = run_real_eval(
            args.dataset,
            profile=args.profile,
            with_experience=args.with_experience,
            province_filters=list(args.provinces or []),
            limit=args.limit,
            max_per_province=args.max_per_province,
            log_level=args.log_level,
            skip_unavailable_provinces=args.skip_unavailable_provinces,
        )

    summary_path = Path(args.summary_out)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_body = payload if args.compare_modes else _strip_details(payload)
    summary_path.write_text(json.dumps(summary_body, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.details_out and not args.compare_modes:
        details_path = Path(args.details_out)
        details_path.parent.mkdir(parents=True, exist_ok=True)
        with details_path.open("w", encoding="utf-8") as handle:
            for result in payload.get("province_results", []):
                for detail in result.get("details", []):
                    handle.write(json.dumps(detail, ensure_ascii=False) + "\n")

    if args.keyword_miss_export_out and not args.compare_modes:
        export_rows = _build_keyword_miss_export_rows(payload)
        export_path = Path(args.keyword_miss_export_out)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with export_path.open("w", encoding="utf-8") as handle:
            for row in export_rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    if args.compare_modes:
        _print_comparison_summary(payload)
    else:
        _print_summary(payload)
    print(f"[OK] summary saved to: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
