import shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

import src.experience_db as experience_db_mod


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"experience-db-init-{uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


class _FailingCursor:
    def execute(self, *args, **kwargs):
        raise RuntimeError("init ddl failed")


class _FailingConn:
    def __init__(self):
        self.closed = False

    def cursor(self):
        return _FailingCursor()

    def commit(self):
        return None

    def close(self):
        self.closed = True


def test_experience_db_init_closes_connection_on_init_error():
    tmp_dir = _new_tmp_dir()
    try:
        db_path = tmp_dir / "experience.db"
        conn = _FailingConn()
        db = experience_db_mod.ExperienceDB.__new__(experience_db_mod.ExperienceDB)
        db.db_path = db_path

        with patch.object(experience_db_mod, "_db_connect_init", return_value=conn):
            with pytest.raises(RuntimeError, match="init ddl failed"):
                db._init_db()

        assert conn.closed is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
