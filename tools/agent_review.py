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


def export_review_items(excel_path, limit=None, confidence_threshold=85):
    """导出需要审核的清单项（红色+黄色）及其候选列表"""
    print(f"读取清单: {excel_path}")
    reader = BillReader()
    items = reader.read_excel(excel_path)
    if not items:
        print("未读取到清单项")
        return

    items = clean_bill_items(items)
    if limit:
        items = items[:limit]

    print(f"初始化搜索引擎...")
    searcher = HybridSearcher()
    validator = ParamValidator()
    reranker = Reranker()
    rule_validator = RuleValidator()

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
            confidence = int(best.get("param_score", 0.5) * 85)
        else:
            best = candidates[0]
            confidence = max(int(best.get("param_score", 0.0) * 40), 15)

        if confidence >= confidence_threshold:
            green_count += 1
            continue

        # 简化候选列表（只保留关键信息，减小文件大小）
        simple_candidates = []
        for c in candidates[:10]:
            simple_candidates.append({
                "index": len(simple_candidates) + 1,
                "quota_id": c["quota_id"],
                "name": c["name"],
                "unit": c.get("unit", ""),
                "param_match": c.get("param_match", True),
                "param_score": round(c.get("param_score", 0), 2),
                "param_detail": c.get("param_detail", ""),
            })

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
                "quota_id": best["quota_id"],
                "name": best["name"],
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
    output_path = f"output/review/agent_review_{basename}.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "source_file": excel_path,
            "total_items": len(items),
            "green_count": green_count,
            "rule_count": rule_count,
            "review_count": len(review_items),
            "items": review_items,
        }, f, ensure_ascii=False, indent=2)

    print(f"\n汇总: 总{len(items)}条, 绿{green_count}, 规则{rule_count}, 待审{len(review_items)}")
    print(f"审核文件: {output_path}")
    return output_path


def store_decisions(decisions_file):
    """把审核决策存入经验库"""
    from src.experience_db import ExperienceDB
    from src.learning_notebook import LearningNotebook

    with open(decisions_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    exp_db = ExperienceDB()
    notebook = LearningNotebook()
    stored = 0

    for item in data.get("decisions", []):
        bill_text = f"{item['name']} {item.get('description', '')}".strip()
        quota_ids = item.get("correct_quota_ids", [])
        quota_names = item.get("correct_quota_names", [])
        reasoning = item.get("reasoning", "")

        if not quota_ids:
            continue

        # 存入经验库（user_confirmed来源 = 权威层数据）
        exp_db.add_experience(
            bill_text=normalize_bill_text(item["name"], item.get("description", "")),
            quota_ids=quota_ids,
            quota_names=quota_names,
            source="user_confirmed",
            confidence=90,
            specialty=item.get("specialty", ""),
            notes=reasoning,
        )

        # 记录学习笔记
        notebook.record_note({
            "bill_text": bill_text,
            "bill_name": item["name"],
            "bill_description": item.get("description", ""),
            "specialty": item.get("specialty", ""),
            "reasoning": reasoning,
            "result_quota_ids": quota_ids,
            "result_quota_names": quota_names,
            "confidence": 90,
            "llm_type": "claude_code_manual",
        })
        # 标记为已确认
        notebook.mark_user_feedback(notebook.get_stats()["total"], "confirmed")

        stored += 1

    print(f"已存入 {stored} 条经验到权威层")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--store":
        # 存入模式
        store_decisions(sys.argv[2])
    else:
        # 导出审核模式
        limit = None
        for i, arg in enumerate(sys.argv):
            if arg == "--limit" and i + 1 < len(sys.argv):
                limit = int(sys.argv[i + 1])
        export_review_items(sys.argv[1], limit=limit)
