"""
自动套定额系统 - 命令行入口
功能：
1. 读取清单Excel → 逐条匹配定额 → 输出结果Excel
2. 支持两种模式：
   - agent: Agent模式（造价员贾维斯，搜索+Agent分析+自动学习进化）
   - search: 纯搜索模式（不调API，免费）
3. 整合经验库：先查经验库→命中直通→未命中走搜索（经验只在人工审核后入库）

使用方法：
    # Agent模式（造价员贾维斯，自动学习进化）
    python main.py 清单文件.xlsx --mode agent

    # 纯搜索模式（不需要API Key，免费）
    python main.py 清单文件.xlsx --mode search

    # 不使用经验库（不查也不存经验）
    python main.py 清单文件.xlsx --no-experience

    # 查看帮助
    python main.py --help
"""

import argparse
import sys
import time
import os
import json
import tempfile
from pathlib import Path

from loguru import logger

# 日志写入文件（logs/目录下，按天轮转，保留30天）
config_module = __import__("config")
logger.add(
    str(config_module.LOG_DIR / "auto_quota_{time:YYYY-MM-DD}.log"),
    rotation="00:00",     # 每天零点新建一个日志文件
    retention="30 days",  # 保留30天
    encoding="utf-8",
    level="INFO",
)

import config
from src.bill_reader import BillReader
from src.output_writer import OutputWriter
from src.bill_cleaner import clean_bill_items
from src.match_engine import (
    init_search_components, init_experience_db, match_by_mode,
)


# ============================================================
# 工具函数（run 直接使用）
# ============================================================

def _atomic_write_json(output_path: str, payload: dict):
    """原子写JSON，避免中断时留下损坏文件。"""
    target = Path(output_path)
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
            json.dump(payload, tf, ensure_ascii=False)
        os.replace(tmp_path, target)
    finally:
        if tmp_path and Path(tmp_path).exists():
            try:
                os.remove(tmp_path)
            except OSError:
                pass


# ============================================================
# 初始化辅助函数（run 直接使用）
# ============================================================

def _resolve_run_province(province: str, interactive, json_output):
    """解析并设置当前省份。"""
    if interactive is None:
        interactive = not json_output
    resolved_province = config.resolve_province(
        province,
        interactive=interactive
    )
    return resolved_province


def _load_bill_items_for_run(input_path: Path, sheet=None, limit=None):
    """读取并清洗清单数据，按需截断数量。"""
    logger.info("第1步：读取清单文件...")
    reader = BillReader()
    bill_items = reader.read_excel(str(input_path), sheet_name=sheet)

    if not bill_items:
        raise RuntimeError("未读取到任何清单项目，请检查文件格式")

    # 清单数据清洗（名称修正+专业分类+参数提取）
    bill_items = clean_bill_items(bill_items)

    # 限制数量（调试用）
    if limit:
        bill_items = bill_items[:limit]
        logger.info(f"限制处理前 {limit} 条")

    return bill_items


# ============================================================
# 统计与日志（run 直接使用）
# ============================================================

def _build_run_stats(results: list[dict], elapsed: float) -> dict:
    """构建运行统计信息。"""
    total = len(results)
    matched = sum(1 for r in results if r.get("quotas"))
    high_conf = sum(
        1 for r in results if r.get("confidence", 0) >= config.CONFIDENCE_GREEN)
    mid_conf = sum(
        1 for r in results
        if config.CONFIDENCE_YELLOW <= r.get("confidence", 0) < config.CONFIDENCE_GREEN)
    exp_matched = sum(
        1 for r in results if r.get("match_source", "").startswith("experience"))

    # 从结果中统计审核规则拦截经验库直通的次数
    review_rejected = sum(
        1 for r in results
        if r.get("bill_item", {}).get("_review_rejected"))

    return {
        "total": total,
        "matched": matched,
        "high_conf": high_conf,
        "mid_conf": mid_conf,
        "low_conf": total - high_conf - mid_conf,
        "exp_hits": exp_matched,
        "review_rejected": review_rejected,
        "elapsed": elapsed,
    }


