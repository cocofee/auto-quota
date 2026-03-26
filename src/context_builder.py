from __future__ import annotations

from collections import Counter
from typing import Any, Callable
import re


_SYSTEM_HINT_RULES: list[tuple[str, tuple[str, ...]]] = [
    (
        "消防",
        (
            "消防", "消防水", "消火栓", "喷淋", "喷洒", "火灾报警", "报警阀",
            "湿式报警", "末端试水", "消防泵", "消防箱",
        ),
    ),
    (
        "给排水",
        (
            "给排水", "生活给水", "生产给水", "给水系统", "给水管", "给水",
            "排水系统", "重力排水", "污水系统", "污废水", "废水", "污水",
            "雨水系统", "虹吸雨水", "雨水", "中水", "冷凝水", "排水", "水暖",
        ),
    ),
    (
        "电气",
        (
            "电气", "强电", "弱电", "机电", "供配电", "配电", "动力", "照明",
            "防雷", "接地", "桥架", "电缆", "配线", "配管", "导管", "线槽",
            "母线", "开关", "插座",
        ),
    ),
    (
        "通风空调",
        (
            "通风空调", "暖通", "空调水", "空调", "通风", "新风", "送风", "排风",
            "排烟", "补风", "风管", "风阀", "风口", "盘管", "风机盘管",
        ),
    ),
]

_SYSTEM_NORMALIZE_MAP: dict[str, str] = {
    "给水": "给排水",
    "排水": "给排水",
    "污水": "给排水",
    "雨水": "给排水",
    "中水": "给排水",
    "消防水": "消防",
    "暖通": "通风空调",
}


_SYSTEM_HINT_RULES = [
    (
        "\u6d88\u9632",
        (
            "\u6d88\u9632", "\u6d88\u9632\u6c34", "\u6d88\u706b\u6813", "\u55b7\u6dcb", "\u55b7\u6d12",
            "\u706b\u707e\u62a5\u8b66", "\u62a5\u8b66\u9600", "\u6e7f\u5f0f\u62a5\u8b66",
            "\u672b\u7aef\u8bd5\u6c34", "\u6d88\u9632\u6cf5", "\u6d88\u9632\u7ba1",
        ),
    ),
    (
        "\u7ed9\u6392\u6c34",
        (
            "\u7ed9\u6392\u6c34", "\u751f\u6d3b\u7ed9\u6c34", "\u751f\u4ea7\u7ed9\u6c34",
            "\u7ed9\u6c34\u7cfb\u7edf", "\u7ed9\u6c34\u7ba1", "\u7ed9\u6c34",
            "\u6392\u6c34\u7cfb\u7edf", "\u91cd\u529b\u6392\u6c34", "\u6c61\u6c34\u7cfb\u7edf",
            "\u6c61\u5e9f\u6c34", "\u5e9f\u6c34", "\u6c61\u6c34", "\u96e8\u6c34\u7cfb\u7edf",
            "\u8679\u5438\u96e8\u6c34", "\u96e8\u6c34", "\u4e2d\u6c34", "\u51b7\u51dd\u6c34",
            "\u6392\u6c34", "\u6c34\u6696",
        ),
    ),
    (
        "\u7535\u6c14",
        (
            "\u7535\u6c14", "\u5f3a\u7535", "\u5f31\u7535", "\u673a\u7535", "\u4f9b\u914d\u7535",
            "\u914d\u7535", "\u52a8\u529b", "\u7167\u660e", "\u9632\u96f7", "\u63a5\u5730",
            "\u6865\u67b6", "\u7535\u7f06", "\u914d\u7ebf", "\u914d\u7ba1", "\u5bfc\u7ba1",
            "\u7ebf\u69fd", "\u6bcd\u7ebf", "\u5f00\u5173", "\u63d2\u5ea7",
        ),
    ),
    (
        "\u901a\u98ce\u7a7a\u8c03",
        (
            "\u901a\u98ce\u7a7a\u8c03", "\u6696\u901a", "\u7a7a\u8c03\u6c34", "\u7a7a\u8c03",
            "\u901a\u98ce", "\u65b0\u98ce", "\u9001\u98ce", "\u6392\u98ce", "\u6392\u70df",
            "\u8865\u98ce", "\u98ce\u7ba1", "\u98ce\u9600", "\u98ce\u53e3", "\u76d8\u7ba1",
            "\u98ce\u673a\u76d8\u7ba1",
        ),
    ),
]

