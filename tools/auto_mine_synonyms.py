from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.sqlite import connect as db_connect
from tools.mine_bill_synonyms import SOURCE_DB as BILL_LIBRARY_DB
from tools.mine_bill_synonyms import mine_from_db
from tools.synonym_miner import mine_synonyms as mine_experience_synonyms


DEFAULT_BENCHMARK_REPORT = ROOT / "output" / "diagnostics" / "stage3_top_gaps_report_full.json"
DEFAULT_OUTPUT = ROOT / "output" / "diagnostics" / "auto_synonym_candidates.json"
DEFAULT_TOP = 50

GENERIC_TARGET_TERMS = {
    "套管",
    "法兰",
    "螺栓",
    "软件",
    "填方",
    "接线箱",
    "交换机",
}

TARGET_DETAIL_MARKERS = (
    "厚度",
    "直径",
    "周长",
    "半周长",
    "截面",
    "公称直径",
    "土球",
    "一层",
    "二层",
    "平面",
    "立面",
    "以内",
    "以下",
    "附框",
    "带肋",
    "自粘法",
    "吊筋",
)

TARGET_COMPONENT_TERMS = (
    "垫层",
    "隔振垫",
    "箭头",
)

EXCLUDE_REVIEW_FLAGS = {
    "generic_target",
    "component_target",
    "parametric_target",
    "weak_lexical_overlap",
}


def normalize_term(value: object) -> str:
    return " ".join(str(value or "").replace("\u3000", " ").split())


def load_existing_pairs(paths: Iterable[Path] | None = None) -> set[tuple[str, str]]:
    synonym_paths = list(paths or (
        ROOT / "data" / "engineering_synonyms.json",
        ROOT / "data" / "auto_synonyms.json",
    ))
    existing_pairs: set[tuple[str, str]] = set()

    for path in synonym_paths:
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        for raw_source, raw_targets in data.items():
            if str(raw_source).startswith("_"):
                continue
            source = normalize_term(raw_source)
            if not source:
                continue
            targets = raw_targets if isinstance(raw_targets, list) else [raw_targets]
            for raw_target in targets:
                target = normalize_term(raw_target)
                if source and target:
                    existing_pairs.add((source, target))

    return existing_pairs


def collect_benchmark_candidates_from_report(report: dict, *, min_count: int = 1) -> dict[tuple[str, str], dict]:
    recall_report = dict(report.get("recall_gaps") or {})
    rows = list(recall_report.get("top_missing_synonyms") or [])
    candidates: dict[tuple[str, str], dict] = {}

    for row in rows:
        source = normalize_term(row.get("source_term"))
        target = normalize_term(row.get("target_term"))
        if not source or not target:
            key_text = normalize_term(row.get("key"))
            if "->" in key_text:
                left, right = key_text.split("->", 1)
                source = source or normalize_term(left)
                target = target or normalize_term(right)
        count = int(row.get("count") or 0)
        if count < min_count or not source or not target or source == target:
            continue

        pair = (source, target)
        candidates[pair] = {
            "source_term": source,
            "target_term": target,
            "sources": {"benchmark"},
            "benchmark_count": count,
            "examples": list(row.get("examples") or [])[:3],
            "provinces": sorted(set(row.get("provinces") or [])),
        }

    return candidates


def collect_mapping_candidates(mapping: dict, *, source_name: str) -> dict[tuple[str, str], dict]:
    candidates: dict[tuple[str, str], dict] = {}

    for raw_source, raw_targets in dict(mapping or {}).items():
        source = normalize_term(raw_source)
        targets = raw_targets if isinstance(raw_targets, list) else [raw_targets]
        for raw_target in targets:
            target = normalize_term(raw_target)
            if not source or not target or source == target:
                continue
            pair = (source, target)
            candidate = candidates.setdefault(
                pair,
                {
                    "source_term": source,
                    "target_term": target,
                    "sources": set(),
                    "benchmark_count": 0,
                    "examples": [],
                    "provinces": [],
                },
            )
            candidate["sources"].add(source_name)

    return candidates