def _log_run_summary(stats: dict, has_experience_db: bool):
    """打印运行汇总日志。"""
    total = stats["total"]
    matched = stats["matched"]
    high_conf = stats["high_conf"]
    mid_conf = stats["mid_conf"]
    exp_matched = stats["exp_hits"]
    elapsed = stats["elapsed"]

    logger.info("=" * 60)
    logger.info("匹配完成")
    logger.info(f"  总清单项: {total}")
    logger.info(f"  已匹配: {matched} ({matched * 100 // max(total, 1)}%)")
    logger.info(f"  高置信度(绿): {high_conf}")
    logger.info(f"  中置信度(黄): {mid_conf}")
    logger.info(f"  未匹配/低置信度(红): {total - high_conf - mid_conf}")
    if has_experience_db:
        logger.info(f"  经验库命中: {exp_matched} ({exp_matched * 100 // max(total, 1)}%)")
    review_rejected = stats.get("review_rejected", 0)
    if review_rejected > 0:
        logger.info(f"  审核规则拦截: {review_rejected}条经验库直通被拦截（已走搜索兜底）")
    logger.info(f"  耗时: {elapsed:.1f}秒")
    per_item = elapsed / max(total, 1)
    logger.info(f"  平均每条: {per_item:.2f}秒/条（含初始化）")
    if total > 0:
        init_overhead = 23  # 模型加载固定开销（秒）
        match_time = max(elapsed - init_overhead, 0)
        match_per_item = match_time / total
        logger.info(f"  纯匹配速度: {match_per_item:.2f}秒/条")
        logger.info(f"  预估: 100条≈{(init_overhead + match_per_item * 100) / 60:.1f}分钟 | "
                   f"500条≈{(init_overhead + match_per_item * 500) / 60:.1f}分钟 | "
                   f"1000条≈{(init_overhead + match_per_item * 1000) / 60:.1f}分钟")
    logger.info("=" * 60)


def _log_run_banner(input_path: Path, mode: str, province: str,
                    no_experience: bool):
    """打印启动横幅信息。"""
    logger.info("=" * 60)
    logger.info("自动套定额系统")
    logger.info(f"  输入文件: {input_path}")
    logger.info(f"  匹配模式: {mode}")
    logger.info(f"  省份: {province}")
    logger.info(f"  经验库: {'关闭' if no_experience else '开启'}")
    logger.info("=" * 60)


# ============================================================
# 核心编排入口
# ============================================================

