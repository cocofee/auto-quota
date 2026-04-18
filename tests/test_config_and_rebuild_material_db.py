from __future__ import annotations

import importlib.util
from pathlib import Path


def test_config_reads_hybrid_cache_ttl_from_env(monkeypatch):
    monkeypatch.setenv("HYBRID_KB_KEYWORD_CACHE_TTL_SEC", "0")
    monkeypatch.setenv("HYBRID_SESSION_CACHE_TTL_SEC", "42")

    config_path = Path(__file__).resolve().parents[1] / "config.py"
    spec = importlib.util.spec_from_file_location("config_ttl_probe", config_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)

    assert module.HYBRID_KB_KEYWORD_CACHE_TTL_SEC == 0.0
    assert module.HYBRID_SESSION_CACHE_TTL_SEC == 42.0


def test_rebuild_material_db_keeps_anhui_import_opt_in(monkeypatch, tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir(parents=True)
    (tools_dir / "import_anhui_official.py").write_text("# stub\n", encoding="utf-8")

    import tools.rebuild_material_db as rebuild_material_db

    monkeypatch.setattr(rebuild_material_db, "PROJECT_ROOT", tmp_path)

    default_commands = rebuild_material_db._build_extra_commands(include_wuhan=False)
    opted_in_commands = rebuild_material_db._build_extra_commands(
        include_wuhan=False,
        include_anhui_official=True,
    )

    assert not any(spec.label == "anhui:official" for spec in default_commands)
    assert any(spec.label == "anhui:official" for spec in opted_in_commands)
