import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
import shutil

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

import app.config as app_config  # noqa: E402
from app.tasks import match_task  # noqa: E402


class _Session:
    def __init__(self, task):
        self.task = task
        self.closed = False
        self.rolled_back = False

    def get(self, _model, task_id):
        if task_id == self.task.id:
            return self.task
        return None

    def refresh(self, _task, attribute_names=None):
        return None

    def commit(self):
        return None

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"match-task-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def test_assert_task_not_cancelled_raises():
    task = SimpleNamespace(id=uuid.uuid4(), status="cancelled")
    session = _Session(task)

    with pytest.raises(match_task.TaskCancelled):
        match_task._assert_task_not_cancelled(session, task)


def test_execute_match_marks_task_cancelled(monkeypatch):
    task = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="demo",
        status="pending",
        progress=0,
        progress_current=0,
        progress_message="",
        error_message=None,
        started_at=None,
        completed_at=None,
        stats=None,
        output_path=None,
        json_output_path=None,
    )
    session = _Session(task)
    tmp_dir = _new_tmp_dir()
    try:
        upload = tmp_dir / "input.xlsx"
        upload.write_bytes(b"PK\x03\x04dummy")

        def _raise_cancel(*_args, **_kwargs):
            raise match_task.TaskCancelled("用户取消")

        monkeypatch.setattr(match_task, "get_sync_session", lambda: session)
        monkeypatch.setattr(match_task, "_execute_local_match", _raise_cancel)
        monkeypatch.setattr(app_config, "MATCH_BACKEND", "local", raising=False)

        match_task.execute_match.run(str(task.id), str(upload), {})

        assert task.status == "cancelled"
        assert task.progress_message == "用户取消"
        assert "用户取消" in task.error_message
        assert task.completed_at is not None
        assert session.closed
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
