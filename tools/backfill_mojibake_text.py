import asyncio
import argparse
import sys
import uuid
from pathlib import Path

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.models  # noqa: F401,E402
from app.database import async_session  # noqa: E402
from app.models.result import MatchResult  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.text_utils import repair_mojibake_data, repair_mojibake_text, repair_quota_name_loss  # noqa: E402


def _repair_text(value):
    if value is None:
        return None
    return repair_mojibake_text(str(value), preserve_newlines=True)


def _repair_json(value):
    if value is None:
        return None
    return repair_mojibake_data(value, preserve_newlines=True)


def _set_if_changed(obj, field_name, repaired) -> bool:
    original = getattr(obj, field_name)
    if repaired != original:
        setattr(obj, field_name, repaired)
        return True
    return False


def _normalize_task(task: Task) -> bool:
    changed = False
    for field_name in (
        "name",
        "original_filename",
        "province",
        "progress_message",
        "error_message",
    ):
        changed |= _set_if_changed(task, field_name, _repair_text(getattr(task, field_name)))

    for field_name in ("stats", "feedback_stats"):
        changed |= _set_if_changed(task, field_name, _repair_json(getattr(task, field_name)))
    return changed


def _normalize_match_result(result: MatchResult) -> bool:
    changed = False
    for field_name in (
        "bill_name",
        "bill_description",
        "bill_unit",
        "specialty",
        "sheet_name",
        "section",
        "match_source",
        "explanation",
        "review_note",
        "openclaw_review_note",
        "openclaw_review_actor",
        "openclaw_retry_query",
        "openclaw_review_confirmed_by",
    ):
        changed |= _set_if_changed(result, field_name, _repair_text(getattr(result, field_name)))

    quotas = _repair_json(getattr(result, "quotas"))
    corrected_quotas = _repair_json(getattr(result, "corrected_quotas"))
    alternatives = _repair_json(getattr(result, "alternatives"))
    trace = _repair_json(getattr(result, "trace"))
    openclaw_suggested_quotas = _repair_json(getattr(result, "openclaw_suggested_quotas"))
    openclaw_reason_codes = _repair_json(getattr(result, "openclaw_reason_codes"))
    openclaw_review_payload = _repair_json(getattr(result, "openclaw_review_payload"))
    human_feedback_payload = _repair_json(getattr(result, "human_feedback_payload"))

    openclaw_suggested_quotas, _ = repair_quota_name_loss(
        openclaw_suggested_quotas,
        alternatives or [],
        corrected_quotas or [],
        quotas or [],
        preserve_newlines=True,
    )
    if isinstance(openclaw_review_payload, dict):
        payload_suggested, payload_changed = repair_quota_name_loss(
            openclaw_review_payload.get("suggested_quotas"),
            openclaw_suggested_quotas or [],
            alternatives or [],
            corrected_quotas or [],
            quotas or [],
            preserve_newlines=True,
        )
        if payload_changed:
            openclaw_review_payload = dict(openclaw_review_payload)
            openclaw_review_payload["suggested_quotas"] = payload_suggested

    for field_name, repaired in (
        ("quotas", quotas),
        ("corrected_quotas", corrected_quotas),
        ("alternatives", alternatives),
        ("trace", trace),
        ("openclaw_suggested_quotas", openclaw_suggested_quotas),
        ("openclaw_reason_codes", openclaw_reason_codes),
        ("openclaw_review_payload", openclaw_review_payload),
        ("human_feedback_payload", human_feedback_payload),
    ):
        changed |= _set_if_changed(result, field_name, repaired)
    return changed


async def _scan_rows(session, stmt, normalizer, limit: int | None) -> tuple[int, int]:
    changed = 0
    scanned = 0
    result = await session.execute(stmt)
    for row in result.scalars():
        scanned += 1
        if normalizer(row):
            changed += 1
        if limit and scanned >= limit:
            break
    return scanned, changed


async def _async_main() -> int:
    parser = argparse.ArgumentParser(description="Backfill mojibake text stored in tasks/match_results.")
    parser.add_argument("--apply", action="store_true", help="Write repaired text back to the database.")
    parser.add_argument("--limit", type=int, default=None, help="Scan at most N rows per table.")
    parser.add_argument("--tasks-only", action="store_true", help="Only scan tasks.")
    parser.add_argument("--results-only", action="store_true", help="Only scan match_results.")
    parser.add_argument("--task-id", type=str, default=None, help="Only scan a single task and its results.")
    args = parser.parse_args()

    if args.tasks_only and args.results_only:
        parser.error("--tasks-only and --results-only cannot be used together")

    task_id = None
    if args.task_id:
        try:
            task_id = uuid.UUID(args.task_id)
        except ValueError as exc:
            parser.error(f"--task-id must be a valid UUID: {exc}")

    async with async_session() as session:
        task_scanned = task_changed = 0
        result_scanned = result_changed = 0

        if not args.results_only:
            task_stmt = select(Task)
            if task_id:
                task_stmt = task_stmt.where(Task.id == task_id)
            task_scanned, task_changed = await _scan_rows(session, task_stmt, _normalize_task, args.limit)

        if not args.tasks_only:
            result_stmt = select(MatchResult)
            if task_id:
                result_stmt = result_stmt.where(MatchResult.task_id == task_id)
            result_scanned, result_changed = await _scan_rows(
                session,
                result_stmt,
                _normalize_match_result,
                args.limit,
            )

        total_changed = task_changed + result_changed
        print(
            f"tasks: scanned={task_scanned} changed={task_changed}; "
            f"match_results: scanned={result_scanned} changed={result_changed}"
        )

        if args.apply:
            if total_changed > 0:
                await session.commit()
                print("database updated")
            else:
                await session.rollback()
                print("no changes needed")
        else:
            await session.rollback()
            print("dry-run only; rerun with --apply to persist changes")
        return 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    raise SystemExit(main())
