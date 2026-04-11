from __future__ import annotations

from pathlib import Path
from typing import Any

from src.knowledge_staging import KnowledgeStaging
from src.source_pack import (
    SOURCE_PACKS_DIR,
    list_source_pack_files,
    load_source_pack,
    normalize_source_pack_metadata,
    safe_text,
)
from tools.extract_source_to_staging import process_source_pack


class SourceLearningService:
    def __init__(self, packs_dir: str | Path | None = None):
        self.packs_dir = Path(packs_dir) if packs_dir else SOURCE_PACKS_DIR

    def _iter_packs(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in list_source_pack_files(self.packs_dir):
            try:
                items.append(normalize_source_pack_metadata(load_source_pack(path)))
            except Exception:
                continue
        return items

    @staticmethod
    def _summary(pack: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_source_pack_metadata(pack)
        return {
            "source_id": safe_text(normalized.get("source_id")),
            "title": safe_text(normalized.get("title")),
            "summary": safe_text(normalized.get("summary")),
            "source_kind": safe_text(normalized.get("source_kind")),
            "province": safe_text(normalized.get("province")),
            "specialty": safe_text(normalized.get("specialty")),
            "created_at": safe_text(normalized.get("created_at")),
            "confidence": int(normalized.get("confidence") or 0),
            "full_text_path": safe_text(normalized.get("full_text_path")),
            "evidence_refs": list(normalized.get("evidence_refs") or []),
            "tags": list(normalized.get("tags") or []),
        }

    def list_source_packs(
        self,
        *,
        q: str = "",
        source_kind: str = "",
        province: str = "",
        specialty: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        query = safe_text(q).lower()
        kind_filter = safe_text(source_kind).lower()
        province_filter = safe_text(province).lower()
        specialty_filter = safe_text(specialty).lower()

        items: list[dict[str, Any]] = []
        for pack in self._iter_packs():
            summary = self._summary(pack)
            haystack = "\n".join(
                [
                    summary["source_id"],
                    summary["title"],
                    summary["summary"],
                    summary["province"],
                    summary["specialty"],
                    " ".join(summary["tags"]),
                ]
            ).lower()
            if query and query not in haystack:
                continue
            if kind_filter and summary["source_kind"].lower() != kind_filter:
                continue
            if province_filter and province_filter not in summary["province"].lower():
                continue
            if specialty_filter and specialty_filter not in summary["specialty"].lower():
                continue
            items.append(summary)
            if limit > 0 and len(items) >= limit:
                break

        return {"items": items, "total": len(items)}

    def get_source_pack(self, source_id: str) -> dict[str, Any]:
        target = safe_text(source_id)
        if not target:
            raise ValueError("source_id is required")
        path = self.packs_dir / f"{target}.json"
        if path.exists():
            return normalize_source_pack_metadata(load_source_pack(path))
        for pack in self._iter_packs():
            if safe_text(pack.get("source_id")) == target:
                return pack
        raise ValueError(f"source pack not found: {target}")

    def get_source_pack_summary(self, source_id: str) -> dict[str, Any]:
        return self._summary(self.get_source_pack(source_id))

    def extract_source_pack(
        self,
        source_id: str,
        *,
        dry_run: bool = False,
        llm_type: str | None = None,
        chunk_size: int = 1800,
        overlap: int = 240,
        max_chunks: int = 24,
    ) -> dict[str, Any]:
        pack = self.get_source_pack(source_id)
        staging = None if dry_run else KnowledgeStaging()
        summary = process_source_pack(
            pack,
            staging=staging,
            llm_type=llm_type,
            chunk_size=chunk_size,
            overlap=overlap,
            max_chunks=max_chunks,
            dry_run=dry_run,
        )
        summary["pack"] = self._summary(pack)
        return summary
