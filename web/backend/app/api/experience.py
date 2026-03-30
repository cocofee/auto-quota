"""
经验库管理 API（管理员专属）。

路由挂载在 /api/admin/experience 前缀下。
"""

import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel

from app.auth.permissions import require_admin
from app.config import LOCAL_MATCH_API_KEY, LOCAL_MATCH_URL, MATCH_BACKEND
from app.models.user import User
from app.services.local_http import local_match_async_client

router = APIRouter()


def _safe_list(raw) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw if item]
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return []


def _infer_specialty_code_from_quota_id(quota_id: str | None) -> str:
    if not quota_id:
        return ""
    alpha_match = re.match(r"^([A-Za-z]+\d{0,2})-", str(quota_id).strip())
    if alpha_match:
        return alpha_match.group(1).upper()
    numeric_match = re.match(r"^(\d{1,2})-", str(quota_id).strip())
    if not numeric_match:
        return ""
    num = int(numeric_match.group(1))
    return f"C{num}" if 1 <= num <= 12 else str(num)


def _infer_record_category(record: dict) -> str:
    text_parts = [
        str(record.get("bill_name") or ""),
        str(record.get("bill_text") or ""),
        *_safe_list(record.get("quota_names")),
        *_safe_list(record.get("quota_ids")),
    ]
    text = " ".join(text_parts)
    if re.search(r"光伏|升压站|发电", text, re.IGNORECASE):
        return "光伏"
    if re.search(r"电力|变电|输电|配电装置|电力电缆|变压器|母线|开关站|开闭所|间隔", text, re.IGNORECASE):
        return "电力"

    specialty = ""
    for quota_id in _safe_list(record.get("quota_ids")):
        specialty = _infer_specialty_code_from_quota_id(quota_id)
        if specialty:
            break
    if not specialty:
        specialty = str(record.get("specialty") or "").upper()

    if specialty.startswith("C"):
        return "安装"
    if specialty in {"A", "B"}:
        return "建筑装饰"
    if specialty == "D":
        return "市政"
    if specialty == "E":
        return "园林绿化"
    return "安装"


def _filter_records_by_category(records: list[dict], category: str | None) -> list[dict]:
    if not category or category == "all":
        return records
    return [record for record in records if _infer_record_category(record) == category]


def _extract_province_name(name: str | None) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    city_to_province = {
        "佛山": "广东",
        "深圳": "广东",
        "广州": "广东",
        "东莞": "广东",
        "珠海": "广东",
        "中山": "广东",
        "惠州": "广东",
    }
    match = re.search(r"(.{2,6}?)(省|市|自治区)", text[:12])
    if match:
        region = match.group(1)
    else:
        fallback = re.match(r"^[^\d(（]{2,6}", text)
        region = fallback.group(0).strip() if fallback else text[:2]
    return city_to_province.get(region, region)


def _infer_specialty_from_province_label(name: str | None) -> str:
    text = str(name or "")
    if not text:
        return ""
    if re.search(r"光伏|发电|升压站", text, re.IGNORECASE):
        return "光伏"
    if re.search(r"电力|输电|变电|配电", text, re.IGNORECASE):
        return "电力"
    if re.search(r"园林|绿化", text):
        return "园林绿化"
    if re.search(r"市政", text):
        return "市政"
    if re.search(r"装饰|装修", text):
        return "建筑装饰"
    if re.search(r"安装", text):
        return "安装"
    return ""


def _filter_records_by_scope(
    records: list[dict],
    province_name: str | None,
    specialty_name: str | None,
) -> list[dict]:
    filtered = records
    if province_name:
        filtered = [
            record for record in filtered
            if _extract_province_name(record.get("province")) == province_name
        ]
    if specialty_name and specialty_name != "all":
        filtered = [
            record for record in filtered
            if _infer_specialty_from_province_label(record.get("province")) == specialty_name
        ]
    return filtered


def _extract_region(name: str | None) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    city_to_province = {
        "佛山": "广东",
        "深圳": "广东",
        "广州": "广东",
        "东莞": "广东",
        "珠海": "广东",
        "中山": "广东",
        "惠州": "广东",
    }
    match = re.search(r"(.{2,6}?)(省|市|自治区)", text[:12])
    if match:
        region = match.group(1)
    else:
        fallback = re.match(r"^[^\d(（]{2,6}", text)
        region = fallback.group(0).strip() if fallback else text[:2]
    return city_to_province.get(region, region)


