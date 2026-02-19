"""
一键导入定额数据工具

功能：扫描指定省份目录下所有xlsx → 自动识别specialty → 导入数据库 → 生成规则JSON → 重建索引

用法：
    python tools/import_all.py --province "北京2024"
    python tools/import_all.py                          # 使用默认省份
"""

import sys
import time
import argparse
from pathlib import Path

# 添加项目根目录到路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
import config
from src.quota_db import QuotaDB, detect_specialty_from_excel


def _resolve_import_province(name: str = None) -> str:
    """解析导入目标省份，优先匹配 data/quota_data 中真实可用的省份目录。"""
    available = config.list_all_provinces()

    # 未指定：优先当前省份，不存在则回退第一个可用省份
    if not name:
        current = config.get_current_province()
        if available and current not in available:
            return available[0]
        return current

    # 没有可扫描目录时（如旧版扁平结构），保留原始输入做兼容
    if not available:
        return name

    # 精确匹配
    if name in available:
        return name

    # 模糊匹配（唯一命中）
    matches = [p for p in available if name in p]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        options = ", ".join(matches)
        raise ValueError(f"'{name}' 匹配到多个省份: {options}，请输入更精确名称")

    options = ", ".join(available)
    raise ValueError(f"找不到省份 '{name}'，可用省份: {options}")


def main():
    parser = argparse.ArgumentParser(description="一键导入定额数据")
    parser.add_argument("--province", type=str, default=None,
                        help="省份版本（如 北京2024），不指定使用默认配置")
    parser.add_argument("--skip-index", action="store_true",
                        help="跳过索引重建（仅导入数据和生成规则）")
    args = parser.parse_args()

    try:
        province = _resolve_import_province(args.province)
    except ValueError as e:
        print(f"错误: {e}")
        return

    # 同步运行态省份，避免后续未显式传参模块回落到硬编码默认值
    config.set_current_province(province)

    print("=" * 60)
    print(f"  一键导入定额数据")
    print(f"  省份: {province}")
    print("=" * 60)
    print()

    # ===== 第1步：扫描xlsx文件 =====
    quota_dir = config.get_quota_data_dir(province)
    if not quota_dir.exists():
        # 兼容旧目录
        quota_dir = config.QUOTA_DATA_DIR
        if not quota_dir.exists():
            print(f"错误：定额目录不存在: {quota_dir}")
            print(f"请创建目录并放入广联达导出的定额Excel文件")
            return

    xlsx_files = sorted(quota_dir.glob("*.xlsx"))
    if not xlsx_files:
        print(f"错误：目录下没有xlsx文件: {quota_dir}")
        print(f"请将广联达导出的定额Excel放到该目录")
        return

    print(f"发现 {len(xlsx_files)} 个Excel文件:")
    for f in xlsx_files:
        print(f"  · {f.name}")
    print()

    # ===== 第2步：逐个导入数据库 =====
    print("【第1步】导入定额到数据库...")
    db = QuotaDB(province=province)
    imported = {}  # {specialty: count}
    cleared_specialties = set()  # 记录已清理旧数据的specialty，避免重复清理导致数据丢失

    for xlsx_file in xlsx_files:
        specialty = detect_specialty_from_excel(str(xlsx_file))
        # 同一specialty的第一个文件清除旧数据，后续文件追加
        is_first = specialty not in cleared_specialties
        cleared_specialties.add(specialty)
        mode_label = "清除旧数据+导入" if is_first else "追加导入"
        print(f"  导入: {xlsx_file.name} → specialty='{specialty}' ({mode_label})")
        count = db.import_excel(str(xlsx_file), specialty=specialty,
                                clear_existing=is_first)
        imported[specialty] = imported.get(specialty, 0) + count
        print(f"    完成: {count}条")

    total = sum(imported.values())
    print(f"\n  导入完成: 共{total}条")
    for sp, cnt in imported.items():
        print(f"    {sp}: {cnt}条")
    print()

    # ===== 第3步：生成规则JSON =====
    print("【第2步】生成定额规则JSON...")
    # 调用extract_quota_rules为每个specialty生成规则
    from tools.extract_quota_rules import process_all_chapters, generate_summary
    import json, os, tempfile

    rules_dir = PROJECT_ROOT / "data" / "quota_rules" / province
    rules_dir.mkdir(parents=True, exist_ok=True)

    for specialty in imported.keys():
        print(f"  生成 {specialty} 规则...")
        rules = process_all_chapters(db, specialty=specialty)

        json_path = rules_dir / f"{specialty}定额规则.json"
        # 原子写入
        json_tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json",
                prefix=f"{json_path.stem}_tmp_",
                dir=str(json_path.parent),
                encoding="utf-8", delete=False,
            ) as f:
                json_tmp = f.name
                json.dump(rules, f, ensure_ascii=False, indent=2)
            os.replace(json_tmp, json_path)
        finally:
            if json_tmp and Path(json_tmp).exists():
                try:
                    os.remove(json_tmp)
                except OSError:
                    pass

        # 写摘要
        summary_path = rules_dir / f"{specialty}定额规则_摘要.txt"
        summary = generate_summary(rules)
        summary_tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt",
                prefix=f"{summary_path.stem}_tmp_",
                dir=str(summary_path.parent),
                encoding="utf-8", delete=False,
            ) as f:
                summary_tmp = f.name
                f.write(summary)
            os.replace(summary_tmp, summary_path)
        finally:
            if summary_tmp and Path(summary_tmp).exists():
                try:
                    os.remove(summary_tmp)
                except OSError:
                    pass

        meta = rules["meta"]
        print(f"    {meta['total_quotas']}条定额 → {meta['total_families']}个家族 + {meta['total_standalone']}个独立")
        print(f"    保存: {json_path.name}")
    print()

    # ===== 第4步：重建搜索索引 =====
    if args.skip_index:
        print("【跳过】索引重建（--skip-index）")
    else:
        print("【第3步】重建搜索索引...")

        # BM25索引
        print("  构建BM25索引...")
        from src.bm25_engine import BM25Engine
        bm25 = BM25Engine(province=province)
        bm25.build_index()
        print(f"    完成: {len(bm25.quota_ids)}条")

        # 向量索引
        print("  构建向量索引（需要GPU，耗时较长）...")
        from src.vector_engine import VectorEngine
        vec = VectorEngine(province=province)
        vec.build_index()
        print(f"    完成")

    print()

    # ===== 汇总 =====
    print("=" * 60)
    print("  导入完成!")
    print("=" * 60)
    stats = db.get_stats()
    print(f"  数据库: {db.db_path}")
    print(f"  总定额: {stats['total']}条")
    print(f"  总章节: {stats['chapters']}个")
    print(f"  专业数: {stats['specialties']}个")
    for sp, cnt in imported.items():
        print(f"    · {sp}: {cnt}条")
    print()
    print(f"  规则文件: data/quota_rules/{province}/*定额规则.json")
    if not args.skip_index:
        print(f"  BM25索引: 已重建")
        print(f"  向量索引: 已重建")


if __name__ == "__main__":
    main()
