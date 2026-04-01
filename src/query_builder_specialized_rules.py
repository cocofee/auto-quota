# -*- coding: utf-8 -*-
"""Specialized query-builder rule clusters split out of query_builder.py."""

from __future__ import annotations

import re


def _build_metal_opening_query_parts(frame_material: str,
                                     *,
                                     is_window: bool,
                                     opening_type: str,
                                     opening_explicit: bool,
                                     thermal_break: bool,
                                     has_attached_frame: bool,
                                     include_fixed_window: bool = False) -> list[str]:
    family_suffix = "窗" if is_window else "门"
    family_term = f"{frame_material}{family_suffix}" if frame_material else ""
    query_parts: list[str] = []
    if family_term:
        query_parts.append(family_term)

    normalized_opening = str(opening_type or "").strip()
    if normalized_opening:
        opening_term = f"{frame_material}{normalized_opening}"
        if opening_term not in query_parts:
            query_parts.append(opening_term)

    if is_window and not opening_explicit and frame_material:
        for fallback in ("固定窗", "平开窗", "推拉窗"):
            fallback_term = f"{frame_material}{fallback}"
            if fallback_term not in query_parts:
                query_parts.append(fallback_term)

    if thermal_break:
        query_parts.append("隔热断桥型材")

    if include_fixed_window:
        fixed_term = f"{frame_material}固定窗"
        if fixed_term not in query_parts:
            query_parts.append(fixed_term)

    if has_attached_frame:
        query_parts.append("有附框")

    return query_parts


def _build_fire_door_query(text: str) -> str | None:
    raw_text = str(text or "")
    fire_door_keywords = (
        "钢质防火门",
        "钢制防火门",
        "甲级钢质防火门",
        "乙级钢质防火门",
        "丙级钢质防火门",
    )
    if not (
        "钢质防火门" in raw_text
        or "钢制防火门" in raw_text
        or any(keyword in raw_text for keyword in fire_door_keywords)
    ):
        return None

    fire_tokens = ["钢质防火门"]
    for grade in ("甲级", "乙级", "丙级"):
        if grade in raw_text:
            fire_tokens.append(grade)
            break
    return " ".join(fire_tokens)


def _build_fire_door_or_metal_opening_query(name: str, description: str = "") -> str | None:
    fire_door_query = _build_fire_door_query(f"{name} {description}")
    if fire_door_query:
        return fire_door_query

    if not (
        ("金属（塑钢" in name or "金属(塑钢" in name or "金属（断桥" in name or "金属(断桥" in name)
        and ("窗" in name or "门" in name)
    ):
        return None

    detail_text = str(description or "")
    material_text = f"{name} {detail_text}"
    nested_fire_door_query = _build_fire_door_query(detail_text) or _build_fire_door_query(material_text)
    if nested_fire_door_query:
        return nested_fire_door_query

    has_attached_frame = any(token in detail_text for token in ("附框", "钢附框", "木塑附框", "标准化附框"))
    thermal_break = any(token in detail_text for token in ("断桥", "隔热", "隔热型", "断热"))
    if any(token in detail_text for token in ("铝合金", "断桥", "隔热", "隔热型", "断热")):
        frame_material = "铝合金"
    elif "塑钢" in detail_text:
        frame_material = "塑钢"
    elif "铝合金" in material_text:
        frame_material = "铝合金"
    elif "塑钢" in material_text:
        frame_material = "塑钢"
    else:
        frame_material = ""

    opening_type = ""
    opening_explicit = False
    is_window = "窗" in name
    if is_window:
        for candidate in ("平开窗", "推拉窗", "固定窗", "百叶窗", "上悬窗"):
            if candidate in detail_text:
                opening_type = candidate
                opening_explicit = True
                break
    elif "门" in name:
        for candidate in ("平开门", "推拉门", "地弹门"):
            if candidate in detail_text:
                opening_type = candidate
                opening_explicit = True
                break
        if not opening_type and frame_material:
            opening_type = "门"

    if not frame_material or (not is_window and not opening_type):
        return None

    query_parts = _build_metal_opening_query_parts(
        frame_material,
        is_window=is_window,
        opening_type=opening_type,
        opening_explicit=opening_explicit,
        thermal_break=thermal_break,
        has_attached_frame=has_attached_frame,
        include_fixed_window=(
            "门" in name
            and any(token in detail_text for token in ("固定窗", "门联窗", "其余固定"))
        ),
    )
    return " ".join(query_parts) if query_parts else None


