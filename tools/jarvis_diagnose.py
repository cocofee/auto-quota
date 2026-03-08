# -*- coding: utf-8 -*-
"""
Jarvis 诊断工具 - 分析人工审核项的根因

跑完Jarvis流水线后，自动分析"待人工"条目为什么匹配失败：
  - 同义词缺口：清单叫"凿槽"，定额库叫"刨沟"
  - 排序偏差：正确定额在候选里但没排第一
  - 需人工：定额库确实没有对应项

用法：
    python tools/jarvis_diagnose.py "output/匹配结果_xxx_已审核.xlsx" --province "上海建筑"
"""

import sys
import os
import json
import argparse
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# 清单常见叫法 → 定额库可能的叫法（跑的越多积累越多）
SYNONYM_VARIANTS = {
    "凿槽": ["刨沟", "开槽", "剔槽"],
    "洗漱台": ["洗脸盆", "洗手盆", "台盆", "面盆"],
    "跷板开关": ["暗开关", "翘板开关", "墙壁开关"],
    "翘板开关": ["暗开关", "跷板开关", "墙壁开关"],
    "贴膜": ["玻璃膜", "隔热膜", "防晒膜"],
    "脚手架搭拆": ["脚手架", "搭拆费"],
    "塑胶地板": ["塑料地板", "PVC地板", "橡胶地板"],
    "洗手台": ["洗脸盆", "洗手盆", "台盆"],
    "机柜": ["通信机柜", "配线箱", "配线架"],
    "大屏": ["显示屏", "LED屏"],
    "光纤": ["光缆", "光纤电缆"],
}


def _extract_manual_from_excel(excel_path: str) -> list[dict]:
    """从已审核Excel的待审核sheet提取人工审核项"""
    import openpyxl

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    # 找所有待审核sheet（可能有多个，如"待审核"和"待审核1"）
    review_sheets = [wb[sn] for sn in wb.sheetnames if "待审核" in sn]
    if not review_sheets:
        wb.close()
        return []

    # 找关键列（用第一个sheet的表头）
    ws0 = review_sheets[0]
    headers = [ws0.cell(1, c).value for c in range(1, ws0.max_column + 1)]

    # 找关键列
    col_map = {}
    for i, h in enumerate(headers):
        if h is None:
            continue
        h = str(h).strip()
        if "清单序号" in h:
            col_map["seq"] = i
        elif "清单名称" in h:
            col_map["name"] = i
        elif "项目特征" in h:
            col_map["desc"] = i
        elif "当前定额编号" in h:
            col_map["quota_id"] = i
        elif "当前定额名称" in h:
            col_map["quota_name"] = i
        elif "推荐度" in h:
            col_map["recommend"] = i
        elif "问题说明" in h or "审核说明" in h:
            col_map["note"] = i

    if "recommend" not in col_map:
        wb.close()
        return []

    # 从所有待审核sheet提取（去重，按清单名+定额编号）
    # 除了"推荐"（高置信度）之外，其他都要诊断
    items = []
    seen = set()
    for ws in review_sheets:
        for row in range(2, ws.max_row + 1):
            rec = str(ws.cell(row, col_map["recommend"] + 1).value or "")
            # 跳过高置信度推荐项（大概率正确的不用诊断）
            if "推荐" in rec:
                continue
            # 跳过空行
            if not rec or rec == "None":
                continue

            name = str(ws.cell(row, col_map.get("name", 0) + 1).value or "").strip()
            qid = str(ws.cell(row, col_map.get("quota_id", 0) + 1).value or "").strip()
            key = f"{name}|{qid}"
            if key in seen:
                continue
            seen.add(key)

            item = {
                "seq": ws.cell(row, col_map.get("seq", 0) + 1).value,
                "name": name,
                "desc": str(ws.cell(row, col_map.get("desc", 0) + 1).value or "").strip(),
                "quota_id": qid,
                "quota_name": str(ws.cell(row, col_map.get("quota_name", 0) + 1).value or "").strip(),
                "recommend": rec,
                "note": str(ws.cell(row, col_map.get("note", 0) + 1).value or "").strip(),
            }
            items.append(item)

    wb.close()
    return items


def _search_all_libraries(keywords: list[str], province: str,
                          sibling_provinces: list[str] | None) -> list[tuple]:
    """在主库+所有兄弟库搜索，返回所有命中结果"""
    from src.quota_search import search_quota_db

    all_results = []
    # 搜主库
    results = search_quota_db(keywords, province=province) or []
    for r in results[:5]:
        all_results.append((province, r[0], r[1]))

    # 搜兄弟库
    for sib in (sibling_provinces or []):
        results = search_quota_db(keywords, province=sib) or []
        for r in results[:5]:
            all_results.append((sib, r[0], r[1]))

    return all_results


def _try_synonym_variants(name: str, province: str,
                          sibling_provinces: list[str] | None) -> dict | None:
    """尝试同义变体替换，看能否搜到定额

    返回: {"original": "凿槽", "variant": "刨沟", "hit": ("03-4-13-16", "砖墙刨沟", "上海安装")}
    """
    for keyword, variants in SYNONYM_VARIANTS.items():
        if keyword not in name:
            continue
        for variant in variants:
            # 用变体词搜索
            results = _search_all_libraries([variant], province, sibling_provinces)
            if results:
                return {
                    "original": keyword,
                    "variant": variant,
                    "hit_province": results[0][0],
                    "hit_id": results[0][1],
                    "hit_name": results[0][2],
                }
    return None


