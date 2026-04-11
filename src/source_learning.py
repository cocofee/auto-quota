from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import config
from src.source_pack import (
    normalize_source_pack_metadata,
    read_text_with_fallbacks,
    safe_text,
    slugify,
)


MAX_SUMMARY_CHARS = 320
SUPPORTED_CANDIDATE_TYPES = {"rule", "method", "experience"}
_TOC_DOT_RE = re.compile(r"[.．·…]{4,}")
_TOC_LINE_RE = re.compile(r"^.{0,120}[.．·…]{4,}\s*\d+\s*$")
_BODY_SIGNAL_RE = re.compile(
    r"(编制概况|总说明|说明|工程量计算规则|计算规则|工作内容|适用范围|项目划分|定额)")
_PLAIN_HEADING_PATTERNS: tuple[tuple[int, re.Pattern[str]], ...] = (
    (1, re.compile(r"^第[一二三四五六七八九十百千零〇0-9]+[册篇章部分节]\s*.*$")),
    (2, re.compile(r"^附录[一二三四五六七八九十百千零〇0-9A-Za-z]*\s*.*$")),
    (2, re.compile(r"^(总说明|编制概况|编制说明|工程量计算规则|工作内容|项目划分|适用范围|说明)$")),
    (3, re.compile(r"^[一二三四五六七八九十百千零〇]+、.+$")),
    (4, re.compile(r"^[（(][一二三四五六七八九十百千零〇0-9]+[)）].+$")),
    (5, re.compile(r"^\d+[\.、]\S.*$")),
)
_SECTION_STRONG_KEYWORDS = (
    "工程量计算规则",
    "计算规则",
    "工作内容",
    "总说明",
    "编制说明",
    "编制概况",
    "适用范围",
    "项目划分",
)
_SECTION_RULE_KEYWORDS = (
    "包括",
    "不包括",
    "另计",
    "应按",
    "不得",
    "不另计",
    "已含",
    "未含",
    "适用",
)


@dataclass(slots=True)
class SourceLearningChunk:
    chunk_id: str
    heading: str
    text: str
    preview: str


