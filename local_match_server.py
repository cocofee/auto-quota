"""
本地匹配API服务 — 在你的电脑上运行，提供定额匹配算力

懒猫盒子通过HTTP调用这个服务来执行匹配任务，
这样懒猫只需要轻量镜像（~200MB），算力全在你的电脑上。

启动方式：
    python local_match_server.py
    或者双击「启动匹配服务.bat」

端口：9100（默认，可通过 LOCAL_MATCH_PORT 环境变量修改）
"""

import json
import os
import re
import shutil
import time
import uuid
import threading
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from loguru import logger
from src.excel_compat import ensure_openpyxl_input, validate_excel_upload
from src.output_writer import safe_excel_text

# 加载项目 .env
load_dotenv()

import config  # 项目全局配置（省份列表等）
import main as auto_quota_main  # 匹配入口

# ============================================================
# 配置
# ============================================================

def _require_api_key() -> str:
    api_key = os.getenv("LOCAL_MATCH_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "LOCAL_MATCH_API_KEY 未配置，出于安全原因拒绝启动。\n"
            "请先设置一个随机高强度密钥，再启动本地匹配服务。"
        )
    return api_key


def _mask_secret(secret: str) -> str:
    if len(secret) <= 8:
        return "*" * len(secret)
    return f"{secret[:4]}...{secret[-4:]}"


# API密钥（必须显式配置，和懒猫环境变量 LOCAL_MATCH_API_KEY 保持一致）
API_KEY = _require_api_key()

# 最大并发匹配任务数
MAX_CONCURRENT = int(os.getenv("LOCAL_MATCH_MAX_CONCURRENT", "5"))

# 单文件上传上限（MB）
MAX_UPLOAD_MB = int(os.getenv("LOCAL_MATCH_MAX_UPLOAD_MB", "30"))

# 临时文件目录
TEMP_DIR = Path("output/temp/remote_match")
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# 任务保留时间（秒），超时后自动清理
TASK_TTL = 3600  # 1小时

# 服务端口
PORT = int(os.getenv("LOCAL_MATCH_PORT", "9100"))

# 默认监听所有网卡，便于懒猫盒子/局域网访问；如需仅本机访问可显式设为 127.0.0.1
HOST = os.getenv("LOCAL_MATCH_HOST", "0.0.0.0").strip() or "0.0.0.0"

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


def _verify_api_key(api_key: str):
    """验证API密钥"""
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="API Key不正确")


def _sanitize_client_filename(filename: str | None, default: str = "input.xlsx") -> str:
    raw = (filename or "").replace("\\", "/").split("/")[-1]
    raw = raw.replace("\x00", "").replace("\r", "").replace("\n", "").strip().strip(". ")
    return raw or default


def _safe_join_under(base_dir: Path, filename: str) -> Path:
    candidate = (base_dir / filename).resolve()
    base_resolved = base_dir.resolve()
    if candidate.parent != base_resolved:
        raise HTTPException(status_code=400, detail="非法文件名")
    return candidate


