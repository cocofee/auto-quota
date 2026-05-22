# -*- coding: utf-8 -*-
"""Build AutoQuota artifacts for a GCCP auxiliary GBQ7 pricing source."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.gccp_aux_project import (  # noqa: E402
    ExcelImportPackageUpdater,
    GccpAuxRunRequest,
    write_manifest,
)


DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "gccp_aux"
SNAPSHOT_SCRIPT = PROJECT_ROOT / "tools" / "gccp_validation_snapshot.ps1"


def parse_aux_provinces(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def run_snapshot(
    mode: str,
    name: str,
    project_path: str = "",
    *,
    skip_local_files: bool = False,
) -> int:
    command = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SNAPSHOT_SCRIPT),
        "-Mode",
        mode,
        "-Name",
        name,
    ]
    if project_path:
        command.extend(["-ProjectPath", project_path])
    if skip_local_files:
        command.append("-SkipLocalFiles")
    completed = subprocess.run(command, check=False)
    return completed.returncode


def build_command(args: argparse.Namespace) -> int:
    if args.formal_gbq7 and args.aux_gbq7:
        formal = Path(args.formal_gbq7).expanduser().resolve()
        aux = Path(args.aux_gbq7).expanduser().resolve()
        if str(formal).lower() == str(aux).lower():
            print("Safety check blocked this run:")
            print("  - formal GBQ7 and auxiliary GBQ7 are the same file")
            return 2

    request = GccpAuxRunRequest(
        bill_excel=args.bill,
        formal_gbq7=args.formal_gbq7 or "",
        aux_gbq7=args.aux_gbq7 or "",
        province=args.province or "",
        aux_provinces=parse_aux_provinces(args.aux_province),
        mode=args.mode,
        sheet=args.sheet or "",
        limit=args.limit,
        use_experience=not args.no_experience,
        run_id=args.run_id or "",
        notes=args.notes or "",
    )

    output_dir = Path(args.output_dir).expanduser().resolve()
    updater = ExcelImportPackageUpdater()
    manifest = updater.build(request, output_dir=output_dir)

    manifest_path = output_dir / f"{manifest.run_id}_manifest.json"
    write_manifest(manifest_path, manifest)

    if manifest.safety.get("status") != "ok":
        print("Safety check blocked this run:")
        for item in manifest.safety.get("violations", []):
            print(f"  - {item}")
        return 2

    print("GCCP auxiliary pricing package created")
    print(f"  Import Excel : {manifest.generated_excel}")
    print(f"  Match JSON   : {manifest.generated_json}")
    print(f"  Manifest     : {manifest_path}")
    if manifest.aux_gbq7:
        print(f"  Aux GBQ7     : {manifest.aux_gbq7}")
    if manifest.formal_gbq7:
        print(f"  Formal GBQ7  : {manifest.formal_gbq7}")
    print()
    print("Next:")
    for index, step in enumerate(manifest.next_steps, start=1):
        print(f"  {index}. {step}")
    return 0


def snapshot_command(args: argparse.Namespace) -> int:
    project_path = args.project_path or args.formal_gbq7 or ""
    return run_snapshot(
        args.mode,
        args.name,
        project_path=project_path,
        skip_local_files=args.skip_local_files,
    )


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create and validate AutoQuota artifacts for a GCCP auxiliary GBQ7 "
            "pricing source. The formal GBQ7 project is never written by this tool."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="run AutoQuota and create aux import package")
    build.add_argument("--bill", required=True, help="formal/standard bill Excel to match")
    build.add_argument("--formal-gbq7", default="", help="formal GBQ7 path, recorded read-only")
    build.add_argument("--aux-gbq7", default="", help="auxiliary pricing-source GBQ7 path")
    build.add_argument("--province", default="", help="main quota province/book")
    build.add_argument("--aux-province", default="", help="comma-separated sibling quota books")
    build.add_argument("--mode", choices=["agent", "search"], default="agent")
    build.add_argument("--sheet", default="", help="optional Excel sheet name")
    build.add_argument("--limit", type=int, default=None, help="optional item limit for testing")
    build.add_argument("--no-experience", action="store_true", help="disable experience DB")
    build.add_argument("--run-id", default="", help="stable run id, optional")
    build.add_argument("--notes", default="", help="free-form manifest notes")
    build.add_argument("--output-dir", default=str(DEFAULT_OUT_DIR), help="artifact output dir")
    build.set_defaults(func=build_command)

    snapshot = subparsers.add_parser(
        "snapshot",
        help="wrap gccp_validation_snapshot.ps1 before/after/compare",
    )
    snapshot.add_argument("--mode", required=True, choices=["before", "after", "compare"])
    snapshot.add_argument("--name", required=True)
    snapshot.add_argument("--project-path", default="", help="GBQ7 path to inspect as zip-like package")
    snapshot.add_argument("--formal-gbq7", default="", help="alias for --project-path")
    snapshot.add_argument(
        "--skip-local-files",
        action="store_true",
        help="only inspect the project package; useful for quick smoke tests",
    )
    snapshot.set_defaults(func=snapshot_command)

    return parser


def main() -> None:
    parser = make_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
