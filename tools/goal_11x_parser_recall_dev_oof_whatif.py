from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.goal_search.national_index import clean_text, extract_signal, tokenize
from src.query_builder import build_quota_query
from src.text_parser import TextParser

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_INPUT = AGENT_STATE / "goal_10x_s6_parser_taxonomy_fix_planning_scope_definition_planning_manifest.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_11x_parser_recall_dev_oof_whatif"


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _quota_db_path(province: str) -> Path | None:
    direct = PROJECT_ROOT / "db" / "provinces" / province / "quota.db"
    if direct.exists():
        return direct
    for root in sorted(PROJECT_ROOT.parent.glob("auto-quota-local-assets*/db/provinces")):
        candidate = root / province / "quota.db"
        if candidate.exists():
            return candidate
    return None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"pragma table_info({table})").fetchall()}


def _load_quota_records(province: str) -> list[dict[str, Any]]:
    db_path = _quota_db_path(province)
    if not db_path:
        return []
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        cols = _table_columns(conn, "quotas")
        optional = [col for col in ("book", "chapter", "specialty", "work_type", "search_text") if col in cols]
        rows = conn.execute(f"select quota_id, name, unit, {', '.join(optional)} from quotas").fetchall()
    finally:
        conn.close()
    records: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        text = " ".join(clean_text(data.get(key)) for key in ("quota_id", "name", "unit", *optional) if clean_text(data.get(key)))
        signal = extract_signal(text)
        records.append(
            {
                "quota_id": clean_text(data.get("quota_id")),
                "name": clean_text(data.get("name")),
                "unit": clean_text(data.get("unit")),
                "book": clean_text(data.get("book") or data.get("chapter") or data.get("specialty")),
                "family": signal.family,
                "tokens": signal.tokens or tokenize(text),
            }
        )
    return records


def _rank_pool(records: list[dict[str, Any]], query_text: str, family_hint: str = "", top_k: int = 80) -> list[dict[str, Any]]:
    query_tokens = set(tokenize(query_text))
    ranked: list[tuple[float, int, dict[str, Any]]] = []
    for idx, record in enumerate(records):
        candidate_tokens = set(record.get("tokens") or [])
        overlap = len(query_tokens & candidate_tokens)
        score = overlap / math.sqrt(max(1, len(candidate_tokens)))
        if family_hint and record.get("family") == family_hint:
            score += 0.08
        if score > 0:
            ranked.append((score, -idx, record))
    ranked.sort(reverse=True)
    pool = [record for _, _, record in ranked[:top_k]]
    if family_hint:
        seen = {record["quota_id"] for record in pool}
        family_records = [record for record in records if record.get("family") == family_hint and record["quota_id"] not in seen]
        pool.extend(family_records[: max(0, top_k - len(pool))])
    return pool[:top_k]


def _pool_summary(pool: list[dict[str, Any]], family: str) -> dict[str, Any]:
    top1 = pool[0] if pool else {}
    families = Counter(clean_text(record.get("family")) or "<empty>" for record in pool)
    return {
        "top1_quota_id": top1.get("quota_id", ""),
        "top1_name": top1.get("name", ""),
        "top1_family": clean_text(top1.get("family")) or "<empty>",
        "target_family_hit": bool(family and any(record.get("family") == family for record in pool)),
        "target_family_count": families.get(family, 0) if family else 0,
        "family_distribution": ";".join(f"{key}:{value}" for key, value in families.most_common(5)),
    }


