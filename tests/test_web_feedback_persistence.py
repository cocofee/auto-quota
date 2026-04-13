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

from app.api.feedback import _commit_feedback_stats, _commit_feedback_upload


def test_commit_feedback_upload_only_flushes_marker():
    task = SimpleNamespace(feedback_path=None, feedback_uploaded_at=None)
    db = AsyncMock()

    asyncio.run(_commit_feedback_upload(db, task, save_path=Path('/tmp/feedback.xlsx')))

    assert task.feedback_path == str(Path('/tmp/feedback.xlsx'))
    assert task.feedback_uploaded_at is not None
    assert task.feedback_uploaded_at.tzinfo == timezone.utc
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()


def test_commit_feedback_stats_persists_error_state():
    task = SimpleNamespace(feedback_stats=None)
    db = AsyncMock()
    stats = {'status': 'learn_failed', 'error': 'boom'}

    asyncio.run(_commit_feedback_stats(db, task, stats))

    assert task.feedback_stats == stats
    db.commit.assert_awaited_once()
