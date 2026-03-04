r"""
从懒猫微服网盘拉取造价文件到本地 F:\jarvis

通过 WebDAV 协议远程扫描懒猫网盘，只下载 Excel 和广联达文件，跳过其他格式。
支持增量下载（已下载的文件不重复拉）。

用法:
    python tools/pull_from_lazycat.py                    # 扫描所有文件夹
    python tools/pull_from_lazycat.py --folder "工程造价私活"  # 只扫指定文件夹
    python tools/pull_from_lazycat.py --preview          # 只统计不下载
"""

import os
import sys
import json
import time
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import unquote, quote

import requests
from requests.auth import HTTPBasicAuth

# ============================================================
# 配置
# ============================================================

# 懒猫微服地址
LAZYCAT_HOST = "https://microfeicat2025.heiyu.space"
LAZYCAT_FILE_HOST = "https://file.microfeicat2025.heiyu.space"

# 懒猫登录凭证
LAZYCAT_USER = "cocofee2012"
LAZYCAT_PASS = "COCOfee2012"

# 本地保存目录
LOCAL_OUTPUT = r"F:\jarvis"

# 要下载的文件扩展名
EXCEL_EXTENSIONS = {".xlsx", ".xls", ".xlsm"}
# 广联达等造价软件文件
SOFTWARE_EXTENSIONS = {
    ".gbq6", ".gbq7", ".gbq9",     # 广联达计价
    ".qdg4", ".qdg",               # 清单文件
    ".gczjwj",                     # 广联达XML导出
    ".gcbzj",                      # 广联达标准接口
    ".e2d", ".13jz", ".13jk",     # 新点计价
    ".zjfx",                       # 造价分析
}
TARGET_EXTENSIONS = EXCEL_EXTENSIONS | SOFTWARE_EXTENSIONS

# 跳过的目录（不含造价文件的懒猫系统目录）
SKIP_DIRS = {
    ".media", ".otherAppVar", ".remotefs", ".oneway-sync",
    "lzc-sys-logs", "Music", "LazycatMedia", "Pictures",
    "CyclingResults", "Backtrader", ".shared-center", ".Trash",
    ".snapshot", "AppShareCenter", "Downloads",
}

# 增量记录文件
HISTORY_FILE = Path(__file__).parent / ".pull_history.json"

# DAV命名空间
DAV_NS = {"D": "DAV:"}


# ============================================================
# WebDAV 操作
# ============================================================

class LazyCatWebDAV:
    """懒猫微服 WebDAV 客户端"""

    def __init__(self):
        self.session = requests.Session()
        self.auth = None
        # 禁用SSL警告（懒猫用自签名证书）
        requests.packages.urllib3.disable_warnings()

    def login(self):
        """登录懒猫微服，获取 WebDAV token"""
        # 第1步：登录获取 Cookie（懒猫登录可能返回404但Cookie仍然有效）
        self.session.post(
            f"{LAZYCAT_HOST}/sys/api/login",
            data={"username": LAZYCAT_USER, "password": LAZYCAT_PASS},
            timeout=15, verify=False,
        )
        if "HC-Auth-Token" not in self.session.cookies:
            print("  登录失败：未获取到 HC-Auth-Token Cookie")
            sys.exit(1)

        # 第2步：获取 WebDAV token
        r = self.session.get(
            f"{LAZYCAT_FILE_HOST}/api/davToken",
            timeout=15, verify=False,
        )
        token = r.json().get("token", "")
        if not token:
            print("  获取 davToken 失败")
            sys.exit(1)

        self.auth = HTTPBasicAuth(LAZYCAT_USER, token)
        print(f"  登录成功，davToken: {token}")

    def list_dir(self, path="/"):
        """列出目录内容（WebDAV PROPFIND）

        返回: [(name, href, is_dir, size, mtime), ...]
        """
        # 对中文路径做URL编码（保留/不编码）
        encoded_path = quote(path, safe="/")
        url = f"{LAZYCAT_FILE_HOST}/dav{encoded_path}"
        body = '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:allprop/></d:propfind>'
        r = requests.request(
            "PROPFIND", url, auth=self.auth,
            headers={"Depth": "1"}, data=body.encode("utf-8"),
            timeout=30, verify=False,
        )
        if r.status_code not in (200, 207):
            return []

        results = []
        root = ET.fromstring(r.text)
        # 当前目录的href（用于跳过自身）
        current_prefix = f"/dav{encoded_path}".rstrip("/")

        for resp in root.findall("D:response", DAV_NS):
            href = resp.find("D:href", DAV_NS).text
            # 跳过当前目录自身
            if href.rstrip("/") == current_prefix or unquote(href.rstrip("/")) == unquote(current_prefix):
                continue

            is_dir = resp.find(".//D:collection", DAV_NS) is not None
            name_elem = resp.find(".//D:displayname", DAV_NS)
            name = name_elem.text if name_elem is not None and name_elem.text else ""
            if not name:
                name = unquote(href.rstrip("/").split("/")[-1])

            # 文件大小
            size_elem = resp.find(".//D:getcontentlength", DAV_NS)
            size = int(size_elem.text) if size_elem is not None and size_elem.text else 0

            # 修改时间
            mtime_elem = resp.find(".//D:getlastmodified", DAV_NS)
            mtime = mtime_elem.text if mtime_elem is not None else ""

            results.append((name, href, is_dir, size, mtime))

        return results

    def download_file(self, dav_href, local_path):
        """下载单个文件"""
        url = f"{LAZYCAT_FILE_HOST}{dav_href}"
        r = requests.get(url, auth=self.auth, stream=True, timeout=60, verify=False)
        if r.status_code != 200:
            return False

        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                f.write(chunk)
        return True


