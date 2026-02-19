"""
Agent手动审核工具 - Claude Code 充当大模型大脑
用法: python tools/agent_review.py <清单Excel路径> [--limit N]

功能:
1. 跑搜索模式获取每条清单的候选列表
2. 输出红色/黄色项的候选详情到JSON文件
3. 等待人工（或Claude Code）审核判断
4. 把确认的结果存入经验库
"""

import sys
import os
import json
import re
import argparse
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src.bill_reader import BillReader
from src.bill_cleaner import clean_bill_items
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator
from src.text_parser import parser as text_parser, normalize_bill_text
from src.specialty_classifier import classify as classify_specialty
from src.reranker import Reranker
from src.rule_validator import RuleValidator


def _resolve_runtime_province(name: str = None) -> str:
    """Resolve province and sync runtime province for downstream modules."""
    province = config.resolve_province(name, interactive=False)
    config.set_current_province(province)
    return province


def _safe_unlink(path: str):
    try:
        os.remove(path)
    except OSError as e:
        print(f"[WARN] 清理临时文件失败: {path} ({e})")


def _atomic_write_json(path: str, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix=f"{target.stem}_tmp_",
            dir=str(target.parent),
            encoding="utf-8",
            delete=False,
        ) as tf:
            tmp_path = tf.name
            json.dump(payload, tf, ensure_ascii=False, indent=2)
        os.replace(tmp_path, target)
    finally:
        if tmp_path and Path(tmp_path).exists():
            _safe_unlink(tmp_path)


def _normalize_fallbacks(value) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = list(value)
    elif isinstance(value, str):
        items = [value]
    else:
        items = []

    cleaned = []
    seen = set()
    for item in items:
        if item is None:
            continue
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


def get_candidates_for_item(item, searcher, validator, reranker, rule_validator):
    """对单条清单项跑搜索+参数验证，返回候选列表"""
    name = item.get("name", "")
    desc = item.get("description", "") or ""
    section = item.get("section", "") or ""
    full_query = f"{name} {desc}".strip()
    search_query = text_parser.build_quota_query(name, desc)

    # 专业分类
    classification = {
        "primary": item.get("specialty"),
        "fallbacks": item.get("specialty_fallbacks", []),
    }
    if not classification["primary"]:
        classification = classify_specialty(name, desc, section_title=section)
    primary = classification.get("primary")
    primary = str(primary).strip() if primary is not None else ""
    primary = primary or None
    fallbacks = _normalize_fallbacks(classification.get("fallbacks", []))
    if primary:
        fallbacks = [b for b in fallbacks if b != primary]
    classification["primary"] = primary
    classification["fallbacks"] = fallbacks

    # 规则预匹配
    rule_books = [classification["primary"]] + classification.get("fallbacks", [])
    rule_books = [b for b in rule_books if b]
    rule_result = rule_validator.match_by_rules(
        full_query, item, clean_query=search_query,
        books=rule_books if rule_books else None)
    if rule_result:
        return None, rule_result, "rule"

    # 搜索
    from main import cascade_search
    candidates = cascade_search(searcher, search_query, classification)
    if candidates:
        candidates = reranker.rerank(search_query, candidates)
    if candidates:
        candidates = validator.validate_candidates(
            full_query, candidates, supplement_query=search_query)

    return candidates, None, "search"


