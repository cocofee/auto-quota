import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sse_starlette.sse import EventSourceResponse


BACKEND_ROOT = Path(__file__).resolve().parents[1] / 'web' / 'backend'
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.api.tasks import task_progress  # noqa: E402


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def one_or_none(self):
        return self._value


class _Session:
    def __init__(self, values):
        self._values = list(values)

    async def execute(self, _query):
        if not self._values:
            raise AssertionError('unexpected execute call')
        return _Result(self._values.pop(0))


class _SessionCtx:
    def __init__(self, values):
        self._session = _Session(values)

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_task_progress_rejects_disabled_user():
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    request = SimpleNamespace(headers={'Authorization': 'Bearer valid-token'}, cookies={})

    with patch('app.api.tasks.decode_token', return_value={'type': 'access', 'sub': str(user_id)}):
        with patch('app.api.tasks.async_session', return_value=_SessionCtx([False])):
            with pytest.raises(HTTPException) as exc:
                asyncio.run(task_progress(task_id, request))

    assert exc.value.status_code == 403
    assert '禁用' in str(exc.value.detail)


def test_task_progress_accepts_cookie_access_token():
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    request = SimpleNamespace(headers={}, cookies={'access_token': 'cookie-token'})

    with patch('app.api.tasks.decode_token', return_value={'type': 'access', 'sub': str(user_id)}):
        with patch('app.api.tasks.async_session', return_value=_SessionCtx([True, (task_id, user_id)])):
            resp = asyncio.run(task_progress(task_id, request))

    assert isinstance(resp, EventSourceResponse)
