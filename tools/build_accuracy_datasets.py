from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.accuracy_baseline.data_audit import (  # noqa: E402
    export_oss_diagnostic_cases,
    export_primary_cases,
)


def _report_dict(report) -> dict:
    def jsonable(value):
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, dict):
            return {str(key): jsonable(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [jsonable(item) for item in value]
        return value

    return jsonable(asdict(report))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build audited accuracy datasets")
    parser.add_argument("--experience-db", required=True)
    parser.add_argument("--primary-output")
    parser.add_argument("--oss-xml-root")
    parser.add_argument("--oss-output-dir")
    parser.add_argument("--split-seed", default="accuracy-baseline-v1")
    parser.add_argument("--summary-output", required=True)
    args = parser.parse_args()

    if not args.primary_output and not args.oss_output_dir:
        parser.error("at least one dataset output is required")
    if bool(args.oss_xml_root) != bool(args.oss_output_dir):
        parser.error("--oss-xml-root and --oss-output-dir must be provided together")

    manifest = {}
    if args.primary_output:
        manifest["primary"] = _report_dict(
            export_primary_cases(args.experience_db, args.primary_output)
        )
    if args.oss_output_dir:
        manifest["oss_diagnostic"] = _report_dict(
            export_oss_diagnostic_cases(
                args.experience_db,
                args.oss_xml_root,
                args.oss_output_dir,
                args.split_seed,
            )
        )
    summary_path = Path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    summary_path.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
