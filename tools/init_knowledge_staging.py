#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Initialize the knowledge staging SQLite database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.knowledge_staging import init_knowledge_staging


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize knowledge_staging.db")
    parser.add_argument(
        "--db-path",
        default="",
        help="Optional override for the staging database path.",
    )
    parser.add_argument(
        "--schema-path",
        default="",
        help="Optional override for the staging schema SQL path.",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None
    schema_path = Path(args.schema_path) if args.schema_path else None

    staging = init_knowledge_staging(db_path=db_path, schema_path=schema_path)
    health = staging.health_check()
    print(json.dumps(health, ensure_ascii=False, indent=2))
    return 0 if health.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
