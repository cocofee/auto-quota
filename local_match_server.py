"""
本地匹配API服务 — 在你的电脑上运行，提供定额匹配算力

懒猫盒子通过HTTP调用这个服务来执行匹配任务，
这样懒猫只需要轻量镜像（~200MB），算力全在你的电脑上。

启动方式：
    python local_match_server.py
    或者双击「启动匹配服务.bat」

端口：9527（固定）
"""

import json
import os
import shutil
import time
import uuid
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from src.excel_compat import ensure_openpyxl_input, validate_excel_upload

# 加载项目 .env
load_dotenv()

import config  # 项目全局配置（省份列表等）
import main as auto_quota_main  # 匹配入口

# ============================================================
# 配置
# ============================================================

# API密钥（从环境变量读取，未设置则自动生成一个随机密钥）
_DEFAULT_KEY = uuid.uuid4().hex[:16]
API_KEY = os.getenv("LOCAL_MATCH_API_KEY", _DEFAULT_KEY)

# 最大并发匹配任务数
MAX_CONCURRENT = int(os.getenv("LOCAL_MATCH_MAX_CONCURRENT", "5"))

# 临时文件目录
TEMP_DIR = Path("output/temp/remote_match")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# 任务保留时间（秒），超时后自动清理
TASK_TTL = 3600  # 1小时

# 服务端口
PORT = int(os.getenv("LOCAL_MATCH_PORT", "9527"))

# ============================================================
# 全局状态
# ============================================================

# 任务字典：match_id → 任务状态
# 状态结构：{
#   "status": "running" / "completed" / "failed",
#   "progress": 0~100,
#   "current_idx": 当前第几条,
#   "message": 进度文字,
#   "results": 匹配结果（完成后填入）,
#   "error": 错误信息（失败时填入）,
#   "created_at": 创建时间戳,
#   "work_dir": 工作目录路径,
# }
_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()

# 并发控制信号量
_semaphore = threading.Semaphore(MAX_CONCURRENT)

# ============================================================
# FastAPI 应用
# ============================================================

app = FastAPI(
    title="本地匹配API服务",
    description="提供定额匹配算力，供懒猫盒子远程调用",
    version="1.0.0",
)


def _verify_api_key(api_key: str):
    """验证API密钥"""
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API Key不正确")


@app.get("/health")
def health_check(x_api_key: str = Header(default="")):
    """健康检查 — 返回版本号和可用省份列表"""
    _verify_api_key(x_api_key)

    # 获取可用省份列表
    provinces = config.list_db_provinces()
    groups = config.get_province_groups()
    subgroups = config.get_province_subgroups()

    # 统计当前活跃任务数
    with _tasks_lock:
        active = sum(1 for t in _tasks.values() if t["status"] == "running")

    return {
        "status": "ok",
        "version": "1.0.0",
        "provinces": provinces,
        "groups": groups,
        "subgroups": subgroups,
        "active_tasks": active,
        "max_concurrent": MAX_CONCURRENT,
    }


@app.post("/match")
async def create_match(
    file: UploadFile,
    province: str = Form(...),
    mode: str = Form(default="search"),
    sheet: str = Form(default=None),
    limit: int = Form(default=None),
    no_experience: bool = Form(default=False),
    agent_llm: str = Form(default=None),
    x_api_key: str = Header(default=""),
):
    """提交匹配任务 — 上传Excel+参数，异步执行，返回match_id"""
    _verify_api_key(x_api_key)

    # 检查并发限制（非阻塞尝试获取信号量）
    if not _semaphore.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail=f"服务繁忙，当前已有{MAX_CONCURRENT}个任务在执行，请稍后重试"
        )

    try:
        # 生成任务ID和工作目录
        match_id = str(uuid.uuid4())
        work_dir = TEMP_DIR / match_id
        work_dir.mkdir(parents=True, exist_ok=True)

        content = await file.read()
        filename = file.filename or "input.xlsx"
        try:
            info = validate_excel_upload(filename, content[:8])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if info.is_mislabeled:
            input_name = f"input{info.normalized_suffix}"
        else:
            input_name = f"input{Path(filename).suffix.lower()}"

        # 保存上传的Excel文件
        input_path = work_dir / input_name
        input_path.write_bytes(content)

        # 初始化任务状态
        with _tasks_lock:
            _tasks[match_id] = {
                "status": "running",
                "progress": 0,
                "current_idx": 0,
                "message": "任务已创建，等待执行...",
                "results": None,
                "error": None,
                "created_at": time.time(),
                "work_dir": str(work_dir),
            }

        # 组装匹配参数
        params = {
            "input_file": str(input_path),
            "province": province,
            "mode": mode,
            "sheet": sheet,
            "limit": limit,
            "no_experience": no_experience,
            "agent_llm": agent_llm,
        }

        # 启动后台线程执行匹配
        thread = threading.Thread(
            target=_run_match,
            args=(match_id, params),
            daemon=True,
        )
        thread.start()

        return {"match_id": match_id}

    except Exception:
        # 出错时释放信号量
        _semaphore.release()
        raise


