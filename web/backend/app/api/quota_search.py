"""
定额搜索 API（公开接口，登录用户即可使用）

提供定额库的搜索能力，供 OpenClaw 等外部工具调用。

路由挂载在 /api/quota-search 前缀下:
    GET  /api/quota-search          — 按关键词搜索定额
    GET  /api/quota-search/by-id    — 按定额编号精确查询
    GET  /api/quota-search/provinces — 获取可用省份列表
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.models.user import User
from app.auth.deps import get_current_user

router = APIRouter()


def _validate_province(province: str) -> str:
    """校验省份名称"""
    if not province or not province.strip():
        raise HTTPException(status_code=400, detail="province 不能为空")
    province = province.strip()
    for ch in ['/', '\\', '..', '\x00']:
        if ch in province:
            raise HTTPException(status_code=400, detail=f"省份名称包含非法字符")
    return province


@router.get("")
async def search_quotas(
    keyword: str = Query(description="搜索关键词，多个词用空格分隔（如'管道安装 DN25'）"),
    province: str = Query(description="省份定额库名称（如'北京2024'）"),
    book: str | None = Query(default=None, description="限定大册（如'C10'表示给排水）"),
    chapter: str | None = Query(default=None, description="限定章节"),
    limit: int = Query(default=20, ge=1, le=100, description="最大返回条数"),
    user: User = Depends(get_current_user),
):
    """按关键词搜索定额

    支持多关键词AND搜索（空格分隔），可按大册或章节过滤。
    返回匹配的定额列表，包含编号、名称、单位等信息。

    用法示例:
        /api/quota-search?keyword=管道安装&province=北京2024
        /api/quota-search?keyword=镀锌钢管 DN25&province=北京2024&book=C10
    """
    province = _validate_province(province)

    try:
        def _search():
            from src.quota_db import QuotaDB

            db = QuotaDB(province)
            results = db.search_by_keywords(
                keyword,
                chapter=chapter,
                book=book,
                limit=limit,
            )
            # 只返回关键字段（不暴露内部字段如 search_text）
            return [
                {
                    "quota_id": r.get("quota_id", ""),
                    "name": r.get("name", ""),
                    "unit": r.get("unit", ""),
                    "chapter": r.get("chapter", ""),
                    "book": r.get("book", ""),
                }
                for r in results
            ]

        items = await asyncio.to_thread(_search)
        return {"items": items, "total": len(items), "keyword": keyword, "province": province}

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"省份 '{province}' 的定额库不存在")
    except Exception as e:
        logger.error(f"定额搜索失败: {e}")
        raise HTTPException(status_code=500, detail=f"搜索失败: {e}")


@router.get("/by-id")
async def get_quota_by_id(
    quota_id: str = Query(description="定额编号（如'C10-1-10'）"),
    province: str = Query(description="省份定额库名称"),
    user: User = Depends(get_current_user),
):
    """按定额编号精确查询

    用法: /api/quota-search/by-id?quota_id=C10-1-10&province=北京2024
    """
    province = _validate_province(province)

    try:
        def _query():
            from src.quota_db import QuotaDB
            db = QuotaDB(province)
            return db.get_quota_by_id(quota_id)

        results = await asyncio.to_thread(_query)
        if not results:
            return {"items": [], "total": 0}

        items = [
            {
                "quota_id": r.get("quota_id", ""),
                "name": r.get("name", ""),
                "unit": r.get("unit", ""),
                "chapter": r.get("chapter", ""),
                "book": r.get("book", ""),
            }
            for r in results
        ]
        return {"items": items, "total": len(items)}

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"省份 '{province}' 的定额库不存在")
    except Exception as e:
        logger.error(f"定额查询失败: {e}")
        raise HTTPException(status_code=500, detail=f"查询失败: {e}")


@router.get("/provinces")
async def list_search_provinces(
    user: User = Depends(get_current_user),
):
    """获取可用的省份定额库列表（不需要管理员权限）"""
    try:
        def _query():
            import config as quota_config
            return quota_config.list_db_provinces()

        provinces = await asyncio.to_thread(_query)
        return {"items": provinces}

    except Exception as e:
        logger.error(f"获取省份列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取省份列表失败: {e}")
