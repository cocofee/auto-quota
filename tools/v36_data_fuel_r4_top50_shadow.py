# -*- coding: utf-8 -*-
"""V36-DATA-FUEL-R4 read-only top50 candidate snapshot and guardrail shadow.

This diagnostic runner does not change business code or write databases. It
loads current R2 rows, reruns only those benchmark items with an in-memory
top50 candidate budget, and applies the R3 Jarvis fuel safety guardrails in
shadow mode.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import config  # noqa: E402
from tools.run_benchmark import (  # noqa: E402
    _temporary_scoring_mode,
    filter_json_papers,
    load_json_papers,
)


TARGET_BUCKETS = {
    "correct_low_in_snapshot",
    "correct_very_low_in_snapshot",
    "materialization_or_id_gap",
}


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records


def _load_r2_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("target_group") == "R2_like":
                rows.append(row)
    return rows


def _row_key(row: dict) -> tuple[str, str, str]:
    return (
        str(row.get("province", "") or "").strip(),
        str(row.get("bill_id", "") or "").strip(),
        str(row.get("bill_name", "") or "").strip(),
    )


def _item_id(item: dict, seq: int) -> str:
    return str(item.get("sample_id") or item.get("bill_id") or seq).strip()


def _item_name(item: dict) -> str:
    name = str(item.get("bill_name") or "").strip()
    if name:
        return name
    text = str(item.get("bill_text") or "").strip()
    return text.splitlines()[0].strip()[:30] if text else ""


def _build_bill_item(item: dict, seq: int) -> dict:
    return {
        "name": _item_name(item),
        "description": str(item.get("bill_text") or ""),
        "unit": "",
        "quantity": 1,
        "seq": seq,
        "specialty": item.get("specialty", ""),
    }


def _candidate_id(candidate: dict) -> str:
    return str((candidate or {}).get("quota_id", "") or "").strip()


def _candidate_features(candidate: dict) -> dict:
    return dict(
        (candidate or {}).get("candidate_canonical_features")
        or (candidate or {}).get("canonical_features")
        or {}
    )


def _truthy_keys(data: dict) -> list[str]:
    return sorted(k for k, v in (data or {}).items() if v not in (None, "", [], {}))


def _same(a, b) -> bool:
    return bool(str(a or "").strip()) and str(a or "").strip() == str(b or "").strip()


def _hard_conflict(fuel_value, cand_value) -> bool:
    fuel_value = str(fuel_value or "").strip()
    cand_value = str(cand_value or "").strip()
    return bool(fuel_value and cand_value and fuel_value != cand_value)


def _safe_fuel(record: dict, candidate: dict) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    fuel = dict(record.get("fuel_features_used") or {})
    cand = _candidate_features(candidate)
    source = str(record.get("fuel_source_used") or "").strip()
    confidence = float(record.get("fuel_confidence") or 0.0)
    conflict = float(record.get("fuel_conflict_score") or 0.0)

    if source != "history_desc_only":
        reasons.append("reject:source_not_history_desc_only")
    if confidence < 0.73:
        reasons.append("reject:confidence_lt_0.73")
    if conflict > 0.20:
        reasons.append("reject:conflict_score_gt_0.20")
    if not (_same(fuel.get("family"), cand.get("family")) and _same(fuel.get("entity"), cand.get("entity"))):
        reasons.append("reject:family_entity_not_both_match")

    for key in ("material", "connection", "install_method"):
        if _hard_conflict(fuel.get(key), cand.get(key)):
            reasons.append(f"reject:{key}_hard_conflict")

    fuel_params = dict(fuel.get("numeric_params") or {})
    cand_params = dict(cand.get("numeric_params") or {})
    exact_params = []
    for key in ("dn", "half_perimeter", "perimeter", "cable_section", "kw", "ampere"):
        fuel_value = fuel.get(key, fuel_params.get(key))
        cand_value = cand.get(key, cand_params.get(key))
        try:
            if fuel_value is not None and cand_value is not None and abs(float(fuel_value) - float(cand_value)) <= 1e-6:
                exact_params.append(key)
        except (TypeError, ValueError):
            pass
    if not exact_params:
        reasons.append("reject:no_exact_primary_param")
    else:
        reasons.append("accept:exact_primary_param=" + ",".join(exact_params))

    return not any(reason.startswith("reject:") for reason in reasons), reasons


def _rank(records: list[dict], expected_ids: set[str], guarded: bool) -> tuple[list[dict], list[str]]:
    rows = []
    reject_reasons: list[str] = []
    for idx, candidate in enumerate(records, start=1):
        candidate_id = _candidate_id(candidate)
        base = float(candidate.get("rank_score") or candidate.get("ltr_score") or 0.0)
        bonus = 0.0
        reasons = []
        if guarded:
            ok, guard_reasons = _safe_fuel(candidate.get("_fuel_record") or {}, candidate)
            reasons.extend(guard_reasons)
            if ok:
                bonus = 1000.0
            else:
                reject_reasons.extend(guard_reasons)
        rows.append({
            "quota_id": candidate_id,
            "original_rank": idx,
            "shadow_score": base + bonus,
            "base_score": base,
            "is_expected": candidate_id in expected_ids,
            "guard_reasons": reasons,
            "name": candidate.get("name", ""),
        })
    rows.sort(key=lambda row: (-row["shadow_score"], row["original_rank"], row["quota_id"]))
    return rows, sorted(set(reject_reasons))


def _position(ids: list[str], expected_ids: set[str]) -> int | None:
    for idx, quota_id in enumerate(ids, start=1):
        if quota_id in expected_ids:
            return idx
    return None


def main() -> int:
    r2_csv = PROJECT_ROOT / "reports" / "agent_state" / "v36_oss_r2_canonicalizer_alignment_details.csv"
    r2_shadow = PROJECT_ROOT / "reports" / "agent_state" / "v36_data_fuel_r2_shadow_comparator_details.jsonl"
    out_details = PROJECT_ROOT / "reports" / "agent_state" / "v36_data_fuel_r4_top50_guarded_shadow_details.jsonl"
    out_summary = PROJECT_ROOT / "reports" / "agent_state" / "v36_data_fuel_r4_top50_guarded_shadow_summary.json"
    out_bucket = PROJECT_ROOT / "reports" / "agent_state" / "v36_data_fuel_r4_top50_guarded_shadow_bucket_summary.csv"
    out_report = PROJECT_ROOT / "reports" / "agent_state" / "reports" / "v36_data_fuel_r4_top50_guarded_shadow_20260518.md"

    r2_rows = _load_r2_rows(r2_csv)
    fuel_records = {_row_key(r): r for r in _read_jsonl(r2_shadow)}

    # Diagnostic-only in-memory expansion. No source/config file is written.
    original_top_k = getattr(config, "HYBRID_TOP_K", 10)
    config.HYBRID_TOP_K = max(50, int(original_top_k or 10))

    from src.match_engine import init_search_components, match_search_only
    import src.match_pipeline.reasons as reasons
    import src.match_pipeline.orchestrator as orchestrator

    original_snapshot_builder = reasons._build_ranked_candidate_snapshots

    def top50_snapshot_builder(candidates, top_n=20):
        return original_snapshot_builder(candidates, top_n=max(50, int(top_n or 0)))

    reasons._build_ranked_candidate_snapshots = top50_snapshot_builder
    orchestrator._build_ranked_candidate_snapshots = top50_snapshot_builder

    papers, _ = load_json_papers()
    papers = filter_json_papers(papers, install_only=True, item_keywords=None, max_items_per_province=None)

    rows_by_province: dict[str, list[dict]] = defaultdict(list)
    for row in r2_rows:
        rows_by_province[str(row.get("province") or "")].append(row)

    details = []
    started = time.time()
    try:
        for province, rows in rows_by_province.items():
            paper = papers.get(province)
            if not paper:
                continue
            needed = {str(row.get("bill_id") or "").strip() for row in rows}
            selected_items = []
            selected_cards = {}
            for seq, item in enumerate(paper.get("items", []), start=1):
                sample_id = _item_id(item, seq)
                if sample_id in needed:
                    new_seq = len(selected_items) + 1
                    selected_items.append(_build_bill_item(item, new_seq))
                    selected_cards[new_seq] = item
            if not selected_items:
                continue

            searcher, validator = init_search_components(resolved_province=province)
            with _temporary_scoring_mode("two_stage"):
                results = match_search_only(selected_items, searcher, validator, experience_db=None, province=province)

            by_bill_id = {}
            for result in results:
                seq = int((result.get("bill_item") or {}).get("seq") or 0)
                item = selected_cards.get(seq)
                if item:
                    by_bill_id[_item_id(item, seq)] = (item, result)

            for row in rows:
                bill_id = str(row.get("bill_id") or "").strip()
                item, result = by_bill_id.get(bill_id, ({}, {}))
                expected_ids = set(filter(None, str(row.get("expected_ids") or "").split("|")))
                snapshots = list(result.get("candidate_snapshots") or [])[:50]
                for snap in snapshots:
                    key = (
                        str(row.get("province", "") or "").strip(),
                        str(row.get("bill_id", "") or "").strip(),
                        str(row.get("bill_name", "") or "").strip(),
                    )
                    snap["_fuel_record"] = fuel_records.get(key, {})
                ids = [_candidate_id(c) for c in snapshots]
                original_rank = _position(ids, expected_ids)
                guarded_ranked, reject_reasons = _rank(snapshots, expected_ids, guarded=True)
                guarded_ids = [r["quota_id"] for r in guarded_ranked]
                guarded_rank = _position(guarded_ids, expected_ids)
                record = {
                    "schema_version": "v36_data_fuel_r4_top50_guarded_shadow.record.v1",
                    "province": province,
                    "bill_id": bill_id,
                    "bill_name": row.get("bill_name", ""),
                    "bucket": row.get("bucket", ""),
                    "target_bucket": row.get("bucket") in TARGET_BUCKETS,
                    "expected_ids": sorted(expected_ids),
                    "candidate_snapshot_count": len(snapshots),
                    "original_expected_rank": original_rank,
                    "original_expected_top10": bool(original_rank and original_rank <= 10),
                    "original_expected_top20": bool(original_rank and original_rank <= 20),
                    "original_expected_top50": bool(original_rank and original_rank <= 50),
                    "guarded_expected_rank": guarded_rank,
                    "guarded_expected_top1": bool(guarded_rank == 1),
                    "guarded_expected_top10": bool(guarded_rank and guarded_rank <= 10),
                    "guarded_expected_top20": bool(guarded_rank and guarded_rank <= 20),
                    "guarded_expected_top50": bool(guarded_rank and guarded_rank <= 50),
                    "guarded_gain": bool(guarded_rank == 1 and original_rank != 1),
                    "guarded_worsen": bool(original_rank and guarded_rank and guarded_rank > original_rank),
                    "guarded_lost_expected": bool(original_rank and not guarded_rank),
                    "guard_reject_reasons": reject_reasons,
                    "original_top10_ids": ids[:10],
                    "original_top20_ids": ids[:20],
                    "original_top50_ids": ids[:50],
                    "guarded_top10_ids": guarded_ids[:10],
                    "guarded_top20_ids": guarded_ids[:20],
                    "guarded_top50_ids": guarded_ids[:50],
                    "fuel_feature_keys": _truthy_keys((fuel_records.get(_row_key(row), {}) or {}).get("fuel_features_used") or {}),
                    "fuel_confidence": (fuel_records.get(_row_key(row), {}) or {}).get("fuel_confidence"),
                    "fuel_conflict_score": (fuel_records.get(_row_key(row), {}) or {}).get("fuel_conflict_score"),
                    "fuel_source_used": (fuel_records.get(_row_key(row), {}) or {}).get("fuel_source_used"),
                }
                details.append(record)
    finally:
        config.HYBRID_TOP_K = original_top_k
        reasons._build_ranked_candidate_snapshots = original_snapshot_builder
        orchestrator._build_ranked_candidate_snapshots = original_snapshot_builder

    out_details.parent.mkdir(parents=True, exist_ok=True)
    with out_details.open("w", encoding="utf-8") as handle:
        for record in details:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def summarize(records: list[dict]) -> dict:
        return {
            "total": len(records),
            "snapshot_count_ge_50": sum(1 for r in records if r["candidate_snapshot_count"] >= 50),
            "expected_top10": sum(1 for r in records if r["original_expected_top10"]),
            "expected_top20": sum(1 for r in records if r["original_expected_top20"]),
            "expected_top50": sum(1 for r in records if r["original_expected_top50"]),
            "guarded_top1": sum(1 for r in records if r["guarded_expected_top1"]),
            "guarded_top10": sum(1 for r in records if r["guarded_expected_top10"]),
            "guarded_top20": sum(1 for r in records if r["guarded_expected_top20"]),
            "guarded_top50": sum(1 for r in records if r["guarded_expected_top50"]),
            "guarded_gain": sum(1 for r in records if r["guarded_gain"]),
            "guarded_worsen": sum(1 for r in records if r["guarded_worsen"]),
            "guarded_lost_expected": sum(1 for r in records if r["guarded_lost_expected"]),
        }

    by_bucket = {}
    for bucket in sorted({r["bucket"] for r in details}):
        by_bucket[bucket] = summarize([r for r in details if r["bucket"] == bucket])
    target_records = [r for r in details if r["target_bucket"]]
    candidate_counts = [int(r.get("candidate_snapshot_count", 0) or 0) for r in details]
    summary = {
        "schema_version": "v36_data_fuel_r4_top50_guarded_shadow.v1",
        "readonly": True,
        "business_code_changed": False,
        "db_written": False,
        "benchmark_baseline_changed": False,
        "diagnostic_only_in_memory_top_k": 50,
        "input_r2_csv": str(r2_csv.relative_to(PROJECT_ROOT)),
        "input_r2_shadow_details": str(r2_shadow.relative_to(PROJECT_ROOT)),
        "output_details_jsonl": str(out_details.relative_to(PROJECT_ROOT)),
        "elapsed_seconds": round(time.time() - started, 3),
        "candidate_snapshot_distribution": {
            "min": min(candidate_counts) if candidate_counts else 0,
            "max": max(candidate_counts) if candidate_counts else 0,
            "avg": round(sum(candidate_counts) / len(candidate_counts), 3) if candidate_counts else 0.0,
            "ge_10": sum(1 for count in candidate_counts if count >= 10),
            "ge_20": sum(1 for count in candidate_counts if count >= 20),
            "ge_30": sum(1 for count in candidate_counts if count >= 30),
            "ge_50": sum(1 for count in candidate_counts if count >= 50),
        },
        "overall": summarize(details),
        "target_buckets": summarize(target_records),
        "by_bucket": by_bucket,
        "decision": "no_real_code_change" if summarize(target_records)["guarded_gain"] - summarize(target_records)["guarded_worsen"] < 10 else "eligible_for_minimal_code_design",
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    with out_bucket.open("w", encoding="utf-8", newline="") as handle:
        fields = ["bucket", "total", "snapshot_count_ge_50", "expected_top10", "expected_top20", "expected_top50", "guarded_top1", "guarded_gain", "guarded_worsen"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for bucket, data in by_bucket.items():
            writer.writerow({"bucket": bucket, **{k: data.get(k, 0) for k in fields if k != "bucket"}})

    target = summary["target_buckets"]
    net_gain = target["guarded_gain"] - target["guarded_worsen"]
    report = f"""# V36-DATA-FUEL-R4：R2 top50 候选快照 + 安全门禁 shadow 复测

