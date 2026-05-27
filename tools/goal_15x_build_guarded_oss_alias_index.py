from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from src.goal_search.oss_alias_prior import normalize_alias_text


DEFAULT_GROUPS = PROJECT_ROOT / "reports" / "agent_state" / "goal_14x_rank1_safe_source_robust_matrix" / "ltr_group_dev.jsonl"
DEFAULT_OUTPUT = Path(getattr(config, "OSS_GUARDED_ALIAS_INDEX_PATH", PROJECT_ROOT / "data" / "goal_search" / "guarded_oss_alias_index.jsonl"))
DEFAULT_MANIFEST = PROJECT_ROOT / "reports" / "agent_state" / "goal_15x_guarded_oss_alias_index_build_manifest.json"


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_index_rows(
    group_rows: list[dict[str, Any]],
    *,
    core_families: set[str],
    min_support: int,
) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in group_rows:
        query_family = str(row.get("query_family") or "").strip()
        if query_family not in core_families:
            continue
        normalized_query = normalize_alias_text(row.get("query"))
        province = str(row.get("province") or "").strip()
        if not normalized_query or not province:
            continue
        for quota_id in row.get("expected_ids") or []:
            quota_id = str(quota_id or "").strip()
            if not quota_id:
                continue
            key = (normalized_query, province, query_family, quota_id)
            item = grouped.setdefault(
                key,
                {
                    "normalized_query": normalized_query,
                    "province": province,
                    "query_family": query_family,
                    "quota_id": quota_id,
                    "support_count": 0,
                    "source_families": set(),
                    "source_file_hashes": set(),
                    "source_files": set(),
                    "oof_folds": set(),
                    "evidence": [],
                    "sample_group_ids": [],
                },
            )
            item["support_count"] += 1
            if row.get("source_family"):
                item["source_families"].add(str(row.get("source_family")))
            if row.get("source_file_hash"):
                item["source_file_hashes"].add(str(row.get("source_file_hash")))
            if row.get("source_file"):
                item["source_files"].add(str(row.get("source_file")))
            if row.get("oof_fold") not in (None, ""):
                item["oof_folds"].add(int(row.get("oof_fold")))
            item["evidence"].append(
                {
                    "source_family": str(row.get("source_family") or ""),
                    "source_file_hash": str(row.get("source_file_hash") or ""),
                    "source_file": str(row.get("source_file") or ""),
                    "oof_fold": int(row.get("oof_fold")) if row.get("oof_fold") not in (None, "") else None,
                    "group_id": str(row.get("group_id") or ""),
                }
            )
            if len(item["sample_group_ids"]) < 5 and row.get("group_id"):
                item["sample_group_ids"].append(str(row.get("group_id")))

    output = []
    for item in grouped.values():
        if item["support_count"] < min_support:
            continue
        output.append(
            {
                "normalized_query": item["normalized_query"],
                "province": item["province"],
                "query_family": item["query_family"],
                "quota_id": item["quota_id"],
                "support_count": item["support_count"],
                "source_family_count": len(item["source_families"]),
                "source_families": sorted(item["source_families"]),
                "source_file_hashes": sorted(item["source_file_hashes"]),
                "source_files": sorted(item["source_files"])[:20],
                "oof_folds": sorted(item["oof_folds"]),
                "evidence": item["evidence"],
                "sample_group_ids": item["sample_group_ids"],
                "candidate_source": "15A_GUARDED_CORE_STRICT_ALIAS_SUPPORT2",
            }
        )
    output.sort(key=lambda row: (row["province"], row["query_family"], row["normalized_query"], -row["support_count"], row["quota_id"]))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build guarded OSS strict-alias index")
    parser.add_argument("--groups-jsonl", type=Path, default=DEFAULT_GROUPS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--min-support", type=int, default=int(getattr(config, "OSS_GUARDED_ALIAS_MIN_SUPPORT", 2) or 2))
    parser.add_argument("--core-families", default=",".join(getattr(config, "OSS_GUARDED_ALIAS_CORE_FAMILIES", ("concrete", "rebar", "pipe", "pump", "support"))))
    parser.add_argument("--dev-oof-only", action="store_true", help="Documentation flag: this builder reads only dev/OOF matrix artifacts.")
    args = parser.parse_args()

    core_families = {part.strip() for part in args.core_families.split(",") if part.strip()}
    group_rows = _read_jsonl(args.groups_jsonl)
    index_rows = build_index_rows(group_rows, core_families=core_families, min_support=args.min_support)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for row in index_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    manifest = {
        "stage": "15.6 guarded OSS alias index build",
        "dev_oof_only": True,
        "input": str(args.groups_jsonl),
        "input_sha256": _file_hash(args.groups_jsonl),
        "output": str(args.output),
        "output_rows": len(index_rows),
        "source_group_rows": len(group_rows),
        "min_support": args.min_support,
        "core_families": sorted(core_families),
        "heldout_hard_used": False,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "rows": len(index_rows), "manifest": str(args.manifest)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
