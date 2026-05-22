from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


class BackendError(RuntimeError):
    """Raised when the real auto-quota backend cannot complete a request."""


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
        }


def resolve_auto_quota_root(explicit_root: str | None = None) -> Path:
    """Resolve the auto-quota repository root.

    Priority:
    1. explicit CLI option
    2. AUTO_QUOTA_ROOT environment variable
    3. parent of this harness directory inside the source checkout
    """

    raw = explicit_root or os.getenv("AUTO_QUOTA_ROOT", "").strip()
    if raw:
        root = Path(raw).expanduser().resolve()
    else:
        root = Path(__file__).resolve().parents[4]

    if not (root / "main.py").exists():
        raise BackendError(f"auto-quota root not found or invalid: {root}")
    return root


def project_status(root: Path, service_url: str, api_key: str | None = None) -> dict[str, Any]:
    health = None
    health_error = None
    try:
        health = request_health(service_url, api_key)
    except Exception as exc:  # health is an optional probe
        health_error = str(exc)

    return {
        "root": str(root),
        "main_py": str(root / "main.py"),
        "local_match_server_py": str(root / "local_match_server.py"),
        "main_exists": (root / "main.py").exists(),
        "local_match_server_exists": (root / "local_match_server.py").exists(),
        "service_url": service_url,
        "service_health": health,
        "service_health_error": health_error,
    }


def build_match_command(
    root: Path,
    input_file: str,
    output: str | None = None,
    mode: str = "search",
    province: str | None = None,
    sheet: str | None = None,
    limit: int | None = None,
    no_experience: bool = False,
    json_output: str | None = None,
    agent_llm: str | None = None,
) -> list[str]:
    cmd = [sys.executable, str(root / "main.py"), input_file, "--mode", mode]
    if output:
        cmd += ["--output", output]
    if province:
        cmd += ["--province", province]
    if sheet:
        cmd += ["--sheet", sheet]
    if limit:
        cmd += ["--limit", str(limit)]
    if no_experience:
        cmd.append("--no-experience")
    if json_output:
        cmd += ["--json-output", json_output]
    if agent_llm:
        cmd += ["--agent-llm", agent_llm]
    return cmd


def run_command(cmd: list[str], cwd: Path) -> CommandResult:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return CommandResult(
        command=cmd,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_match(root: Path, **kwargs: Any) -> CommandResult:
    cmd = build_match_command(root=root, **kwargs)
    result = run_command(cmd, cwd=root)
    if result.returncode != 0:
        raise BackendError(result.stderr or result.stdout or f"match failed: {result.returncode}")
    return result


def _headers(api_key: str | None) -> dict[str, str]:
    if not api_key:
        api_key = os.getenv("LOCAL_MATCH_API_KEY", "").strip()
    return {"X-API-Key": api_key} if api_key else {}


def request_health(service_url: str, api_key: str | None = None) -> dict[str, Any]:
    response = requests.get(
        f"{service_url.rstrip('/')}/health",
        headers=_headers(api_key),
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


def quota_search(
    service_url: str,
    query: str,
    province: str | None = None,
    limit: int = 10,
    api_key: str | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"q": query, "limit": limit}
    if province:
        params["province"] = province
    response = requests.get(
        f"{service_url.rstrip('/')}/quota-search",
        params=params,
        headers=_headers(api_key),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
