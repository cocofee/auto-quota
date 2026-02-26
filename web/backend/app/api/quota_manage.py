"""
定额库管理 API（管理员专属）

路由挂载在 /api/admin/quotas 前缀下:
    GET    /api/admin/quotas/provinces            — 已导入的省份列表
    GET    /api/admin/quotas/{province}/stats      — 指定省份的定额统计
    GET    /api/admin/quotas/{province}/chapters    — 指定省份的章节列表
    GET    /api/admin/quotas/{province}/import-history — 导入历史
    POST   /api/admin/quotas/import                — 上传Excel导入定额

通过 asyncio.to_thread() 调用核心引擎的 QuotaDB（SQLite同步操作），
避免阻塞 FastAPI 的异步事件循环。
"""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from loguru import logger

from app.models.user import User
from app.auth.permissions import require_admin

router = APIRouter()


def _validate_province(province: str) -> str:
    """校验省份名称，防止路径穿越攻击"""
    if not province or not province.strip():
        raise HTTPException(status_code=400, detail="省份名称不能为空")
    province = province.strip()
    dangerous_chars = ['/', '\\', '..', '\x00']
    for ch in dangerous_chars:
        if ch in province:
            raise HTTPException(status_code=400, detail=f"省份名称包含非法字符: {ch}")
    return province


def _get_quota_total(db) -> int:
    """直接查询定额总数（不依赖 book 字段，更可靠）"""
    try:
        conn = db._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM quotas")
            return cursor.fetchone()[0]
        finally:
            conn.close()
    except Exception:
        return 0