def _filter_records_by_region(records: list[dict], region: str | None) -> list[dict]:
    if not region:
        return records
    return [record for record in records if _extract_region(record.get("province")) == region]


def _is_remote() -> bool:
    """是否转发到本地匹配服务。"""
    return MATCH_BACKEND == "remote" and bool(LOCAL_MATCH_URL)


async def _remote_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    timeout: float = 60.0,
):
    """转发经验库管理请求到本地匹配服务。"""
    import httpx

    url = f"{LOCAL_MATCH_URL.rstrip('/')}{path}"
    headers = {"X-API-Key": LOCAL_MATCH_API_KEY}

    try:
        async with local_match_async_client(timeout=timeout) as client:
            resp = await client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=payload,
            )

        if resp.status_code == 200:
            return resp.json()

        try:
            detail = resp.json().get("detail", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        raise HTTPException(status_code=resp.status_code, detail=detail)
    except HTTPException:
        raise
    except httpx.ConnectError as e:
        logger.warning(f"远程经验库接口连接失败: [{type(e).__name__}] {e} | url={url}")
        raise HTTPException(
            status_code=503,
            detail="无法连接本地经验库服务，请确认电脑上的本地匹配服务已启动",
        )
    except httpx.TimeoutException as e:
        logger.warning(f"远程经验库接口超时: [{type(e).__name__}] {e} | url={url}")
        raise HTTPException(status_code=504, detail="本地经验库服务响应超时")
    except Exception:
        logger.exception(f"远程经验库接口调用失败: method={method} path={path}")
        raise HTTPException(status_code=500, detail="远程经验库接口调用失败")


def _get_experience_db():
    """获取 ExperienceDB 实例。"""
    from src.experience_db import ExperienceDB

    return ExperienceDB()


@router.get("/stats")
async def experience_stats(
    admin: User = Depends(require_admin),
):
    """经验库统计概览。"""
    if _is_remote():
        return await _remote_request("GET", "/experience/stats")

    try:
        def _query():
            db = _get_experience_db()
            return db.get_stats()

        return await asyncio.to_thread(_query)
    except Exception:
        logger.exception("获取经验库统计失败")
        raise HTTPException(status_code=500, detail="获取经验库统计失败")


@router.get("/records")
async def experience_records(
    layer: str = Query(default="all", description="层级: all/authority/candidate"),
    province: str | None = Query(default=None, description="省份筛选"),
    province_name: str | None = Query(default=None, description="province name filter"),
    specialty_name: str | None = Query(default=None, description="specialty name filter"),
    category: str | None = Query(default=None, description="category filter"),
    page: int = 1,
    size: int = 20,
    admin: User = Depends(require_admin),
):
    """获取经验记录列表。"""
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    if _is_remote():
        return await _remote_request(
            "GET",
            "/experience/records",
            params={
                "layer": layer,
                "province": province,
                "province_name": province_name,
                "specialty_name": specialty_name,
                "category": category,
                "page": page,
                "size": size,
            },
        )

    try:
        def _query():
            db = _get_experience_db()
            if layer == "authority":
                records = db.get_authority_records(province=province, limit=0)
                for record in records:
                    record["layer_type"] = "authority"
            elif layer == "candidate":
                records = db.get_candidate_records(province=province, limit=0)
                for record in records:
                    record["layer_type"] = "candidate"
            else:
                authority = db.get_authority_records(province=province, limit=0)
                for record in authority:
                    record["layer_type"] = "authority"
                candidate = db.get_candidate_records(province=province, limit=0)
                for record in candidate:
                    record["layer_type"] = "candidate"
                records = authority + candidate
            records = _filter_records_by_scope(records, province_name, specialty_name)
            return _filter_records_by_category(records, category)

        all_records = await asyncio.to_thread(_query)
        total = len(all_records)
        start = (page - 1) * size
        end = start + size
        items = all_records[start:end]

        return {
            "items": items,
            "total": total,
            "page": page,
            "size": size,
        }
    except Exception:
        logger.exception("获取经验记录失败")
        raise HTTPException(status_code=500, detail="获取经验记录失败")


@router.get("/search")
async def experience_search(
    q: str = Query(description="搜索关键词"),
    province: str | None = Query(default=None, description="省份筛选"),
    province_name: str | None = Query(default=None, description="province name filter"),
    specialty_name: str | None = Query(default=None, description="specialty name filter"),
    category: str | None = Query(default=None, description="category filter"),
    limit: int = 20,
    admin: User = Depends(require_admin),
):
    """搜索经验记录。"""
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    if limit < 1 or limit > 200:
        limit = 20

    if _is_remote():
        return await _remote_request(
            "GET",
            "/experience/search",
            params={
                "q": q,
                "province": province,
                "province_name": province_name,
                "specialty_name": specialty_name,
                "category": category,
                "limit": limit,
            },
        )

    try:
        def _query():
            db = _get_experience_db()
            text = q.strip()
            escaped = text.replace("%", "\\%").replace("_", "\\_")
            like_pattern = f"%{escaped}%"
            conn = db._connect(row_factory=True)
            try:
                cursor = conn.cursor()
                text_match = """(
                    bill_text = ? OR COALESCE(bill_name, '') = ?
                    OR bill_text LIKE ? ESCAPE '\\' OR COALESCE(bill_name, '') LIKE ? ESCAPE '\\'
                )"""
                rank_order = """
                    CASE
                        WHEN bill_text = ? THEN 0
                        WHEN COALESCE(bill_name, '') = ? THEN 1
                        WHEN bill_text LIKE ? ESCAPE '\\' THEN 2
                        WHEN COALESCE(bill_name, '') LIKE ? ESCAPE '\\' THEN 3
                        ELSE 4
                    END ASC,
                    confidence DESC, id DESC
                """
                if province:
                    where = f"province = ? AND {text_match}"
                    params = [
                        province, text, text, like_pattern, like_pattern,
                        text, text, like_pattern, like_pattern, limit,
                    ]
                else:
                    where = text_match
                    params = [
                        text, text, like_pattern, like_pattern,
                        text, text, like_pattern, like_pattern, limit,
                    ]

                cursor.execute(
                    f"""
                    SELECT * FROM experiences
                    WHERE {where}
                    ORDER BY {rank_order}
                    LIMIT ?
                    """,
                    params,
                )
                rows = cursor.fetchall()
                items = [db._normalize_record_quota_fields(dict(row)) for row in rows]
                for item in items:
                    item["layer_type"] = item.get("layer", "candidate")
                items = _filter_records_by_scope(items, province_name, specialty_name)
                items = _filter_records_by_category(items, category)
                return items
            finally:
                conn.close()

        results = await asyncio.to_thread(_query)
        return {"items": results, "total": len(results)}
    except Exception:
        logger.exception("搜索经验库失败")
        raise HTTPException(status_code=500, detail="搜索经验库失败")


@router.post("/{record_id:int}/promote")
async def promote_experience(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """晋升经验记录到权威层。"""
    if _is_remote():
        return await _remote_request("POST", f"/experience/{record_id}/promote")

    try:
        def _promote():
            db = _get_experience_db()
            return db.promote_to_authority(record_id)

        success = await asyncio.to_thread(_promote)
        if not success:
            raise HTTPException(status_code=404, detail="记录不存在或已在权威层")
        return {"message": "晋升成功"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("晋升经验记录失败")
        raise HTTPException(status_code=500, detail="晋升失败")


@router.post("/{record_id:int}/demote")
async def demote_experience(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """降级经验记录到候选层。"""
    if _is_remote():
        return await _remote_request("POST", f"/experience/{record_id}/demote")

    try:
        def _demote():
            db = _get_experience_db()
            return db.demote_to_candidate(record_id)

        success = await asyncio.to_thread(_demote)
        if not success:
            raise HTTPException(status_code=404, detail="记录不存在或已在候选层")
        return {"message": "降级成功"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("降级经验记录失败")
        raise HTTPException(status_code=500, detail="降级失败")


@router.delete("/{record_id:int}")
async def delete_experience(
    record_id: int,
    admin: User = Depends(require_admin),
):
    """删除经验记录。"""
    if _is_remote():
        return await _remote_request("DELETE", f"/experience/{record_id}")

    try:
        def _delete():
            db = _get_experience_db()
            conn = db._connect()
            try:
                cursor = conn.execute("DELETE FROM experiences WHERE id = ?", (record_id,))
                conn.commit()
                deleted = cursor.rowcount > 0
            finally:
                conn.close()

            if deleted:
                try:
                    coll = db.collection
                    if coll is not None:
                        coll.delete(ids=[str(record_id)])
                except Exception as e:
                    logger.opt(exception=e).warning(f"清理向量索引失败（id={record_id}）")

            return deleted

        deleted = await asyncio.to_thread(_delete)
        if not deleted:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"message": "删除成功"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("删除经验记录失败")
        raise HTTPException(status_code=500, detail="删除失败")


@router.delete("/by-province")
async def delete_by_province(
    province: str = Query(..., description="要删除的省份名称"),
    admin: User = Depends(require_admin),
):
    """按省份批量删除经验记录。"""
    if not province or not province.strip():
        raise HTTPException(status_code=400, detail="省份名称不能为空")

    if _is_remote():
        return await _remote_request(
            "DELETE",
            "/experience/by-province",
            params={"province": province},
        )

    try:
        def _delete():
            db = _get_experience_db()
            conn = db._connect()
            try:
                cursor = conn.execute("SELECT id FROM experiences WHERE province = ?", (province,))
                ids_to_delete = [str(row[0]) for row in cursor.fetchall()]

                if not ids_to_delete:
                    return 0

                conn.execute("DELETE FROM experiences WHERE province = ?", (province,))
                conn.commit()

                try:
                    coll = db.collection
                    if coll is not None and ids_to_delete:
                        coll.delete(ids=ids_to_delete)
                except Exception as e:
                    logger.opt(exception=e).warning(f"批量清理向量索引失败（省份={province}）")

                return len(ids_to_delete)
            finally:
                conn.close()

        count = await asyncio.to_thread(_delete)
        logger.info(f"管理员 {admin.email} 按省份删除经验记录：{province}，共 {count} 条")
        return {"message": f"已删除 {count} 条记录", "deleted": count}
    except HTTPException:
        raise
    except Exception:
        logger.exception("按省份删除经验记录失败")
        raise HTTPException(status_code=500, detail="删除失败")


class BatchPromoteRequest(BaseModel):
    province: str | None = None
    dry_run: bool = True


@router.post("/batch-promote")
async def batch_promote(
    req: BatchPromoteRequest,
    admin: User = Depends(require_admin),
):
    """智能批量晋升候选层记录。"""
    if _is_remote():
        return await _remote_request(
            "POST",
            "/experience/batch-promote",
            payload=req.model_dump(),
            timeout=300.0,
        )

    try:
        def _batch():
            import json as _json

            db = _get_experience_db()
            records = db.get_candidate_records(province=req.province, limit=0)
            records = [record for record in records if record.get("source") != "project_import_suspect"]

            promoted = 0
            skipped = 0
            errors = []

            for record in records:
                quota_ids_raw = record.get("quota_ids", "[]")
                if isinstance(quota_ids_raw, str):
                    try:
                        quota_ids = _json.loads(quota_ids_raw)
                    except Exception:
                        quota_ids = []
                else:
                    quota_ids = quota_ids_raw

                if not quota_ids:
                    skipped += 1
                    if len(errors) < 5:
                        bill = record.get("bill_name") or record.get("bill_text", "")[:30]
                        errors.append(f"{bill}: 无定额编号")
                    continue

                bill_text = record.get("bill_text", "")
                try:
                    validation = db._validate_quota_ids(
                        bill_text,
                        quota_ids,
                        province=record.get("province", ""),
                    )
                except Exception:
                    skipped += 1
                    continue

                if not validation.get("valid", False):
                    skipped += 1
                    if len(errors) < 5:
                        bill = record.get("bill_name") or bill_text[:30]
                        err_msg = "; ".join(validation.get("errors", []))[:50]
                        errors.append(f"{bill}: {err_msg}")
                    continue

                if req.dry_run:
                    promoted += 1
                    continue

                ok = db.promote_to_authority(
                    record["id"],
                    reason="智能批量晋升（定额校验通过）",
                )
                if ok:
                    promoted += 1
                else:
                    skipped += 1

            return {
                "total": len(records),
                "promoted": promoted,
                "skipped": skipped,
                "errors": errors,
                "dry_run": req.dry_run,
            }

        return await asyncio.to_thread(_batch)
    except Exception:
        logger.exception("批量晋升失败")
        raise HTTPException(status_code=500, detail="批量晋升失败")
