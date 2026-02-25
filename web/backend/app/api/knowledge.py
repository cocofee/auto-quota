"""
知识库管理 API（管理员专属）

路由挂载在 /api/admin/knowledge 前缀下，包含三个子模块：

方法论卡片:
    GET    /method-cards/stats    — 统计概览
    GET    /method-cards          — 卡片列表（支持specialty筛选）
    GET    /method-cards/search   — 按清单名搜索相关卡片
    POST   /method-cards/generate — 触发卡片生成

通用知识库:
    GET    /universal-kb/stats    — 统计概览
    GET    /universal-kb/records  — 知识列表（分页+层级筛选）
    GET    /universal-kb/search   — 搜索知识提示
    DELETE /universal-kb/{id}     — 删除知识条目

定额规则库:
    GET    /rules/stats           — 统计概览
    GET    /rules/records         — 规则列表（分页+省份筛选）
    GET    /rules/search          — 搜索规则
    POST   /rules/import          — 上传规则文件导入
"""

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from pydantic import BaseModel
from loguru import logger

from app.models.user import User
from app.auth.permissions import require_admin

router = APIRouter()


# ============================================================
# 懒加载获取知识库实例（每次新建，避免线程安全问题）
# ============================================================

def _get_method_cards():
    """获取方法论卡片实例"""
    from src.method_cards import MethodCards
    return MethodCards()


def _get_universal_kb():
    """获取通用知识库实例"""
    from src.universal_kb import UniversalKB
    return UniversalKB()


def _get_rule_knowledge():
    """获取定额规则库实例"""
    from src.rule_knowledge import RuleKnowledge
    return RuleKnowledge()


# ============================================================
# 方法论卡片
# ============================================================

@router.get("/method-cards/stats")
async def method_cards_stats(
    admin: User = Depends(require_admin),
):
    """方法论卡片统计概览"""
    try:
        stats = await asyncio.to_thread(lambda: _get_method_cards().get_stats())
        return stats
    except Exception as e:
        logger.error(f"获取方法卡片统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取方法卡片统计失败")


@router.get("/method-cards/search")
async def method_cards_search(
    q: str = Query(description="清单名称（用于搜索相关卡片）"),
    specialty: str | None = Query(default=None, description="专业筛选（如C10）"),
    province: str | None = Query(default=None, description="省份筛选"),
    limit: int = Query(default=5, description="返回条数"),
    admin: User = Depends(require_admin),
):
    """按清单名称搜索相关方法卡片"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")

    try:
        def _search():
            mc = _get_method_cards()
            return mc.find_relevant(
                bill_name=q.strip(),
                specialty=specialty,
                province=province,
                top_k=limit,
            )

        results = await asyncio.to_thread(_search)
        return {"items": results, "total": len(results)}
    except Exception as e:
        logger.error(f"搜索方法卡片失败: {e}")
        raise HTTPException(status_code=500, detail="搜索方法卡片失败")


@router.get("/method-cards")
async def method_cards_list(
    specialty: str | None = Query(default=None, description="专业筛选（如C10）"),
    page: int = 1,
    size: int = 20,
    admin: User = Depends(require_admin),
):
    """获取方法卡片列表"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    try:
        def _query():
            mc = _get_method_cards()
            cards = mc.get_all_cards()
            # 按专业筛选
            if specialty:
                cards = [c for c in cards if c.get("specialty") == specialty]
            return cards

        all_cards = await asyncio.to_thread(_query)
        total = len(all_cards)
        start = (page - 1) * size
        items = all_cards[start:start + size]

        return {"items": items, "total": total, "page": page, "size": size}
    except Exception as e:
        logger.error(f"获取方法卡片列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取方法卡片列表失败")


class GenerateRequest(BaseModel):
    """方法卡片生成请求"""
    province: str | None = None    # 省份（None=当前默认）
    dry_run: bool = True           # 预览模式（不调用大模型）
    incremental: bool = True       # 增量模式（跳过已有卡片）


