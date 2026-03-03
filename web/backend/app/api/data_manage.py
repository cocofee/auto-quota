"""
数据管理 API（管理员专属）

路由挂载在 /api/admin/data 前缀下:
    GET  /api/admin/data/scan-summary     — 扫描结果摘要
    GET  /api/admin/data/coverage         — 定额库覆盖矩阵
    GET  /api/admin/data/experience-trend  — 经验库增长趋势

读取 batch.db + 定额库目录 + 经验库数据。
"""

import asyncio
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends
from loguru import logger

from app.models.user import User
from app.auth.permissions import require_admin
from app.config import PROJECT_ROOT

router = APIRouter()

# 路径
_BATCH_DB_PATH = PROJECT_ROOT / "output" / "batch" / "batch.db"
_PROVINCES_DIR = PROJECT_ROOT / "db" / "provinces"


def _get_batch_db() -> sqlite3.Connection:
    """获取 batch.db 连接"""
    if not _BATCH_DB_PATH.exists():
        raise FileNotFoundError("batch.db 不存在")
    conn = sqlite3.connect(str(_BATCH_DB_PATH), timeout=10)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================
# 扫描结果摘要
# ============================================================

@router.get("/scan-summary")
async def scan_summary(
    admin: User = Depends(require_admin),
):
    """扫描结果摘要

    返回文件总数、格式分布、省份/专业覆盖等统计数据。
    """
    def _query():
        try:
            conn = _get_batch_db()
        except FileNotFoundError:
            return {
                "has_data": False,
                "message": "尚未扫描，请先运行 batch_scanner.py",
            }
        try:
            total = conn.execute("SELECT COUNT(*) FROM file_registry").fetchone()[0]

            # 格式分布
            rows = conn.execute(
                "SELECT format, COUNT(*) as cnt FROM file_registry "
                "WHERE format IS NOT NULL GROUP BY format ORDER BY cnt DESC"
            ).fetchall()
            format_dist = [{"format": r["format"], "count": r["cnt"]} for r in rows]

            # 省份分布（TOP20）
            rows = conn.execute(
                "SELECT province, COUNT(*) as cnt FROM file_registry "
                "WHERE province IS NOT NULL AND province != '' "
                "GROUP BY province ORDER BY cnt DESC LIMIT 20"
            ).fetchall()
            province_dist = [{"province": r["province"], "count": r["cnt"]} for r in rows]

            # 专业分布
            rows = conn.execute(
                "SELECT specialty, COUNT(*) as cnt FROM file_registry "
                "WHERE specialty IS NOT NULL AND specialty != '' "
                "GROUP BY specialty ORDER BY cnt DESC"
            ).fetchall()
            specialty_dist = [{"specialty": r["specialty"], "count": r["cnt"]} for r in rows]

            # 状态分布
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM file_registry GROUP BY status"
            ).fetchall()
            status_dist = [{"status": r["status"], "count": r["cnt"]} for r in rows]

            # 有清单数据的文件数（standard_bill + work_list + equipment_list）
            bill_count = conn.execute(
                "SELECT COUNT(*) FROM file_registry "
                "WHERE format IN ('standard_bill', 'work_list', 'equipment_list')"
            ).fetchone()[0]

            # 预估清单总条数
            est_items = conn.execute(
                "SELECT SUM(estimated_items) FROM file_registry "
                "WHERE format IN ('standard_bill', 'work_list', 'equipment_list')"
            ).fetchone()[0] or 0

            return {
                "has_data": True,
                "total_files": total,
                "bill_files": bill_count,
                "estimated_items": est_items,
                "format_distribution": format_dist,
                "province_distribution": province_dist,
                "specialty_distribution": specialty_dist,
                "status_distribution": status_dist,
            }
        finally:
            conn.close()

    result = await asyncio.to_thread(_query)
    return result


# ============================================================
# 定额库覆盖矩阵
# ============================================================

@router.get("/coverage")
async def coverage_matrix(
    admin: User = Depends(require_admin),
):
    """定额库覆盖矩阵

    对比"有定额库的省份"和"有文件的省份"，分3类：
    - 有定额库且有文件（最理想）
    - 有文件无定额库（需要导入定额库）
    - 有定额库无文件（有库但没有测试数据）
    """
    def _query():
        # 获取有定额库的省份
        has_db = set()
        if _PROVINCES_DIR.exists():
            for d in _PROVINCES_DIR.iterdir():
                if d.is_dir():
                    # 去掉年份后缀（如"北京2024" → "北京"）
                    name = re.sub(r'\d+$', '', d.name)
                    has_db.add(name)

        # 获取有文件的省份
        has_files = set()
        try:
            conn = _get_batch_db()
            rows = conn.execute(
                "SELECT DISTINCT province FROM file_registry "
                "WHERE province IS NOT NULL AND province != ''"
            ).fetchall()
            has_files = {r["province"] for r in rows}
            conn.close()
        except FileNotFoundError:
            pass

        return {
            "has_db_and_files": sorted(has_db & has_files),
            "has_files_no_db": sorted(has_files - has_db),
            "has_db_no_files": sorted(has_db - has_files),
            "db_provinces": sorted(has_db),
            "file_provinces": sorted(has_files),
        }

    result = await asyncio.to_thread(_query)
    return result


# ============================================================
# 经验库增长趋势
# ============================================================

@router.get("/experience-trend")
async def experience_trend(
    admin: User = Depends(require_admin),
):
    """经验库增长趋势

    按省份统计经验库中 authority/candidate 两层的记录数。
    """
    def _query():
        try:
            from src.experience_db import ExperienceDB
            db = ExperienceDB()
            stats = db.get_stats()
            return {
                "total": stats.get("total", 0),
                "authority": stats.get("authority", 0),
                "candidate": stats.get("candidate", 0),
                "by_province": stats.get("by_province", {}),
            }
        except Exception as e:
            logger.warning(f"获取经验库趋势失败: {e}")
            return {
                "total": 0,
                "authority": 0,
                "candidate": 0,
                "by_province": {},
            }

    result = await asyncio.to_thread(_query)
    return result
