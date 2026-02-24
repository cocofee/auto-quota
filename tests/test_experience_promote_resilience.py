from unittest.mock import patch

import tools.experience_promote as experience_promote


class _FailingConn:
    def __init__(self):
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("delete failed")

    def commit(self):
        return None

    def close(self):
        self.closed = True


class _FakeDB:
    def __init__(self, conn):
        self._conn = conn

    def get_candidate_records(self, province=None, limit=50):
        return [
            {
                "id": 1,
                "bill_name": "测试清单",
                "bill_text": "测试清单 描述",
                "quota_ids": ["C10-1-1"],
                "quota_names": ["定额A"],
                "source": "auto_review",
                "confidence": 80,
                "notes": "",
            }
        ]

    def _connect(self):
        return self._conn


def test_interactive_review_closes_connection_when_delete_fails():
    conn = _FailingConn()
    fake_db = _FakeDB(conn)

    with patch.object(experience_promote, "ExperienceDB", return_value=fake_db):
        with patch("builtins.input", side_effect=["d"]):
            experience_promote.interactive_review()

    assert conn.closed is True
