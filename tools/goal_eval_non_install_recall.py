from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import config  # noqa: E402

DEFAULT_DATA_DIR = PROJECT_ROOT / "data" / "goal_search"
DEFAULT_REPORT_JSON = PROJECT_ROOT / "reports" / "agent_state" / "goal_non_install_recall_eval_summary.json"
DEFAULT_REPORT_MD = PROJECT_ROOT / "reports" / "agent_state" / "goal_non_install_recall_eval_summary.md"
DEFAULT_DETAILS_DIR = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_LOCAL_MISSING_CSV = PROJECT_ROOT / "reports" / "agent_state" / "goal_top80_recall_audit_local_missing.csv"

WATERPROOF_TERMS = ("防水", "防潮", "涂膜防水", "卷材防水", "砂浆防水")
WATERPROOF_MATERIAL_TERMS = ("涂膜", "卷材", "砂浆", "聚氨酯", "沥青", "防潮")
JOINT_TERMS = ("变形缝", "施工缝", "止水")
LOCATION_TERMS = ("屋面", "楼地面", "楼（地）面", "地面", "墙面", "桩头")
INSTALL_PROTECT_TERMS = (
    "防水型",
    "按钮",
    "检修盒",
    "防护等级",
    "IP65",
    "室外",
    "配电",
    "控制箱",
    "配电箱",
    "开关",
)


@dataclass
class FeatureGroup:
    group_id: str
    query_family: str = ""
    positive_count: int = 0


@dataclass
class RecallSpec:
    matched: bool
    bucket: str = ""
    subtype: str = ""
    trigger: str = ""
    material_hint: str = ""
    location_hint: str = ""
    protected: bool = False
    protect_reason: str = ""


@dataclass
class QuotaCandidate:
    quota_id: str
    name: str
    unit: str
    book: str
    chapter: str
    text: str
    score: float
    reasons: list[str] = field(default_factory=list)


class ProvinceQuotaLookup:
    def __init__(self, province: str):
        self.province = province
        self.path = Path(config.get_quota_db_path(province))
        self.columns: set[str] | None = None
        self.cache: dict[tuple[str, str, str, str], list[dict[str, str]]] = {}

    def waterproof_candidates(self, spec: RecallSpec, limit: int) -> list[QuotaCandidate]:
        if not spec.matched or spec.protected:
            return []
        rows = self._query_rows(spec)
        candidates: list[QuotaCandidate] = []
        for row in rows:
            candidate = _score_waterproof_candidate(row, spec)
            if candidate is not None:
                candidates.append(candidate)
        candidates.sort(key=lambda item: (-item.score, _book_sort_key(item.book), item.quota_id))
        return candidates[:limit]

    def _query_rows(self, spec: RecallSpec) -> list[dict[str, str]]:
        cache_key = (spec.subtype, spec.trigger, spec.material_hint, spec.location_hint)
        if cache_key in self.cache:
            return self.cache[cache_key]
        if not self.path.exists():
            self.cache[cache_key] = []
            return []

        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            if self.columns is None:
                self.columns = {row["name"] for row in conn.execute("pragma table_info(quotas)").fetchall()}
            optional = ["work_type", "specialty", "chapter", "material", "connection", "book", "search_text"]
            select_cols = ["quota_id", "name", "unit"] + [col for col in optional if col in self.columns]
            searchable_cols = [col for col in ("name", "chapter", "specialty", "search_text") if col in self.columns]
            terms = [spec.trigger] if spec.subtype == "joint" else ["防水", "防潮"]
            clauses: list[str] = []
            params: list[str] = []
            for term in terms:
                term_clauses: list[str] = []
                for col in searchable_cols:
                    term_clauses.append(f"coalesce({col}, '') like ?")
                    params.append(f"%{term}%")
                if term_clauses:
                    clauses.append("(" + " or ".join(term_clauses) + ")")
            where_sql = " or ".join(clauses) if clauses else "1=0"
            rows = conn.execute(f"select {', '.join(select_cols)} from quotas where {where_sql}", params).fetchall()
        finally:
            conn.close()

        result: list[dict[str, str]] = []
        for row in rows:
            data = {key: _clean(row[key]) for key in row.keys()}
            text = " ".join(
                data.get(key, "")
                for key in ("quota_id", "name", "unit", "work_type", "specialty", "chapter", "material", "connection", "search_text")
                if data.get(key)
            )
            data["text"] = text
            data["book"] = data.get("book") or _quota_book(data.get("quota_id", ""))
            result.append(data)
        self.cache[cache_key] = result
        return result


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _clean(value))