def export_review_items(excel_path, limit=None, confidence_threshold=85, province=None):
    """导出需要审核的清单项（红色+黄色）及其候选列表"""
    province = _resolve_runtime_province(province)
    print(f"读取清单: {excel_path}")
    print(f"省份: {province}")
    reader = BillReader()
    items = reader.read_excel(excel_path)
    if not items:
        print("未读取到清单项")
        return

    items = clean_bill_items(items)
    if limit:
        items = items[:limit]

    print(f"初始化搜索引擎...")
    searcher = HybridSearcher(province=province)
    validator = ParamValidator()
    reranker = Reranker()
    rule_validator = RuleValidator(province=province)

    review_items = []
    green_count = 0
    rule_count = 0

    for idx, item in enumerate(items, 1):
        candidates, rule_result, source = get_candidates_for_item(
            item, searcher, validator, reranker, rule_validator)

        if rule_result:
            rule_count += 1
            continue

        if not candidates:
            review_items.append({
                "index": idx,
                "name": item.get("name", ""),
                "description": (item.get("description", "") or "")[:200],
                "unit": item.get("unit", ""),
                "specialty": item.get("specialty", ""),
                "search_query": text_parser.build_quota_query(
                    item.get("name", ""), item.get("description", "") or ""),
                "candidates": [],
                "current_best": None,
                "confidence": 0,
            })
            continue

        # 取当前最佳
        matched = [c for c in candidates if c.get("param_match", True)]
        if matched:
            best = matched[0]
            confidence = int(best.get("param_score", 0.5) * 95)  # 乘95：param_score≥0.90绿色，典型向上取档(0.95+)得90+
        else:
            best = candidates[0]
            confidence = max(int(best.get("param_score", 0.0) * 45), 15)

        if confidence >= confidence_threshold:
            green_count += 1
            continue

        # 简化候选列表（只保留关键信息，减小文件大小）
        simple_candidates = []
        for c in candidates[:10]:
            if not isinstance(c, dict):
                continue
            quota_id = str(c.get("quota_id", "")).strip()
            quota_name = str(c.get("name", "")).strip()
            if not quota_id or not quota_name:
                continue
            simple_candidates.append({
                "index": len(simple_candidates) + 1,
                "quota_id": quota_id,
                "name": quota_name,
                "unit": c.get("unit", ""),
                "param_match": c.get("param_match", True),
                "param_score": round(c.get("param_score", 0), 2),
                "param_detail": c.get("param_detail", ""),
            })

        best_quota_id = str(best.get("quota_id", "")).strip() if isinstance(best, dict) else ""
        best_name = str(best.get("name", "")).strip() if isinstance(best, dict) else ""
        review_items.append({
            "index": idx,
            "name": item.get("name", ""),
            "description": (item.get("description", "") or "")[:200],
            "unit": item.get("unit", ""),
            "specialty": item.get("specialty", ""),
            "search_query": text_parser.build_quota_query(
                item.get("name", ""), item.get("description", "") or ""),
            "candidates": simple_candidates,
            "current_best": {
                "quota_id": best_quota_id,
                "name": best_name,
                "confidence": confidence,
            },
            "confidence": confidence,
        })

        if idx % 20 == 0:
            print(f"  进度: {idx}/{len(items)} (绿{green_count} 规则{rule_count} 待审{len(review_items)})")

    # 按置信度排序（最差的在前面）
    review_items.sort(key=lambda x: x["confidence"])

    # 输出
    basename = Path(excel_path).stem.replace("-小栗AI自动编清单", "")[:30]
    basename = re.sub(r'[\\/:*?"<>|]+', "_", basename).strip(" ._") or "project"
    output_path = f"output/review/agent_review_{basename}.json"
    _atomic_write_json(output_path, {
        "source_file": excel_path,
        "province": province,
        "total_items": len(items),
        "green_count": green_count,
        "rule_count": rule_count,
        "review_count": len(review_items),
        "items": review_items,
    })

    print(f"\n汇总: 总{len(items)}条, 绿{green_count}, 规则{rule_count}, 待审{len(review_items)}")
    print(f"审核文件: {output_path}")
    return output_path


def store_decisions(decisions_file, province=None):
    """把审核决策存入经验库"""
    from src.experience_db import ExperienceDB
    from src.learning_notebook import LearningNotebook

    province = _resolve_runtime_province(province)
    print(f"省份: {province}")

    try:
        with open(decisions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"读取决策文件失败: {decisions_file} ({e})")
        return
    if not isinstance(data, dict):
        print("决策文件格式错误：根节点必须是对象")
        return
    decisions = data.get("decisions", [])
    if not isinstance(decisions, list):
        print("决策文件格式错误：decisions 必须是数组")
        return

    exp_db = ExperienceDB()
    notebook = LearningNotebook()
    stored = 0
    skipped = 0

    for item in decisions:
        if not isinstance(item, dict):
            skipped += 1
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            skipped += 1
            continue
        bill_text = f"{name} {item.get('description', '')}".strip()
        quota_ids = item.get("correct_quota_ids", [])
        quota_names = item.get("correct_quota_names", [])
        reasoning = item.get("reasoning", "")

        if not isinstance(quota_ids, list) or not quota_ids:
            skipped += 1
            continue

        # 存入经验库（user_confirmed来源 = 权威层数据）
        record_id = exp_db.add_experience(
            bill_text=normalize_bill_text(name, item.get("description", "")),
            quota_ids=quota_ids,
            quota_names=quota_names,
            source="user_confirmed",
            confidence=90,
            province=province,
            specialty=item.get("specialty", ""),
            notes=reasoning,
        )
        if record_id <= 0:
            skipped += 1
            continue

        # 记录学习笔记
        note_id = notebook.record_note({
            "bill_text": bill_text,
            "bill_name": name,
            "bill_description": item.get("description", ""),
            "specialty": item.get("specialty", ""),
            "reasoning": reasoning,
            "result_quota_ids": quota_ids,
            "result_quota_names": quota_names,
            "confidence": 90,
            "llm_type": "claude_code_manual",
        })
        # 标记该条笔记为已确认（使用返回的note_id，避免并发时标错）
        if note_id > 0:
            notebook.mark_user_feedback(note_id, "confirmed")

        stored += 1

    print(f"已存入 {stored} 条经验到权威层，跳过 {skipped} 条")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Agent手动审核工具（导出待审核项 / 回写审核决策）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python tools/agent_review.py 清单.xlsx --limit 200
  python tools/agent_review.py --store output/review_decisions.json
        """.strip(),
    )
    parser.add_argument(
        "excel_path",
        nargs="?",
        help="待审核清单Excel路径（导出模式）",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="导出审核项时最多处理的清单条数",
    )
    parser.add_argument(
        "--store",
        metavar="DECISIONS_JSON",
        help="把审核决策JSON回写到经验库",
    )
    parser.add_argument(
        "--province",
        default=None,
        help=f"省份（默认: {config.CURRENT_PROVINCE}）",
    )
    args = parser.parse_args()

    if args.store:
        store_decisions(args.store, province=args.province)
    elif args.excel_path:
        export_review_items(args.excel_path, limit=args.limit, province=args.province)
    else:
        parser.print_help()
        sys.exit(1)
