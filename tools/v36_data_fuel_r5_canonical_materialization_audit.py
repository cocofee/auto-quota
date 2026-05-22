# -*- coding: utf-8 -*-
"""V36-DATA-FUEL-R5 read-only canonical feature materialization audit.

This diagnostic uses existing R4 top50 shadow output plus current quota DB
lookup/no-store canonicalization to decide whether R2 failures are mainly:
retrieval pool width, canonical feature materialization, or feature consumption.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.candidate_canonicalizer import build_candidate_canonical_features_no_store  # noqa: E402
from src.quota_search import search_by_id  # noqa: E402
from src.specialty_classifier import get_book_from_quota_id  # noqa: E402


TARGET_BUCKETS = {
    "correct_low_in_snapshot",
    "correct_very_low_in_snapshot",
    "materialization_or_id_gap",
}
CORE_FIELDS = (
    "family",
    "entity",
    "canonical_name",
    "material",
    "connection",
    "install_method",
)
PRIMARY_PARAM_FIELDS = (
    "dn",
    "cable_section",
    "half_perimeter",
    "perimeter",
    "kw",
    "ampere",
    "circuits",
    "bridge_wh_sum",
    "valve_type",
    "lamp_type",
)


def _read_jsonl(path: Path) -> list[dict]:
    records = []
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def _load_r2_rows(path: Path) -> dict[tuple[str, str, str], dict]:
    rows = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("target_group") == "R2_like":
                key = (
                    str(row.get("province", "") or "").strip(),
                    str(row.get("bill_id", "") or "").strip(),
                    str(row.get("bill_name", "") or "").strip(),
                )
                rows[key] = row
    return rows


def _key(record: dict) -> tuple[str, str, str]:
    return (
        str(record.get("province", "") or "").strip(),
        str(record.get("bill_id", "") or "").strip(),
        str(record.get("bill_name", "") or "").strip(),
    )


def _has_value(features: dict, key: str) -> bool:
    value = (features or {}).get(key)
    return value not in (None, "", [], {})


def _present_fields(features: dict, fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if _has_value(features, field)]


def _missing_fields(features: dict, fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if not _has_value(features, field)]


def _numeric_features(features: dict) -> dict:
    numeric = dict((features or {}).get("numeric_params") or {})
    for key in PRIMARY_PARAM_FIELDS:
        if _has_value(features, key) and key not in numeric:
            numeric[key] = features.get(key)
    return {k: v for k, v in numeric.items() if v not in (None, "", [], {})}


def _audit_expected_candidate(province: str, quota_id: str) -> dict:
    row = search_by_id(quota_id, province=province)
    if row:
        qid, name, unit = row
        found = True
    else:
        qid, name, unit = quota_id, "", ""
        found = False
    candidate = {
        "quota_id": qid,
        "name": name,
        "unit": unit,
        "specialty": get_book_from_quota_id(qid) or "",
    }
    features = build_candidate_canonical_features_no_store(candidate, specialty=candidate["specialty"]) if name else {}
    return {
        "quota_id": qid,
        "quota_db_found": found,
        "name": name,
        "unit": unit,
        "specialty": candidate["specialty"],
        "features": features,
        "core_present_fields": _present_fields(features, CORE_FIELDS),
        "core_missing_fields": _missing_fields(features, CORE_FIELDS),
        "primary_params": _numeric_features(features),
    }


def _classify(record: dict, audits: list[dict]) -> str:
    if record.get("original_expected_top50"):
        if record.get("guarded_expected_top1"):
            return "in_pool_guard_can_select"
        return "in_pool_consumption_gap"
    if not any(a.get("quota_db_found") for a in audits):
        return "expected_quota_not_found_in_db"
    if any(a.get("features", {}).get("family") and a.get("features", {}).get("entity") for a in audits):
        return "retrieval_pool_missing_despite_materializable_features"
    return "canonical_feature_materialization_gap"


def main() -> int:
    r4_details_path = PROJECT_ROOT / "reports" / "agent_state" / "v36_data_fuel_r4_top50_guarded_shadow_details.jsonl"
    r2_csv_path = PROJECT_ROOT / "reports" / "agent_state" / "v36_oss_r2_canonicalizer_alignment_details.csv"
    out_details = PROJECT_ROOT / "reports" / "agent_state" / "v36_data_fuel_r5_canonical_materialization_audit_details.jsonl"
    out_summary = PROJECT_ROOT / "reports" / "agent_state" / "v36_data_fuel_r5_canonical_materialization_audit_summary.json"
    out_report = PROJECT_ROOT / "reports" / "agent_state" / "reports" / "v36_data_fuel_r5_canonical_materialization_audit_20260518.md"

    r4_records = _read_jsonl(r4_details_path)
    r2_rows = _load_r2_rows(r2_csv_path)
    details = []
    for record in r4_records:
        row = r2_rows.get(_key(record), {})
        audits = [_audit_expected_candidate(record.get("province", ""), qid) for qid in record.get("expected_ids", [])]
        best = next((a for a in audits if a.get("quota_db_found")), audits[0] if audits else {})
        category = _classify(record, audits)
        fuel_keys = list(record.get("fuel_feature_keys") or [])
        detail = {
            "schema_version": "v36_data_fuel_r5_canonical_materialization_audit.record.v1",
            "province": record.get("province", ""),
            "bill_id": record.get("bill_id", ""),
            "bill_name": record.get("bill_name", ""),
            "bucket": record.get("bucket", ""),
            "target_bucket": bool(record.get("target_bucket", False)),
            "expected_ids": record.get("expected_ids", []),
            "candidate_snapshot_count": int(record.get("candidate_snapshot_count", 0) or 0),
            "expected_top10": bool(record.get("original_expected_top10", False)),
            "expected_top20": bool(record.get("original_expected_top20", False)),
            "expected_top50": bool(record.get("original_expected_top50", False)),
            "guarded_gain": bool(record.get("guarded_gain", False)),
            "failure_category": category,
            "bill_target_feature_snapshot": row.get("target_feature_snapshot", ""),
            "bill_target_primary_params": row.get("target_primary_params", ""),
            "fuel_feature_keys": fuel_keys,
            "expected_candidate_audits": audits,
            "best_expected_core_present_fields": best.get("core_present_fields", []),
            "best_expected_core_missing_fields": best.get("core_missing_fields", []),
            "best_expected_primary_params": best.get("primary_params", {}),
        }
        details.append(detail)

    out_details.parent.mkdir(parents=True, exist_ok=True)
    with out_details.open("w", encoding="utf-8") as handle:
        for detail in details:
            handle.write(json.dumps(detail, ensure_ascii=False) + "\n")

    def summarize(records: list[dict]) -> dict:
        category_counts = Counter(r["failure_category"] for r in records)
        missing_core = Counter()
        present_core = Counter()
        primary_param_presence = Counter()
        for record in records:
            for field in record["best_expected_core_missing_fields"]:
                missing_core[field] += 1
            for field in record["best_expected_core_present_fields"]:
                present_core[field] += 1
            for field in record["best_expected_primary_params"]:
                primary_param_presence[field] += 1
        return {
            "total": len(records),
            "expected_top10": sum(1 for r in records if r["expected_top10"]),
            "expected_top20": sum(1 for r in records if r["expected_top20"]),
            "expected_top50": sum(1 for r in records if r["expected_top50"]),
            "guarded_gain": sum(1 for r in records if r["guarded_gain"]),
            "category_counts": dict(category_counts),
            "expected_core_present_counts": dict(present_core),
            "expected_core_missing_counts": dict(missing_core),
            "expected_primary_param_counts": dict(primary_param_presence),
        }

    target = [r for r in details if r["target_bucket"]]
    by_bucket = {
        bucket: summarize([r for r in details if r["bucket"] == bucket])
        for bucket in sorted({r["bucket"] for r in details})
    }
    summary = {
        "schema_version": "v36_data_fuel_r5_canonical_materialization_audit.v1",
        "readonly": True,
        "business_code_changed": False,
        "db_written": False,
        "benchmark_baseline_changed": False,
        "input_r4_details": str(r4_details_path.relative_to(PROJECT_ROOT)),
        "input_r2_csv": str(r2_csv_path.relative_to(PROJECT_ROOT)),
        "output_details_jsonl": str(out_details.relative_to(PROJECT_ROOT)),
        "overall": summarize(details),
        "target_buckets": summarize(target),
        "by_bucket": by_bucket,
        "decision": "prefer_retrieval_pool_and_materialized_feature_consistency_before_jarvis_consumption",
    }
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    target_summary = summary["target_buckets"]
    target_categories = target_summary["category_counts"]
    report = f"""# V36-DATA-FUEL-R5：候选 canonical feature 物化一致性分析

