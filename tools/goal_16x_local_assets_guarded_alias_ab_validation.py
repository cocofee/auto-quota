from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import config
from src.goal_search.national_index import clean_text, extract_signal
from src.goal_search.oss_alias_prior import GuardedOssAliasPriorSource, reset_guarded_oss_alias_prior_source
from src.goal_search.oss_recall_prior import OssRecallPriorSource, reset_oss_recall_prior_source
from src.goal_search.searcher import GoalSearcher, clear_goal_search_cache


AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_DB_DIR = PROJECT_ROOT.parent / "auto-quota-local-assets-20260522" / "db"
DEFAULT_HELDOUT = PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "heldout_validation.jsonl"
DEFAULT_HARD = PROJECT_ROOT / "data" / "goal_search" / "anchor_audit" / "hard_validation.jsonl"
DEFAULT_INDEX = Path(getattr(config, "OSS_GUARDED_ALIAS_INDEX_PATH", PROJECT_ROOT / "data" / "goal_search" / "guarded_oss_alias_index.jsonl"))
DEFAULT_IMPACTED_AUDIT = AGENT_STATE / "goal_15x_guarded_oss_alias_heldout_hard_validation_row_audit.csv"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_16x_local_assets_guarded_alias_ab_validation"
DEFAULT_STATUS = AGENT_STATE / "goal_current_roadmap_status_20260526_14x.md"
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
CORE_FAMILIES = {"concrete", "rebar", "pipe", "pump", "support"}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _read_impacted_ordinals(path: Path) -> dict[str, set[int]]:
    impacted: dict[str, set[int]] = defaultdict(set)
    if not path.exists():
        return impacted
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                generated = int(row.get("alias_generated_candidates") or 0)
                ordinal = int(row.get("row_ordinal") or 0)
            except ValueError:
                continue
            split = clean_text(row.get("split"))
            if split and ordinal and generated > 0:
                impacted[split].add(ordinal)
    return impacted


def _configure_db_root(db_dir: Path) -> None:
    if not (db_dir / "provinces").exists():
        raise FileNotFoundError(f"db root has no provinces directory: {db_dir}")
    config.DB_DIR = db_dir
    config.PROVINCES_DB_DIR = db_dir / "provinces"
    clear_goal_search_cache()
    reset_guarded_oss_alias_prior_source()


def _query_text(row: dict[str, Any]) -> str:
    return " ".join(
        part
        for part in (
            clean_text(row.get("bill_name") or row.get("name")),
            clean_text(row.get("bill_text") or row.get("description") or row.get("feature_text")),
            clean_text(row.get("specialty")),
            clean_text(row.get("unit") or row.get("bill_unit")),
        )
        if part
    )


def _searcher_prior_texts(row: dict[str, Any]) -> tuple[str, ...]:
    query = GoalSearcher._coerce_item(row)
    combined = " ".join(x for x in [query.bill_name, query.text, query.specialty, query.unit] if x)
    return (query.bill_name, query.text, combined)


def _rank(hits: list[Any], expected_ids: set[str]) -> int:
    for index, hit in enumerate(hits, start=1):
        if clean_text(hit.quota_id) in expected_ids:
            return index
    return 0


def _hit(rank: int, k: int) -> int:
    return int(1 <= rank <= k)


def _top_id(hits: list[Any]) -> str:
    return clean_text(hits[0].quota_id) if hits else ""


def _slice_name(query_family: str) -> str:
    if query_family in CORE_FAMILIES:
        return "core_family"
    if query_family:
        return "nonempty_other_family"
    return "taxonomy_empty"


def _query_family(row: dict[str, Any]) -> str:
    name_family = extract_signal(clean_text(row.get("bill_name") or row.get("name"))).family
    if name_family:
        return name_family
    return extract_signal(_query_text(row)[:500]).family


