# -*- coding: utf-8 -*-
"""
经验库Opus审计工具 — 用Claude Opus验证经验库数据质量

流程：
  1. 读取指定省份的权威层经验条目
  2. 每条发给Opus判断"清单→定额"映射是否正确
  3. 结果写入JSON文件供用户查看
  4. 确认后可批量降级错误条目

用法：
  python tools/audit_experience_opus.py --province "重庆" --limit 50   # 小样本测试
  python tools/audit_experience_opus.py --province "重庆"              # 跑一个省
  python tools/audit_experience_opus.py --province "重庆" --apply      # 跑完直接降级错误条目
  python tools/audit_experience_opus.py --review "重庆"                # 查看上次审计结果
"""

import sys
import os
import json
import time
import sqlite3
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from loguru import logger

logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {message}")

# Windows终端编码修复
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 审计结果保存目录
AUDIT_DIR = "output/temp/opus_audit"


def _safe_ascii(val):
    """清洗不可见非ASCII字符"""
    if not val or not isinstance(val, str):
        return val or ""
    return val.strip().encode("ascii", errors="ignore").decode("ascii")


def call_opus(prompt: str, max_retries: int = 3) -> str:
    """调用Claude Opus API（通过中转服务器）"""
    import config

    api_key = _safe_ascii(config.CLAUDE_API_KEY)
    base_url = _safe_ascii(config.CLAUDE_BASE_URL)
    model = _safe_ascii(getattr(config, "CLAUDE_MODEL", "claude-opus-4-6"))

    url = f"{base_url.rstrip('/')}/v1/messages"
    headers = {
        "x-api-key": api_key,
        "content-type": "application/json",
        "anthropic-version": "2023-06-01",
    }
    data = {
        "model": model,
        "max_tokens": 1024,
        "temperature": 0.0,
        "messages": [{"role": "user", "content": prompt}],
    }

    for retry in range(max_retries):
        try:
            resp = httpx.post(url, headers=headers, json=data, timeout=60)
            if resp.status_code == 429:
                wait = 3 * (retry + 1)
                logger.warning(f"429限流，等{wait}秒重试...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            result = resp.json()
            return result["content"][0]["text"]
        except Exception as e:
            if retry < max_retries - 1:
                logger.warning(f"API调用失败({e})，重试{retry+2}/{max_retries}...")
                time.sleep(2)
            else:
                raise
    return ""


def build_audit_prompt(items: list[dict], province: str) -> str:
    """
    构建批量审计prompt。一次发5条，让Opus逐条判断。

    每条包含：清单文本 + 经验库存的定额编号和名称。
    Opus判断这个映射是否合理。
    """
    lines = []
    lines.append(f"你是工程造价专家，精通{province}定额。")
    lines.append("以下是经验库中存储的「清单→定额」映射记录，请逐条判断映射是否合理。")
    lines.append("")
    lines.append("判断标准：")
    lines.append("- 清单描述的工程内容，和定额描述的施工内容，是否属于同一类工作？")
    lines.append("- 关键参数（管径DN、截面积、材质等）是否匹配？")
    lines.append("- 如果清单是A类工作但定额是B类工作，判为错误。")
    lines.append("- 如果大方向对但参数档位选错（如DN25套了DN32的定额），也判为错误。")
    lines.append("- 如果定额库中没有完全对应的子目，用相近子目借用是允许的（如球形喷口借用旋转吹风口、机房空调借用落地式空调等），判为通过。")
    lines.append("- 如果无法判断（信息不足），判为通过。")
    lines.append("")

    for i, item in enumerate(items):
        lines.append(f"--- 第{i+1}条 ---")
        lines.append(f"清单名称：{item['bill_name']}")
        if item.get('bill_text') and item['bill_text'] != item['bill_name']:
            # 只取前200字，避免太长
            desc = item['bill_text'][:200]
            lines.append(f"清单描述：{desc}")
        lines.append(f"定额编号：{', '.join(item['quota_ids'][:3])}")
        lines.append(f"定额名称：{', '.join(item['quota_names'][:3])}")
        lines.append("")

    lines.append(f"请对以上{len(items)}条逐一判断，严格按以下JSON数组格式返回，不要其他文字：")
    lines.append('[')
    lines.append('  {"index": 1, "pass": true/false, "reason": "一句话理由"},')
    lines.append('  {"index": 2, "pass": true/false, "reason": "一句话理由"},')
    lines.append('  ...')
    lines.append(']')

    return "\n".join(lines)


def parse_audit_response(response: str, count: int) -> list[dict]:
    """解析Opus返回的JSON数组"""
    # 提取JSON部分（可能有多余文字）
    text = response.strip()

    # 尝试找JSON数组
    start = text.find('[')
    end = text.rfind(']')
    if start >= 0 and end > start:
        text = text[start:end + 1]

    try:
        results = json.loads(text)
        if isinstance(results, list):
            return results
    except json.JSONDecodeError:
        pass

    # 解析失败，返回全部"无法判断"（保守策略：不误删）
    logger.warning(f"JSON解析失败，{count}条全部标记为pass（保守策略）")
    return [{"index": i + 1, "pass": True, "reason": "JSON解析失败，保守通过"} for i in range(count)]


def load_authority_cards(province_filter: str) -> list[dict]:
    """加载指定省份的权威层经验卡片"""
    import config
    db_path = config.get_experience_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute('''
        SELECT id, bill_text, bill_name, quota_ids, quota_names,
               province, specialty, source, confirm_count
        FROM experiences
        WHERE layer = 'authority' AND province LIKE ?
        ORDER BY id
    ''', (f'%{province_filter}%',)).fetchall()

    cards = []
    for r in rows:
        try:
            quota_ids = json.loads(r['quota_ids']) if r['quota_ids'] else []
            quota_names = json.loads(r['quota_names']) if r['quota_names'] else []
        except json.JSONDecodeError:
            quota_ids, quota_names = [], []

        # 跳过没有定额的空记录
        if not quota_ids:
            continue

        cards.append({
            'id': r['id'],
            'bill_text': r['bill_text'] or '',
            'bill_name': r['bill_name'] or '',
            'quota_ids': quota_ids,
            'quota_names': quota_names,
            'province': r['province'],
            'specialty': r['specialty'] or '',
            'source': r['source'] or '',
        })

    conn.close()
    return cards


def load_existing_results(result_path: str) -> dict:
    """加载已有的审计结果（断点续跑用）"""
    if os.path.exists(result_path):
        with open(result_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_results(result_path: str, data: dict):
    """保存审计结果"""
    with open(result_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_audit(province: str, cards: list[dict], limit: int = None, batch_size: int = 5):
    """
    对一批经验卡片进行Opus审计。

    参数：
        province: 省份名称
        cards: 经验卡片列表
        limit: 最多审计多少条
        batch_size: 每批发几条给Opus（默认5）
    """
    if limit:
        cards = cards[:limit]
    total = len(cards)

    # 结果文件路径
    safe_name = province[:20].replace('(', '').replace(')', '').replace('（', '').replace('）', '')
    result_path = os.path.join(AUDIT_DIR, f"audit_{safe_name}.json")

    # 断点续跑：加载已有结果
    existing = load_existing_results(result_path)
    audited_ids = set()
    if existing.get('items'):
        audited_ids = {item['card_id'] for item in existing['items']}
        logger.info(f"已有{len(audited_ids)}条审计结果，跳过已审计的")

    # 过滤掉已审计的
    remaining = [c for c in cards if c['id'] not in audited_ids]
    if not remaining:
        logger.info("所有条目已审计完毕")
        return existing

    logger.info(f"开始审计 {province}：总{total}条，待审{len(remaining)}条，每批{batch_size}条")

    # 初始化结果
    items = existing.get('items', [])
    pass_count = existing.get('pass', 0)
    fail_count = existing.get('fail', 0)
    error_count = existing.get('error', 0)
    start_time = time.time()

    # 分批调用Opus
    for batch_start in range(0, len(remaining), batch_size):
        batch = remaining[batch_start:batch_start + batch_size]
        batch_num = batch_start // batch_size + 1
        total_batches = (len(remaining) + batch_size - 1) // batch_size

        logger.info(f"批次 {batch_num}/{total_batches}（第{batch_start+1}-{batch_start+len(batch)}条）")

        try:
            prompt = build_audit_prompt(batch, province)
            response = call_opus(prompt)
            verdicts = parse_audit_response(response, len(batch))

            for i, card in enumerate(batch):
                verdict = verdicts[i] if i < len(verdicts) else {"pass": True, "reason": "超出返回范围"}
                is_pass = verdict.get("pass", True)
                reason = verdict.get("reason", "")

                items.append({
                    'card_id': card['id'],
                    'bill_name': card['bill_name'] or card['bill_text'][:30],
                    'quota_ids': card['quota_ids'][:3],
                    'quota_names': card['quota_names'][:3],
                    'pass': is_pass,
                    'reason': reason,
                    'specialty': card['specialty'],
                })

                if is_pass:
                    pass_count += 1
                else:
                    fail_count += 1

        except Exception as e:
            logger.error(f"批次{batch_num}失败: {e}")
            # 失败的标记为error，不判定
            for card in batch:
                items.append({
                    'card_id': card['id'],
                    'bill_name': card['bill_name'] or card['bill_text'][:30],
                    'quota_ids': card['quota_ids'][:3],
                    'quota_names': card['quota_names'][:3],
                    'pass': True,  # 保守：API失败不误删
                    'reason': f'API调用失败: {str(e)[:50]}',
                    'error': True,
                })
                error_count += 1

        # 每批完成后保存（断点续跑）
        result = {
            'province': province,
            'total': total,
            'audited': len(items),
            'pass': pass_count,
            'fail': fail_count,
            'error': error_count,
            'pass_rate': round(pass_count / max(len(items), 1) * 100, 1),
            'elapsed': round(time.time() - start_time, 1),
            'updated_at': datetime.now().isoformat(),
            'items': items,
        }
        save_results(result_path, result)

        # 批间休息1秒，避免限流
        if batch_start + batch_size < len(remaining):
            time.sleep(1)

    elapsed = time.time() - start_time
    result['elapsed'] = round(elapsed, 1)
    save_results(result_path, result)

    return result


def print_report(result: dict):
    """打印审计报告"""
    print(f"\n{'='*60}")
    print(f"Opus审计报告: {result['province']}")
    print(f"{'='*60}")
    print(f"  总条数: {result['total']}")
    print(f"  已审计: {result['audited']}")
    print(f"  通过: {result['pass']} ({result['pass_rate']}%)")
    print(f"  不通过: {result['fail']}")
    if result.get('error'):
        print(f"  API错误: {result['error']}（保守通过）")
    print(f"  耗时: {result['elapsed']}秒")

    # 显示不通过的条目
    fails = [i for i in result['items'] if not i['pass']]
    if fails:
        print(f"\n--- 不通过的条目（共{len(fails)}条）---")
        for item in fails[:20]:  # 最多显示20条
            qids = ', '.join(item['quota_ids'][:2])
            qnames = ', '.join(item['quota_names'][:2])
            print(f"  ID:{item['card_id']} 「{item['bill_name'][:25]}」")
            print(f"    定额: {qids} / {qnames[:30]}")
            print(f"    原因: {item['reason']}")
        if len(fails) > 20:
            print(f"  ... 还有{len(fails)-20}条，详见JSON文件")


def apply_demote(result: dict):
    """将不通过的条目从权威层降级到候选层"""
    import config
    fails = [i for i in result['items'] if not i['pass'] and not i.get('error')]
    if not fails:
        print("没有需要降级的条目")
        return

    fail_ids = [i['card_id'] for i in fails]
    db_path = config.get_experience_db_path()
    conn = sqlite3.connect(db_path)

    # 备份标记
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    note = f"opus_audit_demote_{ts}"

    conn.execute(f'''
        UPDATE experiences
        SET layer = 'candidate',
            notes = COALESCE(notes, '') || ' | {note}'
        WHERE id IN ({','.join('?' * len(fail_ids))})
          AND layer = 'authority'
    ''', fail_ids)

    affected = conn.total_changes
    conn.commit()
    conn.close()

    print(f"\n已降级 {affected} 条到候选层（标记: {note}）")
    print("如需回滚，可搜索notes包含该标记的记录，改回authority")


def review_results(province: str):
    """查看上次审计结果"""
    safe_name = province[:20].replace('(', '').replace(')', '').replace('（', '').replace('）', '')
    result_path = os.path.join(AUDIT_DIR, f"audit_{safe_name}.json")

    if not os.path.exists(result_path):
        print(f"未找到 {province} 的审计结果")
        return

    with open(result_path, 'r', encoding='utf-8') as f:
        result = json.load(f)

    print_report(result)


def main():
    ap = argparse.ArgumentParser(description='用Claude Opus审计经验库数据质量')
    ap.add_argument('--province', required=False, help='省份（模糊匹配）')
    ap.add_argument('--limit', type=int, help='最多审计N条')
    ap.add_argument('--batch-size', type=int, default=5, help='每批几条（默认5）')
    ap.add_argument('--apply', action='store_true', help='审计后直接降级错误条目')
    ap.add_argument('--review', metavar='PROVINCE', help='查看上次审计结果')
    args = ap.parse_args()

    os.makedirs(AUDIT_DIR, exist_ok=True)

    # 查看模式
    if args.review:
        review_results(args.review)
        return

    if not args.province:
        print("请指定省份: --province \"重庆\"")
        return

    # 加载数据
    print(f"加载 {args.province} 权威层经验卡片...")
    cards = load_authority_cards(args.province)
    if not cards:
        print(f"未找到包含'{args.province}'的权威层数据")
        return

    province = cards[0]['province']
    print(f"省份: {province}，权威层共 {len(cards)} 条")

    # 成本估算
    audit_count = min(len(cards), args.limit) if args.limit else len(cards)
    batches = (audit_count + args.batch_size - 1) // args.batch_size
    est_tokens = batches * 3000  # 粗估每批3000 token
    print(f"预计: {audit_count}条 / {batches}批 / ~{est_tokens//1000}K tokens")
    print()

    # 执行审计
    result = run_audit(province, cards, limit=args.limit, batch_size=args.batch_size)
    print_report(result)

    # 降级
    if args.apply:
        apply_demote(result)
    elif result['fail'] > 0:
        print(f"\n有 {result['fail']} 条不通过，加 --apply 参数可降级到候选层")


if __name__ == '__main__':
    main()