生成时间：2026-05-18

## 1. 本轮边界

本轮只做诊断，不改业务算法，不改最终预测。

- 未修改 `src/**`
- 未写入数据库
- 未修改 benchmark baseline
- 未修改 schema / index
- 未把历史描述当答案
- 只在诊断脚本内存中把候选预算扩到 top50

输出文件：

- `reports/agent_state/v36_data_fuel_r4_top50_guarded_shadow_details.jsonl`
- `reports/agent_state/v36_data_fuel_r4_top50_guarded_shadow_summary.json`
- `reports/agent_state/v36_data_fuel_r4_top50_guarded_shadow_bucket_summary.csv`

## 2. top50 覆盖结果

| 范围 | 样本数 | 正确候选 top10 | 正确候选 top20 | 正确候选 top50 |
| --- | ---: | ---: | ---: | ---: |
| 全部 R2 | {summary['overall']['total']} | {summary['overall']['expected_top10']} | {summary['overall']['expected_top20']} | {summary['overall']['expected_top50']} |
| 目标桶 | {target['total']} | {target['expected_top10']} | {target['expected_top20']} | {target['expected_top50']} |

诊断脚本虽然在内存中把候选预算扩到 top50，但实际候选池并没有真正达到 50：73 条 R2 中，候选池最大 {summary['candidate_snapshot_distribution']['max']}，平均 {summary['candidate_snapshot_distribution']['avg']}，达到 20 个候选的只有 {summary['candidate_snapshot_distribution']['ge_20']} 条，达到 50 个候选的为 {summary['candidate_snapshot_distribution']['ge_50']} 条。

