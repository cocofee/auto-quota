# -*- coding: utf-8 -*-
"""
批量重建所有省份的BM25索引和向量索引
用法：
  python tools/_rebuild_all_indexes.py              # 只建缺失的
  python tools/_rebuild_all_indexes.py --force       # 全部重建（含已有的）
  python tools/_rebuild_all_indexes.py --bm25-only   # 只建BM25（快，不用GPU）
"""
import sys
import os
import time
import argparse

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
from pathlib import Path


def get_all_provinces():
    """获取所有有quota.db的省份"""
    provinces_dir = config.PROVINCES_DB_DIR
    result = []
    for d in sorted(provinces_dir.iterdir()):
        if d.is_dir() and (d / "quota.db").exists():
            # 跳过test目录
            if d.name in ("test", "db"):
                continue
            result.append(d.name)
    return result


def build_bm25(province, force=False):
    """构建BM25索引"""
    from src.bm25_engine import BM25Engine

    db_dir = config.PROVINCES_DB_DIR / province
    index_path = db_dir / "bm25_index.json"

    if index_path.exists() and not force:
        return "跳过(已有)"

    try:
        engine = BM25Engine(province)
        engine.build_index()
        return "成功"
    except Exception as e:
        return f"失败: {e}"


def build_vector(province, force=False):
    """构建向量索引"""
    from src.vector_engine import VectorEngine

    chroma_dir = config.get_chroma_quota_dir(province)
    if chroma_dir.exists() and not force:
        return "跳过(已有)"

    try:
        engine = VectorEngine(province)
        engine.build_index()
        return "成功"
    except Exception as e:
        return f"失败: {e}"


def main():
    parser = argparse.ArgumentParser(description="批量重建索引")
    parser.add_argument("--force", action="store_true", help="强制重建所有（含已有的）")
    parser.add_argument("--bm25-only", action="store_true", help="只建BM25索引")
    parser.add_argument("--vector-only", action="store_true", help="只建向量索引")
    args = parser.parse_args()

    provinces = get_all_provinces()
    print("=" * 60)
    print(f"批量重建索引")
    print(f"共 {len(provinces)} 个定额库")
    print(f"模式: {'强制重建' if args.force else '只建缺失的'}")
    if args.bm25_only:
        print("类型: 仅BM25")
    elif args.vector_only:
        print("类型: 仅向量")
    else:
        print("类型: BM25 + 向量")
    print("=" * 60)

    start_time = time.time()
    results = []

    for i, province in enumerate(provinces, 1):
        print(f"\n[{i}/{len(provinces)}] {province}")
        t0 = time.time()

        bm25_status = "-"
        vector_status = "-"

        if not args.vector_only:
            bm25_status = build_bm25(province, force=args.force)
            print(f"  BM25: {bm25_status}")

        if not args.bm25_only:
            vector_status = build_vector(province, force=args.force)
            print(f"  向量: {vector_status}")

        elapsed = time.time() - t0
        results.append((province, bm25_status, vector_status, elapsed))
        print(f"  耗时: {elapsed:.1f}秒")

    total_elapsed = time.time() - start_time

    # 汇总
    print(f"\n{'='*60}")
    print(f"汇总")
    print(f"{'='*60}")

    bm25_ok = sum(1 for _, b, _, _ in results if b == "成功")
    bm25_skip = sum(1 for _, b, _, _ in results if b.startswith("跳过"))
    bm25_fail = sum(1 for _, b, _, _ in results if b.startswith("失败"))

    vec_ok = sum(1 for _, _, v, _ in results if v == "成功")
    vec_skip = sum(1 for _, _, v, _ in results if v.startswith("跳过"))
    vec_fail = sum(1 for _, _, v, _ in results if v.startswith("失败"))

    if not args.vector_only:
        print(f"BM25: 成功{bm25_ok} 跳过{bm25_skip} 失败{bm25_fail}")
    if not args.bm25_only:
        print(f"向量: 成功{vec_ok} 跳过{vec_skip} 失败{vec_fail}")

    # 列出失败的
    failures = [(p, b, v) for p, b, v, _ in results if b.startswith("失败") or v.startswith("失败")]
    if failures:
        print(f"\n失败列表:")
        for p, b, v in failures:
            if b.startswith("失败"):
                print(f"  BM25 {p}: {b}")
            if v.startswith("失败"):
                print(f"  向量 {p}: {v}")

    print(f"\n总耗时: {total_elapsed:.0f}秒 ({total_elapsed/60:.1f}分钟)")


if __name__ == "__main__":
    main()