@app.get("/match/{match_id}/progress")
def get_progress(match_id: str, x_api_key: str = Header(default="")):
    """查询匹配进度"""
    _verify_api_key(x_api_key)

    with _tasks_lock:
        task = _tasks.get(match_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    return {
        "status": task["status"],
        "progress": task["progress"],
        "current_idx": task["current_idx"],
        "message": task["message"],
        "error": task.get("error"),
    }


@app.get("/match/{match_id}/results")
def get_results(match_id: str, x_api_key: str = Header(default="")):
    """获取匹配结果 — 任务完成后才能调用"""
    _verify_api_key(x_api_key)

    with _tasks_lock:
        task = _tasks.get(match_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task["status"] == "running":
        raise HTTPException(status_code=409, detail="任务还在执行中")

    if task["status"] == "failed":
        raise HTTPException(status_code=500, detail=task.get("error", "匹配失败"))

    # 返回结果JSON
    return task["results"]


@app.get("/match/{match_id}/output.xlsx")
def download_excel(match_id: str, x_api_key: str = Header(default="")):
    """下载输出的Excel文件"""
    _verify_api_key(x_api_key)

    with _tasks_lock:
        task = _tasks.get(match_id)

    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    excel_path = Path(task["work_dir"]) / "output.xlsx"
    if not excel_path.exists():
        raise HTTPException(status_code=404, detail="Excel文件不存在")

    return FileResponse(
        path=str(excel_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="output.xlsx",
    )


# ============================================================
# 编清单接口（本地有清单库数据，直接执行）
# ============================================================

@app.post("/compile-bill/preview")
async def compile_bill_preview(
    file: UploadFile,
    bill_version: str = Form(default="2024"),
    x_api_key: str = Header(default=""),
):
    """编清单预览 — 上传Excel，自动匹配12位清单编码，返回结果"""
    _verify_api_key(x_api_key)

    content = await file.read()
    filename = file.filename or "input.xlsx"

    # 保存临时文件
    work_dir = TEMP_DIR / f"compile_{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_path = work_dir / filename
    input_path.write_bytes(content)

    try:
        from src.bill_reader import BillReader
        from src.bill_compiler import compile_items

        reader = BillReader()
        items = reader.read_file(str(input_path))

        if not items:
            raise HTTPException(status_code=400, detail="未从Excel中读取到清单项，请检查文件格式。")

        compiled = compile_items(items)

        # 构建返回数据
        result_items = []
        matched_count = 0
        for i, item in enumerate(compiled):
            original_code = item.get("code", "").strip()
            bill_match = item.get("bill_match")

            if bill_match and bill_match.get("code_12"):
                code = bill_match["code_12"]
                code_source = "matched"
                matched_count += 1
            elif original_code and len(original_code) >= 9:
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

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/compile-bill/execute")
async def compile_bill_execute(
    file: UploadFile,
    bill_version: str = Form(default="2024"),
    x_api_key: str = Header(default=""),
):
    """编清单导出 — 上传Excel，返回编好的工程量清单Excel文件"""
    _verify_api_key(x_api_key)

    content = await file.read()
    filename = file.filename or "input.xlsx"

    work_dir = TEMP_DIR / f"compile_{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_path = work_dir / filename
    input_path.write_bytes(content)

    try:
        import openpyxl
        from src.bill_reader import BillReader
        from src.bill_compiler import compile_items

        reader = BillReader()
        items = reader.read_file(str(input_path))
        if not items:
            raise HTTPException(status_code=400, detail="未从Excel中读取到清单项，请检查文件格式。")

        compiled = compile_items(items)

        # 生成结果Excel
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "工程量清单"

        headers = ["序号", "项目编码", "项目名称", "项目特征", "计量单位", "工程量", "编码来源"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = openpyxl.styles.Font(bold=True)

        for i, item in enumerate(compiled):
            original_code = item.get("code", "").strip()
            bill_match = item.get("bill_match")

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

        col_widths = [8, 18, 30, 50, 10, 12, 12]
        for col, width in enumerate(col_widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

        out_path = work_dir / "output.xlsx"
        wb.save(str(out_path))
        wb.close()

        return FileResponse(
            path=str(out_path),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"{Path(filename).stem}_工程量清单.xlsx",
        )

    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"编清单失败: {e}")


# ============================================================
# 定额搜索接口（供懒猫远程模式转发）
# ============================================================

@app.get("/quota-search")
def search_quotas(
    keyword: str,
    province: str,
    book: str = None,
    chapter: str = None,
    limit: int = 20,
    x_api_key: str = Header(default=""),
):
    """按关键词搜索定额"""
    _verify_api_key(x_api_key)
    from src.quota_db import QuotaDB

    try:
        db = QuotaDB(province)
        results = db.search_by_keywords(keyword, chapter=chapter, book=book, limit=limit)
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
        return {"items": items, "total": len(items), "keyword": keyword, "province": province}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"省份 '{province}' 的定额库不存在")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"搜索失败: {e}")


@app.get("/quota-search/by-id")
def get_quota_by_id(
    quota_id: str,
    province: str,
    x_api_key: str = Header(default=""),
):
    """按定额编号精确查询"""
    _verify_api_key(x_api_key)
    from src.quota_db import QuotaDB

    try:
        db = QuotaDB(province)
        results = db.get_quota_by_id(quota_id)
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
        raise HTTPException(status_code=500, detail=f"查询失败: {e}")


@app.get("/quota-search/provinces")
def list_search_provinces(x_api_key: str = Header(default="")):
    """获取可用的省份定额库列表"""
    _verify_api_key(x_api_key)
    provinces = config.list_db_provinces()
    return {"items": provinces}


@app.get("/quota-search/smart")
def smart_search(
    name: str,
    province: str,
    description: str = "",
    specialty: str = "",
    limit: int = 10,
    x_api_key: str = Header(default=""),
):
    """智能搜索定额（清单原文 → 自动清洗+同义词+级联搜索）

    和 /quota-search 的区别：
    - /quota-search 需要调用方自己把"JDG20"转成"紧定式钢导管"
    - /quota-search/smart 直接传清单原文，系统自动做术语转换和级联搜索

    参数:
        name: 清单项目名称（如"JDG20暗配"、"PPR给水管DN25"）
        province: 省份定额库名称（如"北京2024"）
        description: 清单特征描述（可选，如"沟槽连接 镀锌钢管"）
        specialty: 专业册号（可选，如"C10"，不传则自动识别）
        limit: 最大返回条数

    返回:
        items: 候选定额列表（按匹配度排序）
        search_query: 系统构建的搜索词（方便调试）
    """
    _verify_api_key(x_api_key)

    try:
        from src.text_parser import TextParser
        from src.hybrid_searcher import HybridSearcher
        from src.specialty_classifier import classify as classify_specialty

        parser = TextParser()

        # 第1步：自动识别专业（如果没传）
        if not specialty:
            spec_result = classify_specialty(name, description)
            specialty = spec_result.get("primary", "") if isinstance(spec_result, dict) else ""

        # 第2步：构建搜索query（清洗+同义词+规范化）
        search_query = parser.build_quota_query(
            name, description, specialty=specialty
        )

        # 第3步：级联搜索（BM25 + 向量，主专业 → 全库）
        searcher = HybridSearcher(province)
        books = [specialty] if specialty else None
        candidates = searcher.search(search_query, top_k=limit, books=books)

        # 如果主专业搜不到足够结果，扩到全库
        if len(candidates) < 3 and books:
            candidates_all = searcher.search(search_query, top_k=limit, books=None)
            # 合并去重
            seen = {c.get("quota_id") for c in candidates}
            for c in candidates_all:
                if c.get("quota_id") not in seen:
                    candidates.append(c)
                    seen.add(c.get("quota_id"))
            candidates = candidates[:limit]

        # 构建返回结果
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

        return {
            "items": items,
            "total": len(items),
            "search_query": search_query,
            "specialty": specialty,
            "province": province,
        }

    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"省份 '{province}' 的定额库不存在")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"智能搜索失败: {e}")


