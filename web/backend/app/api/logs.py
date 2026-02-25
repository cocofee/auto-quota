"""
系统日志查看 API（管理员专属）

路由挂载在 /api/admin/logs 前缀下:
    GET /api/admin/logs/files   — 日志文件列表
    GET /api/admin/logs/read    — 读取指定日志文件内容
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.models.user import User
from app.auth.permissions import require_admin

router = APIRouter()

# 日志目录（和 main.py / celery_app.py 中的 LOG_DIR 保持一致）
LOG_DIR = Path(__file__).parent.parent.parent.parent.parent / "logs"


def _safe_filename(filename: str) -> Path:
    """校验文件名安全性，防止路径穿越攻击

    只允许读取 LOG_DIR 下的 .log 文件，文件名不能包含路径分隔符。
    """
    # 不允许路径分隔符和特殊字符
    dangerous = ['/', '\\', '..', '\x00']
    for ch in dangerous:
        if ch in filename:
            raise HTTPException(status_code=400, detail=f"文件名包含非法字符: {ch}")

    if not filename.endswith('.log'):
        raise HTTPException(status_code=400, detail="只能读取 .log 文件")

    filepath = LOG_DIR / filename
    # 再次确认解析后的路径确实在 LOG_DIR 下
    try:
        filepath.resolve().relative_to(LOG_DIR.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="文件路径非法")

    return filepath


@router.get("/files")
async def list_log_files(
    admin: User = Depends(require_admin),
):
    """获取日志文件列表（按修改时间倒序）"""
    if not LOG_DIR.exists():
        return {"items": []}

    files = []
    for f in LOG_DIR.iterdir():
        if f.is_file() and f.suffix == '.log':
            stat = f.stat()
            files.append({
                "filename": f.name,
                "size": stat.st_size,
                "size_display": _format_size(stat.st_size),
                "modified_at": stat.st_mtime,
            })

    # 按修改时间倒序（最新的在前）
    files.sort(key=lambda x: x["modified_at"], reverse=True)
    return {"items": files}


@router.get("/read")
async def read_log_file(
    filename: str = Query(description="日志文件名"),
    lines: int = Query(default=200, description="读取行数（从末尾开始）"),
    keyword: str = Query(default="", description="过滤关键词（为空则不过滤）"),
    admin: User = Depends(require_admin),
):
    """读取指定日志文件内容

    从文件末尾开始读取指定行数，支持按关键词过滤。
    """
    if lines < 1 or lines > 5000:
        lines = 200

    filepath = _safe_filename(filename)
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="日志文件不存在")

    try:
        # 读取整个文件（日志文件通常不会太大，按天轮转）
        content = filepath.read_text(encoding="utf-8", errors="replace")
        all_lines = content.splitlines()

        # 按关键词过滤
        if keyword.strip():
            kw = keyword.strip().lower()
            all_lines = [line for line in all_lines if kw in line.lower()]

        # 取最后 N 行
        result_lines = all_lines[-lines:]

        return {
            "filename": filename,
            "total_lines": len(all_lines),
            "returned_lines": len(result_lines),
            "content": "\n".join(result_lines),
        }
    except Exception as e:
        logger.error(f"读取日志文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"读取日志文件失败: {e}")


def _format_size(size_bytes: int) -> str:
    """格式化文件大小为可读字符串"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
