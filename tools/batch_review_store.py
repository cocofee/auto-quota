# -*- coding: utf-8 -*-
"""
批量审核入库 — 用Opus 4.6审核已有的批量匹配结果，正确的存入经验库

流程：
  已有批量JSON → 规则预过滤（去掉明显垃圾）
               → 凑批次发给Opus 4.6审核（每批10条）
               → 审核通过的存经验库候选层

用法：
    python tools/batch_review_store.py                          # 全部省份，每次500条
    python tools/batch_review_store.py --province 广东          # 只跑广东
    python tools/batch_review_store.py --daily-limit 2000       # 每次2000条
    python tools/batch_review_store.py --status                 # 查看进度
    python tools/batch_review_store.py --reset                  # 重置进度

设计思路：
  - 规则预过滤去掉明显垃圾（无效名称、措施项、类别不匹配等）
  - Opus 4.6 批量审核（每批10条，节省API调用）
  - 通过审核的存候选层，每天跑一次逐步消化
  - 进度持久化，每次接着上次继续
"""

import sys
import os
import json
import time
import argparse
import re
from pathlib import Path
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 进度文件
PROGRESS_FILE = PROJECT_ROOT / "output" / "temp" / "batch_review_progress.json"
# 批量结果目录
RESULTS_DIR = PROJECT_ROOT / "output" / "batch" / "results"
# 最低置信度门槛
MIN_CONFIDENCE = 70
# 每批发给大模型的条数
LLM_BATCH_SIZE = 10


def load_progress() -> dict:
    """加载进度文件"""
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"processed_files": [], "stats": {}, "last_run": None}


def save_progress(progress: dict):
    """保存进度"""
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def show_status():
    """显示当前进度"""
    progress = load_progress()
    processed = len(progress.get("processed_files", []))
    stats = progress.get("stats", {})
    last_run = progress.get("last_run", "从未运行")

    total_files = 0
    for entry in RESULTS_DIR.iterdir():
        if entry.is_dir():
            total_files += len(list(entry.glob("*.json")))

    print(f"批量审核入库进度")
    print(f"=" * 50)
    print(f"  总文件数:     {total_files}")
    print(f"  已处理:       {processed}")
    print(f"  剩余:         {total_files - processed}")
    print(f"  上次运行:     {last_run}")
    print()
    if stats:
        print(f"  累计统计:")
        print(f"    大模型审核通过→入库: {stats.get('stored', 0)}")
        print(f"    大模型审核拒绝:     {stats.get('llm_rejected', 0)}")
        print(f"    规则预过滤淘汰:     {stats.get('rule_rejected', 0)}")
        print(f"    置信度太低跳过:     {stats.get('low_conf', 0)}")
        print(f"    API调用次数:        {stats.get('api_calls', 0)}")
    print()

    prov_stats = progress.get("province_stats", {})
    if prov_stats:
        print(f"  各省入库数:")
        for prov, cnt in sorted(prov_stats.items(), key=lambda x: -x[1]):
            print(f"    {prov:20s} {cnt:>6d}")


# ============================================================
# 规则预过滤（快速去掉明显垃圾，减少API调用）
# ============================================================

def rule_prefilter(item: dict) -> tuple[bool, str]:
    """规则预过滤：去掉明显不靠谱的，减少发给大模型的量

    返回: (是否通过预过滤, 拒绝原因)
    """
    name = str(item.get("name", "")).strip()
    quota_id = str(item.get("matched_quota_id", "")).strip()
    quota_name = str(item.get("matched_quota_name", "")).strip()
    confidence = item.get("confidence", 0)

    # 基本有效性
    if not name or not quota_id or not quota_name:
        return False, "缺字段"
    if len(name) < 2 or name.isdigit():
        return False, "无效名称"
    if confidence < MIN_CONFIDENCE:
        return False, f"conf={confidence}"

    # 措施项关键词（不需要套定额的）
    measure_kws = ["脚手架", "模板", "措施", "组织措施", "安全文明", "临时设施",
                   "夜间施工", "冬雨季", "垂直运输", "超高增加"]
    for kw in measure_kws:
        if kw in name:
            return False, "措施项"

    # 明显类别不匹配（清单名和定额名完全不相关）
    try:
        from src.review_checkers import (
            extract_description_lines, check_category_mismatch, check_measure_item
        )
        desc = str(item.get("description", ""))
        desc_lines = [line.strip() for line in desc.split("\n") if line.strip()]
        if check_measure_item(item, desc_lines):
            return False, "措施项"
        cat_err = check_category_mismatch(item, quota_name, desc_lines)
        if cat_err:
            return False, f"类别不匹配"
    except ImportError:
        pass

    return True, ""


# ============================================================
# 大模型审核（Opus 4.6 批量审核）
# ============================================================