def build_risk_flags(source_term: str, target_term: str) -> list[str]:
    flags: list[str] = []

    material_conflicts = (
        ("不锈钢", "碳钢"),
        ("铜", "铝"),
        ("镀锌", "不锈钢"),
    )
    for left, right in material_conflicts:
        if (left in source_term and right in target_term) or (right in source_term and left in target_term):
            flags.append("material_conflict")
            break

    for long_term, short_term in ((source_term, target_term), (target_term, source_term)):
        if long_term.endswith(short_term) and len(long_term) == len(short_term) + 1:
            extra = long_term[:1]
            if extra in {"口", "位", "台", "樘", "只", "个", "套", "路"}:
                flags.append("truncated_prefix_noise")
                break
        if long_term.startswith(short_term) and len(long_term) == len(short_term) + 1:
            extra = long_term[-1:]
            if extra in {"口", "位", "台", "樘", "只", "个", "套", "路"}:
                flags.append("truncated_suffix_noise")
                break

    if target_term in GENERIC_TARGET_TERMS and source_term != target_term:
        flags.append("generic_target")

    if any(marker in target_term for marker in TARGET_DETAIL_MARKERS) and not any(marker in source_term for marker in TARGET_DETAIL_MARKERS):
        flags.append("parametric_target")

    if any(term in target_term for term in TARGET_COMPONENT_TERMS) and not any(term in source_term for term in TARGET_COMPONENT_TERMS):
        flags.append("component_target")

    source_chars = {ch for ch in source_term if "\u4e00" <= ch <= "\u9fff"}
    target_chars = {ch for ch in target_term if "\u4e00" <= ch <= "\u9fff"}
    shared_chars = source_chars & target_chars
    if source_term not in target_term and target_term not in source_term and len(shared_chars) < 2:
        flags.append("weak_lexical_overlap")

    return sorted(set(flags))


def score_candidate(candidate: dict) -> int:
    sources = set(candidate.get("sources") or set())
    benchmark_count = int(candidate.get("benchmark_count") or 0)
    score = len(sources) * 1000
    score += benchmark_count * 50
    if "benchmark" in sources:
        score += 10
    if "experience" in sources:
        score += 30
    if "bill_library" in sources:
        score += 20
    score -= int(candidate.get("risk_count") or 0) * 200
    return score


def merge_candidate_maps(
    candidate_maps: Iterable[dict[tuple[str, str], dict]],
    *,
    existing_pairs: set[tuple[str, str]] | None = None,
) -> list[dict]:
    existing_pairs = existing_pairs or set()
    merged: dict[tuple[str, str], dict] = {}

    for candidate_map in candidate_maps:
        for pair, row in dict(candidate_map or {}).items():
            source, target = pair
            if len(source) < 2 or len(target) < 2 or pair in existing_pairs:
                continue

            candidate = merged.setdefault(
                pair,
                {
                    "source_term": source,
                    "target_term": target,
                    "sources": set(),
                    "benchmark_count": 0,
                    "examples": [],
                    "provinces": [],
                },
            )
            candidate["sources"].update(set(row.get("sources") or set()))
            candidate["benchmark_count"] += int(row.get("benchmark_count") or 0)
            if not candidate["examples"]:
                candidate["examples"] = list(row.get("examples") or [])[:3]
            if row.get("provinces"):
                candidate["provinces"] = sorted(set(candidate["provinces"]) | set(row.get("provinces") or []))

    ranked: list[dict] = []
    for candidate in merged.values():
        candidate["sources"] = sorted(candidate["sources"])
        candidate["source_count"] = len(candidate["sources"])
        candidate["risk_flags"] = build_risk_flags(candidate["source_term"], candidate["target_term"])
        candidate["risk_count"] = len(candidate["risk_flags"])
        candidate["exclude_from_review"] = any(flag in EXCLUDE_REVIEW_FLAGS for flag in candidate["risk_flags"])
        candidate["score"] = score_candidate(candidate)
        if candidate["source_count"] >= 2 and candidate["risk_count"] == 0:
            candidate["confidence_band"] = "high"
        elif candidate["benchmark_count"] >= 5 and candidate["risk_count"] <= 1 and not candidate["exclude_from_review"]:
            candidate["confidence_band"] = "medium"
        else:
            candidate["confidence_band"] = "low"
        candidate["suggested_fix"] = f"add_synonym:{candidate['source_term']}->{candidate['target_term']}"
        ranked.append(candidate)

    ranked.sort(
        key=lambda item: (
            -int(item["source_count"]),
            int(item["risk_count"]),
            int(bool(item["exclude_from_review"])),
            -int(item["benchmark_count"]),
            -int(item["score"]),
            item["source_term"],
            item["target_term"],
        )
    )
    return ranked


def build_candidate_report(
    *,
    benchmark_report: dict | None,
    experience_mapping: dict | None,
    bill_mapping: dict | None,
    existing_pairs: set[tuple[str, str]] | None = None,
    benchmark_min_count: int = 1,
    top_n: int = DEFAULT_TOP,
    source_errors: dict[str, str] | None = None,
) -> dict:
    benchmark_candidates = collect_benchmark_candidates_from_report(
        benchmark_report or {},
        min_count=benchmark_min_count,
    )
    experience_candidates = collect_mapping_candidates(
        experience_mapping or {},
        source_name="experience",
    )
    bill_candidates = collect_mapping_candidates(
        bill_mapping or {},
        source_name="bill_library",
    )

    merged = merge_candidate_maps(
        [benchmark_candidates, experience_candidates, bill_candidates],
        existing_pairs=existing_pairs,
    )

    high_confidence = [row for row in merged if row["confidence_band"] == "high"][:top_n]
    benchmark_priority = [
        row for row in merged
        if (
            "benchmark" in row["sources"]
            and row["benchmark_count"] >= benchmark_min_count
            and not row["exclude_from_review"]
            and row["confidence_band"] in {"medium", "high"}
        )
    ][:top_n]

    return {
        "meta": {
            "top_n": top_n,
            "benchmark_min_count": benchmark_min_count,
            "existing_pair_count": len(existing_pairs or set()),
            "source_errors": dict(source_errors or {}),
            "candidate_pool_size": len(merged),
            "source_pair_counts": {
                "benchmark": len(benchmark_candidates),
                "experience": len(experience_candidates),
                "bill_library": len(bill_candidates),
            },
        },
        "candidates": merged[:top_n],
        "high_confidence_candidates": high_confidence,
        "benchmark_priority_candidates": benchmark_priority,
    }


