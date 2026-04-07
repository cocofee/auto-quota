from __future__ import annotations

import json
import os
import re
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.model_profile import (
    VectorModelProfile,
    encode_documents,
    encode_queries,
    get_active_profile,
)


QMD_COLLECTION_NAME = "qmd_docs"
INDEXABLE_DIRS = ("sources", "rules", "cases", "methods", "reviews")
FRONTMATTER_DELIMITER = "---"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
_VECTOR_MODEL = None
_CHROMA_CLIENTS: dict[str, Any] = {}
_CACHE_LOCK = threading.Lock()


def get_qmd_profile() -> VectorModelProfile:
    explicit_key = os.getenv("VECTOR_MODEL_KEY")
    if explicit_key:
        return get_active_profile()

    bundled_qwen3 = PROJECT_ROOT / "models" / "qwen3-embedding-quota-v3"
    if bundled_qwen3.exists():
        return VectorModelProfile(
            key="qwen3",
            model_name=str(bundled_qwen3),
            embedding_dim=1024,
            query_prefix="",
            load_kwargs={"model_kwargs": {"torch_dtype": "bfloat16"}},
            cpu_load_kwargs={},
        )
    return get_active_profile()


def get_vector_model():
    global _VECTOR_MODEL
    if _VECTOR_MODEL is not None:
        return _VECTOR_MODEL

    profile = get_qmd_profile()
    with _CACHE_LOCK:
        if _VECTOR_MODEL is not None:
            return _VECTOR_MODEL
        from sentence_transformers import SentenceTransformer

        try:
            _VECTOR_MODEL = SentenceTransformer(
                profile.model_name,
                device="cuda",
                **profile.load_kwargs,
            )
        except Exception as gpu_error:
            logger.warning("QMD vector model GPU load failed: {}", gpu_error)
            _VECTOR_MODEL = SentenceTransformer(
                profile.model_name,
                device="cpu",
                **profile.cpu_load_kwargs,
            )
    return _VECTOR_MODEL


def get_chroma_client(path: str):
    path_str = str(path)
    if path_str in _CHROMA_CLIENTS:
        return _CHROMA_CLIENTS[path_str]

    with _CACHE_LOCK:
        if path_str in _CHROMA_CLIENTS:
            return _CHROMA_CLIENTS[path_str]
        import chromadb

        Path(path_str).mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=path_str)
        _CHROMA_CLIENTS[path_str] = client
        return client


def get_qmd_chroma_dir(project_root: Path | None = None) -> Path:
    root = Path(project_root or PROJECT_ROOT)
    profile = get_qmd_profile()
    return root / "db" / "chroma" / profile.key / "common_qmd"


@dataclass(slots=True)
class QMDChunk:
    chunk_id: str
    page_path: Path
    relative_path: str
    category: str
    title: str
    heading: str
    body: str
    search_text: str
    metadata: dict[str, Any]


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _decode_scalar(value: str) -> Any:
    raw = value.strip()
    if raw == "":
        return ""
    if raw.startswith('"') and raw.endswith('"'):
        return json.loads(raw)
    if raw in {"[]", "[ ]"}:
        return []
    if raw.isdigit():
        return int(raw)
    return raw


def parse_frontmatter(markdown_text: str) -> tuple[dict[str, Any], str]:
    text = markdown_text.lstrip("\ufeff")
    if not text.startswith(f"{FRONTMATTER_DELIMITER}\n"):
        return {}, markdown_text

    end_marker = f"\n{FRONTMATTER_DELIMITER}\n"
    end_index = text.find(end_marker, len(FRONTMATTER_DELIMITER) + 1)
    if end_index < 0:
        return {}, markdown_text

    raw_meta = text[len(FRONTMATTER_DELIMITER) + 1:end_index]
    body = text[end_index + len(end_marker):]
    metadata: dict[str, Any] = {}
    current_list_key: str | None = None
    current_list: list[Any] | None = None

    for line in raw_meta.splitlines():
        if not line.strip():
            continue
        if line.startswith("  - ") and current_list_key and current_list is not None:
            current_list.append(_decode_scalar(line[4:]))
            continue
        if ":" not in line:
            current_list_key = None
            current_list = None
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        value = raw_value.strip()
        if value == "":
            current_list_key = key
            current_list = []
            metadata[key] = current_list
            continue
        metadata[key] = _decode_scalar(value)
        current_list_key = None
        current_list = None

    return metadata, body


