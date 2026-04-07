from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.knowledge_promotion import KnowledgePromotionService  # noqa: E402
from src.knowledge_staging import KnowledgeStaging  # noqa: E402
from src.qmd_index import QMDIndex  # noqa: E402
from tools.export_staging_to_wiki import export_staging_to_wiki  # noqa: E402


DEFAULT_WIKI_ROOT = PROJECT_ROOT / "knowledge_wiki"


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _execute_one(service: KnowledgePromotionService, promotion: dict[str, Any]) -> dict[str, Any]:
    target_layer = _safe_text(promotion.get("target_layer"))
    promotion_id = int(promotion["id"])
    if target_layer == "RuleKnowledge":
        return service.promote_rule_candidate(promotion_id)
    if target_layer == "MethodCards":
        return service.promote_method_candidate(promotion_id)
    if target_layer == "ExperienceDB":
        return service.promote_experience_candidate(promotion_id)
    raise ValueError(f"unsupported target layer: {target_layer}")


def run_import_wiki_promotions(
    *,
    staging: KnowledgeStaging | None = None,
    service: KnowledgePromotionService | None = None,
    candidate_types: list[str] | None = None,
    target_layers: list[str] | None = None,
    limit: int = 100,
    dry_run: bool = False,
    refresh_wiki: bool = False,
    build_qmd: bool = False,
    wiki_root: Path = DEFAULT_WIKI_ROOT,
) -> dict[str, Any]:
    staging = staging or KnowledgeStaging()
    service = service or KnowledgePromotionService(staging=staging)
    items = staging.list_promotions(
        statuses=["approved"],
        candidate_types=candidate_types or None,
        target_layers=target_layers or None,
        limit=max(1, int(limit)),
    )

    summary = {
        "processed": 0,
        "executed": 0,
        "skipped": 0,
        "errors": 0,
        "items": [],
    }

    for promotion in items:
        summary["processed"] += 1
        record = {
            "id": int(promotion["id"]),
            "candidate_type": _safe_text(promotion.get("candidate_type")),
            "target_layer": _safe_text(promotion.get("target_layer")),
            "candidate_title": _safe_text(promotion.get("candidate_title")),
            "status": "pending",
        }
        try:
            if _safe_text(promotion.get("review_status")) != "approved":
                record["status"] = "skipped"
                record["reason"] = "review_status_not_approved"
                summary["skipped"] += 1
                summary["items"].append(record)
                continue

            if dry_run:
                record["status"] = "would_execute"
                summary["items"].append(record)
                continue

            result = _execute_one(service, promotion)
            record["status"] = "executed"
            record["result"] = result
            summary["executed"] += 1
            summary["items"].append(record)
        except Exception as exc:
            record["status"] = "error"
            record["error"] = str(exc)
            summary["errors"] += 1
            summary["items"].append(record)

    if refresh_wiki and not dry_run:
        summary["wiki_manifest"] = export_staging_to_wiki(output_dir=wiki_root)
    if build_qmd and not dry_run:
        summary["qmd_manifest"] = QMDIndex(wiki_root=wiki_root).rebuild_index()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Execute approved wiki-visible promotions into formal knowledge layers.")
    parser.add_argument("--candidate-types", default="", help="comma-separated: rule,method,experience")
    parser.add_argument("--target-layers", default="", help="comma-separated: RuleKnowledge,MethodCards,ExperienceDB")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--refresh-wiki", action="store_true")
    parser.add_argument("--build-qmd", action="store_true")
    parser.add_argument("--wiki-root", default=str(DEFAULT_WIKI_ROOT))
    args = parser.parse_args()

    result = run_import_wiki_promotions(
        candidate_types=[item.strip() for item in args.candidate_types.split(",") if item.strip()],
        target_layers=[item.strip() for item in args.target_layers.split(",") if item.strip()],
        limit=args.limit,
        dry_run=args.dry_run,
        refresh_wiki=args.refresh_wiki,
        build_qmd=args.build_qmd,
        wiki_root=Path(args.wiki_root),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["errors"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
