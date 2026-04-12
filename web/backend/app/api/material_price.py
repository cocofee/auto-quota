"""
智能填主材 API

提供主材价格查询和回填功能：
  - GET  /provinces          获取有价格数据的省份列表
  - GET  /cities             获取指定省份的城市列表
  - GET  /periods            获取指定省份/城市的期次列表
  - POST /parse              上传Excel，解析出主材行（本地处理，不转发）
  - POST /lookup             批量查价（根据省份/城市/期次）
  - POST /contribute         用户提交手填价格（存入候选层）
  - POST /export             把价格写回原Excel的主材行单价列，返回下载

远程模式（MATCH_BACKEND=remote）下，provinces/cities/periods/lookup/contribute
转发到本地匹配服务（local_match_server.py），因为价格库在本地电脑上。
parse/export 始终在本地处理（只需要操作Excel，不需要价格库）。
"""

import asyncio
import tempfile
import re
import uuid as _uuid_mod
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile, HTTPException, Query
from fastapi.responses import FileResponse
from loguru import logger

from app.config import MATCH_BACKEND, LOCAL_MATCH_URL, LOCAL_MATCH_API_KEY
from app.services.local_http import local_match_async_client

router = APIRouter()

# 主材库路径（本地模式使用）
_MATERIAL_DB_PATH = Path(__file__).parent.parent.parent.parent.parent / "db" / "common" / "material.db"

# 上传文件缓存（parse后保留，export时用）
# key=file_key(uuid), value={"path": 文件路径, "name": 原始文件名}
_uploaded_files: dict[str, dict] = {}


def _is_remote() -> bool:
    """是否使用远程模式（价格库在本地电脑上）"""
    return MATCH_BACKEND == "remote" and bool(LOCAL_MATCH_URL)


def _get_db():
    """获取MaterialDB实例（延迟导入，避免启动时报错）"""
    from src.material_db import MaterialDB
    return MaterialDB(str(_MATERIAL_DB_PATH))


async def _remote_get(path: str, params: dict = None) -> dict:
    """转发GET请求到本地匹配服务"""
    import httpx
    url = f"{LOCAL_MATCH_URL.rstrip('/')}{path}"
    headers = {"X-API-Key": LOCAL_MATCH_API_KEY}
    try:
        async with local_match_async_client(timeout=30.0) as client:
            resp = await client.get(url, headers=headers, params=params)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"远程主材查询返回 {resp.status_code}: {resp.text[:200]}")
        return {}
    except Exception as e:
        logger.error(f"远程主材查询失败: [{type(e).__name__}] {e} | url={url}")
        raise HTTPException(502, f"连接本地匹配服务失败: {e}")


async def _remote_post(path: str, payload: dict) -> dict:
    """转发POST请求到本地匹配服务"""
    import httpx
    url = f"{LOCAL_MATCH_URL.rstrip('/')}{path}"
    headers = {"X-API-Key": LOCAL_MATCH_API_KEY}
    try:
        async with local_match_async_client(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"远程主材操作返回 {resp.status_code}: {resp.text[:200]}")
        return {}
    except Exception as e:
        logger.error(f"远程主材操作失败: [{type(e).__name__}] {e} | url={url}")
        raise HTTPException(502, f"连接本地匹配服务失败: {e}")


# ============================================================
# 1. 获取省份列表
# ============================================================

@router.get("/material-price/provinces")
async def get_provinces():
    """返回有价格数据的省份列表（按数据量降序）"""
    if _is_remote():
        return await _remote_get("/material-price/provinces")

    import sqlite3
    conn = sqlite3.connect(str(_MATERIAL_DB_PATH))
    try:
        rows = conn.execute(
            """SELECT province, COUNT(*) as cnt
               FROM price_fact
               WHERE province != '' AND province != '全国'
               GROUP BY province
               ORDER BY cnt DESC"""
        ).fetchall()
        return {
            "provinces": [{"name": r[0], "count": r[1]} for r in rows]
        }
    finally:
        conn.close()


# ============================================================
# 2. 获取城市列表
# ============================================================

@router.get("/material-price/cities")
async def get_cities(province: str = Query(..., description="省份名称")):
    """返回指定省份下有价格数据的城市列表"""
    if _is_remote():
        return await _remote_get("/material-price/cities", {"province": province})

    import sqlite3
    conn = sqlite3.connect(str(_MATERIAL_DB_PATH))
    try:
        rows = conn.execute(
            """SELECT city, COUNT(*) as cnt
               FROM price_fact
               WHERE province = ? AND city != ''
               GROUP BY city
               ORDER BY cnt DESC""",
            (province,)
        ).fetchall()
        return {
            "cities": [{"name": r[0], "count": r[1]} for r in rows]
        }
    finally:
        conn.close()


# ============================================================
# 3. 获取期次列表
# ============================================================

@router.get("/material-price/periods")
async def get_periods(
    province: str = Query(..., description="省份名称"),
    city: str = Query("", description="城市名称（可选）"),
):
    """返回指定省份/城市的信息价期次列表（按时间倒序）"""
    if _is_remote():
        params = {"province": province}
        if city:
            params["city"] = city
        return await _remote_get("/material-price/periods", params)

    import sqlite3
    conn = sqlite3.connect(str(_MATERIAL_DB_PATH))
    try:
        # 构建查询条件
        conditions = ["province = ?", "period_start != ''"]
        params = [province]
        if city:
            conditions.append("city = ?")
            params.append(city)

        where = " AND ".join(conditions)
        rows = conn.execute(
            f"""SELECT period_start, period_end, COUNT(*) as cnt
                FROM price_fact
                WHERE {where}
                GROUP BY period_start, period_end
                ORDER BY period_end DESC
                LIMIT 24""",
            params
        ).fetchall()
        return {
            "periods": [
                {
                    "start": r[0],
                    "end": r[1],
                    "count": r[2],
                    # 生成显示标签，如"2026年2月"
                    "label": _format_period_label(r[0], r[1]),
                }
                for r in rows
            ]
        }
    finally:
        conn.close()