def _call_claude_review(items_batch: list[dict]) -> list[dict]:
    """调用Claude Opus 4.6审核一批匹配结果

    参数:
        items_batch: [{name, description, quota_id, quota_name, confidence}, ...]

    返回:
        [{seq: 1, correct: true/false, reason: "..."}, ...]
    """
    import config
    import httpx

    # 构造审核prompt
    items_text = ""
    for i, item in enumerate(items_batch):
        name = item.get("name", "")
        desc = str(item.get("description", ""))[:100]  # 截断过长描述
        qid = item.get("matched_quota_id", "")
        qname = item.get("matched_quota_name", "")
        items_text += f"{i+1}. 清单: {name}"
        if desc:
            items_text += f" | 描述: {desc}"
        items_text += f"\n   → 定额: [{qid}] {qname}\n"

    prompt = f"""你是工程造价审核专家。请逐条判断以下清单→定额的匹配是否正确。

判断标准：
- 清单描述的工程内容和定额名称是否属于同一类施工作业
- 不要求DN档位完全匹配，只要类别正确就算对
- 管道类：材质和连接方式要大致吻合
- 阀门类：只要是阀门安装就算对，不要求具体阀门类型完全匹配
- 电气类：灯具/开关/配电箱等大类要正确

请用JSON数组回复，每条一个对象：
[{{"seq": 1, "ok": true}}, {{"seq": 2, "ok": false, "reason": "清单是灯具但定额是电缆"}}]

只回复JSON，不要其他文字。

---
{items_text}"""

    # 调用API
    if config.CLAUDE_BASE_URL:
        url = f"{config.CLAUDE_BASE_URL.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": config.CLAUDE_API_KEY,
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        data = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": 2000,
            "temperature": 0.0,
            "messages": [{"role": "user", "content": prompt}],
        }
        client = httpx.Client(timeout=60)
        response = client.post(url, headers=headers, json=data)
        # 429限流重试
        for retry in range(3):
            if response.status_code != 429:
                break
            time.sleep(3 * (retry + 1))
            response = client.post(url, headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        text = result["content"][0]["text"]
    else:
        # 无中转，用SDK
        import anthropic
        client = anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)
        response = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2000,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text

    # 解析JSON回复
    # 提取JSON数组（可能被包裹在```json...```中）
    json_match = re.search(r'\[.*\]', text, re.DOTALL)
    if not json_match:
        return []
    try:
        return json.loads(json_match.group())
    except json.JSONDecodeError:
        return []


# ============================================================
# 核心处理逻辑
# ============================================================

def collect_items_from_file(json_path: str) -> tuple[list[dict], str]:
    """从一个批量结果JSON中收集通过预过滤的条目

    返回: (通过预过滤的条目列表, 省份名)
    """
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], ""

    province = data.get("province", "")
    specialty = data.get("specialty", "")
    results = data.get("results", [])
    passed_items = []

    for item in results:
        if not isinstance(item, dict):
            continue
        ok, reason = rule_prefilter(item)
        if ok:
            item["_province"] = province
            item["_specialty"] = specialty
            passed_items.append(item)

    return passed_items, province