def _extract_distribution_box_fields(description: str) -> dict:
    if not description:
        return {}

    fields = {}
    for label, value in re.findall(
        r'(名称|型号规格|规格型号|型号|规格|安装方式)[：:]\s*(.*?)(?=(?://|//|；|;|\n|$))',
        description,
    ):
        cleaned = value.strip().strip("/；;")
        if cleaned and cleaned != "详见图纸" and not cleaned.startswith("详见"):
            fields.setdefault(label, cleaned)
    return fields


def _clean_distribution_box_text(value: str) -> str:
    if not value:
        return ""

    cleaned = value.strip().strip("/；;")
    cleaned = re.sub(r'[（(][^)）]*(只计安装|详见|图纸|自带)[^)）]*[)）]', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if cleaned in {"详见图纸", "详见设计图纸", "根据设计图纸综合考虑", "综合考虑", "非标"}:
        return ""
    return cleaned


def _extract_distribution_box_model(value: str) -> str:
    cleaned = _clean_distribution_box_text(value)
    if not cleaned:
        return ""

    candidates = re.findall(r'[A-Za-z0-9#/_\-.]{2,}', cleaned)
    for token in candidates:
        upper = token.upper()
        if upper.startswith("IP") and any(ch.isdigit() for ch in upper[2:]):
            continue
        return token
    return ""


_KNOWN_BOX_MODEL_PREFIXES = (
    "XL", "XRM", "GGD", "GCK", "GCS", "MNS", "PGL",
    "JXF", "PZ", "BXM", "BSM", "ATS", "PDX",
)


def _normalize_distribution_box_name(value: str) -> str:
    cleaned = _clean_distribution_box_text(value)
    if not cleaned:
        return ""

    cleaned = re.sub(r'^成套配电箱(?!安装)', '成套配电箱安装 ', cleaned)
    cleaned = re.sub(r'^成套配电柜(?!安装)', '成套配电柜安装 ', cleaned)
    cleaned = re.sub(
        r'((?:配电箱|配电柜|控制箱|控制柜|程序控制箱|动力箱|照明箱|双电源箱|双电源配电箱|电表箱))([A-Za-z0-9#/_\-.]{2,})$',
        r'\1 \2',
        cleaned,
    )
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    if not any(kw in cleaned for kw in ("箱", "柜")):
        return ""
    return cleaned


def _extract_distribution_box_half_perimeter_mm(full_text: str,
                                                spec_text: str,
                                                params: dict) -> float | None:
    if "半周长" in full_text:
        value = params.get("half_perimeter")
        if value:
            return float(value)

    spec_source = spec_text or full_text
    size_match = re.search(r'(\d+(?:\.\d+)?)\s*[*×xX]\s*(\d+(?:\.\d+)?)', spec_source)
    if size_match:
        width = float(size_match.group(1))
        height = float(size_match.group(2))
        return width + height

    return None


def _bucket_distribution_box_half_perimeter(half_perimeter_mm: float | None) -> str:
    if not half_perimeter_mm:
        return ""

    buckets = (
        (500, "0.5m"),
        (1000, "1.0m"),
        (1500, "1.5m"),
        (2500, "2.5m"),
        (3000, "3.0m"),
    )
    for upper, label in buckets:
        if half_perimeter_mm <= upper:
            return label
    return buckets[-1][1]


def _build_distribution_box_query(name: str,
                                  description: str,
                                  full_text: str,
                                  fields: dict,
                                  params: dict,
                                  specialty: str = "") -> str | None:
    text = " ".join(part for part in (name, description, full_text) if part)
    box_keywords = (
        "配电箱", "配电柜", "控制箱", "控制柜", "程序控制箱",
        "动力箱", "照明箱", "双电源箱", "双电源配电箱",
    )
    electrical_specialties = ("C4", "C5", "C11", "A4", "A5", "A11")
    prefer_cabinet = any(keyword in text for keyword in ("配电柜", "控制柜"))
    is_box_item = (
        "杆上" not in text
        and any(keyword in text for keyword in box_keywords)
        and (
            any(keyword in name for keyword in box_keywords)
            or specialty.startswith(electrical_specialties)
            or not specialty
        )
    )
    if not is_box_item:
        return None

    box_fields = dict(fields)
    box_fields.update(_extract_distribution_box_fields(description))

    explicit_family = re.search(
        r'(?<![\u4e00-\u9fff])((?:高压|低压)?(?:成套)?(?:配电|控制)[箱柜](?:安装)?|程序控制箱|动力箱|照明箱|双电源(?:配电)?箱)\s*([A-Za-z0-9#/_\-.]{2,})?',
        description,
    )
    if explicit_family:
        family = explicit_family.group(1)
        if not family.endswith("安装"):
            family += "安装"
        model = _extract_distribution_box_model(explicit_family.group(2) or "")
        if model or "配电柜" in family or family.startswith(("高压", "低压")):
            return f"{family} {model}".strip()

    box_name = _normalize_distribution_box_name(
        box_fields.get("名称", "") or box_fields.get("规格型号", "") or box_fields.get("型号规格", "")
    )
    model = _extract_distribution_box_model(box_fields.get("型号", ""))
    spec_text = _clean_distribution_box_text(box_fields.get("规格", ""))

    if not model:
        loose_model_match = re.search(
            r'^(?:配电箱|配电柜|控制箱|控制柜|程序控制箱|动力箱|照明箱|双电源(?:配电)?箱|成套配电箱安装|成套配电柜安装)\s+([A-Za-z0-9#/_\-.]{2,})\b',
            description,
        )
        if loose_model_match:
            model = loose_model_match.group(1)

    generic_names = {
        "配电箱",
        "配电柜",
        "控制箱",
        "控制柜",
        "程序控制箱",
        "动力箱",
        "照明箱",
        "双电源箱",
        "双电源配电箱",
        "成套配电箱",
        "成套配电箱安装",
        "成套配电柜",
        "成套配电柜安装",
    }

    def _is_known_product_model(model_text: str) -> bool:
        upper = model_text.upper()
        return any(upper.startswith(prefix) for prefix in _KNOWN_BOX_MODEL_PREFIXES)

    if box_name:
        query = box_name
        if model and model not in query:
            if box_name not in generic_names or _is_known_product_model(model):
                query = f"{query} {model}"
        if query not in generic_names:
            return query

    if model and _is_known_product_model(model):
        base = "配电柜" if prefer_cabinet else "配电箱"
        return f"{base} {model}"

    install_text = _clean_distribution_box_text(box_fields.get("安装方式", ""))
    if not install_text:
        for candidate in ("明装", "暗装", "落地", "落地式", "嵌入", "嵌入式", "壁挂", "挂墙", "墙上", "柱上", "悬挂"):
            if candidate in full_text:
                install_text = candidate
                break

    box_mount_mode = str(params.get("box_mount_mode") or "")
    floor_template = "成套配电柜安装" if prefer_cabinet else "成套配电箱安装"
    wall_template = "成套配电箱安装"
    if box_mount_mode == "落地式" or "落地" in install_text:
        return f"{floor_template} 落地式"

    half_perimeter_mm = _extract_distribution_box_half_perimeter_mm(full_text, spec_text, params)
    bucket = _bucket_distribution_box_half_perimeter(half_perimeter_mm)
    if box_mount_mode == "悬挂/嵌入式" and bucket:
        return f"{wall_template} 悬挂、嵌入式 半周长{bucket}"
    if bucket:
        return f"{wall_template} 悬挂、嵌入式 半周长{bucket}"

    if box_mount_mode == "悬挂/嵌入式":
        return f"{wall_template} 悬挂、嵌入式"
    if any(keyword in install_text for keyword in ("明装", "暗装", "嵌入", "悬挂", "壁挂", "挂墙", "墙上", "柱上")):
        return f"{wall_template} 悬挂、嵌入式"

    return floor_template if prefer_cabinet else wall_template
