"""
错误分析 API（管理员专属）

路由挂载在 /api/admin/analysis 前缀下:
    GET  /api/admin/analysis/error-report  — 获取错误分析报告
    GET  /api/admin/analysis/patterns      — 低置信度模式列表（分页）
    GET  /api/admin/analysis/by-province   — 按省份统计
    GET  /api/admin/analysis/by-specialty  — 按专业统计

读取 batch_report.py 生成的 error_report.json。
"""

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from loguru import logger

from app.models.user import User
from app.auth.permissions import require_admin
from app.config import PROJECT_ROOT

router = APIRouter()

# error_report.json 路径
_REPORT_PATH = PROJECT_ROOT / "output" / "batch" / "error_report.json"


def _load_report() -> dict:
    """加载错误分析报告"""
    if not _REPORT_PATH.exists():
        return {}
    try:
        return json.loads(_REPORT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"读取错误报告失败: {e}")
        return {}


@router.get("/error-report")
async def error_report(
    admin: User = Depends(require_admin),
):
    """获取完整错误分析报告

    返回 batch_report.py 生成的完整报告数据，包括：
    - summary: 总体统计
    - by_province: 按省份分组
    - by_specialty: 按专业分组
    - low_confidence_patterns: 低置信度模式
    - province_coverage: 省份覆盖矩阵
    """
    report = await asyncio.to_thread(_load_report)
    if not report:
        return {
            "has_data": False,
            "message": "暂无报告数据，请先运行 batch_report.py 生成报告",
        }

    return {
        "has_data": True,
        "report_date": report.get("report_date", ""),
        "algo_version": report.get("algo_version", ""),
        "summary": report.get("summary", {}),
        "province_coverage": report.get("province_coverage", {}),
    }


@router.get("/patterns")
async def error_patterns(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    province: Optional[str] = Query(None, description="省份筛选"),
    root_cause: Optional[str] = Query(None, description="根因筛选"),
    admin: User = Depends(require_admin),
):
    """低置信度模式列表（分页+筛选）

    返回按出现次数排序的错误模式列表。
    """
    report = await asyncio.to_thread(_load_report)
    patterns = report.get("low_confidence_patterns", [])

    # 筛选
    if province:
        patterns = [p for p in patterns if province in p.get("provinces", [])]
    if root_cause:
        patterns = [p for p in patterns if p.get("root_cause_guess") == root_cause]

    total = len(patterns)

    # 分页
    start = (page - 1) * page_size
    end = start + page_size
    items = patterns[start:end]

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/by-province")
async def analysis_by_province(
    admin: User = Depends(require_admin),
):
    """按省份的错误分析统计"""
    report = await asyncio.to_thread(_load_report)
    by_province = report.get("by_province", {})

    # 转成列表格式，方便前端Table渲染
    items = []
    for prov, stats in sorted(by_province.items(), key=lambda x: -x[1].get("items", 0)):
        items.append({
            "province": prov,
            **stats,
        })

    return {"items": items}


@router.get("/by-specialty")
async def analysis_by_specialty(
    admin: User = Depends(require_admin),
):
    """按专业的错误分析统计"""
    report = await asyncio.to_thread(_load_report)
    by_specialty = report.get("by_specialty", {})

    items = []
    for spec, stats in sorted(by_specialty.items(), key=lambda x: -x[1].get("items", 0)):
        items.append({
            "specialty": spec,
            **stats,
        })

    return {"items": items}