def _get_chapter_list(db) -> list[dict]:
    """按章节（chapter）分组统计定额数量

    比 get_books() 更可靠：直接按 chapter 字段分组，
    不依赖 book 字段是否正确填充。
    """
    try:
        conn = db._connect()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT chapter, COUNT(*) as cnt
                FROM quotas
                WHERE chapter IS NOT NULL AND chapter != ''
                GROUP BY chapter
                ORDER BY chapter
            """)
            return [
                {"chapter": row[0], "count": row[1]}
                for row in cursor.fetchall()
            ]
        finally:
            conn.close()
    except Exception:
        return []


@router.get("/provinces")
async def quota_provinces(
    admin: User = Depends(require_admin),
):
    """获取已导入（已构建数据库）的省份列表，含定额总数和分组信息"""
    try:
        def _query():
            import config as quota_config
            from src.quota_db import QuotaDB

            provinces = quota_config.list_db_provinces()
            groups = quota_config.get_province_groups()
            result = []
            for name in provinces:
                try:
                    db = QuotaDB(name)
                    total = _get_quota_total(db)
                    version = db.get_version()
                    chapters = _get_chapter_list(db)
                    result.append({
                        "name": name,
                        "total_quotas": total,
                        "chapter_count": len(chapters),
                        "version": version,
                        "group": groups.get(name, name[:2]),
                    })
                except Exception as e:
                    logger.warning(f"读取省份 {name} 定额库失败: {e}")
                    result.append({
                        "name": name,
                        "total_quotas": 0,
                        "chapter_count": 0,
                        "version": "",
                        "group": groups.get(name, name[:2]),
                    })
            return result

        provinces = await asyncio.to_thread(_query)
        return {"items": provinces}
    except Exception as e:
        logger.error(f"获取定额省份列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取省份列表失败: {e}")


@router.get("/{province}/stats")
async def quota_stats(
    province: str,
    admin: User = Depends(require_admin),
):
    """获取指定省份的定额库统计（总数、章节数、版本号）"""
    province = _validate_province(province)
    try:
        def _query():
            import config as quota_config
            from src.quota_db import QuotaDB

            resolved = quota_config.resolve_province(province, scope="db")
            db = QuotaDB(resolved)
            total = _get_quota_total(db)
            chapters = _get_chapter_list(db)
            version = db.get_version()
            history = db.get_import_history()
            return {
                "province": resolved,
                "total_quotas": total,
                "chapter_count": len(chapters),
                "version": version,
                "import_count": len(history),
                "last_import": history[-1]["imported_at"] if history else None,
            }

        stats = await asyncio.to_thread(_query)
        return stats
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"获取定额统计失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取定额统计失败: {e}")


@router.get("/{province}/chapters")
async def quota_chapters(
    province: str,
    admin: User = Depends(require_admin),
):
    """获取指定省份的章节列表（按章节分组统计定额数量）"""
    province = _validate_province(province)
    try:
        def _query():
            import config as quota_config
            from src.quota_db import QuotaDB

            resolved = quota_config.resolve_province(province, scope="db")
            db = QuotaDB(resolved)
            return _get_chapter_list(db)

        chapters = await asyncio.to_thread(_query)
        return {"items": chapters}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"获取章节列表失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取章节列表失败: {e}")


@router.get("/{province}/import-history")
async def quota_import_history(
    province: str,
    admin: User = Depends(require_admin),
):
    """获取指定省份的定额导入历史"""
    province = _validate_province(province)
    try:
        def _query():
            import config as quota_config
            from src.quota_db import QuotaDB

            resolved = quota_config.resolve_province(province, scope="db")
            db = QuotaDB(resolved)
            return db.get_import_history()

        history = await asyncio.to_thread(_query)
        return {"items": history}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"获取导入历史失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取导入历史失败: {e}")


@router.post("/import")
async def import_quota(
    file: UploadFile = File(description="定额Excel文件"),
    province: str = Form(description="省份名称，如：北京市建设工程施工消耗量标准(2024)"),
    admin: User = Depends(require_admin),
):
    """上传并导入定额Excel文件

    工作流程：
    1. 保存上传的Excel到临时目录
    2. 调用 QuotaDB.import_excel() 导入到SQLite
    3. 记录导入历史
    4. 重建搜索索引（BM25 + 向量）
    """
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="请上传 .xlsx 格式的Excel文件")

    # 安全检查：省份名不能包含路径分隔符或特殊字符，防止路径穿越攻击
    province = _validate_province(province)

    temp_path = None
    try:
        import config as quota_config

        # 保存上传文件到临时目录（对文件名做 basename 清洗，防止路径穿越）
        upload_dir = quota_config.OUTPUT_DIR / "temp" / "quota_uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_filename = Path(file.filename).name  # 去掉路径部分，只保留文件名
        temp_path = upload_dir / safe_filename

        content = await file.read()
        # 限制文件大小（最大100MB），防止内存溢出
        max_size = 100 * 1024 * 1024  # 100MB
        if len(content) > max_size:
            raise HTTPException(status_code=400, detail=f"文件过大（{len(content) / 1024 / 1024:.1f}MB），最大允许100MB")
        with open(temp_path, "wb") as f:
            f.write(content)

        logger.info(f"定额Excel已保存: {temp_path}（{len(content)} bytes）")

        def _import():
            from src.quota_db import QuotaDB

            db = QuotaDB(province)
            db.init_db()

            # 导入Excel（追加模式，不清除现有数据）
            count = db.import_excel(str(temp_path), clear_existing=False)
            db.record_import(str(temp_path), "安装", count)

            # 重建搜索索引
            try:
                from src.quota_search import build_search_index
                build_search_index(province)
                index_ok = True
            except Exception as e:
                logger.warning(f"索引重建失败（定额已导入）: {e}")
                index_ok = False

            return {
                "province": province,
                "file": file.filename,
                "imported_count": count,
                "index_rebuilt": index_ok,
            }

        result = await asyncio.to_thread(_import)
        return result
    except HTTPException:
        raise  # 400等验证错误原样抛出，不要被下面的 except Exception 吞掉
    except Exception as e:
        logger.error(f"定额导入失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")
    finally:
        # 清理临时文件
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
