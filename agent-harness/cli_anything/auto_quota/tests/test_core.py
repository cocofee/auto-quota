from __future__ import annotations

from pathlib import Path

import pytest

from cli_anything.auto_quota.utils.auto_quota_backend import (
    BackendError,
    build_match_command,
    resolve_auto_quota_root,
)


def test_resolve_explicit_root() -> None:
    root = Path(__file__).resolve().parents[4]
    assert resolve_auto_quota_root(str(root)) == root


def test_resolve_invalid_root(tmp_path: Path) -> None:
    with pytest.raises(BackendError):
        resolve_auto_quota_root(str(tmp_path))


def test_build_match_command_contains_expected_flags() -> None:
    root = Path("C:/repo")
    cmd = build_match_command(
        root=root,
        input_file="input.xlsx",
        output="out.xlsx",
        mode="search",
        province="广东",
        sheet="单位工程",
        limit=5,
        no_experience=True,
        json_output="result.json",
        agent_llm="deepseek",
    )
    joined = " ".join(cmd)
    assert "main.py" in joined
    assert "input.xlsx" in cmd
    assert "--mode" in cmd
    assert "search" in cmd
    assert "--output" in cmd
    assert "out.xlsx" in cmd
    assert "--province" in cmd
    assert "广东" in cmd
    assert "--no-experience" in cmd
    assert "--json-output" in cmd
