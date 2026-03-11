#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Qwen3 索引重建工具（Phase 5：蓝绿切换）

功能：用 Qwen3-Embedding 微调模型重新编码全部向量索引
- 定额库：211个省份定额库，约149万条
- 经验库：10.2万条
- 通用知识库：4.2万条

蓝绿策略：
- 旧BGE索引保留在 db/chroma/{hash}_quota/ 和 db/chroma/common_*/
- 新Qwen3索引写入 db/chroma/qwen3/{hash}_quota/ 等
- 切换只需改 .env 的 VECTOR_MODEL_KEY=qwen3，回退改回 bge

用法：
    python tools/rebuild_index_qwen3.py              # 重建全部
    python tools/rebuild_index_qwen3.py --quota-only  # 只重建定额库
    python tools/rebuild_index_qwen3.py --skip-quota   # 跳过定额库（只重建经验+知识库）
"""
from __future__ import annotations

import os
import sys
import time
import argparse

# 强制设置 Qwen3 模型
os.environ["VECTOR_MODEL_KEY"] = "qwen3"

# 项目根目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
import config


def rebuild_quota_indices():
    """重建所有省份的定额向量索引"""
    from src.vector_engine import VectorEngine

    # 扫描所有省份目录
    provinces_dir = config.DB_DIR / "provinces"
    if not provinces_dir.exists():
        logger.error(f"省份目录不存在: {provinces_dir}")
        return

    provinces = sorted([
        d.name for d in provinces_dir.iterdir()
        if d.is_dir() and d.name != "test" and (d / "quota.db").exists()
    ])
    logger.info(f"发现 {len(provinces)} 个省份定额库")

    total_records = 0
    success_count = 0
    fail_count = 0
    t_start = time.time()

    for i, province in enumerate(provinces, 1):
        logger.info(f"\n{'='*60}")
        logger.info(f"[{i}/{len(provinces)}] 重建定额索引: {province}")
        logger.info(f"{'='*60}")

        engine = None
        try:
            engine = VectorEngine(province=province)

            # 先检查记录数
            conn = engine._connect(row_factory=True)
            try:
                cursor = conn.cursor()
                n = cursor.execute(
                    "SELECT COUNT(*) as cnt FROM quotas WHERE search_text IS NOT NULL"
                ).fetchone()["cnt"]
            finally:
                conn.close()

            if n == 0:
                logger.warning(f"  跳过（无定额数据）")
                continue

            logger.info(f"  定额数: {n}条")
            t0 = time.time()

            # 用较大batch提高GPU利用率（Qwen3 0.6B显存占用小）
            engine.build_index(batch_size=512)

            elapsed = time.time() - t0
            speed = n / elapsed if elapsed > 0 else 0
            logger.info(f"  完成: {n}条, {elapsed:.1f}秒 ({speed:.0f}条/秒)")

            total_records += n
            success_count += 1

        except Exception as e:
            logger.error(f"  失败: {e}")
            fail_count += 1
        finally:
            # 释放Chroma client缓存，避免211个省份句柄累积（Codex H1）
            # 必须先 clear_system_cache() 让HNSW索引flush到磁盘，
            # 否则只有SQLite数据没有header.bin等文件，查询会报"Cannot open header file"
            if engine is not None:
                import gc
                from src.model_cache import ModelCache
                chroma_path = str(engine.chroma_dir)
                if chroma_path in ModelCache._chroma_clients:
                    try:
                        client = ModelCache._chroma_clients[chroma_path]
                        client.clear_system_cache()  # flush HNSW到磁盘
                        del ModelCache._chroma_clients[chroma_path]
                        del client
                        gc.collect()  # 触发析构，确保文件写入
                    except Exception:
                        pass

    elapsed_total = time.time() - t_start
    logger.info(f"\n{'='*60}")
    logger.info(f"定额索引重建完成:")
    logger.info(f"  成功: {success_count}个省份, 共{total_records}条")
    logger.info(f"  失败: {fail_count}个省份")
    logger.info(f"  耗时: {elapsed_total/60:.1f}分钟")
    logger.info(f"{'='*60}")


def rebuild_experience_index():
    """重建经验库向量索引"""
    from src.experience_db import ExperienceDB

    logger.info(f"\n{'='*60}")
    logger.info("重建经验库向量索引")
    logger.info(f"{'='*60}")

    t0 = time.time()
    try:
        db = ExperienceDB()
        # 经验库的重建方法在 experience_importer 里作为 mixin
        # 直接调用 rebuild_vector_index
        db.rebuild_vector_index()
        elapsed = time.time() - t0
        logger.info(f"经验库索引重建完成: {elapsed:.1f}秒")
    except Exception as e:
        logger.error(f"经验库索引重建失败: {e}")
        import traceback
        traceback.print_exc()


def rebuild_universal_kb_index():
    """重建通用知识库向量索引"""
    from src.universal_kb import UniversalKB

    logger.info(f"\n{'='*60}")
    logger.info("重建通用知识库向量索引")
    logger.info(f"{'='*60}")

    t0 = time.time()
    try:
        kb = UniversalKB()
        kb.rebuild_vector_index()
        elapsed = time.time() - t0
        logger.info(f"通用知识库索引重建完成: {elapsed:.1f}秒")
    except Exception as e:
        logger.error(f"通用知识库索引重建失败: {e}")
        import traceback
        traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="Qwen3向量索引重建工具")
    parser.add_argument("--quota-only", action="store_true", help="只重建定额库索引")
    parser.add_argument("--skip-quota", action="store_true", help="跳过定额库（只重建经验+知识库）")
    parser.add_argument("--exp-only", action="store_true", help="只重建经验库索引")
    parser.add_argument("--kb-only", action="store_true", help="只重建通用知识库索引")
    args = parser.parse_args()

    logger.info(f"Qwen3索引重建工具启动")
    logger.info(f"VECTOR_MODEL_KEY = {os.environ.get('VECTOR_MODEL_KEY')}")
    logger.info(f"索引输出目录: db/chroma/qwen3/")

    # 先预加载模型（避免每个省份重复加载）
    from src.model_cache import ModelCache
    logger.info("预加载Qwen3模型...")
    model = ModelCache.get_vector_model()
    if model is None:
        logger.error("Qwen3模型加载失败，无法重建索引")
        sys.exit(1)
    logger.info("模型加载完成")

    t_global = time.time()

    if args.quota_only:
        rebuild_quota_indices()
    elif args.exp_only:
        rebuild_experience_index()
    elif args.kb_only:
        rebuild_universal_kb_index()
    elif args.skip_quota:
        rebuild_experience_index()
        rebuild_universal_kb_index()
    else:
        # 全部重建
        rebuild_quota_indices()
        rebuild_experience_index()
        rebuild_universal_kb_index()

    total_elapsed = time.time() - t_global
    logger.info(f"\n全部完成! 总耗时: {total_elapsed/60:.1f}分钟")

    # 输出新索引目录大小（跨平台，不依赖Unix du命令）
    qwen3_dir = config.DB_DIR / "chroma" / "qwen3"
    if qwen3_dir.exists():
        total_size = sum(
            f.stat().st_size for f in qwen3_dir.rglob("*") if f.is_file()
        )
        # 转为可读格式
        if total_size >= 1024 ** 3:
            size_str = f"{total_size / 1024**3:.1f}GB"
        elif total_size >= 1024 ** 2:
            size_str = f"{total_size / 1024**2:.1f}MB"
        else:
            size_str = f"{total_size / 1024:.0f}KB"
        logger.info(f"新索引目录大小: {size_str}")


if __name__ == "__main__":
    main()
