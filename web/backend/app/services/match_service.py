"""
匹配服务层

封装文件上传处理和匹配结果保存逻辑。
被 api/tasks.py（创建任务时保存文件）和 tasks/match_task.py（保存结果到DB）调用。
"""

import uuid
from pathlib import Path

from fastapi import UploadFile
from loguru import logger

from src.excel_compat import detect_excel_format_from_header, validate_excel_upload

from app.config import UPLOAD_DIR, UPLOAD_MAX_MB, TASK_OUTPUT_DIR


def _is_valid_excel_signature(suffix: str, header: bytes) -> bool:
    """校验Excel文件魔数，避免仅靠扩展名绕过。"""
    info = detect_excel_format_from_header(header, f"dummy{suffix}")
    return info.actual_format in {"xls", "xlsx"}


def save_upload_file(file: UploadFile, task_id: uuid.UUID) -> Path:
    """保存上传的 Excel 文件到服务器

    参数:
        file: FastAPI 的 UploadFile 对象
        task_id: 任务ID（每个任务一个独立子目录，避免文件名冲突）
    返回:
        保存后的文件路径
    异常:
        ValueError: 文件格式不对或超过大小限制
    """
    # 检查文件扩展名
    filename = file.filename or "unknown.xlsx"
    suffix = Path(filename).suffix.lower()
    if suffix not in (".xlsx", ".xls"):
        raise ValueError(f"不支持的文件格式 '{suffix}'，请上传 .xlsx 或 .xls 文件")
    file.file.seek(0)
    header = file.file.read(8)
    file.file.seek(0)
    info = validate_excel_upload(filename, header)
    if info.is_mislabeled:
        logger.warning(
            f"上传Excel后缀与内容不一致: filename={filename}; "
            f"declared={info.declared_suffix}; actual={info.normalized_suffix}"
        )

    # 创建任务专属目录
    task_dir = UPLOAD_DIR / str(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)

    # 保存文件（统一命名为 input.xlsx/.xls，避免中文文件名问题）
    safe_name = f"input{info.normalized_suffix}"
    save_path = task_dir / safe_name

    # 写入文件并检查大小
    size = 0
    max_bytes = UPLOAD_MAX_MB * 1024 * 1024
    with open(save_path, "wb") as f:
        while chunk := file.file.read(8192):
            size += len(chunk)
            if size > max_bytes:
                save_path.unlink(missing_ok=True)
                raise ValueError(f"文件大小超过 {UPLOAD_MAX_MB}MB 限制")
            f.write(chunk)

    logger.info(f"文件已保存: {save_path} ({size / 1024:.1f}KB)")
    return save_path


def get_task_output_dir(task_id: uuid.UUID) -> Path:
    """获取任务的输出目录（不存在则自动创建）"""
    output_dir = TASK_OUTPUT_DIR / str(task_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def save_results_to_db(session, task_id: uuid.UUID, results: list[dict]):
    """把 main.run() 返回的匹配结果保存到 PostgreSQL

    参数:
        session: 同步 SQLAlchemy 会话（Celery worker 提供）
        task_id: 任务ID
        results: main.run() 返回的 results 列表，每条包含
                 bill_item/quotas/confidence/match_source/explanation 等字段
    """
    from app.models.result import MatchResult

    for idx, result in enumerate(results):
        bill_item = result.get("bill_item", {})

        # 提取定额列表（只保留关键字段，减少存储体积）
        quotas_raw = result.get("quotas", [])
        quotas = []
        for q in quotas_raw:
            quotas.append({
                "quota_id": q.get("quota_id", ""),
                "name": q.get("name", ""),
                "unit": q.get("unit", ""),
                "param_score": q.get("param_score"),
                "rerank_score": q.get("rerank_score"),
                "source": q.get("source", ""),
            })

        # 提取 trace 的简化版本（只保留 path 和 final_source）
        trace_raw = result.get("trace", {})
        trace_simplified = None
        if trace_raw:
            trace_simplified = {
                "path": trace_raw.get("path", []),
                "final_source": trace_raw.get("final_source", ""),
                "final_confidence": trace_raw.get("final_confidence"),
            }

        match_result = MatchResult(
            task_id=task_id,
            index=idx,
            bill_code=(bill_item.get("code") or "")[:50],
            bill_name=(bill_item.get("name") or "")[:500],
            bill_description=bill_item.get("spec") or bill_item.get("description") or "",
            bill_unit=(bill_item.get("unit") or "")[:50],
            bill_quantity=bill_item.get("quantity"),
            specialty=(bill_item.get("specialty") or "")[:20],
            sheet_name=(bill_item.get("sheet_name") or "")[:100],
            section=(bill_item.get("section") or "")[:200],
            quotas=quotas if quotas else None,
            confidence=result.get("confidence", 0),
            match_source=result.get("match_source", ""),
            explanation=result.get("explanation", ""),
            candidates_count=result.get("candidates_count", 0),
            trace=trace_simplified,
        )
        session.add(match_result)

    # 一次性 flush（不 commit，由调用者决定何时 commit）
    session.flush()
    logger.info(f"任务 {task_id}: 保存了 {len(results)} 条匹配结果到数据库")
