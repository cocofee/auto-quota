from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from tools.import_xml import convert_xml_to_pairs

AGENT_STATE = PROJECT_ROOT / "reports" / "agent_state"
DEFAULT_OSS_XML_ROOT = Path(r"D:\广联达临时文件\oss_samples")
DEFAULT_DASHBOARD = AGENT_STATE / "goal_learning_roadmap_dashboard.html"
DEFAULT_OUTPUT_PREFIX = AGENT_STATE / "goal_13x_oss_xml_mother_data_manifest"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _safe_rel(path: str) -> str:
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return path


def _top_dir(root: Path, path: Path) -> str:
    parts = path.relative_to(root).parts
    return parts[0] if parts else "<root>"


def _province_dir(root: Path, path: Path) -> str:
    parts = path.relative_to(root).parts
    if len(parts) >= 3 and parts[0] == "by_province":
        return parts[1]
    return ""


def _pick_sample(files: list[Path], sample_count: int) -> list[Path]:
    non_empty = [path for path in files if path.stat().st_size > 0]
    if not non_empty:
        return []
    sorted_files = sorted(non_empty, key=lambda path: path.stat().st_size)
    picks: list[Path] = []
    for idx in range(sample_count):
        pos = round(idx * (len(sorted_files) - 1) / max(sample_count - 1, 1))
        picks.append(sorted_files[pos])
    seen: set[tuple[str, int]] = set()
    deduped: list[Path] = []
    for path in picks:
        key = (path.name.lower(), path.stat().st_size)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def _write_markdown(path: Path, report: dict[str, Any]) -> None:
    m = report["metrics"]
    lines = [
        "# 13.3 OSS XML Mother-Data Manifest",
        "",
        "Manifest-only stage for the real OSS XML mother-data source. No training, heldout/hard selection, GoalSearcher change, or online integration.",
        "",
        "## Metrics",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| oss_xml_root | {m['oss_xml_root']} |",
        f"| file_count | {m['file_count']} |",
        f"| unique_name_size_count | {m['unique_name_size_count']} |",
        f"| total_gb | {m['total_gb']} |",
        f"| sampled_file_count | {m['sampled_file_count']} |",
        f"| sampled_pairs | {m['sampled_pairs']} |",
        f"| estimated_total_pairs | {m['estimated_total_pairs']} |",
        f"| parse_error_count | {m['parse_error_count']} |",
        "",
        "## Decision",
        "",
        report["decision"],
        "",
        "## Anti-drift",
        "",
        report["anti_drift_conclusion"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _update_dashboard(path: Path, report: dict[str, Any]) -> None:
    text = path.read_text(encoding="utf-8")
    m = report["metrics"]
    prompt = (
        "按 Goal Roadmap 看板执行。\n"
        "当前状态：13.3 OSS XML mother-data manifest 已完成。"
        f" oss_xml_root={m['oss_xml_root']}；file_count={m['file_count']}；unique_name_size_count={m['unique_name_size_count']}；"
        f"total_gb={m['total_gb']}；sampled_pairs={m['sampled_pairs']}；estimated_total_pairs={m['estimated_total_pairs']}。\n"
        "下一步建议：13.4 OSS XML source-aware training matrix build。先从 XML mother-data 抽取 bill-quota pairs，按 source/province/source_family 做 split，构建 dev/OOF-only reranker matrix；仍不使用 heldout/hard 做选择，不上线，不改 GoalSearcher。\n"
        "禁止：直接全量上线、把同源训练结果当泛化证明、用 source_file/expected_id 作模型特征、使用 heldout/hard 做候选选择。"
    )
    text = __import__("re").sub(
        r'<textarea id="nextPrompt" readonly>.*?</textarea>',
        lambda _match: '<textarea id="nextPrompt" readonly>' + prompt + '</textarea>',
        text,
        count=1,
        flags=__import__("re").S,
    )
    if "13.3 OSS XML mother-data manifest summary" not in text:
        marker = "          <tr>\n            <td>13.2 offline reranker data/source redesign summary</td>"
        rows = f"""          <tr>
            <td>13.3 OSS XML mother-data manifest summary</td>
            <td>Manifest and parse sample for the real OSS XML source at D:\\广联达临时文件\\oss_samples.</td>
            <td><code>{_safe_rel(report['artifacts']['summary_json'])}</code></td>
          </tr>
          <tr>
            <td>13.3 OSS XML mother-data manifest tables</td>
            <td>File inventory, directory rollup, and parser sample estimates for future source-aware matrix build.</td>
            <td><code>{_safe_rel(report['artifacts']['file_inventory_csv'])}</code> / <code>{_safe_rel(report['artifacts']['parse_sample_csv'])}</code></td>
          </tr>
"""
        text = text.replace(marker, rows + marker, 1)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    text = __import__("re").sub(r"Last updated: .*? Asia/Shanghai\.", f"Last updated: {stamp} Asia/Shanghai.", text, count=1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build 13.3 OSS XML mother-data manifest")
    parser.add_argument("--oss-xml-root", default=str(DEFAULT_OSS_XML_ROOT))
    parser.add_argument("--sample-count", type=int, default=12)
    parser.add_argument("--dashboard", default=str(DEFAULT_DASHBOARD))
    parser.add_argument("--output-prefix", default=str(DEFAULT_OUTPUT_PREFIX))
    args = parser.parse_args()

    started = time.perf_counter()
    root = Path(args.oss_xml_root)
    if not root.exists():
        raise FileNotFoundError(root)
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".xml"]
    file_rows: list[dict[str, Any]] = []
    for path in files:
        stat = path.stat()
        file_rows.append(
            {
                "path": str(path),
                "relative_path": str(path.relative_to(root)),
                "file_name": path.name,
                "size_bytes": stat.st_size,
                "top_dir": _top_dir(root, path),
                "province_dir": _province_dir(root, path),
                "unique_name_size_key": f"{path.name.lower()}::{stat.st_size}",
            }
        )
    dir_acc: dict[tuple[str, str], dict[str, Any]] = {}
    for row in file_rows:
        for scope, key in [("top_dir", row["top_dir"]), ("province_dir", row["province_dir"])]:
            if not key:
                continue
            acc = dir_acc.setdefault((scope, key), {"scope": scope, "key": key, "file_count": 0, "total_bytes": 0})
            acc["file_count"] += 1
            acc["total_bytes"] += row["size_bytes"]
    dir_rows = []
    for row in dir_acc.values():
        row["total_gb"] = round(row["total_bytes"] / (1024**3), 6)
        dir_rows.append(row)
    dir_rows.sort(key=lambda row: (row["scope"], -row["total_bytes"]))

    sample_rows: list[dict[str, Any]] = []
    for path in _pick_sample(files, args.sample_count):
        t0 = time.perf_counter()
        error = ""
        try:
            pairs = convert_xml_to_pairs(str(path))
        except Exception as exc:
            pairs = []
            error = repr(exc)
        elapsed = round(time.perf_counter() - t0, 3)
        size_mb = path.stat().st_size / (1024**2)
        pair_count = len(pairs)
        sample_rows.append(
            {
                "path": str(path),
                "relative_path": str(path.relative_to(root)),
                "size_bytes": path.stat().st_size,
                "size_mb": round(size_mb, 6),
                "pair_count": pair_count,
                "pairs_per_mb": round(pair_count / size_mb, 6) if size_mb else 0.0,
                "elapsed_sec": elapsed,
                "error": error,
            }
        )

    total_bytes = sum(row["size_bytes"] for row in file_rows)
    pairs_per_mb = [row["pairs_per_mb"] for row in sample_rows if row["pairs_per_mb"] > 0]
    median_pairs_per_mb = statistics.median(pairs_per_mb) if pairs_per_mb else 0.0
    estimated_total_pairs = int(median_pairs_per_mb * (total_bytes / (1024**2))) if median_pairs_per_mb else 0
    output_prefix = Path(args.output_prefix)
    artifacts = {
        "summary_json": str(output_prefix.with_name(output_prefix.name + "_summary.json")),
        "summary_md": str(output_prefix.with_name(output_prefix.name + "_summary.md")),
        "file_inventory_csv": str(output_prefix.with_name(output_prefix.name + "_file_inventory.csv")),
        "directory_rollup_csv": str(output_prefix.with_name(output_prefix.name + "_directory_rollup.csv")),
        "parse_sample_csv": str(output_prefix.with_name(output_prefix.name + "_parse_sample.csv")),
    }
    metrics = {
        "oss_xml_root": str(root),
        "file_count": len(file_rows),
        "unique_name_size_count": len({row["unique_name_size_key"] for row in file_rows}),
        "total_bytes": total_bytes,
        "total_gb": round(total_bytes / (1024**3), 6),
        "sampled_file_count": len(sample_rows),
        "sampled_pairs": sum(row["pair_count"] for row in sample_rows),
        "median_pairs_per_mb": round(median_pairs_per_mb, 6),
        "estimated_total_pairs": estimated_total_pairs,
        "parse_error_count": sum(1 for row in sample_rows if row["error"]),
        "training_executed": False,
        "heldout_used_for_selection": False,
        "hard_used_for_selection": False,
        "goal_searcher_changed": False,
        "elapsed_sec": round(time.perf_counter() - started, 3),
    }
    report = {
        "stage": "13.3 OSS XML mother-data manifest",
        "metrics": metrics,
        "artifacts": artifacts,
        "decision": "Use the real OSS XML mother-data directory as the next training-matrix source. The current stage only builds a manifest and parser sample estimate; the next step is a source-aware matrix build, not direct model release.",
        "anti_drift_conclusion": "No training, no heldout/hard selection, no online integration, no GoalSearcher change, and no feature whitelist change were performed.",
        "next_stage": {
            "recommended": "13.4 OSS XML source-aware training matrix build",
            "default": "build matrix first; do not train/release until matrix manifest and split checks pass",
        },
    }

    _write_csv(Path(artifacts["file_inventory_csv"]), file_rows, list(file_rows[0].keys()))
    _write_csv(Path(artifacts["directory_rollup_csv"]), dir_rows, ["scope", "key", "file_count", "total_bytes", "total_gb"])
    _write_csv(Path(artifacts["parse_sample_csv"]), sample_rows, ["path", "relative_path", "size_bytes", "size_mb", "pair_count", "pairs_per_mb", "elapsed_sec", "error"])
    _write_json(Path(artifacts["summary_json"]), report)
    _write_markdown(Path(artifacts["summary_md"]), report)
    _update_dashboard(Path(args.dashboard), report)
    print(json.dumps({"summary": artifacts["summary_json"], "metrics": metrics}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
