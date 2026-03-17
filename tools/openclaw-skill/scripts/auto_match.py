"""
Auto Quota 自动匹配脚本（OpenClaw Skill 用）

功能：
1. watch 模式 — 持续监控微信文件目录，发现新Excel自动匹配
2. match 模式 — 指定文件，单次匹配
3. status 模式 — 查看最近任务状态

用法：
    python auto_match.py watch                              # 持续监控
    python auto_match.py match "/path/to/file.xlsx"         # 单次匹配
    python auto_match.py match "/path/to/file.xlsx" --province "河南2024"
    python auto_match.py status                             # 查看状态
"""

import os
import sys
import json
import time
import glob
import hashlib
import argparse
from pathlib import Path
from datetime import datetime

# --- 用 urllib 替代 requests，避免依赖安装问题 ---
import urllib.request
import urllib.error
import urllib.parse
import ssl

# 忽略SSL证书验证（内网环境）
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"


def load_config():
    """加载配置文件"""
    if not CONFIG_PATH.exists():
        print(f"❌ 配置文件不存在: {CONFIG_PATH}")
        print("请先编辑 config.json，填入API地址和登录信息")
        sys.exit(1)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_processed():
    """加载已处理文件记录"""
    cfg = load_config()
    history_file = Path(cfg["processed"]["history_file"])
    if history_file.exists():
        with open(history_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"files": {}}


