"""
经验库 — 批量导入模块

从 experience_db.py 拆分出来，负责：
1. import_from_project: 从已完成项目批量导入经验（含审核规则检查）
2. rebuild_vector_index: 重建向量索引（数据不同步时手动触发）

使用方式（方法重绑定，调用方无需感知拆分）：
    from src.experience_db import ExperienceDB
    db = ExperienceDB("北京2024")
    db.import_from_project(records, ...)  # 和拆分前一样调用
"""

import os

import config
from loguru import logger
from src.specialty_classifier import get_book_from_quota_id


def import_from_project(self, records: list[dict],
                        project_name: str = None,
                        province: str = None,
                        enabled_checkers: list = None) -> dict:
    """
    从已完成项目批量导入经验

    导入时会对每条记录跑审核规则检查（review_checkers），
    通过的写权威层，不通过的降级到候选层（等人工审核后再晋升）。

    参数:
        records: 导入记录列表，每条包含：
            {
                "bill_text": "清单文本",
                "bill_name": "项目名称",
                "bill_code": "清单编码",
                "bill_unit": "单位",
                "quota_ids": ["定额编号1", "定额编号2"],
                "quota_names": ["定额名称1", "定额名称2"],
            }
        project_name: 项目名称（标记来源）
        province: 省份
        enabled_checkers: 启用的审核规则函数名列表（如 ["check_category_mismatch"]），
            None=启用全部规则（默认行为不变）

    返回:
        {"total": 总数, "added": 新增数, "updated": 更新数,
         "skipped": 跳过数, "suspect": 降级数}
    """
    province = province or self.province
    quota_db_ver = config.get_current_quota_version(province)
    stats = {"total": len(records), "added": 0, "updated": 0,
             "skipped": 0, "suspect": 0}

    # 延迟导入审核检测器（避免循环依赖）
    try:
        from src.review_checkers import (
            check_category_mismatch, check_material_mismatch,
            check_connection_mismatch, check_sleeve_mismatch,
            check_electric_pair, extract_description_lines,
        )
        all_checkers = [
            check_category_mismatch, check_material_mismatch,
            check_connection_mismatch, check_sleeve_mismatch,
            check_electric_pair,
        ]
        # 按名称过滤（enabled_checkers=None表示全部启用）
        if enabled_checkers is not None:
            all_checkers = [c for c in all_checkers
                            if c.__name__ in enabled_checkers]
        has_checkers = bool(all_checkers)
    except ImportError:
        has_checkers = False
        logger.warning("审核检测器不可用，导入时跳过规则检查")

    for record in records:
        bill_text = record.get("bill_text", "").strip()
        quota_ids = record.get("quota_ids", [])

        if not bill_text or not quota_ids:
            stats["skipped"] += 1
            continue

        # 导入时规范化文本（去掉废话、空值字段等，统一格式）
        # 优先使用 record 中的 bill_desc/description 字段（与 import_reference.py 一致），
        # fallback 才用字符串截取
        try:
            from src.text_parser import normalize_bill_text
            bill_name = record.get("bill_name", "")
            bill_desc = record.get("bill_desc", "") or record.get("description", "")
            if not bill_desc and bill_name and bill_text.startswith(bill_name):
                bill_desc = bill_text[len(bill_name):].strip()
            if bill_name:
                bill_text = normalize_bill_text(bill_name, bill_desc)
        except Exception as e:
            logger.debug(f"经验导入文本规范化失败，使用原文本: {e}")

        # ========== 导入时审核：跑规则检查，有问题的降级到候选层 ==========
        is_suspect = False
        if has_checkers and quota_ids:
            quota_name = record.get("quota_names", [""])[0] if record.get("quota_names") else ""
            item = {"name": record.get("bill_name", "")}
            desc_text = record.get("description", "")
            if not desc_text:
                # 从 bill_text 中提取描述部分（去掉名称前缀）
                bname = record.get("bill_name", "")
                desc_text = bill_text[len(bname):].strip() if bname and bill_text.startswith(bname) else ""
            desc_lines = extract_description_lines(desc_text)

            # 跑审核规则（按 enabled_checkers 配置过滤）
            for checker in all_checkers:
                try:
                    # check_electric_pair 只接受 (item, quota_name, desc_lines)
                    result = checker(item, quota_name, desc_lines)
                    if result:
                        is_suspect = True
                        logger.debug(
                            f"导入审核降级: '{bill_text[:40]}' → {quota_ids} "
                            f"原因: {result.get('reason', '?')}"
                        )
                        break
                except Exception:
                    pass  # 规则检查出错不阻塞导入

        # 确定来源：审核通过=project_import(权威层)，不通过=project_import_suspect(候选层)
        source = "project_import_suspect" if is_suspect else "project_import"

        # 检查是否已存在
        existing = self._find_exact_match(bill_text, province)
        if existing:
            # 已有记录，增加确认次数（同时补全空的specialty）
            inferred_spec = None
            if quota_ids:
                for qid in quota_ids:
                    inferred_spec = get_book_from_quota_id(qid)
                    if inferred_spec:
                        break
            self._update_experience(
                existing["id"], quota_ids,
                record.get("quota_names"),
                source, 85,
                quota_db_version=quota_db_ver,
                specialty=inferred_spec,
            )
            stats["updated"] += 1
            if is_suspect:
                stats["suspect"] += 1
        else:
            record_id = self.add_experience(
                bill_text=bill_text,
                quota_ids=quota_ids,
                quota_names=record.get("quota_names"),
                bill_name=record.get("bill_name"),
                bill_code=record.get("bill_code"),
                bill_unit=record.get("bill_unit"),
                source=source,
                confidence=85,
                province=province,
                project_name=project_name,
            )
            if record_id > 0:
                stats["added"] += 1
                if is_suspect:
                    stats["suspect"] += 1
            else:
                stats["skipped"] += 1

    suspect_msg = f", 审核降级{stats['suspect']}" if stats.get("suspect") else ""
    logger.info(f"项目导入完成: 总{stats['total']}条, "
                f"新增{stats['added']}, 更新{stats['updated']}, "
                f"跳过{stats['skipped']}{suspect_msg}")

    return stats


