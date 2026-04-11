from __future__ import annotations

from src.source_learning import (
    build_learning_chunks,
    merge_source_learning_candidates,
    normalize_source_learning_candidate,
    parse_source_learning_response,
)


def test_build_learning_chunks_splits_by_heading_and_size(tmp_path):
    text_path = tmp_path / "source.md"
    text_path.write_text(
        "# 总则\n"
        "这里是总则内容。\n\n"
        "## 规则\n"
        + ("配管 SC20 暗配。判断时先看材质，再看敷设方式。\n" * 80),
        encoding="utf-8",
    )
    pack = {
        "source_id": "doc-001",
        "title": "测试资料",
        "source_kind": "doc",
        "full_text_path": str(text_path),
    }

    chunks = build_learning_chunks(pack, chunk_size=160, overlap=20, max_chunks=10)

    assert len(chunks) >= 2
    assert chunks[0].chunk_id == "doc-001:s01:c01"
    assert "总则" in chunks[0].heading
    assert any("规则" in chunk.heading for chunk in chunks)


def test_parse_source_learning_response_accepts_fenced_json():
    response = """```json
{
  "candidates": [
    {
      "candidate_type": "rule",
      "title": "SC20 暗配优先看材质",
      "summary": "先判断管材，再判断敷设方式。",
      "confidence": 0.92,
      "keywords": ["SC20", "暗配"],
      "rule_text": "当清单为 SC20 暗配时，先区分管材。",
      "conditions": ["适用于配管审核"],
      "exclusions": ["不适用于桥架"],
      "evidence_text": "片段明确写到先判断材质再判断敷设方式"
    },
    {
      "candidate_type": "note",
      "title": "无效",
      "summary": "应被过滤"
    }
  ]
}
```"""

    parsed = parse_source_learning_response(response)

    assert len(parsed) == 1
    assert parsed[0]["candidate_type"] == "rule"
    assert parsed[0]["keywords"] == ["SC20", "暗配"]
    assert parsed[0]["conditions"] == ["适用于配管审核"]


def test_normalize_source_learning_candidate_builds_promotion_payload(tmp_path):
    text_path = tmp_path / "source.md"
    text_path.write_text("## 规则\n配管 SC20 暗配时优先看材质。", encoding="utf-8")
    pack = {
        "source_id": "doc-002",
        "title": "配管审核资料",
        "source_kind": "doc",
        "province": "山东2025",
        "specialty": "安装",
        "full_text_path": str(text_path),
        "evidence_refs": [r"E:\Jarvis-Raw\10_docs\配管审核资料.txt"],
    }
    raw_candidate = {
        "candidate_type": "method",
        "title": "SC20 暗配审核步骤",
        "summary": "先看材质，再看敷设方式。",
        "confidence": 0.8,
        "keywords": ["SC20", "暗配"],
        "method_text": "审核 SC20 暗配时先核材质，再核敷设方式。",
        "common_errors": ["把桥架误判成配管"],
        "evidence_text": "资料写明先判断材质再判断敷设方式",
    }
    chunk = build_learning_chunks(pack, chunk_size=300, overlap=20, max_chunks=5)[0]

    normalized = normalize_source_learning_candidate(raw_candidate, pack=pack, chunk=chunk)

    assert normalized is not None
    assert normalized["target_layer"] == "MethodCards"
    assert normalized["source_record_id"].startswith("doc-002:method:")
    assert normalized["candidate_payload"]["province"] == "山东2025"
    assert normalized["candidate_payload"]["pattern_keys"] == ["SC20", "暗配"]
    assert "source_pack:doc-002#chunk:" in normalized["evidence_ref"]


def test_merge_source_learning_candidates_merges_payload_lists():
    candidates = [
        {
            "candidate_type": "rule",
            "candidate_title": "SC20 暗配规则",
            "candidate_summary": "短摘要",
            "candidate_payload": {
                "keywords": ["SC20"],
                "evidence_refs": ["source_pack:doc-1#chunk:a"],
                "rule_text": "先判断材质。",
            },
            "confidence_score": 80,
        },
        {
            "candidate_type": "rule",
            "candidate_title": "SC20 暗配规则",
            "candidate_summary": "更长一点的摘要内容",
            "candidate_payload": {
                "keywords": ["暗配"],
                "evidence_refs": ["source_pack:doc-1#chunk:b"],
                "rule_text": "先判断材质，再判断敷设方式。",
            },
            "confidence_score": 90,
        },
    ]

    merged = merge_source_learning_candidates(candidates)

    assert len(merged) == 1
    assert merged[0]["candidate_summary"] == "更长一点的摘要内容"
    assert merged[0]["candidate_payload"]["keywords"] == ["SC20", "暗配"]
    assert merged[0]["candidate_payload"]["evidence_refs"] == [
        "source_pack:doc-1#chunk:a",
        "source_pack:doc-1#chunk:b",
    ]
    assert merged[0]["confidence_score"] == 90


def test_build_learning_chunks_skips_toc_and_prioritizes_body_sections(tmp_path):
    text_path = tmp_path / "source.txt"
    text_path.write_text(
        "目录\n\n"
        "第一册 机械设备安装工程........1\n"
        "总说明........5\n"
        "工程量计算规则........8\n\n"
        "编制概况\n"
        "本册用于说明安装工程定额编制背景。\n\n"
        "总说明\n"
        "配管工程量应按设计图示数量计算，不包括沟槽开挖。\n\n"
        "工程量计算规则\n"
        "电缆敷设长度应按设计图示中心线长度计算。\n",
        encoding="utf-8",
    )
    pack = {
        "source_id": "doc-plain",
        "title": "山东安装资料",
        "source_kind": "doc",
        "full_text_path": str(text_path),
    }

    chunks = build_learning_chunks(pack, chunk_size=120, overlap=20, max_chunks=4)

    assert chunks
    assert all("目录" not in chunk.text for chunk in chunks)
    assert any("工程量计算规则" in chunk.heading for chunk in chunks)
    assert any("应按设计图示" in chunk.text for chunk in chunks)