def save_processed(data):
    """保存已处理文件记录"""
    cfg = load_config()
    history_file = Path(cfg["processed"]["history_file"])
    history_file.parent.mkdir(parents=True, exist_ok=True)
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def file_hash(filepath):
    """计算文件MD5，避免同文件重复处理"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# ========== API 调用 ==========

class AutoQuotaAPI:
    """Auto Quota API 客户端（纯标准库，不依赖requests）"""

    def __init__(self, base_url, email, password):
        self.base_url = base_url.rstrip("/")
        self.email = email
        self.password = password
        self.token = None

    def _request(self, method, path, data=None, files=None, form_data=None):
        """发送HTTP请求"""
        url = f"{self.base_url}{path}"
        headers = {}
        body = None

        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        if files:
            # multipart/form-data（文件上传）
            boundary = f"----AutoQuotaBoundary{int(time.time())}"
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            body = self._build_multipart(boundary, form_data or {}, files)
        elif data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=120)
            resp_body = resp.read().decode("utf-8")
            if resp_body:
                return json.loads(resp_body)
            return {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            print(f"❌ API错误 {e.code}: {error_body}")
            return None
        except Exception as e:
            print(f"❌ 请求失败: {e}")
            return None

    def _build_multipart(self, boundary, fields, files):
        """构建 multipart/form-data 请求体"""
        lines = []
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

    def login(self):
        """登录获取Token"""
        print(f"🔑 正在登录 {self.base_url} ...")
        result = self._request("POST", "/api/auth/login", data={
            "email": self.email,
            "password": self.password,
        })
        if result and "access_token" in result:
            self.token = result["access_token"]
            print("✅ 登录成功")
            return True
        else:
            print("❌ 登录失败，请检查邮箱和密码")
            return False

    def create_task(self, filepath, province, mode="search", use_experience=True):
        """创建匹配任务（上传Excel）"""
        filename = Path(filepath).name
        print(f"📤 上传文件: {filename} (省份: {province}, 模式: {mode})")

        with open(filepath, "rb") as f:
            file_data = f.read()

        content_type = (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            if filename.endswith(".xlsx")
            else "application/vnd.ms-excel"
        )

        result = self._request(
            "POST", "/api/tasks",
            files={"file": (filename, file_data, content_type)},
            form_data={
                "province": province,
                "mode": mode,
                "use_experience": str(use_experience).lower(),
            },
        )

        if result and "id" in result:
            task_id = result["id"]
            print(f"✅ 任务创建成功: {task_id}")
            return task_id
        return None

    def get_task_status(self, task_id):
        """查询任务状态"""
        return self._request("GET", f"/api/tasks/{task_id}")

    def get_results(self, task_id):
        """获取匹配结果"""
        return self._request("GET", f"/api/tasks/{task_id}/results")

    def list_tasks(self, page=1, size=5):
        """获取最近任务列表"""
        return self._request("GET", f"/api/tasks?page={page}&size={size}")

    def search_quotas(self, keyword, province, book=None, limit=20):
        """搜索定额（让AI找到正确的定额编号）

        参数:
            keyword: 搜索关键词，如"管道安装 DN25"
            province: 省份，如"北京2024"
            book: 大册过滤，如"C10"（给排水）
        返回:
            {"items": [{"quota_id": "C10-1-10", "name": "...", "unit": "..."}]}
        """
        params = f"keyword={urllib.parse.quote(keyword)}&province={urllib.parse.quote(province)}&limit={limit}"
        if book:
            params += f"&book={urllib.parse.quote(book)}"
        return self._request("GET", f"/api/quota-search?{params}")

    def get_provinces(self):
        """获取可用的省份定额库列表"""
        return self._request("GET", f"/api/quota-search/provinces")

    def correct_result(self, task_id, result_id, corrected_quotas, review_note=""):
        """纠正匹配结果（错了的，改成正确的定额）

        纠正后自动回流经验库候选层，管理员审核后晋升权威层。
        参数:
            corrected_quotas: [{"quota_id": "C10-1-10", "name": "管道安装", "unit": "m"}]
            review_note: 审核备注（说明为什么改）
        """
        return self._request("PUT", f"/api/tasks/{task_id}/results/{result_id}", data={
            "corrected_quotas": corrected_quotas,
            "review_note": review_note,
        })

    def confirm_results(self, task_id, result_ids):
        """批量确认匹配结果（对的，确认没问题）

        确认后自动回流经验库权威层，系统下次遇到同类清单直接命中。
        参数:
            result_ids: ["uuid1", "uuid2", ...] 要确认的结果ID列表
        """
        return self._request("POST", f"/api/tasks/{task_id}/results/confirm", data={
            "result_ids": result_ids,
        })

    def download_export(self, task_id, save_path):
        """下载结果Excel"""
        url = f"{self.base_url}/api/tasks/{task_id}/export"
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = urllib.request.Request(url, headers=headers)
        try:
            resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=120)
            with open(save_path, "wb") as f:
                f.write(resp.read())
            print(f"📥 结果已下载: {save_path}")
            return True
        except Exception as e:
            print(f"❌ 下载失败: {e}")
            return False


# ========== 核心逻辑 ==========

def wait_for_task(api, task_id, interval=10):
    """轮询等待任务完成"""
    print("⏳ 等待匹配完成...")
    while True:
        result = api.get_task_status(task_id)
        if not result:
            print("❌ 无法获取任务状态")
            return None

        status = result.get("status", "unknown")
        progress = result.get("progress", 0)
        message = result.get("progress_message", "")

        print(f"   [{progress}%] {status} - {message}")

        if status == "completed":
            print("✅ 匹配完成！")
            return result
        elif status in ("failed", "cancelled"):
            error = result.get("error_message", "未知错误")
            print(f"❌ 任务{status}: {error}")
            return result

        time.sleep(interval)


def format_result_summary(results_data):
    """格式化结果摘要"""
    if not results_data:
        return "无法获取结果"

    summary = results_data.get("summary", {})
    total = summary.get("total", 0)
    high = summary.get("high_confidence", 0)
    mid = summary.get("mid_confidence", 0)
    low = summary.get("low_confidence", 0)
    no_match = summary.get("no_match", 0)

    lines = [
        f"📊 匹配结果汇总",
        f"   总条数: {total}",
        f"   🟢 高置信度（≥85%）: {high} 条",
        f"   🟡 中置信度（70-84%）: {mid} 条",
        f"   🔴 低置信度（<70%）: {low} 条",
        f"   ⚪ 未匹配: {no_match} 条",
    ]

    if total > 0:
        accuracy = round(high / total * 100, 1)
        lines.append(f"   📈 绿灯率: {accuracy}%")

    return "\n".join(lines)


def format_detail_for_review(results_data):
    """格式化详细结果，供 OpenClaw AI 审核

    输出每条清单和匹配的定额，让AI判断哪些可能套错了。
    重点输出黄灯和红灯的项目（最需要审核的）。
    """
    if not results_data:
        return ""

    items = results_data.get("items", [])
    if not items:
        return ""

    lines = [
        "",
        "=" * 60,
        "🔍 以下是需要AI审核的匹配结果（按置信度从低到高排列）",
        "格式: [序号] 清单名称 → 匹配定额 (置信度%)",
        "=" * 60,
    ]

    # 按置信度升序排列（低置信度优先审核）
    sorted_items = sorted(items, key=lambda x: x.get("confidence", 0))

    red_items = []    # 红灯（<70%）
    yellow_items = []  # 黄灯（70-84%）
    green_items = []   # 绿灯（≥85%）

    for item in sorted_items:
        conf = item.get("confidence", 0)
        idx = item.get("index", "?")
        bill_name = item.get("bill_name", "未知")
        bill_desc = item.get("bill_description", "")
        quotas = item.get("quotas") or []

        # 拼接定额信息
        if quotas:
            quota_text = " + ".join(
                f"{q.get('quota_id', '?')} {q.get('name', '?')}"
                for q in quotas
            )
        else:
            quota_text = "❌ 未匹配"

        # 清单描述（如果有的话）
        desc_text = f"（{bill_desc}）" if bill_desc else ""
        line = f"[{idx}] {bill_name}{desc_text} → {quota_text} ({conf}%)"

        if conf < 70:
            red_items.append(f"🔴 {line}")
        elif conf < 85:
            yellow_items.append(f"🟡 {line}")
        else:
            green_items.append(f"🟢 {line}")

    # 红灯项目全部输出
    if red_items:
        lines.append(f"\n--- 🔴 红灯（低置信度 <70%，共{len(red_items)}条）---")
        lines.extend(red_items)

    # 黄灯项目全部输出
    if yellow_items:
        lines.append(f"\n--- 🟡 黄灯（中置信度 70-84%，共{len(yellow_items)}条）---")
        lines.extend(yellow_items)

    # 绿灯项目只显示数量（太多了不用逐条审核）
    if green_items:
        lines.append(f"\n--- 🟢 绿灯（高置信度 ≥85%，共{len(green_items)}条）--- 省略详情")

    lines.append("")
    lines.append("请根据以上结果进行审核，找出可能套错的定额。")

    return "\n".join(lines)


def process_file(api, filepath, province, mode="search", use_experience=True):
    """处理单个文件：上传 → 等待 → 获取结果 → 下载"""
    print(f"\n{'='*50}")
    print(f"📋 开始处理: {Path(filepath).name}")
    print(f"   省份: {province} | 模式: {mode}")
    print(f"{'='*50}")

    # 1. 创建任务
    task_id = api.create_task(filepath, province, mode, use_experience)
    if not task_id:
        return None

    # 2. 等待完成
    task_result = wait_for_task(api, task_id)
    if not task_result or task_result.get("status") != "completed":
        return None

    # 3. 获取结果摘要 + 详细审核数据
    results = api.get_results(task_id)
    summary_text = format_result_summary(results)
    detail_text = format_detail_for_review(results)
    print(f"\n{summary_text}")
    print(detail_text)

    # 4. 下载结果Excel
    output_dir = Path(filepath).parent / "auto_quota_results"
    output_dir.mkdir(exist_ok=True)
    output_name = Path(filepath).stem + "_定额匹配结果.xlsx"
    output_path = output_dir / output_name
    api.download_export(task_id, str(output_path))

    return {
        "task_id": task_id,
        "file": filepath,
        "summary": summary_text,
        "output": str(output_path),
        "results": results,  # 完整结果数据，供审核后纠正/确认用
    }


def find_new_excel_files(watch_dir, processed):
    """扫描目录，找出新的Excel文件"""
    new_files = []
    patterns = ["**/*.xlsx"]  # 跳过.xls，提交会失败

    for pattern in patterns:
        for filepath in Path(watch_dir).glob(pattern):
            # 跳过临时文件和已处理文件
            name = filepath.name
            if name.startswith("~") or name.startswith("."):
                continue

            str_path = str(filepath)
            if str_path in processed.get("files", {}):
                continue

            # 检查文件大小（至少1KB，排除空文件）
            if filepath.stat().st_size < 1024:
                continue

            new_files.append(str_path)

    return new_files


# ========== 命令入口 ==========

def cmd_watch(args):
    """持续监控模式"""
    cfg = load_config()
    api_cfg = cfg["api"]
    match_cfg = cfg["match"]
    wechat_cfg = cfg["wechat"]

    watch_dir = wechat_cfg["watch_dir"]
    interval = wechat_cfg.get("scan_interval_seconds", 30)
    province = args.province or match_cfg["default_province"]
    mode = match_cfg.get("mode", "search")

    print(f"👀 开始监控目录: {watch_dir}")
    print(f"   扫描间隔: {interval}秒")
    print(f"   默认省份: {province}")
    print(f"   按 Ctrl+C 停止\n")

    # 登录
    api = AutoQuotaAPI(api_cfg["base_url"], api_cfg["email"], api_cfg["password"])
    if not api.login():
        sys.exit(1)

    processed = load_processed()

    while True:
        try:
            # 检查目录是否存在
            if not Path(watch_dir).exists():
                print(f"⚠️  监控目录不存在: {watch_dir}，等待{interval}秒后重试...")
                time.sleep(interval)
                continue

            # 扫描新文件
            new_files = find_new_excel_files(watch_dir, processed)

            if new_files:
                print(f"\n📁 发现 {len(new_files)} 个新文件")
                for filepath in new_files:
                    result = process_file(api, filepath, province, mode)

                    # 记录已处理
                    processed["files"][filepath] = {
                        "processed_at": datetime.now().isoformat(),
                        "md5": file_hash(filepath),
                        "task_id": result["task_id"] if result else None,
                        "success": result is not None,
                    }
                    save_processed(processed)

            time.sleep(interval)

        except KeyboardInterrupt:
            print("\n👋 监控已停止")
            break
        except Exception as e:
            print(f"❌ 监控出错: {e}")
            time.sleep(interval)


def cmd_match(args):
    """单次匹配模式"""
    cfg = load_config()
    api_cfg = cfg["api"]
    match_cfg = cfg["match"]

    filepath = args.file
    if not Path(filepath).exists():
        print(f"❌ 文件不存在: {filepath}")
        sys.exit(1)

    province = args.province or match_cfg["default_province"]
    mode = args.mode or match_cfg.get("mode", "search")

    # 登录
    api = AutoQuotaAPI(api_cfg["base_url"], api_cfg["email"], api_cfg["password"])
    if not api.login():
        sys.exit(1)

    # 处理文件
    result = process_file(api, filepath, province, mode)
    if result:
        print(f"\n✅ 完成！结果文件: {result['output']}")
    else:
        print(f"\n❌ 处理失败")
        sys.exit(1)


def cmd_status(args):
    """查看最近任务状态"""
    cfg = load_config()
    api_cfg = cfg["api"]

    # 登录
    api = AutoQuotaAPI(api_cfg["base_url"], api_cfg["email"], api_cfg["password"])
    if not api.login():
        sys.exit(1)

    # 获取最近任务
    result = api.list_tasks(page=1, size=10)
    if not result or "items" not in result:
        print("❌ 无法获取任务列表")
        return

    tasks = result["items"]
    if not tasks:
        print("📭 暂无任务")
        return

    print(f"\n📋 最近 {len(tasks)} 个任务:\n")
    for t in tasks:
        status_icon = {
            "completed": "✅",
            "running": "⏳",
            "pending": "🕐",
            "failed": "❌",
            "cancelled": "🚫",
        }.get(t.get("status", ""), "❓")

        name = t.get("name", "未知")
        status = t.get("status", "未知")
        province = t.get("province", "")
        created = t.get("created_at", "")[:16]
        stats = t.get("stats") or {}
        total = stats.get("total", "-")

        print(f"  {status_icon} {name} | {province} | {status} | {total}条 | {created}")


def cmd_confirm(args):
    """确认绿灯结果（让经验库学习正确的匹配）"""
    cfg = load_config()
    api_cfg = cfg["api"]

    task_id = args.task_id

    # 登录
    api = AutoQuotaAPI(api_cfg["base_url"], api_cfg["email"], api_cfg["password"])
    if not api.login():
        sys.exit(1)

    # 获取结果
    results = api.get_results(task_id)
    if not results or "items" not in results:
        print("❌ 无法获取结果")
        return

    items = results["items"]

    # 筛选绿灯项（≥85%置信度，且未审核过的）
    green_ids = [
        item["id"] for item in items
        if item.get("confidence", 0) >= 85
        and item.get("review_status") not in ("confirmed", "corrected")
        and item.get("quotas")  # 有匹配结果
    ]

    # 黄灯也一起确认（≥70%，够靠谱了）
    yellow_ids = [
        item["id"] for item in items
        if 70 <= item.get("confidence", 0) < 85
        and item.get("review_status") not in ("confirmed", "corrected")
        and item.get("quotas")
    ]

    all_ids = green_ids + yellow_ids

    if not all_ids:
        print("📭 没有需要确认的结果")
        return

    print(f"🟢 绿灯 {len(green_ids)} 条 + 🟡 黄灯 {len(yellow_ids)} 条，正在批量确认...")
    result = api.confirm_results(task_id, all_ids)
    if result:
        confirmed = result.get("confirmed", 0)
        print(f"✅ 已确认 {confirmed} 条（绿灯+黄灯），经验库已学习")
    else:
        print("❌ 确认失败")


def cmd_correct(args):
    """纠正单条匹配结果

    用法: python auto_match.py correct <task_id> <result_id> <定额编号> <定额名称> [--note "备注"]
    """
    cfg = load_config()
    api_cfg = cfg["api"]

    # 登录
    api = AutoQuotaAPI(api_cfg["base_url"], api_cfg["email"], api_cfg["password"])
    if not api.login():
        sys.exit(1)

    corrected_quotas = [{
        "quota_id": args.quota_id,
        "name": args.quota_name,
        "unit": args.unit or "",
    }]
    note = args.note or "OpenClaw AI审核纠正"

    print(f"🔧 纠正结果 {args.result_id}")
    print(f"   改为: {args.quota_id} {args.quota_name}")
    result = api.correct_result(args.task_id, args.result_id, corrected_quotas, note)
    if result:
        print(f"✅ 纠正成功，已回流经验库候选层")
    else:
        print("❌ 纠正失败")


# ========== 主入口 ==========

def main():
    parser = argparse.ArgumentParser(description="Auto Quota 自动匹配工具")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # watch 子命令
    watch_parser = subparsers.add_parser("watch", help="持续监控微信文件目录")
    watch_parser.add_argument("--province", help="省份定额库名称")

    # match 子命令
    match_parser = subparsers.add_parser("match", help="单次匹配指定文件")
    match_parser.add_argument("file", help="Excel文件路径")
    match_parser.add_argument("--province", help="省份定额库名称")
    match_parser.add_argument("--mode", choices=["search", "agent"], help="匹配模式")

    # status 子命令
    subparsers.add_parser("status", help="查看最近任务状态")

    # confirm 子命令（确认绿灯结果，让经验库学习）
    confirm_parser = subparsers.add_parser("confirm", help="确认绿灯结果，经验库学习正确匹配")
    confirm_parser.add_argument("task_id", help="任务ID")

    # correct 子命令（纠正单条结果）
    correct_parser = subparsers.add_parser("correct", help="纠正单条匹配结果")
    correct_parser.add_argument("task_id", help="任务ID")
    correct_parser.add_argument("result_id", help="结果ID")
    correct_parser.add_argument("quota_id", help="正确的定额编号，如 C10-1-10")
    correct_parser.add_argument("quota_name", help="正确的定额名称")
    correct_parser.add_argument("--unit", default="", help="计量单位")
    correct_parser.add_argument("--note", default="", help="审核备注")

    args = parser.parse_args()

    if args.command == "watch":
        cmd_watch(args)
    elif args.command == "match":
        cmd_match(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "confirm":
        cmd_confirm(args)
    elif args.command == "correct":
        cmd_correct(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
