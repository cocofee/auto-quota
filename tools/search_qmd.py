from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.qmd_index import QMDIndex  # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Search the QMD semantic index for knowledge_wiki.")
    parser.add_argument("query", help="Natural-language query")
    parser.add_argument("--wiki-root", default=str(PROJECT_ROOT / "knowledge_wiki"))
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--category")
    parser.add_argument("--type", dest="page_type")
    parser.add_argument("--province")
    parser.add_argument("--specialty")
    parser.add_argument("--source-kind")
    parser.add_argument("--status")
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def _print_human_results(results: list[dict[str, object]]) -> None:
    if not results:
        print("No results.")
        return

    for idx, item in enumerate(results, start=1):
        print(f"{idx}. [{item['score']:.4f}] {item['title']}")
        print(f"   path: {item['path']}")
        print(f"   heading: {item['heading']}")
        print(f"   category/type: {item['category']} / {item['type']}")
        if item.get("province") or item.get("specialty"):
            print(f"   province/specialty: {item.get('province', '')} / {item.get('specialty', '')}")
        if item.get("source_kind"):
            print(f"   source_kind: {item['source_kind']}")
        preview = str(item.get("preview", "")).replace("\n", " ").strip()
        if preview:
            print(f"   preview: {preview}")
        print()


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    index = QMDIndex(wiki_root=Path(args.wiki_root))
    results = index.search(
        args.query,
        top_k=args.top_k,
        category=args.category,
        page_type=args.page_type,
        province=args.province,
        specialty=args.specialty,
        source_kind=args.source_kind,
        status=args.status,
    )
    if args.as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        _print_human_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