def _format_period_label(start: str, end: str) -> str:
    """把期次日期格式化成中文标签，如 '2026-02-01' → '2026年2月'"""
    try:
        parts = start.split("-")
        year, month = int(parts[0]), int(parts[1])
        return f"{year}年{month}月"
    except (IndexError, ValueError):
        return f"{start} ~ {end}"


# ============================================================
# 4. 解析Excel中的主材行
# ============================================================

@router.post("/material-price/parse")
async def parse_materials(file: UploadFile = File(...)):
    """上传Excel，识别出主材行，返回主材列表

    文件会保留在临时目录，export时用file_key找回来写入价格。
    """
    # 校验文件类型
    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传Excel文件（.xlsx/.xls）")

    # 保存到临时文件（不删除，留给export用）
    content = await file.read()
    suffix = Path(filename).suffix
    file_key = str(_uuid_mod.uuid4())
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=suffix, delete=False, prefix="material_"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    # 记录文件路径和原始文件名
    _uploaded_files[file_key] = {"path": tmp_path, "name": Path(filename).stem}

    # 在线程池中解析（CPU密集型）
    try:
        result = await asyncio.to_thread(_do_parse, tmp_path)
        materials = result["materials"]
        return {
            "materials": materials,
            "all_rows": result["all_rows"],
            "is_mixed": result["is_mixed"],
            "count": len(materials),
            "file_key": file_key,  # 前端保存，export时回传
        }
    except Exception as e:
        logger.error(f"解析主材失败: {e}")
        # 解析失败才删文件
        Path(tmp_path).unlink(missing_ok=True)
        _uploaded_files.pop(file_key, None)
        raise HTTPException(500, f"解析失败: {e}")


def _do_parse(excel_path: str) -> dict:
    """解析Excel，返回主材行 + 所有行（用于层级预览）

    返回:
    {
        "materials": [...],   # 只含主材行（查价/导出用）
        "all_rows": [...],    # 所有行含类型标记（前端层级展示用）
        "is_mixed": bool,     # 是否混合表（分部分项格式）
    }
    """
    import openpyxl

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    materials = []
    all_rows = []
    is_mixed = False

    for ws in wb.worksheets:
        result = _parse_sheet(ws)
        materials.extend(result["materials"])
        all_rows.extend(result["all_rows"])
        if result["is_mixed"]:
            is_mixed = True

    wb.close()
    return {"materials": materials, "all_rows": all_rows, "is_mixed": is_mixed}


