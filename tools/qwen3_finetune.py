# -*- coding: utf-8 -*-
"""
Qwen3-Embedding-0.6B LoRA 微调训练器

用经验库三元组数据训练领域专用向量模型，替代通用BGE。

用法:
    python tools/qwen3_finetune.py                           # 默认参数训练
    python tools/qwen3_finetune.py --epochs 5 --batch-size 16  # 调参
    python tools/qwen3_finetune.py --resume models/qwen3-embedding-quota-lora  # 继续训练
    python tools/qwen3_finetune.py --merge-only               # 只做LoRA合并（不训练）

依赖:
    pip install sentence-transformers peft torch
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")


# ============================================================
# 1. 加载训练数据
# ============================================================

def load_triplets(jsonl_path: str, split: str = None) -> list[dict]:
    """从JSONL文件加载三元组数据"""
    triplets = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if split and rec.get("split") != split:
                continue
            triplets.append(rec)
    return triplets


# ============================================================
# 2. 训练
# ============================================================

def train(args):
    """LoRA微调训练主流程"""
    from sentence_transformers import (
        SentenceTransformer,
        SentenceTransformerTrainer,
        SentenceTransformerTrainingArguments,
        losses,
    )
    from datasets import Dataset

    # ----- 2.1 加载数据 -----
    print(f"加载训练数据: {args.input}")
    train_data = load_triplets(args.input, split="train")
    val_data = load_triplets(args.input, split="val")
    print(f"  训练集: {len(train_data)} 条三元组")
    print(f"  验证集: {len(val_data)} 条三元组")

    if not train_data:
        print("❌ 训练集为空")
        return

    # 数据采样（分层采样：按省份均衡，避免大省碾压小省）
    if args.max_samples and len(train_data) > args.max_samples:
        import random
        from collections import defaultdict
        random.seed(42)

        # 按省份分组
        by_province = defaultdict(list)
        for t in train_data:
            by_province[t.get("province", "unknown")].append(t)

        # 计算每个省份的配额（均分，但不超过该省实际数量）
        n_provinces = len(by_province)
        per_province_quota = args.max_samples // n_provinces

        sampled = []
        remaining_budget = args.max_samples
        # 第一轮：小省份全取，大省份按配额取
        small_provinces = {p: data for p, data in by_province.items() if len(data) <= per_province_quota}
        large_provinces = {p: data for p, data in by_province.items() if len(data) > per_province_quota}

        for p, data in small_provinces.items():
            sampled.extend(data)
            remaining_budget -= len(data)

        # 第二轮：大省份均分剩余预算
        if large_provinces and remaining_budget > 0:
            per_large = remaining_budget // len(large_provinces)
            for p, data in large_provinces.items():
                sampled.extend(random.sample(data, min(per_large, len(data))))

        train_data = sampled
        random.shuffle(train_data)

        # 打印分布
        from collections import Counter
        prov_counts = Counter(t.get("province", "?")[:6] for t in train_data)
        print(f"  分层采样后训练集: {len(train_data)} 条")
        print(f"  省份分布: {dict(prov_counts.most_common(5))}...")
    if args.max_samples and val_data and len(val_data) > args.max_samples // 5:
        import random
        random.seed(42)
        val_data = random.sample(val_data, args.max_samples // 5)
        print(f"  采样后验证集: {len(val_data)} 条")

    # 转为HuggingFace Dataset格式
    train_dataset = Dataset.from_dict({
        "anchor": [t["query"] for t in train_data],
        "positive": [t["positive"] for t in train_data],
        "negative": [t["negative"] for t in train_data],
    })
    val_dataset = None
    if val_data:
        val_dataset = Dataset.from_dict({
            "anchor": [t["query"] for t in val_data],
            "positive": [t["positive"] for t in val_data],
            "negative": [t["negative"] for t in val_data],
        })

    # ----- 2.2 加载模型 -----
    model_name = args.resume or args.model
    print(f"\n加载模型: {model_name}")

    model = SentenceTransformer(
        model_name,
        model_kwargs={"torch_dtype": "bfloat16"},  # Qwen3用bf16
        tokenizer_kwargs={"padding_side": "left"},
    )

    # 限制最大序列长度（清单+定额通常不超过128字，减少显存和加速）
    model.max_seq_length = 128

    # 检查模型维度
    test_emb = model.encode(["测试"], normalize_embeddings=True)
    print(f"  模型维度: {len(test_emb[0])}")
    print(f"  设备: {model.device}")

    # ----- 2.3 损失函数 -----
    # MultipleNegativesRankingLoss：拉近anchor和positive，推远anchor和negative
    train_loss = losses.MultipleNegativesRankingLoss(model)

    # ----- 2.4 训练参数 -----
    total_steps = len(train_dataset) // args.batch_size * args.epochs
    warmup_steps = int(total_steps * 0.1)

    training_args = SentenceTransformerTrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,  # 梯度累积：省显存，等效大batch
        learning_rate=args.lr,
        warmup_steps=warmup_steps,
        bf16=True,  # Qwen3用bf16（不是fp16，fp16会报grad scaler错误）
        logging_steps=100,
        eval_strategy="steps" if val_dataset else "no",
        eval_steps=1000 if val_dataset else None,
        save_steps=500,  # 每500步保存checkpoint（防崩溃丢进度）
        save_total_limit=3,
        dataloader_num_workers=0,  # Windows兼容
        report_to="none",  # 不上传到wandb
    )

    # ----- 2.5 开始训练 -----
    print(f"\n开始训练:")
    print(f"  epochs: {args.epochs}")
    print(f"  batch_size: {args.batch_size}")
    print(f"  lr: {args.lr}")
    print(f"  warmup_steps: {warmup_steps}")
    print(f"  total_steps: {total_steps}")
    print(f"  输出目录: {args.output}")

    trainer = SentenceTransformerTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=val_dataset,
        loss=train_loss,
    )

    start = time.time()
    trainer.train()
    elapsed = time.time() - start
    print(f"\n训练完成！耗时: {elapsed/3600:.1f} 小时")

    # ----- 2.6 保存模型 -----
    model.save(args.output)
    print(f"模型已保存: {args.output}")

    # 打印模型大小
    total_size = sum(f.stat().st_size for f in Path(args.output).rglob("*") if f.is_file())
    print(f"模型大小: {total_size / 1024 / 1024:.1f} MB")


# ============================================================
# 3. 快速验证
# ============================================================

def quick_eval(args):
    """训练完后的快速验证"""
    from sentence_transformers import SentenceTransformer
    import numpy as np

    model_path = args.output
    if not Path(model_path).exists():
        print(f"模型不存在: {model_path}")
        return

    print(f"\n加载微调后模型: {model_path}")
    model = SentenceTransformer(model_path)

    # 用测试集做简单评估
    test_data = load_triplets(args.input, split="test")
    if not test_data:
        test_data = load_triplets(args.input, split="val")
    if not test_data:
        print("没有测试数据，跳过评估")
        return

    print(f"测试集: {len(test_data)} 条")

    # 计算正样本和负样本的相似度
    correct_sims = []
    wrong_sims = []
    for t in test_data[:1000]:  # 最多测1000条
        embs = model.encode(
            [t["query"], t["positive"], t["negative"]],
            normalize_embeddings=True,
        )
        # 余弦相似度（归一化后就是点积）
        sim_pos = float(np.dot(embs[0], embs[1]))
        sim_neg = float(np.dot(embs[0], embs[2]))
        correct_sims.append(sim_pos)
        wrong_sims.append(sim_neg)

    avg_pos = np.mean(correct_sims)
    avg_neg = np.mean(wrong_sims)
    # 正样本相似度应该显著高于负样本
    margin = avg_pos - avg_neg
    # 准确率：正样本相似度 > 负样本相似度的比例
    accuracy = np.mean([p > n for p, n in zip(correct_sims, wrong_sims)])

    print(f"\n快速评估结果:")
    print(f"  正样本平均相似度: {avg_pos:.4f}")
    print(f"  负样本平均相似度: {avg_neg:.4f}")
    print(f"  margin (正-负):   {margin:.4f}")
    print(f"  区分准确率:       {accuracy:.1%}")

    if accuracy > 0.8:
        print(f"  ✅ 模型效果不错（准确率 > 80%）")
    elif accuracy > 0.6:
        print(f"  ⚠️ 模型效果一般，考虑增加epochs或调参")
    else:
        print(f"  ❌ 模型效果差，需要检查训练数据质量")


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Qwen3-Embedding LoRA微调训练器")
    parser.add_argument("--input", type=str, default="data/qwen3_training_triplets.jsonl",
                        help="训练数据JSONL路径")
    parser.add_argument("--output", type=str, default="models/qwen3-embedding-quota",
                        help="模型输出目录")
    parser.add_argument("--model", type=str, default="Qwen/Qwen3-Embedding-0.6B",
                        help="基座模型（HuggingFace ID或本地路径）")
    parser.add_argument("--resume", type=str, default=None,
                        help="继续训练的checkpoint路径")
    parser.add_argument("--epochs", type=int, default=3,
                        help="训练轮数（默认3）")
    parser.add_argument("--batch-size", type=int, default=16,
                        help="批大小（默认16，配合梯度累积等效32）")
    parser.add_argument("--grad-accum", type=int, default=2,
                        help="梯度累积步数（默认2，等效batch=batch_size×grad_accum）")
    parser.add_argument("--lr", type=float, default=2e-4,
                        help="学习率（默认2e-4）")
    parser.add_argument("--max-samples", type=int, default=100000,
                        help="最大训练样本数（默认10万，设0不采样）")
    parser.add_argument("--eval-only", action="store_true",
                        help="只做评估不训练")
    args = parser.parse_args()

    if args.eval_only:
        quick_eval(args)
        return

    # 训练
    train(args)

    # 训练完自动做快速评估
    quick_eval(args)

    print(f"\n{'='*60}")
    print("下一步:")
    print(f"  1. 运行评测: python tools/qwen3_eval.py")
    print(f"  2. 对比BGE:  看Recall@10是否提升")
    print(f"  3. 效果好就替换搜索管线")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
