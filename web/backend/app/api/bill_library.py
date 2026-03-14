"""
编清单 API

上传工程量Excel（算量导出/手工表格等）→ 自动匹配12位清单编码 → 下载标准工程量清单。

两个接口：
  - POST /bill-compiler/preview   上传Excel + 选清单版本，返回编码匹配预览
  - POST /bill-compiler/execute   上传Excel + 选清单版本，直接返回编好的Excel文件
"""

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException, Form
from fastapi.responses import FileResponse
from loguru import logger

router = APIRouter()

# 项目根目录（用于导入src模块）
PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent


def _validate_excel(file: UploadFile, label: str) -> None:
    """检查上传的文件是否为Excel格式"""
    filename = file.filename or ""
    valid_exts = (".xlsx", ".xls")
    if not any(filename.lower().endswith(ext) for ext in valid_exts):
        raise HTTPException(
            status_code=400,
            detail=f"{label}必须是 Excel 文件（.xlsx/.xls），当前文件: {filename}",
        )


async def _save_upload(file: UploadFile, prefix: str) -> str:
    """把上传文件保存到临时目录，返回临时文件路径"""
    suffix = Path(file.filename or "upload.xlsx").suffix
    content = await file.read()
    with tempfile.NamedTemporaryFile(
        mode="wb", suffix=suffix, delete=False, prefix=prefix,
    ) as tmp:
        tmp.write(content)
        return tmp.name


def _do_compile(file_path: str, bill_version: str) -> dict:
    """在同步线程中执行编清单逻辑

    参数:
        file_path: 上传的Excel临时文件路径
        bill_version: 清单版本 "2024" 或 "2013"

    返回:
        {
            "total": 总条数,
            "matched": 匹配成功条数,
            "unmatched": 未匹配条数,
            "items": [
                {
                    "index": 序号,
                    "name": 原始名称,
                    "description": 特征描述,
                    "unit": 单位,
                    "quantity": 工程量,
                    "bill_code": 匹配到的清单编码（9位或12位），
                    "bill_code_source": 编码来源（original/matched/unmatched）,
                    "sheet_name": 所在Sheet页,
                },
                ...
            ]
        }
    """
    from src.bill_reader import BillReader
    from src.bill_compiler import compile_items

    # 1. 读取Excel
    reader = BillReader()
    items = reader.read_file(file_path)

    if not items:
        raise HTTPException(status_code=400, detail="未从Excel中读取到清单项，请检查文件格式。")

    # 2. 编译（包含自动匹配编码）
    # TODO: 后续支持 bill_version 参数控制使用哪个版本的清单库
    compiled = compile_items(items)

    # 3. 构建返回数据
    # bill_match 字段结构: {"code": 9位编码, "code_12": 12位编码, "name": 标准名称, ...}
    result_items = []
    matched_count = 0
    for i, item in enumerate(compiled):
        original_code = item.get("code", "").strip()
        bill_match = item.get("bill_match")  # 自动匹配结果

        # 优先用12位编码
        if bill_match and bill_match.get("code_12"):
            # 自动匹配成功 → 用12位编码
            code = bill_match["code_12"]
            code_source = "matched"
            matched_count += 1
        elif original_code and len(original_code) >= 9:
            # Excel里本来就有编码
            code = original_code
            code_source = "original"
            matched_count += 1
        else:
            code = original_code
            code_source = "unmatched"

        result_items.append({
            "index": i + 1,
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "unit": item.get("unit", ""),
            "quantity": item.get("quantity", None),
            "bill_code": code,
            "bill_code_source": code_source,
            "matched_name": bill_match.get("name", "") if bill_match else "",
            "sheet_name": item.get("sheet_name", ""),
            "section": item.get("section", ""),
        })

    return {
        "total": len(result_items),
        "matched": matched_count,
        "unmatched": len(result_items) - matched_count,
        "bill_version": bill_version,
        "items": result_items,
    }