def rebuild_vector_index(self):
    """
    重建经验库的向量索引（当SQLite数据更新但向量索引不同步时使用）
    重建后带省份metadata，支持按省份过滤向量搜索
    """
    logger.info("重建经验库向量索引...")

    conn = self._connect(row_factory=True)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, bill_text, province FROM experiences")
        rows = cursor.fetchall()
    finally:
        conn.close()

    if not rows:
        logger.info("经验库为空，无需重建")
        return

    # 清空旧索引
    import chromadb
    self.chroma_dir.mkdir(parents=True, exist_ok=True)
    self._chroma_client = chromadb.PersistentClient(path=str(self.chroma_dir))
    try:
        self._chroma_client.delete_collection("experiences")
    except Exception as e:
        logger.debug(f"经验库旧向量集合删除跳过: {e}")
    try:
        self._collection = self._chroma_client.create_collection(
            name="experiences",
            metadata={
                "hnsw:space": "cosine",
                "vector_model": os.getenv("VECTOR_MODEL_KEY", "bge"),
            }
        )
    except Exception as e:
        logger.error(f"经验库向量集合创建失败: {e}")
        return

    # 第一阶段：GPU批量编码（大batch，充分利用GPU）
    import time as _time
    total = len(rows)
    all_texts = [row["bill_text"] for row in rows]
    encode_batch = 512  # RTX 4070 12GB显存，512刚好不爆

    logger.info(f"第1阶段: GPU编码 {total} 条文本 (batch={encode_batch})...")
    t0 = _time.time()
    from src.model_profile import encode_documents
    all_embeddings = encode_documents(
        self.model, all_texts,
        batch_size=encode_batch,
        show_progress=True,
    )
    t1 = _time.time()
    logger.info(f"GPU编码完成: {total}条, {t1-t0:.1f}秒 ({total/(t1-t0):.0f}条/秒)")

    # 第二阶段：批量写入ChromaDB（CPU操作，batch可以大一些）
    write_batch = 5000  # ChromaDB写入用大batch减少事务开销
    logger.info(f"第2阶段: 写入ChromaDB (batch={write_batch})...")
    for start in range(0, total, write_batch):
        end = min(start + write_batch, total)
        batch = rows[start:end]

        ids = [str(row["id"]) for row in batch]
        texts = [row["bill_text"] for row in batch]
        metadatas = [{"province": row["province"] or ""} for row in batch]

        self._collection.add(
            ids=ids,
            documents=texts,
            embeddings=all_embeddings[start:end].tolist(),
            metadatas=metadatas,
        )
        logger.info(f"  写入进度: {end}/{total} ({end*100//total}%)")

    t2 = _time.time()
    logger.info(f"经验库向量索引重建完成: {total}条记录, "
                f"编码{t1-t0:.0f}s + 写入{t2-t1:.0f}s = 总计{t2-t0:.0f}s")
