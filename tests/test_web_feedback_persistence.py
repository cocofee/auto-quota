import asyncio
import sys
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parents[1]
WEB_BACKEND = ROOT / 'web' / 'backend'
if str(WEB_BACKEND) not in sys.path:
    sys.path.insert(0, str(WEB_BACKEND))

from app.api.feedback import (
    _can_retry_feedback_upload,
    _commit_feedback_stats,
    _commit_feedback_upload,
)


def test_commit_feedback_upload_persists_marker_immediately():
    task = SimpleNamespace(feedback_path=None, feedback_uploaded_at=None)
    db = AsyncMock()

    asyncio.run(_commit_feedback_upload(db, task, save_path=Path('/tmp/feedback.xlsx')))

    assert task.feedback_path == str(Path('/tmp/feedback.xlsx'))
    assert task.feedback_uploaded_at is not None
    assert task.feedback_uploaded_at.tzinfo == timezone.utc
    db.commit.assert_awaited_once()


def test_commit_feedback_upload_can_stage_processing_status():
    task = SimpleNamespace(feedback_path=None, feedback_uploaded_at=None, feedback_stats=None)
    db = AsyncMock()

    asyncio.run(
        _commit_feedback_upload(
            db,
            task,
            save_path=Path('/tmp/feedback.xlsx'),
            stats={'status': 'processing'},
        )
    )

    assert task.feedback_stats == {'status': 'processing'}
    db.commit.assert_awaited_once()


def test_commit_feedback_stats_persists_error_state():
    task = SimpleNamespace(feedback_stats=None)
    db = AsyncMock()
    stats = {'status': 'learn_failed', 'error': 'boom'}

    asyncio.run(_commit_feedback_stats(db, task, stats))

    assert task.feedback_stats == stats
    db.commit.assert_awaited_once()


def test_can_retry_feedback_upload_only_after_failed_status():
    failed_task = SimpleNamespace(feedback_stats={'status': 'learn_failed'})
    processing_task = SimpleNamespace(feedback_stats={'status': 'processing'})
    completed_task = SimpleNamespace(feedback_stats={'status': 'completed'})
    empty_task = SimpleNamespace(feedback_stats=None)

    assert _can_retry_feedback_upload(failed_task) is True
    assert _can_retry_feedback_upload(processing_task) is False
    assert _can_retry_feedback_upload(completed_task) is False
    assert _can_retry_feedback_upload(empty_task) is False