def _parse_sheet(ws) -> dict:
    """解析单个sheet，返回主材行 + 所有行（带类型标记）

    行类型：
    - "section": 分部标题行（如"给水工程"、"新风系统"）
    - "bill": 清单行（有项目编码，如030701003005）
    - "quota": 定额行（编码像C7-1-20）
    - "material": 主材行（编码为"主"等）

    对于纯材料表，all_rows == materials（没有层级）。
    """
    materials = []
    all_rows = []

    # 找表头行
    header_row = None
    col_map = {}
    is_pure_material_table = False
    for row_idx in range(1, min(20, ws.max_row + 1)):
        for col_idx in range(1, min(15, ws.max_column + 1)):
            val = str(ws.cell(row=row_idx, column=col_idx).value or "").strip()
            if val in ("名称", "项目名称", "材料名称"):
                header_row = row_idx
                break
        if header_row:
            break

    if not header_row:
        return {"materials": [], "all_rows": [], "is_mixed": False}

    # 建立列映射——扫描表头行及下一行（处理双行表头）
    _PRICE_KEYWORDS = ("单价", "综合单价", "不含税单价", "含税单价",
                       "市场价", "信息价", "除税单价", "除税信息价")
    # 也检测"序号"列（用于判断清单行，但不作为code列）
    seq_col = None
    for scan_row in range(header_row, min(header_row + 2, ws.max_row + 1)):
        for col_idx in range(1, min(15, ws.max_column + 1)):
            val = str(ws.cell(row=scan_row, column=col_idx).value or "").strip()
            if not val:
                continue
            if val in ("编码", "编号", "材料编码", "项目编码"):
                col_map.setdefault("code", col_idx)
            elif val in ("名称", "项目名称", "材料名称"):
                col_map.setdefault("name", col_idx)
            elif val in ("规格", "规格型号", "规格、型号等特殊要求"):
                col_map.setdefault("spec", col_idx)
            elif val in ("单位", "计量单位"):
                col_map.setdefault("unit", col_idx)
            elif val in ("数量", "工程量", "消耗量", "用量"):
                col_map.setdefault("qty", col_idx)
            elif val in _PRICE_KEYWORDS:
                col_map.setdefault("price", col_idx)
            if val in ("序号",):
                seq_col = col_idx
            # 纯材料表特征
            if val in ("材料名称", "材料编码", "市场价", "信息价",
                        "除税单价", "除税信息价", "消耗量"):
                is_pure_material_table = True

    sheet_name = ws.title or ""
    if any(kw in sheet_name for kw in ("材料", "主材", "材价", "物资")):
        is_pure_material_table = True

    if "name" not in col_map:
        return {"materials": [], "all_rows": [], "is_mixed": False}

    # 确定价格列位置
    price_col = col_map.get("price")
    if price_col is None and "qty" in col_map:
        price_col = col_map["qty"] + 1

    # 也检测"项目特征描述"列（用于清单行展示）
    desc_col = None
    for col_idx in range(1, min(15, ws.max_column + 1)):
        val = str(ws.cell(row=header_row, column=col_idx).value or "").strip()
        if val in ("项目特征描述", "项目特征", "特征描述"):
            desc_col = col_idx
            break

    # 遍历数据行
    current_bill: dict | None = None
    for row_idx in range(header_row + 1, ws.max_row + 1):
        code_val = ""
        if "code" in col_map:
            code_val = str(ws.cell(row=row_idx, column=col_map["code"]).value or "").strip()

        name_val = str(ws.cell(row=row_idx, column=col_map["name"]).value or "").strip()
        if not name_val:
            continue

        unit_val = ""
        if "unit" in col_map:
            unit_val = str(ws.cell(row=row_idx, column=col_map["unit"]).value or "").strip()

        qty_val = None
        if "qty" in col_map:
            raw = ws.cell(row=row_idx, column=col_map["qty"]).value
            if raw is not None:
                try:
                    qty_val = float(raw)
                except (ValueError, TypeError):
                    pass

        spec_val = ""
        if "spec" in col_map:
            spec_val = str(ws.cell(row=row_idx, column=col_map["spec"]).value or "").strip()

        price_val = None
        if price_col is not None:
            raw = ws.cell(row=row_idx, column=price_col).value
            if raw is not None:
                try:
                    price_val = float(raw)
                except (ValueError, TypeError):
                    pass

        # 纯材料表：所有行都当主材
        if is_pure_material_table:
            mat = {
                "type": "material",
                "row": row_idx,
                "sheet": ws.title,
                "code": code_val,
                "name": name_val,
                "name_col": col_map.get("name"),
                "spec": spec_val,
                "spec_col": col_map.get("spec"),
                "unit": unit_val,
                "qty": qty_val,
                "existing_price": price_val,
                "price_col": price_col,
                "lookup_price": None,
                "lookup_source": None,
            }
            materials.append(mat)
            all_rows.append(mat)
            continue

        # 混合表：判断行类型
        seq_val = ""
        if seq_col is not None:
            seq_val = str(ws.cell(row=row_idx, column=seq_col).value or "").strip()

        row_type = _classify_row(code_val, name_val, seq_val)

        if row_type == "material":
            suggested_name = ""
            suggested_spec = ""
            if current_bill:
                suggested_name, suggested_spec = _suggest_material_from_bill_context(
                    name_val, current_bill.get("name", ""), current_bill.get("desc", "")
                )
            mat = {
                "type": "material",
                "row": row_idx,
                "sheet": ws.title,
                "code": code_val,
                "name": name_val,
                "name_col": col_map.get("name"),
                "suggested_name": suggested_name,
                "spec": spec_val,
                "spec_col": col_map.get("spec"),
                "suggested_spec": suggested_spec,
                "unit": unit_val,
                "qty": qty_val,
                "existing_price": price_val,
                "price_col": price_col,
                "lookup_price": None,
                "lookup_source": None,
            }
            materials.append(mat)
            all_rows.append(mat)
        elif row_type == "bill":
            # 清单行：读取项目特征描述
            desc_val = ""
            if desc_col is not None:
                desc_val = str(ws.cell(row=row_idx, column=desc_col).value or "").strip()
            current_bill = {
                "name": name_val,
                "desc": desc_val,
            }
            all_rows.append({
                "type": "bill",
                "row": row_idx,
                "sheet": ws.title,
                "code": code_val,
                "name": name_val,
                "desc": desc_val,
                "unit": unit_val,
                "qty": qty_val,
            })
        elif row_type == "quota":
            all_rows.append({
                "type": "quota",
                "row": row_idx,
                "sheet": ws.title,
                "code": code_val,
                "name": name_val,
                "unit": unit_val,
                "qty": qty_val,
            })
        elif row_type == "section":
            current_bill = None
            all_rows.append({
                "type": "section",
                "row": row_idx,
                "sheet": ws.title,
                "name": name_val,
            })

    return {
        "materials": materials,
        "all_rows": all_rows,
        "is_mixed": not is_pure_material_table,
    }


def _classify_row(code: str, name: str, seq: str) -> str:
    """判断分部分项混合表中一行的类型

    返回: "bill"(清单行) / "quota"(定额行) / "material"(主材行) / "section"(分部标题)
    """
    c = code.strip() if code else ""

    # 主材行判断（优先，因为B列="主"最明确）
    if _is_material_row(c, name):
        return "material"

    # 清单行：有序号（A列有数字）且编码像项目编码（9-12位数字）
    if seq and re.fullmatch(r"\d+", seq):
        if re.fullmatch(r"\d{9,15}", c):
            return "bill"

    # 定额行：编码像定额编号（如C10-2-123、A-9-63、SC20）
    if c and re.match(r"^[A-Za-z]{1,4}\d", c):
        return "quota"

    # 分部标题行：没有编码、没有序号、没有单位，只有名称
    if not c and not seq:
        return "section"

    return "section"  # 无法识别的行当标题处理


