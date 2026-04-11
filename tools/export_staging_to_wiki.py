from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.knowledge_staging import KnowledgeStaging  # noqa: E402


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "knowledge_wiki"
MANIFEST_NAME = ".generated_manifest.json"

CATEGORY_TO_FOLDER = {
    "rules": "10-规则",
    "cases": "20-案例",
    "methods": "30-方法",
    "concepts": "40-概念",
    "reviews": "50-审核沉淀",
    "sources": "60-资料来源",
    "inbox": "70-待处理",
    "daily": "80-日报周报",
    "entities": "90-实体",
}

PROMOTION_CATEGORY_MAP = {
    "rule": "rules",
    "method": "methods",
    "experience": "cases",
    "universal": "concepts",
}

PROMOTION_FILE_PREFIX_MAP = {
    "rule": "rule",
    "method": "method",
    "experience": "case",
    "universal": "concept",
}


def safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\r\n", "\n").strip()


def safe_slug(text: str, *, max_len: int = 48) -> str:
    value = safe_text(text)
    value = re.sub(r'[\\/:*?"<>|]+', "-", value)
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-._ ")
    if not value:
        return "item"
    return value[:max_len].rstrip("-._ ")


def format_date(value: Any) -> str:
    if value in (None, ""):
        return datetime.now().strftime("%Y-%m-%d")
    try:
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d")
    except Exception:
        return safe_text(value) or datetime.now().strftime("%Y-%m-%d")