def run_batch(province_filter: str = None, daily_limit: int = 500):
    """批量审核入库主函数"""
    progress = load_progress()
    processed_set = set(progress.get("processed_files", []))
    stats = progress.get("stats", {
        "stored": 0, "llm_rejected": 0, "rule_rejected": 0,
        "low_conf": 0, "api_calls": 0, "invalid": 0
    })
    prov_stats = progress.get("province_stats", {})

    # 导入存储函数
    from tools.jarvis_store import store_one

    # 收集待处理文件
    pending_files = []
    for entry in sorted(RESULTS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        dir_name = entry.name
        if province_filter and province_filter not in dir_name:
            continue
        for json_file in sorted(entry.glob("*.json")):
            rel_path = str(json_file.relative_to(RESULTS_DIR))
            if rel_path not in processed_set:
                pending_files.append((str(json_file), rel_path, dir_name))

    if not pending_files:
        print("没有待处理文件（全部已处理完，用 --reset 重置）")
        return

    print(f"批量审核入库（Opus 4.6审核）")
    print(f"=" * 50)
    print(f"  待处理文件: {len(pending_files)}")
    print(f"  本次上限:   {daily_limit}条（通过预过滤的）")
    print(f"  每批审核:   {LLM_BATCH_SIZE}条")
    if province_filter:
        print(f"  省份过滤:   {province_filter}")
    print()

    # 第一阶段：收集所有通过预过滤的条目
    all_items = []  # [(item_dict, rel_path, dir_name), ...]
    file_item_map = {}  # rel_path → 该文件条目数
    total_rule_rejected = 0
    total_low_conf = 0

    print("第1步：规则预过滤...")
    for json_path, rel_path, dir_name in pending_files:
        if len(all_items) >= daily_limit:
            break

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            processed_set.add(rel_path)
            continue

        province = data.get("province", dir_name)
        specialty = data.get("specialty", "")
        results = data.get("results", [])
        file_passed = 0

        for item in results:
            if not isinstance(item, dict):
                continue
            conf = item.get("confidence", 0)
            if conf < MIN_CONFIDENCE:
                total_low_conf += 1
                continue
            ok, reason = rule_prefilter(item)
            if ok:
                item["_province"] = province
                item["_specialty"] = specialty
                item["_file"] = rel_path
                all_items.append(item)
                file_passed += 1
            else:
                total_rule_rejected += 1

        file_item_map[rel_path] = file_passed
        processed_set.add(rel_path)

        if len(all_items) >= daily_limit:
            break

    stats["rule_rejected"] = stats.get("rule_rejected", 0) + total_rule_rejected
    stats["low_conf"] = stats.get("low_conf", 0) + total_low_conf

    print(f"  通过预过滤: {len(all_items)}条")
    print(f"  规则淘汰:   {total_rule_rejected}条")
    print(f"  低置信度:   {total_low_conf}条")

    if not all_items:
        print("没有通过预过滤的条目")
        progress["processed_files"] = list(processed_set)
        progress["stats"] = stats
        progress["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_progress(progress)
        return

    # 第二阶段：分批发给大模型审核
    print(f"\n第2步：Opus 4.6审核（共{len(all_items)}条，分{(len(all_items)-1)//LLM_BATCH_SIZE+1}批）...")
    start_time = time.time()
    stored_count = 0
    llm_rejected_count = 0

    for batch_start in range(0, len(all_items), LLM_BATCH_SIZE):
        batch = all_items[batch_start:batch_start + LLM_BATCH_SIZE]
        batch_num = batch_start // LLM_BATCH_SIZE + 1

        try:
            review_results = _call_claude_review(batch)
            stats["api_calls"] = stats.get("api_calls", 0) + 1
        except Exception as e:
            print(f"  批次{batch_num} API调用失败: {e}")
            # API失败时跳过这批，不存
            time.sleep(5)
            continue

        # 根据审核结果存入经验库
        ok_set = set()
        for r in review_results:
            if isinstance(r, dict) and r.get("ok"):
                ok_set.add(r.get("seq", 0))

        for i, item in enumerate(batch):
            seq = i + 1
            if seq in ok_set:
                # 审核通过，存入候选层
                try:
                    store_one(
                        name=item.get("name", "").strip(),
                        desc=item.get("description", ""),
                        quota_ids=[item.get("matched_quota_id", "")],
                        quota_names=[item.get("matched_quota_name", "")],
                        specialty=item.get("_specialty", ""),
                        province=item.get("_province", ""),
                        confirmed=False,
                    )
                    stored_count += 1
                    prov = item.get("_province", "未知")
                    prov_stats[prov] = prov_stats.get(prov, 0) + 1
                except Exception:
                    pass
            else:
                llm_rejected_count += 1

        elapsed = time.time() - start_time
        print(f"  批次{batch_num}: 通过{len(ok_set)}/{len(batch)}, "
              f"累计入库{stored_count}, 耗时{elapsed:.0f}s")

        # API限流保护（每批间隔1秒）
        time.sleep(1)

    elapsed = time.time() - start_time

    # 更新统计
    stats["stored"] = stats.get("stored", 0) + stored_count
    stats["llm_rejected"] = stats.get("llm_rejected", 0) + llm_rejected_count

    # 保存进度
    progress["processed_files"] = list(processed_set)
    progress["stats"] = stats
    progress["province_stats"] = prov_stats
    progress["last_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_progress(progress)

    # 打印汇总
    print()
    print(f"本次完成")
    print(f"=" * 50)
    print(f"  大模型审核通过→入库: {stored_count}条")
    print(f"  大模型审核拒绝:     {llm_rejected_count}条")
    print(f"  规则预过滤淘汰:     {total_rule_rejected}条")
    print(f"  API调用次数:        {stats.get('api_calls', 0) - (stats.get('api_calls', 0) - (len(all_items)-1)//LLM_BATCH_SIZE - 1)}次")
    print(f"  耗时:               {elapsed:.1f}s")
    print()
    print(f"累计统计")
    print(f"  入库: {stats['stored']} | 大模型拒绝: {stats['llm_rejected']} | "
          f"规则淘汰: {stats['rule_rejected']} | 低置信度: {stats['low_conf']}")


def main():
    parser = argparse.ArgumentParser(
        description="批量审核入库：Opus 4.6审核已匹配的批量结果，正确的存入经验库"
    )
    parser.add_argument("--province", help="只处理指定省份（模糊匹配）")
    parser.add_argument("--daily-limit", type=int, default=500,
                        help="每次处理上限（默认500条，约50次API调用）")
    parser.add_argument("--status", action="store_true", help="查看当前进度")
    parser.add_argument("--reset", action="store_true", help="重置进度")
    parser.add_argument("--min-confidence", type=int, default=70,
                        help="最低置信度门槛（默认70）")
    parser.add_argument("--batch-size", type=int, default=10,
                        help="每批发给大模型的条数（默认10）")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    if args.reset:
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
            print("进度已重置")
        else:
            print("没有进度文件")
        return

    global MIN_CONFIDENCE, LLM_BATCH_SIZE
    MIN_CONFIDENCE = args.min_confidence
    LLM_BATCH_SIZE = args.batch_size

    run_batch(province_filter=args.province, daily_limit=args.daily_limit)


if __name__ == "__main__":
    main()
