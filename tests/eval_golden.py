"""
黄金集评测工具 —— 导入纠正 + 回归测试，一个脚本搞定

============================================================
这是什么？
============================================================
这个脚本做两件事：
1. 导入你的正确答案（从广联达改好的Excel） → 存入经验库 + 黄金集文件
2. 跑评测（用你的正确答案测试系统准确率） → 出报告 + 检测退步

============================================================
怎么用？（三步走）
============================================================

第一步：自动匹配 → 导出Excel
    python main.py 清单文件.xlsx --mode search
    （生成 output/匹配结果_xxx.xlsx）

第二步：你在广联达里改对定额 → 保存为新Excel

第三步：把改好的喂给系统
    python tests/eval_golden.py --import 改好的文件.xlsx
    （自动对比、学习、存入黄金集）

以后改了代码想看有没有退步：
    python tests/eval_golden.py
    （跑全部黄金集，出准确率报告）

============================================================
Excel格式要求（和广联达导出格式一样）
============================================================
  A列=序号(数字)  B列=项目编码   C列=项目名称  D列=特征描述  E列=单位  F列=工程量
  A列=空          B列=定额编号(C开头)  C列=定额名称  ...

就是标准的"清单行+定额行交替"格式，广联达导出的和我们系统导出的都是这个格式。
"""

import argparse
import json
import os
import sys
import time
import tempfile
from pathlib import Path
from datetime import datetime

import openpyxl
from loguru import logger

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from src.text_parser import parser as text_parser
from src.hybrid_searcher import HybridSearcher
from src.param_validator import ParamValidator

# 黄金集存储路径
GOLDEN_FILE = PROJECT_ROOT / "tests" / "golden_cases.json"
# 上一次评测结果存储路径（用于对比退步）
LAST_RESULT_FILE = PROJECT_ROOT / "tests" / "last_eval_result.json"


