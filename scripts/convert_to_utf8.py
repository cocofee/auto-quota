from __future__ import annotations

import argparse
from pathlib import Path


TEXT_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".ps1",
    ".py",
    ".rb",
    ".sh",
    ".sql",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".vbs",
    ".yaml",
    ".yml",
}

SKIP_DIRS = {
    ".git",
    ".idea",
    ".pytest-tmp",
    ".pytest_tmp_knowledge_commit",
    ".pytest_tmp_knowledge_promotion",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "output",
    "tmp",
    "venv",
}

SOURCE_ENCODINGS = (
    "utf-8-sig",
    "utf-8",
    "gb18030",
    "utf-16",
    "utf-16-le",
    "utf-16-be",
)


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def looks_like_text(raw: bytes) -> bool:
    return b"\x00" not in raw


def decode_bytes(raw: bytes) -> tuple[str, str] | None:
    for encoding in SOURCE_ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return None


def iter_candidate_files(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if should_skip(path):
            continue
        if path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        candidates.append(path)
    return candidates


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert common text files in the repository to UTF-8."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Root directory to scan. Defaults to the current directory.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Write changes back to disk. Without this flag the script only reports.",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    changed = 0
    scanned = 0

    for path in iter_candidate_files(root):
        raw = path.read_bytes()
        if not looks_like_text(raw):
            continue
        scanned += 1
        decoded = decode_bytes(raw)
        if decoded is None:
            print(f"skip  {path}")
            continue

        text, source_encoding = decoded
        normalized = text.encode("utf-8")
        if raw == normalized:
            continue

        changed += 1
        action = "write" if args.write else "plan "
        print(f"{action} {path} [{source_encoding} -> utf-8]")
        if args.write:
            path.write_bytes(normalized)

    print(f"scanned={scanned} changed={changed} write={args.write}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