async def _read_and_validate_upload(file: UploadFile, label: str) -> tuple[bytes, str, str]:
    filename = _sanitize_client_filename(file.filename)
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024
    content = bytearray()
    while chunk := await file.read(1024 * 1024):
        content.extend(chunk)
        if len(content) > max_bytes:
            raise HTTPException(status_code=400, detail=f"{label}大小超过 {MAX_UPLOAD_MB}MB 限制")
    try:
        info = validate_excel_upload(filename, bytes(content[:8]))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return bytes(content), filename, info.normalized_suffix


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

        content, filename, normalized_suffix = await _read_and_validate_upload(file, "Excel文件")
        input_name = f"input{normalized_suffix}"

        # 保存上传的Excel文件
        input_path = _safe_join_under(work_dir, input_name)
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

    content, filename, normalized_suffix = await _read_and_validate_upload(file, "工程量文件")

    # 保存临时文件
    work_dir = TEMP_DIR / f"compile_{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_path = _safe_join_under(work_dir, f"input{normalized_suffix}")
    input_path.write_bytes(content)

    try:
        from src.bill_reader import BillReader
        from src.bill_compiler import compile_items

        reader = BillReader()
        items = reader.read_file(str(input_path))

        if not items:
            raise HTTPException(status_code=400, detail="未从Excel中读取到清单项，请检查文件格式。")

        compiled = compile_items(items, bill_version=bill_version)

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
                "bill_code": safe_excel_text(code),
                "bill_code_source": code_source,
                "matched_name": safe_excel_text(bill_match.get("name", "") if bill_match else ""),
                "standard_name": safe_excel_text(item.get("standard_name", "")),
                "standard_unit": safe_excel_text(item.get("standard_unit", "")),
                "compiled_features": safe_excel_text(item.get("compiled_features", "")),
                "sheet_name": safe_excel_text(item.get("sheet_name", "")),
                "section": safe_excel_text(item.get("section", "")),
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

    content, filename, normalized_suffix = await _read_and_validate_upload(file, "工程量文件")

    work_dir = TEMP_DIR / f"compile_{uuid.uuid4().hex[:8]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    input_path = _safe_join_under(work_dir, f"input{normalized_suffix}")
    input_path.write_bytes(content)

    try:
        import openpyxl
        from src.bill_reader import BillReader
        from src.bill_compiler import compile_items

        reader = BillReader()
        items = reader.read_file(str(input_path))
        if not items:
            raise HTTPException(status_code=400, detail="未从Excel中读取到清单项，请检查文件格式。")

        compiled = compile_items(items, bill_version=bill_version)

        # 生成结果Excel
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "工程量清单"

        headers = ["序号", "项目编码", "项目名称", "项目特征描述", "计量单位", "工程量", "编码来源", "原始名称"]
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

            # 优先用标准名称和标准单位，没有则降级用原始值
            display_name = item.get("standard_name") or item.get("name", "")
            display_features = item.get("compiled_features") or item.get("description", "")
            display_unit = item.get("standard_unit") or item.get("unit", "")
            original_name = item.get("name", "")

            row = i + 2
            ws.cell(row=row, column=1, value=i + 1)
            ws.cell(row=row, column=2, value=safe_excel_text(code))
            ws.cell(row=row, column=3, value=safe_excel_text(display_name))
            ws.cell(row=row, column=4, value=safe_excel_text(display_features))
            ws.cell(row=row, column=5, value=safe_excel_text(display_unit))
            ws.cell(row=row, column=6, value=item.get("quantity", ""))
            ws.cell(row=row, column=7, value=safe_excel_text(source))
            ws.cell(row=row, column=8, value=safe_excel_text(original_name))

        col_widths = [8, 18, 20, 50, 10, 12, 12, 25]
        for col, width in enumerate(col_widths, 1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(col)].width = width

        out_path = work_dir / "output.xlsx"
        wb.save(str(out_path))
        wb.close()

        return FileResponse(
            path=str(out_path),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename=f"{Path(_sanitize_client_filename(filename)).stem}_工程量清单.xlsx",
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


def _find_sibling_libs(province: str) -> list[str]:
    """找同省的其他定额库（用于跨库搜索）

    例如传入"河南省通用安装工程预算定额(2016)"，
    返回["河南省房屋建筑与装饰工程预算定额(2016)", "河南省市政工程预算定额(2016)"]

    匹配逻辑：提取省份前缀（如"河南省"），找所有同前缀的库，排除自己。
    """
    import re
    # 提取省份前缀：匹配"XX省"或"XX市"或"XX自治区"
    m = re.match(r'^(.{2,6}(?:省|市|自治区))', province)
    if not m:
        return []
    prefix = m.group(1)

    all_libs = config.list_db_provinces()
    siblings = [
        lib for lib in all_libs
        if lib.startswith(prefix) and lib != province
    ]
    return siblings


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

        # 第4步：同省跨库搜索
        # 安装项目里经常有土建相关清单（拆除/开槽/封堵/支墩/防水等），
        # 这些定额在建筑装饰库里，不在安装库里。
        # 触发条件：主库结果不够 OR 清单含跨库关键词（即使主库有结果也要搜）
        _CROSS_LIB_KEYWORDS = (
            "拆除", "拆卸",           # 拆除定额基本都在建筑装饰库
            "抹灰", "粉刷",           # 装饰工程
            "砌筑", "砌墙", "拆墙",   # 土建工程
            "支墩", "基础",           # 混凝土基础
        )
        need_cross = len(candidates) < 3  # 结果太少，必须跨库
        if not need_cross:
            # 结果够了，但如果清单含跨库关键词，也要搜（防止语义偏离）
            combined_text = name + " " + description
            need_cross = any(kw in combined_text for kw in _CROSS_LIB_KEYWORDS)

        if need_cross:
            sibling_libs = _find_sibling_libs(province)
            for sib_lib in sibling_libs:
                try:
                    sib_searcher = HybridSearcher(sib_lib)
                    sib_results = sib_searcher.search(search_query, top_k=limit, books=None)
                    seen = {c.get("quota_id") for c in candidates}
                    for c in sib_results:
                        if c.get("quota_id") not in seen:
                            # 标记来源库，方便调试
                            c["cross_lib"] = sib_lib
                            candidates.append(c)
                            seen.add(c.get("quota_id"))
                except Exception as e:
                    logger.warning(f"跨库搜索 {sib_lib} 失败（跳过）: {e}")
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
                "cross_lib": c.get("cross_lib", ""),  # 跨库来源（空=主库）
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
# 经验库写入（远程模式下懒猫转发到这里）
# ============================================================

from pydantic import BaseModel as _BaseModel
from typing import Optional as _Optional


class _StoreExperienceRequest(_BaseModel):
    """经验库写入请求"""
    name: str
    desc: str = ""
    quota_ids: list[str]
    quota_names: list[str] = []
    reason: str = ""
    specialty: str = ""
    province: str = ""
    confirmed: bool = False
    feedback_payload: dict | None = None


@app.post("/experience/store")
def store_experience_api(
    req: _StoreExperienceRequest,
    x_api_key: str = Header(default=""),
):
    """单条经验库写入"""
    _verify_api_key(x_api_key)
    try:
        from tools.jarvis_store import store_one
        result = store_one(
            name=req.name,
            desc=req.desc,
            quota_ids=req.quota_ids,
            quota_names=req.quota_names,
            reason=req.reason,
            specialty=req.specialty,
            province=req.province or None,
            confirmed=req.confirmed,
            feedback_payload=req.feedback_payload,
        )
        return {"success": bool(result), "record_id": result if isinstance(result, int) else 0}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"经验库写入失败: {e}")


