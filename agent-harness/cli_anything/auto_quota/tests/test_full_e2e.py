from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cli_anything.auto_quota"] + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_help() -> None:
    result = run_cli(["--help"])
    assert result.returncode == 0
    assert "CLI-Anything harness" in result.stdout


def test_status_json() -> None:
    result = run_cli(["--json", "status"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["main_exists"] is True
    assert payload["local_match_server_exists"] is True


def test_match_file_dry_run_json(tmp_path: Path) -> None:
    fake_input = tmp_path / "input.xlsx"
    fake_input.write_bytes(b"placeholder")
    result = run_cli(["--json", "match", "file", str(fake_input), "--dry-run", "--mode", "search"])
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["cwd"].endswith("auto-quota")
    assert "--mode" in payload["command"]
