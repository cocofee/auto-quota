from __future__ import annotations

from collections import Counter
from typing import Any, Callable
import re


_SYSTEM_HINT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("消防", ("消防", "喷淋", "消火栓", "火灾报警")),
    ("给排水", ("给水", "排水", "污水", "雨水", "中水")),
    ("电气", ("电气", "桥架", "电缆", "配线", "配管", "照明", "动力")),
    ("通风空调", ("通风", "空调", "风管", "风阀", "风口")),
]


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


def detect_system_hint(*texts: str) -> str:
    merged = " ".join(str(text or "") for text in texts)
    for system, keywords in _SYSTEM_HINT_RULES:
        if any(keyword in merged for keyword in keywords):
            return system
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
    summary = {
        "context_hints": list(context_prior.get("context_hints") or item.get("_context_hints") or []),
        "prior_family": str(context_prior.get("prior_family") or item.get("_prior_family") or ""),
        "system_hint": str(context_prior.get("system_hint") or ""),
        "project_system_hint": str(batch_context.get("project_system_hint") or ""),
        "section_system_hint": str(batch_context.get("section_system_hint") or ""),
        "sheet_system_hint": str(batch_context.get("sheet_system_hint") or ""),
        "neighbor_system_hint": str(batch_context.get("neighbor_system_hint") or ""),
        "batch_size": int(batch_context.get("batch_size") or 0),
    }
    return {key: value for key, value in summary.items() if value not in ("", [], {}, 0, None)}