def _split_markdown_sections(body: str, fallback_title: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    current_heading = fallback_title
    current_lines: list[str] = []

    def flush() -> None:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append((current_heading, text))

    for line in body.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            flush()
            level = len(match.group(1))
            heading = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            current_heading = " / ".join(item[1] for item in stack if item[1])
            current_lines = []
            continue
        current_lines.append(line)

    flush()
    if not sections and body.strip():
        return [(fallback_title, body.strip())]
    return sections


def _slice_text_block(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    parts: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + max_chars, len(cleaned))
        parts.append(cleaned[start:end].strip())
        if end >= len(cleaned):
            break
        start = max(end - overlap_chars, start + 1)
    return [part for part in parts if part]


def _normalize_chroma_metadata(
    *,
    metadata: dict[str, Any],
    category: str,
    title: str,
    heading: str,
    relative_path: str,
    preview: str,
    tags: list[str],
    related: list[str],
    source_refs: list[str],
    chunk_index: int,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": category,
        "type": _safe_text(metadata.get("type")) or category,
        "title": title,
        "heading": heading,
        "path": relative_path.replace("\\", "/"),
        "status": _safe_text(metadata.get("status")),
        "province": _safe_text(metadata.get("province")),
        "specialty": _safe_text(metadata.get("specialty")),
        "source_kind": _safe_text(metadata.get("source_kind")),
        "owner": _safe_text(metadata.get("owner")),
        "created_at": _safe_text(metadata.get("created_at")),
        "updated_at": _safe_text(metadata.get("updated_at")),
        "preview": preview,
        "tags_text": " | ".join(tags),
        "related_text": " | ".join(related),
        "source_refs_text": " | ".join(source_refs),
        "chunk_index": int(chunk_index),
        "confidence": int(metadata.get("confidence", 0) or 0),
    }
    return {key: value for key, value in payload.items() if value not in {"", None}}


def _build_chunk(
    *,
    chunk_index: int,
    page_path: Path,
    relative_path: str,
    category: str,
    title: str,
    heading: str,
    body: str,
    metadata: dict[str, Any],
    tags: list[str],
    related: list[str],
    source_refs: list[str],
) -> QMDChunk:
    chunk_id = f"{category}::{page_path.stem}::chunk-{chunk_index:04d}"
    preview = body.replace("\n", " ").strip()
    search_text = "\n".join(
        part
        for part in [
            f"title: {title}",
            f"type: {_safe_text(metadata.get('type')) or category}",
            f"category: {category}",
            f"heading: {heading}",
            f"province: {_safe_text(metadata.get('province'))}",
            f"specialty: {_safe_text(metadata.get('specialty'))}",
            f"tags: {' | '.join(tags)}",
            f"source_kind: {_safe_text(metadata.get('source_kind'))}",
            body.strip(),
        ]
        if _safe_text(part)
    )
    normalized_metadata = _normalize_chroma_metadata(
        metadata=metadata,
        category=category,
        title=title,
        heading=heading,
        relative_path=relative_path,
        preview=preview[:240],
        tags=tags,
        related=related,
        source_refs=source_refs,
        chunk_index=chunk_index,
    )
    return QMDChunk(
        chunk_id=chunk_id,
        page_path=page_path,
        relative_path=relative_path,
        category=category,
        title=title,
        heading=heading,
        body=body.strip(),
        search_text=search_text.strip(),
        metadata=normalized_metadata,
    )


def chunk_markdown_page(
    page_path: Path,
    relative_path: str,
    *,
    max_chars: int = 900,
    overlap_chars: int = 120,
) -> list[QMDChunk]:
    metadata, body = parse_frontmatter(page_path.read_text(encoding="utf-8"))
    title = _safe_text(metadata.get("title")) or page_path.stem
    category = page_path.parent.name

    tags = [_safe_text(item) for item in metadata.get("tags", []) if _safe_text(item)]
    related = [_safe_text(item) for item in metadata.get("related", []) if _safe_text(item)]
    source_refs = [_safe_text(item) for item in metadata.get("source_refs", []) if _safe_text(item)]

    chunks: list[QMDChunk] = []
    chunk_index = 0
    for heading, section_text in _split_markdown_sections(body, title):
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", section_text) if part.strip()]
        if not paragraphs:
            paragraphs = [section_text.strip()]

        buffer = ""
        for paragraph in paragraphs:
            candidate = paragraph if not buffer else f"{buffer}\n\n{paragraph}"
            if len(candidate) <= max_chars:
                buffer = candidate
                continue
            if buffer:
                chunk_index += 1
                chunks.append(
                    _build_chunk(
                        chunk_index=chunk_index,
                        page_path=page_path,
                        relative_path=relative_path,
                        category=category,
                        title=title,
                        heading=heading,
                        body=buffer,
                        metadata=metadata,
                        tags=tags,
                        related=related,
                        source_refs=source_refs,
                    )
                )
                buffer = ""
            for block in _slice_text_block(paragraph, max_chars=max_chars, overlap_chars=overlap_chars):
                chunk_index += 1
                chunks.append(
                    _build_chunk(
                        chunk_index=chunk_index,
                        page_path=page_path,
                        relative_path=relative_path,
                        category=category,
                        title=title,
                        heading=heading,
                        body=block,
                        metadata=metadata,
                        tags=tags,
                        related=related,
                        source_refs=source_refs,
                    )
                )
        if buffer:
            chunk_index += 1
            chunks.append(
                _build_chunk(
                    chunk_index=chunk_index,
                    page_path=page_path,
                    relative_path=relative_path,
                    category=category,
                    title=title,
                    heading=heading,
                    body=buffer,
                    metadata=metadata,
                    tags=tags,
                    related=related,
                    source_refs=source_refs,
                )
            )

    if chunks:
        return chunks

    fallback_body = body.strip() or title
    return [
        _build_chunk(
            chunk_index=1,
            page_path=page_path,
            relative_path=relative_path,
            category=category,
            title=title,
            heading=title,
            body=fallback_body,
            metadata=metadata,
            tags=tags,
            related=related,
            source_refs=source_refs,
        )
    ]


class QMDIndex:
    def __init__(
        self,
        *,
        wiki_root: Path | None = None,
        chroma_dir: Path | None = None,
        collection_name: str = QMD_COLLECTION_NAME,
    ) -> None:
        self.wiki_root = Path(wiki_root or (PROJECT_ROOT / "knowledge_wiki")).resolve()
        self.chroma_dir = Path(chroma_dir or get_qmd_chroma_dir(PROJECT_ROOT))
        self.collection_name = collection_name
        self._collection = None
        self._chroma_client = None
        self._model = None

    @property
    def model(self):
        if self._model is None:
            self._model = get_vector_model()
        return self._model

    @property
    def collection(self):
        client = get_chroma_client(str(self.chroma_dir))
        if client is not self._chroma_client or self._collection is None:
            self._chroma_client = client
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    def scan_pages(self) -> list[Path]:
        pages: list[Path] = []
        for directory_name in INDEXABLE_DIRS:
            directory = self.wiki_root / directory_name
            if not directory.exists():
                continue
            pages.extend(sorted(directory.glob("*.md")))
        return pages

    def build_chunks(self, *, max_chars: int = 900, overlap_chars: int = 120) -> list[QMDChunk]:
        chunks: list[QMDChunk] = []
        for page_path in self.scan_pages():
            relative_path = str(page_path.relative_to(self.wiki_root)).replace("\\", "/")
            chunks.extend(
                chunk_markdown_page(
                    page_path,
                    relative_path,
                    max_chars=max_chars,
                    overlap_chars=overlap_chars,
                )
            )
        return chunks

    def rebuild_index(
        self,
        *,
        batch_size: int = 64,
        max_chars: int = 900,
        overlap_chars: int = 120,
        reset: bool = True,
    ) -> dict[str, Any]:
        profile = get_qmd_profile()
        if self.model is None:
            raise RuntimeError("QMD index build failed: vector model is unavailable.")

        pages = self.scan_pages()
        chunks = self.build_chunks(max_chars=max_chars, overlap_chars=overlap_chars)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)

        if reset:
            shutil.rmtree(self.chroma_dir, ignore_errors=True)
            path_key = str(self.chroma_dir)
            if path_key in _CHROMA_CLIENTS:
                del _CHROMA_CLIENTS[path_key]
            self._chroma_client = None
            self._collection = None

        client = get_chroma_client(str(self.chroma_dir))
        self._chroma_client = client
        try:
            client.delete_collection(self.collection_name)
        except Exception:
            pass
        self._collection = client.create_collection(
            name=self.collection_name,
            metadata={
                "hnsw:space": "cosine",
                "vector_model": profile.key,
                "wiki_root": str(self.wiki_root),
                "built_at": datetime.now().isoformat(timespec="seconds"),
            },
        )

        if not chunks:
            return {
                "page_count": 0,
                "chunk_count": 0,
                "categories": {},
                "wiki_root": str(self.wiki_root),
                "chroma_dir": str(self.chroma_dir),
                "collection_name": self.collection_name,
            }

        by_category: dict[str, int] = {}
        for chunk in chunks:
            by_category[chunk.category] = by_category.get(chunk.category, 0) + 1

        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            embeddings = encode_documents(
                self.model,
                [item.search_text for item in batch],
                batch_size=batch_size,
                show_progress=False,
            )
            self.collection.add(
                ids=[item.chunk_id for item in batch],
                documents=[item.search_text for item in batch],
                embeddings=embeddings.tolist(),
                metadatas=[item.metadata for item in batch],
            )

        manifest = {
            "page_count": len(pages),
            "chunk_count": len(chunks),
            "categories": by_category,
            "wiki_root": str(self.wiki_root),
            "chroma_dir": str(self.chroma_dir),
            "collection_name": self.collection_name,
            "built_at": datetime.now().isoformat(timespec="seconds"),
        }
        logger.info(
            "QMD index build complete: {} pages -> {} chunks -> {}",
            manifest["page_count"],
            manifest["chunk_count"],
            self.chroma_dir,
        )
        return manifest

    def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        category: str | None = None,
        page_type: str | None = None,
        province: str | None = None,
        specialty: str | None = None,
        source_kind: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        if self.model is None:
            raise RuntimeError("QMD search failed: vector model is unavailable.")

        total = self.collection.count()
        if total <= 0:
            return []

        query_embedding = encode_queries(self.model, [query])
        where_filter = self._build_where_filter(
            category=category,
            page_type=page_type,
            province=province,
            specialty=specialty,
            source_kind=source_kind,
            status=status,
        )
        results = self.collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=min(max(top_k * 4, top_k), total),
            where=where_filter,
        )
        return self._format_results(results, top_k=top_k)

    @staticmethod
    def _build_where_filter(
        *,
        category: str | None,
        page_type: str | None,
        province: str | None,
        specialty: str | None,
        source_kind: str | None,
        status: str | None,
    ) -> dict[str, Any] | None:
        conditions: list[dict[str, Any]] = []
        if category:
            conditions.append({"category": category})
        if page_type:
            conditions.append({"type": page_type})
        if province:
            conditions.append({"province": province})
        if specialty:
            conditions.append({"specialty": specialty})
        if source_kind:
            conditions.append({"source_kind": source_kind})
        if status:
            conditions.append({"status": status})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    @staticmethod
    def _format_results(results: dict[str, Any], *, top_k: int) -> list[dict[str, Any]]:
        ids = results.get("ids", [[]])[0] if results else []
        metadatas = results.get("metadatas", [[]])[0] if results else []
        documents = results.get("documents", [[]])[0] if results else []
        distances = results.get("distances", [[]])[0] if results else []

        hits: list[dict[str, Any]] = []
        for idx, chunk_id in enumerate(ids):
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            document = documents[idx] if idx < len(documents) else ""
            distance = distances[idx] if idx < len(distances) else 1.0
            score = max(0.0, min(1.0, 1 - float(distance)))
            hits.append(
                {
                    "chunk_id": chunk_id,
                    "score": round(score, 6),
                    "title": metadata.get("title", ""),
                    "heading": metadata.get("heading", ""),
                    "category": metadata.get("category", ""),
                    "type": metadata.get("type", ""),
                    "path": metadata.get("path", ""),
                    "province": metadata.get("province", ""),
                    "specialty": metadata.get("specialty", ""),
                    "status": metadata.get("status", ""),
                    "source_kind": metadata.get("source_kind", ""),
                    "source_refs_text": metadata.get("source_refs_text", ""),
                    "preview": metadata.get("preview", ""),
                    "document": document,
                }
            )
        hits.sort(key=lambda item: item["score"], reverse=True)
        return hits[:top_k]


__all__ = [
    "INDEXABLE_DIRS",
    "QMD_COLLECTION_NAME",
    "QMDChunk",
    "QMDIndex",
    "chunk_markdown_page",
    "parse_frontmatter",
]
