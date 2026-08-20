from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.accuracy_baseline.providers import (  # noqa: E402
    GoalShadowProvider,
    ProductionProvider,
)
from eval.accuracy_baseline.runner import run_accuracy_baseline  # noqa: E402
from eval.accuracy_baseline.union_shadow import GoalUnionShadowProvider  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run read-only accuracy baseline evaluation"
    )
    parser.add_argument("--primary")
    parser.add_argument("--oss-diagnostic")
    parser.add_argument("--historical-stress")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--providers", default="production,goal_shadow")
    parser.add_argument("--goal-top-k", type=int, default=80)
    parser.add_argument(
        "--union-budget-policy",
        choices=("none", "production_40_goal_10"),
        default="none",
    )
    parser.add_argument("--min-slice-size", type=int, default=20)
    parser.add_argument("--with-experience", action="store_true")
    parser.add_argument("--provinces-db-dir")
    args = parser.parse_args()

    datasets = {
        name: value
        for name, value in {
            "primary": args.primary,
            "oss_diagnostic": args.oss_diagnostic,
            "historical_stress": args.historical_stress,
        }.items()
        if value
    }
    if not datasets:
        parser.error("at least one dataset path is required")
    requested = {value.strip() for value in args.providers.split(",") if value.strip()}
    unknown = requested - {
        "production",
        "goal_shadow",
        "production_goal_union_shadow",
    }
    if unknown:
        parser.error(f"unknown providers: {','.join(sorted(unknown))}")
    import config

    original_provinces_dir = config.PROVINCES_DB_DIR
    if args.provinces_db_dir:
        resolved_provinces_dir = Path(args.provinces_db_dir).resolve()
        if not resolved_provinces_dir.is_dir():
            parser.error(f"provinces db dir not found: {resolved_provinces_dir}")
        config.PROVINCES_DB_DIR = resolved_provinces_dir
    try:
        providers = []
        if "production" in requested:
            providers.append(ProductionProvider(with_experience=args.with_experience))
        if "goal_shadow" in requested:
            providers.append(GoalShadowProvider(top_k=args.goal_top_k))
        if "production_goal_union_shadow" in requested:
            providers.append(
                GoalUnionShadowProvider(
                    goal_top_k=args.goal_top_k,
                    candidate_budget_policy=args.union_budget_policy,
                )
            )

        payload = run_accuracy_baseline(
            datasets=datasets,
            output_dir=args.output_dir,
            providers=providers,
            min_slice_size=args.min_slice_size,
        )
    finally:
        config.PROVINCES_DB_DIR = original_provinces_dir
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
