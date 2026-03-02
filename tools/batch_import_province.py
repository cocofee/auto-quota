# -*- coding: utf-8 -*-
"""
通用省份批量导入脚本

从 by_province/<省份代码>/ 目录解析XML文件，
验证定额编号匹配率后导入经验库（候选层）。

使用方法：
    python tools/batch_import_province.py JS --province "江苏省安装工程计价定额(2014)" --preview
    python tools/batch_import_province.py JS --province "江苏省安装工程计价定额(2014)"
    python tools/batch_import_province.py GD --province "广东省通用安装工程综合定额(2018)"
"""

import sys
import time
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from tools.import_xml import convert_xml_to_pairs, do_import, preview_import
import config


def main():
    import argparse
    parser = argparse.ArgumentParser(description="通用省份批量导入")
    parser.add_argument("province_code", help="省份代码（如 JS/GD/ZJ/FJ）")
    parser.add_argument("--province", required=True, help="定额库完整名称")
    parser.add_argument("--preview", action="store_true", help="预览模式")
    parser.add_argument("--limit", type=int, default=0, help="只处理前N个文件")
    parser.add_argument("--dir", default=None,
                        help="XML文件目录（默认 data/oss_samples/by_province/<代码>/）")
    args = parser.parse_args()

    # 省份和目录
    province = args.province
    xml_dir = Path(args.dir) if args.dir else (
        PROJECT_ROOT / "data" / "oss_samples" / "by_province" / args.province_code)

    if not xml_dir.exists():
        logger.error(f"目录不存在: {xml_dir}")
        sys.exit(1)

    # 验证定额库存在
    try:
        db_path = config.get_quota_db_path(province)
        if not db_path.exists():
            logger.error(f"定额库不存在: {province}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"定额库查找失败: {e}")
        sys.exit(1)

    # Windows文件系统大小写不敏感，用set去重
    xml_files = sorted(set(xml_dir.glob("*.XML")) | set(xml_dir.glob("*.xml")))
    if args.limit > 0:
        xml_files = xml_files[:args.limit]

    logger.info(f"待处理: {len(xml_files)}个文件, 省份: {province}")

    if not xml_files:
        logger.error("没有找到XML文件")
        sys.exit(1)

    # 逐文件解析
    all_pairs = []
    start = time.time()

    for i, fpath in enumerate(xml_files):
        pairs = convert_xml_to_pairs(str(fpath))
        if pairs:
            all_pairs.extend(pairs)

        if (i + 1) % 50 == 0:
            elapsed = time.time() - start
            logger.info(f"  解析进度: {i+1}/{len(xml_files)}, "
                        f"累计{len(all_pairs)}条, {elapsed:.0f}s")

    elapsed = time.time() - start
    logger.info(f"解析完成: {len(xml_files)}个文件, {len(all_pairs)}条清单-定额对, {elapsed:.0f}s")

    if not all_pairs:
        logger.error("没有解析到有效数据")
        sys.exit(1)

    # 去重：同一个 (bill_pattern, quota_code组合) 只保留一条
    seen = set()
    unique_pairs = []
    for p in all_pairs:
        key = (p['bill_pattern'], tuple(q['code'] for q in p['quotas']))
        if key not in seen:
            seen.add(key)
            unique_pairs.append(p)

    logger.info(f"去重: {len(all_pairs)} → {len(unique_pairs)}条 "
                f"(去掉{len(all_pairs) - len(unique_pairs)}条重复)")

    if args.preview:
        preview_import(unique_pairs, [province])
    else:
        # 先预览看命中率
        stats = preview_import(unique_pairs, [province])
        importable = stats['matched_pairs'] + stats['partial_pairs']
        if importable == 0:
            logger.error("没有可导入的数据（定额编号全部未命中）")
            sys.exit(1)

        logger.info(f"\n开始导入 {importable} 条到候选层...")
        do_import(
            unique_pairs,
            project_name=f"{args.province_code}_OSS批量导入",
            provinces=[province],
            only_matched=True,
            source="batch_import",  # 候选层
        )
        logger.info("批量导入完成!")


if __name__ == "__main__":
    main()
