import json
import shutil
from pathlib import Path

from tools.build_benchmark_knowledge import (
    build_knowledge_digest,
    build_knowledge_from_asset_root,
    render_digest_markdown,
)


def test_build_benchmark_knowledge_aggregates_assets():
    temp_root = Path("output/_tmp_benchmark_knowledge")
    shutil.rmtree(temp_root, ignore_errors=True)
    run_dir = temp_root / "20260320_010203"
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
        (run_dir / "synonym_gaps.jsonl").write_text(
            json.dumps({
                "province": "宁夏安装工程计价定额(2019)",
                "bill_name": "配电箱",
                "expected_quota_names": ["成套配电箱安装 悬挂、嵌入式"],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "route_errors.jsonl").write_text(
            json.dumps({
                "province": "宁夏安装工程计价定额(2019)",
                "bill_name": "配电箱",
                "expected_book": "C4",
                "specialty": "C4",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "tier_errors.jsonl").write_text(
            json.dumps({
                "province": "宁夏安装工程计价定额(2019)",
                "bill_name": "配电箱",
                "expected_quota_names": ["成套配电箱安装 悬挂、嵌入式"],
                "predicted_quota_name": "成套配电箱安装 落地式",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        knowledge = build_knowledge_from_asset_root(temp_root)

        province_block = knowledge["provinces"]["宁夏安装工程计价定额(2019)"]
        assert province_block["synonyms"]["配电箱"]["aliases"] == ["成套配电箱安装 悬挂、嵌入式"]
        assert province_block["route_biases"]["配电箱"]["preferred_books"] == ["C4"]
        assert province_block["route_biases"]["配电箱"]["preferred_specialties"] == ["C4"]
        assert province_block["tier_hints"]["配电箱"]["preferred_quota_names"] == ["成套配电箱安装 悬挂、嵌入式"]
        assert province_block["tier_hints"]["配电箱"]["avoided_quota_names"] == ["成套配电箱安装 落地式"]
        assert knowledge["national"]["synonyms"]["配电箱"]["aliases"] == ["成套配电箱安装 悬挂、嵌入式"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_build_benchmark_knowledge_digest_summarizes_top_terms():
    digest = build_knowledge_digest({
        "generated_at": "2026-03-20 10:00:00",
        "source_asset_root": "output/benchmark_assets",
        "record_counts": {"synonym_records": 3, "route_records": 2, "tier_records": 1},
        "national": {
            "synonyms": {
                "配电箱": {"aliases": ["成套配电箱安装"], "count": 3},
            },
            "route_biases": {
                "配电箱": {"preferred_books": ["C4"], "count": 2},
            },
            "tier_hints": {
                "配电箱": {"preferred_quota_names": ["成套配电箱安装 悬挂、嵌入式"], "count": 1},
            },
        },
        "provinces": {
            "宁夏安装工程计价定额(2019)": {
                "synonyms": {"配电箱": {"aliases": ["成套配电箱安装"], "count": 3}},
                "route_biases": {"配电箱": {"preferred_books": ["C4"], "count": 2}},
                "tier_hints": {"配电箱": {"preferred_quota_names": ["成套配电箱安装 悬挂、嵌入式"], "count": 1}},
            }
        },
    })

    assert digest["top_national_synonyms"][0]["term"] == "配电箱"
    assert digest["top_national_route_biases"][0]["preferred_books"] == ["C4"]
    assert digest["top_national_tier_hints"][0]["preferred_quota_names"] == ["成套配电箱安装 悬挂、嵌入式"]
    assert digest["province_summaries"][0]["province"] == "宁夏安装工程计价定额(2019)"


def test_render_digest_markdown_contains_sections():
    markdown = render_digest_markdown({
        "generated_at": "2026-03-20 10:00:00",
        "source_asset_root": "output/benchmark_assets",
        "record_counts": {"synonym_records": 1},
        "top_national_synonyms": [{"term": "配电箱", "aliases": ["成套配电箱安装"]}],
        "top_national_route_biases": [{"term": "配电箱", "preferred_books": ["C4"]}],
        "top_national_tier_hints": [{"term": "配电箱", "preferred_quota_names": ["悬挂、嵌入式"]}],
        "province_summaries": [{"province": "宁夏", "synonym_terms": 1, "route_terms": 1, "tier_terms": 1}],
    })

    assert "# Benchmark Knowledge Digest" in markdown
    assert "## Top National Synonyms" in markdown
    assert "配电箱: 成套配电箱安装" in markdown
    assert "## Province Summaries" in markdown