class _StoreExperienceBatchRequest(_BaseModel):
    """批量经验库写入请求"""
    records: list[dict]
    province: str
    reason: str = ""
    confirmed: bool = False


@app.post("/experience/store-batch")
def store_experience_batch_api(
    req: _StoreExperienceBatchRequest,
    x_api_key: str = Header(default=""),
):
    """批量经验库写入"""
    _verify_api_key(x_api_key)
    try:
        from tools.jarvis_store import store_one
        count = 0
        for rec in req.records:
            if rec.get("quota_ids"):
                ok = store_one(
                    name=rec["name"],
                    desc=rec.get("desc", ""),
                    quota_ids=rec["quota_ids"],
                    quota_names=rec.get("quota_names", []),
                    reason=req.reason,
                    specialty=rec.get("specialty", ""),
                    province=req.province or None,
                    confirmed=req.confirmed,
                    feedback_payload=rec.get("feedback_payload"),
                )
                if ok:
                    count += 1
        return {"success": True, "count": count}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"批量经验库写入失败: {e}")


class _FlagDisputedRequest(_BaseModel):
    """争议标记请求"""
    bill_name: str
    province: str
    reason: str = ""


@app.post("/experience/flag-disputed")
def flag_disputed_api(
    req: _FlagDisputedRequest,
    x_api_key: str = Header(default=""),
):
    """标记权威层记录为有争议（纠正经验库直通结果时调用）"""
    _verify_api_key(x_api_key)
    try:
        from src.experience_db import ExperienceDB
        db = ExperienceDB(province=req.province)
        affected = db.flag_disputed(
            bill_name=req.bill_name,
            province=req.province,
            reason=req.reason,
        )
        return {"success": True, "affected": affected}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"争议标记失败: {e}")