def run(input_file, mode="agent", output=None,
        limit=None, province=None, aux_provinces=None,
        no_experience=False, sheet=None,
        json_output=None, agent_llm=None, interactive=None,
        progress_callback=None):
    """执行匹配的核心逻辑（供命令行和其他模块直接调用）

    参数:
        input_file: 清单Excel文件路径
        mode: 匹配模式 (search/agent)
        output: 输出Excel路径（默认自动生成）
        limit: 只处理前N条（调试用）
        province: 主定额库省份名称
        aux_provinces: 辅助定额库列表（如 ["广东土建", "广东市政"]）
        no_experience: 是否禁用经验库
        sheet: 指定只读取的Sheet名称
        json_output: JSON结果输出路径（可选）
        agent_llm: Agent模式使用的大模型
        interactive: 是否允许交互式提示（如省份选择）。
                     默认None=自动判断（命令行调用时True，程序调用建议传False）
        progress_callback: 进度回调函数（可选），签名: callback(percent, current_idx, message)
                           percent: 0~100 进度百分比
                           current_idx: 当前处理到第几条
                           message: 进度描述文字

    返回: {"results": [...], "stats": {...}}
    """
    input_path = Path(input_file)
    if not input_path.exists():
        raise FileNotFoundError(f"文件不存在: {input_path}")

    # 解析省份（支持简称模糊匹配）
    resolved_province = _resolve_run_province(
        province, interactive=interactive, json_output=json_output)
    _log_run_banner(input_path, mode, resolved_province, no_experience)

    start_time = time.time()

    # 进度回调辅助函数（容错：callback为None或调用出错都不影响主流程）
    def _notify(percent, idx, msg):
        if progress_callback:
            try:
                progress_callback(percent, idx, msg)
            except Exception:
                pass

    # 1. 读取清单
    bill_items = _load_bill_items_for_run(input_path, sheet=sheet, limit=limit)
    _notify(15, 0, f"清单读取完成，共{len(bill_items)}条")

    # 1.1 保存清单预览文件（供前端实时展示每条清单的匹配进度）
    if json_output:
        try:
            preview_path = Path(json_output).parent / "bill_preview.json"
            preview = [
                {
                    "code": it.get("code", ""),
                    "name": it.get("name", ""),
                    "description": it.get("description", ""),
                    "unit": it.get("unit", ""),
                    "quantity": it.get("quantity"),
                    "specialty_name": it.get("specialty_name", ""),
                }
                for it in bill_items
            ]
            preview_path.write_text(json.dumps(preview, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            logger.debug(f"清单预览文件写入失败（不影响匹配）: {e}")

    # 1.5 分析项目上下文（L2：项目级感知，让匹配有全局视角）
    # 容错：分析失败不影响主流程，降级为空上下文
    project_overview_text = ""
    if mode == "agent":
        try:
            from src.bill_cleaner import analyze_project_context, format_project_overview
            project_context = analyze_project_context(bill_items)
            project_overview_text = format_project_overview(project_context)
        except Exception as e:
            logger.warning(f"项目上下文分析失败（不影响匹配）: {e}")

    # 2. 初始化搜索引擎
    searcher, validator = init_search_components(resolved_province, aux_provinces)

    # 初始化经验库（可选）
    experience_db = init_experience_db(no_experience, province=resolved_province)
    _notify(25, 0, "搜索引擎就绪")

    # 3. 执行匹配
    logger.info(f"第3步：开始匹配 ({mode} 模式)...")
    _notify(30, 0, "开始匹配...")
    results = match_by_mode(
        mode, bill_items, searcher, validator, experience_db,
        resolved_province, agent_llm=agent_llm,
        project_overview=project_overview_text,
        progress_callback=progress_callback)
    _notify(90, len(bill_items), "匹配完成，验证中...")

    # 3.5 LLM后验证：逐条审核匹配结果，错误的自动纠正重搜
    if mode == "agent" and config.LLM_VERIFY_ENABLED:
        try:
            from src.llm_verifier import LLMVerifier
            logger.info("第3.5步：LLM后验证（逐条审核+纠正）...")
            verifier = LLMVerifier(llm_type=agent_llm)
            results = verifier.verify_batch(
                results, searcher=searcher,
                progress_callback=progress_callback)
            vs = verifier.stats
            logger.info(f"  验证汇总: 纠正{vs['corrected']}条, "
                        f"确认{vs['correct']}条, "
                        f"未纠正{vs['correct_failed']}条")
        except Exception as e:
            logger.warning(f"LLM后验证跳过（不影响输出）: {e}")

    _notify(92, len(bill_items), "生成结果中...")

    # 4. 输出结果
    elapsed = time.time() - start_time
    stats = _build_run_stats(results, elapsed)

    # 4.5 L4 主动学习：不确定项分组标注（在输出前标注，让Excel显示[请教]标记）
    try:
        from src.active_learner import mark_learning_groups
        mark_learning_groups(results)
    except Exception as e:
        logger.warning(f"L4主动学习标注跳过（不影响输出）: {e}")

    # 生成Excel（基于原始文件结构，保留分部小节标题）
    logger.info("第4步：生成结果Excel...")
    writer = OutputWriter()
    output_path = writer.write_results(
        results, output, original_file=str(input_path))
    logger.info(f"  输出文件: {output_path}")
    _notify(95, len(results), "结果已生成")

    # 如果指定了JSON输出，也保存一份JSON（供审核工具读取）
    if json_output:
        _atomic_write_json(json_output, {"results": results, "stats": stats})
        logger.info(f"  JSON结果已保存: {json_output}")

    # 5. 打印统计
    _log_run_summary(stats, has_experience_db=bool(experience_db))

    # 6. 记录运行指标（准确率追踪）
    try:
        from src.accuracy_tracker import AccuracyTracker
        AccuracyTracker().record_run(
            stats, input_file=str(input_path),
            mode=mode, province=resolved_province)
    except Exception as e:
        logger.error(f"准确率追踪记录失败: {e}")

    return {"results": results, "stats": stats}


# ============================================================
# CLI 入口
# ============================================================

def main():
    """命令行入口：解析参数后调用 run()"""
    parser = argparse.ArgumentParser(
        description="自动套定额系统 - 命令行入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # Agent模式（造价员贾维斯，需要API Key）
  python main.py 清单文件.xlsx --mode agent

  # 纯搜索模式（免费，不需要API Key）
  python main.py 清单文件.xlsx --mode search

  # 指定输出路径
  python main.py 清单文件.xlsx --output 结果.xlsx

  # 不使用经验库
  python main.py 清单文件.xlsx --no-experience
        """,
    )
    parser.add_argument("input_file", help="清单Excel文件路径")
    parser.add_argument("--mode", choices=["search", "agent"], default="agent",
                        help="匹配模式: agent=造价员贾维斯(默认) search=纯搜索(免费)")
    parser.add_argument("--output", "-o", help="输出文件路径（默认自动生成）")
    parser.add_argument("--limit", type=int, help="只处理前N条清单项（调试用）")
    parser.add_argument("--province", default=None, help=f"主定额库（默认: {config.CURRENT_PROVINCE}）")
    parser.add_argument("--aux-province", default=None,
                        help="辅助定额库（逗号分隔，用于安装清单中的土建/市政项目）")
    parser.add_argument("--no-experience", action="store_true",
                        help="不使用经验库（不查询也不存储经验）")
    parser.add_argument("--sheet", help="指定只读取的Sheet名称（默认读取所有Sheet）")
    parser.add_argument("--json-output", help="将匹配结果输出为JSON文件（供Web界面读取）")
    parser.add_argument("--agent-llm", help="Agent模式使用的大模型（覆盖config中的AGENT_LLM）")

    args = parser.parse_args()

    # 解析辅助定额库
    aux_provinces = None
    if args.aux_province:
        aux_provinces = [p.strip() for p in args.aux_province.split(",") if p.strip()]

    try:
        run(
            input_file=args.input_file,
            mode=args.mode,
            output=args.output,
            limit=args.limit,
            province=args.province,
            aux_provinces=aux_provinces,
            no_experience=args.no_experience,
            sheet=args.sheet,
            json_output=args.json_output,
            agent_llm=args.agent_llm,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as e:
        logger.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