def _trim_text(value: Any, limit: int = MAX_SUMMARY_CHARS) -> str:
    text = safe_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_list(value: Any) -> list[str]:
    if isinstance(value, list):
        items = [safe_text(item) for item in value if safe_text(item)]
    elif isinstance(value, str):
        items = [item.strip() for item in re.split(r"[;,，；\n]+", value) if item.strip()]
    else:
        items = []
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _split_markdown_sections(body: str, fallback_title: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    current_heading = fallback_title
    current_lines: list[str] = []

    def flush() -> None:
        text = "\n".join(current_lines).strip()
        if text:
            sections.append((current_heading, text))

    for line in body.splitlines():
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            flush()
            level = len(match.group(1))
            heading = safe_text(match.group(2))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            current_heading = " / ".join(item[1] for item in stack if item[1]) or fallback_title
            current_lines = []
            continue
        current_lines.append(line)

    flush()
    if not sections and body.strip():
        return [(fallback_title, body.strip())]
    return sections


def _is_toc_marker(line: str) -> bool:
    compact = safe_text(line).replace(" ", "").lower()
    return compact in {"目录", "目录", "contents"}


def _is_toc_like_line(line: str) -> bool:
    value = safe_text(line)
    if not value:
        return False
    if _TOC_LINE_RE.match(value):
        return True
    if _TOC_DOT_RE.search(value) and re.search(r"\d+\s*$", value):
        return True
    return False


def _match_plain_heading(line: str) -> tuple[int, str] | None:
    value = safe_text(line)
    if not value or _is_toc_like_line(value):
        return None
    for level, pattern in _PLAIN_HEADING_PATTERNS:
        if pattern.match(value):
            return level, value
    return None


def _trim_leading_toc(body: str) -> str:
    lines = body.splitlines()
    toc_start = -1
    for index, line in enumerate(lines):
        if _is_toc_marker(line):
            toc_start = index
            break
    if toc_start < 0:
        return body

    for index in range(toc_start + 1, len(lines)):
        line = safe_text(lines[index])
        if not line or _is_toc_like_line(line):
            continue
        if _BODY_SIGNAL_RE.search(line) or len(line) >= 20 or _match_plain_heading(line):
            trimmed = "\n".join(lines[index:]).strip()
            if trimmed:
                return trimmed
            break
    return body


def _split_plaintext_sections(body: str, fallback_title: str) -> list[tuple[str, str]]:
    cleaned_body = _trim_leading_toc(body)
    sections: list[tuple[str, str]] = []
    stack: list[tuple[int, str]] = []
    current_heading = fallback_title
    current_lines: list[str] = []

    def flush() -> None:
        value = "\n".join(current_lines).strip()
        if value:
            sections.append((current_heading, value))

    for raw_line in cleaned_body.splitlines():
        line = safe_text(raw_line)
        if not line:
            current_lines.append(raw_line)
            continue
        match = _match_plain_heading(line)
        if match:
            flush()
            level, heading = match
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            current_heading = " / ".join(item[1] for item in stack if item[1]) or fallback_title
            current_lines = []
            continue
        current_lines.append(raw_line)

    flush()
    if not sections and cleaned_body.strip():
        return [(fallback_title, cleaned_body.strip())]
    return sections


def _section_priority(heading: str, text: str) -> int:
    normalized_heading = safe_text(heading)
    normalized_text = safe_text(text)
    if _is_toc_marker(normalized_heading) or "目录" in normalized_heading:
        return -100
    if _is_toc_like_line(normalized_text[:200]):
        return -100

    score = 0
    if len(normalized_text) < 40:
        score -= 5

    for keyword in _SECTION_STRONG_KEYWORDS:
        if keyword in normalized_heading:
            score += 12
        if keyword in normalized_text:
            score += 4

    for keyword in _SECTION_RULE_KEYWORDS:
        if keyword in normalized_heading:
            score += 5
        if keyword in normalized_text:
            score += 2

    if normalized_heading.endswith("目录"):
        score -= 2
    return score


def _slice_text_block(text: str, max_chars: int, overlap_chars: int) -> list[str]:
    cleaned = safe_text(text)
    if not cleaned:
        return []
    if len(cleaned) <= max_chars:
        return [cleaned]

    paragraphs = [item.strip() for item in re.split(r"\n\s*\n+", cleaned) if item.strip()]
    if len(paragraphs) >= 2:
        chunks: list[str] = []
        current_parts: list[str] = []
        for paragraph in paragraphs:
            extra = len(paragraph) + (2 if current_parts else 0)
            current_len = len("\n\n".join(current_parts))
            if current_parts and current_len + extra > max_chars:
                chunks.append("\n\n".join(current_parts))
                overlap_parts: list[str] = []
                overlap_len = 0
                for item in reversed(current_parts):
                    candidate_len = len(item) + (2 if overlap_parts else 0)
                    if overlap_parts and overlap_len + candidate_len > overlap_chars:
                        break
                    overlap_parts.insert(0, item)
                    overlap_len += candidate_len
                current_parts = overlap_parts[:]
            current_parts.append(paragraph)
        if current_parts:
            chunks.append("\n\n".join(current_parts))
        if chunks:
            return chunks

    result: list[str] = []
    start = 0
    while start < len(cleaned):
        end = min(start + max_chars, len(cleaned))
        result.append(cleaned[start:end].strip())
        if end >= len(cleaned):
            break
        start = max(end - overlap_chars, start + 1)
    return [item for item in result if item]


def build_learning_chunks(
    pack: dict[str, Any],
    *,
    chunk_size: int = 1800,
    overlap: int = 240,
    max_chunks: int = 24,
) -> list[SourceLearningChunk]:
    normalized = normalize_source_pack_metadata(pack)
    full_text_path = safe_text(normalized.get("full_text_path"))
    if not full_text_path:
        return []

    body = read_text_with_fallbacks(full_text_path)
    title = safe_text(normalized.get("title")) or safe_text(normalized.get("source_id")) or "source"
    if re.search(r"^\s*#{1,6}\s+", body, re.MULTILINE):
        sections = _split_markdown_sections(body, title)
    else:
        sections = _split_plaintext_sections(body, title)

    ranked_sections = [
        (priority, index, heading, section_text)
        for index, (heading, section_text) in enumerate(sections)
        for priority in [_section_priority(heading, section_text)]
        if priority > -100
    ]
    ranked_sections.sort(key=lambda item: (-item[0], item[1]))
    ordered_sections = [(heading, section_text) for _, _, heading, section_text in ranked_sections] or sections

    chunks: list[SourceLearningChunk] = []
    source_id = safe_text(normalized.get("source_id")) or "source"
    for section_index, (heading, section_text) in enumerate(ordered_sections, start=1):
        pieces = _slice_text_block(section_text, chunk_size, overlap)
        for piece_index, piece in enumerate(pieces, start=1):
            preview = _trim_text(piece.replace("\n", " "), 180)
            chunk_id = f"{source_id}:s{section_index:02d}:c{piece_index:02d}"
            chunks.append(
                SourceLearningChunk(
                    chunk_id=chunk_id,
                    heading=heading,
                    text=piece,
                    preview=preview,
                )
            )
            if len(chunks) >= max_chunks:
                return chunks
    return chunks


def build_source_learning_prompt(pack: dict[str, Any], chunk: SourceLearningChunk) -> str:
    normalized = normalize_source_pack_metadata(pack)
    source_id = safe_text(normalized.get("source_id"))
    title = safe_text(normalized.get("title")) or source_id
    source_kind = safe_text(normalized.get("source_kind")) or "unknown"
    province = safe_text(normalized.get("province"))
    specialty = safe_text(normalized.get("specialty"))

    return f"""你是工程造价知识抽取器。请从下面资料片段中抽取可复用的 rule / method / experience 候选。

要求：
1. candidate_type 只能是 rule、method、experience。
2. 只抽取明确、有复用价值、能进入知识库的内容。
3. 如果片段中出现“应按、不应、不得、包括、不包括、另计、不另计、计算规则、工作内容、说明”等表述，通常应至少抽出 1 条 rule 或 method。
4. 仅当前片段明显是封面、目录、页码导航、无有效正文时，才返回空数组。
5. 每个候选写 1-3 句中文摘要，优先保留可执行判断。
6. 只返回 JSON，不要解释。

字段说明：
- rule: 规范、适用条件、计量口径、是否另计、包含/不包含。
- method: 审核步骤、判断流程、识别方法、排错方法。
- experience: 典型清单项、最终定额、经验映射、案例归纳。

Source metadata:
- source_id: {source_id}
- title: {title}
- source_kind: {source_kind}
- province: {province}
- specialty: {specialty}
- chunk_id: {chunk.chunk_id}
- heading: {chunk.heading}

返回 JSON 结构：
{{
  "candidates": [
    {{
      "candidate_type": "rule|method|experience",
      "title": "候选标题",
      "summary": "1-3 句摘要",
      "confidence": 0.0,
      "keywords": ["关键词1", "关键词2"],
      "rule_text": "rule 原文提炼",
      "method_text": "method 原文提炼",
      "bill_text": "experience 对应清单描述",
      "bill_name": "experience 对应清单名称",
      "bill_desc": "experience 补充说明",
      "final_quota_code": "最终定额编码",
      "final_quota_name": "最终定额名称",
      "conditions": ["适用条件"],
      "exclusions": ["不适用条件"],
      "common_errors": ["常见错误"],
      "evidence_text": "不超过 80 字的关键证据"
    }}
  ]
}}

资料片段：
```text
{chunk.text}
```"""


def parse_source_learning_response(response_text: str) -> list[dict[str, Any]]:
    text = safe_text(response_text)
    if not text:
        return []

    fenced = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()

    payload: Any = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                payload = None

    if isinstance(payload, list):
        raw_items = payload
    elif isinstance(payload, dict):
        raw_items = payload.get("candidates") or []
    else:
        raw_items = []

    result: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        candidate_type = safe_text(item.get("candidate_type")).lower()
        if candidate_type not in SUPPORTED_CANDIDATE_TYPES:
            continue
        title = safe_text(item.get("title"))
        summary = safe_text(item.get("summary"))
        evidence_text = _trim_text(item.get("evidence_text"), 120)
        if not title or not (summary or evidence_text):
            continue
        result.append(
            {
                "candidate_type": candidate_type,
                "title": title,
                "summary": summary or evidence_text,
                "confidence": max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.0))),
                "keywords": _normalize_list(item.get("keywords")),
                "rule_text": safe_text(item.get("rule_text")),
                "method_text": safe_text(item.get("method_text")),
                "bill_text": safe_text(item.get("bill_text")),
                "bill_name": safe_text(item.get("bill_name")),
                "bill_desc": safe_text(item.get("bill_desc")),
                "final_quota_code": safe_text(item.get("final_quota_code")),
                "final_quota_name": safe_text(item.get("final_quota_name")),
                "conditions": _normalize_list(item.get("conditions")),
                "exclusions": _normalize_list(item.get("exclusions")),
                "common_errors": _normalize_list(item.get("common_errors")),
                "evidence_text": evidence_text,
            }
        )
    return result


