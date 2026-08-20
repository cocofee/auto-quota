from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_reports(
    output_dir: str | Path,
    payload: dict[str, Any],
) -> dict[str, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary_json": root / "summary.json",
        "cases_jsonl": root / "cases.jsonl",
        "stage_attribution_csv": root / "stage_attribution.csv",
        "slice_metrics_csv": root / "slice_metrics.csv",
        "provider_comparison_csv": root / "provider_comparison.csv",
    }
    paths["summary_json"].write_text(
        json.dumps(
            payload["summary"],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    case_rows = sorted(
        payload.get("cases") or [],
        key=lambda row: (
            str(row.get("dataset") or ""),
            str(row.get("case_id") or (row.get("case") or {}).get("case_id") or ""),
            str((row.get("provider_result") or {}).get("provider_name") or ""),
        ),
    )
    paths["cases_jsonl"].write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in case_rows
        ),
        encoding="utf-8",
    )
    _write_csv(
        paths["stage_attribution_csv"],
        list(payload.get("stage_attribution") or []),
    )
    _write_csv(
        paths["slice_metrics_csv"],
        list(payload.get("slice_metrics") or []),
    )
    _write_csv(
        paths["provider_comparison_csv"],
        list(payload.get("provider_comparison") or []),
    )
    return paths
