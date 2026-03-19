import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web" / "backend"))

from app.api.shared import store_experience


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