def _do_export(file_path: str, bill_version: str) -> str:
    """执行编清单并生成结果Excel文件

    返回结果文件路径。
    """
    import openpyxl
    from src.bill_reader import BillReader
    from src.bill_compiler import compile_items

    # 1. 读取+编译
    reader = BillReader()
    items = reader.read_file(file_path)
    if not items:
        raise HTTPException(status_code=400, detail="未从Excel中读取到清单项，请检查文件格式。")

    compiled = compile_items(items)

    # 2. 生成结果Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "工程量清单"

    # 表头
    headers = ["序号", "项目编码", "项目名称", "项目特征", "计量单位", "工程量", "编码来源"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = openpyxl.styles.Font(bold=True)

    # 数据行
    for i, item in enumerate(compiled):
        original_code = item.get("code", "").strip()
        bill_match = item.get("bill_match")

        # 优先用12位编码
        if bill_match and bill_match.get("code_12"):
            code = bill_match["code_12"]
            source = "自动匹配"
        elif original_code and len(original_code) >= 9:
            code = original_code
            source = "原始编码"
        else:
            code = original_code
            source = "未匹配"

        row = i + 2
        ws.cell(row=row, column=1, value=i + 1)
        ws.cell(row=row, column=2, value=code)
        ws.cell(row=row, column=3, value=item.get("name", ""))
        ws.cell(row=row, column=4, value=item.get("description", ""))
        ws.cell(row=row, column=5, value=item.get("unit", ""))
        ws.cell(row=row, column=6, value=item.get("quantity", ""))
        ws.cell(row=row, column=7, value=source)

    # 设置列宽
    col_widths = [8, 18, 30, 50, 10, 12, 12]
    for col, width in enumerate(col_widths, 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

    # 保存到临时文件
    out_dir = Path(tempfile.gettempdir()) / "bill_compiler"
    out_dir.mkdir(exist_ok=True)
    orig_name = Path(file_path).stem
    out_path = out_dir / f"{orig_name}_工程量清单.xlsx"
    wb.save(str(out_path))
    wb.close()

    logger.info(f"编清单完成: {len(compiled)}条 → {out_path}")
    return str(out_path)


# ============================================================
# API 接口
# ============================================================

@router.post("/bill-compiler/preview")
async def preview_compile(
    file: UploadFile = File(description="工程量Excel文件（算量导出/手工表格）"),
    bill_version: str = Form(default="2024", description="清单版本: 2024 或 2013"),
):
    """预览编清单结果

    上传Excel文件，返回每条清单项的编码匹配情况。
    用户确认后再调 execute 接口导出结果Excel。
    """
    _validate_excel(file, "工程量文件")

    if bill_version not in ("2024", "2013"):
        raise HTTPException(status_code=400, detail=f"不支持的清单版本: {bill_version}，请选择 2024 或 2013")

    tmp_path = await _save_upload(file, "bill_")

    try:
        result = await asyncio.to_thread(_do_compile, tmp_path, bill_version)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"编清单预览失败: {e}")
        raise HTTPException(status_code=500, detail=f"编清单失败: {e}")
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


@router.post("/bill-compiler/execute")
async def execute_compile(
    file: UploadFile = File(description="工程量Excel文件（算量导出/手工表格）"),
    bill_version: str = Form(default="2024", description="清单版本: 2024 或 2013"),
):
    """执行编清单，返回结果Excel文件下载

    上传文件后直接执行编清单，返回可下载的标准工程量清单Excel。
    """
    _validate_excel(file, "工程量文件")

    if bill_version not in ("2024", "2013"):
        raise HTTPException(status_code=400, detail=f"不支持的清单版本: {bill_version}，请选择 2024 或 2013")

    tmp_path = await _save_upload(file, "bill_")

    try:
        result_path = await asyncio.to_thread(_do_export, tmp_path, bill_version)

        # 用原始文件名构造下载文件名
        orig_name = Path(file.filename or "工程量").stem
        download_name = f"{orig_name}_工程量清单.xlsx"

        return FileResponse(
            path=result_path,
            filename=download_name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"编清单导出失败: {e}")
        raise HTTPException(status_code=500, detail=f"编清单失败: {e}")
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
