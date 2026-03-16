"""
Jarvis 任务审核脚本 — 从服务器拉取匹配结果，自动生成诊断报告

用法：
    python tools/jarvis_review_task.py                    # 拉所有已完成任务，出汇总报告
    python tools/jarvis_review_task.py --recent 10        # 只看最近10个任务
    python tools/jarvis_review_task.py --task abc123       # 只看指定任务
    python tools/jarvis_review_task.py --since 2026-03-16  # 只看某天之后的任务

报告输出到 output/temp/jarvis_review_report.md
"""

import os
import sys
import json
import urllib.request
import urllib.error
import ssl
from pathlib import Path
from datetime import datetime, date

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# API配置（和龙虾auto_match.py一样的地址）
API_BASE = "https://autoquota.microfeicat2025.heiyu.space"
API_EMAIL = "41024847@qq.com"
API_PASSWORD = "COCOfee2012"

# SSL（内网环境忽略证书）
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# ========== API客户端 ==========

class API:
    def __init__(self):
        self.token = None

    def _request(self, method, path):
        """发HTTP请求，返回JSON（处理重定向）"""
        url = f"{API_BASE}{path}"
        for _ in range(3):
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            req = urllib.request.Request(url, headers=headers, method=method)
            try:
                resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=120)
                return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 307, 308):
                    url = e.headers.get("Location", url)
                    continue
                print(f"  API错误 {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
                return None
            except Exception as e:
                print(f"  请求失败: {e}")
                return None
        return None

    def _post(self, path, data):
        """发POST请求（处理307重定向）"""
        url = f"{API_BASE}{path}"
        body = json.dumps(data).encode()
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # urllib不自动跟随POST重定向，手动处理
        for _ in range(3):
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=30)
                return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (301, 302, 307, 308):
                    url = e.headers.get("Location", url)
                    continue
                print(f"  POST错误 {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
                return None
            except Exception as e:
                print(f"  POST失败: {e}")
                return None
        return None

    def login(self):
        """登录拿token"""
        result = self._post("/api/auth/login", {"email": API_EMAIL, "password": API_PASSWORD})
        if result and result.get("access_token"):
            self.token = result["access_token"]
            return True
        # 尝试带尾部斜杠
        result = self._post("/api/auth/login/", {"email": API_EMAIL, "password": API_PASSWORD})
        if result and result.get("access_token"):
            self.token = result["access_token"]
            return True
        print("登录失败")
        return False

    def list_tasks(self, page=1, size=50):
        return self._request("GET", f"/api/tasks?page={page}&size={size}")

    def get_results(self, task_id):
        return self._request("GET", f"/api/tasks/{task_id}/results")


# ========== 诊断分类 ==========

def classify_error(item):
    """根据匹配结果自动判断错误类型

    返回: (分类代号, 诊断说明)
    分类: [词]同义词缺口 [档]参数错档 [跨]跨库缺失 [非]非定额项 [脏]脏数据 [冷]偏门项
    """
    bill_name = item.get("bill_name", "") or ""
    bill_desc = item.get("bill_description", "") or ""
    bill_unit = item.get("bill_unit", "") or ""
    confidence = item.get("confidence", 0)
    quotas = item.get("quotas") or []
    quota_name = quotas[0].get("name", "") if quotas else ""

    # [脏] 脏数据：没有清单名称，或明显不是清单条目
    if not bill_name.strip() or len(bill_name.strip()) <= 1:
        return "[脏]", "清单名称为空或过短"

    # [非] 非定额项：单位是"项"且包含改造/费用类关键词
    non_quota_keywords = ["改造费", "补偿费", "协调费", "配合费", "措施费",
                          "安全文明", "赶工费", "奖励", "罚款", "暂估",
                          "暂定", "预留金", "总承包", "管理费", "利润",
                          "规费", "税金", "保险费"]
    if bill_unit == "项" and any(k in bill_name for k in non_quota_keywords):
        return "[非]", f"估算费用项（单位=项），不套定额"

    # [非] 措施项
    measure_keywords = ["脚手架", "模板", "安全文明施工", "夜间施工", "冬雨季施工",
                        "大型机械进出场", "施工排水", "施工降水"]
    if item.get("is_measure_item"):
        return "[非]", "措施项"

    # 无匹配结果
    if not quotas or confidence == 0:
        return "[冷]", "定额库无匹配"

    # [跨] 跨库缺失：拆除类在安装库
    demolish_keywords = ["拆除", "拆卸", "拆装"]
    if any(k in bill_name for k in demolish_keywords):
        if "拆除" not in quota_name:
            return "[跨]", f"拆除类清单匹配到安装定额，需跨库搜建筑装饰"

    # [跨] 电气/弱电在市政库
    elec_keywords = ["电缆", "灯具", "照明", "配电", "开关", "插座", "光纤",
                     "网线", "监控", "门禁", "报警", "探测器", "广播"]
    if any(k in bill_name for k in elec_keywords):
        # 匹配到的不是电气类定额
        if quota_name and not any(k in quota_name for k in ["电缆", "灯", "配电", "开关",
                                                             "线路", "插座", "穿线", "导管",
                                                             "探测", "报警", "广播", "监控"]):
            return "[跨]", f"电气/弱电类匹配到非电气定额"

    # [词] 同义词缺口：名称方向完全不一致
    # 简单判断：清单名和定额名没有共同关键字（超过2个字的词）
    if confidence < 60:
        return "[词]", f"低置信度，可能存在同义词缺口"

    # [档] 参数错档：名称方向对但置信度不高（通常是规格/型号不匹配）
    if 60 <= confidence < 85:
        # 检查是否名称方向大致对（有共同关键词）
        common = set()
        for i in range(len(bill_name) - 1):
            bigram = bill_name[i:i+2]
            if bigram in quota_name:
                common.add(bigram)
        if len(common) >= 2:
            return "[档]", f"名称方向对，可能规格/型号不匹配"
        else:
            return "[词]", f"名称不匹配，可能需要同义词映射"

    return "[档]", f"置信度{confidence}%，需确认参数"


def generate_report(tasks_data):
    """生成诊断报告

    tasks_data: [(task_info, results_data), ...]
    """
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines.append(f"# Jarvis 自动审核报告（{now}）\n")

    # 全局统计
    total_all = 0
    confirmed_all = 0
    pending_all = 0
    corrected_all = 0
    # 错误分类汇总
    error_counts = {"[词]": 0, "[档]": 0, "[跨]": 0, "[非]": 0, "[脏]": 0, "[冷]": 0}
    # 高频缺口（bill_name → 出现次数）
    gap_names = {}

    for task_info, results_data in tasks_data:
        if not results_data or "items" not in results_data:
            continue

        items = results_data["items"]
        summary = results_data.get("summary", {})
        task_name = task_info.get("original_filename", task_info.get("name", "未知"))
        province = task_info.get("province", "未知")
        task_id = task_info.get("id", "?")

        total = len(items)
        total_all += total

        # 分类统计
        confirmed = [i for i in items if i.get("review_status") == "confirmed"]
        corrected = [i for i in items if i.get("review_status") == "corrected"]
        green = [i for i in items if i.get("confidence", 0) >= 85 and i.get("review_status") != "corrected"]
        pending = [i for i in items if i.get("confidence", 0) < 85 and i.get("review_status") not in ("confirmed", "corrected")]

        confirmed_all += len(confirmed)
        corrected_all += len(corrected)
        pending_all += len(pending)

        lines.append(f"## {task_name} | {province}")
        lines.append(f"任务ID: `{task_id}`")
        lines.append(f"总{total}条 | 已确认{len(confirmed)} | 已纠正{len(corrected)} | 待人工{len(pending)}\n")

        # 待人工明细
        if pending:
            lines.append("### 待人工明细\n")
            lines.append("| # | 清单名称 | 特征描述 | 匹配定额 | 置信度 | 分类 | 诊断 |")
            lines.append("|---|---------|---------|---------|--------|------|------|")

            for item in sorted(pending, key=lambda x: x.get("confidence", 0)):
                idx = item.get("index", "?")
                bill_name = (item.get("bill_name", "") or "")[:20]
                bill_desc = (item.get("bill_description", "") or "")[:15]
                conf = item.get("confidence", 0)
                quotas = item.get("quotas") or []
                quota_text = quotas[0].get("name", "无")[:20] if quotas else "无匹配"

                # 分类诊断
                err_code, err_msg = classify_error(item)
                error_counts[err_code] = error_counts.get(err_code, 0) + 1

                # 记录高频缺口
                raw_name = item.get("bill_name", "") or ""
                if err_code in ("[词]", "[跨]", "[冷]"):
                    gap_names[raw_name] = gap_names.get(raw_name, 0) + 1

                lines.append(f"| {idx} | {bill_name} | {bill_desc} | {quota_text} | {conf}% | {err_code} | {err_msg[:25]} |")

            lines.append("")

    # ========== 全局汇总 ==========
    lines.append("---\n")
    lines.append("## 全局汇总\n")
    lines.append(f"- 任务数: {len(tasks_data)}")
    lines.append(f"- 总条数: {total_all}")
    lines.append(f"- 已确认: {confirmed_all}")
    lines.append(f"- 已纠正: {corrected_all}")
    lines.append(f"- 待人工: {pending_all}")
    if total_all > 0:
        lines.append(f"- 确认率: {confirmed_all/total_all*100:.1f}%")
    lines.append("")

    # 错误分类汇总
    lines.append("### 错误分类分布\n")
    lines.append("| 分类 | 数量 | 含义 |")
    lines.append("|------|------|------|")
    labels = {
        "[词]": "同义词缺口", "[档]": "参数错档", "[跨]": "跨库缺失",
        "[非]": "非定额项", "[脏]": "脏数据", "[冷]": "偏门/冷门"
    }
    for code in ["[词]", "[档]", "[跨]", "[非]", "[冷]", "[脏]"]:
        if error_counts.get(code, 0) > 0:
            lines.append(f"| {code} | {error_counts[code]} | {labels[code]} |")
    lines.append("")

    # 高频缺口（按频次排序，前20个）
    if gap_names:
        lines.append("### 高频缺口（可修复，按频次排序）\n")
        sorted_gaps = sorted(gap_names.items(), key=lambda x: -x[1])[:20]
        for i, (name, count) in enumerate(sorted_gaps, 1):
            lines.append(f"{i}. **{name}** × {count}次")
        lines.append("")

    return "\n".join(lines)


# ========== 主流程 ==========

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Jarvis 任务审核 — 从服务器拉结果出诊断报告")
    parser.add_argument("--task", help="指定任务ID")
    parser.add_argument("--recent", type=int, default=20, help="拉最近N个任务（默认20）")
    parser.add_argument("--since", help="只看某天之后的任务，格式YYYY-MM-DD")
    parser.add_argument("--output", help="报告输出路径（默认output/temp/jarvis_review_report.md）")
    args = parser.parse_args()

    # 登录
    api = API()
    print("登录API...")
    if not api.login():
        print("登录失败")
        sys.exit(1)
    print("登录成功\n")

    # 获取任务列表
    tasks_to_review = []

    if args.task:
        # 指定任务
        task_ids = [args.task]
        for tid in task_ids:
            # 先拿任务信息
            task_info = api._request("GET", f"/api/tasks/{tid}")
            if task_info:
                tasks_to_review.append(task_info)
    else:
        # 拉最近N个
        result = api.list_tasks(page=1, size=args.recent)
        if not result or "items" not in result:
            print("无法获取任务列表")
            sys.exit(1)

        for t in result["items"]:
            # 只看已完成的
            if t.get("status") != "completed":
                continue
            # 按日期过滤
            if args.since:
                created = t.get("created_at", "")[:10]
                if created < args.since:
                    continue
            tasks_to_review.append(t)

    if not tasks_to_review:
        print("没有需要审核的任务")
        return

    print(f"共 {len(tasks_to_review)} 个任务待审核\n")

    # 逐个拉取结果
    tasks_data = []
    for i, task_info in enumerate(tasks_to_review):
        tid = task_info["id"]
        name = task_info.get("original_filename", task_info.get("name", "?"))
        province = task_info.get("province", "?")
        print(f"[{i+1}/{len(tasks_to_review)}] 拉取 {name} ({province})...")

        results = api.get_results(tid)
        if results:
            summary = results.get("summary", {})
            total = summary.get("total", 0)
            confirmed = summary.get("confirmed", 0)
            pending = summary.get("pending", 0)
            print(f"  总{total} | 已确认{confirmed} | 待审{pending}")
            tasks_data.append((task_info, results))
        else:
            print(f"  拉取失败，跳过")

    # 生成报告
    print(f"\n生成诊断报告...")
    report = generate_report(tasks_data)

    # 输出
    output_path = args.output or str(PROJECT_ROOT / "output" / "temp" / "jarvis_review_report.md")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\n报告已生成: {output_path}")

    # 同时打印汇总到终端
    # 找全局汇总部分
    summary_start = report.find("## 全局汇总")
    if summary_start >= 0:
        print("\n" + report[summary_start:])


if __name__ == "__main__":
    main()