def _is_material_row(code: str, name: str) -> bool:
    """判断是否为主材行

    识别规则（和 bill_reader.py 保持一致）：
    1. 编码以 ZCGL 开头（广联达主材编码）
    2. 编码包含 Z@ 或 @
    3. 编码为"主"字
    4. 编码为"补充主材"开头
    5. 7-8位纯数字编码（广联达材料编码）
    6. 名称包含典型材料关键词且不是定额行
    """
    c = code.strip() if code else ""

    # 编码特征判断
    if c.upper().startswith("ZCGL"):
        return True
    if c.upper().startswith("ZC"):
        return True
    if re.fullmatch(r"Z[0-9A-Z]+", c.upper()):
        return True
    if "Z@" in c or "@" in c:
        return True
    if c == "主":
        return True
    if c.startswith("补充主材"):
        return True
    if re.fullmatch(r"\d{7,8}", c):
        return True

    # 名称特征判断（补充：没有编码但名称像材料的行）
    if not c and name:
        material_keywords = [
            "钢管", "管材", "管件", "阀门", "水表", "电缆", "电线",
            "灯具", "开关", "插座", "配电箱", "桥架", "线槽",
            "PPR", "PVC", "PE管", "铜管", "不锈钢管",
            "水泥", "砂浆", "砂石", "混凝土", "钢筋",
            "防水卷材", "保温材料", "涂料", "油漆",
            "风机", "风口", "消火栓", "喷头", "烟感", "温感",
        ]
        for kw in material_keywords:
            if kw in name:
                return True

    return False


def _suggest_material_from_bill_context(material_name: str, bill_name: str, desc: str) -> tuple[str, str]:
    info = _extract_material_from_desc(desc)
    candidate_name = info["name"]
    candidate_spec = info["spec"]
    candidate_type = info["type"]
    candidate_material = info["material"]
    if not candidate_name and not candidate_spec and not candidate_type:
        return "", ""

    normalized_material = _normalize_material_hint(material_name)
    normalized_bill = _normalize_material_hint(bill_name)
    normalized_candidate = _normalize_material_hint(candidate_name)

    suggested_name = ""
    if _should_use_bill_type_for_material(material_name) and candidate_type:
        suggested_name = candidate_type
        if candidate_material and candidate_material not in suggested_name:
            suggested_name = f"{candidate_material}{suggested_name}"
    elif _is_generic_material_name(material_name) and candidate_name:
        suggested_name = candidate_name
    elif normalized_material and normalized_bill and normalized_material == normalized_bill and candidate_name:
        suggested_name = candidate_name
    elif normalized_material and normalized_candidate and normalized_material == normalized_candidate:
        suggested_name = candidate_name

    return suggested_name, candidate_spec


def _extract_material_from_desc(desc: str) -> dict[str, str]:
    if not desc:
        return {"name": "", "spec": "", "type": "", "material": ""}

    pairs: dict[str, str] = {}
    for raw_line in desc.splitlines():
        line = re.sub(r"^\s*\d+[\.、]\s*", "", raw_line.strip())
        if not line:
            continue
        parts = re.split(r"[:：]", line, maxsplit=1)
        if len(parts) != 2:
            continue
        key = parts[0].strip()
        value = parts[1].strip()
        if key and value:
            pairs[key] = value

    combined = ""
    for key in ("材质、规格", "材质规格", "材质/规格", "材质及规格", "材质、型号", "材质"):
        if pairs.get(key):
            combined = pairs[key]
            break

    candidate_type = ""
    for key in ("类型", "类别", "名称"):
        if pairs.get(key):
            candidate_type = pairs[key].strip()
            break

    candidate_material = ""
    for key in ("材质", "材质要求"):
        if pairs.get(key):
            candidate_material = pairs[key].strip()
            break

    name = ""
    spec = ""
    if combined:
        name, spec = _split_material_and_spec(combined)

    if not name and candidate_material:
        name = candidate_material

    if not spec:
        for key in ("规格型号", "规格", "型号"):
            if pairs.get(key):
                spec = pairs[key].strip()
                break

    return {
        "name": name.strip(),
        "spec": spec.strip(),
        "type": candidate_type,
        "material": candidate_material,
    }


def _split_material_and_spec(text: str) -> tuple[str, str]:
    value = str(text or "").strip()
    if not value:
        return "", ""

    patterns = [
        r"^(?P<name>.+?)\s+(?P<spec>DN\d+[A-Za-z0-9\-\./]*(?:\s+[A-Za-z0-9\-\./]+)*)$",
        r"^(?P<name>.+?)\s+(?P<spec>De\d+[A-Za-z0-9\-\./]*(?:\s+[A-Za-z0-9\-\./]+)*)$",
        r"^(?P<name>.+?)\s+(?P<spec>\d+(?:\.\d+)?(?:mm|mm2|㎡|m2))$",
        r"^(?P<name>.+?)\s+(?P<spec>\d+(?:\*\d+){1,3})$",
    ]
    for pattern in patterns:
        matched = re.match(pattern, value, flags=re.IGNORECASE)
        if matched:
            return matched.group("name").strip(), matched.group("spec").strip()
    return value, ""


def _normalize_material_hint(text: str) -> str:
    return re.sub(r"[\s\-\(\)（）]", "", str(text or "")).lower()


