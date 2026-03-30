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
from app.services.local_http import local_match_async_client

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


async def _remote_get(path: str, params: dict, timeout: float = 60.0) -> dict:
    """转发GET请求到本地匹配服务

    Args:
        timeout: 超时秒数，默认60秒。smart等重计算接口应传更大值。
    """
    import httpx

    url = f"{LOCAL_MATCH_URL.rstrip('/')}{path}"
    headers = {"X-API-Key": LOCAL_MATCH_API_KEY}

    # 最多重试2次（首次 + 1次重试），应对偶发网络抖动
    last_exc = None
    for attempt in range(2):
        try:
            async with local_match_async_client(timeout=timeout) as client:
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

        except httpx.ConnectError as e:
            last_exc = e
            if attempt == 0:
                logger.warning(f"远程匹配服务连接失败，1秒后重试: {e}")
                await asyncio.sleep(1)
                continue
            raise HTTPException(
                status_code=503,
                detail="无法连接本地匹配服务，请确认电脑上的匹配服务已启动"
            )
        except httpx.TimeoutException as e:
            last_exc = e
            if attempt == 0:
                logger.warning(f"远程匹配服务超时，1秒后重试: {e}")
                await asyncio.sleep(1)
                continue
            raise HTTPException(status_code=504, detail=f"本地匹配服务响应超时（{int(timeout)}秒）")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"远程定额搜索失败: {e}")
            raise HTTPException(status_code=500, detail=f"远程搜索失败: {e}")


@router.get("")
async def search_quotas(
    keyword: str = Query(description="搜索关键词，多个词用空格分隔（如'管道安装 DN25'）"),
    province: str = Query(description="省份定额库名称（如'北京市建设工程施工消耗量标准(2024)'）"),
    book: str | None = Query(default=None, description="限定大册（如'C10'表示给排水）"),
    chapter: str | None = Query(default=None, description="限定章节"),
    limit: int = Query(default=20, ge=1, le=100, description="最大返回条数"),
    user: User = Depends(get_current_user),
):
    """按关键词搜索定额

    支持多关键词AND搜索（空格分隔），可按大册或章节过滤。
    返回匹配的定额列表，包含编号、名称、单位等信息。

    用法示例:
        /api/quota-search?keyword=管道安装&province=北京市建设工程施工消耗量标准(2024)
        /api/quota-search?keyword=镀锌钢管 DN25&province=北京市建设工程施工消耗量标准(2024)&book=C10
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

    用法: /api/quota-search/by-id?quota_id=C10-1-10&province=北京市建设工程施工消耗量标准(2024)
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


@router.get("/smart")
async def smart_search(
    name: str = Query(description="清单项目名称（如'JDG20暗配'、'PPR给水管DN25'）"),
    province: str = Query(description="省份定额库名称"),
    description: str = Query(default="", description="清单特征描述（可选）"),
    specialty: str = Query(default="", description="专业册号（可选，如'C10'，不传则自动识别）"),
    limit: int = Query(default=10, ge=1, le=50, description="最大返回条数"),
    user: User = Depends(get_current_user),
):
    """智能搜索定额（清单原文 → 自动清洗+同义词+级联搜索）

    和普通 /quota-search 的区别：
    - 普通搜索需要调用方自己把"JDG20"转成"紧定式钢导管"
    - 智能搜索直接传清单原文，系统自动做术语转换和级联搜索

    用法:
        /api/quota-search/smart?name=JDG20暗配&province=北京市建设工程施工消耗量标准(2024)
        /api/quota-search/smart?name=PPR给水管&description=DN25沟槽连接&province=北京市建设工程施工消耗量标准(2024)
    """
    province = _validate_province(province)

    # 远程模式
    if _is_remote():
        params = {"name": name, "province": province, "limit": limit}
        if description:
            params["description"] = description
        if specialty:
            params["specialty"] = specialty
        # smart接口做4步重计算（专业识别+搜索词构建+级联搜索+跨库），给180秒
        return await _remote_get("/quota-search/smart", params, timeout=180.0)

    # 本地模式
    try:
        def _search():
            from src.text_parser import TextParser
            from src.hybrid_searcher import HybridSearcher
            from src.specialty_classifier import classify as classify_specialty

            parser = TextParser()

            spec = specialty
            if not spec:
                spec_result = classify_specialty(name, description)
                spec = spec_result.get("primary", "") if isinstance(spec_result, dict) else ""

            search_query = parser.build_quota_query(name, description, specialty=spec)

            searcher = HybridSearcher(province)
            books = [spec] if spec else None
            candidates = searcher.search(search_query, top_k=limit, books=books)

            if len(candidates) < 3 and books:
                candidates_all = searcher.search(search_query, top_k=limit, books=None)
                seen = {c.get("quota_id") for c in candidates}
                for c in candidates_all:
                    if c.get("quota_id") not in seen:
                        candidates.append(c)
                        seen.add(c.get("quota_id"))
                candidates = candidates[:limit]

            items = [
                {
                    "quota_id": c.get("quota_id", ""),
                    "name": c.get("name", ""),
                    "unit": c.get("unit", ""),
                    "chapter": c.get("chapter", ""),
                    "book": c.get("book", ""),
                    "score": round(c.get("hybrid_score", 0), 4),
                }
                for c in candidates
            ]
            return {"items": items, "total": len(items), "search_query": search_query, "specialty": spec, "province": province}

        return await asyncio.to_thread(_search)

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"省份 '{province}' 的定额库不存在")
    except Exception as e:
        logger.error(f"智能搜索失败: {e}")
        raise HTTPException(status_code=500, detail=f"智能搜索失败: {e}")
