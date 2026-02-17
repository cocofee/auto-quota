"""
经验库去重迁移脚本
功能：
1. 用最新的 normalize_bill_text 重新计算所有记录的 bill_text
2. 按新 bill_text 分组，合并重复记录（保留最佳，合并定额列表）
3. 删除多余记录
4. 重建 ChromaDB 向量索引

运行方式：
    python tools/migrate_experience_dedup.py           # 预览模式（只看不改）
    python tools/migrate_experience_dedup.py --apply    # 执行迁移
"""

import json
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import config
from src.text_parser import normalize_bill_text


# 来源的优先级：user_confirmed > user_correction > project_import > auto_match
SOURCE_PRIORITY = {
    "user_confirmed": 4,
    "user_correction": 3,
    "project_import": 2,
    "auto_match": 1,
}


def get_all_records(db_path):
    """读取所有权威层记录"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM experiences WHERE layer='authority'")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def compute_new_text(record):
    """用最新的 normalize_bill_text 重新计算 bill_text"""
    old_text = record["bill_text"]
    name = record.get("bill_name") or ""

    # 从旧text中提取description部分
    # 旧text = name + " " + description（各字段用空格连接）
    if name and old_text.startswith(name):
        desc = old_text[len(name):].strip()
    else:
        desc = old_text

    new_text = normalize_bill_text(name, desc)
    # 如果normalize结果为空（不应该发生），保留原文
    return new_text if new_text else old_text


def merge_quota_ids(records):
    """合并多条记录的定额编号列表（去重、保持顺序）

    注意：带 R* 换算系数的定额编号（如 "C10-2-3 R*1.1"）暂时去掉，
    以后统一处理换算系数。
    """
    import re
    seen = set()
    merged_ids = []
    merged_names = []

    # 按优先级排序：优先取高信任来源的定额顺序
    sorted_records = sorted(
        records,
        key=lambda r: (-SOURCE_PRIORITY.get(r["source"], 0), -r["confidence"])
    )

    for r in sorted_records:
        qids = json.loads(r["quota_ids"]) if isinstance(r["quota_ids"], str) else r["quota_ids"]
        qnames = json.loads(r.get("quota_names") or "[]") if isinstance(r.get("quota_names", "[]"), str) else (r.get("quota_names") or [])

        for i, qid in enumerate(qids):
            # 跳过带换算系数的编号（如 "C10-2-3 R*1.1"），以后统一处理
            if re.search(r'\s+R\*', qid):
                continue
            if qid not in seen:
                seen.add(qid)
                merged_ids.append(qid)
                qname = qnames[i] if i < len(qnames) else ""
                merged_names.append(qname)

    return merged_ids, merged_names


def pick_best_record(records):
    """从一组重复记录中选出最佳记录（保留这条，其他删除）

    选择策略：
    1. 优先选 user_confirmed/user_correction（人工验证过的最可靠）
    2. 同来源选 confidence 最高的
    3. 同分的选 confirm_count 最多的
    """
    return max(
        records,
        key=lambda r: (
            SOURCE_PRIORITY.get(r["source"], 0),
            r["confidence"],
            r.get("confirm_count", 1),
        )
    )


def run_migration(apply=False):
    """执行迁移"""
    db_path = config.get_experience_db_path()
    records = get_all_records(db_path)

    print(f"权威层总记录: {len(records)}")
    print()

    # 第1步：用新normalize重算bill_text并分组
    groups = defaultdict(list)
    text_changed = 0

    for r in records:
        new_text = compute_new_text(r)
        if new_text != r["bill_text"]:
            text_changed += 1
        r["_new_text"] = new_text
        groups[new_text].append(r)

    print(f"text有变化的记录: {text_changed}")
    print(f"去重后分组数: {len(groups)}")
    print(f"将删除记录数: {len(records) - len(groups)}")
    print()

    # 第2步：分析每组，决定保留哪条、如何合并
    actions = []  # (keep_record, merged_qids, merged_qnames, delete_ids, new_text)

    for new_text, group_records in groups.items():
        if len(group_records) == 1:
            # 只有一条记录，只需更新text
            r = group_records[0]
            qids = json.loads(r["quota_ids"]) if isinstance(r["quota_ids"], str) else r["quota_ids"]
            qnames = json.loads(r.get("quota_names") or "[]") if isinstance(r.get("quota_names", "[]"), str) else (r.get("quota_names") or [])
            actions.append((r, qids, qnames, [], new_text))
        else:
            # 多条重复记录
            best = pick_best_record(group_records)
            merged_ids, merged_names = merge_quota_ids(group_records)
            delete_ids = [r["id"] for r in group_records if r["id"] != best["id"]]
            actions.append((best, merged_ids, merged_names, delete_ids, new_text))

    # 统计
    total_delete = sum(len(a[3]) for a in actions)
    total_update = sum(1 for a in actions if a[4] != a[0]["bill_text"] or a[3])

    print(f"操作统计:")
    print(f"  更新text/合并定额: {total_update} 条")
    print(f"  删除重复记录: {total_delete} 条")
    print(f"  最终保留记录: {len(actions)} 条")
    print()

    # 展示删除/合并的详情（前20条）
    merge_actions = [(a[0], a[1], a[3], a[4]) for a in actions if a[3]]
    print(f"=== 合并详情（共{len(merge_actions)}组）===")
    for i, (best, merged_ids, delete_ids, new_text) in enumerate(merge_actions[:20]):
        print(f"  [{i+1}] 保留ID={best['id']}(source={best['source']}, conf={best['confidence']})")
        print(f"       text: {new_text[:70]}")
        print(f"       合并定额: {merged_ids}")
        print(f"       删除IDs: {delete_ids}")
    if len(merge_actions) > 20:
        print(f"  ... 还有{len(merge_actions)-20}组未显示")
    print()

    if not apply:
        print(">>> 预览模式，不实际修改。加 --apply 参数执行迁移。")
        return

    # 第3步：执行迁移
    print("开始执行迁移...")
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    updated = 0
    deleted = 0

    for best, merged_ids, merged_names, delete_ids, new_text in actions:
        # 更新保留记录的text和定额列表
        cur.execute("""
            UPDATE experiences SET
                bill_text = ?,
                quota_ids = ?,
                quota_names = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            new_text,
            json.dumps(merged_ids, ensure_ascii=False),
            json.dumps(merged_names, ensure_ascii=False),
            time.time(),
            best["id"],
        ))
        updated += 1

        # 删除重复记录
        for did in delete_ids:
            cur.execute("DELETE FROM experiences WHERE id = ?", (did,))
            deleted += 1

    conn.commit()
    conn.close()

    print(f"SQLite迁移完成: 更新{updated}条, 删除{deleted}条")

    # 第4步：重建ChromaDB向量索引
    print("重建ChromaDB向量索引...")
    rebuild_chroma_index(db_path)

    # 第5步：候选层也去重（用同样逻辑）
    print("处理候选层...")
    dedup_candidate_layer(db_path)

    print()
    print("迁移完成!")

    # 验证
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM experiences WHERE layer="authority"')
    auth_count = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM experiences WHERE layer="candidate"')
    cand_count = cur.fetchone()[0]
    conn.close()
    print(f"  权威层: {auth_count} 条")
    print(f"  候选层: {cand_count} 条")