def _target_layer(candidate_type: str) -> str:
    return {
        "rule": "RuleKnowledge",
        "method": "MethodCards",
        "experience": "ExperienceDB",
    }[candidate_type]


def _priority(candidate_type: str) -> int:
    return {
        "rule": 30,
        "method": 45,
        "experience": 60,
    }[candidate_type]


def _candidate_key(candidate: dict[str, Any]) -> tuple[str, str]:
    return (
        safe_text(candidate.get("candidate_type")).lower(),
        safe_text(candidate.get("candidate_title") or candidate.get("title")).lower(),
    )


def _merge_list_fields(base: list[str], incoming: list[str]) -> list[str]:
    seen = set(base)
    result = list(base)
    for item in incoming:
        if item in seen or not item:
            continue
        seen.add(item)
        result.append(item)
    return result


def merge_source_learning_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        key = _candidate_key(candidate)
        current = merged.get(key)
        if current is None:
            merged[key] = dict(candidate)
            continue

        if len(safe_text(candidate.get("candidate_summary"))) > len(safe_text(current.get("candidate_summary"))):
            current["candidate_summary"] = candidate.get("candidate_summary")

        payload = dict(current.get("candidate_payload") or {})
        new_payload = dict(candidate.get("candidate_payload") or {})

        for field in (
            "keywords",
            "pattern_keys",
            "exclusion_reasons",
            "core_knowledge_points",
            "quota_ids",
            "quota_names",
            "evidence_refs",
        ):
            payload[field] = _merge_list_fields(
                _normalize_list(payload.get(field)),
                _normalize_list(new_payload.get(field)),
            )

        for field in (
            "rule_text",
            "method_text",
            "summary",
            "notes",
            "bill_text",
            "bill_name",
            "bill_desc",
            "final_quota_code",
            "final_quota_name",
            "category",
            "chapter",
            "section",
            "source_file",
            "judgment_basis",
            "common_errors",
        ):
            if len(safe_text(new_payload.get(field))) > len(safe_text(payload.get(field))):
                payload[field] = new_payload.get(field)

        current["candidate_payload"] = payload
        if float(candidate.get("confidence_score") or 0.0) > float(current.get("confidence_score") or 0.0):
            current["confidence_score"] = candidate.get("confidence_score")
    return list(merged.values())


