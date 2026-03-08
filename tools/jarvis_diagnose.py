# -*- coding: utf-8 -*-
"""
Jarvis 诊断工具 - 分析人工审核项的根因，可选自动修复

跑完Jarvis流水线后，自动分析"待人工"条目为什么匹配失败：
  - 同义词缺口：清单叫"凿槽"，定额库叫"刨沟"
  - 排序偏差：正确定额在候选里但没排第一
  - 需人工：定额库确实没有对应项

--fix 模式下会自动：
  1. 跨省验证同义词候选（>=2省能搜到才通过）
  2. 写入 engineering_synonyms.json
  3. 跑 benchmark 回归检查（退化则自动回滚）

用法：
    python tools/jarvis_diagnose.py "output/xxx_已审核.xlsx" --province "上海建筑"
    python tools/jarvis_diagnose.py "output/xxx_已审核.xlsx" --province "上海建筑" --fix
"""

import sys
import os
import json
import shutil
import argparse
import subprocess
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


# ---- 自动修复相关函数（--fix 模式）----

# 跨省验证用的代表省份（建筑+安装各4个，覆盖南北东西）
# 同义词可能在建筑库也可能在安装库，两种都要搜
VERIFY_PROVINCES_RAW = [
    "北京2024",        # 北京（建筑+安装合一）
    "广东房屋",        # 广东建筑
    "广东通用安装",    # 广东安装
    "江西房屋",        # 江西建筑
    "江西安装",        # 江西安装
    "浙江房屋",        # 浙江建筑
    "浙江安装",        # 浙江安装
]


def _cross_province_verify(variant: str) -> tuple[int, list[str]]:
    """验证目标词在几个代表省能搜到

    按省去重统计（同省建筑+安装算1个省），>=2省命中即通过。
    返回: (命中省数, 命中省份列表)
    """
    from src.quota_search import search_quota_db
    from config import resolve_province

    # 解析省份全名（只在首次调用时解析，缓存在函数属性上）
    if not hasattr(_cross_province_verify, "_resolved"):
        resolved = []
        for raw in VERIFY_PROVINCES_RAW:
            try:
                full = resolve_province(raw)
                resolved.append((raw, full))
            except Exception:
                pass
        _cross_province_verify._resolved = resolved

    # 搜索并按省去重（"广东房屋"和"广东通用安装"都算广东）
    hit_provinces = set()  # 用省份简称去重
    hit_details = []
    for raw, full in _cross_province_verify._resolved:
        try:
            results = search_quota_db([variant], province=full)
            if results:
                # 提取省份简称（取前2个汉字，如"北京""广东""江西"）
                short = raw[:2]
                hit_provinces.add(short)
                hit_details.append(raw)
        except Exception:
            pass
    return len(hit_provinces), hit_details