def dedup_candidate_layer(db_path):
    """候选层去重（逻辑同权威层）"""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM experiences WHERE layer='candidate'")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not rows:
        print("  候选层无记录，跳过")
        return

    groups = defaultdict(list)
    for r in rows:
        new_text = compute_new_text(r)
        r["_new_text"] = new_text
        groups[new_text].append(r)

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    deleted = 0
    updated = 0

    for new_text, group_records in groups.items():
        best = pick_best_record(group_records)
        merged_ids, merged_names = merge_quota_ids(group_records)

        # 检查权威层是否已有此text的记录，如果有则删除候选层记录
        cur.execute("SELECT id FROM experiences WHERE bill_text=? AND layer='authority' LIMIT 1", (new_text,))
        auth_exists = cur.fetchone()

        if auth_exists:
            # 权威层已有，候选层全删
            for r in group_records:
                cur.execute("DELETE FROM experiences WHERE id=?", (r["id"],))
                deleted += 1
        else:
            # 权威层没有，保留最佳候选，删其余
            cur.execute("""
                UPDATE experiences SET
                    bill_text = ?,
                    quota_ids = ?,
                    quota_names = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                new_text,
                json.dumps(merged_ids, ensure_ascii=False),
                json.dumps(merged_names, ensure_ascii=False),
                time.time(),
                best["id"],
            ))
            updated += 1
            for r in group_records:
                if r["id"] != best["id"]:
                    cur.execute("DELETE FROM experiences WHERE id=?", (r["id"],))
                    deleted += 1

    conn.commit()
    conn.close()
    print(f"  候选层: 更新{updated}条, 删除{deleted}条")


def rebuild_chroma_index(db_path):
    """重建ChromaDB向量索引（全部权威层记录重新向量化）"""
    import chromadb

    # 读取所有权威层记录
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id, bill_text FROM experiences WHERE layer='authority'")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not rows:
        print("  无记录，跳过重建")
        return

    # 删除旧collection并重建
    chroma_dir = config.get_chroma_experience_dir()
    chroma_dir.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_dir))

    # 删除旧的
    try:
        client.delete_collection("experiences")
    except Exception:
        pass

    # 创建新的
    collection = client.get_or_create_collection(
        name="experiences",
        metadata={"hnsw:space": "cosine"}
    )

    # 批量向量化
    from sentence_transformers import SentenceTransformer
    try:
        model = SentenceTransformer(config.VECTOR_MODEL_NAME, device="cuda")
    except Exception:
        model = SentenceTransformer(config.VECTOR_MODEL_NAME, device="cpu")

    texts = [r["bill_text"] for r in rows]
    ids = [str(r["id"]) for r in rows]

    # 分批处理（避免内存溢出）
    batch_size = 100
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i+batch_size]
        batch_ids = ids[i:i+batch_size]

        embeddings = model.encode(batch_texts, normalize_embeddings=True)
        collection.upsert(
            ids=batch_ids,
            documents=batch_texts,
            embeddings=embeddings.tolist(),
        )
        print(f"  向量化进度: {min(i+batch_size, len(texts))}/{len(texts)}")

    print(f"  ChromaDB重建完成: {len(texts)} 条向量")


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    run_migration(apply=apply)
