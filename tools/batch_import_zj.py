# -*- coding: utf-8 -*-
"""
浙江OSS数据批量导入脚本

从 data/oss_samples/zj_batch/ 解析257个浙江XML文件，
将安装工程的清单-定额对照数据导入经验库（候选层）和通用知识库。

使用方法：
    python tools/batch_import_zj.py --preview          # 预览（不写入）
    python tools/batch_import_zj.py                     # 正式导入
    python tools/batch_import_zj.py --limit 5           # 只处理前5个文件
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
    parser = argparse.ArgumentParser(description="浙江OSS数据批量导入")
    parser.add_argument("--preview", action="store_true", help="预览模式")
    parser.add_argument("--limit", type=int, default=0, help="只处理前N个文件")
    parser.add_argument("--dir", default="data/oss_samples/zj_batch", help="XML文件目录")
    args = parser.parse_args()

    # 省份
    province = "浙江省通用安装工程预算定额(2018)"
    xml_dir = Path(args.dir)

    if not xml_dir.exists():
        logger.error(f"目录不存在: {xml_dir}")
        sys.exit(1)

    # Windows文件系统大小写不敏感，*.XML和*.xml会匹配同一批文件
    # 用set去重避免每个文件被处理两次
    xml_files = sorted(set(xml_dir.glob("*.XML")) | set(xml_dir.glob("*.xml")))
    if args.limit > 0:
        xml_files = xml_files[:args.limit]

    logger.info(f"待处理: {len(xml_files)}个文件, 省份: {province}")

    # 逐文件解析，合并所有pairs
    all_pairs = []
    file_stats = []
    start = time.time()

    for i, fpath in enumerate(xml_files):
        pairs = convert_xml_to_pairs(str(fpath))
        if pairs:
            all_pairs.extend(pairs)
            file_stats.append((fpath.name, len(pairs)))

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
        # 预览模式
        preview_import(unique_pairs, [province])
    else:
        # 正式导入（候选层）
        stats = preview_import(unique_pairs, [province])
        importable = stats['matched_pairs'] + stats['partial_pairs']
        if importable == 0:
            logger.error("没有可导入的数据")
            sys.exit(1)

        logger.info(f"\n开始导入 {importable} 条到候选层...")
        do_import(
            unique_pairs,
            project_name="浙江OSS批量导入",
            provinces=[province],
            only_matched=True,
            source="batch_import",  # 候选层
        )
        logger.info("批量导入完成!")


if __name__ == "__main__":
    main()