def _is_generic_material_name(name: str) -> bool:
    value = _normalize_material_hint(name)
    generic_names = {
        "复合管", "管材", "管件", "钢管", "塑料管", "阀门", "法兰", "桥架",
        "电缆", "电线", "配电箱", "灯具", "喷头", "风口", "水表",
    }
    return value in {_normalize_material_hint(x) for x in generic_names}


def _should_use_bill_type_for_material(name: str) -> bool:
    value = _normalize_material_hint(name)
    generic_valve_names = {
        "阀门", "螺纹阀门", "减压阀", "螺纹减压阀", "法兰阀门", "法兰减压阀",
    }
    return value in {_normalize_material_hint(x) for x in generic_valve_names}


# ============================================================
# 4b. 从任务结果中提取主材行
# ============================================================

@router.get("/material-price/from-task/{task_id}")
async def parse_from_task(task_id: str):
    """从已完成的套定额任务中提取主材行

    读取任务的output Excel（带主材版本），解析出主材行。
    """
    import uuid as _uuid
    from sqlalchemy import select
    from app.database import async_session
    from app.models.task import Task

    # 查任务
    try:
        task_uuid = _uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(400, "无效的任务ID")

    async with async_session() as session:
        result = await session.execute(
            select(Task).where(Task.id == task_uuid)
        )
        task = result.scalar_one_or_none()

    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status != "completed":
        raise HTTPException(400, "任务尚未完成")
    if not task.output_path or not Path(task.output_path).exists():
        raise HTTPException(404, "输出文件不存在")

    # 解析output Excel中的主材行
    try:
        result = await asyncio.to_thread(_do_parse, task.output_path)
        materials = result["materials"]
        # 用任务的output_path作为file_key（不需要复制，直接指向）
        file_key = f"task-{task_id}"
        _uploaded_files[file_key] = {
            "path": task.output_path,
            "name": Path(task.original_filename or "").stem or f"task_{task_id[:8]}",
        }
        return {
            "materials": materials,
            "all_rows": result["all_rows"],
            "is_mixed": result["is_mixed"],
            "count": len(materials),
            "task_name": task.original_filename or "",
            "file_key": file_key,
        }
    except Exception as e:
        logger.error(f"从任务提取主材失败: {e}")
        raise HTTPException(500, f"提取失败: {e}")


# ============================================================
# 5. 批量查价
# ============================================================

@router.post("/material-price/lookup")
async def lookup_prices(body: dict):
    """根据主材列表和地区信息，批量查价

    请求体:
    {
        "materials": [{"name": "镀锌钢管", "spec": "DN25", "unit": "m"}, ...],
        "province": "湖北",
        "city": "武汉",           // 可选
        "period_end": "2026-02-28",  // 可选，指定期次
        "price_type": "all"       // 可选：all=不限, info=信息价, market=市场价
    }
    """
    materials = body.get("materials", [])
    province = body.get("province", "")
    city = body.get("city", "")
    period_end = body.get("period_end", "")
    price_type = body.get("price_type", "all")

    if not materials:
        raise HTTPException(400, "materials 不能为空")
    if not province:
        raise HTTPException(400, "province 不能为空")

    # 远程模式：转发到本地匹配服务
    if _is_remote():
        result = await _remote_post("/material-price/lookup", {
            "materials": materials,
            "province": province,
            "city": city,
            "period_end": period_end,
            "price_type": price_type,
        })
        return result or {"results": [], "stats": {"total": 0, "found": 0, "not_found": 0}}

    # 本地模式：直接查价
    results = await asyncio.to_thread(
        _do_lookup, materials, province, city, period_end, price_type
    )
    # 统计
    found = sum(1 for r in results if r.get("lookup_price") is not None)
    return {
        "results": results,
        "stats": {
            "total": len(results),
            "found": found,
            "not_found": len(results) - found,
        }
    }


def _do_lookup(materials: list[dict], province: str, city: str,
               period_end: str, price_type: str = "all") -> list[dict]:
    """批量查价核心逻辑

    price_type: all=不限, info=只查信息价, market=只查市场价
    级联策略：信息价 → 市场价(企业集采库) → 广材网缓存
    """
    import sqlite3

    db = _get_db()
    conn = sqlite3.connect(str(db.db_path))
    conn.row_factory = sqlite3.Row

    # 加载广材网缓存（作为第三层兜底）
    _gldjc_cache: dict = {}
    _gldjc_cache_path = Path(__file__).resolve().parents[4] / "data" / "material_prices.json"
    if _gldjc_cache_path.exists():
        try:
            import json as _json
            _gldjc_cache = _json.loads(_gldjc_cache_path.read_text(encoding="utf-8"))
        except Exception:
            _gldjc_cache = {}

    # 价格类型映射到 source_type 过滤条件
    source_filter = None
    if price_type == "info":
        source_filter = "government"  # 信息价
    elif price_type == "market":
        source_filter = "market"      # 市场价

    results = []
    for mat in materials:
        name = mat.get("name", "").strip()
        spec = mat.get("spec", "").strip()
        unit = mat.get("unit", "").strip()

        if not name:
            results.append({**mat, "lookup_price": None, "lookup_source": "名称为空"})
            continue

        # 用 MaterialDB 的查价方法
        kwargs = dict(province=province, spec=spec, target_unit=unit)
        if source_filter:
            kwargs["source_type"] = source_filter
        price_info = db.search_price_by_name(name, **kwargs)

        if price_info:
            results.append({
                **mat,
                "lookup_price": price_info["price"],
                "lookup_source": price_info.get("source", "价格库"),
            })
        else:
            # 尝试从名称中提取规格再查一次
            m = re.search(r'[Dd][Nn]\s*\d+|De\s*\d+|Φ\s*\d+|\d+mm', name)
            if m:
                extracted_spec = m.group(0).replace(" ", "")
                short_name = name[:m.start()].strip()
                if short_name:
                    kwargs2 = dict(province=province, spec=extracted_spec, target_unit=unit)
                    if source_filter:
                        kwargs2["source_type"] = source_filter
                    price_info2 = db.search_price_by_name(short_name, **kwargs2)
                    if price_info2:
                        results.append({
                            **mat,
                            "lookup_price": price_info2["price"],
                            "lookup_source": price_info2.get("source", "价格库"),
                        })
                        continue

            # 广材网缓存兜底（第三层）
            if _gldjc_cache and price_type != "info":
                # 广材网是市场价，信息价模式下不查
                cache_key = f"{name}|{unit}"
                cached = _gldjc_cache.get(cache_key)
                if cached and cached.get("price_with_tax"):
                    results.append({
                        **mat,
                        "lookup_price": cached["price_with_tax"],
                        "lookup_source": "广材网市场价",
                    })
                    continue

            results.append({**mat, "lookup_price": None, "lookup_source": "未查到"})

    conn.close()
    return results