# ============================================================
# 递归扫描 + 下载
# ============================================================

def scan_and_download(client, remote_path, local_base, preview=False,
                      history=None, stats=None):
    """递归扫描远程目录，下载目标文件

    参数:
        client: WebDAV客户端
        remote_path: 远程目录路径（如 /工程造价私活/）
        local_base: 本地保存根目录
        preview: 只统计不下载
        history: 已下载文件记录 {远程路径: {size, mtime}}
        stats: 统计计数器
    """
    if history is None:
        history = {}
    if stats is None:
        stats = {"scanned": 0, "target": 0, "downloaded": 0,
                 "skipped": 0, "failed": 0, "total_size": 0}

    items = client.list_dir(remote_path)

    for name, href, is_dir, size, mtime in items:
        if is_dir:
            # 跳过系统目录
            if name in SKIP_DIRS:
                continue
            # 递归进入子目录
            sub_path = unquote(href).replace("/dav", "", 1)
            scan_and_download(client, sub_path, local_base, preview, history, stats)
        else:
            stats["scanned"] += 1
            ext = Path(name).suffix.lower()

            # 只下载目标格式
            if ext not in TARGET_EXTENSIONS:
                continue

            stats["target"] += 1
            stats["total_size"] += size

            # 构建本地路径（保持远程目录结构）
            rel_path = unquote(href).replace("/dav/", "", 1)
            local_path = os.path.join(local_base, rel_path)

            # 增量判断：已下载且大小相同则跳过
            remote_key = unquote(href)
            if remote_key in history:
                old = history[remote_key]
                if old.get("size") == size:
                    stats["skipped"] += 1
                    continue

            if preview:
                size_mb = size / 1024 / 1024
                print(f"  [待下载] {rel_path} ({size_mb:.1f}MB)")
                continue

            # 下载文件
            size_mb = size / 1024 / 1024
            print(f"  下载: {rel_path} ({size_mb:.1f}MB)...", end="", flush=True)
            ok = client.download_file(href, local_path)
            if ok:
                stats["downloaded"] += 1
                history[remote_key] = {"size": size, "mtime": mtime}
                print(" OK")
            else:
                stats["failed"] += 1
                print(" 失败!")

    return stats


# ============================================================
# 增量记录
# ============================================================

def load_history():
    """加载增量下载记录"""
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(history):
    """保存增量下载记录"""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


# ============================================================
# 主函数
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="从懒猫微服网盘拉取造价文件")
    parser.add_argument("--folder", help="只扫描指定文件夹（如'工程造价私活'）")
    parser.add_argument("--preview", action="store_true", help="只统计不下载")
    parser.add_argument("--output", default=LOCAL_OUTPUT, help=f"本地保存目录（默认 {LOCAL_OUTPUT}）")
    parser.add_argument("--full", action="store_true", help="忽略增量记录，全量重下")
    args = parser.parse_args()

    print("=" * 60)
    print("懒猫微服网盘 → 本地造价文件拉取工具")
    print("=" * 60)
    print()

    # 登录
    print("[1/3] 登录懒猫微服...")
    client = LazyCatWebDAV()
    client.login()
    print()

    # 确定要扫描的目录
    print("[2/3] 扫描远程文件...")
    if args.folder:
        # 只扫指定文件夹
        folders = [f"/{args.folder}/"]
        print(f"  指定文件夹: {args.folder}")
    else:
        # 扫描根目录，找出所有非系统文件夹
        root_items = client.list_dir("/")
        folders = []
        for name, href, is_dir, size, mtime in root_items:
            if is_dir and name not in SKIP_DIRS:
                sub_path = unquote(href).replace("/dav", "", 1)
                folders.append(sub_path)
        print(f"  发现 {len(folders)} 个文件夹:")
        for f in folders:
            print(f"    {f}")
    print()

    # 加载增量记录
    history = {} if args.full else load_history()
    if history:
        print(f"  已有增量记录: {len(history)} 个文件")

    # 扫描+下载
    total_stats = {"scanned": 0, "target": 0, "downloaded": 0,
                   "skipped": 0, "failed": 0, "total_size": 0}

    for folder in folders:
        folder_name = folder.strip("/")
        print(f"\n--- 扫描: {folder_name} ---")
        stats = scan_and_download(
            client, folder, args.output,
            preview=args.preview, history=history,
        )
        for k in total_stats:
            total_stats[k] += stats[k]

    # 保存增量记录
    if not args.preview:
        save_history(history)

    # 汇总
    print()
    print("=" * 60)
    print("[3/3] 汇总")
    print("=" * 60)
    total_mb = total_stats["total_size"] / 1024 / 1024
    total_gb = total_mb / 1024
    print(f"  扫描文件数:     {total_stats['scanned']}")
    print(f"  目标文件数:     {total_stats['target']} (Excel + 广联达)")
    print(f"  目标文件大小:   {total_gb:.1f}GB ({total_mb:.0f}MB)")
    if args.preview:
        print(f"  （预览模式，未实际下载）")
    else:
        print(f"  本次下载:       {total_stats['downloaded']}")
        print(f"  跳过(已有):     {total_stats['skipped']}")
        print(f"  失败:           {total_stats['failed']}")
        print(f"  保存到: {args.output}")


if __name__ == "__main__":
    main()