@router.post("/method-cards/generate")
async def method_cards_generate(
    req: GenerateRequest,
    admin: User = Depends(require_admin),
):
    """触发方法卡片生成

    dry_run=true 时只分析有哪些模式可以提炼（不调大模型）。
    incremental=true 时跳过已有卡片，只生成新模式。
    """
    try:
        def _generate():
            from tools.gen_method_cards import generate_cards
            return generate_cards(
                province=req.province,
                dry_run=req.dry_run,
                incremental=req.incremental,
            )

        result = await asyncio.to_thread(_generate)
        return result
    except Exception as e:
        logger.error(f"生成方法卡片失败: {e}")
        raise HTTPException(status_code=500, detail=f"生成方法卡片失败: {e}")


# ============================================================
# 通用知识库
# ============================================================

@router.get("/universal-kb/stats")
async def universal_kb_stats(
    admin: User = Depends(require_admin),
):
    """通用知识库统计概览"""
    try:
        stats = await asyncio.to_thread(lambda: _get_universal_kb().get_stats())
        return stats
    except Exception as e:
        logger.error(f"获取通用知识库统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取通用知识库统计失败")


@router.get("/universal-kb/records")
async def universal_kb_records(
    layer: str = Query(default="all", description="层级: all/authority/candidate"),
    page: int = 1,
    size: int = 20,
    admin: User = Depends(require_admin),
):
    """获取通用知识库记录列表"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    try:
        def _query():
            import json as _json
            import sqlite3
            from config import get_universal_kb_path

            db_path = get_universal_kb_path()
            if not db_path.exists():
                return [], 0

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.cursor()

                # 构建查询条件
                where = ""
                params: list = []
                if layer in ("authority", "candidate"):
                    where = "WHERE layer = ?"
                    params.append(layer)

                # 查总数
                cursor.execute(f"SELECT COUNT(*) FROM knowledge {where}", params)
                total = cursor.fetchone()[0]

                # 查分页数据
                offset = (page - 1) * size
                cursor.execute(
                    f"SELECT * FROM knowledge {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                    params + [size, offset],
                )
                rows = cursor.fetchall()

                # 转为字典列表，解析JSON字段
                items = []
                for row in rows:
                    item = dict(row)
                    for json_field in ("bill_keywords", "quota_patterns",
                                       "associated_patterns", "param_hints", "province_list"):
                        val = item.get(json_field)
                        if isinstance(val, str):
                            try:
                                item[json_field] = _json.loads(val)
                            except Exception:
                                pass
                    items.append(item)

                return items, total
            finally:
                conn.close()

        items, total = await asyncio.to_thread(_query)
        return {"items": items, "total": total, "page": page, "size": size}
    except Exception as e:
        logger.error(f"获取通用知识库记录失败: {e}")
        raise HTTPException(status_code=500, detail="获取通用知识库记录失败")


@router.get("/universal-kb/search")
async def universal_kb_search(
    q: str = Query(description="清单描述（用于搜索知识提示）"),
    limit: int = Query(default=10, description="返回条数"),
    admin: User = Depends(require_admin),
):
    """搜索通用知识库"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")

    try:
        def _search():
            kb = _get_universal_kb()
            # search_hints 默认只查权威层，管理员界面查全部
            return kb.search_hints(q.strip(), top_k=limit, authority_only=False)

        results = await asyncio.to_thread(_search)
        return {"items": results, "total": len(results)}
    except Exception as e:
        logger.error(f"搜索通用知识库失败: {e}")
        raise HTTPException(status_code=500, detail="搜索通用知识库失败")


