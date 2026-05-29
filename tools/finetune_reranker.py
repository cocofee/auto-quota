#!/usr/bin/env python
"""微调 CrossEncoder 重排序模型 — 用 OSS 数据提升建筑领域候选区分力."""

import argparse, json, sqlite3, sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DB = PROJECT_ROOT / "db" / "common" / "experience.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "bge-reranker-construction-v1"
DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"

NEGATIVE_RATIO = 3  # 每个正样本配几个负样本


def load_samples(db_path: Path, source: str = "oss_import", limit: int = 0) -> list[tuple[str, str, float]]:
    """加载训练样本: (清单, 定额名, label). 正样本=1.0, 负样本=0.0."""
    from collections import defaultdict
    import random
    random.seed(42)

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT bill_name, quota_names FROM experiences WHERE source = ? AND bill_name != ''",
        (source,)
    ).fetchall()
    conn.close()

    # 收集每个清单的所有正确定额
    bill_positives = defaultdict(set)
    all_quotas = set()
    for bill_name, quota_names_json in rows:
        quota_list = json.loads(quota_names_json) if quota_names_json else []
        for q in quota_list:
            if bill_name.strip() and q.strip():
                bill_positives[bill_name.strip()].add(q.strip())
                all_quotas.add(q.strip())

    all_quotas = list(all_quotas)

    samples = []
    for bill, positives in bill_positives.items():
        for pos in positives:
            samples.append((bill, pos, 1.0))
        # 随机采样负样本 (其他清单的正确定额, 对这个清单来说是错的)
        neg_pool = [q for q in all_quotas if q not in positives]
        neg_count = min(len(positives) * NEGATIVE_RATIO, len(neg_pool))
        if neg_count > 0:
            for neg in random.sample(neg_pool, neg_count):
                samples.append((bill, neg, 0.0))

    if limit and len(samples) > limit:
        samples = samples[:limit]
    return samples


def train(args):
    from sentence_transformers import CrossEncoder

    print(f"加载训练样本: {args.db_path}")
    samples = load_samples(args.db_path, source=args.source, limit=args.limit)
    pos = sum(1 for _, _, l in samples if l > 0.5)
    neg = sum(1 for _, _, l in samples if l < 0.5)
    print(f"  正样本: {pos}, 负样本: {neg}, 总计: {len(samples)}")

    if len(samples) < 1000:
        print("  样本不足 (< 1000), 无法训练")
        return

    print(f"加载基础模型: {args.base_model}")
    model = CrossEncoder(args.base_model, num_labels=1)

    print(f"开始训练: {args.epochs} epochs, batch_size={args.batch_size}")
    model.fit(
        
        train_samples=samples,
        epochs=args.epochs,
        warmup_steps=int(len(samples) * 0.1 / args.batch_size),
        output_path=str(args.output_dir),
        save_best_model=True,
        show_progress_bar=True,
    )
    print(f"模型已保存: {args.output_dir}")


def main():
    p = argparse.ArgumentParser(description="微调 CrossEncoder 重排序模型")
    p.add_argument("--db-path", default=str(DEFAULT_DB), help="经验库路径")
    p.add_argument("--source", default="oss_import", help="经验来源过滤")
    p.add_argument("--limit", type=int, default=0, help="限制训练样本数")
    p.add_argument("--base-model", default=DEFAULT_MODEL, help="基础模型名")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT), help="输出目录")
    p.add_argument("--epochs", type=int, default=2, help="训练轮数")
    p.add_argument("--batch-size", type=int, default=16, help="批次大小")
    args = p.parse_args()
    train(args)


if __name__ == "__main__":
    main()
