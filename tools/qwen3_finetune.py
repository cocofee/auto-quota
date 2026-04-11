# -*- coding: utf-8 -*-
"""
Qwen3 embedding finetune utility.

Default behavior is conservative:
- train on triplets with LoRA
- keep the base model frozen
- use a low learning rate
- optionally upweight recall-miss samples
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, ".")


def load_triplets(jsonl_path: str, split: str | None = None) -> list[dict]:
    triplets: list[dict] = []
    with open(jsonl_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if split and row.get("split") != split:
                continue
            triplets.append(row)
    return triplets


def _boost_train_samples(train_data: list[dict], recall_boost: float) -> list[dict]:
    if recall_boost <= 1.0:
        return train_data

    boosted = list(train_data)
    whole = max(0, int(math.floor(recall_boost)) - 1)
    fractional = max(0.0, recall_boost - math.floor(recall_boost))

    recall_rows = [row for row in train_data if row.get("source_type") == "recall_miss"]
    if not recall_rows:
        return train_data

    for _ in range(whole):
        boosted.extend(recall_rows)

    if fractional > 0:
        rng = random.Random(42)
        for row in recall_rows:
            if rng.random() < fractional:
                boosted.append(row)

    return boosted


def _sample_train_data(train_data: list[dict], max_samples: int) -> list[dict]:
    if not max_samples or len(train_data) <= max_samples:
        return train_data

    rng = random.Random(42)
    by_province: dict[str, list[dict]] = defaultdict(list)
    for row in train_data:
        by_province[row.get("province", "unknown")].append(row)

    province_count = max(1, len(by_province))
    per_province_quota = max(1, max_samples // province_count)
    sampled: list[dict] = []
    remaining_budget = max_samples

    small = {k: v for k, v in by_province.items() if len(v) <= per_province_quota}
    large = {k: v for k, v in by_province.items() if len(v) > per_province_quota}

    for rows in small.values():
        sampled.extend(rows)
        remaining_budget -= len(rows)

    if large and remaining_budget > 0:
        per_large = max(1, remaining_budget // len(large))
        for rows in large.values():
            sampled.extend(rng.sample(rows, min(per_large, len(rows))))

    if len(sampled) > max_samples:
        sampled = rng.sample(sampled, max_samples)

    rng.shuffle(sampled)
    return sampled


def _print_sample_stats(label: str, rows: list[dict]) -> None:
    province_counts = Counter(row.get("province", "?")[:8] for row in rows)
    source_counts = Counter(row.get("source_type", "unknown") for row in rows)
    print(f"{label}: {len(rows)}")
    print(f"  source_type: {dict(source_counts)}")
    print(f"  province_top5: {dict(province_counts.most_common(5))}")


def _configure_lora(model, args) -> None:
    from peft import LoraConfig, TaskType, get_peft_model

    transformer = model._first_module()
    auto_model = transformer.auto_model

    lora_config = LoraConfig(
        task_type=TaskType.FEATURE_EXTRACTION,
        inference_mode=False,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=[item.strip() for item in args.lora_targets.split(",") if item.strip()],
    )
    transformer.auto_model = get_peft_model(auto_model, lora_config)
    if hasattr(transformer.auto_model, "print_trainable_parameters"):
        transformer.auto_model.print_trainable_parameters()


def _verify_sentence_transformer_dir(model_path: str) -> None:
    from sentence_transformers import SentenceTransformer

    print(f"verify saved model: {model_path}")
    verified = SentenceTransformer(model_path)
    test_emb = verified.encode(["test"], normalize_embeddings=True)
    print(f"  verified_dim: {len(test_emb[0])}")
    del verified


def _maybe_merge_lora(model, merge_output: str | None) -> None:
    if not merge_output:
        return

    from peft import PeftModel

    transformer = model._first_module()
    auto_model = transformer.auto_model
    if not isinstance(auto_model, PeftModel):
        print("[WARN] skip merge: current model is not a PEFT model")
        return

    print(f"merge LoRA adapter -> {merge_output}")
    transformer.auto_model = auto_model.merge_and_unload()
    model.save(merge_output)
    _verify_sentence_transformer_dir(merge_output)


def train(args) -> None:
    import torch
    from datasets import Dataset
    from sentence_transformers import (
        SentenceTransformer,
        SentenceTransformerTrainer,
        SentenceTransformerTrainingArguments,
        losses,
    )

    print(f"load triplets: {args.input}")
    train_data = load_triplets(args.input, split="train")
    val_data = load_triplets(args.input, split="val")

    if not train_data:
        print("[FAIL] empty train split")
        return

    _print_sample_stats("train before boost", train_data)

    if args.recall_boost > 1.0:
        train_data = _boost_train_samples(train_data, args.recall_boost)
        _print_sample_stats("train after boost", train_data)

    train_data = _sample_train_data(train_data, args.max_samples)
    _print_sample_stats("train final", train_data)

    if args.max_samples and val_data and len(val_data) > max(1, args.max_samples // 5):
        rng = random.Random(42)
        val_data = rng.sample(val_data, args.max_samples // 5)
    print(f"val final: {len(val_data)}")

    train_dataset = Dataset.from_dict({
        "anchor": [row["query"] for row in train_data],
        "positive": [row["positive"] for row in train_data],
        "negative": [row["negative"] for row in train_data],
    })
    val_dataset = None
    if val_data:
        val_dataset = Dataset.from_dict({
            "anchor": [row["query"] for row in val_data],
            "positive": [row["positive"] for row in val_data],
            "negative": [row["negative"] for row in val_data],
        })

    model_name = args.resume or args.model
    print(f"load model: {model_name}")

    model_kwargs = {}
    use_bf16 = torch.cuda.is_available()
    if use_bf16:
        model_kwargs["torch_dtype"] = "bfloat16"

    model = SentenceTransformer(
        model_name,
        model_kwargs=model_kwargs,
        tokenizer_kwargs={"padding_side": "left"},
    )
    model.max_seq_length = args.max_seq_length

    if args.use_lora:
        _configure_lora(model, args)
    else:
        print("[WARN] full finetune enabled")

    test_emb = model.encode(["测试"], normalize_embeddings=True)
    print(f"embedding_dim: {len(test_emb[0])}")
    print(f"device: {model.device}")

    train_loss = losses.MultipleNegativesRankingLoss(model)
    total_steps = max(1, len(train_dataset) // args.batch_size * args.epochs)
    warmup_steps = int(total_steps * args.warmup_ratio)

    training_args = SentenceTransformerTrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        warmup_steps=warmup_steps,
        bf16=use_bf16,
        fp16=False,
        logging_steps=args.logging_steps,
        eval_strategy="steps" if val_dataset else "no",
        eval_steps=args.eval_steps if val_dataset else None,
        save_steps=args.save_steps,
        save_total_limit=2,
        dataloader_num_workers=0,
        report_to="none",
    )

    print("start training")
    print(f"  epochs: {args.epochs}")
    print(f"  batch_size: {args.batch_size}")
    print(f"  grad_accum: {args.grad_accum}")
    print(f"  lr: {args.lr}")
    print(f"  warmup_steps: {warmup_steps}")
    print(f"  output: {args.output}")

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
    print(f"train finished in {elapsed / 3600:.2f}h")

    model.save(args.output)
    print(f"saved model: {args.output}")
    total_size = sum(path.stat().st_size for path in Path(args.output).rglob("*") if path.is_file())
    print(f"model_size_mb: {total_size / 1024 / 1024:.1f}")
    _verify_sentence_transformer_dir(args.output)

    _maybe_merge_lora(model, args.merge_output)


def quick_eval(args) -> None:
    import numpy as np
    from sentence_transformers import SentenceTransformer

    model_path = args.eval_model or args.merge_output or args.output
    if not Path(model_path).exists():
        print(f"[FAIL] model not found: {model_path}")
        return

    test_data = load_triplets(args.input, split="test")
    if not test_data:
        test_data = load_triplets(args.input, split="val")
    if not test_data:
        print("[WARN] no eval split found")
        return

    model = SentenceTransformer(model_path)
    correct_sims = []
    wrong_sims = []
    for row in test_data[: args.eval_limit]:
        embs = model.encode([row["query"], row["positive"], row["negative"]], normalize_embeddings=True)
        correct_sims.append(float(np.dot(embs[0], embs[1])))
        wrong_sims.append(float(np.dot(embs[0], embs[2])))

    avg_pos = float(np.mean(correct_sims))
    avg_neg = float(np.mean(wrong_sims))
    margin = avg_pos - avg_neg
    accuracy = float(np.mean([p > n for p, n in zip(correct_sims, wrong_sims)]))

    print("quick eval")
    print(f"  test_rows: {min(len(test_data), args.eval_limit)}")
    print(f"  avg_pos: {avg_pos:.4f}")
    print(f"  avg_neg: {avg_neg:.4f}")
    print(f"  margin: {margin:.4f}")
    print(f"  pair_acc: {accuracy:.1%}")

    if accuracy > 0.8:
        print("  [OK] pair discrimination looks healthy")
    elif accuracy > 0.6:
        print("  [WARN] pair discrimination is mediocre")
    else:
        print("  [FAIL] pair discrimination is poor")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Qwen3 embedding finetune")
    parser.add_argument("--input", type=str, default="data/qwen3_training_triplets.jsonl")
    parser.add_argument("--output", type=str, default="models/qwen3-embedding-quota")
    parser.add_argument("--model", type=str, default="Qwen/Qwen3-Embedding-0.6B")
    parser.add_argument("--resume", type=str, default=None)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--grad-accum", type=int, default=2)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--max-samples", type=int, default=12000)
    parser.add_argument("--max-seq-length", type=int, default=128)
    parser.add_argument("--warmup-ratio", type=float, default=0.1)
    parser.add_argument("--logging-steps", type=int, default=100)
    parser.add_argument("--eval-steps", type=int, default=500)
    parser.add_argument("--save-steps", type=int, default=500)
    parser.add_argument("--recall-boost", type=float, default=2.0)
    parser.add_argument("--use-lora", action="store_true", default=True)
    parser.add_argument("--full-finetune", action="store_true")
    parser.add_argument("--lora-r", type=int, default=8)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--lora-targets", type=str, default="q_proj,k_proj,v_proj,o_proj")
    parser.add_argument("--merge-output", type=str, default=None)
    parser.add_argument("--eval-only", action="store_true")
    parser.add_argument("--eval-model", type=str, default=None)
    parser.add_argument("--eval-limit", type=int, default=1000)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.full_finetune:
        args.use_lora = False

    if args.eval_only:
        quick_eval(args)
        return

    train(args)
    quick_eval(args)

    print("=" * 60)
    print("next")
    print("  1. run qwen3_eval.py for recall@k")
    print("  2. compare with current v3 baseline")
    print("  3. only swap pipeline after recall smoke test passes")
    print("=" * 60)


if __name__ == "__main__":
    main()