def _safe_load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_auto_mining(
    *,
    benchmark_report_path: Path,
    benchmark_min_count: int,
    top_n: int,
    include_experience: bool,
    include_bill_library: bool,
    experience_min_group: int,
    experience_min_count: int,
    bill_min_freq: int,
) -> dict:
    source_errors: dict[str, str] = {}
    benchmark_report = _safe_load_json(benchmark_report_path)
    existing_pairs = load_existing_pairs()

    experience_mapping: dict | None = {}
    if include_experience:
        try:
            experience_mapping = mine_experience_synonyms(
                min_group_size=experience_min_group,
                min_occurrence=experience_min_count,
            )
        except Exception as exc:  # pragma: no cover
            source_errors["experience"] = str(exc)
            experience_mapping = {}

    bill_mapping: dict | None = {}
    if include_bill_library:
        try:
            conn = db_connect(BILL_LIBRARY_DB)
            try:
                bill_mapping, _stats = mine_from_db(conn, min_freq=bill_min_freq)
            finally:
                conn.close()
        except Exception as exc:  # pragma: no cover
            source_errors["bill_library"] = str(exc)
            bill_mapping = {}

    return build_candidate_report(
        benchmark_report=benchmark_report,
        experience_mapping=experience_mapping,
        bill_mapping=bill_mapping,
        existing_pairs=existing_pairs,
        benchmark_min_count=benchmark_min_count,
        top_n=top_n,
        source_errors=source_errors,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Auto mine synonym candidates from benchmark, experience DB and bill library.")
    parser.add_argument("--benchmark-report", default=str(DEFAULT_BENCHMARK_REPORT), help="Recall-gap report JSON path.")
    parser.add_argument(
        "--output",
        nargs="?",
        const=str(DEFAULT_OUTPUT),
        default=str(DEFAULT_OUTPUT),
        help="Output JSON path. Passing bare --output falls back to the default diagnostics path.",
    )
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="Top candidate count to keep.")
    parser.add_argument("--benchmark-min-count", type=int, default=2, help="Minimum benchmark count to keep a pair.")
    parser.add_argument("--experience-min-group", type=int, default=3, help="Minimum group size for experience synonym mining.")
    parser.add_argument("--experience-min-count", type=int, default=2, help="Minimum pair count for experience synonym mining.")
    parser.add_argument("--bill-min-freq", type=int, default=2, help="Minimum bill-name frequency for bill library mining.")
    parser.add_argument("--no-experience", action="store_true", help="Skip experience DB mining.")
    parser.add_argument("--no-bill-library", action="store_true", help="Skip bill library mining.")
    parser.add_argument("--preview", action="store_true", help="Print top candidates without writing file.")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()

    report = run_auto_mining(
        benchmark_report_path=Path(args.benchmark_report),
        benchmark_min_count=args.benchmark_min_count,
        top_n=args.top,
        include_experience=not args.no_experience,
        include_bill_library=not args.no_bill_library,
        experience_min_group=args.experience_min_group,
        experience_min_count=args.experience_min_count,
        bill_min_freq=args.bill_min_freq,
    )

    print(f"candidate_pool_size: {report['meta']['candidate_pool_size']}")
    print(f"high_confidence_count: {len(report['high_confidence_candidates'])}")
    print(f"benchmark_priority_count: {len(report['benchmark_priority_candidates'])}")
    print("top_candidates:")
    for row in report["candidates"][:10]:
        sources = ",".join(row["sources"])
        risks = ",".join(row["risk_flags"]) if row["risk_flags"] else "-"
        print(
            f"  band={row['confidence_band']:<6} score={row['score']:>4} count={row['benchmark_count']:>2} "
            f"sources={sources:<28} risks={risks:<24} {row['source_term']} -> {row['target_term']}"
        )

    if report["meta"]["source_errors"]:
        print("source_errors:")
        for source_name, message in report["meta"]["source_errors"].items():
            print(f"  {source_name}: {message}")

    if not args.preview:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report_written: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