def _diagnose_item(item: dict, province: str,
                   sibling_provinces: list[str] | None) -> dict:
    """诊断单条人工审核项的根因"""
    name = item["name"]
    result = {**item, "diagnosis": "", "detail": ""}

    # Step 1: 用清单名直接搜所有库
    hits = _search_all_libraries([name], province, sibling_provinces)
    if hits:
        # 有结果 → 排序偏差（搜到了但匹配阶段没排上来）
        result["diagnosis"] = "排序偏差"
        result["detail"] = f"候选: {hits[0][1]} {hits[0][2][:30]} ({hits[0][0][-6:]})"
        result["candidates"] = hits[:3]
        return result

    # Step 2: 尝试同义替换
    syn_hit = _try_synonym_variants(name, province, sibling_provinces)
    if syn_hit:
        result["diagnosis"] = "同义词缺口"
        result["detail"] = (
            f"清单叫\"{syn_hit['original']}\", "
            f"定额叫\"{syn_hit['variant']}\" "
            f"({syn_hit['hit_id']} {syn_hit['hit_name'][:25]})"
        )
        result["synonym_gap"] = syn_hit
        return result

    # Step 3: 无解
    result["diagnosis"] = "需人工"
    result["detail"] = "定额库中未找到对应项"
    return result


def diagnose(excel_path: str, province: str,
             sibling_provinces: list[str] | None = None) -> dict:
    """分析已审核Excel的人工项根因

    返回: {
        "total": 人工项总数,
        "synonym_gap": [同义词缺口条目],
        "ranking_miss": [排序偏差条目],
        "needs_manual": [需人工条目],
        "suggestions": [修复建议],
    }
    """
    # 自动获取兄弟库
    if sibling_provinces is None and province:
        from config import get_sibling_provinces
        sibling_provinces = get_sibling_provinces(province)

    # 提取人工项
    items = _extract_manual_from_excel(excel_path)
    if not items:
        print("未找到人工审核项。")
        return {"total": 0, "synonym_gap": [], "ranking_miss": [],
                "needs_manual": [], "suggestions": []}

    # 逐条诊断
    synonym_gap = []
    ranking_miss = []
    needs_manual = []

    for item in items:
        result = _diagnose_item(item, province, sibling_provinces)
        if result["diagnosis"] == "同义词缺口":
            synonym_gap.append(result)
        elif result["diagnosis"] == "排序偏差":
            ranking_miss.append(result)
        else:
            needs_manual.append(result)

    # 输出报告
    _print_report(items, synonym_gap, ranking_miss, needs_manual, excel_path)

    # 保存JSON
    output_dir = Path(excel_path).parent
    stem = Path(excel_path).stem[:40]
    json_path = output_dir / f"diagnosis_{stem}.json"
    report = {
        "total": len(items),
        "synonym_gap": [{k: v for k, v in r.items() if k != "candidates"} for r in synonym_gap],
        "ranking_miss": [{k: v for k, v in r.items() if k != "candidates"} for r in ranking_miss],
        "needs_manual": needs_manual,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n诊断JSON: {json_path}")

    return report


def _print_report(items, synonym_gap, ranking_miss, needs_manual, excel_path):
    """打印格式化诊断报告"""
    stem = Path(excel_path).stem[:30]
    print()
    print("=" * 60)
    print(f"诊断报告: {stem}")
    print(f"  诊断条目: {len(items)}条 (同义词缺口{len(synonym_gap)} "
          f"排序偏差{len(ranking_miss)} 需人工{len(needs_manual)})")
    print("=" * 60)

    if synonym_gap:
        print(f"\n■ 同义词缺口 ({len(synonym_gap)}条) — 加同义词可立即修复")
        # 按同义词对分组
        gap_groups: dict[str, list] = {}
        for r in synonym_gap:
            syn = r.get("synonym_gap", {})
            key = f"{syn.get('original', '')}→{syn.get('variant', '')}"
            gap_groups.setdefault(key, []).append(r)
        for key, group in gap_groups.items():
            count = f" x{len(group)}" if len(group) > 1 else ""
            detail = group[0]["detail"]
            print(f"  {group[0]['name']}{count}: {detail}")

    if ranking_miss:
        print(f"\n■ 排序偏差 ({len(ranking_miss)}条) — 正确定额在候选中但没排第一")
        for r in ranking_miss:
            rec = r.get("recommend", "")
            print(f"  [{rec[:8]}] {r['name'][:20]}: {r['detail']}")

    if needs_manual:
        print(f"\n■ 需人工 ({len(needs_manual)}条) — 系统暂无法处理")
        for r in needs_manual:
            rec = r.get("recommend", "")
            desc_short = r.get("desc", "")[:40]
            print(f"  [{rec[:8]}] {r['name'][:25]}")
            print(f"    特征: {desc_short}")
            print(f"    当前: {r['quota_id']} {r['quota_name'][:30]}")

    # 建议
    if synonym_gap:
        pairs = set()
        for r in synonym_gap:
            syn = r.get("synonym_gap", {})
            if syn:
                pairs.add((syn["original"], syn["variant"]))
        affected = len(synonym_gap)
        print(f"\n建议: 加{len(pairs)}对同义词可消除{affected}条人工项")
        for orig, var in pairs:
            print(f"  {orig} → {var}")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Jarvis 诊断工具：分析人工审核项的根因",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  python tools/jarvis_diagnose.py "output/匹配结果_xxx_已审核.xlsx" --province "上海建筑"
""",
    )
    parser.add_argument("excel_path", help="已审核Excel文件路径")
    parser.add_argument("--province", required=True, help="主定额库名称")
    args = parser.parse_args()

    if not os.path.exists(args.excel_path):
        print(f"错误：文件不存在 {args.excel_path}")
        sys.exit(1)

    # 解析省份
    from config import resolve_province
    try:
        province = resolve_province(args.province)
    except Exception as e:
        print(f"错误：省份解析失败 - {e}")
        sys.exit(1)

    diagnose(args.excel_path, province)


if __name__ == "__main__":
    main()