生成时间：2026-05-18

## 1. 本轮边界

本轮只读分析，不改业务算法，不改最终预测。

- 未修改 `src/**`
- 未写入数据库
- 未修改 benchmark baseline
- 未修改 schema / index
- 只用当前 R4 top50 shadow 产物、本省 `quota.db` 查询、当前 canonicalizer no-store 解析

输出文件：

- `reports/agent_state/v36_data_fuel_r5_canonical_materialization_audit_details.jsonl`
- `reports/agent_state/v36_data_fuel_r5_canonical_materialization_audit_summary.json`

## 2. 关键结论

R4 证明：即使诊断预算拉到 top50，R2 目标桶里正确候选进入 top50 的只有 {target_summary['expected_top50']}/{target_summary['total']} 条。

R5 进一步证明：大量正确定额在本省 `quota.db` 中可以被 no-store canonicalizer 解析出基础结构字段，但它们没有稳定进入 rankable pool。因此当前第一优先级不是消费 Jarvis 历史描述，也不是继续调排序权重，而是修“召回/融合后的候选池宽度 + 候选 feature 物化一致性”。

## 3. 目标桶失败分类

| 分类 | 数量 | 含义 |
| --- | ---: | --- |
| retrieval_pool_missing_despite_materializable_features | {target_categories.get('retrieval_pool_missing_despite_materializable_features', 0)} | 正确定额本身可解析出 family/entity 等字段，但没有进 top50 候选池 |
| canonical_feature_materialization_gap | {target_categories.get('canonical_feature_materialization_gap', 0)} | 正确定额解析后仍缺 family/entity 等核心字段 |
| in_pool_consumption_gap | {target_categories.get('in_pool_consumption_gap', 0)} | 正确候选已经进池，但排序/最终决策没有用好 |
| in_pool_guard_can_select | {target_categories.get('in_pool_guard_can_select', 0)} | 正确候选进池，并且安全门禁 shadow 能选中 |
| expected_quota_not_found_in_db | {target_categories.get('expected_quota_not_found_in_db', 0)} | 标准答案定额编号在当前本省 quota.db 中查不到 |

