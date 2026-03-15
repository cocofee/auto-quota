"""
定额搜索 API（公开接口，登录用户即可使用）

提供定额库的搜索能力，供 OpenClaw 等外部工具调用。

路由挂载在 /api/quota-search 前缀下:
    GET  /api/quota-search          — 按关键词搜索定额
    GET  /api/quota-search/by-id    — 按定额编号精确查询
    GET  /api/quota-search/provinces — 获取可用省份列表

远程模式（MATCH_BACKEND=remote）：
    转发请求到本地电脑的匹配API（local_match_server.py），
    懒猫容器内不需要定额库数据文件。
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger

from app.models.user import User
from app.auth.deps import get_current_user
from app.config import MATCH_BACKEND, LOCAL_MATCH_URL, LOCAL_MATCH_API_KEY

router = APIRouter()


def _is_remote() -> bool:
    """是否使用远程模式"""
    return MATCH_BACKEND == "remote" and LOCAL_MATCH_URL


def _validate_province(province: str) -> str:
    """校验省份名称"""
    if not province or not province.strip():
        raise HTTPException(status_code=400, detail="province 不能为空")
    province = province.strip()
    for ch in ['/', '\\', '..', '\x00']:
        if ch in province:
            raise HTTPException(status_code=400, detail=f"省份名称包含非法字符")
    return province


async def _remote_get(path: str, params: dict) -> dict:
    """转发GET请求到本地匹配服务"""
    import httpx

    url = f"{LOCAL_MATCH_URL.rstrip('/')}{path}"
    headers = {"X-API-Key": LOCAL_MATCH_API_KEY}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers, params=params)

        if resp.status_code == 200:
            return resp.json()

        # 转发错误
        detail = ""
        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:
            detail = resp.text[:200]

        raise HTTPException(status_code=resp.status_code, detail=detail)

    except httpx.ConnectError:
        raise HTTPException(
            status_code=503,
            detail="无法连接本地匹配服务，请确认电脑上的匹配服务已启动"
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="本地匹配服务响应超时")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"远程定额搜索失败: {e}")
        raise HTTPException(status_code=500, detail=f"远程搜索失败: {e}")


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

    # 远程模式：转发到本地电脑
    if _is_remote():
        params = {"keyword": keyword, "province": province, "limit": limit}
        if book:
            params["book"] = book
        if chapter:
            params["chapter"] = chapter
        return await _remote_get("/quota-search", params)

    # 本地模式：直接查询
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

    # 远程模式
    if _is_remote():
        return await _remote_get("/quota-search/by-id", {"quota_id": quota_id, "province": province})

    # 本地模式
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

    # 远程模式
    if _is_remote():
        return await _remote_get("/quota-search/provinces", {})

    # 本地模式
    try:
        def _query():
            import config as quota_config
            return quota_config.list_db_provinces()

        provinces = await asyncio.to_thread(_query)
        return {"items": provinces}

    except Exception as e:
        logger.error(f"获取省份列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取省份列表失败: {e}")
