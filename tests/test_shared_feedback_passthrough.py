import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

from app.api.shared import store_experience, store_experience_batch


def test_store_experience_passes_feedback_payload_to_store_one():
    async def run():
        with patch("tools.jarvis_store.store_one", return_value=True) as mock_store:
            ok = await store_experience(
                name="??????",
                desc="DN100 ??",
                quota_ids=["C10-1-1"],
                quota_names=["??????"],
                reason="Web???",
                specialty="C10",
                province="???",
                confirmed=True,
                feedback_payload={"trace": {"path": ["search_select"]}},
            )
            assert ok is True
            assert mock_store.call_args.kwargs["feedback_payload"] == {"trace": {"path": ["search_select"]}}

    asyncio.run(run())


def test_store_experience_waits_for_write_completion_before_returning():
    events = []

    async def fake_store(**kwargs):
        await asyncio.sleep(0)
        events.append(f"stored:{kwargs['name']}")
        return True

    async def run():
        with patch("app.api.shared._store_experience_now", side_effect=fake_store):
            ok = await store_experience(
                name="test-bill",
                desc="desc",
                quota_ids=["Q-1"],
                quota_names=["Quota 1"],
                reason="reason",
                specialty="C10",
                province="Guangdong",
                confirmed=True,
            )
            assert ok is True
            assert events == ["stored:test-bill"]

    asyncio.run(run())


def test_store_experience_batch_waits_for_write_completion_before_returning():
    events = []

    async def fake_store_batch(**kwargs):
        await asyncio.sleep(0)
        events.append("batch-stored")
        return len(kwargs["records"])

    async def run():
        records = [
            {"name": "A", "quota_ids": ["Q-1"], "quota_names": ["Quota 1"], "specialty": "C10"},
            {"name": "B", "quota_ids": ["Q-2"], "quota_names": ["Quota 2"], "specialty": "C10"},
        ]
        with patch("app.api.shared._store_experience_batch_now", side_effect=fake_store_batch):
            count = await store_experience_batch(
                records=records,
                province="Guangdong",
                reason="reason",
                confirmed=True,
            )
            assert count == 2
            assert events == ["batch-stored"]

    asyncio.run(run())