@router.delete("/universal-kb/{record_id}")
async def universal_kb_delete(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """删除通用知识库条目"""
    try:
        def _delete():
            import sqlite3
            from config import get_universal_kb_path

            db_path = get_universal_kb_path()
            conn = sqlite3.connect(str(db_path))
            try:
                cursor = conn.execute("DELETE FROM knowledge WHERE id = ?", (record_id,))
                conn.commit()
                return cursor.rowcount > 0
            finally:
                conn.close()

        deleted = await asyncio.to_thread(_delete)
        if not deleted:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除通用知识库记录失败: {e}")
        raise HTTPException(status_code=500, detail="删除失败")


# ============================================================
# 定额规则库
# ============================================================

@router.get("/rules/stats")
async def rules_stats(
    admin: User = Depends(require_admin),
):
    """定额规则库统计概览"""
    try:
        stats = await asyncio.to_thread(lambda: _get_rule_knowledge().get_stats())
        return stats
    except Exception as e:
        logger.error(f"获取规则库统计失败: {e}")
        raise HTTPException(status_code=500, detail="获取规则库统计失败")


@router.get("/rules/records")
async def rules_records(
    province: str | None = Query(default=None, description="省份筛选"),
    page: int = 1,
    size: int = 20,
    admin: User = Depends(require_admin),
):
    """获取定额规则列表"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    try:
        def _query():
            import sqlite3
            import config as quota_config

            # rule_knowledge.db 路径（与 RuleKnowledge 类一致）
            db_path = quota_config.COMMON_DB_DIR / "rule_knowledge.db"
            if not db_path.exists():
                return [], 0

            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.cursor()

                where = ""
                params: list = []
                if province:
                    where = "WHERE province = ?"
                    params.append(province)

                # 查总数
                cursor.execute(f"SELECT COUNT(*) FROM rules {where}", params)
                total = cursor.fetchone()[0]

                # 查分页数据
                offset = (page - 1) * size
                cursor.execute(
                    f"SELECT * FROM rules {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                    params + [size, offset],
                )
                rows = cursor.fetchall()
                items = [dict(row) for row in rows]

                return items, total
            finally:
                conn.close()

        items, total = await asyncio.to_thread(_query)
        return {"items": items, "total": total, "page": page, "size": size}
    except Exception as e:
        logger.error(f"获取规则列表失败: {e}")
        raise HTTPException(status_code=500, detail="获取规则列表失败")


@router.get("/rules/search")
async def rules_search(
    q: str = Query(description="搜索关键词"),
    province: str | None = Query(default=None, description="省份筛选"),
    limit: int = Query(default=10, description="返回条数"),
    admin: User = Depends(require_admin),
):
    """搜索定额规则"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")

    try:
        def _search():
            kb = _get_rule_knowledge()
            return kb.search_rules(q.strip(), top_k=limit, province=province)

        results = await asyncio.to_thread(_search)
        return {"items": results, "total": len(results)}
    except Exception as e:
        logger.error(f"搜索规则库失败: {e}")
        raise HTTPException(status_code=500, detail="搜索规则库失败")


@router.post("/rules/import")
async def rules_import(
    file: UploadFile = File(description="规则文本文件（.txt）"),
    province: str = Form(description="省份名称"),
    specialty: str = Form(default="", description="专业（安装/土建/市政，可留空自动推断）"),
    admin: User = Depends(require_admin),
):
    """上传规则文件导入定额规则库"""
    if not province.strip():
        raise HTTPException(status_code=400, detail="省份不能为空")

    # 检查文件类型
    filename = file.filename or ""
    if not filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="只支持 .txt 格式的规则文件")

    try:
        # 保存上传文件到临时目录
        content = await file.read()
        with tempfile.NamedTemporaryFile(
            mode="wb", suffix=".txt", delete=False, prefix="rule_import_"
        ) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        def _import():
            kb = _get_rule_knowledge()
            return kb.import_file(
                file_path=tmp_path,
                province=province.strip(),
                specialty=specialty.strip() or None,
            )

        stats = await asyncio.to_thread(_import)

        # 清理临时文件
        try:
            Path(tmp_path).unlink()
        except Exception:
            pass

        return {"message": "导入成功", "stats": stats}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"导入规则文件失败: {e}")
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")