# ============================================================
# 6. 用户贡献价格（存入候选层）
# ============================================================

@router.post("/material-price/contribute")
async def contribute_price(body: dict):
    """用户手填价格，存入价格库候选层

    请求体:
    {
        "items": [
            {
                "name": "镀锌钢管",
                "spec": "DN25",
                "unit": "m",
                "price": 18.5,
                "province": "湖北",
                "city": "武汉"
            },
            ...
        ]
    }
    """
    items = body.get("items", [])
    if not items:
        raise HTTPException(400, "items 不能为空")

    # 远程模式：转发到本地匹配服务
    if _is_remote():
        result = await _remote_post("/material-price/contribute", {"items": items})
        return result or {"saved": 0, "message": "转发失败"}

    # 本地模式：直接写入
    saved = await asyncio.to_thread(_do_contribute, items)
    return {"saved": saved, "message": f"已保存 {saved} 条价格，感谢贡献！"}


def _do_contribute(items: list[dict]) -> int:
    """保存用户贡献的价格到候选层"""
    db = _get_db()
    saved = 0

    for item in items:
        name = item.get("name", "").strip()
        spec = item.get("spec", "").strip()
        unit = item.get("unit", "").strip()
        price = item.get("price")
        province = item.get("province", "").strip()
        city = item.get("city", "").strip()

        if not name or price is None:
            continue

        try:
            price_val = float(price)
        except (ValueError, TypeError):
            continue

        # 异常值检测：价格为负数或超过100万，视为异常
        if price_val <= 0 or price_val > 1_000_000:
            logger.warning(f"用户贡献异常价格: {name} {spec} = {price_val}，已跳过")
            continue

        # 先确保材料主数据存在
        material_id = db.add_material(name, spec=spec, unit=unit)

        # 存入价格库（候选层：authority_level='reference'）
        db.add_price(
            material_id=material_id,
            price_incl_tax=price_val,
            source_type="user_contribute",
            province=province,
            city=city,
            unit=unit,
            authority_level="reference",
            source_doc="用户手填",
            dedup=True,
        )
        saved += 1

    return saved


# ============================================================
# 7. 导出：把价格写回原Excel的主材行单价列
# ============================================================

@router.post("/material-price/export")
async def export_with_prices(body: dict):
    """把查到的价格写回到原Excel的主材行单价列，返回修改后的Excel下载

    请求体:
    {
        "file_key": "xxx",  // parse返回的file_key
        "materials": [      // 带价格的主材列表（前端合并后的最终结果）
            {
                "row": 10,
                "sheet": "分部分项...",
                "price_col": 7,
                "final_price": 18.5  // 最终价格（手填优先，否则查价，否则原有）
            },
            ...
        ]
    }
    """
    file_key = body.get("file_key", "")
    materials = body.get("materials", [])

    if not file_key:
        raise HTTPException(400, "file_key 不能为空")

    # 找到原文件
    file_info = _uploaded_files.get(file_key)
    if not file_info or not Path(file_info["path"]).exists():
        raise HTTPException(404, "原始文件不存在或已过期，请重新上传")

    source_path = file_info["path"]
    original_name = file_info.get("name", "output")  # 原始文件名（不含后缀）

    # 复制一份到临时文件（不改原文件）
    import shutil
    suffix = Path(source_path).suffix
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=suffix, delete=False, prefix="material_export_"
    ) as tmp:
        tmp_path = tmp.name
    shutil.copy2(source_path, tmp_path)

    # 在线程池中写入价格
    try:
        written = await asyncio.to_thread(_do_write_material_updates, tmp_path, materials)
        logger.info(f"智能填主材导出：写入 {written} 个价格")

        # 生成下载文件名（用原始文件名，不是临时文件名）
        download_name = f"{original_name}_已填价{suffix}"

        return FileResponse(
            path=tmp_path,
            filename=download_name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            # FileResponse发送完毕后自动删除临时文件
            background=None,
        )
    except Exception as e:
        Path(tmp_path).unlink(missing_ok=True)
        logger.error(f"写入价格失败: {e}")
        raise HTTPException(500, f"导出失败: {e}")


