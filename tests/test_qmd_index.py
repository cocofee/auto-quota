from __future__ import annotations

import numpy as np

from src.qmd_index import QMDIndex, chunk_markdown_page, parse_frontmatter


class FakeModel:
    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False, batch_size=64):
        vectors = []
        for text in texts:
            value = str(text)
            vector = np.array(
                [
                    1.0 if "阀" in value else 0.0,
                    1.0 if "电缆" in value else 0.0,
                    1.0 if "照片" in value or "现场" in value else 0.0,
                    max(len(value), 1) / 1000.0,
                ],
                dtype=float,
            )
            if normalize_embeddings:
                norm = np.linalg.norm(vector)
                if norm > 0:
                    vector = vector / norm
            vectors.append(vector)
        return np.array(vectors)


class FakeCollection:
    def __init__(self):
        self._items: list[dict] = []

    def add(self, *, ids, documents, embeddings, metadatas):
        for item_id, document, embedding, metadata in zip(ids, documents, embeddings, metadatas):
            self._items.append(
                {
                    "id": item_id,
                    "document": document,
                    "embedding": np.array(embedding, dtype=float),
                    "metadata": metadata,
                }
            )

    def count(self):
        return len(self._items)

    def query(self, *, query_embeddings, n_results, where=None):
        query_vec = np.array(query_embeddings[0], dtype=float)
        matches = []
        for item in self._items:
            if not _match_where(item["metadata"], where):
                continue
            score = float(np.dot(query_vec, item["embedding"]))
            distance = 1 - score
            matches.append((distance, item))
        matches.sort(key=lambda pair: pair[0])
        matches = matches[:n_results]
        return {
            "ids": [[item["id"] for _, item in matches]],
            "documents": [[item["document"] for _, item in matches]],
            "metadatas": [[item["metadata"] for _, item in matches]],
            "distances": [[distance for distance, _ in matches]],
        }


def _match_where(metadata: dict, where: dict | None) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_match_where(metadata, part) for part in where["$and"])
    for key, expected in where.items():
        if isinstance(expected, dict) and "$in" in expected:
            return metadata.get(key) in expected["$in"]
        return metadata.get(key) == expected
    return True


class FakeClient:
    def __init__(self):
        self._collections: dict[str, FakeCollection] = {}

    def get_or_create_collection(self, name, metadata=None):
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]

    def create_collection(self, name, metadata=None):
        collection = FakeCollection()
        self._collections[name] = collection
        return collection

    def delete_collection(self, name):
        self._collections.pop(name, None)


def test_parse_frontmatter_and_chunk_markdown_page(tmp_path):
    page = tmp_path / "rule-0001.md"
    page.write_text(
        """---
title: "电缆敷设纠正规则"
type: "rule"
status: "draft"
province: "北京"
specialty: "安装"
source_refs:
  - "source_pack:abc"
source_kind: "doc"
created_at: "2026-04-06"
updated_at: "2026-04-06"
confidence: 85
owner: "codex"
tags:
  - "rule"
  - "电缆"
related: []
---

# 电缆敷设纠正规则

## 规则正文
电缆敷设应结合桥架与穿管语义，不应直接套线缆明敷项目。
""",
        encoding="utf-8",
    )

    metadata, body = parse_frontmatter(page.read_text(encoding="utf-8"))
    assert metadata["title"] == "电缆敷设纠正规则"
    assert metadata["confidence"] == 85
    assert metadata["tags"] == ["rule", "电缆"]
    assert "规则正文" in body

    chunks = chunk_markdown_page(page, "rules/rule-0001.md", max_chars=80, overlap_chars=10)
    assert len(chunks) >= 1
    assert chunks[0].metadata["path"] == "rules/rule-0001.md"


def test_qmd_index_rebuild_and_search(tmp_path, monkeypatch):
    wiki_root = tmp_path / "knowledge_wiki"
    (wiki_root / "rules").mkdir(parents=True)
    (wiki_root / "sources").mkdir(parents=True)

    (wiki_root / "rules" / "rule-0001.md").write_text(
        """---
title: "电缆敷设纠正规则"
type: "rule"
status: "draft"
province: "北京"
specialty: "安装"
source_refs:
  - "source_pack:rule-1"
source_kind: "staging"
created_at: "2026-04-06"
updated_at: "2026-04-06"
confidence: 88
owner: "codex"
tags:
  - "rule"
  - "电缆"
related: []
---

# 电缆敷设纠正规则

## 规则正文
电缆敷设需要优先判断桥架、穿管和敷设方式，不应误套阀门或线缆明敷项目。
""",
        encoding="utf-8",
    )
    (wiki_root / "sources" / "source-image-0001.md").write_text(
        """---
title: "现场照片样例"
type: "source"
status: "draft"
province: "北京"
specialty: "安装"
source_refs:
  - "source_pack:image-1"
source_kind: "image"
created_at: "2026-04-06"
updated_at: "2026-04-06"
confidence: 80
owner: "codex"
tags:
  - "现场"
related: []
---

# 现场照片样例

## Summary
现场照片显示电缆桥架转角和管线交叉。
""",
        encoding="utf-8",
    )

    fake_client = FakeClient()
    monkeypatch.setattr("src.qmd_index.get_vector_model", lambda: FakeModel())
    monkeypatch.setattr("src.qmd_index.get_chroma_client", lambda path: fake_client)
    monkeypatch.setattr("src.qmd_index._CHROMA_CLIENTS", {})

    index = QMDIndex(wiki_root=wiki_root, chroma_dir=tmp_path / "chroma")
    manifest = index.rebuild_index(batch_size=2, max_chars=120, overlap_chars=20)

    assert manifest["page_count"] == 2
    assert manifest["chunk_count"] >= 2
    assert manifest["categories"]["rules"] >= 1
    assert manifest["categories"]["sources"] >= 1

    results = index.search("电缆桥架怎么纠正", top_k=3, page_type="rule")
    assert results
    assert results[0]["type"] == "rule"
    assert results[0]["path"] == "rules/rule-0001.md"

    source_results = index.search("现场照片 电缆", top_k=3, source_kind="image")
    assert source_results
    assert source_results[0]["source_kind"] == "image"