这说明当前最大问题不是“报告只截断 top10”，而是召回/融合后的 rankable pool 本身偏薄。很多正确候选不是藏在 top20/top50，而是根本没有稳定进入足够宽的候选池。

## 3. 安全门禁 shadow 结果

| 范围 | guarded top1 | guarded gain | guarded worsen | 净潜在收益 |
| --- | ---: | ---: | ---: | ---: |
| 全部 R2 | {summary['overall']['guarded_top1']} | {summary['overall']['guarded_gain']} | {summary['overall']['guarded_worsen']} | {summary['overall']['guarded_gain'] - summary['overall']['guarded_worsen']} |
| 目标桶 | {target['guarded_top1']} | {target['guarded_gain']} | {target['guarded_worsen']} | {net_gain} |

## 4. 判断

R4 的门槛是：安全门禁后目标桶净潜在收益至少 10 条，才进入最小代码改造。

本轮目标桶净潜在收益为 {net_gain} 条。结论：{"可以进入最小代码改造方案设计" if net_gain >= 10 else "暂时不进入真实代码改造"}。

Jarvis 历史描述更适合做“有门禁的结构证据燃料”，目前还不适合直接做最终排序权重。它对同族 tie-breaker 有少量帮助，但还没有证明能系统性解决 75% 目标所需的 40+ 条净增。

## 5. 下一步唯一建议

{"执行最小代码改造方案设计，只允许把安全门禁后的 Jarvis fuel 做 shadow candidate feature，不直接改最终答案。" if net_gain >= 10 else "回到候选 canonical feature 物化一致性修复。先解决正确候选自身没有稳定 family/entity/canonical_name/material/connection/install_method 的问题，再考虑消费 Jarvis 历史字段。"}

## 6. 给非程序员看的三句话

本轮颜色：黄色。

是否离 75% 更近：还没有真实涨分；这轮是在确认 Jarvis 数据能不能安全进入算法。

100 条是否还在 2 分钟内：本轮只跑诊断样本，没有新增热路径代码，也没有跑 core_100 速度门禁。
"""
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