_SYSTEM_NORMALIZE_MAP = {
    "\u7ed9\u6c34": "\u7ed9\u6392\u6c34",
    "\u6392\u6c34": "\u7ed9\u6392\u6c34",
    "\u6c61\u6c34": "\u7ed9\u6392\u6c34",
    "\u96e8\u6c34": "\u7ed9\u6392\u6c34",
    "\u4e2d\u6c34": "\u7ed9\u6392\u6c34",
    "\u6d88\u9632\u6c34": "\u6d88\u9632",
    "\u6696\u901a": "\u901a\u98ce\u7a7a\u8c03",
}

def _dedupe_keep_order(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _item_text(item: dict[str, Any], *fields: str) -> str:
    return " ".join(str(item.get(field) or "") for field in fields).strip()


def normalize_system_hint(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return _SYSTEM_NORMALIZE_MAP.get(text, text)


def detect_system_hint(*texts: str) -> str:
    merged = " ".join(str(text or "") for text in texts)
    for system, keywords in _SYSTEM_HINT_RULES:
        if any(keyword in merged for keyword in keywords):
            return normalize_system_hint(system)
    return ""


def build_project_context(items: list[dict[str, Any]]) -> dict[str, Any]:
    specialty_counter: Counter[str] = Counter()
    system_counter: Counter[str] = Counter()
    section_system_counter: dict[str, Counter[str]] = {}
    sheet_system_counter: dict[str, Counter[str]] = {}

    for item in items or []:
        specialty = str(item.get("specialty") or "").strip()
        if specialty:
            specialty_counter[specialty] += 1

        system_hint = detect_system_hint(
            str(item.get("section") or ""),
            str(item.get("sheet_name") or ""),
            str(item.get("name") or ""),
            str(item.get("description") or ""),
        )
        if system_hint:
            system_counter[system_hint] += 1

        section = str(item.get("section") or "").strip()
        if section and system_hint:
            section_system_counter.setdefault(section, Counter())[system_hint] += 1

        sheet = str(item.get("sheet_name") or "").strip()
        if sheet and system_hint:
            sheet_system_counter.setdefault(sheet, Counter())[system_hint] += 1

    primary_specialty = specialty_counter.most_common(1)[0][0] if specialty_counter else ""
    system_hint = system_counter.most_common(1)[0][0] if system_counter else ""
    context_hints: list[str] = []
    if system_hint:
        context_hints.append(system_hint)

    return {
        "primary_specialty": primary_specialty,
        "system_hint": system_hint,
        "context_hints": context_hints,
        "batch_size": len(items or []),
        "section_system_hints": {
            section: counter.most_common(1)[0][0]
            for section, counter in section_system_counter.items() if counter
        },
        "sheet_system_hints": {
            sheet: counter.most_common(1)[0][0]
            for sheet, counter in sheet_system_counter.items() if counter
        },
    }


def apply_batch_context(items: list[dict[str, Any]],
                        *,
                        project_context: dict[str, Any] | None = None,
                        is_ambiguous_fn: Callable[[dict[str, Any]], bool] | None = None,
                        short_name_priors: dict[tuple[str, str], str] | None = None,
                        neighbor_window: int = 5) -> dict[str, Any]:
    items = items or []
    project_context = dict(project_context or build_project_context(items))
    short_name_priors = dict(short_name_priors or {})

    for index, item in enumerate(items):
        item["_batch_index"] = index
        item["_batch_context"] = {
            "project_system_hint": str(project_context.get("system_hint") or ""),
            "section_system_hint": str(
                (project_context.get("section_system_hints") or {}).get(str(item.get("section") or "").strip()) or ""
            ),
            "sheet_system_hint": str(
                (project_context.get("sheet_system_hints") or {}).get(str(item.get("sheet_name") or "").strip()) or ""
            ),
        }

    if is_ambiguous_fn is None:
        return project_context

    for index, item in enumerate(items):
        if not is_ambiguous_fn(item):
            continue

        item["_is_ambiguous_short"] = True
        section = str(item.get("section") or "").strip()
        sheet = str(item.get("sheet_name") or "").strip()

        context_keywords: list[tuple[str, float]] = []
        neighbor_system_counter: Counter[str] = Counter()
        if section or sheet:
            for offset in range(-neighbor_window, neighbor_window + 1):
                if offset == 0:
                    continue
                neighbor_index = index + offset
                if neighbor_index < 0 or neighbor_index >= len(items):
                    continue
                neighbor = items[neighbor_index]

                neighbor_section = str(neighbor.get("section") or "").strip()
                neighbor_sheet = str(neighbor.get("sheet_name") or "").strip()
                if section and neighbor_section != section:
                    continue
                if not section and sheet and neighbor_sheet != sheet:
                    continue
                if is_ambiguous_fn(neighbor):
                    continue

                distance = abs(offset)
                weight = 1.0 / (1 + distance)
                neighbor_name = str(neighbor.get("name") or "")
                for keyword in re.findall(r"[\u4e00-\u9fff]{2,4}", neighbor_name)[:2]:
                    context_keywords.append((keyword, weight))

                neighbor_system = detect_system_hint(
                    neighbor.get("section", ""),
                    neighbor.get("sheet_name", ""),
                    neighbor.get("name", ""),
                    neighbor.get("description", ""),
                )
                if neighbor_system:
                    neighbor_system_counter[neighbor_system] += 1

        if context_keywords:
            keyword_scores: dict[str, float] = {}
            for keyword, weight in context_keywords:
                keyword_scores[keyword] = keyword_scores.get(keyword, 0.0) + weight
            item["_context_hints"] = [
                keyword for keyword, _ in sorted(keyword_scores.items(), key=lambda pair: -pair[1])[:3]
            ]

        if not item.get("_context_hints"):
            prior_key = (str(item.get("name", "")).strip(), str(item.get("specialty", "")).strip())
            prior_family = short_name_priors.get(prior_key)
            if prior_family:
                item["_prior_family"] = prior_family

        batch_context = item.setdefault("_batch_context", {})
        if neighbor_system_counter:
            batch_context["neighbor_system_hint"] = neighbor_system_counter.most_common(1)[0][0]
            batch_context["neighbor_system_hints"] = [
                name for name, _ in neighbor_system_counter.most_common(2)
            ]

    return project_context


def build_context_prior(item: dict[str, Any],
                        project_context: dict[str, Any] | None = None) -> dict[str, Any]:
    item = dict(item or {})
    project_context = dict(project_context or {})
    batch_context = dict(item.get("_batch_context") or {})

    context_prior = {
        "specialty": item.get("specialty", ""),
        "specialty_name": item.get("specialty_name", ""),
        "project_name": str(item.get("project_name") or "").strip(),
        "bill_name": str(item.get("bill_name") or "").strip(),
    }

    context_hints = _dedupe_keep_order(list(item.get("_context_hints") or []))
    batch_hints = _dedupe_keep_order(list(batch_context.get("neighbor_system_hints") or []))
    section_hint = str(batch_context.get("section_system_hint") or "").strip()
    sheet_hint = str(batch_context.get("sheet_system_hint") or "").strip()
    project_hints = _dedupe_keep_order(list(project_context.get("context_hints") or []))

    merged_hints = context_hints + batch_hints
    if section_hint:
        merged_hints.append(section_hint)
    if sheet_hint and sheet_hint != section_hint:
        merged_hints.append(sheet_hint)
    if project_hints:
        merged_hints.extend(project_hints[:2])
    merged_hints = _dedupe_keep_order(merged_hints)
    if merged_hints:
        context_prior["context_hints"] = merged_hints

    if item.get("_prior_family"):
        context_prior["prior_family"] = item["_prior_family"]
    if item.get("cable_type"):
        context_prior["cable_type"] = item["cable_type"]

    system_hint = detect_system_hint(
        item.get("section", ""),
        item.get("sheet_name", ""),
        item.get("name", ""),
        item.get("description", ""),
    )
    if not system_hint:
        system_hint = str(batch_context.get("neighbor_system_hint") or "")
    if not system_hint:
        system_hint = section_hint or sheet_hint
    if not system_hint:
        system_hint = str(project_context.get("system_hint") or "")
    if system_hint:
        context_prior["system_hint"] = system_hint

    primary_specialty = str(project_context.get("primary_specialty") or "").strip()
    if primary_specialty and not context_prior.get("specialty"):
        context_prior["specialty"] = primary_specialty
    primary_specialty_name = str(project_context.get("primary_specialty_name") or "").strip()
    if primary_specialty_name and not context_prior.get("specialty_name"):
        context_prior["specialty_name"] = primary_specialty_name

    context_prior["batch_context"] = {
        "project_system_hint": str(batch_context.get("project_system_hint") or project_context.get("system_hint") or ""),
        "section_system_hint": section_hint,
        "sheet_system_hint": sheet_hint,
        "neighbor_system_hint": str(batch_context.get("neighbor_system_hint") or ""),
        "batch_size": int(project_context.get("batch_size") or 0),
    }

    return context_prior


def format_overview_context(*,
                            item: dict[str, Any] | None = None,
                            project_context: dict[str, Any] | None = None,
                            project_overview: str = "",
                            match_stats: list[str] | None = None) -> str:
    item = dict(item or {})
    project_context = dict(project_context or {})
    batch_context = dict((item.get("context_prior") or {}).get("batch_context") or item.get("_batch_context") or {})
    canonical_query = dict(item.get("canonical_query") or {})

    parts: list[str] = []
    if project_overview:
        parts.append(project_overview.strip())

    structured_lines: list[str] = []
    batch_size = int(project_context.get("batch_size") or 0)
    primary_specialty = str(project_context.get("primary_specialty") or "").strip()
    project_system = str(project_context.get("system_hint") or "").strip()
    if batch_size:
        line = f"批次规模: {batch_size}条"
        if primary_specialty:
            line += f"; 主专业: {primary_specialty}"
        if project_system:
            line += f"; 主系统: {project_system}"
        structured_lines.append(line)

    section = str(item.get("section") or "").strip()
    sheet = str(item.get("sheet_name") or "").strip()
    section_system = str(batch_context.get("section_system_hint") or "").strip()
    sheet_system = str(batch_context.get("sheet_system_hint") or "").strip()
    neighbor_system = str(batch_context.get("neighbor_system_hint") or "").strip()
    context_hints = list((item.get("context_prior") or {}).get("context_hints") or item.get("_context_hints") or [])
    route_query = str(canonical_query.get("route_query") or "").strip()
    validation_query = str(canonical_query.get("validation_query") or "").strip()
    search_query = str(canonical_query.get("search_query") or "").strip()

    item_lines: list[str] = []
    if section:
        item_lines.append(f"当前分部: {section}")
    if sheet:
        item_lines.append(f"当前表页: {sheet}")
    if section_system:
        item_lines.append(f"分部主系统: {section_system}")
    if sheet_system and sheet_system != section_system:
        item_lines.append(f"表页主系统: {sheet_system}")
    if neighbor_system:
        item_lines.append(f"邻近条目主系统: {neighbor_system}")
    if context_hints:
        item_lines.append(f"上下文提示: {', '.join(str(v) for v in context_hints[:4])}")
    if route_query:
        item_lines.append(f"RouteQuery: {route_query[:80]}")
    if validation_query and validation_query != route_query:
        item_lines.append(f"ValidationQuery: {validation_query[:80]}")
    if search_query and search_query not in {route_query, validation_query}:
        item_lines.append(f"SearchQuery: {search_query[:80]}")
    if item_lines:
        structured_lines.append("当前条目批次上下文: " + " | ".join(item_lines))

    if match_stats:
        structured_lines.append("已处理的同类清单匹配情况:\n" + "\n".join(f"- {line}" for line in match_stats[:5]))

    if structured_lines:
        parts.append("\n".join(structured_lines))

    return "\n\n".join(part for part in parts if part).strip()


def summarize_batch_context_for_trace(item: dict[str, Any] | None = None) -> dict[str, Any]:
    item = dict(item or {})
    context_prior = dict(item.get("context_prior") or {})
    batch_context = dict(context_prior.get("batch_context") or item.get("_batch_context") or {})
    plugin_hints = dict(context_prior.get("plugin_hints") or item.get("plugin_hints") or {})
    canonical_query = dict(item.get("canonical_query") or {})
    summary = {
        "context_hints": list(context_prior.get("context_hints") or item.get("_context_hints") or []),
        "prior_family": str(context_prior.get("prior_family") or item.get("_prior_family") or ""),
        "system_hint": str(context_prior.get("system_hint") or ""),
        "project_system_hint": str(batch_context.get("project_system_hint") or ""),
        "section_system_hint": str(batch_context.get("section_system_hint") or ""),
        "sheet_system_hint": str(batch_context.get("sheet_system_hint") or ""),
        "neighbor_system_hint": str(batch_context.get("neighbor_system_hint") or ""),
        "batch_size": int(batch_context.get("batch_size") or 0),
        "plugin_terms": list(plugin_hints.get("matched_terms") or []),
        "plugin_aliases": list(plugin_hints.get("synonym_aliases") or [])[:2],
        "plugin_books": list(plugin_hints.get("preferred_books") or [])[:2],
        "plugin_specialties": list(plugin_hints.get("preferred_specialties") or [])[:2],
        "route_query": str(canonical_query.get("route_query") or ""),
        "validation_query": str(canonical_query.get("validation_query") or ""),
        "search_query": str(canonical_query.get("search_query") or ""),
    }
    return {key: value for key, value in summary.items() if value not in ("", [], {}, 0, None)}
