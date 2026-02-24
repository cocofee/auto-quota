import shutil
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import tools.import_reference as import_reference


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"import-reference-{uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


class _FailingConn:
    def __init__(self):
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("count failed")

    def close(self):
        self.closed = True


def test_select_quota_db_closes_connection_on_count_error():
    tmp_dir = _new_tmp_dir()
    try:
        provinces = ["省A", "省B"]
        db_paths = {}
        for p in provinces:
            db_path = tmp_dir / f"{p}.db"
            db_path.write_text("x", encoding="utf-8")
            db_paths[p] = db_path

        conn_a = _FailingConn()
        conn_b = _FailingConn()

        with patch.object(import_reference.config, "list_db_provinces", return_value=provinces):
            with patch.object(import_reference.config, "get_quota_db_path", side_effect=lambda p: db_paths[p]):
                with patch("db.sqlite.connect", side_effect=[conn_a, conn_b]):
                    with patch("builtins.input", return_value="1"):
                        selected = import_reference._select_quota_db()

        assert selected == "省A"
        assert conn_a.closed is True
        assert conn_b.closed is True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
