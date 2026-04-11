"""
Auto Quota OpenClaw bridge helper.

This script intentionally avoids web-login / bearer-token flow and talks only to
the dedicated `/api/openclaw/*` bridge endpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
GREEN_THRESHOLD = 90
YELLOW_THRESHOLD = 70


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"配置文件不存在: {CONFIG_PATH}")
        sys.exit(1)
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def load_processed() -> dict:
    cfg = load_config()
    history_file = Path(cfg["processed"]["history_file"])
    if history_file.exists():
        return json.loads(history_file.read_text(encoding="utf-8-sig"))
    return {"files": {}}


def save_processed(data: dict) -> None:
    cfg = load_config()
    history_file = Path(cfg["processed"]["history_file"])
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def file_hash(filepath: str) -> str:
    md5 = hashlib.md5()
    with open(filepath, "rb") as handle:
        for chunk in iter(lambda: handle.read(8192), b""):
            md5.update(chunk)
    return md5.hexdigest()


class AutoQuotaAPI:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise ValueError("缺少 OpenClaw API Key，请配置 OPENCLAW_API_KEY 或 config.json")

    def _request(self, method: str, path: str, data=None, files=None, form_data=None):
        url = f"{self.base_url}{path}"
        headers = {"X-OpenClaw-Key": self.api_key}
        body = None

        if files:
            boundary = f"----AutoQuotaBoundary{int(time.time())}"
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            body = self._build_multipart(boundary, form_data or {}, files)
        elif data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")

        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            response = urllib.request.urlopen(request, context=SSL_CTX, timeout=120)
            payload = response.read()
            if not payload:
                return {}
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return json.loads(payload.decode("utf-8"))
            return payload
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            print(f"API 错误 {exc.code}: {error_body}")
            return None
        except Exception as exc:
            print(f"请求失败: {exc}")
            return None

    @staticmethod
    def _build_multipart(boundary: str, fields: dict, files: dict) -> bytes:
        lines: list[bytes] = []
        for key, value in fields.items():
            lines.append(f"--{boundary}".encode())
            lines.append(f'Content-Disposition: form-data; name="{key}"'.encode())
            lines.append(b"")
            lines.append(str(value).encode("utf-8"))

        for key, (filename, filedata, content_type) in files.items():
            lines.append(f"--{boundary}".encode())
            lines.append(
                f'Content-Disposition: form-data; name="{key}"; filename="{filename}"'.encode()
            )
            lines.append(f"Content-Type: {content_type}".encode())
            lines.append(b"")
            lines.append(filedata)

        lines.append(f"--{boundary}--".encode())
        lines.append(b"")
        return b"\r\n".join(lines)

    def create_task(self, filepath: str, province: str, mode: str = "search", use_experience: bool = True):
        filename = Path(filepath).name
        with open(filepath, "rb") as handle:
            file_data = handle.read()

        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if filename.endswith(".xlsx")
            else "application/vnd.ms-excel"
        )

        result = self._request(
            "POST",
            "/api/openclaw/tasks",
            files={"file": (filename, file_data, content_type)},
            form_data={
                "province": province,
                "mode": mode,
                "use_experience": str(use_experience).lower(),
            },
        )
        if result and "id" in result:
            return result["id"]
        return None

    def list_tasks(self, page: int = 1, size: int = 10):
        return self._request("GET", f"/api/openclaw/tasks?page={page}&size={size}")

    def get_task_status(self, task_id: str):
        return self._request("GET", f"/api/openclaw/tasks/{task_id}")

    def get_results(self, task_id: str):
        return self._request("GET", f"/api/openclaw/tasks/{task_id}/results")

    def get_provinces(self):
        return self._request("GET", "/api/openclaw/provinces")

    def list_source_packs(self, query: str = "", limit: int = 20, source_kind: str = "", province: str = "", specialty: str = ""):
        params = {"limit": str(limit)}
        if query:
            params["q"] = query
        if source_kind:
            params["source_kind"] = source_kind
        if province:
            params["province"] = province
        if specialty:
            params["specialty"] = specialty
        query_string = urllib.parse.urlencode(params)
        return self._request("GET", f"/api/openclaw/source-packs?{query_string}")

    def get_source_pack(self, source_id: str):
        return self._request("GET", f"/api/openclaw/source-packs/{source_id}")

    def learn_source_pack(
        self,
        source_id: str,
        *,
        dry_run: bool = False,
        llm_type: str | None = None,
        chunk_size: int = 1800,
        overlap: int = 240,
        max_chunks: int = 24,
    ):
        payload = {
            "dry_run": dry_run,
            "chunk_size": chunk_size,
            "overlap": overlap,
            "max_chunks": max_chunks,
        }
        if llm_type:
            payload["llm_type"] = llm_type
        return self._request("POST", f"/api/openclaw/source-packs/{source_id}/learn", data=payload)


    def search_quotas(self, keyword: str, province: str, book: str | None = None, limit: int = 20):
        params = {
            "keyword": keyword,
            "province": province,
            "limit": str(limit),
        }
        if book:
            params["book"] = book
        query = urllib.parse.urlencode(params)
        return self._request("GET", f"/api/openclaw/quota-search?{query}")

    def save_review_draft(
        self,
        task_id: str,
        result_id: str,
        corrected_quotas: list[dict],
        review_note: str = "",
        review_confidence: int | None = None,
    ):
        return self._request(
            "PUT",
            f"/api/openclaw/tasks/{task_id}/results/{result_id}/review-draft",
            data={
                "openclaw_suggested_quotas": corrected_quotas,
                "openclaw_review_note": review_note,
                "openclaw_review_confidence": review_confidence,
            },
        )

    def auto_confirm_green(self, task_id: str):
        return self._request(
            "POST",
            f"/api/openclaw/tasks/{task_id}/results/auto-confirm-green",
        )

    def download_export(self, task_id: str, save_path: str) -> bool:
        url = f"{self.base_url}/api/openclaw/tasks/{task_id}/export-final"
        request = urllib.request.Request(
            url,
            headers={"X-OpenClaw-Key": self.api_key},
            method="GET",
        )
        try:
            response = urllib.request.urlopen(request, context=SSL_CTX, timeout=120)
            Path(save_path).write_bytes(response.read())
            return True
        except Exception as exc:
            print(f"下载失败: {exc}")
            return False


def build_api_client() -> AutoQuotaAPI:
    cfg = load_config()
    api_cfg = cfg["api"]
    api_key = os.getenv("OPENCLAW_API_KEY") or api_cfg.get("openclaw_api_key", "")
    return AutoQuotaAPI(api_cfg["base_url"], api_key)


def wait_for_task(api: AutoQuotaAPI, task_id: str, interval: int = 10):
    print("等待任务完成...")
    while True:
        result = api.get_task_status(task_id)
        if not result:
            print("无法获取任务状态")
            return None

        status = result.get("status", "unknown")
        progress = result.get("progress", 0)
        message = result.get("progress_message", "")
        print(f"  [{progress}%] {status} - {message}")

        if status == "completed":
            return result
        if status in ("failed", "cancelled"):
            print(f"任务{status}: {result.get('error_message', '')}")
            return result

        time.sleep(interval)


def format_result_summary(results_data: dict | None) -> str:
    if not results_data:
        return "无法获取结果"

    summary = results_data.get("summary", {})
    total = summary.get("total", 0)
    high = summary.get("high_confidence", 0)
    mid = summary.get("mid_confidence", 0)
    low = summary.get("low_confidence", 0)
    no_match = summary.get("no_match", 0)
    green_rate = round((high / total) * 100, 1) if total else 0

    return "\n".join(
        [
            "匹配结果汇总",
            f"  总条数: {total}",
            f"  绿灯(>={GREEN_THRESHOLD}%): {high}",
            f"  黄灯({YELLOW_THRESHOLD}-{GREEN_THRESHOLD - 1}%): {mid}",
            f"  红灯(<{YELLOW_THRESHOLD}%): {low}",
            f"  未匹配: {no_match}",
            f"  绿灯率: {green_rate}%",
        ]
    )


def format_source_pack_list(data: dict | None) -> str:
    if not data:
        return "???? source packs"

    items = data.get("items", [])
    if not items:
        return "????????"

    lines = ["?????:"]
    for item in items:
        lines.append(
            f"  {item.get('source_id', '')} | {item.get('title', '')} | "
            f"{item.get('province', '')} | {item.get('specialty', '')} | {item.get('source_kind', '')}"
        )
    return "\n".join(lines)


def format_source_learning_result(data: dict | None) -> str:
    if not data:
        return "??????"

    lines = [
        "??????:",
        f"  source_id: {data.get('source_id', '')}",
        f"  title: {data.get('title', '')}",
        f"  chunks: {data.get('chunks', 0)}",
        f"  raw_candidates: {data.get('raw_candidates', 0)}",
        f"  merged_candidates: {data.get('merged_candidates', 0)}",
        f"  staged: {data.get('staged', 0)}",
    ]
    candidates = data.get("candidates", []) or []
    if candidates:
        lines.append("  candidates:")
        for item in candidates[:10]:
            lines.append(
                f"    - {item.get('candidate_type', '')} | {item.get('candidate_title', '')} | {item.get('target_layer', '')}"
            )
    return "\n".join(lines)


def format_detail_for_review(results_data: dict | None) -> str:
    if not results_data:
        return ""

    items = results_data.get("items", [])
    if not items:
        return ""

    red_items: list[str] = []
    yellow_items: list[str] = []
    green_count = 0

    for item in sorted(items, key=lambda value: value.get("confidence", 0)):
        confidence = item.get("confidence", 0)
        index = item.get("index", "?")
        bill_name = item.get("bill_name", "未知")
        bill_desc = item.get("bill_description", "")
        quotas = item.get("quotas") or []
        quota_text = " + ".join(
            f"{quota.get('quota_id', '?')} {quota.get('name', '?')}"
            for quota in quotas
        ) or "未匹配"
        desc_text = f" ({bill_desc})" if bill_desc else ""
        line = f"[{index}] {bill_name}{desc_text} -> {quota_text} ({confidence}%)"

        if confidence < YELLOW_THRESHOLD:
            red_items.append(line)
        elif confidence < GREEN_THRESHOLD:
            yellow_items.append(line)
        else:
            green_count += 1

    lines = ["", "=" * 60, "待审核明细", "=" * 60]
    if red_items:
        lines.append(f"红灯(<{YELLOW_THRESHOLD}%):")
        lines.extend(red_items)
    if yellow_items:
        lines.append(f"黄灯({YELLOW_THRESHOLD}-{GREEN_THRESHOLD - 1}%):")
        lines.extend(yellow_items)
    lines.append(f"绿灯(>={GREEN_THRESHOLD}%): {green_count} 条，省略明细")
    return "\n".join(lines)


def process_file(api: AutoQuotaAPI, filepath: str, province: str, mode: str = "search", use_experience: bool = True):
    print("=" * 50)
    print(f"开始处理: {Path(filepath).name}")
    print(f"省份: {province} | 模式: {mode}")
    print("=" * 50)

    task_id = api.create_task(filepath, province, mode, use_experience)
    if not task_id:
        return None

    task_result = wait_for_task(api, task_id)
    if not task_result or task_result.get("status") != "completed":
        return None

    results = api.get_results(task_id)
    print(format_result_summary(results))
    print(format_detail_for_review(results))

    output_dir = Path(filepath).parent / "auto_quota_results"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{Path(filepath).stem}_定额匹配结果.xlsx"
    api.download_export(task_id, str(output_path))

    return {
        "task_id": task_id,
        "file": filepath,
        "output": str(output_path),
        "results": results,
    }


def find_new_excel_files(watch_dir: str, processed: dict) -> list[str]:
    new_files: list[str] = []
    for filepath in Path(watch_dir).glob("**/*.xlsx"):
        name = filepath.name
        if name.startswith("~") or name.startswith("."):
            continue
        if filepath.stat().st_size < 1024:
            continue
        full_path = str(filepath)
        if full_path in processed.get("files", {}):
            continue
        new_files.append(full_path)
    return new_files


def cmd_watch(args) -> None:
    cfg = load_config()
    match_cfg = cfg["match"]
    wechat_cfg = cfg["wechat"]

    watch_dir = wechat_cfg["watch_dir"]
    interval = wechat_cfg.get("scan_interval_seconds", 30)
    province = args.province or match_cfg["default_province"]
    mode = match_cfg.get("mode", "search")
    use_experience = bool(match_cfg.get("use_experience", True))

    try:
        api = build_api_client()
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    processed = load_processed()

    print(f"开始监控目录: {watch_dir}")
    print(f"扫描间隔: {interval} 秒")
    print(f"默认省份: {province}")

    while True:
        try:
            if not Path(watch_dir).exists():
                print(f"监控目录不存在: {watch_dir}")
                time.sleep(interval)
                continue

            new_files = find_new_excel_files(watch_dir, processed)
            if new_files:
                print(f"发现 {len(new_files)} 个新文件")
            for filepath in new_files:
                result = process_file(api, filepath, province, mode, use_experience)
                processed["files"][filepath] = {
                    "processed_at": datetime.now().isoformat(),
                    "md5": file_hash(filepath),
                    "task_id": result["task_id"] if result else None,
                    "success": result is not None,
                }
                save_processed(processed)

            time.sleep(interval)
        except KeyboardInterrupt:
            print("监控已停止")
            return
        except Exception as exc:
            print(f"监控出错: {exc}")
            time.sleep(interval)


def cmd_match(args) -> None:
    cfg = load_config()
    match_cfg = cfg["match"]
    filepath = args.file

    if not Path(filepath).exists():
        print(f"文件不存在: {filepath}")
        sys.exit(1)

    province = args.province or match_cfg["default_province"]
    mode = args.mode or match_cfg.get("mode", "search")
    use_experience = bool(match_cfg.get("use_experience", True))

    try:
        api = build_api_client()
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    result = process_file(api, filepath, province, mode, use_experience)
    if not result:
        print("处理失败")
        sys.exit(1)

    print(f"完成，结果文件: {result['output']}")


def cmd_status(_args) -> None:
    try:
        api = build_api_client()
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    result = api.list_tasks(page=1, size=10)
    if not result or "items" not in result:
        print("无法获取任务列表")
        return

    tasks = result["items"]
    if not tasks:
        print("暂无任务")
        return

    print(f"最近 {len(tasks)} 个任务:")
    for task in tasks:
        stats = task.get("stats") or {}
        print(
            f"  {task.get('status', 'unknown'):10} | "
            f"{task.get('province', ''):12} | "
            f"{stats.get('total', '-')!s:6} | "
            f"{task.get('created_at', '')[:16]} | "
            f"{task.get('name', 'unknown')}"
        )


def cmd_confirm(args) -> None:
    try:
        api = build_api_client()
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    result = api.auto_confirm_green(args.task_id)
    if not result:
        print("绿灯自动确认失败")
        sys.exit(1)

    print(
        "绿灯自动确认完成: "
        f"confirmed={result.get('confirmed', 0)}, "
        f"skipped_low_confidence={result.get('skipped_low_confidence', 0)}, "
        f"skipped_corrected={result.get('skipped_corrected', 0)}"
    )


def cmd_source_list(args) -> None:
    try:
        api = build_api_client()
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    result = api.list_source_packs(
        query=args.query or "",
        limit=args.limit,
        source_kind=args.source_kind or "",
        province=args.province or "",
        specialty=args.specialty or "",
    )
    if result is None:
        print("?? source packs ??")
        sys.exit(1)
    print(format_source_pack_list(result))


def cmd_source_show(args) -> None:
    try:
        api = build_api_client()
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    result = api.get_source_pack(args.source_id)
    if result is None:
        print("?? source pack ??")
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_source_learn(args) -> None:
    cfg = load_config()
    learning_cfg = cfg.get("source_learning", {})

    try:
        api = build_api_client()
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    result = api.learn_source_pack(
        args.source_id,
        dry_run=args.dry_run if args.dry_run else bool(learning_cfg.get("dry_run", False)),
        llm_type=args.llm or learning_cfg.get("llm_type") or None,
        chunk_size=args.chunk_size or int(learning_cfg.get("chunk_size", 1800)),
        overlap=args.overlap or int(learning_cfg.get("overlap", 240)),
        max_chunks=args.max_chunks or int(learning_cfg.get("max_chunks", 24)),
    )
    if result is None:
        print("??????")
        sys.exit(1)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_source_learning_result(result))


def cmd_correct(args) -> None:
    try:
        api = build_api_client()
    except ValueError as exc:
        print(exc)
        sys.exit(1)

    corrected_quotas = [
        {
            "quota_id": args.quota_id,
            "name": args.quota_name,
            "unit": args.unit or "",
        }
    ]
    note = args.note or "OpenClaw yellow review draft"
    result = api.save_review_draft(args.task_id, args.result_id, corrected_quotas, note)
    if not result:
        print("纠正失败")
        sys.exit(1)

    print("纠正成功")


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto Quota OpenClaw helper")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    watch_parser = subparsers.add_parser("watch", help="持续监控微信文件目录")
    watch_parser.add_argument("--province", help="默认省份定额库名称")

    match_parser = subparsers.add_parser("match", help="单次匹配指定文件")
    match_parser.add_argument("file", help="Excel 文件路径")
    match_parser.add_argument("--province", help="省份定额库名称")
    match_parser.add_argument("--mode", choices=["search", "agent"], help="匹配模式")

    subparsers.add_parser("status", help="查看最近任务")

    confirm_parser = subparsers.add_parser("confirm", help="自动确认绿灯结果")
    confirm_parser.add_argument("task_id", help="任务 ID")

    correct_parser = subparsers.add_parser("correct", help="纠正单条结果")
    correct_parser.add_argument("task_id", help="任务 ID")
    correct_parser.add_argument("result_id", help="结果 ID")
    correct_parser.add_argument("quota_id", help="正确的定额编号")
    correct_parser.add_argument("quota_name", help="正确的定额名称")
    correct_parser.add_argument("--unit", default="", help="计量单位")
    correct_parser.add_argument("--note", default="", help="审核备注")

    source_list_parser = subparsers.add_parser("source-list", help="List learnable source packs")
    source_list_parser.add_argument("--query", default="", help="Filter by title or summary text")
    source_list_parser.add_argument("--source-kind", default="", help="Filter by source kind")
    source_list_parser.add_argument("--province", default="", help="Filter by province")
    source_list_parser.add_argument("--specialty", default="", help="Filter by specialty")
    source_list_parser.add_argument("--limit", type=int, default=20, help="Max number of source packs")

    source_show_parser = subparsers.add_parser("source-show", help="Show one source pack")
    source_show_parser.add_argument("source_id", help="Source pack id")

    source_learn_parser = subparsers.add_parser("source-learn", help="Extract learning candidates from one source pack")
    source_learn_parser.add_argument("source_id", help="Source pack id")
    source_learn_parser.add_argument("--dry-run", action="store_true", help="Preview only, do not write promotion_queue")
    source_learn_parser.add_argument("--llm", default="", help="LLM type, for example deepseek")
    source_learn_parser.add_argument("--chunk-size", type=int, default=0, help="Chunk size override")
    source_learn_parser.add_argument("--overlap", type=int, default=0, help="Chunk overlap override")
    source_learn_parser.add_argument("--max-chunks", type=int, default=0, help="Max chunks override")
    source_learn_parser.add_argument("--json", action="store_true", help="Print raw JSON result")
    args = parser.parse_args()

    if args.command == "watch":
        cmd_watch(args)
    elif args.command == "match":
        cmd_match(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "confirm":
        cmd_confirm(args)
    elif args.command == "source-list":
        cmd_source_list(args)
    elif args.command == "source-show":
        cmd_source_show(args)
    elif args.command == "source-learn":
        cmd_source_learn(args)
    elif args.command == "correct":
        cmd_correct(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

