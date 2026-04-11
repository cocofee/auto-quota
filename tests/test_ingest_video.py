from __future__ import annotations

import json

from tools.ingest_video import ingest_video


def test_ingest_video_writes_source_pack_and_timeline(tmp_path, monkeypatch):
    source_root = tmp_path / "source_packs"
    packs_dir = source_root / "packs"
    texts_dir = source_root / "texts"

    monkeypatch.setattr("src.source_pack.SOURCE_PACK_ROOT", source_root)
    monkeypatch.setattr("src.source_pack.SOURCE_PACKS_DIR", packs_dir)
    monkeypatch.setattr("src.source_pack.SOURCE_TEXTS_DIR", texts_dir)

    transcript = tmp_path / "sample.srt"
    transcript.write_text(
        "1\n00:00:01,000 --> 00:00:05,000\n先看消火栓系统组成。\n\n"
        "2\n00:00:06,000 --> 00:00:10,000\n再看泵房六管系统。\n",
        encoding="utf-8",
    )

    result = ingest_video(
        transcript_file=transcript,
        title="视频样例",
        video_ref="video://sample-01",
        province="北京市建设工程施工消耗量标准(2024)",
        specialty="安装",
        tags=["sample", "p3"],
    )

    pack_path = packs_dir / f"{result['source_id']}.json"
    text_path = texts_dir / f"{result['source_id']}.md"

    assert pack_path.exists()
    assert text_path.exists()

    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    assert pack["source_kind"] == "video"
    assert pack["metadata"]["cue_count"] == 2
    assert pack["metadata"]["has_timeline"] is True

    transcript_text = text_path.read_text(encoding="utf-8")
    assert "## Timeline" in transcript_text
    assert "[00:00:01.000 -> 00:00:05.000]" in transcript_text