def _do_write_prices(excel_path: str, materials: list[dict]) -> int:
    """把价格写入Excel的主材行单价列

    按 sheet名+行号+列号 定位每个主材行，写入 final_price。
    """
    import openpyxl

    wb = openpyxl.load_workbook(excel_path)
    written = 0

    # 按sheet分组
    sheet_materials: dict[str, list[dict]] = {}
    for mat in materials:
        sheet_name = mat.get("sheet", "")
        if sheet_name not in sheet_materials:
            sheet_materials[sheet_name] = []
        sheet_materials[sheet_name].append(mat)

    for sheet_name, mats in sheet_materials.items():
        if sheet_name not in wb.sheetnames:
            logger.warning(f"sheet [{sheet_name}] 不存在，跳过 {len(mats)} 条")
            continue

        ws = wb[sheet_name]
        for mat in mats:
            row = mat.get("row")
            price_col = mat.get("price_col")
            final_price = mat.get("final_price")

            if row is None or price_col is None or final_price is None:
                continue

            try:
                price_val = float(final_price)
            except (ValueError, TypeError):
                continue

            # 写入单价
            cell = ws.cell(row=row, column=price_col, value=round(price_val, 2))
            cell.number_format = '0.00'
            written += 1

    wb.save(excel_path)
    wb.close()
    return written


def _do_write_material_updates(excel_path: str, materials: list[dict]) -> int:
    """Write edited material names and prices back into the reviewed Excel."""
    import openpyxl

    wb = openpyxl.load_workbook(excel_path)
    written = 0

    sheet_materials: dict[str, list[dict]] = {}
    for mat in materials:
        sheet_name = mat.get("sheet", "")
        if sheet_name not in sheet_materials:
            sheet_materials[sheet_name] = []
        sheet_materials[sheet_name].append(mat)

    for sheet_name, mats in sheet_materials.items():
        if sheet_name not in wb.sheetnames:
            logger.warning(f"sheet [{sheet_name}] not found, skipped {len(mats)} material rows")
            continue

        ws = wb[sheet_name]
        for mat in mats:
            row = mat.get("row")
            if row is None:
                continue

            row_written = False

            name_col = mat.get("name_col")
            final_name = str(mat.get("final_name") or "").strip()
            spec_col = mat.get("spec_col")
            final_spec = str(mat.get("final_spec") or "").strip()
            write_name = final_name
            if spec_col is None and final_spec and final_spec not in final_name:
                write_name = f"{final_name} {final_spec}".strip()

            if name_col is not None and write_name:
                ws.cell(row=row, column=name_col, value=write_name)
                row_written = True

            if spec_col is not None:
                ws.cell(row=row, column=spec_col, value=final_spec)
                row_written = True

            price_col = mat.get("price_col")
            final_price = mat.get("final_price")
            if price_col is not None and final_price is not None:
                try:
                    price_val = float(final_price)
                except (ValueError, TypeError):
                    price_val = None

                if price_val is not None:
                    cell = ws.cell(row=row, column=price_col, value=round(price_val, 2))
                    cell.number_format = '0.00'
                    row_written = True

            if row_written:
                written += 1

    wb.save(excel_path)
    wb.close()
    return written


# ============================================================
# 8. 广材网实时查价（管理员专用）
# ============================================================

@router.post("/material-price/gldjc-lookup")
async def gldjc_lookup(body: dict):
    """管理员专用：对DB查不到的材料，实时爬广材网查价

    请求体:
    {
        "materials": [{"name": "镀锌钢管 DN25", "unit": "m", "spec": "DN25"}, ...],
        "cookie": "token=bearer xxx"  // 广材网登录Cookie
    }

    返回: { results: [...], total, found }
    """
    materials = body.get("materials", [])
    cookie = body.get("cookie", "").strip()

    if not cookie:
        raise HTTPException(400, "请输入广材网Cookie")
    if not materials:
        raise HTTPException(400, "材料列表为空")

    # 远程模式：转发到本地匹配服务（广材网Cookie绑定IP，必须从本机发请求）
    if _is_remote():
        import httpx
        url = f"{LOCAL_MATCH_URL.rstrip('/')}/material-price/gldjc-lookup"
        headers = {"X-API-Key": LOCAL_MATCH_API_KEY}
        try:
            async with local_match_async_client(timeout=600.0) as client:
                resp = await client.post(url, headers=headers, json={
                    "materials": materials, "cookie": cookie,
                })
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"远程广材网查价返回 {resp.status_code}: {resp.text[:200]}")
            raise HTTPException(resp.status_code, resp.json().get("detail", "远程查价失败"))
        except httpx.HTTPError as e:
            logger.error(f"远程广材网查价失败: {e}")
            raise HTTPException(502, f"连接本地匹配服务失败: {e}")

    # 本地模式：直接在线程池中执行
    results = await asyncio.to_thread(
        _do_gldjc_lookup, materials, cookie
    )

    found = sum(1 for r in results if r.get("gldjc_price"))
    return {
        "results": results,
        "total": len(results),
        "found": found,
    }


