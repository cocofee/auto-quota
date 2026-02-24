import shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

import src.method_cards as method_cards_mod


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"method-cards-init-{uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


class _FailingConn:
    def __init__(self):
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("init ddl failed")

    def commit(self):
        return None

    def close(self):
        self.closed = True


def test_method_cards_init_closes_connection_on_init_error():
    tmp_dir = _new_tmp_dir()
    try:
        db_path = tmp_dir / "method_cards.db"
        conn = _FailingConn()
        mc = method_cards_mod.MethodCards.__new__(method_cards_mod.MethodCards)
        mc.db_path = db_path

        with patch.object(method_cards_mod, "_db_connect_init", return_value=conn):
            with pytest.raises(RuntimeError, match="init ddl failed"):
                mc._init_db()

        assert conn.closed is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
