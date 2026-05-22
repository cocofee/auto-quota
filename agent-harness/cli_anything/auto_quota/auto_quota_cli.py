from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

import click

from . import __version__
from .utils.auto_quota_backend import (
    BackendError,
    build_match_command,
    dumps,
    project_status,
    quota_search,
    request_health,
    resolve_auto_quota_root,
    run_match,
)
from .utils.repl_skin import ReplSkin


DEFAULT_SERVICE_URL = "http://127.0.0.1:9300"


def emit(payload: Any, as_json: bool) -> None:
    if as_json:
        click.echo(dumps(payload))
    elif isinstance(payload, str):
        click.echo(payload)
    else:
        click.echo(dumps(payload))


def pass_common(fn):
    fn = click.option("--json", "json_mode", is_flag=True, help="Emit machine-readable JSON.")(fn)
    fn = click.option("--service-url", default=DEFAULT_SERVICE_URL, show_default=True)(fn)
    fn = click.option("--api-key", default=None, help="LOCAL_MATCH_API_KEY override.")(fn)
    fn = click.option("--root", "root_override", default=None, help="Path to auto-quota repo root.")(fn)
    return fn


@click.group(invoke_without_command=True)
@pass_common
@click.pass_context
def cli(ctx: click.Context, root_override: str | None, service_url: str, api_key: str | None, json_mode: bool) -> None:
    """CLI-Anything harness for auto-quota."""

    ctx.ensure_object(dict)
    ctx.obj.update(
        {
            "root_override": root_override,
            "service_url": service_url,
            "api_key": api_key,
            "json": json_mode,
        }
    )
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


@cli.command()
@click.pass_context
def repl(ctx: click.Context) -> None:
    """Start a small interactive shell."""

    skin = ReplSkin("auto-quota", version=__version__)
    skin.print_banner()
    skin.help(
        {
            "status": "show backend paths and service probe",
            "match file ...": "run auto-quota on an Excel file",
            "server health": "check local_match_server",
            "quota search ...": "search quotas through local_match_server",
        }
    )
    while True:
        try:
            line = input("auto-quota> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in {"exit", "quit"}:
            return
        if line == "help":
            skin.help(
                {
                    "status": "show backend paths and service probe",
                    "match file INPUT --mode search -o OUT.xlsx": "run a match",
                    "server health": "check local service",
                    "quota search KEYWORD": "search quota DB",
                }
            )
            continue
        try:
            cli.main(args=shlex.split(line), obj=ctx.obj, standalone_mode=False)
        except Exception as exc:
            skin.error(str(exc))


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Inspect backend paths and optional service health."""

    root = resolve_auto_quota_root(ctx.obj["root_override"])
    payload = project_status(root, ctx.obj["service_url"], ctx.obj["api_key"])
    emit(payload, ctx.obj["json"])


@cli.group()
def match() -> None:
    """Run auto-quota matching operations."""


@match.command("file")
@click.argument("input_file", type=click.Path(exists=True, dir_okay=False))
@click.option("-o", "--output", default=None, help="Output Excel path.")
@click.option("--mode", type=click.Choice(["search", "agent"]), default="search", show_default=True)
@click.option("--province", default=None, help="auto-quota province database label.")
@click.option("--sheet", default=None, help="Only process one sheet.")
@click.option("--limit", type=int, default=None, help="Debug limit.")
@click.option("--no-experience", is_flag=True, help="Disable experience DB.")
@click.option("--json-output", default=None, help="Write raw match JSON to this path.")
@click.option("--agent-llm", default=None, help="Agent-mode LLM name.")
@click.option("--dry-run", is_flag=True, help="Print the backend command without running it.")
@click.pass_context
def match_file(
    ctx: click.Context,
    input_file: str,
    output: str | None,
    mode: str,
    province: str | None,
    sheet: str | None,
    limit: int | None,
    no_experience: bool,
    json_output: str | None,
    agent_llm: str | None,
    dry_run: bool,
) -> None:
    """Run existing main.py matching for an Excel file."""

    root = resolve_auto_quota_root(ctx.obj["root_override"])
    kwargs = {
        "input_file": input_file,
        "output": output,
        "mode": mode,
        "province": province,
        "sheet": sheet,
        "limit": limit,
        "no_experience": no_experience,
        "json_output": json_output,
        "agent_llm": agent_llm,
    }
    if dry_run:
        emit({"command": build_match_command(root=root, **kwargs), "cwd": str(root)}, ctx.obj["json"])
        return
    try:
        result = run_match(root=root, **kwargs)
    except BackendError as exc:
        raise click.ClickException(str(exc)) from exc
    emit(result.as_dict(), ctx.obj["json"])


@cli.group()
def server() -> None:
    """Interact with local_match_server.py."""


@server.command("health")
@click.pass_context
def server_health(ctx: click.Context) -> None:
    """Check the running local match service."""

    try:
        payload = request_health(ctx.obj["service_url"], ctx.obj["api_key"])
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    emit(payload, ctx.obj["json"])


@cli.group()
def quota() -> None:
    """Quota database helpers."""


@quota.command("search")
@click.argument("query")
@click.option("--province", default=None)
@click.option("--limit", type=int, default=10, show_default=True)
@click.pass_context
def quota_search_cmd(ctx: click.Context, query: str, province: str | None, limit: int) -> None:
    """Search quotas through the local HTTP service."""

    try:
        payload = quota_search(
            service_url=ctx.obj["service_url"],
            query=query,
            province=province,
            limit=limit,
            api_key=ctx.obj["api_key"],
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc
    emit(payload, ctx.obj["json"])