def normalize_source_learning_candidate(
    raw_candidate: dict[str, Any],
    *,
    pack: dict[str, Any],
    chunk: SourceLearningChunk,
) -> dict[str, Any] | None:
    normalized_pack = normalize_source_pack_metadata(pack)
    candidate_type = safe_text(raw_candidate.get("candidate_type")).lower()
    if candidate_type not in SUPPORTED_CANDIDATE_TYPES:
        return None

    source_id = safe_text(normalized_pack.get("source_id"))
    province = safe_text(normalized_pack.get("province"))
    specialty = safe_text(normalized_pack.get("specialty"))
    title = safe_text(raw_candidate.get("title"))
    summary = _trim_text(raw_candidate.get("summary"), 240)
    evidence_text = _trim_text(raw_candidate.get("evidence_text"), 120)
    if not title or not (summary or evidence_text):
        return None

    confidence = max(0.0, min(1.0, _safe_float(raw_candidate.get("confidence"), 0.0)))
    confidence_score = int(round(60 + confidence * 35)) if confidence > 0 else 80
    candidate_summary = summary or evidence_text
    if evidence_text:
        candidate_summary = _trim_text(
            f"{candidate_summary}\n证据: {chunk.heading} | {evidence_text}",
            300,
        )

    evidence_refs = [
        f"source_pack:{source_id}",
        f"source_pack:{source_id}#chunk:{chunk.chunk_id}",
        safe_text(normalized_pack.get("full_text_path")),
        *(_normalize_list(normalized_pack.get("evidence_refs"))),
    ]

    base_payload = {
        "province": province,
        "specialty": specialty,
        "source_file": f"source_pack:{source_id}",
        "keywords": _normalize_list(raw_candidate.get("keywords")),
        "evidence_refs": _merge_list_fields([], evidence_refs),
        "judgment_basis": evidence_text,
        "core_knowledge_points": _normalize_list(raw_candidate.get("conditions")),
        "exclusion_reasons": _normalize_list(raw_candidate.get("exclusions")),
    }

    if candidate_type == "rule":
        rule_text = safe_text(raw_candidate.get("rule_text")) or summary or evidence_text
        if not rule_text:
            return None
        payload = {
            **base_payload,
            "chapter": "Source Learning",
            "section": chunk.heading,
            "rule_text": rule_text,
        }
    elif candidate_type == "method":
        method_text = safe_text(raw_candidate.get("method_text")) or summary or evidence_text
        if not method_text:
            return None
        payload = {
            **base_payload,
            "category": chunk.heading.split("/")[0].strip() if "/" in chunk.heading else chunk.heading,
            "method_text": method_text,
            "pattern_keys": _normalize_list(raw_candidate.get("keywords")),
            "common_errors": "；".join(_normalize_list(raw_candidate.get("common_errors"))),
            "sample_count": 1,
            "confirm_rate": round(max(confidence, 0.7), 2),
        }
    else:
        final_quota_code = safe_text(raw_candidate.get("final_quota_code"))
        final_quota_name = safe_text(raw_candidate.get("final_quota_name"))
        bill_text = safe_text(raw_candidate.get("bill_text")) or summary or evidence_text
        if not bill_text:
            return None
        payload = {
            **base_payload,
            "bill_text": bill_text,
            "bill_name": safe_text(raw_candidate.get("bill_name")) or title,
            "bill_desc": safe_text(raw_candidate.get("bill_desc")) or summary,
            "bill_unit": "",
            "unit": "",
            "quota_ids": [final_quota_code] if final_quota_code else [],
            "quota_names": [final_quota_name] if final_quota_name else [],
            "final_quota_code": final_quota_code,
            "final_quota_name": final_quota_name,
            "project_name": f"source_pack:{source_id}",
            "summary": summary or evidence_text,
            "notes": candidate_summary,
            "confidence": confidence_score,
        }

    record_slug = slugify(title, max_len=48)
    return {
        "source_id": source_id,
        "source_type": "source_learning",
        "source_table": "source_packs",
        "source_record_id": f"{source_id}:{candidate_type}:{record_slug}",
        "owner": "source_learning",
        "evidence_ref": f"source_pack:{source_id}#chunk:{chunk.chunk_id}",
        "status": "draft",
        "review_status": "unreviewed",
        "candidate_type": candidate_type,
        "target_layer": _target_layer(candidate_type),
        "candidate_title": title,
        "candidate_summary": candidate_summary,
        "candidate_payload": payload,
        "priority": _priority(candidate_type),
        "approval_required": 1,
        "confidence_score": confidence_score,
    }


