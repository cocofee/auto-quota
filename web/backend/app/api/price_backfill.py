"""
智能填价 API

把广联达组价结果回填到甲方原始清单。
两个接口：
  - POST /preview  上传两个Excel，返回映射预览（不生成文件）
  - POST /execute  上传两个Excel，执行回填，返回已填价的Excel文件
"""

import asyncio
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from loguru import logger

# tools/price_backfill.py 中的核心函数（延迟导入，轻量镜像没有tools/目录）

router = APIRouter()


def _validate_excel(file: UploadFile, label: str) -> None:
    """检查上传的文件是否为Excel格式"""
    filename = file.filename or ""
    valid_exts = (".xlsx", ".xls")
    if not any(filename.lower().endswith(ext) for ext in valid_exts):
        raise HTTPException(
            status_code=400,
            detail=f"{label}必须是 Excel 文件（.xlsx/.xls），"
                   f"当前文件: {filename}",
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


def _do_preview(orig_path: str, gld_path: str) -> dict:
    """在同步线程中执行预览逻辑（CPU密集型操作，不阻塞事件循环）"""
    import openpyxl
    from tools.price_backfill import (
        _detect_original_structure, _read_gld_prices, _build_mapping,
    )
    # 1. 读取甲方原始Excel结构
    wb_orig = openpyxl.load_workbook(orig_path)
    ws_orig = wb_orig.active
    orig_info = _detect_original_structure(ws_orig)
    wb_orig.close()

    # 检查是否有价格列
    has_price_col = bool(
        orig_info["col_map"].get("unit_price") or
        orig_info["col_map"].get("total_price")
    )
    if not has_price_col:
        raise HTTPException(
            status_code=400,
            detail="甲方原始清单中未找到单价/合价列，无法回填价格。"
                   "请确认清单中有'综合单价''合价'等列名。",
        )

    # 2. 读取广联达导出Excel的价格
    wb_gld = openpyxl.load_workbook(gld_path)
    ws_gld = wb_gld.active
    price_data = _read_gld_prices(ws_gld)
    wb_gld.close()

    if not price_data:
        raise HTTPException(
            status_code=400,
            detail="广联达导出文件中未找到有效价格数据。"
                   "请确认文件格式正确（需要有序号、名称、单价/合价列）。",
        )

    # 3. 建立映射
    mapping = _build_mapping(orig_info["items"], price_data)

    # 4. 统计
    matched_by_index = sum(1 for m in mapping if m["match_method"] == "index")
    matched_by_name = sum(
        1 for m in mapping if m["match_method"].startswith("name")
    )
    unmatched = sum(1 for m in mapping if m["match_method"] == "未匹配")

    return {
        "original_count": len(orig_info["items"]),
        "gld_count": len(price_data),
        "mapping": mapping,
        "col_map": orig_info["col_map"],
        "stats": {
            "total": len(mapping),
            "matched_by_index": matched_by_index,
            "matched_by_name": matched_by_name,
            "unmatched": unmatched,
        },
    }


def _do_execute(orig_path: str, gld_path: str) -> str:
    """在同步线程中执行回填，返回结果文件路径"""
    from tools.price_backfill import _write_prices
    # 复用预览逻辑获取映射
    preview = _do_preview(orig_path, gld_path)
    mapping = preview["mapping"]
    col_map = preview["col_map"]

    # 生成回填文件到临时目录
    out_dir = Path(tempfile.gettempdir()) / "price_backfill"
    out_dir.mkdir(exist_ok=True)
    orig_name = Path(orig_path).stem
    out_path = out_dir / f"{orig_name}_已回填.xlsx"

    result_path, written = _write_prices(
        orig_path, mapping, col_map, output_path=str(out_path)
    )
    logger.info(f"智能填价完成: 回填 {written}/{len(mapping)} 条价格 → {result_path}")
    return result_path


# ============================================================
# API 接口
# ============================================================

@router.post("/price-backfill/preview")
async def preview_backfill(
    original_file: UploadFile = File(description="甲方原始Excel（单价列为空）"),
    gld_file: UploadFile = File(description="广联达导出Excel（带价格）"),
):
    """预览映射结果（不生成文件）

    上传甲方原始清单和广联达导出文件，返回每行的匹配情况。
    用户确认映射无误后再调 execute 接口执行回填。
    """
    _validate_excel(original_file, "甲方原始清单")
    _validate_excel(gld_file, "广联达导出文件")

    orig_tmp = await _save_upload(original_file, "orig_")
    gld_tmp = await _save_upload(gld_file, "gld_")

    try:
        result = await asyncio.to_thread(_do_preview, orig_tmp, gld_tmp)
        # 不需要返回 col_map 给前端（内部用）
        result.pop("col_map", None)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"智能填价预览失败: {e}")
        raise HTTPException(status_code=500, detail=f"预览失败: {e}")
    finally:
        # 清理临时文件
        for p in [orig_tmp, gld_tmp]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


@router.post("/price-backfill/execute")
async def execute_backfill(
    original_file: UploadFile = File(description="甲方原始Excel（单价列为空）"),
    gld_file: UploadFile = File(description="广联达导出Excel（带价格）"),
):
    """执行价格回填，返回已填价的Excel文件下载

    上传两个文件后直接执行回填，返回可下载的Excel文件。
    """
    _validate_excel(original_file, "甲方原始清单")
    _validate_excel(gld_file, "广联达导出文件")

    orig_tmp = await _save_upload(original_file, "orig_")
    gld_tmp = await _save_upload(gld_file, "gld_")

    try:
        result_path = await asyncio.to_thread(_do_execute, orig_tmp, gld_tmp)

        # 用原始文件名构造下载文件名
        orig_name = Path(original_file.filename or "清单").stem
        download_name = f"{orig_name}_已回填.xlsx"

        return FileResponse(
            path=result_path,
            filename=download_name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"智能填价执行失败: {e}")
        raise HTTPException(status_code=500, detail=f"回填失败: {e}")
    finally:
        # 清理上传的临时文件（回填结果文件由 FileResponse 发送后由系统清理）
        for p in [orig_tmp, gld_tmp]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
