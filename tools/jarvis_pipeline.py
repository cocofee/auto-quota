# -*- coding: utf-8 -*-
"""
Jarvis 批处理流水线 - 一键完成匹配+审核+纠正

替代 Web 界面，全流程命令行完成：
  清单.xlsx → 匹配定额 → 自动审核 → 纠正Excel → 存经验库(可选)

用法：
    python tools/jarvis_pipeline.py "清单.xlsx"
    python tools/jarvis_pipeline.py "清单.xlsx" --province "北京2024"
    python tools/jarvis_pipeline.py "清单.xlsx" --store      # 纠正结果存入经验库
"""

import sys
import os
import json
import argparse
from pathlib import Path
from datetime import datetime

# 确保能导入项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))
# 确保能导入 tools/ 下的兄弟模块（jarvis_auto_review 等）
sys.path.insert(0, str(Path(__file__).parent))


def pipeline(excel_path, province=None,
             use_experience=False, store=False, quiet=False):
    """Jarvis 批处理流水线（匹配 → 审核 → 纠正 → 存经验库）

    参数:
        excel_path: 清单Excel路径
        province: 省份名称（None=使用默认省份）
        use_experience: 是否启用经验库
        store: 是否将纠正结果存入经验库
        quiet: 静默模式（抑制进度条）

    返回: {
        "output_excel": "已审核Excel路径",
        "summary": "审核摘要文本",
        "stats": {"total", "correct", "auto_corrected", "manual", "measure"}
    }
    """
    from config import OUTPUT_DIR

    # 静默模式：抑制 tqdm 等进度条
    if quiet:
        os.environ["TQDM_DISABLE"] = "1"
        os.environ["TRANSFORMERS_NO_ADVISORY_WARNINGS"] = "1"
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

    # ---- 第1步：匹配定额 ----
    print("=" * 60)
    print("第1步：匹配定额")
    print("=" * 60)

    from main import run

    # 生成明确的输出路径（后续步骤需要定位这个文件）
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(excel_path).stem[:30]
    output_excel = str(OUTPUT_DIR / f"匹配结果_{stem}_{timestamp}.xlsx")

    data = run(
        input_file=excel_path,
        mode="agent",
        output=output_excel,
        province=province,
        no_experience=not use_experience,
        interactive=False,  # 省份已在 main() 中提前解析，这里无需交互
    )

    results = data.get("results", [])
    if not results:
        print("没有匹配结果，请检查清单文件格式。")
        return None

    # ---- 第2步：自动审核 ----
    print()
    print("=" * 60)
    print("第2步：自动审核")
    print("=" * 60)

    # auto_review() 需要JSON文件路径，先保存中间结果
    temp_dir = OUTPUT_DIR / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    json_path = str(temp_dir / f"pipeline_{stem}_{timestamp}.json")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    from jarvis_auto_review import auto_review

    summary, auto_corrections, manual_items, measure_items = auto_review(
        json_path, province
    )

    print(summary)

    # ---- 第3步：纠正Excel ----
    corrected_excel = output_excel  # 默认用匹配结果（无纠正时不生成新文件）

    if auto_corrections:
        print()
        print("=" * 60)
        print(f"第3步：纠正Excel（{len(auto_corrections)}处）")
        print("=" * 60)

        from jarvis_correct import correct_excel

        corrected_excel = correct_excel(output_excel, auto_corrections)
        print(f"  已审核Excel: {corrected_excel}")
    else:
        print("\n第3步：无需纠正，跳过")

    # ---- 第4步：存经验库（可选）----
    if store and auto_corrections:
        print()
        print("=" * 60)
        print("第4步：存入经验库")
        print("=" * 60)

        # 保存纠正JSON供 store_batch 读取
        corr_json = str(temp_dir / f"corrections_{stem}_{timestamp}.json")
        with open(corr_json, "w", encoding="utf-8") as f:
            json.dump(auto_corrections, f, ensure_ascii=False, indent=2)

        from jarvis_store import store_batch

        store_batch(corr_json, province)
        print("  已存入经验库")
    elif store:
        print("\n第4步：无纠正项，跳过经验库存储")

    # ---- 汇总 ----
    stats = {
        "total": len(results),
        "correct": len(results) - len(auto_corrections) - len(manual_items) - len(measure_items),
        "auto_corrected": len(auto_corrections),
        "manual": len(manual_items),
        "measure": len(measure_items),
    }

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
    print(f"  措施项:   {stats['measure']}条（已跳过）")
    print("=" * 60)

    return {
        "output_excel": corrected_excel,
        "summary": summary,
        "stats": stats,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Jarvis 批处理流水线：一键匹配+审核+纠正",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
用法示例:
  python tools/jarvis_pipeline.py "清单.xlsx"
  python tools/jarvis_pipeline.py "清单.xlsx" --province "北京2024"
  python tools/jarvis_pipeline.py "清单.xlsx" --with-experience
  python tools/jarvis_pipeline.py "清单.xlsx" --store
""",
    )
    parser.add_argument("excel_path", help="清单Excel文件路径")
    parser.add_argument("--province", help="省份名称（如\"北京2024\"），不指定则交互选择")
    parser.add_argument("--with-experience", action="store_true",
                        help="启用经验库（默认关闭，纯搜索）")
    parser.add_argument("--store", action="store_true",
                        help="将自动纠正结果存入经验库（默认关闭）")
    parser.add_argument("--quiet", "-q", action="store_true",
                        help="静默模式，抑制进度条")
    args = parser.parse_args()

    if not os.path.exists(args.excel_path):
        print(f"错误：文件不存在 {args.excel_path}")
        sys.exit(1)

    # 省份解析：不指定时交互式选择，指定时自动解析
    from config import resolve_province

    try:
        province = resolve_province(
            args.province,
            interactive=(args.province is None),  # 未指定省份时让用户选
        )
    except SystemExit:
        return  # 用户取消选择
    except Exception as e:
        print(f"错误：省份解析失败 - {e}")
        sys.exit(1)

    print(f"使用省份: {province}")
    print()

    # 运行流水线
    result = pipeline(
        excel_path=args.excel_path,
        province=province,
        use_experience=args.with_experience,
        store=args.store,
        quiet=args.quiet,
    )

    if result is None:
        sys.exit(1)


if __name__ == "__main__":
    main()
