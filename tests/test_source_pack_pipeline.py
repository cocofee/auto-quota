from __future__ import annotations

import json
from pathlib import Path

from src.source_pack import normalize_source_pack_metadata
from tools.compile_sources_to_wiki import compile_sources_to_wiki
from tools.ingest_chat import ingest_chat
from tools.ingest_document import ingest_document
from tools.ingest_image import ingest_image


def test_source_pack_pipeline_generates_source_pages(tmp_path, monkeypatch):
    source_root = tmp_path / "source_packs"
    packs_dir = source_root / "packs"
    texts_dir = source_root / "texts"
    output_dir = tmp_path / "knowledge_wiki"

    monkeypatch.setattr("src.source_pack.SOURCE_PACK_ROOT", source_root)
    monkeypatch.setattr("src.source_pack.SOURCE_PACKS_DIR", packs_dir)
    monkeypatch.setattr("src.source_pack.SOURCE_TEXTS_DIR", texts_dir)

    doc_path = tmp_path / "sample.md"
    doc_path.write_text("# 鏂囨。鏍囬\n閰嶇 SC20 鏆楁暦銆?", encoding="utf-8")

    chat_path = tmp_path / "chat.json"
    chat_path.write_text(
        json.dumps(
            [
                {"role": "user", "content": "濡備綍鍒ゆ柇妗ユ灦鏁疯锛?"},
                {"role": "assistant", "content": "鍏堢湅瀹夎璺緞銆?"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    image_path = tmp_path / "sample.png"
    image_path.write_bytes(b"not-a-real-image-but-existing")
    ocr_path = tmp_path / "sample.ocr.txt"
    ocr_path.write_text("妗ユ灦鍐呯數缂嗘暦璁綷nSC20 鏆楅厤", encoding="utf-8")

    ingest_document(input_path=doc_path, title="鏍蜂緥鏂囨。")
    ingest_chat(input_path=chat_path, title="鏍蜂緥瀵硅瘽")
    ingest_image(input_path=image_path, ocr_file=ocr_path, title="鏍蜂緥鍥剧墖")

    manifest = compile_sources_to_wiki(packs_dir=packs_dir, output_dir=output_dir)

    assert manifest["counts"]["sources"] == 4
    assert (output_dir / "sources" / "index.md").exists()

    source_files = list((output_dir / "sources").glob("source-*.md"))
    assert len(source_files) == 3

    text = source_files[0].read_text(encoding="utf-8")
    assert 'type: "source"' in text
    assert "## Source Pack" in text


def test_normalize_source_pack_metadata_falls_back_to_clean_filename_and_text(tmp_path):
    text_path = tmp_path / "doc.md"
    text_path.write_text(
        "山东省安装工程消耗量定额\n\n交底培训资料\n\n山东省工程建设标准造价中心\n",
        encoding="utf-8",
    )
    pack = {
        "source_id": "doc-c0d82dda08e6",
        "source_kind": "doc",
        "title": "2025閻楀牆鍖楁稉婊呮阜鐎瑰顥婂銉р柤濞戝牐鈧鍣虹€规岸顤傛禍銈呯俺閸╃顔勭挧鍕灐",
        "summary": "鐏炲彉绗㈤惇浣哥暔鐟佸懎浼愮粙瀣Х閼版鍣虹€规岸顤?娴溿倕绨抽崺纭咁唲鐠у嫭鏋?鐏炲彉绗㈤惇浣镐紣缁嬪缂撶拋鐐垼閸戝棝鈧姳鐜稉顓炵妇",
        "full_text_path": str(text_path),
        "evidence_refs": [r"E:\Jarvis-Raw\10_docs\2025版山东省安装工程消耗量定额交底培训资料.txt"],
        "province": "",
        "specialty": "",
        "tags": ["document"],
        "metadata": {"input_ext": ".txt"},
    }

    normalized = normalize_source_pack_metadata(pack)

    assert normalized["title"] == "2025版山东省安装工程消耗量定额交底培训资料"
    assert normalized["summary"].startswith("山东省安装工程消耗量定额 交底培训资料")

def test_normalize_source_pack_metadata_remaps_windows_full_text_path_to_runtime_source_root(tmp_path, monkeypatch):
    source_root = tmp_path / "source_packs"
    packs_dir = source_root / "packs"
    texts_dir = source_root / "texts"
    packs_dir.mkdir(parents=True, exist_ok=True)
    texts_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("src.source_pack.SOURCE_PACK_ROOT", source_root)
    monkeypatch.setattr("src.source_pack.SOURCE_PACKS_DIR", packs_dir)
    monkeypatch.setattr("src.source_pack.SOURCE_TEXTS_DIR", texts_dir)

    text_path = texts_dir / "doc-c0d82dda08e6.md"
    text_path.write_text("山东省安装工程消耗量定额\n\n交底培训资料\n", encoding="utf-8")

    pack = {
        "source_id": "doc-c0d82dda08e6",
        "source_kind": "doc",
        "title": "",
        "summary": "",
        "full_text_path": r"C:\nonexistent-host\auto-quota\data\source_packs\texts\doc-c0d82dda08e6.md",
        "evidence_refs": [],
        "province": "",
        "specialty": "",
        "tags": ["document"],
        "metadata": {"input_ext": ".txt"},
    }

    normalized = normalize_source_pack_metadata(pack)

    assert normalized["full_text_path"] == str(text_path)
    assert normalized["title"] == "山东省安装工程消耗量定额"
    assert normalized["summary"].startswith("山东省安装工程消耗量定额 交底培训资料")

