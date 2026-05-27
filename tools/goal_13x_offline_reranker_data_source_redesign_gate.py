from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
MATRIX_DIR = AGENT_STATE / "goal_query_anchored_ranking_matrix_dry_run"
DEFAULT_GROUP_DEV = MATRIX_DIR / "ltr_group_dev.jsonl"
DEFAULT_OSS_SAMPLES = PROJECT_ROOT / "data" / "goal_search" / "oss_samples.jsonl"
DEFAULT_OSS_SAMPLES_EXPANDED = PROJECT_ROOT / "data" / "goal_search" / "oss_samples_expanded.jsonl"
DEFAULT_OSS_XML_ROOT = Path(r"D:\广联达临时文件\oss_samples")
DEFAULT_LOCAL_ASSETS = PROJECT_ROOT.parent / "auto-quota-local-assets-20260522"
DEFAULT_NATIONAL_INDEX_DB = DEFAULT_LOCAL_ASSETS / "data" / "goal_search" / "national_index.sqlite"
DEFAULT_BILL_LIBRARY_DB = DEFAULT_LOCAL_ASSETS / "data" / "bill_library.db"
DEFAULT_13X_SUMMARY = AGENT_STATE / "goal_13x_offline_reranker_dev_oof_robustness_audit_summary.json"
DEFAULT_13X_ROBUST_SCORECARD = AGENT_STATE / "goal_13x_offline_reranker_dev_oof_robustness_audit_robust_scorecard.csv"
DEFAULT_S8_SOURCE_FILE_REGISTRY = AGENT_STATE / "goal_10x_s8_source_family_independence_registry_artifact_definition_source_file_registry.csv"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_offline_reranker_data_source_redesign_gate"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _sqlite_count(path: Path, table: str) -> int:
    if not path.exists():
        return 0
    with sqlite3.connect(path) as con:
        return int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def _xml_inventory(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not root.exists():
        return {
            "oss_xml_root": str(root),
            "oss_xml_root_exists": False,
            "oss_xml_file_count": 0,
            "oss_xml_unique_name_size_count": 0,
            "oss_xml_total_bytes": 0,
            "oss_xml_total_gb": 0.0,
        }, []
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".xml"]
    total_bytes = sum(path.stat().st_size for path in files)
    unique_name_size = {(path.name.lower(), path.stat().st_size) for path in files}
    top_level: Counter[str] = Counter()
    province: Counter[str] = Counter()
    bytes_by_key: defaultdict[str, int] = defaultdict(int)
    for path in files:
        parts = path.relative_to(root).parts
        first = parts[0] if parts else "<root>"
        top_level[first] += 1
        bytes_by_key[f"top:{first}"] += path.stat().st_size
        if len(parts) >= 3 and parts[0] == "by_province":
            province[parts[1]] += 1
            bytes_by_key[f"province:{parts[1]}"] += path.stat().st_size
    rows: list[dict[str, Any]] = []
    for name, count in top_level.most_common():
        total = bytes_by_key[f"top:{name}"]
        rows.append({"scope": "top_level_dir", "key": name, "file_count": count, "total_bytes": total, "total_gb": round(total / (1024**3), 6)})
    for name, count in province.most_common():
        total = bytes_by_key[f"province:{name}"]
        rows.append({"scope": "province_dir", "key": name, "file_count": count, "total_bytes": total, "total_gb": round(total / (1024**3), 6)})
    return {
        "oss_xml_root": str(root),
        "oss_xml_root_exists": True,
        "oss_xml_file_count": len(files),
        "oss_xml_unique_name_size_count": len(unique_name_size),
        "oss_xml_total_bytes": total_bytes,
        "oss_xml_total_gb": round(total_bytes / (1024**3), 6),
    }, rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return path


def _source_family_for(source_file: str, accepted_sources: dict[str, dict[str, str]]) -> str:
    if source_file in accepted_sources:
        return accepted_sources[source_file].get("source_family_id", "")
    if source_file == "global_repair_decision_table.csv":
        return "sf_global_repair_single_source"
    stem = re.sub(r"[^a-zA-Z0-9]+", "_", source_file).strip("_").lower()[:80]
    return "sf_unregistered_" + stem


