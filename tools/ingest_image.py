from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.source_pack import build_source_pack, make_source_id, read_text_with_fallbacks, safe_text, summarize_text, write_source_pack  # noqa: E402


def _ocr_with_rapidocr(path: Path) -> str:
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR

    image = Image.open(path)
    try:
        ocr = RapidOCR()
        result = ocr(image)
    finally:
        image.close()

    items = result[0] if isinstance(result, tuple) else result
    lines: list[str] = []
    for item in items or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        text_info = item[1]
        text = safe_text(text_info[0] if isinstance(text_info, (list, tuple)) and text_info else text_info)
        if text:
            lines.append(text)
    return "\n".join(lines).strip()


def extract_image_text(image_path: str | Path, *, ocr_file: str | Path | None = None, caption: str = "") -> tuple[str, list[str]]:
    source_path = Path(image_path).resolve()
    evidence_refs = [str(source_path)]
    segments: list[str] = []

    if safe_text(caption):
        segments.append(f"人工描述:\n{safe_text(caption)}")

    if ocr_file:
        sidecar = Path(ocr_file).resolve()
        evidence_refs.append(str(sidecar))
        sidecar_text = read_text_with_fallbacks(sidecar)
        if safe_text(sidecar_text):
            segments.append(f"OCR/旁注:\n{safe_text(sidecar_text)}")
        return "\n\n".join(segments).strip(), evidence_refs

    auto_sidecar = source_path.with_suffix(source_path.suffix + ".txt")
    if auto_sidecar.exists():
        evidence_refs.append(str(auto_sidecar))
        sidecar_text = read_text_with_fallbacks(auto_sidecar)
        if safe_text(sidecar_text):
            segments.append(f"OCR/旁注:\n{safe_text(sidecar_text)}")
        return "\n\n".join(segments).strip(), evidence_refs

    try:
        ocr_text = _ocr_with_rapidocr(source_path)
    except Exception as exc:
        if segments:
            return "\n\n".join(segments).strip(), evidence_refs
        raise RuntimeError("image OCR requires --ocr-file, --caption, or RapidOCR/Pillow availability") from exc

    if safe_text(ocr_text):
        segments.append(f"OCR:\n{safe_text(ocr_text)}")
    return "\n\n".join(segments).strip(), evidence_refs


def ingest_image(*, input_path: str | Path, title: str = "", province: str = "", specialty: str = "", tags: list[str] | None = None, confidence: int = 70, ocr_file: str | Path | None = None, caption: str = "") -> dict[str, str]:
    source_path = Path(input_path).resolve()
    extracted_text, evidence_refs = extract_image_text(source_path, ocr_file=ocr_file, caption=caption)
    resolved_title = safe_text(title) or source_path.stem
    source_id = make_source_id(source_kind="image", title=resolved_title, source_path=source_path)
    full_text = "\n".join(
        [
            f"Image Title: {resolved_title}",
            f"Image Path: {source_path}",
            "",
            safe_text(extracted_text) or "无可用 OCR 或人工描述",
        ]
    ).strip()
    pack = build_source_pack(
        source_id=source_id,
        source_kind="image",
        title=resolved_title,
        summary=summarize_text(extracted_text or resolved_title, max_chars=180),
        full_text_path="",
        evidence_refs=evidence_refs,
        province=province,
        specialty=specialty,
        tags=(tags or []) + ["image"],
        confidence=confidence,
        metadata={"input_ext": source_path.suffix.lower()},
    )
    pack_path, text_path = write_source_pack(pack=pack, full_text=full_text)
    return {"source_id": source_id, "pack_path": str(pack_path), "full_text_path": str(text_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest one image into source pack format.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--ocr-file", default="")
    parser.add_argument("--caption", default="")
    parser.add_argument("--title", default="")
    parser.add_argument("--province", default="")
    parser.add_argument("--specialty", default="")
    parser.add_argument("--tags", default="")
    parser.add_argument("--confidence", type=int, default=70)
    args = parser.parse_args()

    result = ingest_image(
        input_path=args.input,
        ocr_file=args.ocr_file or None,
        caption=args.caption,
        title=args.title,
        province=args.province,
        specialty=args.specialty,
        tags=[item.strip() for item in args.tags.split(",") if item.strip()],
        confidence=args.confidence,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
