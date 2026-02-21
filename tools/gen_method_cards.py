# -*- coding: utf-8 -*-
"""
方法卡片生成工具 — 从经验数据中提炼"选定额方法论"

功能：
1. 从经验库中按"模式键"聚类相似清单（如所有"XX管管道安装DN*丝接"归为一类）
2. 对每类积累够的模式（≥5条样本），调用大模型总结选定额的方法论
3. 生成的方法卡片存入 db/common/method_cards.db
4. 同时导出 knowledge_notes/method_cards.md（可读版本）

使用方法：
    # 从当前经验库生成方法卡片（需要大模型API）
    python tools/gen_method_cards.py

    # 指定省份
    python tools/gen_method_cards.py --province "北京2024"

    # 只分析不调用大模型（看有哪些模式可以提炼）
    python tools/gen_method_cards.py --dry-run

    # 指定最少样本数（默认5）
    python tools/gen_method_cards.py --min-samples 3

    # 增量模式：只生成新增模式的卡片（跳过已有的）
    python tools/gen_method_cards.py --incremental
"""

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from collections import defaultdict

# 把项目根目录加入路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
import config
from src.method_cards import MethodCards
from src.text_parser import normalize_bill_text


def _get_experience_clusters(province: str = None,
                             min_samples: int = 5) -> list[dict]:
    """
    从经验库中按模式键聚类，找出可以提炼方法论的模式

    聚类逻辑：
    1. 从经验库读取所有权威层记录
    2. 对每条记录提取模式键（用 learning_notebook 的 extract_pattern_key）
    3. 按模式键分组，统计每组的样本数
    4. 筛出样本数 >= min_samples 的组

    返回:
        [
            {
                "pattern_key": "管道安装_镀锌钢管_丝接_DN*",
                "specialty": "C10",  # 最常见的专业
                "samples": [  # 该模式下的样本（清单→定额对）
                    {"bill_text": "...", "quota_ids": [...], "quota_names": [...]},
                    ...
                ],
                "sample_count": 15,
            },
            ...
        ]
    """
    from src.experience_db import ExperienceDB
    from src.learning_notebook import extract_pattern_key

    exp_db = ExperienceDB()
    province = province or config.get_current_province()

    # 从经验库读取所有权威层记录
    conn = sqlite3.connect(str(exp_db.db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT bill_text, quota_ids, quota_names, specialty, confidence, source
            FROM experiences
            WHERE layer = 'authority'
            AND province = ?
            AND confidence >= 70
        """, (province,)).fetchall()
    finally:
        conn.close()

    if not rows:
        logger.warning(f"经验库中没有找到省份 '{province}' 的权威层记录")
        return []

    logger.info(f"经验库读取: {len(rows)} 条权威层记录 (省份={province})")

    # 按模式键聚类
    clusters = defaultdict(lambda: {"samples": [], "specialties": []})

    for row in rows:
        row = dict(row)
        bill_text = row.get("bill_text", "")
        if not bill_text:
            continue

        # 从 bill_text 中提取名称和描述（bill_text = "名称 | 描述" 格式）
        parts = bill_text.split("|", 1)
        bill_name = parts[0].strip() if parts else bill_text
        bill_desc = parts[1].strip() if len(parts) > 1 else ""

        pattern_key = extract_pattern_key(bill_name, bill_desc)
        if not pattern_key or len(pattern_key) < 3:
            continue

        # 解析 quota_ids 和 quota_names
        try:
            quota_ids = json.loads(row.get("quota_ids", "[]"))
        except (json.JSONDecodeError, TypeError):
            quota_ids = []
        try:
            quota_names = json.loads(row.get("quota_names", "[]"))
        except (json.JSONDecodeError, TypeError):
            quota_names = []

        if not quota_ids:
            continue

        cluster = clusters[pattern_key]
        cluster["samples"].append({
            "bill_text": bill_text,
            "bill_name": bill_name,
            "bill_desc": bill_desc,
            "quota_ids": quota_ids,
            "quota_names": quota_names,
        })
        if row.get("specialty"):
            cluster["specialties"].append(row["specialty"])

    # 筛出样本够多的模式
    result = []
    for pattern_key, cluster in clusters.items():
        if len(cluster["samples"]) < min_samples:
            continue

        # 取最常见的专业
        spec_counts = defaultdict(int)
        for s in cluster["specialties"]:
            spec_counts[s] += 1
        top_specialty = max(spec_counts, key=spec_counts.get) if spec_counts else ""

        result.append({
            "pattern_key": pattern_key,
            "specialty": top_specialty,
            "samples": cluster["samples"],
            "sample_count": len(cluster["samples"]),
        })

    # 按样本数降序排序
    result.sort(key=lambda x: x["sample_count"], reverse=True)
    logger.info(f"聚类结果: {len(result)} 个模式（样本数≥{min_samples}）")

    return result


def _extract_keywords(pattern_key: str, samples: list[dict]) -> list[str]:
    """
    从模式键和样本中提取关键词，用于方法卡片的快速匹配

    思路：把模式键拆分为词，再从样本的清单名称中找高频词
    """
    keywords = set()

    # 从模式键中提取词
    parts = pattern_key.replace("_", " ").split()
    for p in parts:
        p = p.strip("*")
        if len(p) >= 2 and p not in ("DN", "mm"):
            keywords.add(p)

    # 从样本清单名称中找高频词（出现在50%以上样本中的词）
    word_count = defaultdict(int)
    total = len(samples)
    for s in samples:
        name = s.get("bill_name", "")
        # 简单分词：按非汉字非字母切分
        words = re.findall(r'[\u4e00-\u9fff]{2,}|[A-Za-z]+', name)
        seen = set()
        for w in words:
            if w not in seen and len(w) >= 2:
                word_count[w] += 1
                seen.add(w)

    for word, count in word_count.items():
        if count / total >= 0.5:
            keywords.add(word)

    return sorted(keywords)


def _infer_category(pattern_key: str, samples: list[dict]) -> str:
    """
    从模式键推断类别名称

    例如：
    "管道安装_镀锌钢管_丝接_DN*" → "管道安装"
    "电缆敷设_沿桥架_截面*" → "电缆敷设"
    """
    # 取模式键的第一个有意义的词
    parts = pattern_key.split("_")
    for p in parts:
        p = p.strip("*")
        if len(p) >= 2 and p not in ("DN", "mm", "截面"):
            return p

    # 兜底：用第一个样本的名称前4个字
    if samples:
        name = samples[0].get("bill_name", "")
        return name[:4] if len(name) >= 4 else name

    return pattern_key[:10]


def _build_llm_prompt(cluster: dict) -> str:
    """
    构建给大模型的提示词，让它从样本中总结方法论

    输入一个聚类（模式键+样本列表），输出方法论卡片内容
    """
    pattern_key = cluster["pattern_key"]
    samples = cluster["samples"]
    specialty = cluster.get("specialty", "")

    # 取前15条样本（避免prompt太长）
    sample_lines = []
    for i, s in enumerate(samples[:15], 1):
        quotas_str = ", ".join(
            f"{qid} {qname}" if qname else qid
            for qid, qname in zip(
                s.get("quota_ids", []),
                s.get("quota_names", []) + [""] * 10  # 补齐长度
            )
        )
        sample_lines.append(
            f"  {i}. 清单: {s['bill_text'][:80]}\n"
            f"     定额: {quotas_str}"
        )
    samples_text = "\n".join(sample_lines)

    prompt = f"""你是一位资深的工程造价专家。下面是同一类清单项目的多个历史匹配案例。
请从这些案例中总结出"如何为这类清单选择正确定额"的方法论。

## 模式类别
{pattern_key}
专业: {specialty}
样本数: {len(samples)}

## 历史案例
{samples_text}

## 请输出以下内容（严格按JSON格式）

```json
{{
    "category": "类别名称（如：管道安装、电缆敷设、阀门安装等，2-6个字）",
    "method_text": "选定额方法论（自然语言，分步骤描述如何判断该选哪条定额，重点说明：1.关键判断维度是什么 2.不同情况对应哪个定额系列 3.参数取档规则 4.是否需要关联定额）",
    "common_errors": "常见错误提示（列出2-3个这类清单最容易犯的错误）",
    "keywords": ["关键词1", "关键词2", "关键词3"]
}}
```

注意：
- method_text 要具体实用，像是资深造价师在教新人
- 重点讲"方法"而不是罗列案例
- 如果能发现定额编号的规律（如同系列不同档位），要指出来
- keywords 是用于匹配清单的关键词（2-5个），能让系统判断一条新清单是否属于这个类别"""

    return prompt


def _call_llm(prompt: str) -> str:
    """调用大模型API（复用 agent_matcher 的调用方式）"""
    llm_type = config.DEFAULT_LLM

    if llm_type == "claude":
        return _call_claude(prompt)
    else:
        return _call_openai_compatible(prompt, llm_type)


def _call_openai_compatible(prompt: str, llm_type: str) -> str:
    """调用OpenAI兼容API"""
    from openai import OpenAI

    api_configs = {
        "deepseek": (config.DEEPSEEK_API_KEY, config.DEEPSEEK_BASE_URL, config.DEEPSEEK_MODEL),
        "kimi": (config.KIMI_API_KEY, config.KIMI_BASE_URL, config.KIMI_MODEL),
        "qwen": (config.QWEN_API_KEY, config.QWEN_BASE_URL, config.QWEN_MODEL),
        "openai": (config.OPENAI_API_KEY, config.OPENAI_BASE_URL, config.OPENAI_MODEL),
    }

    api_key, base_url, model = api_configs.get(llm_type, api_configs["deepseek"])
    client = OpenAI(api_key=api_key, base_url=base_url)

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        max_tokens=2000,
        timeout=config.LLM_TIMEOUT,
    )
    return response.choices[0].message.content


def _call_claude(prompt: str) -> str:
    """调用Claude API（支持中转和官方两种模式）"""
    import httpx

    if config.CLAUDE_BASE_URL:
        # 中转模式：用httpx直接请求
        url = f"{config.CLAUDE_BASE_URL.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": config.CLAUDE_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        body = {
            "model": config.CLAUDE_MODEL,
            "max_tokens": 2000,
            "temperature": 0.3,
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = httpx.post(url, headers=headers, json=body, timeout=config.LLM_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"]
    else:
        # 官方SDK模式
        import anthropic
        client = anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)
        message = client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=2000,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text


def _parse_llm_response(response_text: str) -> dict:
    """解析大模型返回的JSON结果"""
    # 尝试提取JSON块
    json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
    if json_match:
        json_text = json_match.group(1)
    else:
        # 直接尝试解析整个文本
        json_text = response_text.strip()

    try:
        result = json.loads(json_text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # 解析失败，尝试宽松提取
    logger.warning(f"大模型返回非标准JSON，尝试宽松解析")
    # 寻找第一个 { 到最后一个 }
    start = response_text.find("{")
    end = response_text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(response_text[start:end + 1])
        except json.JSONDecodeError:
            pass

    logger.error(f"无法解析大模型返回: {response_text[:200]}")
    return {}


def generate_cards(province: str = None, min_samples: int = 5,
                   dry_run: bool = False, incremental: bool = False) -> dict:
    """
    主函数：生成方法卡片

    参数:
        province: 省份
        min_samples: 每类最少样本数
        dry_run: 只分析不调用大模型
        incremental: 增量模式（跳过已有卡片的类别）

    返回:
        {"generated": 新生成数, "updated": 更新数, "skipped": 跳过数, "failed": 失败数}
    """
    province = province or config.get_current_province()

    # 第1步：从经验库聚类
    clusters = _get_experience_clusters(province=province, min_samples=min_samples)

    if not clusters:
        logger.warning("没有找到足够样本的模式，无法生成方法卡片")
        return {"generated": 0, "updated": 0, "skipped": 0, "failed": 0}

    logger.info(f"找到 {len(clusters)} 个可提炼模式:")
    for i, c in enumerate(clusters[:20], 1):
        logger.info(f"  {i}. {c['pattern_key']} ({c['sample_count']}条, 专业:{c.get('specialty', '?')})")

    if dry_run:
        logger.info("--- dry-run模式，不调用大模型 ---")
        # 打印每个聚类的样本明细
        for c in clusters[:10]:
            print(f"\n=== {c['pattern_key']} ({c['sample_count']}条) ===")
            for s in c["samples"][:5]:
                quotas = ", ".join(s.get("quota_ids", []))
                print(f"  {s['bill_text'][:60]} → {quotas}")
        return {"generated": 0, "updated": 0, "skipped": len(clusters), "failed": 0}

    # 第2步：初始化方法卡片DB
    mc = MethodCards()
    existing_cards = mc.get_all_cards()

    # 增量模式：记录已有卡片的类别（跳过）
    existing_categories = set()
    if incremental:
        for card in existing_cards:
            existing_categories.add((card.get("category", ""), card.get("specialty", "")))

    # 第3步：逐个聚类调用大模型生成方法论
    generated = 0
    updated = 0
    skipped = 0
    failed = 0

    for i, cluster in enumerate(clusters, 1):
        pattern_key = cluster["pattern_key"]
        specialty = cluster.get("specialty", "")

        # 预推断类别（用于增量检查）
        pre_category = _infer_category(pattern_key, cluster["samples"])

        # 增量模式：已有同类别+同专业的卡片则跳过
        if incremental and (pre_category, specialty) in existing_categories:
            logger.debug(f"  跳过（已有卡片）: {pre_category} ({specialty})")
            skipped += 1
            continue

        logger.info(f"[{i}/{len(clusters)}] 生成方法卡片: {pattern_key} ({cluster['sample_count']}条)")

        try:
            # 构建prompt并调用大模型
            prompt = _build_llm_prompt(cluster)
            response = _call_llm(prompt)
            result = _parse_llm_response(response)

            if not result or not result.get("method_text"):
                logger.warning(f"  大模型返回无效结果，跳过: {pattern_key}")
                failed += 1
                continue

            # 提取字段
            category = result.get("category", pre_category)
            method_text = result["method_text"]
            common_errors = result.get("common_errors", "")
            llm_keywords = result.get("keywords", [])

            # 合并关键词：大模型返回的 + 从样本中提取的
            auto_keywords = _extract_keywords(pattern_key, cluster["samples"])
            all_keywords = list(set(llm_keywords + auto_keywords))

            # 存入方法卡片
            card_id = mc.add_card(
                category=category,
                specialty=specialty,
                pattern_keys=[pattern_key],
                keywords=all_keywords,
                method_text=method_text,
                common_errors=common_errors,
                sample_count=cluster["sample_count"],
                confirm_rate=1.0,  # 权威层数据，确认率视为100%
                source_province=province,
            )

            if card_id > 0:
                # 检查是新建还是更新
                was_existing = (category, specialty) in existing_categories
                if was_existing:
                    updated += 1
                    logger.info(f"  已更新: {category} (#{card_id})")
                else:
                    generated += 1
                    logger.info(f"  已生成: {category} (#{card_id})")
                    existing_categories.add((category, specialty))

        except Exception as e:
            logger.error(f"  生成失败: {pattern_key}, 错误: {e}")
            failed += 1
            continue

    # 第4步：导出Markdown
    if generated > 0 or updated > 0:
        mc.export_markdown()

    # 打印总结
    stats = mc.get_stats()
    logger.info("=" * 50)
    logger.info("方法卡片生成完成")
    logger.info(f"  本次新增: {generated}张")
    logger.info(f"  本次更新: {updated}张")
    logger.info(f"  跳过: {skipped}个")
    logger.info(f"  失败: {failed}个")
    logger.info(f"  总计: {stats['total_cards']}张方法卡片")
    logger.info(f"  覆盖专业: {', '.join(stats['specialties'])}")
    logger.info("=" * 50)

    return {"generated": generated, "updated": updated, "skipped": skipped, "failed": failed}


def incremental_generate(province: str = None, min_samples: int = 5) -> dict:
    """
    增量生成：只生成新模式的方法卡片（供 import_reference.py 导入后自动调用）

    和 generate_cards(incremental=True) 一样，但接口更简洁。
    """
    return generate_cards(
        province=province,
        min_samples=min_samples,
        dry_run=False,
        incremental=True,
    )


def main():
    parser = argparse.ArgumentParser(
        description="方法卡片生成工具 — 从经验数据中提炼选定额方法论",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 生成方法卡片（需要大模型API）
  python tools/gen_method_cards.py

  # 指定省份
  python tools/gen_method_cards.py --province "北京2024"

  # 只看有哪些模式可以提炼（不调用大模型）
  python tools/gen_method_cards.py --dry-run

  # 增量模式（只生成新增的）
  python tools/gen_method_cards.py --incremental

  # 降低样本门槛（默认5条）
  python tools/gen_method_cards.py --min-samples 3
        """,
    )
    parser.add_argument("--province", default=None, help="省份（默认用当前配置）")
    parser.add_argument("--min-samples", type=int, default=5,
                        help="每类最少样本数（默认5）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只分析不调用大模型")
    parser.add_argument("--incremental", action="store_true",
                        help="增量模式（跳过已有卡片的类别）")

    args = parser.parse_args()

    # 处理省份参数
    province = None
    if args.province:
        try:
            province = config.resolve_province(args.province)
        except ValueError as e:
            logger.error(f"省份解析失败: {e}")
            sys.exit(1)

    result = generate_cards(
        province=province,
        min_samples=args.min_samples,
        dry_run=args.dry_run,
        incremental=args.incremental,
    )

    if result["failed"] > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