# ============================================================
# 主材价格查询（远程模式下懒猫转发到这里）
# ============================================================

@app.get("/experience/stats")
def experience_stats_api(x_api_key: str = Header(default="")):
    """返回经验库统计。"""
    _verify_api_key(x_api_key)
    try:
        from src.experience_db import ExperienceDB

        db = ExperienceDB()
        return db.get_stats()
    except Exception as e:
        logger.exception("获取经验库统计失败")
        raise HTTPException(status_code=500, detail=f"获取经验库统计失败: {e}")


@app.get("/experience/records")
def experience_records_api(
    layer: str = Query(default="all"),
    province: str | None = Query(default=None),
    province_name: str | None = Query(default=None),
    specialty_name: str | None = Query(default=None),
    category: str | None = Query(default=None),
    page: int = Query(default=1),
    size: int = Query(default=20),
    x_api_key: str = Header(default=""),
):
    """返回经验库记录列表。"""
    _verify_api_key(x_api_key)
    if page < 1:
        page = 1
    if size < 1 or size > 100:
        size = 20

    try:
        from src.experience_db import ExperienceDB

        db = ExperienceDB(province=province)
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
        records = _filter_records_by_category(records, category)
        total = len(records)
        start = (page - 1) * size
        end = start + size
        return {
            "items": records[start:end],
            "total": total,
            "page": page,
            "size": size,
        }
    except Exception as e:
        logger.exception("获取经验记录失败")
        raise HTTPException(status_code=500, detail=f"获取经验记录失败: {e}")


@app.get("/experience/search")
def experience_search_api(
    q: str = Query(...),
    province: str | None = Query(default=None),
    province_name: str | None = Query(default=None),
    specialty_name: str | None = Query(default=None),
    category: str | None = Query(default=None),
    limit: int = Query(default=20),
    x_api_key: str = Header(default=""),
):
    """搜索经验库记录。"""
    _verify_api_key(x_api_key)
    if not q.strip():
        raise HTTPException(status_code=400, detail="搜索关键词不能为空")
    if limit < 1 or limit > 200:
        limit = 20

    try:
        from src.experience_db import ExperienceDB

        db = ExperienceDB(province=province)
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
            return {"items": items, "total": len(items)}
        finally:
            conn.close()
    except Exception as e:
        logger.exception("搜索经验库失败")
        raise HTTPException(status_code=500, detail=f"搜索经验库失败: {e}")