def yaml_scalar(value: Any) -> str:
    text = safe_text(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def dump_frontmatter(payload: dict[str, Any]) -> str:
    ordered_keys = [
        "title",
        "type",
        "status",
        "province",
        "specialty",
        "source_refs",
        "source_kind",
        "created_at",
        "updated_at",
        "confidence",
        "owner",
        "tags",
        "related",
    ]
    lines = ["---"]
    for key in ordered_keys:
        value = payload.get(key)
        if key in {"source_refs", "tags", "related"}:
            items = [safe_text(item) for item in (value or []) if safe_text(item)]
            if not items:
                lines.append(f"{key}: []")
            else:
                lines.append(f"{key}:")
                lines.extend([f"  - {yaml_scalar(item)}" for item in items])
            continue
        if isinstance(value, (int, float)) and key == "confidence":
            lines.append(f"{key}: {int(value)}")
            continue
        lines.append(f"{key}: {yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def markdown_list(items: list[str]) -> list[str]:
    values = [safe_text(item) for item in items if safe_text(item)]
    if not values:
        return ["- 无"]
    return [f"- {item}" for item in values]


def block_text(text: Any) -> str:
    value = safe_text(text)
    return value if value else "无"


def normalize_status(record: dict[str, Any]) -> str:
    status = safe_text(record.get("status")).lower()
    review_status = safe_text(record.get("review_status")).lower()
    if "promoted" in {status, review_status}:
        return "promoted"
    if review_status in {"approved", "rejected", "rolled_back", "reviewing"} or status in {"approved", "rejected", "rolled_back"}:
        return "reviewed"
    return "draft"


def ensure_dirs(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for folder in CATEGORY_TO_FOLDER:
        (output_dir / folder).mkdir(parents=True, exist_ok=True)


def load_previous_manifest(output_dir: Path) -> dict[str, Any]:
    manifest_path = output_dir / MANIFEST_NAME
    if not manifest_path.exists():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def cleanup_previous_generated(output_dir: Path, manifest: dict[str, Any]) -> None:
    for item in manifest.get("files", []):
        rel = safe_text(item.get("relative_path"))
        if not rel:
            continue
        path = (output_dir / rel).resolve()
        try:
            path.relative_to(output_dir.resolve())
        except Exception:
            continue
        if path.exists():
            path.unlink()


def extract_audit_id(source_record_id: Any) -> str:
    text = safe_text(source_record_id)
    if ":" in text:
        return text.split(":", 1)[0].strip()
    return text


def build_review_page(audit: dict[str, Any], related_pages: list[str]) -> tuple[str, str]:
    audit_id = int(audit["id"])
    slug = safe_slug(audit.get("bill_name") or audit.get("error_type") or f"audit-{audit_id}")
    relative_path = f"reviews/review-{audit_id:04d}-{slug}.md"
    title = f"{safe_text(audit.get('bill_name')) or '未命名清单'} 审核沉淀"
    source_refs = [
        f"staging:audit_errors:{audit_id}",
        f"task:{safe_text(audit.get('task_id'))}" if safe_text(audit.get("task_id")) else "",
        f"result:{safe_text(audit.get('result_id'))}" if safe_text(audit.get("result_id")) else "",
    ]
    tags = [
        "audit",
        safe_text(audit.get("match_source")),
        safe_text(audit.get("error_type")),
        safe_text(audit.get("error_level")),
    ]
    frontmatter = dump_frontmatter({
        "title": title,
        "type": "review",
        "status": normalize_status(audit),
        "province": safe_text(audit.get("province")),
        "specialty": safe_text(audit.get("specialty")),
        "source_refs": source_refs,
        "source_kind": "staging",
        "created_at": format_date(audit.get("created_at")),
        "updated_at": format_date(audit.get("updated_at")),
        "confidence": 90,
        "owner": safe_text(audit.get("owner")) or "system",
        "tags": [item for item in tags if item],
        "related": related_pages,
    })
    lines = [
        frontmatter,
        "",
        f"# {title}",
        "",
        "## 来源",
        f"- 审核记录: `audit_errors:{audit_id}`",
        f"- 任务 ID: `{safe_text(audit.get('task_id')) or '无'}`",
        f"- 结果 ID: `{safe_text(audit.get('result_id')) or '无'}`",
        f"- 匹配来源: `{safe_text(audit.get('match_source')) or '无'}`",
        "",
        "## 清单信息",
        f"- 名称: {safe_text(audit.get('bill_name')) or '无'}",
        f"- 特征: {safe_text(audit.get('bill_desc')) or '无'}",
        f"- 省份: {safe_text(audit.get('province')) or '无'}",
        f"- 专业: {safe_text(audit.get('specialty')) or '无'}",
        "",
        "## 错配结论",
        f"- 错因类型: `{safe_text(audit.get('error_type')) or '无'}`",
        f"- 错因等级: `{safe_text(audit.get('error_level')) or '无'}`",
        f"- 原命中定额: `{safe_text(audit.get('predicted_quota_code')) or '无'}` {safe_text(audit.get('predicted_quota_name'))}".rstrip(),
        f"- 修正定额: `{safe_text(audit.get('corrected_quota_code')) or '无'}` {safe_text(audit.get('corrected_quota_name'))}".rstrip(),
        "",
        "## 判断依据",
        block_text(audit.get("decision_basis") or audit.get("root_cause")),
        "",
        "## 修正建议",
        block_text(audit.get("fix_suggestion")),
        "",
        "## 根因标签",
        *markdown_list(list(audit.get("root_cause_tags") or [])),
        "",
        "## 可晋升输出",
        f"- 可生成规则: {'是' if int(audit.get('can_promote_rule') or 0) else '否'}",
        f"- 可生成方法: {'是' if int(audit.get('can_promote_method') or 0) else '否'}",
        "",
        "## 关联页面",
        *markdown_list([f"[[{Path(item).stem}]]" for item in related_pages]),
    ]
    return relative_path, "\n".join(lines).strip() + "\n"


def build_rule_page(promotion: dict[str, Any], related_pages: list[str]) -> tuple[str, str]:
    promotion_id = int(promotion["id"])
    payload = dict(promotion.get("candidate_payload") or {})
    slug = safe_slug(promotion.get("candidate_title") or f"rule-{promotion_id}")
    relative_path = f"rules/rule-{promotion_id:04d}-{slug}.md"
    title = safe_text(promotion.get("candidate_title")) or f"规则候选 {promotion_id}"
    source_refs = [f"staging:promotion_queue:{promotion_id}"]
    audit_id = extract_audit_id(promotion.get("source_record_id"))
    if audit_id:
        source_refs.append(f"staging:audit_errors:{audit_id}")
    frontmatter = dump_frontmatter({
        "title": title,
        "type": "rule",
        "status": normalize_status(promotion),
        "province": safe_text(payload.get("province")) or safe_text(promotion.get("province")),
        "specialty": safe_text(payload.get("specialty")),
        "source_refs": source_refs,
        "source_kind": "staging",
        "created_at": format_date(promotion.get("created_at")),
        "updated_at": format_date(promotion.get("updated_at")),
        "confidence": 85,
        "owner": safe_text(promotion.get("owner")) or "system",
        "tags": ["rule", safe_text(promotion.get("review_status")), safe_text(promotion.get("target_layer"))],
        "related": related_pages,
    })
    lines = [
        frontmatter,
        "",
        f"# {title}",
        "",
        "## 候选信息",
        f"- 晋升记录: `promotion_queue:{promotion_id}`",
        f"- 来源审核: `audit_errors:{audit_id or '无'}`",
        f"- 目标层: `{safe_text(promotion.get('target_layer')) or 'RuleKnowledge'}`",
        f"- 审核状态: `{safe_text(promotion.get('review_status')) or 'unreviewed'}`",
        "",
        "## 规则正文",
        block_text(payload.get("rule_text")),
        "",
        "## 关键词",
        *markdown_list(list(payload.get("keywords") or [])),
        "",
        "## 结构信息",
        f"- 章节: {safe_text(payload.get('chapter')) or '无'}",
        f"- 小节: {safe_text(payload.get('section')) or '无'}",
        f"- 来源文件: {safe_text(payload.get('source_file')) or '无'}",
        "",
        "## 审核说明",
        block_text(promotion.get("candidate_summary") or promotion.get("review_comment")),
        "",
        "## 关联页面",
        *markdown_list([f"[[{Path(item).stem}]]" for item in related_pages]),
    ]
    return relative_path, "\n".join(lines).strip() + "\n"


def build_method_page(promotion: dict[str, Any], related_pages: list[str]) -> tuple[str, str]:
    promotion_id = int(promotion["id"])
    payload = dict(promotion.get("candidate_payload") or {})
    slug = safe_slug(promotion.get("candidate_title") or f"method-{promotion_id}")
    relative_path = f"methods/method-{promotion_id:04d}-{slug}.md"
    title = safe_text(promotion.get("candidate_title")) or f"方法候选 {promotion_id}"
    source_refs = [f"staging:promotion_queue:{promotion_id}"]
    audit_id = extract_audit_id(promotion.get("source_record_id"))
    if audit_id:
        source_refs.append(f"staging:audit_errors:{audit_id}")
    frontmatter = dump_frontmatter({
        "title": title,
        "type": "method",
        "status": normalize_status(promotion),
        "province": safe_text(payload.get("province")),
        "specialty": safe_text(payload.get("specialty")),
        "source_refs": source_refs,
        "source_kind": "staging",
        "created_at": format_date(promotion.get("created_at")),
        "updated_at": format_date(promotion.get("updated_at")),
        "confidence": 85,
        "owner": safe_text(promotion.get("owner")) or "system",
        "tags": ["method", safe_text(payload.get("category")), safe_text(promotion.get("review_status"))],
        "related": related_pages,
    })
    lines = [
        frontmatter,
        "",
        f"# {title}",
        "",
        "## 方法卡片",
        f"- 晋升记录: `promotion_queue:{promotion_id}`",
        f"- 方法分类: {safe_text(payload.get('category')) or '无'}",
        f"- 样本数: `{payload.get('sample_count') if payload.get('sample_count') is not None else 0}`",
        f"- 确认率: `{payload.get('confirm_rate') if payload.get('confirm_rate') is not None else 0}`",
        "",
        "## 方法正文",
        block_text(payload.get("method_text")),
        "",
        "## 关键词",
        *markdown_list(list(payload.get("keywords") or [])),
        "",
        "## 模式键",
        *markdown_list(list(payload.get("pattern_keys") or [])),
        "",
        "## 常见误判",
        block_text(payload.get("common_errors")),
        "",
        "## 关联页面",
        *markdown_list([f"[[{Path(item).stem}]]" for item in related_pages]),
    ]
    return relative_path, "\n".join(lines).strip() + "\n"


def build_case_page(promotion: dict[str, Any], related_pages: list[str]) -> tuple[str, str]:
    promotion_id = int(promotion["id"])
    payload = dict(promotion.get("candidate_payload") or {})
    slug = safe_slug(promotion.get("candidate_title") or f"case-{promotion_id}")
    relative_path = f"cases/case-{promotion_id:04d}-{slug}.md"
    title = safe_text(promotion.get("candidate_title")) or f"历史案例候选 {promotion_id}"
    source_refs = [f"staging:promotion_queue:{promotion_id}"]
    audit_id = extract_audit_id(promotion.get("source_record_id"))
    if audit_id:
        source_refs.append(f"staging:audit_errors:{audit_id}")
    frontmatter = dump_frontmatter({
        "title": title,
        "type": "case",
        "status": normalize_status(promotion),
        "province": safe_text(payload.get("province")),
        "specialty": safe_text(payload.get("specialty")),
        "source_refs": source_refs,
        "source_kind": "staging",
        "created_at": format_date(promotion.get("created_at")),
        "updated_at": format_date(promotion.get("updated_at")),
        "confidence": int(payload.get("confidence", 85) or 85),
        "owner": safe_text(promotion.get("owner")) or "system",
        "tags": ["experience", safe_text(payload.get("final_quota_code")), safe_text(promotion.get("review_status"))],
        "related": related_pages,
    })
    lines = [
        frontmatter,
        "",
        f"# {title}",
        "",
        "## 案例概览",
        f"- 晋升记录: `promotion_queue:{promotion_id}`",
        f"- 清单名称: {safe_text(payload.get('bill_name')) or '无'}",
        f"- 清单特征: {safe_text(payload.get('bill_desc')) or '无'}",
        f"- 计量单位: {safe_text(payload.get('bill_unit') or payload.get('unit')) or '无'}",
        "",
        "## 最终定额",
        f"- 定额编码: `{safe_text(payload.get('final_quota_code')) or '无'}`",
        f"- 定额名称: {safe_text(payload.get('final_quota_name')) or '无'}",
        "",
        "## 案例摘要",
        block_text(payload.get("summary") or payload.get("notes") or promotion.get("candidate_summary")),
        "",
        "## 经验字段",
        f"- 项目来源: {safe_text(payload.get('project_name')) or '无'}",
        f"- 组合文本: {safe_text(payload.get('bill_text')) or '无'}",
        "",
        "## 关联页面",
        *markdown_list([f"[[{Path(item).stem}]]" for item in related_pages]),
    ]
    return relative_path, "\n".join(lines).strip() + "\n"


def build_daily_page(*,
                     export_date: str,
                     audit_count: int,
                     promotion_count: int,
                     category_counts: dict[str, int],
                     samples: list[str]) -> tuple[str, str]:
    relative_path = f"daily/daily-{export_date.replace('-', '')}-staging-export.md"
    title = f"{export_date} staging 导出日报"
    frontmatter = dump_frontmatter({
        "title": title,
        "type": "daily_summary",
        "status": "reviewed",
        "province": "",
        "specialty": "",
        "source_refs": ["staging:audit_errors", "staging:promotion_queue"],
        "source_kind": "system",
        "created_at": export_date,
        "updated_at": export_date,
        "confidence": 100,
        "owner": "codex",
        "tags": ["daily", "staging", "export"],
        "related": samples,
    })
    lines = [
        frontmatter,
        "",
        f"# {title}",
        "",
        "## 导出统计",
        f"- 审核沉淀页面: `{audit_count}`",
        f"- 晋升候选页面: `{promotion_count}`",
    ]
    for key in ["rules", "methods", "cases", "reviews"]:
        if category_counts.get(key):
            lines.append(f"- {key}: `{category_counts[key]}`")
    lines.extend([
        "",
        "## 抽样页面",
        *markdown_list([f"[[{Path(item).stem}]]" for item in samples[:8]]),
    ])
    return relative_path, "\n".join(lines).strip() + "\n"


def build_root_index(export_date: str, counts: dict[str, int]) -> str:
    frontmatter = dump_frontmatter({
        "title": "JARVIS Wiki Index",
        "type": "index",
        "status": "reviewed",
        "province": "",
        "specialty": "",
        "source_refs": ["staging:audit_errors", "staging:promotion_queue"],
        "source_kind": "system",
        "created_at": export_date,
        "updated_at": export_date,
        "confidence": 100,
        "owner": "codex",
        "tags": ["wiki", "index", "staging"],
        "related": [],
    })
    lines = [
        frontmatter,
        "",
        "# JARVIS Wiki Index",
        "",
        "## 本次导出",
        f"- 导出日期: `{export_date}`",
        f"- 审核沉淀: `{counts.get('reviews', 0)}`",
        f"- 规则候选: `{counts.get('rules', 0)}`",
        f"- 方法候选: `{counts.get('methods', 0)}`",
        f"- 历史案例候选: `{counts.get('cases', 0)}`",
        "",
        "## 目录",
        "- `reviews/` 审核沉淀页面",
        "- `rules/` 规则候选页面",
        "- `methods/` 审核方法页面",
        "- `cases/` 历史案例页面",
        "- `daily/` 导出日报",
        "",
        "## 用法",
        "- 先运行 `python tools/export_staging_to_wiki.py`",
        "- 再运行 `powershell -ExecutionPolicy Bypass -File tools/sync_wiki_to_obsidian.ps1`",
    ]
    return "\n".join(lines).strip() + "\n"


def build_root_log(export_date: str, counts: dict[str, int]) -> str:
    frontmatter = dump_frontmatter({
        "title": "JARVIS Wiki Log",
        "type": "daily_summary",
        "status": "reviewed",
        "province": "",
        "specialty": "",
        "source_refs": ["staging:audit_errors", "staging:promotion_queue"],
        "source_kind": "system",
        "created_at": export_date,
        "updated_at": export_date,
        "confidence": 100,
        "owner": "codex",
        "tags": ["wiki", "log", "staging"],
        "related": [],
    })
    lines = [
        frontmatter,
        "",
        "# JARVIS Wiki Log",
        "",
        f"## {export_date}",
        "",
        f"- 导出审核沉淀页面 {counts.get('reviews', 0)} 个",
        f"- 导出规则候选页面 {counts.get('rules', 0)} 个",
        f"- 导出方法候选页面 {counts.get('methods', 0)} 个",
        f"- 导出历史案例页面 {counts.get('cases', 0)} 个",
        "- 生成 `.generated_manifest.json` 供 Obsidian 同步脚本使用",
    ]
    return "\n".join(lines).strip() + "\n"


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def export_staging_to_wiki(*,
                           output_dir: Path = DEFAULT_OUTPUT_DIR,
                           audit_limit: int = 200,
                           promotion_limit: int = 300) -> dict[str, Any]:
    ensure_dirs(output_dir)
    previous_manifest = load_previous_manifest(output_dir)
    cleanup_previous_generated(output_dir, previous_manifest)

    staging = KnowledgeStaging()
    audits = staging.list_active_audit_errors(limit=audit_limit)
    promotions = staging.list_promotions(limit=promotion_limit)

    promotion_pages_by_audit: dict[str, list[str]] = {}
    for promotion in promotions:
        audit_id = extract_audit_id(promotion.get("source_record_id"))
        if not audit_id:
            continue
        folder = PROMOTION_CATEGORY_MAP.get(safe_text(promotion.get("candidate_type")).lower())
        if not folder:
            continue
        candidate_type = safe_text(promotion.get("candidate_type")).lower()
        file_prefix = PROMOTION_FILE_PREFIX_MAP.get(candidate_type, candidate_type)
        slug = safe_slug(promotion.get("candidate_title") or f"{file_prefix}-{promotion.get('id')}")
        relative_path = f"{folder}/{file_prefix}-{int(promotion['id']):04d}-{slug}.md"
        promotion_pages_by_audit.setdefault(audit_id, []).append(relative_path)

    generated_files: list[dict[str, Any]] = []
    category_counts = {key: 0 for key in CATEGORY_TO_FOLDER}
    sample_pages: list[str] = []

    for audit in audits:
        related = promotion_pages_by_audit.get(str(audit.get("id")), [])
        relative_path, content = build_review_page(audit, related)
        write_text(output_dir / relative_path, content)
        generated_files.append({
            "relative_path": relative_path,
            "category": "reviews",
            "obsidian_dir": CATEGORY_TO_FOLDER["reviews"],
            "title": safe_text(audit.get("bill_name")) or f"audit-{audit.get('id')}",
            "source_ref": f"staging:audit_errors:{audit.get('id')}",
        })
        category_counts["reviews"] += 1
        if len(sample_pages) < 8:
            sample_pages.append(relative_path)

    builders = {
        "rule": build_rule_page,
        "method": build_method_page,
        "experience": build_case_page,
    }
    for promotion in promotions:
        candidate_type = safe_text(promotion.get("candidate_type")).lower()
        builder = builders.get(candidate_type)
        folder = PROMOTION_CATEGORY_MAP.get(candidate_type)
        if not builder or not folder:
            continue
        audit_id = extract_audit_id(promotion.get("source_record_id"))
        related = []
        if audit_id:
            for item in generated_files:
                if item.get("source_ref") == f"staging:audit_errors:{audit_id}":
                    related.append(item["relative_path"])
        relative_path, content = builder(promotion, related)
        write_text(output_dir / relative_path, content)
        generated_files.append({
            "relative_path": relative_path,
            "category": folder,
            "obsidian_dir": CATEGORY_TO_FOLDER[folder],
            "title": safe_text(promotion.get("candidate_title")) or f"{candidate_type}-{promotion.get('id')}",
            "source_ref": f"staging:promotion_queue:{promotion.get('id')}",
        })
        category_counts[folder] += 1
        if len(sample_pages) < 8:
            sample_pages.append(relative_path)

    export_date = datetime.now().strftime("%Y-%m-%d")
    daily_relative_path, daily_content = build_daily_page(
        export_date=export_date,
        audit_count=category_counts["reviews"],
        promotion_count=category_counts["rules"] + category_counts["methods"] + category_counts["cases"],
        category_counts=category_counts,
        samples=sample_pages,
    )
    write_text(output_dir / daily_relative_path, daily_content)
    generated_files.append({
        "relative_path": daily_relative_path,
        "category": "daily",
        "obsidian_dir": CATEGORY_TO_FOLDER["daily"],
        "title": f"{export_date} staging 导出日报",
        "source_ref": "staging:export",
    })
    category_counts["daily"] += 1

    write_text(output_dir / "index.md", build_root_index(export_date, category_counts))
    write_text(output_dir / "log.md", build_root_log(export_date, category_counts))

    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_db": str(staging.db_path),
        "output_dir": str(output_dir),
        "counts": category_counts,
        "files": generated_files,
    }
    write_text(output_dir / MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Export knowledge staging records into knowledge_wiki markdown pages.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="knowledge_wiki output directory")
    parser.add_argument("--audit-limit", type=int, default=200, help="max active audit errors to export")
    parser.add_argument("--promotion-limit", type=int, default=300, help="max promotion records to export")
    args = parser.parse_args()

    manifest = export_staging_to_wiki(
        output_dir=Path(args.output_dir),
        audit_limit=max(1, args.audit_limit),
        promotion_limit=max(1, args.promotion_limit),
    )
    print(f"Exported {len(manifest['files'])} files to {manifest['output_dir']}")
    for key in ["reviews", "rules", "methods", "cases", "daily"]:
        if manifest["counts"].get(key):
            print(f"  {key}: {manifest['counts'][key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
