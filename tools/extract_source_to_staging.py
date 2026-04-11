from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from src.knowledge_staging import KnowledgeStaging
from src.source_learning import (
    build_learning_chunks,
    build_source_learning_prompt,
    call_source_learning_llm,
    merge_source_learning_candidates,
    normalize_source_learning_candidate,
    parse_source_learning_response,
)
from src.source_pack import list_source_pack_files, load_source_pack, normalize_source_pack_metadata, safe_text


def _load_fixture_responses(path: str | Path) -> dict[str, str]:
    text = Path(path).read_text(encoding="utf-8-sig")
    try:
        payload: Any = json.loads(text)
    except json.JSONDecodeError:
        return {"*": text}

    if isinstance(payload, dict) and isinstance(payload.get("responses"), dict):
        result: dict[str, str] = {}
        for chunk_id, value in payload["responses"].items():
            result[str(chunk_id)] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return result

    if isinstance(payload, dict) and "candidates" in payload:
        return {"*": json.dumps(payload, ensure_ascii=False)}

    if isinstance(payload, dict):
        result = {}
        for key, value in payload.items():
            result[str(key)] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        if result:
            return result

    return {"*": json.dumps(payload, ensure_ascii=False)}


def _select_pack_files(*, packs_dir: str | Path | None, source_ids: list[str], limit: int) -> list[Path]:
    files = list_source_pack_files(packs_dir)
    if source_ids:
        allowed = {item.strip() for item in source_ids if item.strip()}
        files = [path for path in files if path.stem in allowed]
    if limit > 0:
        files = files[:limit]
    return files


def _resolve_response(
    *,
    fixture_responses: dict[str, str] | None,
    chunk_id: str,
    prompt: str,
    llm_type: str | None,
) -> str:
    if fixture_responses is not None:
        return fixture_responses.get(chunk_id) or fixture_responses.get("*") or ""
    return call_source_learning_llm(prompt, llm_type=llm_type)


def process_source_pack(
    pack: dict[str, Any],
    *,
    staging: KnowledgeStaging | None = None,
    fixture_responses: dict[str, str] | None = None,
    llm_type: str | None = None,
    chunk_size: int = 1800,
    overlap: int = 240,
    max_chunks: int = 24,
    dry_run: bool = False,
    print_prompts: bool = False,
) -> dict[str, Any]:
    normalized_pack = normalize_source_pack_metadata(pack)
    chunks = build_learning_chunks(
        normalized_pack,
        chunk_size=chunk_size,
        overlap=overlap,
        max_chunks=max_chunks,
    )
    if not chunks:
        return {
            "source_id": safe_text(normalized_pack.get("source_id")),
            "title": safe_text(normalized_pack.get("title")),
            "chunks": 0,
            "raw_candidates": 0,
            "merged_candidates": 0,
            "staged": 0,
        }

    normalized_candidates: list[dict[str, Any]] = []
    raw_candidate_count = 0

    for chunk in chunks:
        prompt = build_source_learning_prompt(normalized_pack, chunk)
        if print_prompts:
            logger.info("prompt chunk={} heading={}", chunk.chunk_id, chunk.heading)
            print(prompt)
        response_text = _resolve_response(
            fixture_responses=fixture_responses,
            chunk_id=chunk.chunk_id,
            prompt=prompt,
            llm_type=llm_type,
        )
        if not response_text:
            continue
        parsed_candidates = parse_source_learning_response(response_text)
        raw_candidate_count += len(parsed_candidates)
        for candidate in parsed_candidates:
            normalized = normalize_source_learning_candidate(
                candidate,
                pack=normalized_pack,
                chunk=chunk,
            )
            if normalized is not None:
                normalized_candidates.append(normalized)

    merged_candidates = merge_source_learning_candidates(normalized_candidates)

    staged_ids: list[int] = []
    if not dry_run and staging is not None:
        for candidate in merged_candidates:
            staged_ids.append(staging.enqueue_promotion(candidate))

    return {
        "source_id": safe_text(normalized_pack.get("source_id")),
        "title": safe_text(normalized_pack.get("title")),
        "chunks": len(chunks),
        "raw_candidates": raw_candidate_count,
        "merged_candidates": len(merged_candidates),
        "staged": len(staged_ids),
        "staged_ids": staged_ids,
        "candidates": merged_candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract reviewable rule/method/experience candidates from source packs into knowledge staging.",
    )
    parser.add_argument("--packs-dir", default=None, help="Override source_packs/packs directory.")
    parser.add_argument("--source-id", action="append", default=[], help="Specific source_id to process. Repeatable.")
    parser.add_argument("--limit", type=int, default=1, help="How many source packs to process. 0 means all.")
    parser.add_argument("--chunk-size", type=int, default=1800, help="Chunk size in characters.")
    parser.add_argument("--overlap", type=int, default=240, help="Chunk overlap in characters.")
    parser.add_argument("--max-chunks", type=int, default=24, help="Max chunks per source pack.")
    parser.add_argument("--llm", default=None, help="Override llm type, e.g. deepseek/qwen/kimi/openai/claude.")
    parser.add_argument("--fixture-response", default=None, help="Use fixture response instead of calling LLM.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and normalize without writing to staging.")
    parser.add_argument("--print-prompts", action="store_true", help="Print prompts for inspection.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable summary JSON.")
    args = parser.parse_args()

    fixture_responses = _load_fixture_responses(args.fixture_response) if args.fixture_response else None
    pack_files = _select_pack_files(
        packs_dir=args.packs_dir,
        source_ids=args.source_id,
        limit=args.limit,
    )

    if not pack_files:
        logger.error("No source packs found for the given selection.")
        sys.exit(1)

    staging = None if args.dry_run else KnowledgeStaging()
    summaries: list[dict[str, Any]] = []

    for pack_file in pack_files:
        pack = load_source_pack(pack_file)
        summary = process_source_pack(
            pack,
            staging=staging,
            fixture_responses=fixture_responses,
            llm_type=args.llm,
            chunk_size=args.chunk_size,
            overlap=args.overlap,
            max_chunks=args.max_chunks,
            dry_run=args.dry_run,
            print_prompts=args.print_prompts,
        )
        summaries.append(summary)
        logger.info(
            "source_id={} chunks={} raw={} merged={} staged={}",
            summary["source_id"],
            summary["chunks"],
            summary["raw_candidates"],
            summary["merged_candidates"],
            summary["staged"],
        )

    total = {
        "processed": len(summaries),
        "chunks": sum(int(item["chunks"]) for item in summaries),
        "raw_candidates": sum(int(item["raw_candidates"]) for item in summaries),
        "merged_candidates": sum(int(item["merged_candidates"]) for item in summaries),
        "staged": sum(int(item["staged"]) for item in summaries),
    }

    if args.json:
        print(json.dumps({"summary": total, "items": summaries}, ensure_ascii=False, indent=2))
    else:
        print(
            "source_learning "
            f"processed={total['processed']} "
            f"chunks={total['chunks']} "
            f"raw_candidates={total['raw_candidates']} "
            f"merged_candidates={total['merged_candidates']} "
            f"staged={total['staged']}"
        )


if __name__ == "__main__":
    main()