def _rate(count: int, total: int) -> float:
    return round(count / total, 6) if total else 0.0


def _quota_book(quota_id: str) -> str:
    qid = _clean(quota_id)
    match = re.match(r"([A-Z]\d+)-", qid, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.match(r"([A-Z])-\d+-", qid, flags=re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.match(r"2-(\d+)-", qid)
    if match:
        return "2"
    match = re.match(r"(\d+)-", qid)
    if match:
        return match.group(1)
    return ""


def _book_sort_key(book: str) -> tuple[int, str]:
    if book == "9":
        return (0, book)
    if book == "10":
        return (1, book)
    if book == "A1":
        return (2, book)
    return (3, book)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def _expected_ids(row: dict[str, Any]) -> set[str]:
    raw = row.get("expected_ids") or row.get("expected_id") or row.get("quota_id")
    values: list[str] = []
    if isinstance(raw, list):
        values.extend(str(item) for item in raw)
    elif raw:
        values.append(str(raw))
    result: set[str] = set()
    for value in values:
        for part in value.split("|"):
            part = part.strip()
            if part:
                result.add(part)
    return result


def _load_local_missing_query_family(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            group_id = _clean(row.get("group_id"))
            if group_id:
                result[group_id] = _clean(row.get("query_family"))
    return result


def _first_token(text: str, tokens: tuple[str, ...]) -> str:
    return next((token for token in tokens if token in text), "")


def _waterproof_spec(query: str, query_family: str) -> RecallSpec:
    compact = _compact(query)
    if query_family:
        return RecallSpec(matched=False, bucket="waterproof_joint", protected=True, protect_reason="query_family_present")
    protect = _first_token(compact, INSTALL_PROTECT_TERMS)
    if protect:
        return RecallSpec(
            matched=False,
            bucket="waterproof_joint",
            protected=True,
            protect_reason=f"install_context:{protect}",
        )

    joint = _first_token(compact, JOINT_TERMS)
    if joint:
        return RecallSpec(
            matched=True,
            bucket="waterproof_joint",
            subtype="joint",
            trigger=joint,
            location_hint=_first_token(compact, LOCATION_TERMS),
        )

    waterproof = _first_token(compact, WATERPROOF_TERMS)
    if waterproof:
        return RecallSpec(
            matched=True,
            bucket="waterproof_joint",
            subtype="waterproof",
            trigger=waterproof,
            material_hint=_first_token(compact, WATERPROOF_MATERIAL_TERMS),
            location_hint=_first_token(compact, LOCATION_TERMS),
        )

    return RecallSpec(matched=False, bucket="waterproof_joint")


def _score_waterproof_candidate(row: dict[str, str], spec: RecallSpec) -> QuotaCandidate | None:
    text = _compact(row.get("text", ""))
    name = _clean(row.get("name"))
    book = _clean(row.get("book"))
    score = 0.0
    reasons: list[str] = []

    if spec.subtype == "joint":
        if spec.trigger not in text:
            return None
        score += 10.0
        reasons.append(f"joint:{spec.trigger}")
    elif spec.subtype == "waterproof":
        if "防水" not in text and "防潮" not in text:
            return None
        score += 8.0
        reasons.append("waterproof")
        if spec.material_hint and spec.material_hint in text:
            score += 2.0
            reasons.append(f"material:{spec.material_hint}")
    else:
        return None

    if spec.location_hint and spec.location_hint in text:
        score += 1.2
        reasons.append(f"location:{spec.location_hint}")
    if book == "9":
        score += 3.0
        reasons.append("book:9")
    elif book == "10":
        score += 1.0
        reasons.append("book:10")
    if spec.subtype == "waterproof" and any(term in text for term in ("桥面", "隧道", "井", "排水")) and book != "9":
        score -= 1.5
        reasons.append("non_building_context_penalty")

    return QuotaCandidate(
        quota_id=_clean(row.get("quota_id")),
        name=name,
        unit=_clean(row.get("unit")),
        book=book,
        chapter=_clean(row.get("chapter")),
        text=_clean(row.get("text")),
        score=round(score, 6),
        reasons=reasons,
    )


def _candidate_label(candidate: QuotaCandidate) -> str:
    return f"{candidate.quota_id} {candidate.name}".strip()


def _md_table(rows: list[list[object]]) -> str:
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(str(item) for item in rows[0]) + " |",
        "| " + " | ".join("---" for _ in rows[0]) + " |",
    ]
    for row in rows[1:]:
        lines.append("| " + " | ".join(str(item).replace("\n", " ") for item in row) + " |")
    return "\n".join(lines)


def _top_items(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [{"key": key, "count": count} for key, count in counter.most_common(limit)]


def _evaluate_split(
    *,
    split: str,
    data_dir: Path,
    local_missing_query_family: dict[str, str],
    enabled: bool,
    supplement_limit: int,
    details_path: Path,
    lookups: dict[str, ProvinceQuotaLookup],
) -> dict[str, Any]:
    meta_rows = _read_jsonl(data_dir / f"ltr_group_{split}.jsonl")

    total = len(meta_rows)
    baseline_hits = 0
    final_hits = 0
    rescued = 0
    losses = 0
    triggered = 0
    protected = 0
    no_candidate = 0
    added_total = 0
    candidate_total = 0
    reason_counts: Counter[str] = Counter()
    trigger_counts: Counter[str] = Counter()
    details_path.parent.mkdir(parents=True, exist_ok=True)

    with details_path.open("w", encoding="utf-8") as handle:
        for index, meta in enumerate(meta_rows, start=1):
            group_id = _clean(meta.get("group_id"))
            expected = _expected_ids(meta)
            baseline_positive_count = int(float(meta.get("positive_count") or 0))
            baseline_hit = baseline_positive_count > 0
            final_hit = baseline_hit
            baseline_hits += int(baseline_hit)

            province = _clean(meta.get("province"))
            query = _clean(meta.get("query"))
            query_family = local_missing_query_family.get(group_id, "") if not baseline_hit else ""
            spec = _waterproof_spec(query, query_family)
            supplement: list[QuotaCandidate] = []
            added: list[QuotaCandidate] = []
            fallback_reason = ""

            if baseline_hit:
                fallback_reason = "baseline_already_has_expected"
            elif not enabled:
                fallback_reason = "switch_disabled"
            elif spec.protected:
                protected += 1
                fallback_reason = spec.protect_reason
            elif not spec.matched:
                fallback_reason = "no_waterproof_joint_trigger"
            else:
                triggered += 1
                trigger_counts[f"{spec.subtype}:{spec.trigger}"] += 1
                if province not in lookups:
                    lookups[province] = ProvinceQuotaLookup(province)
                try:
                    supplement = lookups[province].waterproof_candidates(spec, supplement_limit)
                except Exception as exc:  # noqa: BLE001
                    supplement = []
                    fallback_reason = f"lookup_error:{exc}"
                if not supplement and not fallback_reason:
                    no_candidate += 1
                    fallback_reason = "no_supplement_candidate"
                candidate_total += len(supplement)
                added = supplement
                added_total += len(added)
                supplement_ids = {candidate.quota_id for candidate in supplement}
                final_hit = bool(expected & supplement_ids)

            final_hits += int(final_hit)
            rescued += int((not baseline_hit) and final_hit)
            losses += int(baseline_hit and not final_hit)
            reason_counts[fallback_reason or "applied"] += 1

            detail = {
                "split": split,
                "group_index": index,
                "group_id": group_id,
                "sample_id": _clean(meta.get("sample_id")),
                "source_file": _clean(meta.get("source_file")),
                "project_name": _clean(meta.get("project_name")),
                "province": province,
                "query": query,
                "query_family": query_family,
                "expected_ids": "|".join(sorted(expected)),
                "baseline_positive_count": baseline_positive_count,
                "baseline_hit_top80": baseline_hit,
                "final_hit_augmented": final_hit,
                "rescued_by_non_install_recall": bool((not baseline_hit) and final_hit),
                "non_install_recall_enabled": enabled,
                "non_install_recall_bucket": "waterproof_joint",
                "non_install_recall_trigger": spec.trigger,
                "non_install_recall_subtype": spec.subtype,
                "non_install_recall_protected": spec.protected,
                "non_install_recall_candidates": len(supplement),
                "non_install_recall_added": len(added),
                "non_install_recall_added_ids": "|".join(candidate.quota_id for candidate in added),
                "non_install_recall_top_added": " || ".join(_candidate_label(candidate) for candidate in added[:5]),
                "non_install_recall_fallback_reason": fallback_reason,
            }
            handle.write(json.dumps(detail, ensure_ascii=False, separators=(",", ":")) + "\n")

    return {
        "split": split,
        "groups": total,
        "enabled": enabled,
        "baseline_top80_hit": baseline_hits,
        "baseline_top80_recall_rate": _rate(baseline_hits, total),
        "final_augmented_hit": final_hits,
        "final_augmented_recall_rate": _rate(final_hits, total),
        "rescued": rescued,
        "losses": losses,
        "triggered": triggered,
        "protected": protected,
        "no_candidate": no_candidate,
        "supplement_candidates_total": candidate_total,
        "supplement_added_total": added_total,
        "fallback_reasons": dict(reason_counts),
        "trigger_counts": dict(trigger_counts),
        "details_jsonl": str(details_path),
    }


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    summaries = report["splits"]
    lines = [
        "# Goal Non-Install Recall Eval",
        "",
        "Stage 3.5 eval-only prototype. It only simulates append-only `waterproof_joint` recall. No production search integration and no ranking change.",
        "",
        "## Inputs",
        "",
        _md_table(
            [
                ["metric", "value"],
                ["non_install_recall_enabled", report["non_install_recall_enabled"]],
                ["bucket", report["bucket"]],
                ["supplement_limit", report["supplement_limit"]],
                ["splits", ", ".join(report["splits_requested"])],
                ["elapsed_sec", report["elapsed_sec"]],
            ]
        ),
        "",
        "## Metrics",
        "",
        _md_table(
            [
                [
                    "split",
                    "baseline_ceiling",
                    "augmented_ceiling",
                    "rescued",
                    "losses",
                    "triggered",
                    "protected",
                    "added",
                ],
                *[
                    [
                        item["split"],
                        item["baseline_top80_recall_rate"],
                        item["final_augmented_recall_rate"],
                        item["rescued"],
                        item["losses"],
                        item["triggered"],
                        item["protected"],
                        item["supplement_added_total"],
                    ]
                    for item in summaries
                ],
            ]
        ),
        "",
        "## Details",
        "",
        _md_table([["split", "details_jsonl"]] + [[item["split"], item["details_jsonl"]] for item in summaries]),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Eval-only append-only non-install recall prototype")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--splits", nargs="+", default=["heldout", "hard"])
    parser.add_argument("--local-missing-csv", default=str(DEFAULT_LOCAL_MISSING_CSV))
    parser.add_argument("--non-install-recall-enabled", action="store_true")
    parser.add_argument("--bucket", default="waterproof_joint", choices=["waterproof_joint"])
    parser.add_argument("--supplement-limit", type=int, default=20)
    parser.add_argument("--report-json", default=str(DEFAULT_REPORT_JSON))
    parser.add_argument("--report-md", default=str(DEFAULT_REPORT_MD))
    parser.add_argument("--details-dir", default=str(DEFAULT_DETAILS_DIR))
    args = parser.parse_args()

    started = time.perf_counter()
    data_dir = Path(args.data_dir)
    local_missing_query_family = _load_local_missing_query_family(Path(args.local_missing_csv))
    lookups: dict[str, ProvinceQuotaLookup] = {}
    summaries: list[dict[str, Any]] = []
    for split in args.splits:
        details_path = Path(args.details_dir) / f"goal_non_install_recall_eval_details_{split}.jsonl"
        summaries.append(
            _evaluate_split(
                split=split,
                data_dir=data_dir,
                local_missing_query_family=local_missing_query_family,
                enabled=bool(args.non_install_recall_enabled),
                supplement_limit=args.supplement_limit,
                details_path=details_path,
                lookups=lookups,
            )
        )

    report = {
        "stage": "Goal LTR v1 / stage 3.5 eval-only non-install recall prototype",
        "read_only": True,
        "no_model_tuning": True,
        "no_ranking_change": True,
        "no_search_integration": True,
        "non_install_recall_enabled": bool(args.non_install_recall_enabled),
        "bucket": args.bucket,
        "supplement_limit": args.supplement_limit,
        "data_dir": str(data_dir),
        "local_missing_csv": args.local_missing_csv,
        "splits_requested": args.splits,
        "province_lookup_count": len(lookups),
        "splits": summaries,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }

    report_json = Path(args.report_json)
    report_json.parent.mkdir(parents=True, exist_ok=True)
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_markdown(Path(args.report_md), report)

    print(
        json.dumps(
            {
                "summary": {
                    "stage": report["stage"],
                    "read_only": True,
                    "non_install_recall_enabled": report["non_install_recall_enabled"],
                    "bucket": args.bucket,
                    "elapsed_sec": report["elapsed_sec"],
                },
                "splits": summaries,
                "artifacts": {
                    "report_json": str(report_json),
                    "report_md": args.report_md,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
