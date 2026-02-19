"""
一键导入定额数据工具

功能：扫描指定省份目录下所有xlsx → 自动识别specialty → 导入数据库 → 生成规则JSON → 重建索引

默认增量模式：自动跳过已导入的文件，只处理新增文件。
用 --full 强制全量重导。

用法：
    python tools/import_all.py --province "北京2024"        # 增量导入（默认）
    python tools/import_all.py --full                       # 全量重导
    python tools/import_all.py --skip-index                 # 跳过索引重建
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


def _filter_new_files(xlsx_files: list[Path], db: QuotaDB) -> list[Path]:
    """对比导入历史，筛选出未导入过的新文件

    判断逻辑：文件名 + 文件大小 + 修改时间 全部一致 → 已导入，跳过
    文件名相同但大小/时间变了 → 视为修改过的文件，需要重新导入

    参数:
        xlsx_files: 目录中所有xlsx文件路径列表
        db: 定额数据库实例

    返回:
        需要导入的文件列表（新增的 + 修改过的）
    """
    history = db.get_import_history()
    # 建立已导入文件的索引：{文件名: {file_size, file_mtime}}
    imported_map = {
        h["file_name"]: {"file_size": h["file_size"], "file_mtime": h["file_mtime"]}
        for h in history
    }

    new_files = []      # 需要导入的文件
    skipped_files = []  # 已导入跳过的文件
    modified_files = [] # 修改过需要重新导入的文件

    for f in xlsx_files:
        stat = f.stat()
        prev = imported_map.get(f.name)
        if prev is None:
            # 全新文件
            new_files.append(f)
        elif prev["file_size"] == stat.st_size and abs(prev["file_mtime"] - stat.st_mtime) < 1:
            # 文件名、大小、修改时间都一致 → 已导入，跳过
            # （修改时间允许1秒误差，避免文件系统精度差异）
            skipped_files.append(f)
        else:
            # 同名文件但内容变了 → 需要重新导入
            modified_files.append(f)

    # 打印筛选结果
    if skipped_files:
        print(f"  ✓ 已导入（跳过）: {len(skipped_files)} 个文件")
        for f in skipped_files:
            print(f"    · {f.name}")

    if modified_files:
        print(f"  ↻ 已修改（重新导入）: {len(modified_files)} 个文件")
        for f in modified_files:
            print(f"    · {f.name}")

    if new_files:
        print(f"  ★ 新增（待导入）: {len(new_files)} 个文件")
        for f in new_files:
            print(f"    · {f.name}")

    print()
    return new_files + modified_files


def main():
    parser = argparse.ArgumentParser(description="一键导入定额数据")
    parser.add_argument("--province", type=str, default=None,
                        help="省份版本（如 北京2024），不指定使用默认配置")
    parser.add_argument("--skip-index", action="store_true",
                        help="跳过索引重建（仅导入数据和生成规则）")
    parser.add_argument("--full", action="store_true",
                        help="全量重导（忽略导入历史，清除旧数据重新导入所有文件）")
    args = parser.parse_args()

    try:
        province = _resolve_import_province(args.province)
    except ValueError as e:
        print(f"错误: {e}")
        return

    # 同步运行态省份，避免后续未显式传参模块回落到硬编码默认值
    config.set_current_province(province)

    mode_label = "全量重导" if args.full else "增量导入"
    print("=" * 60)
    print(f"  一键导入定额数据（{mode_label}）")
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

    print(f"扫描目录: {quota_dir}")
    print(f"发现 {len(xlsx_files)} 个Excel文件")
    print()

    # ===== 第2步：筛选需要导入的文件 =====
    db = QuotaDB(province=province)

    # 自动检测：如果import_history表为空但数据库已有数据，说明是旧库升级
    # 这种情况自动切换为全量模式，避免重复追加
    history = db.get_import_history()
    stats = db.get_stats()
    if not args.full and not history and stats.get("total", 0) > 0:
        print("检测到旧数据库（无导入历史记录），自动切换为全量模式")
        print("（后续运行将自动使用增量模式）")
        print()
        args.full = True

    if args.full:
        # 全量模式：清空导入历史，所有文件都需要导入
        db.clear_import_history()
        files_to_import = xlsx_files
        print(f"全量模式：将导入全部 {len(files_to_import)} 个文件")
        print()
    else:
        # 增量模式：对比导入历史，只导入新文件
        files_to_import = _filter_new_files(xlsx_files, db)
        if not files_to_import:
            print("所有文件已导入，无需更新。")
            print("如需强制全量重导，请使用 --full 参数")
            return

    # ===== 第3步：逐个导入数据库 =====
    print(f"【第1步】导入定额到数据库（{len(files_to_import)} 个文件）...")
    imported = {}  # {specialty: count}

    if args.full:
        # 全量模式：同一specialty的第一个文件清除旧数据，后续追加
        cleared_specialties = set()
        for xlsx_file in files_to_import:
            specialty = detect_specialty_from_excel(str(xlsx_file))
            is_first = specialty not in cleared_specialties
            cleared_specialties.add(specialty)
            mode = "清除旧数据+导入" if is_first else "追加导入"
            print(f"  导入: {xlsx_file.name} → specialty='{specialty}' ({mode})")
            count = db.import_excel(str(xlsx_file), specialty=specialty,
                                    clear_existing=is_first)
            imported[specialty] = imported.get(specialty, 0) + count
            # 记录导入历史
            db.record_import(str(xlsx_file), specialty, count)
            print(f"    完成: {count}条")
    else:
        # 增量模式：所有文件都用追加模式，不清除旧数据
        for xlsx_file in files_to_import:
            specialty = detect_specialty_from_excel(str(xlsx_file))
            print(f"  导入: {xlsx_file.name} → specialty='{specialty}' (增量追加)")
            count = db.import_excel(str(xlsx_file), specialty=specialty,
                                    clear_existing=False)
            imported[specialty] = imported.get(specialty, 0) + count
            # 记录导入历史
            db.record_import(str(xlsx_file), specialty, count)
            print(f"    完成: {count}条")

    total = sum(imported.values())
    print(f"\n  本次导入: 共{total}条")
    for sp, cnt in imported.items():
        print(f"    {sp}: {cnt}条")
    print()

    # ===== 第4步：生成规则JSON =====
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

    # ===== 第5步：重建搜索索引 =====
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
        print(f"    · {sp}: {cnt}条（本次导入）")
    print()
    print(f"  规则文件: data/quota_rules/{province}/*定额规则.json")
    if not args.skip_index:
        print(f"  BM25索引: 已重建")
        print(f"  向量索引: 已重建")


if __name__ == "__main__":
    main()
