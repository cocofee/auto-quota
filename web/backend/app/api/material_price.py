"""
智能填主材 API

提供主材价格查询和回填功能：
  - GET  /provinces          获取有价格数据的省份列表
  - GET  /cities             获取指定省份的城市列表
  - GET  /periods            获取指定省份/城市的期次列表
  - POST /parse              上传Excel，解析出主材行
  - POST /lookup             批量查价（根据省份/城市/期次）
  - POST /contribute         用户提交手填价格（存入候选层）
"""

import asyncio
import tempfile
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, UploadFile, HTTPException, Query
from loguru import logger

router = APIRouter()

# 主材库路径（和 src/material_db.py 一致）
_MATERIAL_DB_PATH = Path(__file__).parent.parent.parent.parent.parent / "db" / "common" / "material.db"


def _get_db():
    """获取MaterialDB实例（延迟导入，避免启动时报错）"""
    from src.material_db import MaterialDB
    return MaterialDB(str(_MATERIAL_DB_PATH))


# ============================================================
# 1. 获取省份列表
# ============================================================

@router.get("/material-price/provinces")
async def get_provinces():
    """返回有价格数据的省份列表（按数据量降序）"""
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
    """上传Excel，识别出主材行，返回主材列表"""
    # 校验文件类型
    filename = file.filename or ""
    if not filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(400, "请上传Excel文件（.xlsx/.xls）")

    # 保存到临时文件
    content = await file.read()
    suffix = Path(filename).suffix
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=suffix, delete=False, prefix="material_"
    ) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    # 在线程池中解析（CPU密集型）
    try:
        materials = await asyncio.to_thread(_do_parse, tmp_path)
        return {"materials": materials, "count": len(materials)}
    except Exception as e:
        logger.error(f"解析主材失败: {e}")
        raise HTTPException(500, f"解析失败: {e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _do_parse(excel_path: str) -> list[dict]:
    """解析Excel，提取主材行

    支持两种格式：
    1. 本系统输出的Excel（主材行有特定编码标记）
    2. 广联达导出的Excel（通过列名识别）
    """
    import openpyxl

    wb = openpyxl.load_workbook(excel_path, data_only=True)
    materials = []

    for ws in wb.worksheets:
        sheet_materials = _parse_sheet(ws)
        materials.extend(sheet_materials)

    wb.close()
    return materials


def _parse_sheet(ws) -> list[dict]:
    """解析单个sheet中的主材行

    自动识别两种表格类型：
    1. 纯材料表（广联达导出的材料汇总表）→ 所有行都是主材，不用逐行判断
    2. 混合表（套完定额的清单）→ 需要逐行识别主材行
    """
    materials = []

    # 找表头行（包含"名称"或"材料名称"的行）
    header_row = None
    col_map = {}
    is_pure_material_table = False  # 是否为纯材料表
    for row_idx in range(1, min(20, ws.max_row + 1)):
        for col_idx in range(1, min(15, ws.max_column + 1)):
            val = str(ws.cell(row=row_idx, column=col_idx).value or "").strip()
            if val in ("名称", "项目名称", "材料名称"):
                header_row = row_idx
                break
        if header_row:
            break

    if not header_row:
        return materials

    # 建立列映射，同时判断是否为纯材料表
    for col_idx in range(1, min(15, ws.max_column + 1)):
        val = str(ws.cell(row=header_row, column=col_idx).value or "").strip()
        if val in ("编码", "编号", "序号", "材料编码"):
            col_map["code"] = col_idx
        elif val in ("名称", "项目名称", "材料名称"):
            col_map["name"] = col_idx
        elif val in ("规格", "规格型号"):
            col_map["spec"] = col_idx
        elif val in ("单位",):
            col_map["unit"] = col_idx
        elif val in ("数量", "工程量", "消耗量", "用量"):
            col_map["qty"] = col_idx
        elif val in ("单价", "综合单价", "不含税单价", "含税单价",
                      "市场价", "信息价", "除税单价", "除税信息价"):
            col_map["price"] = col_idx
        # 纯材料表特征：表头含"材料名称"/"材料编码"/"市场价"/"信息价"等
        if val in ("材料名称", "材料编码", "市场价", "信息价",
                    "除税单价", "除税信息价", "消耗量"):
            is_pure_material_table = True

    # sheet名包含"材料"/"主材"也视为纯材料表
    sheet_name = ws.title or ""
    if any(kw in sheet_name for kw in ("材料", "主材", "材价", "物资")):
        is_pure_material_table = True

    if "name" not in col_map:
        return materials

    # 遍历数据行
    for row_idx in range(header_row + 1, ws.max_row + 1):
        code_val = ""
        if "code" in col_map:
            code_val = str(ws.cell(row=row_idx, column=col_map["code"]).value or "").strip()

        name_val = str(ws.cell(row=row_idx, column=col_map["name"]).value or "").strip()
        if not name_val:
            continue

        spec_val = ""
        if "spec" in col_map:
            spec_val = str(ws.cell(row=row_idx, column=col_map["spec"]).value or "").strip()

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

        price_val = None
        if "price" in col_map:
            raw = ws.cell(row=row_idx, column=col_map["price"]).value
            if raw is not None:
                try:
                    price_val = float(raw)
                except (ValueError, TypeError):
                    pass

        # 判断是否为主材行：纯材料表全部当主材，混合表逐行识别
        is_material = is_pure_material_table or _is_material_row(code_val, name_val)
        if is_material:
            materials.append({
                "row": row_idx,
                "sheet": ws.title,
                "code": code_val,
                "name": name_val,
                "spec": spec_val,
                "unit": unit_val,
                "qty": qty_val,
                "existing_price": price_val,  # Excel中已有的价格
                "lookup_price": None,         # 系统查到的价格（后续填充）
                "lookup_source": None,        # 价格来源说明
            })

    return materials


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
        "city": "武汉",        // 可选
        "period_end": "2026-02-28"  // 可选，指定期次
    }
    """
    materials = body.get("materials", [])
    province = body.get("province", "")
    city = body.get("city", "")
    period_end = body.get("period_end", "")

    if not materials:
        raise HTTPException(400, "materials 不能为空")
    if not province:
        raise HTTPException(400, "province 不能为空")

    # 在线程池中批量查价
    results = await asyncio.to_thread(
        _do_lookup, materials, province, city, period_end
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
               period_end: str) -> list[dict]:
    """批量查价核心逻辑"""
    import sqlite3

    db = _get_db()
    conn = sqlite3.connect(str(db.db_path))
    conn.row_factory = sqlite3.Row

    results = []
    for mat in materials:
        name = mat.get("name", "").strip()
        spec = mat.get("spec", "").strip()
        unit = mat.get("unit", "").strip()

        if not name:
            results.append({**mat, "lookup_price": None, "lookup_source": "名称为空"})
            continue

        # 用 MaterialDB 的查价方法
        price_info = db.search_price_by_name(
            name, province=province, spec=spec, target_unit=unit
        )

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
                    price_info2 = db.search_price_by_name(
                        short_name, province=province,
                        spec=extracted_spec, target_unit=unit
                    )
                    if price_info2:
                        results.append({
                            **mat,
                            "lookup_price": price_info2["price"],
                            "lookup_source": price_info2.get("source", "价格库"),
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