def _atomic_write_json(path: Path, payload):
    """原子写JSON文件，避免中断时留下损坏内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".json",
            prefix=f"{path.stem}_tmp_",
            dir=str(path.parent),
            encoding="utf-8",
            delete=False,
        ) as tf:
            tmp_path = tf.name
            json.dump(payload, tf, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    finally:
        if tmp_path and Path(tmp_path).exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ================================================================
# 第1部分：从Excel导入黄金集
# ================================================================

def read_excel_pairs(excel_path: str) -> list[dict]:
    """
    从Excel中读取 清单→正确定额 的对应关系

    支持格式：
    - 广联达导出格式（清单行+定额行交替）
    - 我们系统的输出格式（同样是清单行+定额行交替）
    - 小栗AI格式（A列标记"清单"或"定额"）

    返回:
        [{"bill_text": "...", "bill_name": "...", "bill_code": "...",
          "bill_description": "...", "bill_unit": "...",
          "correct_quota_ids": ["C8-2-40", ...],
          "correct_quota_names": ["...", ...]}, ...]
    """
    path = Path(excel_path)
    if not path.exists():
        logger.error(f"文件不存在: {path}")
        return []

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    all_pairs = []

    for ws in wb.worksheets:
        pairs = _read_sheet_pairs(ws)
        if pairs:
            all_pairs.extend(pairs)
            logger.info(f"  Sheet [{ws.title}]: 读取 {len(pairs)} 条清单→定额对")

    wb.close()

    if not all_pairs:
        logger.warning("未从Excel中识别到任何清单→定额对应关系，请检查文件格式")

    return all_pairs


def _read_sheet_pairs(ws) -> list[dict]:
    """从单个Sheet中读取清单→定额对"""
    pairs = []
    current_bill = None
    current_quota_ids = []
    current_quota_names = []

    for row_idx, row in enumerate(ws.iter_rows(values_only=True), 1):
        if not row or not any(row):
            continue

        a = row[0] if len(row) > 0 else None  # 序号
        b = row[1] if len(row) > 1 else None  # 编码/定额编号
        c = row[2] if len(row) > 2 else None  # 名称
        d = row[3] if len(row) > 3 else None  # 特征描述
        e = row[4] if len(row) > 4 else None  # 单位

        # 跳过表头行（包含"序号""项目编码"等关键词的行）
        if c and any(kw in str(c) for kw in ["项目名称", "名称", "清单名称"]):
            continue
        if a and any(kw in str(a) for kw in ["序号"]):
            continue

        # 判断行类型
        a_str = str(a).strip() if a else ""
        b_str = str(b).strip() if b else ""

        is_bill_row = _is_bill_serial(a) and c  # A列是序号 + C列有内容
        is_quota_row = (not a_str or a_str == "None") and b_str and _looks_like_quota_id(b_str)

        if is_bill_row:
            # 保存上一条
            if current_bill and current_quota_ids:
                pairs.append(_make_pair(current_bill, current_quota_ids, current_quota_names))

            current_bill = {
                "code": b_str,
                "name": str(c).strip() if c else "",
                "description": str(d).strip() if d else "",
                "unit": str(e).strip() if e else "",
            }
            current_quota_ids = []
            current_quota_names = []

        elif is_quota_row and current_bill:
            # 清洗定额编号（去掉"换"后缀、多余空格等）
            clean_id = b_str.split()[0].rstrip("换").strip()
            current_quota_ids.append(clean_id)
            quota_name = str(c).strip() if c else ""
            current_quota_names.append(quota_name)

    # 最后一条
    if current_bill and current_quota_ids:
        pairs.append(_make_pair(current_bill, current_quota_ids, current_quota_names))

    return pairs


def _is_bill_serial(value) -> bool:
    """识别清单序号，兼容 1 / 1.0 / "2.0" / "03"。"""
    if value is None:
        return False
    if isinstance(value, int):
        return value >= 0
    if isinstance(value, float):
        return value.is_integer() and value >= 0
    text = str(value).strip()
    if not text:
        return False
    if text.isdigit():
        return True
    if text.endswith(".0"):
        body = text[:-2].strip()
        return body.isdigit()
    return False


def _looks_like_quota_id(text: str) -> bool:
    """判断文本是否像定额编号（如 C8-2-40, C10-3-120, 01-02-003）"""
    import re
    text = text.split()[0].strip()  # 取第一个词
    # C开头+数字+短横线 或 纯数字+短横线
    return bool(re.match(r'^[A-Za-z]?\d+[-]', text))


def _make_pair(bill: dict, quota_ids: list, quota_names: list) -> dict:
    """构造一条黄金集记录"""
    name = bill.get("name", "")
    desc = bill.get("description", "")
    bill_text = f"{name} {desc}".strip()

    return {
        "bill_text": bill_text,
        "bill_name": name,
        "bill_code": bill.get("code", ""),
        "bill_description": desc,
        "bill_unit": bill.get("unit", ""),
        "correct_quota_ids": quota_ids,
        "correct_quota_names": quota_names,
    }


def import_golden(excel_path: str, save_to_exp: bool = True):
    """
    从Excel导入黄金集：存入JSON文件 + 存入经验库

    参数:
        excel_path: 修正后的Excel文件路径
        save_to_exp: 是否同时存入经验库（默认True）
    """
    logger.info(f"从Excel导入黄金集: {excel_path}")
    new_pairs = read_excel_pairs(excel_path)

    if not new_pairs:
        logger.error("未读取到任何数据，导入终止")
        return

    # 加载已有的黄金集（追加模式，不覆盖之前的）
    existing = load_golden_cases()
    existing_texts = {c["bill_text"] for c in existing}

    added = 0
    updated = 0
    for pair in new_pairs:
        if pair["bill_text"] in existing_texts:
            # 已有相同清单文本 → 更新正确答案
            for i, e in enumerate(existing):
                if e["bill_text"] == pair["bill_text"]:
                    existing[i]["correct_quota_ids"] = pair["correct_quota_ids"]
                    existing[i]["correct_quota_names"] = pair["correct_quota_names"]
                    existing[i]["updated_at"] = datetime.now().isoformat()
                    updated += 1
                    break
        else:
            # 新条目
            pair["imported_at"] = datetime.now().isoformat()
            pair["source_file"] = Path(excel_path).name
            existing.append(pair)
            added += 1

    # 保存黄金集JSON
    save_golden_cases(existing)
    logger.info(f"黄金集已更新: 新增 {added} 条, 更新 {updated} 条, 总计 {len(existing)} 条")
    logger.info(f"  保存到: {GOLDEN_FILE}")

    # 同时存入经验库
    if save_to_exp:
        exp_saved = _save_pairs_to_experience(new_pairs)
        logger.info(f"  存入经验库: {exp_saved} 条")

    print(f"\n导入完成！")
    print(f"  新增: {added} 条")
    print(f"  更新: {updated} 条")
    print(f"  黄金集总计: {len(existing)} 条")
    print(f"  文件: {GOLDEN_FILE}")


def _save_pairs_to_experience(pairs: list[dict]) -> int:
    """将黄金集对存入经验库"""
    try:
        from src.experience_db import ExperienceDB
        exp_db = ExperienceDB()
    except Exception as e:
        logger.warning(f"经验库加载失败: {e}")
        return 0

    saved = 0
    failed = 0
    for pair in pairs:
        if not pair["correct_quota_ids"]:
            continue
        try:
            from src.text_parser import normalize_bill_text
            bill_text = normalize_bill_text(
                pair.get("bill_name", ""), pair.get("bill_description", ""))
            if not bill_text:
                bill_text = pair["bill_text"]  # 兜底用原始文本
            record_id = exp_db.add_experience(
                bill_text=bill_text,
                quota_ids=pair["correct_quota_ids"],
                quota_names=pair.get("correct_quota_names", []),
                bill_name=pair.get("bill_name"),
                bill_code=pair.get("bill_code"),
                bill_unit=pair.get("bill_unit"),
                source="user_correction",  # 用户修正的，最高信任
                confidence=95,
            )
            if record_id > 0:
                saved += 1
        except Exception as e:
            failed += 1
            logger.warning(f"黄金集写入经验库失败: {pair.get('bill_name', '')[:30]} ({e})")

    if failed > 0:
        logger.warning(f"黄金集写入有失败: 成功{saved} 失败{failed}")

    return saved


# ================================================================
# 第2部分：黄金集存取
# ================================================================

def load_golden_cases() -> list[dict]:
    """加载黄金集（从JSON文件）"""
    if not GOLDEN_FILE.exists():
        return []
    try:
        with open(GOLDEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
            logger.warning("黄金集文件格式异常（应为list），已降级为空")
            return []
    except Exception as e:
        logger.warning(f"黄金集文件读取失败，已降级为空: {e}")
        return []


def save_golden_cases(cases: list[dict]):
    """保存黄金集到JSON文件"""
    _atomic_write_json(GOLDEN_FILE, cases)


# ================================================================
# 第3部分：评测（跑匹配 + 对比正确答案 + 出报告）
# ================================================================

def run_evaluation(compare_last: bool = False):
    """
    跑黄金集评测：对每条黄金集记录执行搜索+参数验证，
    检查正确答案是否在候选列表中，生成准确率报告。
    """
    cases = load_golden_cases()
    if not cases:
        print("黄金集为空！请先导入：python tests/eval_golden.py --import 你的文件.xlsx")
        return

    logger.info(f"开始评测: {len(cases)} 条黄金集")

    # 初始化搜索引擎
    searcher = HybridSearcher()
    validator = ParamValidator()

    status = searcher.get_status()
    logger.info(f"  BM25: {status['bm25_count']} 条, 向量: {status['vector_count']} 条")

    # 逐条评测
    results = []
    start_time = time.time()

    for i, case in enumerate(cases, 1):
        bill_text = case["bill_text"]
        bill_name = case.get("bill_name", "")
        bill_desc = case.get("bill_description", "")
        correct_ids = case["correct_quota_ids"]
        correct_main = correct_ids[0] if correct_ids else ""

        # 构建搜索query（和main.py里的逻辑一致）
        search_query = text_parser.build_quota_query(bill_name, bill_desc)
        full_query = bill_text

        # 搜索
        candidates = searcher.search(search_query, top_k=20)

        # 参数验证
        if candidates:
            candidates = validator.validate_candidates(full_query, candidates)

        # 分析结果
        eval_result = _analyze_case(
            case, candidates, correct_main, correct_ids, search_query
        )
        results.append(eval_result)

        if i % 20 == 0:
            logger.info(f"  进度: {i}/{len(cases)}")

    elapsed = time.time() - start_time

    # 生成报告
    report = _generate_report(results, elapsed)
    print(report)

    # 和上次结果对比
    if compare_last:
        _compare_with_last(results)

    # 保存本次结果（供下次对比用）
    _save_eval_results(results)


def _analyze_case(case: dict, candidates: list, correct_main: str,
                  correct_ids: list, search_query: str) -> dict:
    """分析单条评测结果"""
    bill_text = case["bill_text"]
    bill_name = case.get("bill_name", "")

    result = {
        "bill_text": bill_text,
        "bill_name": bill_name,
        "search_query": search_query,
        "correct_main": correct_main,
        "correct_all": correct_ids,
    }

    if not candidates:
        result["hit_rank"] = -1  # -1 表示搜索无结果
        result["system_quota_id"] = ""
        result["verdict"] = "miss"
        result["detail"] = "搜索无结果"
        return result

    # 查找正确答案在候选列表中的排名
    all_candidate_ids = [c.get("quota_id", "") for c in candidates]
    matched_candidates = [c for c in candidates if c.get("param_match", True)]
    matched_ids = [c.get("quota_id", "") for c in matched_candidates]

    # 取系统的Top1（和实际匹配逻辑一致：优先param_match的）
    if matched_candidates:
        system_top = matched_candidates[0]
    elif candidates:
        system_top = candidates[0]
    else:
        system_top = None

    system_id = system_top.get("quota_id", "") if system_top else ""
    result["system_quota_id"] = system_id

    # 正确答案在全部候选中的排名（hit@N 的 N）
    if correct_main in all_candidate_ids:
        rank_in_all = all_candidate_ids.index(correct_main) + 1
    else:
        rank_in_all = -1

    # 正确答案在param_match候选中的排名
    if correct_main in matched_ids:
        rank_in_matched = matched_ids.index(correct_main) + 1
    else:
        rank_in_matched = -1

    result["hit_rank_all"] = rank_in_all      # 在全部候选中的排名
    result["hit_rank_matched"] = rank_in_matched  # 在param_match候选中的排名

    # 判断结果类型
    if system_id == correct_main:
        result["verdict"] = "exact"  # Top1就是正确答案
        result["hit_rank"] = 1
        result["detail"] = "精确匹配"
    elif system_id and correct_main and system_id.rsplit("-", 1)[0] == correct_main.rsplit("-", 1)[0]:
        result["verdict"] = "near"  # 同一小节（近似匹配）
        result["hit_rank"] = rank_in_all
        result["detail"] = f"近似(同节): 系统={system_id}, 正确={correct_main}"
    elif rank_in_all > 0 and rank_in_all <= 5:
        result["verdict"] = "hit5"  # 正确答案在前5但没排第1
        result["hit_rank"] = rank_in_all
        result["detail"] = f"正确答案在第{rank_in_all}名（Reranker可挽救）"
    elif rank_in_all > 5:
        result["verdict"] = "hit20"  # 正确答案在前20但排在后面
        result["hit_rank"] = rank_in_all
        result["detail"] = f"正确答案在第{rank_in_all}名（排序问题）"
    else:
        result["verdict"] = "miss"  # 正确答案不在候选中
        result["hit_rank"] = -1
        result["detail"] = "正确答案未召回"

    # 检查是否被参数验证误杀
    if rank_in_all > 0 and rank_in_matched < 0:
        result["param_killed"] = True
        result["detail"] += " [被参数验证排除!]"
    else:
        result["param_killed"] = False

    # 记录系统Top1的参数验证详情（方便调试）
    if system_top:
        result["system_param_detail"] = system_top.get("param_detail", "")
        result["system_param_match"] = system_top.get("param_match", True)

    return result


def _generate_report(results: list[dict], elapsed: float) -> str:
    """生成评测报告"""
    total = len(results)
    if total == 0:
        return "黄金集为空，无法评测"

    # 统计各类结果
    exact = sum(1 for r in results if r["verdict"] == "exact")
    near = sum(1 for r in results if r["verdict"] == "near")
    hit5 = sum(1 for r in results if r["verdict"] == "hit5")
    hit20 = sum(1 for r in results if r["verdict"] == "hit20")
    miss = sum(1 for r in results if r["verdict"] == "miss")
    param_killed = sum(1 for r in results if r.get("param_killed", False))

    pct = lambda n: f"{n * 100 // total}%"

    lines = []
    lines.append("")
    lines.append("=" * 65)
    lines.append(f"  黄金集评测报告  ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    lines.append("=" * 65)
    lines.append(f"  测试条目:     {total}")
    lines.append(f"  耗时:         {elapsed:.1f}秒")
    lines.append("-" * 65)
    lines.append(f"  精确匹配(Top1正确):  {exact:4d}  ({pct(exact)})")
    lines.append(f"  近似匹配(同小节):    {near:4d}  ({pct(near)})")
    lines.append(f"  Top5内有正确答案:    {hit5:4d}  ({pct(hit5)})")
    lines.append(f"  Top20内有正确答案:   {hit20:4d}  ({pct(hit20)})")
    lines.append(f"  未召回(正确答案不在候选):  {miss:4d}  ({pct(miss)})")
    lines.append("-" * 65)
    lines.append(f"  准确率(精确+近似):   {pct(exact + near)}")
    lines.append(f"  召回率(Top20命中):   {pct(exact + near + hit5 + hit20)}")
    lines.append(f"  参数验证误杀:        {param_killed:4d}  ({pct(param_killed)})")
    lines.append("=" * 65)

    # 诊断建议
    lines.append("\n诊断建议:")
    if miss > total * 0.1:
        lines.append(f"  ⚠ 未召回率 {pct(miss)} 偏高 → 需要改进搜索/分词/向量模型")
    if hit5 > total * 0.1:
        lines.append(f"  ⚠ {hit5}条正确答案在Top5但未排第1 → 加Reranker可提升 ~{pct(hit5)}")
    if param_killed > total * 0.03:
        lines.append(f"  ⚠ 参数验证误杀 {param_killed}条 → ParamValidator需要从硬排除改为降权")
    if exact + near >= total * 0.85:
        lines.append(f"  ✓ 准确率 {pct(exact + near)} 达标（目标85%+）")

    # 列出所有错误项的详细信息
    errors = [r for r in results if r["verdict"] not in ("exact",)]
    if errors:
        lines.append(f"\n{'—' * 65}")
        lines.append(f"错误/非精确项明细（{len(errors)}条）:")
        lines.append(f"{'—' * 65}")
        for r in errors:
            lines.append(f"  [{r['verdict']:5s}] {r['bill_name'][:35]}")
            lines.append(f"         正确={r['correct_main']:15s}  系统={r['system_quota_id']:15s}")
            lines.append(f"         {r['detail']}")

    return "\n".join(lines)


def _save_eval_results(results: list[dict]):
    """保存本次评测结果（供下次对比用）"""
    save_data = {
        "eval_time": datetime.now().isoformat(),
        "total": len(results),
        "results": [{
            "bill_text": r["bill_text"],
            "correct_main": r["correct_main"],
            "system_quota_id": r["system_quota_id"],
            "verdict": r["verdict"],
            "hit_rank": r.get("hit_rank", -1),
            "param_killed": r.get("param_killed", False),
        } for r in results],
    }

    _atomic_write_json(LAST_RESULT_FILE, save_data)

    logger.info(f"评测结果已保存: {LAST_RESULT_FILE}")


def _compare_with_last(current_results: list[dict]):
    """和上次评测结果对比，找出退步项"""
    if not LAST_RESULT_FILE.exists():
        print("\n没有上次的评测结果，无法对比。下次再运行 --compare-last 就有了。")
        return

    try:
        with open(LAST_RESULT_FILE, "r", encoding="utf-8") as f:
            last_data = json.load(f)
    except Exception as e:
        print(f"\n上次的评测结果文件损坏，跳过对比: {e}")
        return

    if not isinstance(last_data, dict):
        print("\n上次的评测结果格式错误（根节点非对象），跳过对比。")
        return
    last_rows = last_data.get("results", [])
    if not isinstance(last_rows, list):
        print("\n上次的评测结果格式错误（results 非数组），跳过对比。")
        return

    last_results = {}
    for r in last_rows:
        if not isinstance(r, dict):
            continue
        bt = str(r.get("bill_text", "")).strip()
        if bt:
            last_results[bt] = r
    if not last_results:
        print("\n上次评测结果中无有效条目，跳过对比。")
        return

    regressions = []  # 退步项（上次对，这次错）
    improvements = []  # 进步项（上次错，这次对）

    for curr in current_results:
        bt = curr["bill_text"]
        if bt not in last_results:
            continue

        last = last_results[bt]
        last_ok = last["verdict"] in ("exact", "near")
        curr_ok = curr["verdict"] in ("exact", "near")

        if last_ok and not curr_ok:
            regressions.append({
                "bill_name": curr.get("bill_name", ""),
                "correct": curr["correct_main"],
                "last_system": last["system_quota_id"],
                "now_system": curr["system_quota_id"],
                "now_verdict": curr["verdict"],
            })
        elif not last_ok and curr_ok:
            improvements.append({
                "bill_name": curr.get("bill_name", ""),
                "correct": curr["correct_main"],
            })

    print(f"\n{'=' * 65}")
    print(f"  与上次对比 ({last_data.get('eval_time', '?')[:16]})")
    print(f"{'=' * 65}")

    if regressions:
        print(f"\n  ⚠ 退步项（{len(regressions)}条，上次对/这次错）:")
        for r in regressions:
            print(f"    {r['bill_name'][:35]}")
            print(f"      正确={r['correct']}  上次={r['last_system']}  现在={r['now_system']}({r['now_verdict']})")
    else:
        print(f"\n  ✓ 无退步项")

    if improvements:
        print(f"\n  ✓ 进步项（{len(improvements)}条，上次错/这次对）:")
        for r in improvements:
            print(f"    {r['bill_name'][:35]}  正确={r['correct']}")

    print()


# ================================================================
# 命令行入口
# ================================================================

def main():
    parser = argparse.ArgumentParser(
        description="黄金集评测工具 —— 导入纠正 + 回归测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 导入你改好的Excel（存入黄金集+经验库）
  python tests/eval_golden.py --import 改好的文件.xlsx

  # 跑评测（用黄金集测试系统准确率）
  python tests/eval_golden.py

  # 跑评测 + 和上次结果对比（检测退步）
  python tests/eval_golden.py --compare-last

  # 只导入黄金集，不存入经验库
  python tests/eval_golden.py --import 文件.xlsx --no-experience

  # 查看当前黄金集有多少条
  python tests/eval_golden.py --info
        """,
    )

    parser.add_argument("--import", dest="import_file",
                        help="从修正后的Excel导入黄金集")
    parser.add_argument("--compare-last", action="store_true",
                        help="和上次评测结果对比，找出退步项")
    parser.add_argument("--no-experience", action="store_true",
                        help="导入时不存入经验库（只存黄金集JSON）")
    parser.add_argument("--info", action="store_true",
                        help="查看黄金集基本信息")

    args = parser.parse_args()

    if args.info:
        cases = load_golden_cases()
        print(f"黄金集: {GOLDEN_FILE}")
        print(f"  条目数: {len(cases)}")
        if cases:
            sources = {}
            for c in cases:
                src = c.get("source_file", "未知")
                sources[src] = sources.get(src, 0) + 1
            print(f"  来源文件:")
            for src, cnt in sources.items():
                print(f"    {src}: {cnt}条")
        return

    if args.import_file:
        import_golden(args.import_file, save_to_exp=not args.no_experience)
        return

    # 默认：跑评测
    run_evaluation(compare_last=args.compare_last)


if __name__ == "__main__":
    main()