def _alias_candidates(
    source: GuardedOssAliasPriorSource,
    resolved_province: str,
    row: dict[str, Any],
    query_family: str,
) -> list[dict[str, Any]]:
    seen_queries: set[str] = set()
    seen_quota_ids: set[str] = set()
    output: list[dict[str, Any]] = []
    for text in _searcher_prior_texts(row):
        if not text or text in seen_queries:
            continue
        seen_queries.add(text)
        for candidate in source.collect(
            province=resolved_province,
            query_text=text,
            query_family=query_family,
            item=row,
            top_k=int(getattr(config, "OSS_GUARDED_ALIAS_TOP_K", 6) or 6),
        ):
            quota_id = clean_text(candidate.get("quota_id"))
            if quota_id and quota_id not in seen_quota_ids:
                seen_quota_ids.add(quota_id)
                output.append(candidate)
    return output


def _recall_candidates(
    source: OssRecallPriorSource,
    resolved_province: str,
    row: dict[str, Any],
    query_family: str,
) -> list[dict[str, Any]]:
    seen_queries: set[str] = set()
    seen_quota_ids: set[str] = set()
    output: list[dict[str, Any]] = []
    top_k = int(getattr(config, "OSS_RECALL_INDEX_TOP_K", 8) or 8)
    for text in _searcher_prior_texts(row):
        if not text or text in seen_queries:
            continue
        seen_queries.add(text)
        for candidate in source.collect(
            province=resolved_province,
            query_text=text,
            query_family=query_family,
            item=row,
            top_k=top_k,
        ):
            quota_id = clean_text(candidate.get("quota_id"))
            if quota_id and quota_id not in seen_quota_ids:
                seen_quota_ids.add(quota_id)
                output.append(candidate)
                if len(output) >= top_k:
                    return output
    return output


def _candidate_rows(source: Any, candidate_kind: str, resolved_province: str, row: dict[str, Any], query_family: str) -> list[dict[str, Any]]:
    if candidate_kind == "recall":
        return _recall_candidates(source, resolved_province, row, query_family)
    return _alias_candidates(source, resolved_province, row, query_family)


def _set_treatment_enabled(candidate_kind: str, enabled: bool) -> None:
    if candidate_kind == "recall":
        config.OSS_RECALL_INDEX_ENABLED = enabled
    else:
        config.OSS_GUARDED_ALIAS_ENABLED = enabled