def _markdown(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    lines = [
        "# 11.1 Parser Recall Dev/OOF What-If",
        "",
        "Dev/OOF-only dry run for the minimal parser/query recall hints.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for key, value in metrics.items():
        lines.append(f"| {key} | {value} |")
    lines.extend(
        [
            "",
            "## Decision",
            "",
            report["decision"],
            "",
            "## Anti-drift",
            "",
            report["anti_drift_conclusion"],
        ]
    )
    return "\n".join(lines) + "\n"


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    current = (
        "当前状态：11.1 parser/query normalization + candidate recall minimal implementation 已执行。"
        f"dev/OOF what-if rows={report['metrics']['evaluated_rows']}，"
        f"positive_candidate_pool_delta={report['metrics']['positive_candidate_pool_delta']}，"
        f"new_loss_count={report['metrics']['new_loss_count']}；"
        "仍未训练、未调参、未改阈值、未上线、未使用 heldout/hard 做选择。"
    )
    next_text = (
        "下一步：11.2 只读复核 11.1 scorecard + loss audit，决定是否 freeze 这个最小 hint set；"
        "若 source dominance 或 loss budget 不过，则停止并回到策略。"
    )
    if "当前状态：11.0 algorithm bottleneck selection plan 已完成。" in text:
        start = text.index("当前状态：11.0 algorithm bottleneck selection plan 已完成。")
        end = text.index("禁止：继续 S2、训练、调参", start)
        replacement = current + "\n" + next_text + "\n"
        text = text[:start] + replacement + text[end:]
    marker = "          <tr>\n            <td>11.0 algorithm bottleneck selection plan</td>"
    row = (
        "          <tr>\n"
        "            <td>11.1 parser recall dev/OOF what-if</td>\n"
        "            <td>最小 parser/query hint 实现与 dev/OOF-only candidate-pool/loss audit。</td>\n"
        "            <td><code>reports/agent_state/goal_11x_parser_recall_dev_oof_whatif_summary.json</code></td>\n"
        "          </tr>\n"
    )
    if marker in text and "goal_11x_parser_recall_dev_oof_whatif_summary.json" not in text:
        text = text.replace(marker, row + marker, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dev-oof-only", action="store_true", required=True)
    parser.add_argument("--emit-loss-audit", action="store_true", required=True)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    args = parser.parse_args()

    started = time.time()
    text_parser = TextParser()
    input_rows = [
        row
        for row in _read_csv(args.input)
        if row.get("planned_fix_lane") == "parser_query_family_hint_planning"
    ]
    quota_cache: dict[str, list[dict[str, Any]]] = {}
    scorecard: list[dict[str, Any]] = []
    source_gain = Counter()
    source_rows = Counter()
    family_gain = Counter()

    for row in input_rows:
        province = row["province"]
        records = quota_cache.setdefault(province, _load_quota_records(province))
        query = row["query"]
        before_family = "" if row.get("query_family_before") == "<empty>" else row.get("query_family_before", "")
        after_query = build_quota_query(text_parser, query, "", specialty="")
        after_family = extract_signal(query).family or extract_signal(after_query).family
        before_pool = _rank_pool(records, query, before_family)
        after_pool = _rank_pool(records, after_query, after_family)
        before_summary = _pool_summary(before_pool, after_family)
        after_summary = _pool_summary(after_pool, after_family)
        positive_delta = int(after_summary["target_family_count"]) - int(before_summary["target_family_count"])
        new_loss = bool(before_summary["target_family_hit"] and not after_summary["target_family_hit"])
        if positive_delta > 0:
            source_gain[row["source_file"]] += positive_delta
            family_gain[after_family or "<empty>"] += positive_delta
        source_rows[row["source_file"]] += 1
        scorecard.append(
            {
                "inventory_id": row["inventory_id"],
                "query": query,
                "province": province,
                "source_file": row["source_file"],
                "source_family": row["source_family"],
                "rank_bucket": row["rank_bucket"],
                "before_query_family": before_family or "<empty>",
                "after_query_family": after_family or "<empty>",
                "before_query": query,
                "after_query": after_query,
                "before_target_family_count_top80": before_summary["target_family_count"],
                "after_target_family_count_top80": after_summary["target_family_count"],
                "candidate_pool_delta": positive_delta,
                "before_top1_family": before_summary["top1_family"],
                "after_top1_family": after_summary["top1_family"],
                "before_top1_name": before_summary["top1_name"],
                "after_top1_name": after_summary["top1_name"],
                "new_loss": str(new_loss).lower(),
                "after_family_distribution": after_summary["family_distribution"],
            }
        )

    positive_rows = [row for row in scorecard if int(row["candidate_pool_delta"]) > 0]
    new_loss_rows = [row for row in scorecard if row["new_loss"] == "true"]
    total_gain = sum(int(row["candidate_pool_delta"]) for row in positive_rows)
    max_source_gain = max(source_gain.values(), default=0)
    max_source_gain_share = round(max_source_gain / total_gain, 6) if total_gain else 0.0
    source_slices = [
        {
            "source_file": source,
            "row_count": source_rows[source],
            "positive_candidate_pool_delta": source_gain[source],
            "gain_share": round(source_gain[source] / total_gain, 6) if total_gain else 0.0,
        }
        for source in sorted(source_rows)
    ]
    loss_audit = [
        {
            "slice": "new_loss",
            "count": len(new_loss_rows),
            "queries": " | ".join(row["query"] for row in new_loss_rows[:10]),
        },
        {
            "slice": "source_dominance",
            "count": max_source_gain,
            "queries": f"max_source_gain_share={max_source_gain_share}",
        },
        {
            "slice": "family_gain",
            "count": total_gain,
            "queries": ";".join(f"{key}:{value}" for key, value in family_gain.most_common()),
        },
    ]
    metrics = {
        "evaluated_rows": len(scorecard),
        "dev_oof_only": True,
        "heldout_rows_used": 0,
        "hard_validation_rows_used": 0,
        "positive_candidate_pool_delta": total_gain,
        "positive_rows": len(positive_rows),
        "new_loss_count": len(new_loss_rows),
        "max_source_gain_share": max_source_gain_share,
        "source_dominance_stop": max_source_gain_share >= 0.8 if total_gain else False,
        "training_allowed": False,
        "threshold_change_allowed": False,
        "goal_searcher_change_allowed": False,
    }
    artifacts = {
        "summary_json": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.json")),
        "summary_md": str(args.output_prefix.with_name(args.output_prefix.name + "_summary.md")),
        "scorecard_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_scorecard.csv")),
        "loss_audit_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_loss_audit.csv")),
        "source_slices_csv": str(args.output_prefix.with_name(args.output_prefix.name + "_source_slices.csv")),
    }
    decision = (
        "Run completed as dev/OOF-only what-if. The parser/query hints improve candidate-family pool coverage "
        "on the S6 parser-empty rows, but freeze is not automatic; 11.2 must review source dominance, losses, "
        "and whether this is enough to freeze the tiny hint set."
    )
    if metrics["source_dominance_stop"]:
        decision = (
            "Stop before freeze: positive candidate-pool delta is source dominated "
            f"(max_source_gain_share={max_source_gain_share}). Keep the implementation diagnostic-only until 11.2 review."
        )
    if len(new_loss_rows) > 0:
        decision = (
            "Stop before freeze: new loss rows appeared in the dev/OOF what-if. Keep the implementation diagnostic-only "
            "until the loss slices are reviewed."
        )
    report = {
        "stage": "Goal LTR v1 / 11.1 parser/query normalization + candidate recall minimal implementation",
        "read_only": False,
        "dev_oof_only": True,
        "implementation_scope": "minimal parser/query family hints, tests, and dev/OOF what-if only",
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": decision,
        "anti_drift_conclusion": (
            "11.1 did not train, tune, change thresholds, edit feature whitelists, use heldout/hard for selection, "
            "edit taxonomy row mappings, wire an online GoalSearcher switch, or claim general Top1 gain. "
            "The output is a bounded dev/OOF candidate-pool and loss audit."
        ),
        "next_stage": {
            "stage": "11.2 scorecard + loss slice freeze gate",
            "default": "do_not_freeze_until_review",
        },
        "elapsed_sec": round(time.time() - started, 3),
    }
    _write_csv(Path(artifacts["scorecard_csv"]), scorecard, list(scorecard[0].keys()) if scorecard else [])
    _write_csv(Path(artifacts["loss_audit_csv"]), loss_audit, list(loss_audit[0].keys()))
    _write_csv(Path(artifacts["source_slices_csv"]), source_slices, list(source_slices[0].keys()) if source_slices else ["source_file", "row_count", "positive_candidate_pool_delta", "gain_share"])
    _write_json(Path(artifacts["summary_json"]), report)
    Path(artifacts["summary_md"]).write_text(_markdown(report), encoding="utf-8")
    _update_dashboard(args.dashboard, report)
    print(json.dumps({"metrics": metrics, "artifacts": artifacts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