def _write_synonyms(pairs: list[tuple[str, str]]) -> int:
    """安全写入同义词到 engineering_synonyms.json

    规则：
    - 已有的key不覆盖
    - 按key长度降序排序（保护伞机制）
    - 写入前先备份

    返回: 实际写入的同义词对数
    """
    syn_path = PROJECT_ROOT / "data" / "engineering_synonyms.json"
    bak_path = syn_path.with_suffix(".json.bak")

    # 读取现有同义词表
    with open(syn_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 备份（覆盖上次备份）
    shutil.copy2(syn_path, bak_path)

    added = 0
    for original, variant in pairs:
        if original in data:
            continue  # 已存在，不覆盖
        data[original] = [variant]
        added += 1

    if added == 0:
        return 0

    # _specialty_scope排序特殊处理：meta字段放最前，普通词按长度降序
    sorted_data = dict(sorted(
        data.items(),
        key=lambda x: (not x[0].startswith("_"), len(x[0])),
        reverse=True,
    ))

    with open(syn_path, "w", encoding="utf-8") as f:
        json.dump(sorted_data, f, ensure_ascii=False, indent=2)

    return added


def _run_benchmark_check() -> tuple[bool, str]:
    """跑 benchmark 对比基线，检查有没有退化

    返回: (是否通过, 摘要文本)
    """
    try:
        result = subprocess.run(
            [sys.executable, "tools/run_benchmark.py", "--compare"],
            capture_output=True, text=True, timeout=600,
            cwd=str(PROJECT_ROOT),
        )
        output = result.stdout + result.stderr

        # 从输出中提取命中率变化（格式如 "命中率: 38.9% → 39.2% (▲+0.3%)"）
        for line in output.split("\n"):
            if "命中率" in line or "hit_rate" in line:
                # 找到命中率行，检查是否退化
                if "▼" in line or "退化" in line or "下降" in line:
                    return False, line.strip()
                return True, line.strip()

        # 没找到命中率行，看退出码
        if result.returncode == 0:
            return True, "benchmark通过（未解析到具体命中率）"
        return False, f"benchmark异常（退出码{result.returncode}）"
    except subprocess.TimeoutExpired:
        return False, "benchmark超时（>10分钟）"
    except Exception as e:
        return False, f"benchmark执行失败: {e}"


def _rollback_synonyms() -> bool:
    """从备份回滚同义词表"""
    syn_path = PROJECT_ROOT / "data" / "engineering_synonyms.json"
    bak_path = syn_path.with_suffix(".json.bak")
    if bak_path.exists():
        shutil.copy2(bak_path, syn_path)
        return True
    return False


def auto_fix_synonyms(synonym_gap_items: list[dict], skip_benchmark: bool = False) -> dict:
    """自动修复同义词缺口（诊断 → 跨省验证 → 写入 → benchmark检查）

    参数:
        synonym_gap_items: 诊断出的同义词缺口条目列表
        skip_benchmark: 跳过benchmark（调试用）

    返回: {"verified": 通过验证数, "written": 写入数, "benchmark_ok": bool, "details": [...]}
    """
    if not synonym_gap_items:
        return {"verified": 0, "written": 0, "benchmark_ok": True, "details": []}

    # 提取去重的同义词对（清单词→定额词）
    pairs: dict[str, str] = {}  # original → variant（去重）
    for item in synonym_gap_items:
        syn = item.get("synonym_gap", {})
        if syn and syn.get("original") and syn.get("variant"):
            orig = syn["original"]
            if orig not in pairs:
                pairs[orig] = syn["variant"]

    if not pairs:
        return {"verified": 0, "written": 0, "benchmark_ok": True, "details": []}

    # ---- 跨省验证 ----
    print()
    print("=" * 60)
    print(f"跨省验证同义词（{len(pairs)}对）")
    print("=" * 60)

    verified_pairs = []  # 通过验证的同义词对
    details = []

    for original, variant in pairs.items():
        hit_count, hit_details = _cross_province_verify(variant)
        status = "通过" if hit_count >= 2 else "跳过（覆盖率不足）"
        detail_str = ", ".join(hit_details) if hit_details else "无命中"
        print(f"  {original}→{variant}: {hit_count}省命中 [{detail_str}] → {status}")

        detail = {
            "original": original, "variant": variant,
            "hit_count": hit_count, "passed": hit_count >= 2,
        }
        details.append(detail)

        if hit_count >= 2:
            verified_pairs.append((original, variant))

    if not verified_pairs:
        print("\n  无同义词通过跨省验证，跳过写入")
        return {"verified": 0, "written": 0, "benchmark_ok": True, "details": details}

    # ---- 写入同义词表 ----
    print()
    print("=" * 60)
    print(f"写入同义词表（{len(verified_pairs)}对通过验证）")
    print("=" * 60)

    written = _write_synonyms(verified_pairs)
    if written > 0:
        print(f"  已写入 {written} 对同义词到 engineering_synonyms.json")
        # 清除同义词缓存（让下次搜索用新同义词）
        try:
            from src.query_builder import _SYNONYMS_CACHE
            import src.query_builder as qb
            qb._SYNONYMS_CACHE = None
        except Exception:
            pass
    else:
        print("  所有同义词已存在，无需写入")
        return {"verified": len(verified_pairs), "written": 0,
                "benchmark_ok": True, "details": details}

    # ---- Benchmark回归检查 ----
    if skip_benchmark:
        print("\n  跳过benchmark检查（--skip-benchmark）")
        return {"verified": len(verified_pairs), "written": written,
                "benchmark_ok": True, "details": details}

    print()
    print("=" * 60)
    print("Benchmark回归检查")
    print("=" * 60)

    benchmark_ok, benchmark_summary = _run_benchmark_check()
    print(f"  {benchmark_summary}")

    if not benchmark_ok:
        # 退化了，回滚！
        print("  命中率退化，回滚同义词表...")
        if _rollback_synonyms():
            print("  已回滚到修改前的同义词表")
        else:
            print("  警告：回滚失败，请手动检查 engineering_synonyms.json")
        return {"verified": len(verified_pairs), "written": written,
                "benchmark_ok": False, "details": details}

    print("  验证通过，同义词已生效")
    return {"verified": len(verified_pairs), "written": written,
            "benchmark_ok": True, "details": details}


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
             sibling_provinces: list[str] | None = None,
             auto_fix: bool = False,
             skip_benchmark: bool = False) -> dict:
    """分析已审核Excel的人工项根因，可选自动修复同义词缺口

    参数:
        auto_fix: 自动修复模式（跨省验证→写入同义词→benchmark检查）
        skip_benchmark: 跳过benchmark回归检查（调试用）

    返回: {
        "total": 人工项总数,
        "synonym_gap": [同义词缺口条目],
        "ranking_miss": [排序偏差条目],
        "needs_manual": [需人工条目],
        "fix_result": 自动修复结果(仅auto_fix=True时),
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

    # ---- 自动修复同义词缺口（--fix 模式）----
    if auto_fix and synonym_gap:
        fix_result = auto_fix_synonyms(synonym_gap, skip_benchmark=skip_benchmark)
        report["fix_result"] = fix_result

    # ---- 写入 Obsidian 诊断报告 ----
    try:
        _write_ob_report(stem, province, report, synonym_gap, ranking_miss, needs_manual)
    except Exception as e:
        print(f"  OB报告写入跳过: {e}")

    return report


def _write_ob_report(stem: str, province: str, report: dict,
                     synonym_gap: list, ranking_miss: list, needs_manual: list):
    """将诊断报告写入 Obsidian 笔记（自动生成，用户可直接查看）"""
    from datetime import datetime

    ob_dir = Path(r"D:\Obsidian\工程造价\系统更新\诊断报告")
    if not ob_dir.exists():
        return

    today = datetime.now().strftime("%Y-%m-%d")
    # 项目名从stem提取（去掉"匹配结果_"前缀和时间戳后缀）
    project_name = stem.replace("匹配结果_", "").split("_20")[0][:20]
    ob_path = ob_dir / f"{today} 诊断报告-{project_name}.md"

    lines = [
        f"---",
        f"topic: Jarvis诊断报告",
        f"project: {project_name}",
        f"province: {province}",
        f"date: {today}",
        f"---",
        f"# 诊断报告: {project_name}",
        f"",
        f"**省份**: {province}  ",
        f"**诊断条目**: {report['total']}条  ",
        f"**分布**: 同义词缺口{len(synonym_gap)} | 排序偏差{len(ranking_miss)} | 需人工{len(needs_manual)}",
        f"",
    ]

    # 同义词缺口
    if synonym_gap:
        lines.append(f"## 同义词缺口（{len(synonym_gap)}条）")
        lines.append(f"加同义词可立即修复。")
        lines.append(f"")
        lines.append(f"| 清单名称 | 清单叫法 | 定额叫法 | 命中定额 |")
        lines.append(f"|----------|----------|----------|----------|")
        seen_pairs = set()
        for r in synonym_gap:
            syn = r.get("synonym_gap", {})
            pair_key = f"{syn.get('original', '')}→{syn.get('variant', '')}"
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            lines.append(
                f"| {r['name'][:20]} | {syn.get('original', '')} "
                f"| {syn.get('variant', '')} "
                f"| {syn.get('hit_id', '')} {syn.get('hit_name', '')[:20]} |"
            )
        lines.append(f"")

        # 自动修复结果
        fix = report.get("fix_result")
        if fix:
            lines.append(f"### 自动修复结果")
            lines.append(f"- 通过验证: {fix['verified']}对")
            lines.append(f"- 实际写入: {fix['written']}对")
            lines.append(f"- Benchmark: {'通过' if fix['benchmark_ok'] else '退化（已回滚）'}")
            lines.append(f"")

    # 排序偏差
    if ranking_miss:
        lines.append(f"## 排序偏差（{len(ranking_miss)}条）")
        lines.append(f"正确定额在候选中但没排第一，需要改进排序算法。")
        lines.append(f"")
        for r in ranking_miss[:10]:  # 只显示前10条，太多了不方便看
            lines.append(f"- **{r['name'][:25]}**: {r['detail']}")
        if len(ranking_miss) > 10:
            lines.append(f"- ...还有{len(ranking_miss) - 10}条")
        lines.append(f"")

    # 需人工
    if needs_manual:
        lines.append(f"## 需人工（{len(needs_manual)}条）")
        lines.append(f"定额库中未找到对应项，需要人工判断。")
        lines.append(f"")
        for r in needs_manual[:15]:  # 只显示前15条
            desc = r.get("desc", "")[:40]
            lines.append(f"- **{r['name'][:25]}**")
            if desc:
                lines.append(f"  - 特征: {desc}")
            if r.get("quota_id"):
                lines.append(f"  - 当前: {r['quota_id']} {r['quota_name'][:25]}")
        if len(needs_manual) > 15:
            lines.append(f"- ...还有{len(needs_manual) - 15}条")
        lines.append(f"")

    with open(ob_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  OB报告: {ob_path}")


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
        description="Jarvis 诊断工具：分析人工审核项的根因，可选自动修复",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  python tools/jarvis_diagnose.py "output/xxx_已审核.xlsx" --province "上海建筑"
  python tools/jarvis_diagnose.py "output/xxx_已审核.xlsx" --province "上海建筑" --fix
  python tools/jarvis_diagnose.py "output/xxx_已审核.xlsx" --province "上海建筑" --fix --skip-benchmark
""",
    )
    parser.add_argument("excel_path", help="已审核Excel文件路径")
    parser.add_argument("--province", required=True, help="主定额库名称")
    parser.add_argument("--fix", action="store_true",
                        help="自动修复：跨省验证同义词→写入→benchmark回归检查")
    parser.add_argument("--skip-benchmark", action="store_true",
                        help="跳过benchmark回归检查（调试用）")
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

    diagnose(args.excel_path, province, auto_fix=args.fix,
             skip_benchmark=args.skip_benchmark)


if __name__ == "__main__":
    main()
