import io
import shutil
import sys
import uuid
from pathlib import Path

import pytest
from starlette.datastructures import UploadFile


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

import app.services.match_service as match_service  # noqa: E402


def _new_tmp_dir() -> Path:
    root = Path("output") / "test_tmp"
    root.mkdir(parents=True, exist_ok=True)
    d = root / f"web-upload-{uuid.uuid4().hex}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def test_save_upload_file_rejects_xlsx_with_invalid_signature(monkeypatch):
    tmp_dir = _new_tmp_dir()
    try:
        monkeypatch.setattr(match_service, "UPLOAD_DIR", tmp_dir)
        upload = UploadFile(filename="bad.xlsx", file=io.BytesIO(b"NOTAZIPDATA"))
        with pytest.raises(ValueError, match="Excel"):
            match_service.save_upload_file(upload, uuid.uuid4())
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_save_upload_file_accepts_valid_xlsx_signature(monkeypatch):
    tmp_dir = _new_tmp_dir()
    try:
        monkeypatch.setattr(match_service, "UPLOAD_DIR", tmp_dir)
        data = b"PK\x03\x04dummy-xlsx-content"
        upload = UploadFile(filename="ok.xlsx", file=io.BytesIO(data))
        task_id = uuid.uuid4()
        saved_path = match_service.save_upload_file(upload, task_id)

        assert saved_path.exists()
        assert saved_path.read_bytes() == data
        assert saved_path.suffix == ".xlsx"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_save_upload_file_accepts_mislabeled_xls_content(monkeypatch):
    tmp_dir = _new_tmp_dir()
    try:
        monkeypatch.setattr(match_service, "UPLOAD_DIR", tmp_dir)
        data = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1rest-of-xls"
        upload = UploadFile(filename="fake.xlsx", file=io.BytesIO(data))
        task_id = uuid.uuid4()
        saved_path = match_service.save_upload_file(upload, task_id)

        assert saved_path.exists()
        assert saved_path.read_bytes() == data
        assert saved_path.suffix == ".xls"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