def _write_markdown(path: Path, report: dict[str, Any], policy_rows: list[dict[str, Any]], gates: list[dict[str, Any]]) -> None:
    metrics = report["metrics"]
    lines = [
        "# 13.2 Offline Reranker Data/Source Redesign Gate",
        "",
        "This gate turns the 13.1 source-dominated result into a bolder but bounded next experiment design.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| dev_group_count | {metrics['dev_group_count']} |",
        f"| global_repair_group_count | {metrics['global_repair_group_count']} |",
        f"| non_global_group_count | {metrics['non_global_group_count']} |",
        f"| accepted_oss_group_count | {metrics['accepted_oss_group_count']} |",
        f"| oss_sample_rows | {metrics['oss_sample_rows']} |",
        f"| oss_expanded_rows | {metrics['oss_expanded_rows']} |",
        f"| oss_xml_file_count | {metrics['oss_xml_file_count']} |",
        f"| oss_xml_total_gb | {metrics['oss_xml_total_gb']} |",
        f"| national_quota_rows | {metrics['national_quota_rows']} |",
        f"| bill_item_rows | {metrics['bill_item_rows']} |",
        f"| bill_description_rows | {metrics['bill_description_rows']} |",
        f"| source_file_count | {metrics['source_file_count']} |",
        f"| source_family_count_for_split | {metrics['source_family_count_for_split']} |",
        f"| recommended_next_scope | {metrics['recommended_next_scope']} |",
        "",
        "## Candidate Execution Policies",
        "",
        "| policy_id | boldness | training_boundary | primary_score | acceptance_gate |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in policy_rows:
        lines.append(
            f"| {row['policy_id']} | {row['boldness']} | {row['training_boundary']} | {row['primary_score']} | {row['acceptance_gate']} |"
        )
    lines.extend(
        [
            "",
            "## Gates",
            "",
            "| gate | status | evidence | decision |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in gates:
        lines.append(f"| {row['gate']} | {row['status']} | {row['evidence']} | {row['decision']} |")
    lines.extend(["", "## Decision", "", report["decision"], "", "## Anti-drift", "", report["anti_drift_conclusion"]])
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    text = path.read_text(encoding="utf-8")
    metrics = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前状态：13.2 offline reranker data/source redesign gate 已完成。"
        f" dev_groups={metrics['dev_group_count']}；global_repair_groups={metrics['global_repair_group_count']}；"
        f"non_global_groups={metrics['non_global_group_count']}；accepted_oss_groups={metrics['accepted_oss_group_count']}；"
        f"oss_samples={metrics['oss_sample_rows']}；oss_expanded={metrics['oss_expanded_rows']}；"
        f"oss_xml_files={metrics['oss_xml_file_count']}；oss_xml_total_gb={metrics['oss_xml_total_gb']}；"
        f"national_quota_rows={metrics['national_quota_rows']}；bill_item_rows={metrics['bill_item_rows']}；"
        f"source_family_count_for_split={metrics['source_family_count_for_split']}。\n"
        "设计结论：OSS/定额母库是百万级高信任人工造价训练资产，D:\\广联达临时文件\\oss_samples 是真实 OSS XML 母库，oss_samples*.jsonl 只是小样本视图；下一轮应先做 13.3 OSS XML mother-data manifest + source-aware training matrix build/execution，而不是继续围着 2945 条抽样打转。允许 OSS-first training、global_repair source-aware training/downweight/exclusion ablation、source-family/province OOF、non-global primary score 和 accepted OSS guard；"
        "但仍禁止 heldout/hard selection、上线、改 GoalSearcher、改线上阈值。\n"
        "建议下一步：go: run 13.3 OSS mother-data source-aware reranker dev/OOF experiment。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "13.2 offline reranker data/source redesign gate" not in text:
        section = f"""
    <section>
      <h2>13.2 Data/Source Redesign</h2>
      <div class="infobox">
        <strong>13.2 完成。</strong>
        OSS/定额母库是百万级人工套价高信任训练资产；`D:\\广联达临时文件\\oss_samples` 是真实 XML 母库。下一轮不再按 raw all-source Top1 选候选，改走 OSS XML mother-data + source-aware OOF，source-family/province split 做防同源和防单省过拟合。
      </div>
      <table>
        <tbody>
          <tr>
            <td class="stage">13.2 offline reranker data/source redesign gate</td>
            <td><span class="pill done">done</span></td>
            <td>Define bolder OSS-first/source-clean reranker execution policies after 13.1 source-dominated stop.</td>
            <td>non_global_groups={metrics['non_global_group_count']}; accepted_oss_groups={metrics['accepted_oss_group_count']}; oss_xml_files={metrics['oss_xml_file_count']}; oss_xml_total_gb={metrics['oss_xml_total_gb']}; national_quota_rows={metrics['national_quota_rows']}; bill_item_rows={metrics['bill_item_rows']}.</td>
            <td>Next allowed move: explicit go for 13.3 OSS mother-data source-aware reranker dev/OOF execution.</td>
          </tr>
        </tbody>
      </table>
    </section>
"""
        text = text.replace("    <footer>", section + "\n    <footer>", 1)
    artifact_marker = "          <tr>\n            <td>13.1 offline reranker execution summary</td>"
    if "13.2 offline reranker data/source redesign summary" not in text and artifact_marker in text:
        artifact_rows = f"""          <tr>
            <td>13.2 offline reranker data/source redesign summary</td>
            <td>Bolder but bounded next-execution scope after 13.1 source-dominated stop.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
          <tr>
            <td>13.2 offline reranker policy matrix</td>
            <td>Source-clean policy variants, split contract, coverage inventory, acceptance gates, and blocked actions.</td>
            <td><code>{_safe_rel(report['artifacts']['policy_matrix_csv'])}</code> / <code>{_safe_rel(report['artifacts']['source_split_contract_csv'])}</code></td>
          </tr>
"""
        text = text.replace(artifact_marker, artifact_rows + artifact_marker, 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = re.sub(
        r"Last updated: .*? Asia/Shanghai\.",
        f"Last updated: {stamp} Asia/Shanghai.",
        text,
        count=1,
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Define 13.2 offline reranker data/source redesign gate")
    parser.add_argument("--group-dev", default=str(DEFAULT_GROUP_DEV))
    parser.add_argument("--oss-samples", default=str(DEFAULT_OSS_SAMPLES))
    parser.add_argument("--oss-samples-expanded", default=str(DEFAULT_OSS_SAMPLES_EXPANDED))
    parser.add_argument("--oss-xml-root", default=str(DEFAULT_OSS_XML_ROOT))
    parser.add_argument("--national-index-db", default=str(DEFAULT_NATIONAL_INDEX_DB))
    parser.add_argument("--bill-library-db", default=str(DEFAULT_BILL_LIBRARY_DB))
    parser.add_argument("--audit-summary", default=str(DEFAULT_13X_SUMMARY))
    parser.add_argument("--robust-scorecard", default=str(DEFAULT_13X_ROBUST_SCORECARD))
    parser.add_argument("--s8-source-file-registry", default=str(DEFAULT_S8_SOURCE_FILE_REGISTRY))
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    groups = _read_jsonl(Path(args.group_dev))
    oss_samples = _read_jsonl(Path(args.oss_samples)) if Path(args.oss_samples).exists() else []
    oss_expanded = _read_jsonl(Path(args.oss_samples_expanded)) if Path(args.oss_samples_expanded).exists() else []
    national_quota_rows = _sqlite_count(Path(args.national_index_db), "national_quotas")
    bill_item_rows = _sqlite_count(Path(args.bill_library_db), "bill_items")
    bill_description_rows = _sqlite_count(Path(args.bill_library_db), "bill_descriptions")
    xml_metrics, xml_inventory_rows = _xml_inventory(Path(args.oss_xml_root))
    audit_summary = _read_json(Path(args.audit_summary))
    robust_scorecard = _read_csv(Path(args.robust_scorecard))
    s8_registry = _read_csv(Path(args.s8_source_file_registry))
    accepted_sources = {row["source_file"]: row for row in s8_registry}

    source_counts: Counter[str] = Counter(row.get("source_file", "") for row in groups)
    oss_source_counts: Counter[str] = Counter(row.get("source_file", "") for row in oss_samples)
    oss_expanded_source_counts: Counter[str] = Counter(row.get("source_file", "") for row in oss_expanded)
    family_counts: Counter[str] = Counter(_source_family_for(row.get("source_file", ""), accepted_sources) for row in groups)
    source_family_by_source = {source: _source_family_for(source, accepted_sources) for source in source_counts}
    global_groups = source_counts["global_repair_decision_table.csv"]
    accepted_groups = sum(count for source, count in source_counts.items() if source in accepted_sources)
    non_global_groups = len(groups) - global_groups

    source_inventory: list[dict[str, Any]] = []
    for source, count in source_counts.most_common():
        source_inventory.append(
            {
                "source_file": source,
                "group_count": count,
                "group_share": round(count / len(groups), 6) if groups else 0.0,
                "source_family_id": source_family_by_source[source],
                "is_global_repair": str(source == "global_repair_decision_table.csv").lower(),
                "is_accepted_oss": str(source in accepted_sources).lower(),
                "eligible_for_primary_score": str(source != "global_repair_decision_table.csv").lower(),
                "eligible_for_guard_score": str(source in accepted_sources).lower(),
            }
        )

    policy_rows = [
        {
            "policy_id": "P0_oss_first_source_aware",
            "boldness": "very_high",
            "training_boundary": "build/train from OSS XML mother data and extracted OSS samples as high-trust human quantity-surveyor labels; split by source_file/source_family/province to avoid same-source proof",
            "split_policy": "source/province-aware OOF over OSS-derived groups; global_repair can train but cannot alone approve",
            "primary_score": "source_heldout_oss_net + non_global_net + cross_province_family_net",
            "guard_score": "no single source positive share > 0.45; accepted OSS and non-global slices non-negative",
            "acceptance_gate": "positive net on at least 3 source_files or 2 source_families; global-only gain is insufficient",
            "why": "Uses the XML OSS mother data and million-scale OSS/quota assets as the main learning asset instead of treating the current 2945-row sample view as the full universe.",
        },
        {
            "policy_id": "P1_source_clean_primary",
            "boldness": "medium_high",
            "training_boundary": "train on non-global dev groups only; exclude global_repair from train and primary eval",
            "split_policy": "ordinary group OOF inside non-global groups",
            "primary_score": "non_global_hit1_net",
            "guard_score": "accepted_oss_net >= 0 and loss_budget_pass",
            "acceptance_gate": "non_global_net >= 20; hit1_loss <= 25; top_positive_source_share <= 0.45",
            "why": "Fastest direct test of whether reranker works outside global_repair.",
        },
        {
            "policy_id": "P2_global_downweight_auxiliary",
            "boldness": "high",
            "training_boundary": "train on all dev groups with global_repair sample_weight=0.15; primary eval excludes global_repair",
            "split_policy": "ordinary group OOF; report global as auxiliary only",
            "primary_score": "non_global_hit1_net",
            "guard_score": "global_repair_gain_share <= 0.35 and accepted_oss_net >= 0",
            "acceptance_gate": "non_global_net >= 25; hit1_loss <= 28; source_dominated=false",
            "why": "Keeps some global signal for learning but prevents it from deciding success.",
        },
        {
            "policy_id": "P3_source_family_oof",
            "boldness": "high",
            "training_boundary": "train on all non-global groups, validate by source_family blocks where feasible",
            "split_policy": "source_family grouped OOF; unregistered sources get stable synthetic family ids",
            "primary_score": "heldout_source_family_oof_non_global_net",
            "guard_score": "positive_net in at least 3 source_files or 2 source_families",
            "acceptance_gate": "non_global_net >= 15; independent_source_file_count >= 3; no single source positive share > 0.45",
            "why": "Attacks same-source leakage directly; this is the boldest reliability test.",
        },
        {
            "policy_id": "P4_two_stage_guarded_override",
            "boldness": "targeted_high",
            "training_boundary": "train source-clean reranker, apply only when baseline positive_rank in 2..10 and candidate confidence margin passes",
            "split_policy": "same as best of P1/P2/P3",
            "primary_score": "rank_2_10_non_global_net",
            "guard_score": "new_loss_count <= 10 and accepted_oss_net >= 0",
            "acceptance_gate": "rank_2_10_net >= 12; no family slice net < -3",
            "why": "Makes the first implementation safer by limiting override depth without being read-only forever.",
        },
    ]

    split_contract = [
        {
            "split_component": "global_repair_boundary",
            "rule": "global_repair_decision_table.csv may be high-value OSS-derived training data, but it is never sufficient as sole proof",
            "implementation_hint": "allow in P0/P2 training with source-aware validation; exclude/downweight only for clean ablations",
            "required_artifact": "global_repair_exclusion_effects.csv",
        },
        {
            "split_component": "oss_first_training_value",
            "rule": "OSS XML mother data and extracted samples are treated as high-trust human quantity-surveyor labels",
            "implementation_hint": "parse XML into bill-quota pairs, derive training groups from bill_name/bill_text/province/expected_ids plus national quota candidates; never use source_file or exact expected_id as model feature",
            "required_artifact": "oss_xml_mother_data_training_manifest.csv",
        },
        {
            "split_component": "source_family_grouping",
            "rule": "accepted OSS uses S8 registry; other sources receive deterministic source_file families",
            "implementation_hint": "source_family_id is carried in group metadata only, never as model feature",
            "required_artifact": "source_family_fold_manifest.csv",
        },
        {
            "split_component": "accepted_oss_guard",
            "rule": "accepted OSS may be small, so it is a guard rather than the only approval signal",
            "implementation_hint": "accepted_oss_net must be >=0; positive accepted_oss evidence strengthens but is not mandatory",
            "required_artifact": "accepted_oss_guard_report.csv",
        },
        {
            "split_component": "source_dominance_check",
            "rule": "no single source may contribute more than 45 percent of positive net for a freeze candidate",
            "implementation_hint": "compute after OOF predictions from hit1 flips/loss audit",
            "required_artifact": "source_concentration_report.csv",
        },
    ]

    gates = [
        {
            "gate": "13.1_stop_understood",
            "status": "pass",
            "evidence": f"13.1 decision={audit_summary['metrics']['decision']}; robust_pass_candidate_count={audit_summary['metrics']['robust_pass_candidate_count']}",
            "decision": "redesign data/source policy before next execution",
        },
        {
            "gate": "enough_non_global_groups",
            "status": "pass" if non_global_groups >= 80 else "warn",
            "evidence": f"non_global_groups={non_global_groups}",
            "decision": "allow bold thin-slice source-clean experiment" if non_global_groups >= 80 else "need more non-global data",
        },
        {
            "gate": "accepted_oss_guard_available",
            "status": "pass" if accepted_groups > 0 else "warn",
            "evidence": f"accepted_oss_groups={accepted_groups}",
            "decision": "use current matrix OSS as guard; use raw OSS files as first-class future training source",
        },
        {
            "gate": "oss_training_asset_available",
            "status": "pass" if xml_metrics["oss_xml_file_count"] > 0 and national_quota_rows > 0 else "warn",
            "evidence": f"oss_xml_files={xml_metrics['oss_xml_file_count']}; oss_xml_total_gb={xml_metrics['oss_xml_total_gb']}; oss_samples={len(oss_samples)}; oss_expanded={len(oss_expanded)}; national_quotas={national_quota_rows}; bill_items={bill_item_rows}; bill_descriptions={bill_description_rows}",
            "decision": "promote OSS XML mother-data source-aware training design",
        },
        {
            "gate": "source_family_split_feasible",
            "status": "pass" if len(family_counts) >= 3 else "warn",
            "evidence": f"source_family_count_for_split={len(family_counts)}",
            "decision": "allow source-family grouped OOF variant",
        },
        {
            "gate": "bold_execution_scope",
            "status": "pass",
            "evidence": "P1/P2/P3/P4 policy matrix defined",
            "decision": "next stage may execute dev/OOF-only source-clean reranker with explicit go",
        },
    ]

    blocked_actions = [
        {
            "blocked_action": "heldout_or_hard_selection",
            "reason": "13.2 only defines dev/OOF source design; heldout/hard remain unavailable for candidate selection.",
            "allowed_after": "explicit validation go after a frozen dev/OOF candidate",
        },
        {
            "blocked_action": "online_goal_searcher_integration",
            "reason": "No robust dev/OOF candidate is frozen yet.",
            "allowed_after": "future validation pass plus explicit implementation go",
        },
        {
            "blocked_action": "raw_all_source_scorecard_freeze",
            "reason": "13.1 showed raw all-source net is global_repair/source dominated.",
            "allowed_after": "never; primary score must be source-clean",
        },
    ]

    best_raw_rows = sorted(robust_scorecard, key=lambda row: _int(row.get("raw_hit1_net")), reverse=True)[:5]
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "source_inventory_csv": str(output_prefix.with_name(output_prefix.name + "_source_inventory.csv")),
        "oss_xml_inventory_csv": str(output_prefix.with_name(output_prefix.name + "_oss_xml_inventory.csv")),
        "policy_matrix_csv": str(output_prefix.with_name(output_prefix.name + "_policy_matrix.csv")),
        "source_split_contract_csv": str(output_prefix.with_name(output_prefix.name + "_source_split_contract.csv")),
        "acceptance_gates_csv": str(output_prefix.with_name(output_prefix.name + "_acceptance_gates.csv")),
        "blocked_actions_csv": str(output_prefix.with_name(output_prefix.name + "_blocked_actions.csv")),
    }
    metrics = {
        "dev_group_count": len(groups),
        "global_repair_group_count": global_groups,
        "global_repair_group_share": round(global_groups / len(groups), 6) if groups else 0.0,
        "non_global_group_count": non_global_groups,
        "accepted_oss_group_count": accepted_groups,
        "source_file_count": len(source_counts),
        "source_family_count_for_split": len(family_counts),
        "oss_sample_rows": len(oss_samples),
        "oss_expanded_rows": len(oss_expanded),
        **xml_metrics,
        "national_quota_rows": national_quota_rows,
        "bill_item_rows": bill_item_rows,
        "bill_description_rows": bill_description_rows,
        "oss_sample_source_file_count": len(oss_source_counts),
        "oss_expanded_source_file_count": len(oss_expanded_source_counts),
        "policy_count": len(policy_rows),
        "recommended_next_scope": "13.3_source_clean_reranker_dev_oof_execution",
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "training_executed": False,
        "implementation_allowed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "13.2 offline reranker data/source redesign gate",
        "metrics": metrics,
        "top_13_1_raw_rows": best_raw_rows,
        "artifacts": artifacts,
        "decision": (
            "Proceed boldly and promote OSS XML mother data to first-class training evidence. The next reranker execution should not optimize or approve on raw all-source net, "
            "but it should parse/build from D:\\广联达临时文件\\oss_samples, the million-scale national quota and bill library assets, plus extracted OSS samples as high-trust human quantity-surveyor labels under source-aware OOF. Run OSS-first and source-clean "
            "dev/OOF policies that allow global_repair as training signal but prevent it from being the sole proof, score non-global/source-heldout/cross-province slices, "
            "and add source-family/single-source dominance checks. This is enough to authorize a future 13.3 dev/OOF-only execution after explicit go."
        ),
        "anti_drift_conclusion": (
            "13.2 does not train, tune, use heldout/hard, change thresholds, edit GoalSearcher, edit feature whitelist, or integrate online. "
            "It deliberately relaxes the too-conservative accepted-OSS-only approval into an OSS-first source-aware gate while preserving source-artifact protection."
        ),
        "next_stage": {
            "recommended": "13.3 source-clean reranker dev/OOF experiment",
            "explicit_go_prompt": "go: run 13.3 source-clean reranker dev/OOF experiment using the 13.2 policy matrix",
        },
    }

    _write_csv(Path(artifacts["source_inventory_csv"]), source_inventory, list(source_inventory[0].keys()))
    _write_csv(Path(artifacts["oss_xml_inventory_csv"]), xml_inventory_rows, ["scope", "key", "file_count", "total_bytes", "total_gb"])
    _write_csv(Path(artifacts["policy_matrix_csv"]), policy_rows, list(policy_rows[0].keys()))
    _write_csv(Path(artifacts["source_split_contract_csv"]), split_contract, list(split_contract[0].keys()))
    _write_csv(Path(artifacts["acceptance_gates_csv"]), gates, list(gates[0].keys()))
    _write_csv(Path(artifacts["blocked_actions_csv"]), blocked_actions, list(blocked_actions[0].keys()))
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report, policy_rows, gates)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