@app.post("/experience/{record_id:int}/promote")
def promote_experience_api(
    record_id: int,
    x_api_key: str = Header(default=""),
):
    """晋升经验记录到权威层。"""
    _verify_api_key(x_api_key)
    try:
        from src.experience_db import ExperienceDB

        db = ExperienceDB()
        success = db.promote_to_authority(record_id)
        if not success:
            raise HTTPException(status_code=404, detail="记录不存在或已在权威层")
        return {"message": "晋升成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("晋升经验记录失败")
        raise HTTPException(status_code=500, detail=f"晋升经验记录失败: {e}")


@app.post("/experience/{record_id:int}/demote")
def demote_experience_api(
    record_id: int,
    x_api_key: str = Header(default=""),
):
    """降级经验记录到候选层。"""
    _verify_api_key(x_api_key)
    try:
        from src.experience_db import ExperienceDB

        db = ExperienceDB()
        success = db.demote_to_candidate(record_id)
        if not success:
            raise HTTPException(status_code=404, detail="记录不存在或已在候选层")
        return {"message": "降级成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("降级经验记录失败")
        raise HTTPException(status_code=500, detail=f"降级经验记录失败: {e}")


@app.delete("/experience/{record_id:int}")
def delete_experience_api(
    record_id: int,
    x_api_key: str = Header(default=""),
):
    """删除经验记录。"""
    _verify_api_key(x_api_key)
    try:
        from src.experience_db import ExperienceDB

        db = ExperienceDB()
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

        if not deleted:
            raise HTTPException(status_code=404, detail="记录不存在")
        return {"message": "删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("删除经验记录失败")
        raise HTTPException(status_code=500, detail=f"删除经验记录失败: {e}")


@app.delete("/experience/by-province")
def delete_experience_by_province_api(
    province: str = Query(...),
    x_api_key: str = Header(default=""),
):
    """按省份删除经验记录。"""
    _verify_api_key(x_api_key)
    if not province or not province.strip():
        raise HTTPException(status_code=400, detail="省份名称不能为空")

    try:
        from src.experience_db import ExperienceDB

        db = ExperienceDB(province=province)
        conn = db._connect()
        try:
            cursor = conn.execute("SELECT id FROM experiences WHERE province = ?", (province,))
            ids_to_delete = [str(row[0]) for row in cursor.fetchall()]
            if not ids_to_delete:
                return {"message": "已删除 0 条记录", "deleted": 0}

            conn.execute("DELETE FROM experiences WHERE province = ?", (province,))
            conn.commit()

            try:
                coll = db.collection
                if coll is not None:
                    coll.delete(ids=ids_to_delete)
            except Exception as e:
                logger.opt(exception=e).warning(f"批量清理向量索引失败（省份={province}）")
        finally:
            conn.close()

        return {"message": f"已删除 {len(ids_to_delete)} 条记录", "deleted": len(ids_to_delete)}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("按省份删除经验记录失败")
        raise HTTPException(status_code=500, detail=f"按省份删除经验记录失败: {e}")


class _BatchPromoteRequest(_BaseModel):
    """批量晋升请求。"""
    province: str | None = None
    dry_run: bool = True


@app.post("/experience/batch-promote")
def batch_promote_api(
    req: _BatchPromoteRequest,
    x_api_key: str = Header(default=""),
):
    """智能批量晋升候选层记录。"""
    _verify_api_key(x_api_key)
    try:
        from src.experience_db import ExperienceDB

        db = ExperienceDB(province=req.province)
        records = db.get_candidate_records(province=req.province, limit=0)
        records = [record for record in records if record.get("source") != "project_import_suspect"]

        promoted = 0
        skipped = 0
        errors: list[str] = []

        for record in records:
            quota_ids_raw = record.get("quota_ids", "[]")
            if isinstance(quota_ids_raw, str):
                try:
                    quota_ids = json.loads(quota_ids_raw)
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

            if db.promote_to_authority(record["id"], reason="智能批量晋升（定额校验通过）"):
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
    except Exception as e:
        logger.exception("批量晋升失败")
        raise HTTPException(status_code=500, detail=f"批量晋升失败: {e}")


@app.get("/material-price/provinces")
def material_price_provinces(x_api_key: str = Header(default="")):
    """返回有价格数据的省份列表"""
    _verify_api_key(x_api_key)
    import sqlite3
    from src.material_db import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            """SELECT province, COUNT(*) as cnt
               FROM price_fact
               WHERE province != '' AND province != '全国'
               GROUP BY province
               ORDER BY cnt DESC"""
        ).fetchall()
        return {"provinces": [{"name": r[0], "count": r[1]} for r in rows]}
    finally:
        conn.close()


@app.get("/material-price/cities")
def material_price_cities(
    province: str,
    x_api_key: str = Header(default=""),
):
    """返回指定省份下有价格数据的城市列表"""
    _verify_api_key(x_api_key)
    import sqlite3
    from src.material_db import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            """SELECT city, COUNT(*) as cnt
               FROM price_fact
               WHERE province = ? AND city != ''
               GROUP BY city
               ORDER BY cnt DESC""",
            (province,)
        ).fetchall()
        return {"cities": [{"name": r[0], "count": r[1]} for r in rows]}
    finally:
        conn.close()


@app.get("/material-price/periods")
def material_price_periods(
    province: str,
    city: str = "",
    x_api_key: str = Header(default=""),
):
    """返回指定省份/城市的信息价期次列表"""
    _verify_api_key(x_api_key)
    import sqlite3
    from src.material_db import DB_PATH
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conditions = ["province = ?", "period_start != ''"]
        params: list = [province]
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

        def _label(start):
            try:
                parts = start.split("-")
                return f"{int(parts[0])}年{int(parts[1])}月"
            except (IndexError, ValueError):
                return start

        return {
            "periods": [
                {"start": r[0], "end": r[1], "count": r[2], "label": _label(r[0])}
                for r in rows
            ]
        }
    finally:
        conn.close()


class _MaterialLookupRequest(_BaseModel):
    """主材批量查价请求"""
    model_config = {"extra": "ignore"}  # 兼容新旧版本，忽略多余字段
    materials: list[dict]
    province: str
    city: str = ""
    period_end: str = ""
    price_type: str = "all"  # all=不限, info=信息价, market=市场价


@app.post("/material-price/lookup")
def material_price_lookup(
    req: dict,
    x_api_key: str = Header(default=""),
):
    """批量查价"""
    _verify_api_key(x_api_key)
    import re as _re
    from src.material_db import MaterialDB
    db = MaterialDB()

    province = req.get("province", "")
    materials = req.get("materials", [])

    # 价格类型映射
    source_filter = ""
    price_type = req.get("price_type", "all")
    if price_type == "info":
        source_filter = "government"
    elif price_type == "market":
        source_filter = "market"

    results = []
    for mat in materials:
        name = mat.get("name", "").strip()
        spec = mat.get("spec", "").strip()
        unit = mat.get("unit", "").strip()
        if not name:
            results.append({**mat, "lookup_price": None, "lookup_source": "名称为空"})
            continue
        price_info = db.search_price_by_name(
            name, province=province, spec=spec, target_unit=unit,
            source_type=source_filter
        )
        if price_info:
            results.append({
                **mat,
                "lookup_price": price_info["price"],
                "lookup_source": price_info.get("source", "价格库"),
            })
        else:
            # 从名称中提取规格再查一次
            m = _re.search(r'[Dd][Nn]\s*\d+|De\s*\d+|Φ\s*\d+|\d+mm', name)
            if m:
                extracted_spec = m.group(0).replace(" ", "")
                short_name = name[:m.start()].strip()
                if short_name:
                    price_info2 = db.search_price_by_name(
                        short_name, province=province,
                        spec=extracted_spec, target_unit=unit,
                        source_type=source_filter
                    )
                    if price_info2:
                        results.append({
                            **mat,
                            "lookup_price": price_info2["price"],
                            "lookup_source": price_info2.get("source", "价格库"),
                        })
                        continue
            results.append({**mat, "lookup_price": None, "lookup_source": "未查到"})

    found = sum(1 for r in results if r.get("lookup_price") is not None)
    return {
        "results": results,
        "stats": {"total": len(results), "found": found, "not_found": len(results) - found},
    }


class _MaterialContributeRequest(_BaseModel):
    """用户贡献价格请求"""
    items: list[dict]


@app.post("/material-price/contribute")
def material_price_contribute(
    req: _MaterialContributeRequest,
    x_api_key: str = Header(default=""),
):
    """用户手填价格存入候选层"""
    _verify_api_key(x_api_key)
    from src.material_db import MaterialDB
    db = MaterialDB()
    saved = 0
    for item in req.items:
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
        if price_val <= 0 or price_val > 1_000_000:
            continue
        material_id = db.add_material(name, spec=spec, unit=unit)
        db.add_price(
            material_id=material_id,
            price_incl_tax=price_val,
            source_type="user_contribute",
            province=province, city=city, unit=unit,
            authority_level="reference",
            source_doc="用户手填",
            dedup=True,
        )
        saved += 1
    return {"saved": saved, "message": f"已保存 {saved} 条价格"}


# ============================================================
# 广材网实时查价（从Docker转发过来，本机执行爬虫）
# ============================================================

class _GldjcLookupRequest(_BaseModel):
    materials: list[dict]
    cookie: str

@app.post("/material-price/gldjc-lookup")
def material_price_gldjc_lookup(
    req: _GldjcLookupRequest,
    x_api_key: str = Header(default=""),
):
    """广材网实时查价（本机执行，Cookie绑定本机IP）"""
    _verify_api_key(x_api_key)

    cookie = req.cookie.strip()
    materials = req.materials
    if not cookie:
        raise HTTPException(400, "请输入广材网Cookie")
    if not materials:
        raise HTTPException(400, "材料列表为空")

    # 直接复用 tools/gldjc_price.py 的核心函数
    import sys
    import time
    import random
    tools_dir = str(Path(__file__).parent / "tools")
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
    search_dedup: dict[str, list] = {}
    net_requests = 0
    blocked = False
    MAX_BATCH = 30

    for i, mat in enumerate(materials):
        name = mat.get("name", "").strip()
        unit = mat.get("unit", "").strip()
        spec = mat.get("spec", "").strip()

        if not name:
            results.append({**mat, "gldjc_price": None, "gldjc_source": "名称为空"})
            continue

        # 先查缓存
        cached = check_cache(cache, name, unit)
        if cached and cached.get("price_with_tax") and cached.get("confidence") != "低":
            results.append({
                **mat,
                "gldjc_price": cached["price_with_tax"],
                "gldjc_source": f"广材网缓存({cached.get('confidence', '中')})",
            })
            continue

        if net_requests >= MAX_BATCH:
            results.append({**mat, "gldjc_price": None, "gldjc_source": f"已达单次上限{MAX_BATCH}条"})
            continue

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
            if kw in search_dedup:
                all_results = search_dedup[kw]
                searched_keyword = kw
                if all_results:
                    break
                continue

            if net_requests > 0:
                time.sleep(random.uniform(5, 8))

            web_results = search_material_web(session, kw)
            net_requests += 1
            searched_keyword = kw
            search_dedup[kw] = web_results

            if not web_results and net_requests >= 3:
                recent_empty = sum(1 for r in list(search_dedup.values())[-3:] if not r)
                if recent_empty >= 3:
                    logger.warning("广材网连续3次搜索无结果，疑似Cookie失效或被限制")
                    blocked = True

            if web_results:
                all_results = web_results
                break
            time.sleep(random.uniform(1, 2))

        if not all_results:
            update_cache(cache, name, unit, {
                "price_with_tax": None, "confidence": "低",
                "match_status": "未匹配", "source": "广材网",
                "query_date": datetime.now().strftime("%Y-%m-%d"),
                "result_count": 0, "searched_keyword": searched_keyword,
            })
            results.append({**mat, "gldjc_price": None, "gldjc_source": "广材网未找到"})
            continue

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
                "result_count": len(price_source), "searched_keyword": searched_keyword,
            })
            results.append({**mat, "gldjc_price": median, "gldjc_source": f"广材网市场价({confidence})"})
        else:
            update_cache(cache, name, unit, {
                "price_with_tax": median,
                "price_without_tax": round(median / 1.13, 2) if median else None,
                "confidence": "低", "match_status": "低置信度", "source": "广材网",
                "query_date": datetime.now().strftime("%Y-%m-%d"),
                "result_count": len(price_source), "searched_keyword": searched_keyword,
            })
            results.append({**mat, "gldjc_price": None, "gldjc_source": "广材网低置信度"})

        if net_requests % 10 == 0:
            save_cache(cache)

    save_cache(cache)
    logger.info(f"广材网查价完成：{len(materials)}条，{net_requests}次请求，"
                f"{'被限制' if blocked else '正常'}")

    found = sum(1 for r in results if r.get("gldjc_price"))
    return {"results": results, "total": len(results), "found": found}


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
    print(f"  监听地址: {HOST}")
    print(f"  端口: {PORT}")
    print(f"  API Key: {_mask_secret(API_KEY)}")
    print(f"  最大并发: {MAX_CONCURRENT}")
    print(f"  上传上限: {MAX_UPLOAD_MB}MB")
    print(f"  临时目录: {TEMP_DIR}")
    print()
    print("  把以下配置填入懒猫盒子的环境变量：")
    print(f"    MATCH_BACKEND=remote")
    print(f"    LOCAL_MATCH_URL=http://你的电脑IP:{PORT}")
    print("    LOCAL_MATCH_API_KEY=<使用你当前已配置的密钥>")
    print()
    print("  按 Ctrl+C 停止服务")
    print("=" * 60)

    # 启动服务
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