def _do_gldjc_lookup(materials: list[dict], cookie: str) -> list[dict]:
    """实时调用广材网搜索+打分+缓存（同步，在线程池中运行）

    防封策略：
    1. 单次上限30条（前端也应校验）
    2. 随机间隔5~8秒（模拟人工浏览）
    3. 检测登录失效/被封，立即停止
    4. 每10条保存一次缓存（中途中断不丢数据）
    5. 搜索结果去重（同关键词只搜一次）
    """
    import sys
    import time
    import random

    # 安全上限：单次最多30条实时搜索
    MAX_BATCH = 30

    # 把 tools/ 加入路径，复用 gldjc_price.py 的核心函数
    tools_dir = str(Path(__file__).resolve().parents[4] / "tools")
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    from gldjc_price import (
        parse_material, search_material_web, filter_and_score,
        get_median_price, determine_confidence,
        load_cache, save_cache, update_cache, check_cache,
    )
    import requests as _requests
    from datetime import datetime

    # 创建带Cookie的session
    session = _requests.Session()
    for part in cookie.split("; "):
        if "=" in part:
            key, value = part.split("=", 1)
            session.cookies.set(key.strip(), value.strip())

    cache = load_cache()
    results = []

    # 本次搜索去重：同关键词只搜一次
    search_dedup: dict[str, list] = {}

    # 计数：实际发起的网络请求数（不含缓存命中）
    net_requests = 0
    blocked = False  # 是否被封/登录失效

    for i, mat in enumerate(materials):
        name = mat.get("name", "").strip()
        unit = mat.get("unit", "").strip()
        spec = mat.get("spec", "").strip()

        if not name:
            results.append({**mat, "gldjc_price": None, "gldjc_source": "名称为空"})
            continue

        # 先查缓存（未过期+非低置信度）
        cached = check_cache(cache, name, unit)
        if cached and cached.get("price_with_tax") and cached.get("confidence") != "低":
            results.append({
                **mat,
                "gldjc_price": cached["price_with_tax"],
                "gldjc_source": f"广材网缓存({cached.get('confidence', '中')})",
            })
            continue

        # 安全检查：网络请求超过上限，剩余全部跳过
        if net_requests >= MAX_BATCH:
            results.append({
                **mat, "gldjc_price": None,
                "gldjc_source": f"已达单次上限{MAX_BATCH}条，请分批查询",
            })
            continue

        # 被封检测：之前发现异常则不再请求
        if blocked:
            results.append({**mat, "gldjc_price": None, "gldjc_source": "已暂停（疑似被限制）"})
            continue

        # 实时搜索广材网
        parsed = parse_material(name, spec)
        base_name = parsed["base_name"]
        specs = parsed["specs"]

        all_results = []
        searched_keyword = ""
        for kw in parsed["search_keywords"]:
            # 搜索去重：同关键词复用结果
            if kw in search_dedup:
                all_results = search_dedup[kw]
                searched_keyword = kw
                if all_results:
                    break
                continue

            # 随机间隔（模拟人工浏览，5~8秒）
            if net_requests > 0:
                delay = random.uniform(5, 8)
                time.sleep(delay)

            web_results = search_material_web(session, kw)
            net_requests += 1
            searched_keyword = kw
            search_dedup[kw] = web_results

            # 检测被封/登录失效（搜索函数返回空列表可能是正常无结果，
            # 但如果连续3次都空，大概率有问题）
            if not web_results and net_requests >= 3:
                recent_empty = sum(
                    1 for r in list(search_dedup.values())[-3:]
                    if not r
                )
                if recent_empty >= 3:
                    logger.warning("广材网连续3次搜索无结果，疑似Cookie失效或被限制，停止查询")
                    blocked = True

            if web_results:
                all_results = web_results
                break

            # 关键词降级间隔（较短，因为主间隔已经够长）
            time.sleep(random.uniform(1, 2))

        if not all_results:
            # 搜不到，缓存"未匹配"避免重复搜
            update_cache(cache, name, unit, {
                "price_with_tax": None, "confidence": "低",
                "match_status": "未匹配", "source": "广材网",
                "query_date": datetime.now().strftime("%Y-%m-%d"),
                "result_count": 0,
                "searched_keyword": searched_keyword,
            })
            results.append({**mat, "gldjc_price": None, "gldjc_source": "广材网未找到"})
            continue

        # 打分过滤
        scored = filter_and_score(all_results, unit, specs, base_name)
        confidence = determine_confidence(scored, unit, specs)
        price_source = scored if scored else all_results
        if not scored:
            confidence = "低"

        median = get_median_price(price_source)

        if median and confidence in ("高", "中"):
            update_cache(cache, name, unit, {
                "price_with_tax": median,
                "price_without_tax": round(median / 1.13, 2),
                "confidence": confidence,
                "match_status": "精确匹配" if confidence == "高" else "模糊匹配",
                "source": "广材网",
                "query_date": datetime.now().strftime("%Y-%m-%d"),
                "result_count": len(price_source),
                "searched_keyword": searched_keyword,
            })
            results.append({
                **mat,
                "gldjc_price": median,
                "gldjc_source": f"广材网市场价({confidence})",
            })
        else:
            update_cache(cache, name, unit, {
                "price_with_tax": median,
                "price_without_tax": round(median / 1.13, 2) if median else None,
                "confidence": "低",
                "match_status": "低置信度",
                "source": "广材网",
                "query_date": datetime.now().strftime("%Y-%m-%d"),
                "result_count": len(price_source),
                "searched_keyword": searched_keyword,
            })
            results.append({
                **mat,
                "gldjc_price": None,
                "gldjc_source": "广材网低置信度",
            })

        # 每10条保存一次缓存（防中途中断丢数据）
        if net_requests % 10 == 0:
            save_cache(cache)

    # 最终保存缓存
    save_cache(cache)
    logger.info(f"广材网查价完成：{len(materials)}条材料，{net_requests}次网络请求，"
                f"{'被限制提前停止' if blocked else '正常完成'}")
    return results
