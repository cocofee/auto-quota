import json
import shutil
from pathlib import Path

from src.province_plugins.loader import ProvincePluginRegistry, normalize_plugin_term


def test_normalize_plugin_term_removes_spacing_and_punctuation():
    assert normalize_plugin_term(" 配电箱：1AP1 ") == "配电箱1AP1"


def test_province_plugin_registry_resolves_hints_from_generated_knowledge():
    temp_root = Path("output/_tmp_plugin_loader")
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        knowledge_path = temp_root / "knowledge.json"
        knowledge_path.write_text(json.dumps({
            "national": {
                "synonyms": {
                    "配电箱": {"aliases": ["成套配电箱安装"]},
                },
                "route_biases": {},
                "tier_hints": {},
            },
            "provinces": {
                "宁夏安装工程计价定额(2019)": {
                    "synonyms": {
                        "配电箱": {"aliases": ["悬挂、嵌入式配电箱"]},
                    },
                    "route_biases": {
                        "配电箱": {
                            "preferred_books": ["C4"],
                            "preferred_specialties": ["C4"],
                        },
                    },
                    "tier_hints": {
                        "配电箱": {
                            "preferred_quota_names": ["成套配电箱安装 悬挂、嵌入式"],
                            "avoided_quota_names": ["成套配电箱安装 落地式"],
                        },
                    },
                },
            },
        }, ensure_ascii=False), encoding="utf-8")

        registry = ProvincePluginRegistry(knowledge_path, extra_paths=[])
        hints = registry.resolve_hints(
            province="宁夏安装工程计价定额(2019)",
            item={"name": "配电箱"},
            canonical_features={"canonical_name": "配电箱"},
        )

        assert hints["province_key"] == "宁夏安装工程计价定额(2019)"
        assert "悬挂、嵌入式配电箱" in hints["synonym_aliases"]
        assert "成套配电箱安装" in hints["synonym_aliases"]
        assert hints["preferred_books"] == ["C4"]
        assert hints["preferred_specialties"] == ["C4"]
        assert hints["preferred_quota_names"] == ["成套配电箱安装 悬挂、嵌入式"]
        assert hints["avoided_quota_names"] == ["成套配电箱安装 落地式"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_province_plugin_registry_matches_family_term_from_code_style_name():
    temp_root = Path("output/_tmp_plugin_family")
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        knowledge_path = temp_root / "knowledge.json"
        knowledge_path.write_text(json.dumps({
            "national": {
                "synonyms": {"配电箱": {"aliases": ["成套配电箱安装"]}},
                "route_biases": {"配电箱": {"preferred_books": ["C4"]}},
                "tier_hints": {"配电箱": {"avoided_quota_names": ["成套配电箱安装 落地式"]}},
            },
            "provinces": {},
        }, ensure_ascii=False), encoding="utf-8")

        registry = ProvincePluginRegistry(knowledge_path, extra_paths=[])
        hints = registry.resolve_hints(
            province="",
            item={"name": "配电箱A1-AP-CJ"},
            canonical_features={},
        )

        assert "成套配电箱安装" in hints["synonym_aliases"]
        assert "C4" in hints["preferred_books"]
        assert "成套配电箱安装 落地式" in hints["avoided_quota_names"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_province_plugin_registry_runtime_asset_fallback_builds_hints():
    temp_root = Path("output/_tmp_plugin_asset_fallback")
    shutil.rmtree(temp_root, ignore_errors=True)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        asset_root = temp_root / "assets" / "20260320_000001"
        asset_root.mkdir(parents=True, exist_ok=True)
        (asset_root / "manifest.json").write_text(json.dumps({"ok": True}, ensure_ascii=False), encoding="utf-8")
        (asset_root / "tier_errors.jsonl").write_text(
            json.dumps({
                "province": "宁夏安装工程计价定额(2019)",
                "bill_name": "配电箱A1-AP-CJ",
                "specialty": "C4",
                "expected_quota_names": ["成套配电箱安装 悬挂、嵌入式"],
                "predicted_quota_name": "成套配电箱安装 落地式",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (asset_root / "synonym_gaps.jsonl").write_text(
            json.dumps({
                "province": "宁夏安装工程计价定额(2019)",
                "bill_name": "配电箱A1-AP-CJ",
                "expected_quota_names": ["成套配电箱安装"],
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (asset_root / "route_errors.jsonl").write_text(
            json.dumps({
                "province": "宁夏安装工程计价定额(2019)",
                "bill_name": "配电箱A1-AP-CJ",
                "expected_book": "C4",
                "specialty": "C4",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        empty_knowledge = temp_root / "knowledge.json"
        empty_knowledge.write_text(json.dumps({
            "national": {"synonyms": {}, "route_biases": {}, "tier_hints": {}},
            "provinces": {},
        }, ensure_ascii=False), encoding="utf-8")

        registry = ProvincePluginRegistry(
            empty_knowledge,
            extra_paths=[],
            asset_root=temp_root / "assets",
        )
        hints = registry.resolve_hints(
            province="宁夏安装工程计价定额(2019)",
            item={"name": "配电箱A1-AP-CJ"},
            canonical_features={},
        )

        assert "成套配电箱安装" in hints["synonym_aliases"]
        assert hints["preferred_books"] == ["C4"]
        assert hints["preferred_specialties"] == ["C4"]
        assert hints["preferred_quota_names"] == ["成套配电箱安装 悬挂、嵌入式"]
        assert hints["avoided_quota_names"] == ["成套配电箱安装 落地式"]
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def test_default_common_plugin_contains_short_code_and_common_install_aliases():
    common_path = Path("data/province_plugins/common.json")
    registry = ProvincePluginRegistry(Path("output/_nonexistent_knowledge.json"), extra_paths=[common_path])

    kl_hints = registry.resolve_hints(
        province="",
        item={"name": "KL"},
        canonical_features={"canonical_name": "KL"},
    )
    assert "控制电缆敷设" in kl_hints["synonym_aliases"]
    assert "C4" in kl_hints["preferred_books"]
    assert "中间头制作与安装" in "".join(kl_hints["avoided_quota_names"])

    wiring_hints = registry.resolve_hints(
        province="",
        item={"name": "配线"},
        canonical_features={"canonical_name": "配线"},
    )
    assert "管内穿线" in wiring_hints["synonym_aliases"]
    assert "电力电缆敷设" in "".join(wiring_hints["avoided_quota_names"])
