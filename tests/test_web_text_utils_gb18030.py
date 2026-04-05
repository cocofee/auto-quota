import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "web" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from app.text_utils import repair_mojibake_data, repair_mojibake_text, repair_quota_name_loss  # noqa: E402


def _garble_gb18030(text: str) -> str:
    return text.encode("utf-8").decode("gb18030")


def test_repair_mojibake_text_repairs_utf8_gb18030_mojibake():
    original = "\u5efa\u8bae\u5b9a\u989d"
    garbled = _garble_gb18030(original)
    assert repair_mojibake_text(garbled, preserve_newlines=True) == original


def test_repair_mojibake_data_repairs_nested_utf8_gb18030_mojibake():
    original = {
        "task": "\u5efa\u8bae\u5b9a\u989d",
        "quotas": [
            {"quota_id": "C10-1-1", "name": "\u5efa\u8bae\u5b9a\u989d", "unit": "m"},
            {"quota_id": "C10-1-2", "name": "\u5efa\u8bae\u5b9a\u989d", "unit": "m"},
        ],
    }
    garbled = {
        "task": _garble_gb18030(original["task"]),
        "quotas": [
            {
                "quota_id": "C10-1-1",
                "name": _garble_gb18030(original["quotas"][0]["name"]),
                "unit": "m",
            },
            {
                "quota_id": "C10-1-2",
                "name": _garble_gb18030(original["quotas"][1]["name"]),
                "unit": "m",
            },
        ],
    }

    assert repair_mojibake_data(garbled, preserve_newlines=True) == original


def test_repair_quota_name_loss_reuses_clean_name_from_same_quota_id():
    clean_name = "\u5957\u63a5\u7d27\u5b9a\u5f0f\u9540\u950c\u94a2\u5bfc\u7ba1"
    repaired, changed = repair_quota_name_loss(
        [{"quota_id": "C10-1-1", "name": "??????????"}],
        [{"quota_id": "C10-1-1", "name": clean_name}],
        preserve_newlines=True,
    )

    assert changed is True
    assert repaired[0]["name"] == clean_name