# ============================================================
# 后台匹配执行
# ============================================================

def _run_match(match_id: str, params: dict):
    """在后台线程中执行匹配（调用 main.run()）"""
    try:
        work_dir = Path(_tasks[match_id]["work_dir"])

        # 进度回调：更新内存字典
        def progress_cb(percent, current_idx, message, result=None):
            with _tasks_lock:
                if match_id in _tasks:
                    _tasks[match_id].update(
                        progress=percent,
                        current_idx=current_idx,
                        message=message,
                    )

        # 输出路径
        excel_output = str(work_dir / "output.xlsx")
        json_output = str(work_dir / "results.json")
        processing_input, normalize_result = ensure_openpyxl_input(
            params["input_file"],
            work_dir / "input_importable.xlsx",
        )
        if normalize_result:
            _tasks[match_id]["message"] = "已自动转换为可导入的 .xlsx，开始匹配..."

        # 调用核心匹配函数
        result = auto_quota_main.run(
            input_file=str(processing_input),
            mode=params["mode"],
            output=excel_output,
            province=params["province"],
            sheet=params.get("sheet"),
            limit=params.get("limit"),
            no_experience=params.get("no_experience", False),
            agent_llm=params.get("agent_llm"),
            json_output=json_output,
            interactive=False,  # API调用不能交互
            progress_callback=progress_cb,
            original_file=params["input_file"],
        )

        # 标记完成
        with _tasks_lock:
            if match_id in _tasks:
                _tasks[match_id].update(
                    status="completed",
                    progress=100,
                    message="匹配完成",
                    results=result,
                )

    except Exception as e:
        # 标记失败
        with _tasks_lock:
            if match_id in _tasks:
                _tasks[match_id].update(
                    status="failed",
                    error=str(e),
                    message=f"匹配失败: {e}",
                )
    finally:
        # 释放并发信号量
        _semaphore.release()


