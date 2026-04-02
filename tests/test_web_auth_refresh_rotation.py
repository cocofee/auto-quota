import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Response


BACKEND_ROOT = Path(__file__).resolve().parents[1] / 'web' / 'backend'
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.auth.router import refresh_token  # noqa: E402
from app.auth.utils import create_refresh_token, decode_token  # noqa: E402
from app.schemas.auth import RefreshTokenRequest  # noqa: E402


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.flush_called = False
        self.queries = []

    async def execute(self, query):
        if not self._results:
            raise AssertionError('unexpected db.execute call')
        self.queries.append(query)
        return _Result(self._results.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flush_called = True


def _request_with_cookies(cookies: dict[str, str] | None = None):
    return SimpleNamespace(cookies=(cookies or {}))


def test_create_refresh_token_contains_jti_claim():
    token = create_refresh_token('user-1', 'fixed-jti-1')
    payload = decode_token(token)
    assert payload is not None
    assert payload.get('type') == 'refresh'
    assert payload.get('jti') == 'fixed-jti-1'


def test_refresh_token_requires_jti_claim():
    user_id = str(uuid.uuid4())
    db = _FakeDb(results=[])
    req = RefreshTokenRequest(refresh_token='dummy')

    with patch('app.auth.router.decode_token', return_value={'sub': user_id, 'type': 'refresh'}):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                refresh_token(
                    response=Response(),
                    request=_request_with_cookies(),
                    req=req,
                    db=db,
                )
            )

    assert exc.value.status_code == 401
    assert 'jti' in str(exc.value.detail)


def test_refresh_token_rotates_and_revokes_old_jti():
    user_id = uuid.uuid4()
    old_jti = 'old-jti-123'
    user = SimpleNamespace(id=user_id, is_active=True)
    old_token_row = SimpleNamespace(revoked_at=None)
    db = _FakeDb(results=[user, old_token_row])
    req = RefreshTokenRequest(refresh_token='dummy')

    with patch(
        'app.auth.router.decode_token',
        return_value={'sub': str(user_id), 'type': 'refresh', 'jti': old_jti},
    ):
        resp = asyncio.run(
            refresh_token(
                response=Response(),
                request=_request_with_cookies(),
                req=req,
                db=db,
            )
        )

    assert old_token_row.revoked_at is not None
    assert db.flush_called is True
    assert len(db.added) == 1
    new_row = db.added[0]
    assert getattr(new_row, 'jti', None) != old_jti

    new_payload = decode_token(resp.refresh_token)
    assert new_payload is not None
    assert new_payload.get('jti') == getattr(new_row, 'jti', None)


def test_refresh_token_reads_cookie_when_body_missing():
    user_id = uuid.uuid4()
    old_jti = 'cookie-old-jti'
    user = SimpleNamespace(id=user_id, is_active=True)
    old_token_row = SimpleNamespace(revoked_at=None)
    db = _FakeDb(results=[user, old_token_row])

    with patch(
        'app.auth.router.decode_token',
        return_value={'sub': str(user_id), 'type': 'refresh', 'jti': old_jti},
    ):
        resp = asyncio.run(
            refresh_token(
                response=Response(),
                request=_request_with_cookies({'refresh_token': 'cookie-refresh-token'}),
                req=None,
                db=db,
            )
        )

    assert old_token_row.revoked_at is not None
    assert len(db.added) == 1
    new_payload = decode_token(resp.refresh_token)
    assert new_payload is not None
    assert new_payload.get('jti') == getattr(db.added[0], 'jti', None)


def test_refresh_token_uses_row_lock_for_rotation_query():
    user_id = uuid.uuid4()
    old_jti = 'lock-jti-123'
    user = SimpleNamespace(id=user_id, is_active=True)
    old_token_row = SimpleNamespace(revoked_at=None)
    db = _FakeDb(results=[user, old_token_row])
    req = RefreshTokenRequest(refresh_token='dummy')

    with patch(
        'app.auth.router.decode_token',
        return_value={'sub': str(user_id), 'type': 'refresh', 'jti': old_jti},
    ):
        asyncio.run(
            refresh_token(
                response=Response(),
                request=_request_with_cookies(),
                req=req,
                db=db,
            )
        )

    assert len(db.queries) >= 2
    assert getattr(db.queries[1], '_for_update_arg', None) is not None
