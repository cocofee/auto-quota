import asyncio
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace


BACKEND_ROOT = Path(__file__).resolve().parents[1] / 'web' / 'backend'
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.auth.deps import get_current_user  # noqa: E402
from app.auth.utils import create_access_token  # noqa: E402


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    def __init__(self, user):
        self._user = user

    async def execute(self, _query):
        return _Result(self._user)


def test_get_current_user_reads_access_token_from_cookie():
    user_id = uuid.uuid4()
    user = SimpleNamespace(id=user_id, is_active=True)
    token = create_access_token(str(user_id))
    request = SimpleNamespace(cookies={'access_token': token})
    db = _FakeDb(user)

    current_user = asyncio.run(
        get_current_user(
            request=request,
            credentials=None,
            db=db,
        )
    )

    assert current_user is user
