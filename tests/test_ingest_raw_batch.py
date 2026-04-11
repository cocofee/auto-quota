from __future__ import annotations

import json
from pathlib import Path

from tools.ingest_raw_batch import run_batch_ingest


def _mkdirs(root: Path) -> None:
    for name in ("00_inbox", "10_docs", "20_images", "30_videos", "40_chats", "90_done"):
        (root / name).mkdir(parents=True, exist_ok=True)


def test_run_batch_ingest_imports_and_moves_supported_files(tmp_path, monkeypatch):
    raw_root = tmp_path / "Jarvis-Raw"
    _mkdirs(raw_root)

    source_root = tmp_path / "source_packs"
    packs_dir = source_root / "packs"
    texts_dir = source_root / "texts"
    monkeypatch.setattr("src.source_pack.SOURCE_PACK_ROOT", source_root)
    monkeypatch.setattr("src.source_pack.SOURCE_PACKS_DIR", packs_dir)
    monkeypatch.setattr("src.source_pack.SOURCE_TEXTS_DIR", texts_dir)
    monkeypatch.setattr("tools.compile_sources_to_wiki.SOURCE_PACKS_DIR", packs_dir)

    doc_path = raw_root / "00_inbox" / "瀹夎璇存槑.md"
    doc_path.write_text("# 瀹夎璇存槑\n妗ユ灦鍐呯數缂嗘暦璁俱€?", encoding="utf-8")

    image_path = raw_root / "00_inbox" / "鐜板満鐓х墖.jpg"
    image_path.write_bytes(b"image-bytes")
    image_ocr = raw_root / "00_inbox" / "鐜板満鐓х墖.jpg.txt"
    image_ocr.write_text("妗ยู灦鍐呯數缂嗙幇鍦虹収鐗囥€?", encoding="utf-8")

    video_path = raw_root / "00_inbox" / "妗ยู灦璁茶В.mp4"
    video_path.write_bytes(b"video-bytes")
    video_srt = raw_root / "00_inbox" / "妗ยู灦璁茶В.srt"
    video_srt.write_text(
        "1\n00:00:01,000 --> 00:00:02,500\n妗ยู灦瀹夎璁茶В\n",
        encoding="utf-8",
    )

    summary = run_batch_ingest(
        raw_root=raw_root,
        province="鍖椾含",
        specialty="瀹夎",
        tags=["鎵归噺瀵煎叆"],
        move_success=True,
        compile_wiki=False,
        build_qmd=False,
    )

    assert summary["imported"] == 3
    assert summary["errors"] == 0
    assert summary["by_kind"]["doc"] == 1
    assert summary["by_kind"]["image"] == 1
    assert summary["by_kind"]["video"] == 1
    assert not doc_path.exists()
    assert not image_path.exists()
    assert not image_ocr.exists()
    assert not video_path.exists()
    assert not video_srt.exists()
    assert any((raw_root / "10_docs").rglob("瀹夎璇存槑.md"))
    assert any((raw_root / "20_images").rglob("鐜板満鐓х墖.jpg"))
    assert any((raw_root / "30_videos").rglob("妗ยู灦璁茶В.mp4"))
    assert len(list(packs_dir.glob("*.json"))) == 3
    assert Path(summary["report_path"]).exists()

    for item in summary["items"]:
        if item["status"] != "imported":
            continue
        pack_path = Path(item["source_pack"]["pack_path"])
        payload = json.loads(pack_path.read_text(encoding="utf-8"))
        assert payload["evidence_refs"] == item["archived_paths"]
