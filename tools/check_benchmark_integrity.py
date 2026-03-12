"""Benchmark paper integrity audit and quarantine helper.

Usage:
    python tools/check_benchmark_integrity.py
    python tools/check_benchmark_integrity.py --paper Jiangxi
    python tools/check_benchmark_integrity.py --report-out tests/benchmark_papers/_integrity_report.json
    python tools/check_benchmark_integrity.py --override-out tests/benchmark_papers/_paper_overrides.json
    python tools/check_benchmark_integrity.py --fix-prune
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import get_quota_db_path  # noqa: E402


PAPERS_DIR = PROJECT_ROOT / "tests" / "benchmark_papers"
DEFAULT_REPORT_PATH = PAPERS_DIR / "_integrity_report.json"
DEFAULT_OVERRIDE_PATH = PAPERS_DIR / "_paper_overrides.json"


def iter_papers(paper_filter: str | None = None) -> list[Path]:
    papers = [
        path for path in sorted(PAPERS_DIR.glob("*.json"))
        if not path.name.startswith("_")
    ]
    if not paper_filter:
        return papers
    return [path for path in papers if paper_filter in path.stem]


def load_paper_data(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_quota_rows(
    province: str, cache: dict[str, dict[str, str] | None]
) -> dict[str, str] | None:
    if province not in cache:
        try:
            db_path = get_quota_db_path(province)
            conn = sqlite3.connect(db_path)
            try:
                cache[province] = {
                    str(row[0]): str(row[1] or "")
                    for row in conn.execute("SELECT quota_id, name FROM quotas")
                }
            finally:
                conn.close()
        except Exception:
            cache[province] = None
    return cache[province]


def _normalize_name(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("（", "(").replace("）", ")")
    text = text.replace("【", "[").replace("】", "]")
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[(){}\[\]<>《》\"“”'‘’：:，,、。；;!！?？/\\|_\-—·]", "", text)
    return text


def _name_similarity(left: str, right: str) -> float:
    left_norm = _normalize_name(left)
    right_norm = _normalize_name(right)
    if not left_norm or not right_norm:
        return 1.0
    if left_norm == right_norm:
        return 1.0
    if left_norm in right_norm or right_norm in left_norm:
        return 0.95
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def iter_answer_pairs(data: dict):
    for item_seq, item in enumerate(data.get("items", []), start=1):
        quota_ids = item.get("quota_ids", []) or []
        quota_names = item.get("quota_names", []) or []
        bill_text = str(item.get("bill_text") or "")[:120]
        item_id = item.get("id")
        item_key = item_id if item_id is not None else f"seq:{item_seq}"
        for answer_seq, quota_id in enumerate(quota_ids, start=1):
            quota_name = quota_names[answer_seq - 1] if answer_seq - 1 < len(quota_names) else ""
            yield {
                "item_seq": item_seq,
                "item_id": item_id,
                "item_key": item_key,
                "answer_seq": answer_seq,
                "bill_text": bill_text,
                "quota_id": str(quota_id or "").strip(),
                "quota_name": str(quota_name or "").strip(),
            }


def validate_paper(
    path: Path,
    quota_cache: dict[str, dict[str, str] | None],
    name_mismatch_threshold: float,
) -> tuple[dict, list[dict]]:
    data = load_paper_data(path)
    province = data.get("province") or data.get("paper_name") or ""
    total_items = len(data.get("items", []))
    total_pairs = sum(len(item.get("quota_ids", []) or []) for item in data.get("items", []))

    summary = {
        "paper": path.name,
        "province": province,
        "total_items": total_items,
        "total_pairs": total_pairs,
        "issues": 0,
        "missing_id_pairs": 0,
        "name_mismatch_pairs": 0,
        "affected_items": 0,
    }

    if not province:
        problem = {
            "paper": path.name,
            "province": "",
            "item_key": None,
            "bill_text": "",
            "quota_id": "",
            "reason": "missing_province",
        }
        summary["issues"] = 1
        return summary, [problem]

    quota_rows = load_quota_rows(province, quota_cache)
    if quota_rows is None:
        problem = {
            "paper": path.name,
            "province": province,
            "item_key": None,
            "bill_text": "",
            "quota_id": "",
            "reason": "quota_db_unavailable",
        }
        summary["issues"] = 1
        return summary, [problem]

    problems: list[dict] = []
    affected_items: set[str] = set()

    for pair in iter_answer_pairs(data):
        quota_id = pair["quota_id"]
        if not quota_id:
            continue
        if quota_id not in quota_rows:
            problems.append({
                **pair,
                "paper": path.name,
                "province": province,
                "reason": "quota_id_not_found",
            })
            affected_items.add(pair["item_key"])
            continue

        quota_name = pair["quota_name"]
        db_name = quota_rows.get(quota_id, "")
        similarity = _name_similarity(quota_name, db_name)
        if quota_name and db_name and similarity < name_mismatch_threshold:
            problems.append({
                **pair,
                "paper": path.name,
                "province": province,
                "db_name": db_name[:120],
                "similarity": round(similarity, 3),
                "reason": "quota_id_name_mismatch",
            })
            affected_items.add(pair["item_key"])

    summary["issues"] = len(problems)
    summary["missing_id_pairs"] = sum(1 for p in problems if p["reason"] == "quota_id_not_found")
    summary["name_mismatch_pairs"] = sum(1 for p in problems if p["reason"] == "quota_id_name_mismatch")
    summary["affected_items"] = len(affected_items)
    return summary, problems


def prune_invalid_ids(
    path: Path, quota_cache: dict[str, dict[str, str] | None]
) -> tuple[int, int]:
    """Drop invalid IDs when the item still has at least one valid answer left."""
    data = load_paper_data(path)
    province = data.get("province") or data.get("paper_name") or ""
    if not province:
        return 0, 0

    quota_rows = load_quota_rows(province, quota_cache)
    if quota_rows is None:
        return 0, 0

    fixed_items = 0
    pruned_ids = 0
    changed = False

    for item in data.get("items", []):
        quota_ids = item.get("quota_ids", []) or []
        quota_names = item.get("quota_names", []) or []
        if not quota_ids:
            continue

        keep_pairs: list[tuple[str, str]] = []
        removed = 0
        for idx, quota_id in enumerate(quota_ids):
            quota_id = str(quota_id or "").strip()
            quota_name = quota_names[idx] if idx < len(quota_names) else ""
            if quota_id in quota_rows:
                keep_pairs.append((quota_id, quota_name))
            else:
                removed += 1

        if removed == 0 or not keep_pairs:
            continue

        item["quota_ids"] = [quota_id for quota_id, _ in keep_pairs]
        item["quota_names"] = [quota_name for _, quota_name in keep_pairs]
        fixed_items += 1
        pruned_ids += removed
        changed = True

    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    return fixed_items, pruned_ids


def classify_problems(
    summary: dict,
    problems: list[dict],
    hard_similarity_threshold: float,
) -> dict:
    hard_pairs = 0
    medium_pairs = 0
    hard_items: set[str] = set()
    medium_items: set[str] = set()

    for problem in problems:
        if problem["reason"] == "quota_id_not_found":
            hard_pairs += 1
            if problem.get("item_key") is not None:
                hard_items.add(problem["item_key"])
            continue

        if problem["reason"] != "quota_id_name_mismatch":
            continue

        similarity = float(problem.get("similarity", 1.0))
        if similarity < hard_similarity_threshold:
            hard_pairs += 1
            hard_items.add(problem["item_key"])
        else:
            medium_pairs += 1
            medium_items.add(problem["item_key"])

    total_items = max(int(summary.get("total_items", 0)), 1)
    total_pairs = max(int(summary.get("total_pairs", 0)), 1)
    return {
        "hard_pairs": hard_pairs,
        "hard_items": len(hard_items),
        "hard_item_rate": round(len(hard_items) / total_items, 4),
        "hard_pair_rate": round(hard_pairs / total_pairs, 4),
        "medium_pairs": medium_pairs,
        "medium_items": len(medium_items),
        "medium_item_rate": round(len(medium_items) / total_items, 4),
    }


def build_report(
    papers: list[Path],
    quota_cache: dict[str, dict[str, str] | None],
    name_mismatch_threshold: float,
    hard_similarity_threshold: float,
) -> dict:
    paper_rows = []
    all_problems = []

    for paper in papers:
        summary, problems = validate_paper(
            paper,
            quota_cache,
            name_mismatch_threshold=name_mismatch_threshold,
        )
        summary.update(classify_problems(summary, problems, hard_similarity_threshold))
        paper_rows.append(summary)
        all_problems.extend(problems)

    paper_rows.sort(
        key=lambda row: (
            row.get("hard_item_rate", 0),
            row.get("hard_pairs", 0),
            row.get("issues", 0),
        ),
        reverse=True,
    )

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "paper_count": len(papers),
        "name_mismatch_threshold": name_mismatch_threshold,
        "hard_similarity_threshold": hard_similarity_threshold,
        "papers": paper_rows,
        "problems": all_problems,
    }


def build_overrides(
    report: dict,
    skip_hard_item_rate: float,
    skip_hard_items: int,
    skip_hard_pair_rate: float,
) -> dict:
    disabled = {}
    for row in report.get("papers", []):
        hard_items = int(row.get("hard_items", 0))
        hard_item_rate = float(row.get("hard_item_rate", 0))
        hard_pair_rate = float(row.get("hard_pair_rate", 0))
        if hard_items < skip_hard_items:
            continue
        if hard_item_rate < skip_hard_item_rate and hard_pair_rate < skip_hard_pair_rate:
            continue

        disabled[row["paper"]] = {
            "reason": "integrity_hard_mismatch",
            "province": row.get("province", ""),
            "total_items": row.get("total_items", 0),
            "total_pairs": row.get("total_pairs", 0),
            "issues": row.get("issues", 0),
            "hard_items": hard_items,
            "hard_pairs": row.get("hard_pairs", 0),
            "hard_item_rate": hard_item_rate,
            "hard_pair_rate": hard_pair_rate,
            "medium_items": row.get("medium_items", 0),
            "medium_pairs": row.get("medium_pairs", 0),
        }

    return {
        "version": 1,
        "generated_at": report.get("generated_at"),
        "source": "tools/check_benchmark_integrity.py",
        "skip_policy": {
            "skip_hard_item_rate": skip_hard_item_rate,
            "skip_hard_items": skip_hard_items,
            "skip_hard_pair_rate": skip_hard_pair_rate,
            "name_mismatch_threshold": report.get("name_mismatch_threshold"),
            "hard_similarity_threshold": report.get("hard_similarity_threshold"),
        },
        "disabled_papers": disabled,
    }


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def print_summary(report: dict, limit: int) -> None:
    papers = report.get("papers", [])
    problems = report.get("problems", [])

    print("=" * 90)
    print("Benchmark integrity report")
    print("=" * 90)
    print(f"papers_checked: {report.get('paper_count', 0)}")
    print(f"issues_found:   {len(problems)}")

    by_reason = Counter(problem["reason"] for problem in problems)
    if by_reason:
        print("\nBy reason:")
        for reason, count in by_reason.most_common():
            print(f"  {reason}: {count}")

    bad_rows = [row for row in papers if row.get("issues", 0) > 0]
    if bad_rows:
        print("\nTop papers:")
        for row in bad_rows[:20]:
            print(
                f"  {row['paper']}: issues={row['issues']} "
                f"hard_items={row.get('hard_items', 0)}/{row.get('total_items', 0)} "
                f"({row.get('hard_item_rate', 0):.1%}) "
                f"hard_pairs={row.get('hard_pairs', 0)}/{row.get('total_pairs', 0)}"
            )

    if problems:
        print("\nExamples:")
        for problem in problems[:limit]:
            if problem["reason"] == "quota_id_name_mismatch":
                print(
                    f"  [{problem['paper']}] {problem['quota_id']} "
                    f"| sim={problem.get('similarity')} "
                    f"| paper={problem.get('quota_name', '')} "
                    f"| db={problem.get('db_name', '')}"
                )
            else:
                print(
                    f"  [{problem['paper']}] {problem.get('quota_id', '')} "
                    f"| item={problem.get('item_key')} "
                    f"| {problem.get('bill_text', '')}"
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit benchmark paper integrity.")
    parser.add_argument("--paper", help="Only check papers whose filename contains this text")
    parser.add_argument("--limit", type=int, default=50, help="Max examples to print")
    parser.add_argument(
        "--name-mismatch-threshold",
        type=float,
        default=0.45,
        help="Flag quota_id/name pairs below this similarity threshold",
    )
    parser.add_argument(
        "--hard-similarity-threshold",
        type=float,
        default=0.20,
        help="Treat mismatches below this similarity threshold as hard errors",
    )
    parser.add_argument(
        "--skip-hard-item-rate",
        type=float,
        default=0.50,
        help="Disable papers whose hard-error item rate reaches this threshold",
    )
    parser.add_argument(
        "--skip-hard-items",
        type=int,
        default=10,
        help="Disable papers only when they also have at least this many hard-error items",
    )
    parser.add_argument(
        "--skip-hard-pair-rate",
        type=float,
        default=0.50,
        help="Alternate disable threshold based on answer-pair hard error rate",
    )
    parser.add_argument(
        "--report-out",
        help=f"Write a full report JSON (default path: {DEFAULT_REPORT_PATH})",
    )
    parser.add_argument(
        "--override-out",
        help=f"Write disabled-paper overrides JSON (default path: {DEFAULT_OVERRIDE_PATH})",
    )
    parser.add_argument(
        "--write-default-files",
        action="store_true",
        help="Write both the default report and default overrides files",
    )
    parser.add_argument(
        "--fix-prune",
        action="store_true",
        help="Prune invalid IDs only when the item still has other valid answers left",
    )
    args = parser.parse_args()

    quota_cache: dict[str, dict[str, str] | None] = {}
    papers = iter_papers(args.paper)
    if not papers:
        print("No benchmark papers matched.")
        return 1

    fixed_papers = 0
    fixed_items = 0
    pruned_ids = 0
    if args.fix_prune:
        for paper in papers:
            paper_fixed_items, paper_pruned_ids = prune_invalid_ids(paper, quota_cache)
            if paper_fixed_items > 0:
                fixed_papers += 1
                fixed_items += paper_fixed_items
                pruned_ids += paper_pruned_ids

    report = build_report(
        papers,
        quota_cache,
        name_mismatch_threshold=max(0.0, min(args.name_mismatch_threshold, 1.0)),
        hard_similarity_threshold=max(0.0, min(args.hard_similarity_threshold, 1.0)),
    )
    print_summary(report, args.limit)

    if args.fix_prune:
        print("\nPrune summary:")
        print(f"  papers_fixed: {fixed_papers}")
        print(f"  items_fixed:  {fixed_items}")
        print(f"  ids_pruned:   {pruned_ids}")

    report_out = Path(args.report_out) if args.report_out else None
    override_out = Path(args.override_out) if args.override_out else None
    if args.write_default_files:
        report_out = DEFAULT_REPORT_PATH
        override_out = DEFAULT_OVERRIDE_PATH

    if report_out:
        save_json(report_out, report)
        print(f"\n[OK] report written: {report_out}")

    if override_out:
        overrides = build_overrides(
            report,
            skip_hard_item_rate=max(0.0, min(args.skip_hard_item_rate, 1.0)),
            skip_hard_items=max(1, args.skip_hard_items),
            skip_hard_pair_rate=max(0.0, min(args.skip_hard_pair_rate, 1.0)),
        )
        save_json(override_out, overrides)
        print(
            f"[OK] overrides written: {override_out} "
            f"(disabled={len(overrides.get('disabled_papers', {}))})"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