# ============================================================
# 定时清理过期任务
# ============================================================

def _cleanup_expired_tasks():
    """每10分钟清理一次过期任务（完成超过1小时的）"""
    while True:
        time.sleep(600)  # 10分钟检查一次
        now = time.time()
        expired = []

        with _tasks_lock:
            for mid, task in list(_tasks.items()):
                if task["status"] in ("completed", "failed"):
                    if now - task["created_at"] > TASK_TTL:
                        expired.append(mid)
            for mid in expired:
                del _tasks[mid]

        # 清理临时文件
        for mid in expired:
            work_dir = TEMP_DIR / mid
            if work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

        if expired:
            print(f"[清理] 已清理 {len(expired)} 个过期任务")


# ============================================================
# 启动入口
# ============================================================

def main():
    """启动本地匹配API服务"""
    # 启动清理线程
    cleaner = threading.Thread(target=_cleanup_expired_tasks, daemon=True)
    cleaner.start()

    # 打印启动信息
    print("=" * 60)
    print("  本地匹配API服务")
    print("=" * 60)
    print(f"  端口: {PORT}")
    print(f"  API Key: {API_KEY}")
    print(f"  最大并发: {MAX_CONCURRENT}")
    print(f"  临时目录: {TEMP_DIR}")
    print()
    print("  把以下配置填入懒猫盒子的环境变量：")
    print(f"    MATCH_BACKEND=remote")
    print(f"    LOCAL_MATCH_URL=http://你的电脑IP:{PORT}")
    print(f"    LOCAL_MATCH_API_KEY={API_KEY}")
    print()
    print("  按 Ctrl+C 停止服务")
    print("=" * 60)

    # 启动服务
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
