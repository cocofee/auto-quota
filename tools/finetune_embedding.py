#!/usr/bin/env python
"""微调 BGE 向量模型 — 用 OSS 人工匹配数据提升建筑领域召回."""

import argparse, json, sqlite3, sys
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DB = PROJECT_ROOT / "db" / "common" / "experience.db"
DEFAULT_OUTPUT = PROJECT_ROOT / "models" / "bge-construction-v1"
DEFAULT_MODEL = "BAAI/bge-large-zh-v1.5"


def load_pairs(db_path: Path, source: str = "oss_import", limit: int = 0) -> list[tuple[str, str]]:
    """从经验库加载 (清单名, 定额名) 配对."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT bill_name, quota_names FROM experiences WHERE source = ? AND bill_name != ''",
        (source,)
    ).fetchall()
    conn.close()

    pairs = []
    for bill_name, quota_names_json in rows:
        quota_list = json.loads(quota_names_json) if quota_names_json else []
        for quota_name in quota_list:
            if bill_name.strip() and quota_name.strip():
                pairs.append((bill_name.strip(), quota_name.strip()))

    if limit and len(pairs) > limit:
        pairs = pairs[:limit]
    return pairs


def dedupe_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """去重: 相同 (bill, quota) 只保留一条."""
    seen = set()
    result = []
    for b, q in pairs:
        key = (b, q)
        if key not in seen:
            seen.add(key)
            result.append((b, q))
    return result


def train(args):
    from sentence_transformers import SentenceTransformer, InputExample, losses
    from torch.utils.data import DataLoader

    print(f"加载配对数据: {args.db_path}")
    pairs = load_pairs(args.db_path, source=args.source, limit=args.limit)
    pairs = dedupe_pairs(pairs)
    print(f"  有效配对: {len(pairs)} (去重后)")

    if len(pairs) < 1000:
        print("  配对数量不足 (< 1000), 无法训练")
        return

    print(f"加载基础模型: {args.base_model}")
    model = SentenceTransformer(args.base_model)

    examples = [InputExample(texts=[bill, quota]) for bill, quota in pairs]
    loader = DataLoader(examples, batch_size=args.batch_size, shuffle=True)
    loss = losses.MultipleNegativesRankingLoss(model)

    print(f"开始训练: {args.epochs} epochs, batch_size={args.batch_size}")
    model.fit(
        train_objectives=[(loader, loss)],
        epochs=args.epochs,
        warmup_steps=int(len(examples) * 0.1 / args.batch_size),
        output_path=str(args.output_dir),
        save_best_model=True,
        show_progress_bar=True,
    )
    print(f"模型已保存: {args.output_dir}")


def main():
    p = argparse.ArgumentParser(description="微调 BGE 向量模型用于建筑领域")
    p.add_argument("--db-path", default=str(DEFAULT_DB), help="经验库路径")
    p.add_argument("--source", default="oss_import", help="经验来源过滤")
    p.add_argument("--limit", type=int, default=0, help="限制训练样本数 (0=全部)")
    p.add_argument("--base-model", default=DEFAULT_MODEL, help="基础模型名")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT), help="输出目录")
    p.add_argument("--epochs", type=int, default=3, help="训练轮数")
    p.add_argument("--batch-size", type=int, default=16, help="批次大小")
    args = p.parse_args()
    train(args)


if __name__ == "__main__":
    main()