def _evaluate_split(
    split_name: str,
    rows: list[dict[str, Any]],
    candidate_source: Any,
    candidate_kind: str,
    *,
    progress_every: int,
    province_cache: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    searchers: dict[str, GoalSearcher] = {}
    audits: list[dict[str, Any]] = []
    for ordinal, row in enumerate(rows, start=1):
        if progress_every > 0 and (ordinal == 1 or ordinal % progress_every == 0):
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {split_name}: {ordinal}/{len(rows)}", flush=True)
        raw_province = clean_text(row.get("province"))
        resolved_province = province_cache.setdefault(raw_province, config.resolve_province(raw_province))
        if resolved_province not in searchers:
            searchers[resolved_province] = GoalSearcher(resolved_province)
        searcher = searchers[resolved_province]
        expected = {clean_text(item) for item in row.get("expected_ids") or [] if clean_text(item)}
        query_family = _query_family(row)

        _set_treatment_enabled(candidate_kind, False)
        baseline_hits = searcher.search(row, top_k=80)
        baseline_rank = _rank(baseline_hits, expected)

        _set_treatment_enabled(candidate_kind, True)
        treatment_hits = searcher.search(row, top_k=80)
        treatment_rank = _rank(treatment_hits, expected)

        candidates = _candidate_rows(candidate_source, candidate_kind, resolved_province, row, query_family)
        positive = sum(1 for candidate in candidates if clean_text(candidate.get("quota_id")) in expected)
        audits.append(
            {
                "split": split_name,
                "row_ordinal": ordinal,
                "anchor_group_id": row.get("anchor_group_id", ""),
                "sample_id": row.get("sample_id", ""),
                "bucket": row.get("bucket", ""),
                "source_file": row.get("source_file", ""),
                "raw_province": raw_province,
                "resolved_province": resolved_province,
                "query_family": query_family or "<empty>",
                "slice": _slice_name(query_family),
                "expected_ids": "|".join(sorted(expected)),
                "baseline_rank": baseline_rank,
                "treatment_rank": treatment_rank,
                "baseline_top1": _hit(baseline_rank, 1),
                "treatment_top1": _hit(treatment_rank, 1),
                "baseline_top5": _hit(baseline_rank, 5),
                "treatment_top5": _hit(treatment_rank, 5),
                "baseline_top20": _hit(baseline_rank, 20),
                "treatment_top20": _hit(treatment_rank, 20),
                "baseline_top80": _hit(baseline_rank, 80),
                "treatment_top80": _hit(treatment_rank, 80),
                "baseline_top1_id": _top_id(baseline_hits),
                "treatment_top1_id": _top_id(treatment_hits),
                "top1_win": int(_hit(treatment_rank, 1) and not _hit(baseline_rank, 1)),
                "top1_loss": int(_hit(baseline_rank, 1) and not _hit(treatment_rank, 1)),
                "top80_gain": int(_hit(treatment_rank, 80) and not _hit(baseline_rank, 80)),
                "top80_loss": int(_hit(baseline_rank, 80) and not _hit(treatment_rank, 80)),
                "prior_generated_candidates": len(candidates),
                "prior_positive_candidates": positive,
                "prior_false_candidates": len(candidates) - positive,
                "prior_candidate_ids": "|".join(clean_text(candidate.get("quota_id")) for candidate in candidates),
            }
        )
    return audits, _scorecard(audits)


def _impacted_rows(
    rows: list[dict[str, Any]],
    candidate_source: Any,
    candidate_kind: str,
    province_cache: dict[str, str],
) -> list[dict[str, Any]]:
    impacted: list[dict[str, Any]] = []
    for row in rows:
        raw_province = clean_text(row.get("province"))
        resolved_province = province_cache.setdefault(raw_province, config.resolve_province(raw_province))
        query_family = _query_family(row)
        if len(impacted) % 50 == 0 and impacted:
            print(f"impacted prefilter: {len(impacted)} rows found", flush=True)
        if _candidate_rows(candidate_source, candidate_kind, resolved_province, row, query_family):
            impacted.append(row)
    return impacted


def _scorecard(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        for name in ("all", row["slice"], f"bucket:{row['bucket']}", f"family:{row['query_family']}"):
            m = metrics[name]
            m["groups"] += 1
            for key in ("top1", "top5", "top20", "top80"):
                m[f"baseline_{key}"] += int(row[f"baseline_{key}"])
                m[f"treatment_{key}"] += int(row[f"treatment_{key}"])
            m["top1_wins"] += int(row["top1_win"])
            m["top1_losses"] += int(row["top1_loss"])
            m["top80_gains"] += int(row["top80_gain"])
            m["top80_losses"] += int(row["top80_loss"])
            m["prior_generated_candidates"] += int(row["prior_generated_candidates"])
            m["prior_positive_candidates"] += int(row["prior_positive_candidates"])
            m["prior_false_candidates"] += int(row["prior_false_candidates"])
    scorecard: list[dict[str, Any]] = []
    for name, m in metrics.items():
        generated = int(m["prior_generated_candidates"])
        scorecard.append(
            {
                "slice": name,
                "groups": int(m["groups"]),
                "baseline_top1": int(m["baseline_top1"]),
                "treatment_top1": int(m["treatment_top1"]),
                "delta_top1": int(m["treatment_top1"] - m["baseline_top1"]),
                "baseline_top5": int(m["baseline_top5"]),
                "treatment_top5": int(m["treatment_top5"]),
                "delta_top5": int(m["treatment_top5"] - m["baseline_top5"]),
                "baseline_top20": int(m["baseline_top20"]),
                "treatment_top20": int(m["treatment_top20"]),
                "delta_top20": int(m["treatment_top20"] - m["baseline_top20"]),
                "baseline_top80": int(m["baseline_top80"]),
                "treatment_top80": int(m["treatment_top80"]),
                "delta_top80": int(m["treatment_top80"] - m["baseline_top80"]),
                "top1_wins": int(m["top1_wins"]),
                "top1_losses": int(m["top1_losses"]),
                "top80_gains": int(m["top80_gains"]),
                "top80_losses": int(m["top80_losses"]),
                "prior_generated_candidates": generated,
                "prior_positive_candidates": int(m["prior_positive_candidates"]),
                "prior_false_candidates": int(m["prior_false_candidates"]),
                "prior_false_candidate_rate": round(m["prior_false_candidates"] / generated, 6) if generated else 0.0,
            }
        )
    scorecard.sort(key=lambda row: (0 if row["slice"] == "all" else 1, row["slice"]))
    return scorecard


def _update_status(path: Path, report: dict[str, Any]) -> None:
    heldout = report["headline"]["heldout"]
    hard = report["headline"]["hard"]
    lines = [
        "# Current Goal Roadmap Status",
        "",
        f"Updated: {report['updated_at']} Asia/Shanghai",
        "",
        "## Where We Are",
        "",
        "Current stage: **16.1 validation substrate repair / local quota.db binding completed**.",
        "",
        f"Decision: **{report['decision']}**.",
        "",
        "## Real A/B Result",
        "",
        f"- heldout delta Top1/Top5/Top20/Top80: `{heldout['delta_top1']}/{heldout['delta_top5']}/{heldout['delta_top20']}/{heldout['delta_top80']}`.",
        f"- hard delta Top1/Top5/Top20/Top80: `{hard['delta_top1']}/{hard['delta_top5']}/{hard['delta_top20']}/{hard['delta_top80']}`.",
        f"- heldout wins/losses Top1: `{heldout['top1_wins']}/{heldout['top1_losses']}`.",
        f"- hard wins/losses Top1: `{hard['top1_wins']}/{hard['top1_losses']}`.",
        "",
        "## What This Means",
        "",
        report["interpretation"],
        "",
        "## Next Meaningful Action",
        "",
        report["next_stage"]["description"],
        "",
        "## Boundaries",
        "",
        "- Do not release 15A strict alias.",
        "- Do not tune from heldout/hard.",
        "- Do not enable online behavior by default.",
        "- Use this only as validation evidence for the broader OSS recall/index redesign.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    if not path.exists():
        return
    import re

    text = path.read_text(encoding="utf-8")
    heldout = report["headline"]["heldout"]
    hard = report["headline"]["hard"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前阶段：16.1 validation substrate repair / local quota.db binding 已完成，并已跑真实 GoalSearcher heldout/hard A/B。\n"
        f"结论：{report['decision']}。\n"
        f"heldout delta Top1/Top5/Top20/Top80={heldout['delta_top1']}/{heldout['delta_top5']}/{heldout['delta_top20']}/{heldout['delta_top80']}；"
        f"hard={hard['delta_top1']}/{hard['delta_top5']}/{hard['delta_top20']}/{hard['delta_top80']}。\n"
        f"下一步：{report['next_stage']['recommended']}。\n"
        "禁止：发布 15A、从 heldout/hard 调参/训练、默认启用、上线。"
    )
    text = re.sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: f'<textarea id="nextPrompt" readonly>{prompt}</textarea>',
        text,
        count=1,
        flags=re.S,
    )
    if "16.1 validation substrate repair / local quota.db binding" not in text:
        row = f"""          <tr>
            <td>16.1 validation substrate repair / local quota.db binding</td>
            <td>Bound validation to local-assets quota.db files and ran real baseline vs guarded-alias-on GoalSearcher heldout/hard A/B.</td>
            <td><code>{report['artifacts']['summary_json']}</code></td>
          </tr>
"""
        marker = "        </tbody>"
        if marker in text:
            text = text.replace(marker, row + marker, 1)
    text = re.sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {report['updated_at']} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="16.x local-assets GoalSearcher A/B for OSS prior candidates")
    parser.add_argument("--db-dir", type=Path, default=DEFAULT_DB_DIR)
    parser.add_argument("--heldout", type=Path, default=DEFAULT_HELDOUT)
    parser.add_argument("--hard", type=Path, default=DEFAULT_HARD)
    parser.add_argument("--index", type=Path, default=DEFAULT_INDEX)
    parser.add_argument("--impacted-row-audit", type=Path, default=DEFAULT_IMPACTED_AUDIT)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--dashboard", type=Path, default=DEFAULT_DASHBOARD)
    parser.add_argument("--candidate-kind", choices=("alias", "recall"), default="alias")
    parser.add_argument("--recall-min-support", type=int, default=int(getattr(config, "OSS_RECALL_INDEX_MIN_SUPPORT", 2) or 2))
    parser.add_argument("--recall-min-source-families", type=int, default=int(getattr(config, "OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES", 2) or 2))
    parser.add_argument("--recall-min-overlap", type=int, default=int(getattr(config, "OSS_RECALL_INDEX_MIN_OVERLAP", 2) or 2))
    parser.add_argument(
        "--recall-intervention-mode",
        choices=("broad", "exact_name"),
        default=str(getattr(config, "OSS_RECALL_INDEX_INTERVENTION_MODE", "broad") or "broad"),
    )
    parser.add_argument(
        "--recall-core-families",
        default=",".join(getattr(config, "OSS_RECALL_INDEX_CORE_FAMILIES", tuple(sorted(CORE_FAMILIES)))),
    )
    parser.add_argument("--limit-per-split", type=int, default=0, help="Smoke-test limit. 0 means full split.")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument(
        "--impacted-only",
        action="store_true",
        help="Run A/B only on rows where 15A generates candidates; other rows have zero treatment delta by construction.",
    )
    args = parser.parse_args()

    _configure_db_root(args.db_dir)
    heldout_input = _read_jsonl(args.heldout)
    hard_input = _read_jsonl(args.hard)
    if args.limit_per_split > 0:
        heldout_input = heldout_input[: args.limit_per_split]
        hard_input = hard_input[: args.limit_per_split]
    provinces = sorted({clean_text(row.get("province")) for row in heldout_input + hard_input if clean_text(row.get("province"))})
    unresolved = []
    province_cache: dict[str, str] = {}
    for province in provinces:
        try:
            province_cache[province] = config.resolve_province(province)
        except Exception as exc:  # pragma: no cover - reported in artifact.
            unresolved.append({"province": province, "error": str(exc).splitlines()[0]})
    if unresolved:
        raise ValueError(f"unresolved validation provinces: {unresolved[:5]}")

    if args.candidate_kind == "recall":
        recall_core_families = {part.strip() for part in args.recall_core_families.split(",") if part.strip()}
        config.OSS_RECALL_INDEX_PATH = str(args.index)
        config.OSS_RECALL_INDEX_MIN_SUPPORT = args.recall_min_support
        config.OSS_RECALL_INDEX_MIN_SOURCE_FAMILIES = args.recall_min_source_families
        config.OSS_RECALL_INDEX_MIN_OVERLAP = args.recall_min_overlap
        config.OSS_RECALL_INDEX_INTERVENTION_MODE = args.recall_intervention_mode
        config.OSS_RECALL_INDEX_CORE_FAMILIES = tuple(sorted(recall_core_families))
        reset_oss_recall_prior_source()
        candidate_source = OssRecallPriorSource(
            args.index,
            min_support=args.recall_min_support,
            min_source_families=args.recall_min_source_families,
            min_overlap=args.recall_min_overlap,
            intervention_mode=args.recall_intervention_mode,
            core_families=recall_core_families,
        )
        family_label = "_".join(sorted(recall_core_families)) or "none"
        candidate_label = f"16B_OSS_RECALL_MULTI_FIELD_SUPPORT_{args.recall_intervention_mode.upper()}_{family_label}"
    else:
        config.OSS_GUARDED_ALIAS_INDEX_PATH = str(args.index)
        reset_guarded_oss_alias_prior_source()
        candidate_source = GuardedOssAliasPriorSource(args.index, min_support=2, core_families=CORE_FAMILIES)
        candidate_label = "15A_GUARDED_CORE_STRICT_ALIAS_SUPPORT2"
    total_heldout_groups = len(heldout_input)
    total_hard_groups = len(hard_input)
    if args.impacted_only:
        impacted_ordinals = _read_impacted_ordinals(args.impacted_row_audit) if args.candidate_kind == "alias" else {}
        if impacted_ordinals:
            heldout_input = [row for index, row in enumerate(heldout_input, start=1) if index in impacted_ordinals.get("heldout", set())]
            hard_input = [row for index, row in enumerate(hard_input, start=1) if index in impacted_ordinals.get("hard", set())]
        else:
            heldout_input = _impacted_rows(heldout_input, candidate_source, args.candidate_kind, province_cache)
            hard_input = _impacted_rows(hard_input, candidate_source, args.candidate_kind, province_cache)
        print(
            f"impacted-only rows: heldout={len(heldout_input)}/{total_heldout_groups}, hard={len(hard_input)}/{total_hard_groups}",
            flush=True,
        )
    heldout_rows, heldout_scorecard = _evaluate_split(
        "heldout",
        heldout_input,
        candidate_source,
        args.candidate_kind,
        progress_every=args.progress_every,
        province_cache=province_cache,
    )
    hard_rows, hard_scorecard = _evaluate_split(
        "hard",
        hard_input,
        candidate_source,
        args.candidate_kind,
        progress_every=args.progress_every,
        province_cache=province_cache,
    )
    all_rows = heldout_rows + hard_rows
    all_scorecard = _scorecard(all_rows)
    heldout_head = next(row for row in heldout_scorecard if row["slice"] == "all")
    hard_head = next(row for row in hard_scorecard if row["slice"] == "all")
    all_head = next(row for row in all_scorecard if row["slice"] == "all")
    taxonomy_generated = sum(int(row["prior_generated_candidates"]) for row in all_scorecard if row["slice"] == "taxonomy_empty")
    top1_loss = int(all_head["top1_losses"])
    top1_net = int(all_head["delta_top1"])
    presence_net = int(all_head["delta_top80"])
    false_dominant = int(all_head["prior_false_candidates"]) > int(all_head["prior_positive_candidates"])

    stop_conditions = [
        {"check": "validation_substrate", "status": "pass", "evidence": f"resolved_provinces={len(provinces)}, db_dir={args.db_dir}"},
        {"check": "taxonomy_empty_block", "status": "pass" if taxonomy_generated == 0 else "fail", "evidence": f"taxonomy_empty prior_generated_candidates={taxonomy_generated}"},
        {"check": "top1_loss_guard", "status": "pass" if top1_loss == 0 else "fail", "evidence": f"top1_losses={top1_loss}, top1_wins={all_head['top1_wins']}"},
        {"check": "top1_positive_net", "status": "pass" if top1_net > 0 else "fail", "evidence": f"delta_top1={top1_net}"},
        {"check": "top80_positive_net", "status": "pass" if presence_net > 0 else "fail", "evidence": f"delta_top80={presence_net}"},
        {"check": "false_candidate_dominance", "status": "fail" if false_dominant else "pass", "evidence": f"false={all_head['prior_false_candidates']}, positive={all_head['prior_positive_candidates']}"},
    ]
    failed = [row for row in stop_conditions if row["status"] == "fail"]
    if not failed:
        decision = "local_assets_ab_pass_request_default_off_integration_review"
        interpretation = "The validation substrate works and the guarded alias package passed A/B stop conditions."
        next_stage = {
            "recommended": "default-off integration review",
            "description": "Proceed only to a default-off integration review; still no online enablement without explicit release approval.",
        }
    else:
        decision = "local_assets_ab_stop_15a_continue_broader_oss_recall_index_redesign"
        interpretation = "The validation substrate is fixed, but 15A still does not pass release criteria. The useful result is that we can now run real A/B, and the next accuracy work should build a broader OSS recall/index generator rather than release strict alias."
        next_stage = {
            "recommended": "16.2 broader OSS recall/index generator implementation plan",
            "description": "Use the repaired validation substrate to build and evaluate a broader default-off OSS recall/index generator from XML bill-quota pairs, with source/province/family guards and real A/B scorecards.",
        }

    summary_json = args.output_prefix.with_name(args.output_prefix.name + "_summary.json")
    scorecard_csv = args.output_prefix.with_name(args.output_prefix.name + "_scorecard.csv")
    row_csv = args.output_prefix.with_name(args.output_prefix.name + "_row_audit.csv")
    stop_csv = args.output_prefix.with_name(args.output_prefix.name + "_stop_conditions.csv")
    report = {
        "stage": "16.1 validation substrate repair / local quota.db binding",
        "validation_only": True,
        "smoke_limit_per_split": args.limit_per_split,
        "impacted_only": args.impacted_only,
        "total_groups_before_impacted_filter": {"heldout": total_heldout_groups, "hard": total_hard_groups},
        "db_dir": str(args.db_dir),
        "resolved_validation_provinces": len(provinces),
        "candidate_kind": args.candidate_kind,
        "candidate": candidate_label,
        "trained": False,
        "tuned": False,
        "online_default_changed": False,
        "headline": {"heldout": heldout_head, "hard": hard_head, "all": all_head},
        "scorecard": {"heldout": heldout_scorecard, "hard": hard_scorecard, "all": all_scorecard},
        "stop_conditions": stop_conditions,
        "decision": decision,
        "interpretation": interpretation,
        "next_stage": next_stage,
        "artifacts": {
            "summary_json": str(summary_json),
            "scorecard_csv": str(scorecard_csv),
            "row_audit_csv": str(row_csv),
            "stop_conditions_csv": str(stop_csv),
            "status_md": str(args.status),
            "dashboard_html": str(args.dashboard),
        },
        "anti_drift_conclusion": "16.1 only repaired validation substrate by pointing the validation process at local-assets quota.db files and ran A/B validation. It did not train, tune, expand 15A, change thresholds, enable online defaults, or release GoalSearcher changes.",
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _write_json(summary_json, report)
    score_rows = []
    for split, rows in (("heldout", heldout_scorecard), ("hard", hard_scorecard), ("all", all_scorecard)):
        for row in rows:
            score_rows.append({"split": split, **row})
    _write_csv(
        scorecard_csv,
        score_rows,
        [
            "split",
            "slice",
            "groups",
            "baseline_top1",
            "treatment_top1",
            "delta_top1",
            "baseline_top5",
            "treatment_top5",
            "delta_top5",
            "baseline_top20",
            "treatment_top20",
            "delta_top20",
            "baseline_top80",
            "treatment_top80",
            "delta_top80",
            "top1_wins",
            "top1_losses",
            "top80_gains",
            "top80_losses",
            "prior_generated_candidates",
            "prior_positive_candidates",
            "prior_false_candidates",
            "prior_false_candidate_rate",
        ],
    )
    _write_csv(row_csv, all_rows, list(all_rows[0].keys()) if all_rows else [])
    _write_csv(stop_csv, stop_conditions, ["check", "status", "evidence"])
    _update_status(args.status, report)
    _update_dashboard(args.dashboard, report)
    config.OSS_GUARDED_ALIAS_ENABLED = False
    config.OSS_RECALL_INDEX_ENABLED = False
    reset_guarded_oss_alias_prior_source()
    reset_oss_recall_prior_source()
    clear_goal_search_cache()
    print(json.dumps({"summary": str(summary_json), "decision": decision, "headline": report["headline"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
