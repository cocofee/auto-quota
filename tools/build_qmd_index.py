from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.qmd_index import QMDIndex  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the QMD semantic index for knowledge_wiki.")
    parser.add_argument("--wiki-root", default=str(PROJECT_ROOT / "knowledge_wiki"))
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--overlap", type=int, default=120)
    args = parser.parse_args()

    index = QMDIndex(wiki_root=Path(args.wiki_root))
    manifest = index.rebuild_index(
        batch_size=args.batch_size,
        max_chars=args.chunk_size,
        overlap_chars=args.overlap,
        reset=True,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