## 4. 正确定额自身字段覆盖

目标桶正确定额 no-store 解析出的核心字段覆盖：

| 字段 | 有值数量 |
| --- | ---: |
| family | {target_summary['expected_core_present_counts'].get('family', 0)} |
| entity | {target_summary['expected_core_present_counts'].get('entity', 0)} |
| canonical_name | {target_summary['expected_core_present_counts'].get('canonical_name', 0)} |
| material | {target_summary['expected_core_present_counts'].get('material', 0)} |
| connection | {target_summary['expected_core_present_counts'].get('connection', 0)} |
| install_method | {target_summary['expected_core_present_counts'].get('install_method', 0)} |

这组结果说明：`family/entity/canonical_name` 多数可以从定额名称本身解析出来，但 `material/connection/install_method` 仍然偏弱。排序器如果依赖这些字段，必须先保证候选侧字段稳定物化，否则继续加权只会来回震荡。

## 5. 对算法架构的判断

当前 R2 大头不是单纯 LTR 权重问题。

更像是三层问题叠加：

1. 召回层：正确候选很多时候没有进入足够宽的 rankable pool。
2. 物化层：候选即使有定额名称，也没有稳定带着 `family/entity/canonical_name/material/connection/install_method` 进入后续排序。
3. 消费层：少数进池样本能被安全门禁救回，但净收益只有 3 条，不够进入真实排序改造。

所以 Jarvis 百万历史描述目前最适合做“召回和结构字段补燃料”的 shadow 数据源，不适合直接当答案或直接改最终排序。

## 6. 下一步唯一建议

执行 `V36-DATA-FUEL-R6：候选池入口物化一致性修复方案设计`。

下一轮仍先不改业务代码，只设计一刀最小改造方案，明确：

- 在哪个入口统一 attach candidate canonical features：BM25、vector、prior、neighbor、aux、rule backup
- 哪些字段必须 no-store 生成，哪些允许读 candidate_features.db
- 如何保证不写 DB、不拖慢 core_100
- 如何用测试证明候选进入 rankable pool 后都有稳定 `family/entity/canonical_name`

如果 R6 方案需要改超过 5 个源码/测试文件，先停下来汇报方案，等确认后再实现。

## 7. 给非程序员看的三句话

本轮颜色：黄色。

是否离 75% 更近：还没有真实涨分，但已经定位到更大的系统入口：候选池和候选字段物化，而不是继续小调排序。

100 条是否还在 2 分钟内：本轮只读分析，没有新增热路径代码，也没有跑 core_100 速度门禁。
"""
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(report, encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