def call_source_learning_llm(prompt: str, *, llm_type: str | None = None) -> str:
    llm_name = (llm_type or config.AGENT_LLM or "deepseek").strip()
    if llm_name == "claude":
        return _call_claude(prompt)
    return _call_openai_compatible(prompt, llm_name)


def _call_openai_compatible(prompt: str, llm_type: str) -> str:
    import httpx
    from openai import OpenAI

    api_configs = {
        "deepseek": (config.DEEPSEEK_API_KEY, config.DEEPSEEK_BASE_URL, config.DEEPSEEK_MODEL),
        "kimi": (config.KIMI_API_KEY, config.KIMI_BASE_URL, config.KIMI_MODEL),
        "qwen": (config.QWEN_API_KEY, config.QWEN_BASE_URL, config.QWEN_MODEL),
        "openai": (config.OPENAI_API_KEY, config.OPENAI_BASE_URL, config.OPENAI_MODEL),
    }
    api_key, base_url, model = api_configs.get(llm_type, api_configs["deepseek"])
    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        http_client=httpx.Client(timeout=config.LLM_TIMEOUT, trust_env=False),
    )
    request_kwargs = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 2200,
        "timeout": config.LLM_TIMEOUT,
    }
    try:
        response = client.chat.completions.create(
            response_format={"type": "json_object"},
            **request_kwargs,
        )
    except Exception as exc:
        if "response_format" not in str(exc).lower():
            raise
        response = client.chat.completions.create(**request_kwargs)
    content = response.choices[0].message.content
    return safe_text(content)


def _call_claude(prompt: str) -> str:
    import httpx

    if config.CLAUDE_BASE_URL:
        url = f"{config.CLAUDE_BASE_URL.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": config.CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": 2200,
            "temperature": 0.1,
            "messages": [{"role": "user", "content": prompt}],
        }
        response = httpx.post(url, headers=headers, json=body, timeout=config.LLM_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        return safe_text(data["content"][0]["text"])

    import anthropic

    client = anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)
    message = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2200,
        temperature=0.1,
        messages=[{"role": "user", "content": prompt}],
    )
    return safe_text(message.content[0].text)
