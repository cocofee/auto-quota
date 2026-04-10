 
"""Jarvis 任务审核脚本。"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).parent.parent
API_BASE = "https://autoquota.microfeicat2025.heiyu.space"
API_EMAIL = "41024847@qq.com"
API_PASSWORD = "COCOfee2012"

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

ERROR_LABELS = {
    "[词]": "同义词/术语映射缺口",
    "[档]": "参数或档位错档",
    "[跨]": "跨库/跨专业错投",
    "[非]": "非定额项/措施项",
    "[脏]": "原始数据不完整",
    "[冷]": "无候选或冷门项",
}
NON_QUOTA_KEYWORDS = (
    "改造费", "补偿费", "协调费", "配合费", "措施费", "安全文明", "赶工费", "奖励",
    "罚款", "暂估", "暂定", "预留金", "总承包", "管理费", "利润", "规费", "税金", "保险费",
)
MEASURE_KEYWORDS = (
    "脚手架", "模板", "安全文明施工", "夜间施工", "冬雨季施工", "大型机械进出场", "施工排水", "施工降水",
)
WEAK_ELECTRIC_KEYWORDS = (
    "网线", "网络", "电话", "监控", "门禁", "广播", "报警", "光纤", "插座", "灯", "灯具", "照明",
)
FINISHED_GOODS_KEYWORDS = (
    "洗衣机", "投影仪", "冰箱", "烟机", "净水器", "微波炉", "电磁炉", "热水器", "家电", "成品设备",
)
NON_ELECTRIC_QUOTA_HINTS = ("工业设备", "机械设备", "阀", "盘阀", "风阀", "泵", "建筑", "装饰", "土石方")


class API:
    def __init__(self) -> None:
        self.token: str | None = None

    def _request(self, method: str, path: str) -> dict[str, Any] | None:
        url = f"{API_BASE}{path}"
        for _ in range(3):
            headers: dict[str, str] = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            req = urllib.request.Request(url, headers=headers, method=method)
            try:
                resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=120)
                return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 307, 308):
                    url = exc.headers.get("Location", url)
                    continue
                print(f"  API错误 {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}")
                return None
            except Exception as exc:
                print(f"  请求失败: {exc}")
                return None
        return None

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any] | None:
        url = f"{API_BASE}{path}"
        body = json.dumps(data).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        for _ in range(3):
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                resp = urllib.request.urlopen(req, context=SSL_CTX, timeout=30)
                return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code in (301, 302, 307, 308):
                    url = exc.headers.get("Location", url)
                    continue
                print(f"  POST错误 {exc.code}: {exc.read().decode('utf-8', errors='replace')[:200]}")
                return None
            except Exception as exc:
                print(f"  POST失败: {exc}")
                return None
        return None

    def login(self) -> bool:
        result = self._post("/api/auth/login", {"email": API_EMAIL, "password": API_PASSWORD})
        if result and result.get("access_token"):
            self.token = str(result["access_token"])
            return True
        result = self._post("/api/auth/login/", {"email": API_EMAIL, "password": API_PASSWORD})
        if result and result.get("access_token"):
            self.token = str(result["access_token"])
            return True
        print("登录失败")
        return False

    def list_tasks(self, page: int = 1, size: int = 50) -> dict[str, Any] | None:
        return self._request("GET", f"/api/tasks?page={page}&size={size}")

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        return self._request("GET", f"/api/tasks/{task_id}")

    def get_results(self, task_id: str) -> dict[str, Any] | None:
        return self._request("GET", f"/api/tasks/{task_id}/results")


def s(value: Any) -> str:
    return str(value or "").strip()


def d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def i(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def quota(item: Any) -> dict[str, Any]:
    src = item if isinstance(item, dict) else {}
    return {"quota_id": s(src.get("quota_id") or src.get("code")), "name": s(src.get("name")), "unit": s(src.get("unit"))}


def quotas(value: Any) -> list[dict[str, Any]]:
    return [quota(item) for item in l(value) if isinstance(item, dict)]


def qline(item: dict[str, Any] | None) -> str:
    item = item or {}
    core = " ".join(part for part in (s(item.get("quota_id")), s(item.get("name"))) if part).strip()
    if s(item.get("unit")):
        core = f"{core} [{s(item.get('unit'))}]".strip()
    return core or "-"


def trunc(text: Any, limit: int) -> str:
    value = s(text)
    return value if len(value) <= limit else f"{value[:limit - 1]}…"


def reason_codes(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        code = s(item)
        if code and code not in seen:
            seen.add(code)
            result.append(code)
    return result


def feedback(item: dict[str, Any]) -> dict[str, Any]:
    return d(item.get("human_feedback_payload"))


def absorbable_report(item: dict[str, Any]) -> dict[str, Any]:
    return d(d(item.get("openclaw_review_payload")).get("jarvis_absorbable_report"))


def current_quota(item: dict[str, Any]) -> dict[str, Any]:
    q = quotas(item.get("quotas"))
    if q:
        return q[0]
    return quota(absorbable_report(item).get("jarvis_top1"))


def final_quota(item: dict[str, Any]) -> dict[str, Any]:
    corrected = quotas(item.get("corrected_quotas"))
    if corrected:
        return corrected[0]
    fb = feedback(item)
    if fb and not bool(fb.get("adopt_openclaw", True)):
        finals = fb.get("final_quotas")
        if finals is None and fb.get("final_quota") is not None:
            finals = [fb.get("final_quota")]
        manual = quotas(finals)
        if manual:
            return manual[0]
    report = absorbable_report(item)
    for key in ("final_top1", "openclaw_top1"):
        q = quota(report.get(key))
        if q.get("quota_id") or q.get("name"):
            return q
    suggested = quotas(item.get("openclaw_suggested_quotas"))
    if suggested and s(item.get("openclaw_review_status")) in {"reviewed", "applied"}:
        return suggested[0]
    q = quotas(item.get("quotas"))
    if q and s(item.get("review_status")) == "confirmed":
        return q[0]
    return {}

def decision_status(item: dict[str, Any]) -> str:
    review = s(item.get("review_status"))
    openclaw = s(item.get("openclaw_review_status"))
    if review == "corrected":
        return "corrected_final"
    if review == "confirmed":
        return "confirmed_final"
    if openclaw == "reviewed":
        return "draft_only"
    if openclaw == "rejected":
        return "draft_rejected"
    return "pending"


def decision_source(item: dict[str, Any]) -> str:
    review = s(item.get("review_status"))
    openclaw = s(item.get("openclaw_review_status"))
    fb = feedback(item)
    if review == "corrected":
        if fb and not bool(fb.get("adopt_openclaw", True)):
            return "human_override"
        if openclaw == "applied":
            return "openclaw_applied"
        return "human_corrected"
    if review == "confirmed":
        return "human_confirmed"
    if openclaw == "reviewed":
        return "openclaw_draft"
    if openclaw == "rejected":
        return "openclaw_rejected"
    return "pending"


def note(item: dict[str, Any]) -> str:
    report = absorbable_report(item)
    for value in (
        feedback(item).get("manual_note"),
        item.get("review_note"),
        d(report.get("judgment")).get("basis_summary"),
        item.get("openclaw_review_note"),
        item.get("explanation"),
    ):
        if s(value):
            return s(value)
    return ""


def item_reason_codes(item: dict[str, Any]) -> list[str]:
    fb = feedback(item)
    codes = reason_codes(fb.get("manual_reason_codes")) if fb else []
    if codes:
        return codes
    report = absorbable_report(item)
    decision = d(report.get("decision"))
    return reason_codes(decision.get("manual_reason_codes") or decision.get("reason_codes") or item.get("openclaw_reason_codes"))


def route(item: dict[str, Any]) -> tuple[str, list[str], dict[str, Any], dict[str, Any], list[str]]:
    current = current_quota(item)
    final = final_quota(item)
    codes = item_reason_codes(item)
    final_note = note(item)
    confirmed = decision_status(item) in {"corrected_final", "confirmed_final"} or bool(feedback(item))
    missing: list[str] = []
    if not (final.get("quota_id") or final.get("name")):
        missing.append("final_quota")
    if not codes:
        missing.append("reason_codes")
    if not final_note:
        missing.append("manual_note_or_review_note")
    if not confirmed:
        missing.append("confirmed_final_state")
    if not (current.get("quota_id") or current.get("name")):
        missing.append("current_quota")
    if not missing:
        absorbability = "absorbable"
    elif "final_quota" not in missing and ("reason_codes" not in missing or "manual_note_or_review_note" not in missing):
        absorbability = "partial"
    else:
        absorbability = "not_absorbable"
    targets: list[str] = []
    cur_id = s(current.get("quota_id"))
    fin_id = s(final.get("quota_id"))
    if fin_id or final.get("name"):
        targets.append("ExperienceDB")
    if (cur_id and fin_id and cur_id != fin_id) or (s(current.get("name")) and s(final.get("name")) and current.get("name") != final.get("name")):
        targets.append("audit_errors")
    if codes and final_note:
        targets.append("promotion_queue")
    if not targets:
        targets.append("manual_only")
    return absorbability, targets, current, final, missing


def classify_error(item: dict[str, Any]) -> tuple[str, str]:
    bill_name = s(item.get("bill_name"))
    bill_desc = s(item.get("bill_description"))
    bill_unit = s(item.get("bill_unit"))
    confidence = i(item.get("confidence"))
    quota_name = s(current_quota(item).get("name"))
    full = f"{bill_name} {bill_desc}".strip()
    if not bill_name or len(bill_name) <= 1:
        return "[脏]", "清单名称为空或过短"
    if item.get("is_measure_item") or any(k in full for k in MEASURE_KEYWORDS):
        return "[非]", "措施项不应进入正常定额审核链"
    if bill_unit == "项" and any(k in full for k in NON_QUOTA_KEYWORDS):
        return "[非]", "费用项/总价项，不宜直接套定额"
    if not quota_name or confidence <= 0:
        return "[冷]", "无有效候选，需要补召回或人工定额"
    if any(k in full for k in FINISHED_GOODS_KEYWORDS) and any(k in quota_name for k in NON_ELECTRIC_QUOTA_HINTS):
        return "[跨]", "成品家电/设备被送入工业或阀门类定额"
    if any(k in full for k in WEAK_ELECTRIC_KEYWORDS) and any(k in quota_name for k in ("电话插座", "测试", "工业设备", "阀", "自动空气开关")):
        return "[跨]", "弱电/照明对象被投到错误专业或测试项"
    if "地插" not in full and "地插" in quota_name:
        return "[档]", "墙装插座被判到地插，安装部位错档"
    if "开关盒" in full and "接线箱" in quota_name:
        return "[档]", "盒/箱对象混淆，部位或类别错档"
    if confidence < 60:
        return "[词]", "名称方向不稳，优先补术语映射或召回词"
    if confidence < 85:
        overlap = sum(1 for token in {bill_name[i:i + 2] for i in range(max(len(bill_name) - 1, 0))} if token and token in quota_name)
        if overlap >= 2:
            return "[档]", "方向接近但规格/参数仍需人工复核"
        return "[词]", "名称相似度不足，疑似同义词缺口"
    return "[档]", f"高分但仍需核档，当前置信度 {confidence}%"


def light(item: dict[str, Any]) -> str:
    conf = i(item.get("confidence"))
    return "green" if conf >= 90 else "yellow" if conf >= 70 else "red"


def position(item: dict[str, Any]) -> str:
    parts = []
    if item.get("sheet_name"):
        parts.append(f"sheet={s(item.get('sheet_name'))}")
    if item.get("section"):
        parts.append(f"section={s(item.get('section'))}")
    if item.get("index") not in (None, ""):
        parts.append(f"index={item.get('index')}")
    return ", ".join(parts) or "-"


def source_text(item: dict[str, Any]) -> str:
    return {
        "human_override": "人工终审改判",
        "openclaw_applied": "OpenClaw 草稿已正式采纳",
        "human_corrected": "人工纠正",
        "human_confirmed": "人工确认 Jarvis 原结果",
        "openclaw_draft": "仅有 OpenClaw 草稿",
        "openclaw_rejected": "OpenClaw 草稿已驳回",
        "pending": "待处理",
    }.get(decision_source(item), decision_source(item))


@dataclass
class Stats:
    total: int = 0
    green: int = 0
    yellow: int = 0
    red: int = 0
    confirmed: int = 0
    corrected: int = 0
    pending: int = 0
    openclaw_reviewed: int = 0
    openclaw_applied: int = 0
    absorbable: int = 0
    partial: int = 0
    not_absorbable: int = 0


def collect_stats(items: list[dict[str, Any]]) -> Stats:
    stats = Stats(total=len(items))
    for item in items:
        bucket = light(item)
        if bucket == "green":
            stats.green += 1
        elif bucket == "yellow":
            stats.yellow += 1
        else:
            stats.red += 1
        review = s(item.get("review_status"))
        if review == "confirmed":
            stats.confirmed += 1
        elif review == "corrected":
            stats.corrected += 1
        else:
            stats.pending += 1
        openclaw = s(item.get("openclaw_review_status"))
        if openclaw == "reviewed":
            stats.openclaw_reviewed += 1
        elif openclaw == "applied":
            stats.openclaw_applied += 1
        absorbability, _, _, _, _ = route(item)
        if absorbability == "absorbable":
            stats.absorbable += 1
        elif absorbability == "partial":
            stats.partial += 1
        else:
            stats.not_absorbable += 1
    return stats


def render_task(task_info: dict[str, Any], results_data: dict[str, Any]) -> tuple[str, Counter[str], Counter[str], Stats]:
    task_name = s(task_info.get("original_filename") or task_info.get("name")) or "未知文件"
    task_id = s(task_info.get("id")) or "?"
    province = s(task_info.get("province")) or "未知"
    pricing = s(task_info.get("pricing_name") or task_info.get("quota_book_name") or task_info.get("pricing_book"))
    items = [item for item in l(results_data.get("items")) if isinstance(item, dict)]
    stats = collect_stats(items)
    errors: Counter[str] = Counter()
    gaps: Counter[str] = Counter()
    lines = [
        "JARVIS审核报告 v6.1",
        f"文件: {task_name}",
        f"任务ID: {task_id}",
        f"省份: {province}",
    ]
    if pricing:
        lines.append(f"定额: {pricing}")
    lines += [
        f"处理时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "--------------------------------------------------",
        "",
        "📊 统计总览",
        f"总条数: {stats.total}",
        f"置信度分布: 绿灯(>=90%) {stats.green} 条 | 黄灯(70-89%) {stats.yellow} 条 | 红灯(<70%) {stats.red} 条",
        f"审核状态: 已确认 {stats.confirmed} 条 | 已纠正 {stats.corrected} 条 | 待审核 {stats.pending} 条",
        f"OpenClaw: 已出草稿 {stats.openclaw_reviewed} 条 | 已正式应用 {stats.openclaw_applied} 条",
        f"可吸收性: absorbable {stats.absorbable} 条 | partial {stats.partial} 条 | not_absorbable {stats.not_absorbable} 条",
        "",
        "--------------------------------------------------",
        "",
    ]

    decided = [item for item in items if decision_status(item) in {"corrected_final", "confirmed_final"}]
    if decided:
        lines.append(f"✅ 已形成最终裁决 ({len(decided)} 条)")
        ordered = sorted(decided, key=lambda row: (decision_status(row) != "corrected_final", i(row.get("index"), 0)))
        for idx, item in enumerate(ordered, 1):
            _, targets, current, final, _ = route(item)
            lines.append(
                f"{idx}. {trunc(item.get('bill_name'), 32)} | 最终: {qline(final)} | 当前: {qline(current)} | "
                f"来源: {source_text(item)} | 去向: {'/'.join(targets)} | 原因码: {','.join(item_reason_codes(item)[:4]) or '-'} | "
                f"定位: {position(item)}"
            )
            if note(item):
                lines.append(f"   备注: {trunc(note(item), 48)}")
        lines.append("")

    pending_items = [item for item in items if decision_status(item) not in {"corrected_final", "confirmed_final"}]
    if pending_items:
        lines.append(f"⏳ 待人工复核 ({len(pending_items)} 条)")
        lines.append("| # | 清单 | 当前候选 | 当前状态 | 分类 | 可吸收 | 学习去向 | 缺失字段 | 定位 |")
        lines.append("|---|------|----------|----------|------|--------|----------|----------|------|")
        pending_items = sorted(pending_items, key=lambda row: (0 if decision_status(row) == "draft_only" else 1, i(row.get("confidence"), 0)))
        for item in pending_items:
            code, _ = classify_error(item)
            errors[code] += 1
            if code in {"[词]", "[跨]", "[冷]"}:
                gaps[s(item.get("bill_name"))] += 1
            absorbability, targets, _, final, missing = route(item)
            candidate = qline(current_quota(item))
            if candidate == "-":
                candidate = qline(final)
            lines.append(
                f"| {item.get('index', '?')} | {trunc(item.get('bill_name'), 20)} | {trunc(candidate, 24)} | {decision_status(item)} | "
                f"{code} | {absorbability} | {'/'.join(targets)} | {','.join(missing) or '-'} | {position(item)} |"
            )
        lines.append("")

    if errors:
        lines.append("💡 问题汇总")
        for code in ("[词]", "[跨]", "[档]", "[非]", "[冷]", "[脏]"):
            if errors.get(code):
                lines.append(f"{code} x {errors[code]}: {ERROR_LABELS[code]}")
        if gaps:
            lines.append("")
            lines.append("高频可修复缺口")
            for idx, (name, count) in enumerate(gaps.most_common(10), 1):
                lines.append(f"{idx}. {name} x {count}")
        lines.append("")

    lines.append("⚠️ 系统级观察")
    if stats.green == 0:
        lines.append("- 高分样本为 0，当前任务不适合高比例自动确认。")
    elif stats.green < max(1, stats.total // 5):
        lines.append("- 高分样本占比偏低，主链召回和排序边界需要继续收紧。")
    else:
        lines.append("- 高分样本已有一定基础，但仍要关注低分错召回的污染风险。")
    if sum(1 for item in items if classify_error(item)[0] == "[跨]"):
        lines.append("- 存在明显跨库/跨专业错投，入口分流和候选池边界需要先补。")
    if sum(1 for item in items if decision_status(item) == "draft_only"):
        lines.append("- 当前已有 OpenClaw 草稿，但缺最终确认，报告只能部分吸收，不能直接当终版学习样本。")
    if sum(1 for item in items if position(item) != "-") < stats.total:
        lines.append("- 部分条目缺少 sheet/section/index 定位锚点，人工回看源表仍有额外搜索成本。")
    if any(decision_status(item) in {"corrected_final", "confirmed_final"} and i(item.get("confidence")) < 70 for item in items):
        lines.append("- 存在低置信度但已形成最终裁决的条目，报告中必须显式标注确认来源。")
    lines.append("")

    lines += [
        "下一步建议",
        "1. 先补入口分流和跨库排除，尤其是成品设备/家电/弱电对象。" if errors.get("[跨]") else "1. 先从高频错配簇入手，收紧召回边界而不是继续放宽。",
        "2. 报告输出默认补齐 final_quota、reason_codes、manual_note、confirmed_final_state。",
        "3. 每条记录带 sheet/section/index，减少人工回表成本。",
        "4. 将置信度统计与审核状态统计分开，避免把低分人工确认误读成系统稳定。",
        "5. 对 draft_only 条目保留 OpenClaw 草稿，但不要直接进入 ExperienceDB。",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n", errors, gaps, stats


def generate_report(tasks_data: list[tuple[dict[str, Any], dict[str, Any]]]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    report = [f"# Jarvis 自动审核汇总报告（{now}）", ""]
    all_errors: Counter[str] = Counter()
    all_gaps: Counter[str] = Counter()
    total = Stats()
    rendered: list[str] = []
    for task_info, results_data in tasks_data:
        if not results_data or "items" not in results_data:
            continue
        text, errors, gaps, stats = render_task(task_info, results_data)
        rendered.append(text)
        all_errors.update(errors)
        all_gaps.update(gaps)
        total.total += stats.total
        total.green += stats.green
        total.yellow += stats.yellow
        total.red += stats.red
        total.confirmed += stats.confirmed
        total.corrected += stats.corrected
        total.pending += stats.pending
        total.openclaw_reviewed += stats.openclaw_reviewed
        total.openclaw_applied += stats.openclaw_applied
        total.absorbable += stats.absorbable
        total.partial += stats.partial
        total.not_absorbable += stats.not_absorbable
    if rendered:
        report += [
            "## 全局汇总",
            f"- 任务数: {len(rendered)}",
            f"- 总条数: {total.total}",
            f"- 置信度分布: 绿灯 {total.green} / 黄灯 {total.yellow} / 红灯 {total.red}",
            f"- 审核状态: 已确认 {total.confirmed} / 已纠正 {total.corrected} / 待审核 {total.pending}",
            f"- OpenClaw: 草稿 {total.openclaw_reviewed} / 已应用 {total.openclaw_applied}",
            f"- 可吸收性: absorbable {total.absorbable} / partial {total.partial} / not_absorbable {total.not_absorbable}",
            "",
        ]
    if all_errors:
        report.append("### 全局问题分布")
        for code in ("[词]", "[跨]", "[档]", "[非]", "[冷]", "[脏]"):
            if all_errors.get(code):
                report.append(f"- {code} {all_errors[code]}: {ERROR_LABELS[code]}")
        report.append("")
    if all_gaps:
        report.append("### 全局高频缺口")
        for idx, (name, count) in enumerate(all_gaps.most_common(20), 1):
            report.append(f"{idx}. {name} x {count}")
        report.append("")
    report.extend(rendered)
    return "\n".join(report).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Jarvis 任务审核并生成 v6.1 报告")
    parser.add_argument("--task", help="指定任务ID")
    parser.add_argument("--recent", type=int, default=20, help="拉最近 N 个任务（默认 20）")
    parser.add_argument("--since", help="只看某天之后的任务，格式 YYYY-MM-DD")
    parser.add_argument("--output", help="报告输出路径（默认 output/temp/jarvis_review_report.md）")
    args = parser.parse_args()
    api = API()
    print("登录API...")
    if not api.login():
        print("登录失败")
        sys.exit(1)
    print("登录成功\n")
    tasks_to_review: list[dict[str, Any]] = []
    if args.task:
        task_info = api.get_task(args.task)
        if task_info:
            tasks_to_review.append(task_info)
    else:
        result = api.list_tasks(page=1, size=args.recent)
        if not result or "items" not in result:
            print("无法获取任务列表")
            sys.exit(1)
        for task_info in result["items"]:
            if task_info.get("status") != "completed":
                continue
            created = s(task_info.get("created_at"))[:10]
            if args.since and created and created < args.since:
                continue
            tasks_to_review.append(task_info)
    if not tasks_to_review:
        print("没有需要审核的任务")
        return
    print(f"共 {len(tasks_to_review)} 个任务待审核\n")
    tasks_data: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for idx, task_info in enumerate(tasks_to_review, 1):
        task_id = s(task_info.get("id"))
        name = s(task_info.get("original_filename") or task_info.get("name")) or "?"
        province = s(task_info.get("province")) or "?"
        print(f"[{idx}/{len(tasks_to_review)}] 拉取 {name} ({province})...")
        results = api.get_results(task_id)
        if results:
            summary = d(results.get("summary"))
            print(f"  总{summary.get('total', len(l(results.get('items'))))} | 已确认{summary.get('confirmed', 0)} | 已纠正{summary.get('corrected', 0)} | 待审{summary.get('pending', 0)}")
            tasks_data.append((task_info, results))
        else:
            print("  拉取失败，跳过")
    print("\n生成诊断报告...")
    output_path = Path(args.output) if args.output else PROJECT_ROOT / "output" / "temp" / "jarvis_review_report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report = generate_report(tasks_data)
    output_path.write_text(report, encoding="utf-8")
    print(f"\n报告已生成: {output_path}")
    start = report.find("## 全局汇总")
    if start >= 0:
        print("\n" + report[start:])


if __name__ == "__main__":
    main()
