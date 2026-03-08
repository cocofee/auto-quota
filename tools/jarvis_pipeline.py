# -*- coding: utf-8 -*-
"""
Jarvis 批处理流水线 - 一键完成匹配+审核+纠正

替代 Web 界面，全流程命令行完成：
  清单.xlsx → 匹配定额 → 自动审核 → 纠正Excel → 存经验库(默认开启)

用法：
    python tools/jarvis_pipeline.py "清单.xlsx"
    python tools/jarvis_pipeline.py "清单.xlsx" --province "北京2024"
    python tools/jarvis_pipeline.py "清单.xlsx" --no-store   # 不存经验库
"""

import sys
import os
import json
import argparse
import secrets
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _generate_run_id() -> str:
    """生成唯一运行ID（毫秒时间戳 + 6位随机hex），用于文件命名防碰撞。

    格式示例：20260223_074512_123_a3f1b2
    - 前15位是秒级时间戳（可读）
    - 中间3位是毫秒（同秒区分）
    - 末尾6位是随机hex（同毫秒兜底）
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]  # 截取到毫秒（3位）
    rand = secrets.token_hex(3)  # 6位随机hex
    return f"{ts}_{rand}"


def _count_manual_review_rows(manual_items) -> tuple[int, int]:
    """统计人工审核项中真正对应清单行的数量。

    返回: (manual_rows, manual_reminders)
    - manual_rows: 需要人工处理的清单行（seq>0）
    - manual_reminders: 跨项提醒（seq<=0 且 name=【跨项提醒】），不占清单行
    """
    manual_rows = 0
    manual_reminders = 0
    for item in manual_items or []:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            seq_raw = item.get("seq", 1)
            try:
                seq = int(seq_raw)
            except (TypeError, ValueError):
                seq = 1
            if seq <= 0 and name == "【跨项提醒】":
                manual_reminders += 1
                continue
        manual_rows += 1
    return manual_rows, manual_reminders


def _build_pipeline_stats(results, auto_corrections, manual_items, measure_items) -> dict:
    """统一构建流水线统计，避免跨项提醒污染正确率。"""
    manual_rows, manual_reminders = _count_manual_review_rows(manual_items)
    total = len(results)
    fallback_sources = {"agent_fallback", "agent_error"}
    fallback_count = sum(
        1 for r in results if r.get("match_source") in fallback_sources
    )
    correct = total - len(auto_corrections) - manual_rows - len(measure_items)
    return {
        "total": total,
        "correct": max(correct, 0),
        "auto_corrected": len(auto_corrections),
        "manual": manual_rows,
        "manual_reminders": manual_reminders,
        "measure": len(measure_items),
        "fallback": fallback_count,
    }


def _build_manual_neutralizations(manual_items) -> list[dict]:
    """Build quota-clearing corrections for category mismatch rows pending manual review."""
    neutralizations: list[dict] = []
    seen: set[tuple[str, int | None, int]] = set()

    for item in manual_items or []:
        if not isinstance(item, dict):
            continue

        error_type = str(item.get("error_type", "") or "").strip()
        reason = str(item.get("error_reason", item.get("reason", "")) or "").strip()
        if error_type != "category_mismatch" and "类别不匹配" not in reason:
            continue

        seq_raw = item.get("seq")
        try:
            seq = int(seq_raw)
        except (TypeError, ValueError):
            continue
        if seq <= 0:
            continue

        sheet_name = str(item.get("sheet_name", "") or "").strip()
        sheet_bill_seq_raw = item.get("sheet_bill_seq")
        try:
            sheet_bill_seq = int(sheet_bill_seq_raw)
        except (TypeError, ValueError):
            sheet_bill_seq = None

        key = (sheet_name, sheet_bill_seq, seq)
        if key in seen:
            continue
        seen.add(key)

        neutralizations.append({
            "seq": seq,
            "sheet_name": sheet_name,
            "sheet_bill_seq": sheet_bill_seq,
            "quota_id": "",
            "quota_name": "",
            "clear_quota": True,
            "review_mark": "待人工",
            "note": f"Jarvis待人工: {reason[:80]}",
        })

    return neutralizations


def pipeline(excel_path, province=None, aux_provinces=None,
             use_experience=True, store=True, quiet=False):
    """Jarvis 批处理流水线（匹配 → 审核 → 纠正 → 存经验库）

    参数:
        excel_path: 清单Excel路径
        province: 主定额库省份名称（None=使用默认省份）
        aux_provinces: 辅助定额库列表（用于跨专业匹配，如安装清单中的土建/市政项目）
        use_experience: 是否启用经验库（默认开启）
        store: 是否将纠正结果存入经验库
        quiet: 静默模式（抑制进度条）

    返回: {
        "output_excel": "已审核Excel路径",
        "summary": "审核摘要文本",
        "stats": {"total", "correct", "auto_corrected", "manual", "measure"},
        "log_file": "本次运行日志路径",
    }
    """
    from loguru import logger
    from config import OUTPUT_DIR

    # 静默模式：抑制 tqdm 等进度条
    if quiet:
        os.environ["TQDM_DISABLE"] = "1"
        os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"

    # ---- 生成唯一运行ID（后续所有输出共用，防止并发碰撞）----
    run_id = _generate_run_id()
    stem = Path(excel_path).stem[:30]

    # ---- 为本次运行创建独立日志文件 ----
    log_dir = OUTPUT_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = str(log_dir / f"jarvis_{stem}_{run_id}.log")
    # loguru的add返回id，运行结束后移除，避免日志串到后续运行
    log_id = logger.add(
        log_file, encoding="utf-8", level="DEBUG",
        format="{time:HH:mm:ss} | {level:<7} | {name}:{function}:{line} | {message}",
    )
    # ---- 自动挂载兄弟库（用户不传aux_provinces时，自动获取同省份同年份的兄弟库）----
    if not aux_provinces and province:
        from config import get_sibling_provinces
        aux_provinces = get_sibling_provinces(province)
        if aux_provinces:
            logger.info(f"自动挂载兄弟库: {aux_provinces}")
            print(f"辅助定额: {', '.join(aux_provinces)}（自动挂载）")

    logger.info(f"Jarvis流水线启动 | 文件: {excel_path} | 主定额: {province} | 辅助: {aux_provinces}")

    json_path = None
    corr_json = None
    try:
        # ---- 启动检查：方法卡片是否需要更新 ----
        try:
            from src.method_cards import MethodCards
            mc = MethodCards()
            mc_stats = mc.get_stats()
            if mc_stats["total_cards"] == 0:
                # 没有方法卡片，尝试增量生成
                logger.info("检测到方法卡片为空，尝试自动生成...")
                print("  检查方法卡片...")
                from tools.gen_method_cards import incremental_generate
                card_result = incremental_generate(province=province, min_samples=5)
                if card_result["generated"] > 0:
                    print(f"  已自动生成 {card_result['generated']} 张方法卡片")
                    logger.info(f"方法卡片自动生成: {card_result['generated']}张")
                else:
                    print("  经验数据不足，暂无方法卡片可生成")
            else:
                logger.info(f"方法卡片已加载: {mc_stats['total_cards']}张")
        except Exception as e:
            logger.debug(f"方法卡片检查跳过（不影响主流程）: {e}")

        # ---- 第1步：匹配定额 ----
        print("=" * 60)
        print("第1步：匹配定额")
        print("=" * 60)

        from main import run

        output_excel = str(OUTPUT_DIR / f"匹配结果_{stem}_{run_id}.xlsx")

        data = run(
            input_file=excel_path,
            mode="agent",
            output=output_excel,
            province=province,
            aux_provinces=aux_provinces,
            no_experience=not use_experience,
            interactive=False,  # 省份已在 main() 中提前解析，这里无需交互
        )

        results = data.get("results", [])
        if not results:
            print("没有匹配结果，请检查清单文件格式。")
            logger.warning("匹配结果为空，流水线终止")
            return None

        logger.info(f"匹配完成: {len(results)}条")

        # ---- 第2步：自动审核 ----
        print()
        print("=" * 60)
        print("第2步：自动审核")
        print("=" * 60)

        # auto_review() 需要JSON文件路径，先保存中间结果
        temp_dir = OUTPUT_DIR / "temp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        json_path = str(temp_dir / f"pipeline_{stem}_{run_id}.json")

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        try:
            from tools.jarvis_auto_review import auto_review
        except ImportError:
            from jarvis_auto_review import auto_review

        summary, auto_corrections, manual_items, measure_items = auto_review(
            json_path, province, sibling_provinces=aux_provinces
        )
        manual_rows, manual_reminders = _count_manual_review_rows(manual_items)

        print(summary)
        logger.info(
            f"审核完成: 自动纠正{len(auto_corrections)}条, "
            f"需人工{manual_rows}条, 跨项提醒{manual_reminders}条, "
            f"措施项{len(measure_items)}条"
        )

        # 记录审核统计到追踪器
        try:
            from src.accuracy_tracker import AccuracyTracker
            stats_for_tracker = _build_pipeline_stats(
                results, auto_corrections, manual_items, measure_items
            )
            AccuracyTracker().record_review(
                input_file=excel_path,
                province=province,
                total=len(results),
                auto_corrections=len(auto_corrections),
                manual_items=stats_for_tracker["manual"],
                measure_items=len(measure_items),
                correct_count=stats_for_tracker["correct"],
            )
        except Exception as e:
            logger.error(f"审核统计记录失败: {e}")

        # ---- 第3步：纠正Excel ----
        manual_neutralizations = _build_manual_neutralizations(manual_items)
        excel_corrections = auto_corrections + manual_neutralizations
        corrected_excel = output_excel  # 默认用匹配结果（无纠正时不生成新文件）

        if excel_corrections:
            print()
            print("=" * 60)
            print(f"第3步：纠正Excel（{len(excel_corrections)}处）")
            print("=" * 60)

            try:
                from tools.jarvis_correct import correct_excel
            except ImportError:
                from jarvis_correct import correct_excel

            corrected_excel = correct_excel(output_excel, excel_corrections)
            print(f"  已审核Excel: {corrected_excel}")
            if manual_neutralizations:
                print(f"  已标记待人工: {len(manual_neutralizations)}条（已清空疑似错配定额）")
        else:
            print("\n第3步：无需纠正，跳过")

        # ---- 第4步：存经验库（可选）----
        if store and auto_corrections:
            print()
            print("=" * 60)
            print("第4步：存入经验库")
            print("=" * 60)

            # 保存纠正JSON供 store_batch 读取
            corr_json = str(temp_dir / f"corrections_{stem}_{run_id}.json")
            with open(corr_json, "w", encoding="utf-8") as f:
                json.dump(auto_corrections, f, ensure_ascii=False, indent=2)

            try:
                from tools.jarvis_store import store_batch
            except ImportError:
                from jarvis_store import store_batch

            # confirmed=False: 自动纠正未经人工确认，写入候选层(auto_review)
            # 用户后续通过 experience_promote.py 审核晋升为权威层
            store_batch(corr_json, province, confirmed=False)
            print("  已存入经验库")
        elif store:
            print("\n第4步：无纠正项，跳过经验库存储")

        # ---- 汇总 ----
        stats = _build_pipeline_stats(results, auto_corrections, manual_items, measure_items)

        # 记录每条结果的关键信息到日志（用于事后分析）
        logger.info("=" * 60)
        logger.info("逐条匹配结果:")
        logger.info("=" * 60)
        for i, r in enumerate(results, 1):
            name = r.get("bill_name", r.get("name", ""))
            matched_id = ""
            matched_name = ""
            confidence = r.get("confidence", 0)  # 用结果级置信度（非定额级score）
            source = r.get("match_source", "")
            quotas = r.get("quotas", [])
            if quotas:
                main_q = quotas[0]
                matched_id = main_q.get("quota_id", "")
                matched_name = main_q.get("name", "")[:20]
            # 标记状态（区分正常/降级/无结果/已纠正）
            status = "OK"
            if not quotas:
                status = "无结果"
            elif r.get("rule_corrected"):
                status = "已纠正"
            elif source == "agent_fallback":
                status = "降级"
            logger.info(
                f"  [{i:3d}] {status:<4} | {name[:25]:<25} → {matched_id} {matched_name} "
                f"| 置信:{confidence:3d} | 来源:{source}"
            )

        logger.info("=" * 60)
        summary_line = (f"汇总: 总{stats['total']} 正确{stats['correct']} "
                        f"自动纠正{stats['auto_corrected']} 人工{stats['manual']} 措施{stats['measure']}")
        if stats['fallback'] > 0:
            pct = stats['fallback'] * 100 / max(stats['total'], 1)
            summary_line += f" 降级{stats['fallback']}({pct:.0f}%)"
        logger.info(summary_line)
        logger.info(f"日志文件: {log_file}")

        print()
        print("=" * 60)
        print("流水线完成")
        print(f"  匹配结果: {output_excel}")
        if corrected_excel != output_excel:
            print(f"  已审核版: {corrected_excel}")
        print(f"  总条数:   {stats['total']}")
        print(f"  正确:     {stats['correct']}")
        print(f"  自动纠正: {stats['auto_corrected']}条")
        print(f"  需人工审: {stats['manual']}条")
        if stats.get("manual_reminders", 0):
            print(f"  跨项提醒: {stats['manual_reminders']}条")
        print(f"  措施项:   {stats['measure']}条（已跳过）")
        print(f"  运行日志: {log_file}")
        print("=" * 60)

        return {
            "output_excel": corrected_excel,
            "summary": summary,
            "stats": stats,
            "log_file": log_file,
        }
    finally:
        try:
            logger.remove(log_id)
        except Exception:
            pass
        try:
            if json_path and os.path.exists(json_path):
                os.remove(json_path)
            if corr_json and os.path.exists(corr_json):
                os.remove(corr_json)
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Jarvis 批处理流水线：一键匹配+审核+纠正",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  python tools/jarvis_pipeline.py "清单.xlsx"
  python tools/jarvis_pipeline.py "清单.xlsx" --province "北京2024"
  python tools/jarvis_pipeline.py "清单.xlsx" --province "广东安装" --aux-province "广东土建,广东市政"
  python tools/jarvis_pipeline.py "清单.xlsx" --no-experience  # 关闭经验库
  python tools/jarvis_pipeline.py "清单.xlsx" --no-store  # 不存经验库
""",
    )
    parser.add_argument("excel_path", help="清单Excel文件路径")
    parser.add_argument("--province", help="主定额库名称（如\"北京2024\"），不指定则交互选择")
    parser.add_argument("--aux-province",
                        help="辅助定额库（逗号分隔，如\"广东土建,广东市政\"）")
    parser.add_argument("--no-experience", action="store_true",
                        help="关闭经验库（默认开启）")
    parser.add_argument("--no-store", action="store_true",
                        help="不存经验库（默认自动存入候选层）")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="静默模式，抑制进度条")
    parser.add_argument("--diagnose", action="store_true",
                        help="跑完后自动诊断人工审核项的根因")
    parser.add_argument("--fix", action="store_true",
                        help="诊断后自动修复同义词缺口（需配合--diagnose使用）")
    parser.add_argument("--skip-benchmark", action="store_true",
                        help="自动修复时跳过benchmark回归检查（调试用）")
    args = parser.parse_args()

    if not os.path.exists(args.excel_path):
        print(f"错误：文件不存在 {args.excel_path}")
        sys.exit(1)

    # 主定额库解析
    from config import resolve_province

    try:
        province = resolve_province(
            args.province,
            interactive=(args.province is None),  # 未指定省份时让用户选
        )
    except SystemExit:
        return  # 用户取消选择
    except Exception as e:
        print(f"错误：主定额库解析失败 - {e}")
        sys.exit(1)

    # 辅助定额库解析
    aux_provinces = None
    if args.aux_province:
        aux_provinces = []
        for name in args.aux_province.split(","):
            name = name.strip()
            if not name:
                continue
            try:
                resolved = resolve_province(name)
                aux_provinces.append(resolved)
            except Exception as e:
                print(f"警告：辅助定额库 '{name}' 解析失败 - {e}（已跳过）")

    print(f"主定额: {province}")
    if aux_provinces:
        print(f"辅助定额: {', '.join(aux_provinces)}")
    print()

    # 运行流水线
    result = pipeline(
        excel_path=args.excel_path,
        province=province,
        aux_provinces=aux_provinces if aux_provinces else None,
        use_experience=not args.no_experience,
        store=not args.no_store,
        quiet=args.quiet,
    )

    if result is None:
        sys.exit(1)

    # 自动诊断人工审核项
    if args.diagnose and result.get("stats", {}).get("manual", 0) > 0:
        print()
        try:
            from tools.jarvis_diagnose import diagnose
            diagnose(result["output_excel"], province,
                     sibling_provinces=aux_provinces,
                     auto_fix=args.fix,
                     skip_benchmark=getattr(args, "skip_benchmark", False))
        except Exception as e:
            print(f"诊断跳过: {e}")


if __name__ == "__main__":
    main()
