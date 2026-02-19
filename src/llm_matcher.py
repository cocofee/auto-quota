"""
大模型精选匹配器
功能：
1. 将清单描述 + 候选定额列表 组成Prompt
2. 调用大模型API（支持DeepSeek/Claude/OpenAI）
3. 大模型从候选中精选最佳匹配定额
4. 返回：主定额 + 关联定额（试压/冲洗/调试等） + 置信度

这是匹配流程的核心环节：
- 混合搜索负责"召回"候选（撒大网）
- 参数验证负责"过滤"不匹配的（排除明显错误）
- 大模型负责"精选"最终答案（理解语义做最终判断）
"""

import json
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


class LLMMatcher:
    """大模型精选匹配器"""

    def __init__(self, llm_type: str = None, province: str = None):
        """
        参数:
            llm_type: 使用哪个大模型（"deepseek"/"claude"/"openai"），默认用config配置
            province: 省份版本（用于规则检索与Prompt上下文）
        """
        self.llm_type = llm_type or config.DEFAULT_LLM
        self.province = province or config.CURRENT_PROVINCE
        self._client = None

    @property
    def client(self):
        """延迟初始化API客户端"""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self):
        """根据llm_type创建对应的API客户端"""
        if self.llm_type == "deepseek":
            from openai import OpenAI
            if not config.DEEPSEEK_API_KEY:
                raise ValueError("未配置DEEPSEEK_API_KEY，请在.env文件中设置")
            return OpenAI(
                api_key=config.DEEPSEEK_API_KEY,
                base_url=config.DEEPSEEK_BASE_URL,
            )
        elif self.llm_type == "kimi":
            from openai import OpenAI
            if not config.KIMI_API_KEY:
                raise ValueError("未配置KIMI_API_KEY，请在.env文件中设置")
            return OpenAI(
                api_key=config.KIMI_API_KEY,
                base_url=config.KIMI_BASE_URL,
            )
        elif self.llm_type == "claude":
            import anthropic
            if not config.ANTHROPIC_API_KEY:
                raise ValueError("未配置ANTHROPIC_API_KEY，请在.env文件中设置")
            return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        elif self.llm_type == "openai":
            from openai import OpenAI
            if not config.OPENAI_API_KEY:
                raise ValueError("未配置OPENAI_API_KEY，请在.env文件中设置")
            return OpenAI(api_key=config.OPENAI_API_KEY)
        else:
            raise ValueError(f"不支持的大模型类型: {self.llm_type}")

    def match(self, bill_item: dict, candidates: list[dict],
              reference_cases: list[dict] = None) -> dict:
        """
        调用大模型，从候选中精选最佳匹配定额

        参数:
            bill_item: 清单项目信息
                {name: 项目名称, description: 特征描述, unit: 单位, quantity: 工程量}
            candidates: 候选定额列表（混合搜索+参数验证后的结果）
            reference_cases: 参考案例列表（从经验库中提取的相似历史匹配）

        返回:
            匹配结果字典:
            {
                "quotas": [  # 定额列表（第一条是主定额，后面是关联定额）
                    {quota_id, name, unit, reason},
                    ...
                ],
                "confidence": 0-100,  # 置信度
                "explanation": "...",  # 匹配说明
                "no_match_reason": "..." or None,  # 无匹配原因（如果没找到合适的）
            }
        """
        # 构建Prompt
        prompt = self._build_prompt(bill_item, candidates, reference_cases)

        # 调用大模型
        try:
            response_text = self._call_llm(prompt)
        except Exception as e:
            logger.error(f"大模型调用失败: {e}")
            return {
                "quotas": [],
                "confidence": 0,
                "explanation": f"大模型调用失败: {e}",
                "no_match_reason": str(e),
            }

        # 解析大模型返回的JSON
        result = self._parse_response(response_text, candidates)

        return result

    def _build_prompt(self, bill_item: dict, candidates: list[dict],
                      reference_cases: list[dict] = None) -> str:
        """
        构建发给大模型的Prompt

        Prompt结构：
        1. 系统角色：经验丰富的造价师
        2. 清单信息：名称、特征、单位、工程量
        3. 候选定额列表（编号、名称、单位、参数验证结果）
        4. 参考案例（如果有的话）
        5. 输出要求（JSON格式）
        """
        # 清单信息
        bill_name = bill_item.get("name", "")
        bill_desc = bill_item.get("description", "")
        bill_unit = bill_item.get("unit", "")
        bill_qty = bill_item.get("quantity", "")

        # 候选定额列表
        candidate_lines = []
        for i, c in enumerate(candidates[:20], start=1):  # 最多送20条给大模型
            param_info = c.get("param_detail", "")
            param_match = "参数匹配" if c.get("param_match", True) else "参数不匹配"
            candidate_lines.append(
                f"{i}. [{c['quota_id']}] {c['name']} | 单位:{c.get('unit', '?')} | {param_match} {param_info}"
            )
        candidates_text = "\n".join(candidate_lines)

        # 参考案例（从经验库获取的相似历史匹配）
        cases_text = ""
        if reference_cases:
            case_lines = []
            for i, case in enumerate(reference_cases[:3], start=1):
                bill = case.get("bill", "")
                quotas = case.get("quotas", [])
                quotas_str = ", ".join(quotas) if isinstance(quotas, list) else str(quotas)
                case_lines.append(f"案例{i}: \"{bill}\" → {quotas_str}")
            cases_text = "\n参考案例（类似清单的历史正确匹配）：\n" + "\n".join(case_lines) + "\n"

        # 定额规则上下文（从规则知识库检索相关规则说明）
        rules_text = ""
        try:
            from src.rule_knowledge import RuleKnowledge
            rule_kb = RuleKnowledge(province=self.province)
            if rule_kb.get_stats()["total"] > 0:
                rules = rule_kb.search_rules(
                    f"{bill_name} {bill_desc}", top_k=3, province=self.province)
                if rules:
                    rule_lines = ["\n## 相关定额规则（来自定额说明）"]
                    for r in rules:
                        chapter = r.get("chapter", "")
                        content = r.get("content", "")[:300]
                        rule_lines.append(f"- [{chapter}] {content}")
                    rules_text = "\n".join(rule_lines) + "\n"
        except Exception as e:
            logger.debug(f"规则知识库上下文加载失败，降级继续匹配: {e}")

        prompt = f"""你是一位经验丰富的工程造价师，精通{self.province}版安装工程定额。
你的任务是根据工程量清单项，从候选定额中选择最合适的定额子目。

## 清单项目
- 项目名称：{bill_name}
- 项目特征描述：{bill_desc}
- 计量单位：{bill_unit}
- 工程量：{bill_qty}

## 候选定额列表
{candidates_text}
{cases_text}{rules_text}
## 匹配要求
1. 从候选列表中选择1条最合适的主定额（给出序号和定额编号）
2. 判断是否需要附加关联定额（如管道试压、水冲洗、系统调试等），如需要也从候选中选择
3. 关联定额只能是**不同类型**的配套工作，不能是与主定额同类型的不同规格/不同方式（如不能同时选"沿桥架敷设"和"穿导管敷设"）
4. 一条清单只选一条主定额，不确定时选最可能的那一条
5. 如果候选中没有合适的定额，请说明原因
6. 给出你的置信度(0-100)
7. 注意："以内"表示该定额适用于不超过指定参数的项目（如"DN150以内"适用于DN150及更小的管径）

## 输出格式
请严格按以下JSON格式回答（不要输出其他内容）：
```json
{{
    "main_quota_index": 1,
    "main_quota_id": "C4-1-10",
    "main_reason": "选择该定额的原因",
    "related_quotas": [
        {{"index": 5, "quota_id": "C9-1-1", "reason": "需要附加试压定额"}}
    ],
    "confidence": 85,
    "explanation": "整体匹配说明",
    "no_match": false,
    "no_match_reason": null
}}
```

如果没有合适的匹配：
```json
{{
    "main_quota_index": null,
    "main_quota_id": null,
    "main_reason": null,
    "related_quotas": [],
    "confidence": 0,
    "explanation": "无匹配说明",
    "no_match": true,
    "no_match_reason": "具体原因"
}}
```"""
        return prompt

    def _call_llm(self, prompt: str) -> str:
        """调用大模型API"""
        if self.llm_type == "claude":
            return self._call_claude(prompt)
        else:
            return self._call_openai_compatible(prompt)

    def _call_openai_compatible(self, prompt: str) -> str:
        """调用OpenAI兼容API（DeepSeek/OpenAI/Kimi）"""
        # 根据模型类型选择对应的模型名称
        if self.llm_type == "deepseek":
            model = config.DEEPSEEK_MODEL
        elif self.llm_type == "kimi":
            model = config.KIMI_MODEL
        else:
            model = config.OPENAI_MODEL

        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,  # 低温度，减少随机性，追求准确匹配
            max_tokens=1000,
            timeout=config.LLM_TIMEOUT,
        )

        return response.choices[0].message.content

    def _call_claude(self, prompt: str) -> str:
        """调用Claude API"""
        response = self.client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1000,
            messages=[
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
        )

        return response.content[0].text

    def _parse_response(self, response_text: str, candidates: list[dict]) -> dict:
        """
        解析大模型返回的JSON文本

        大模型可能返回不规范的JSON（多余文字、markdown格式等），
        需要做鲁棒的解析处理。
        """
        # 尝试从response中提取JSON
        json_str = self._extract_json(response_text)

        if not json_str:
            logger.warning(f"无法从大模型回复中提取JSON: {response_text[:200]}")
            return {
                "quotas": [],
                "confidence": 0,
                "explanation": f"无法解析大模型回复",
                "no_match_reason": "回复格式错误",
                "raw_response": response_text,
            }

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {e}, 原文: {json_str[:200]}")
            return {
                "quotas": [],
                "confidence": 0,
                "explanation": "JSON解析失败",
                "no_match_reason": str(e),
                "raw_response": response_text,
            }
        if not isinstance(data, dict):
            logger.warning(f"大模型返回JSON根节点不是对象: {type(data).__name__}")
            return {
                "quotas": [],
                "confidence": 0,
                "explanation": "JSON结构错误",
                "no_match_reason": "回复JSON根节点必须是对象",
                "raw_response": response_text,
            }

        raw_confidence = data.get("confidence", 0)
        try:
            confidence = int(raw_confidence)
        except (ValueError, TypeError):
            confidence = 0
        confidence = max(0, min(100, confidence))

        # 构建标准化的结果（quotas列表：第一条是主定额，后面是关联定额）
        result = {
            "quotas": [],
            "confidence": confidence,
            "explanation": data.get("explanation", ""),
            "no_match_reason": data.get("no_match_reason"),
            "raw_response": response_text,
        }

        no_match = self._to_bool(data.get("no_match", False))

        # 提取主定额，加入quotas列表
        if not no_match:
            main_idx = self._to_int(data.get("main_quota_index"))
            main_id = str(data.get("main_quota_id", "")).strip()
            if main_id.lower() in ("none", "null"):
                main_id = ""

            if main_idx is not None and 1 <= main_idx <= len(candidates):
                main_candidate = candidates[main_idx - 1]
                result["quotas"].append({
                    "quota_id": main_candidate["quota_id"],
                    "name": main_candidate["name"],
                    "unit": main_candidate.get("unit", ""),
                    "reason": data.get("main_reason", ""),
                    "db_id": main_candidate.get("id"),
                })
            elif main_id:
                # 按编号查找（备用）
                for c in candidates:
                    if c["quota_id"] == main_id:
                        result["quotas"].append({
                            "quota_id": c["quota_id"],
                            "name": c["name"],
                            "unit": c.get("unit", ""),
                            "reason": data.get("main_reason", ""),
                            "db_id": c.get("id"),
                        })
                        break

        # 提取关联定额，也加入quotas列表
        # 只在“主定额存在”时才接受关联定额，避免出现“无主定额但有关联定额”的异常结果
        if result["quotas"]:
            main_quota_prefix = ""  # 主定额的册号+章节前缀（如"C4-8"）
            main_qid = result["quotas"][0].get("quota_id", "")
            # 提取前缀：C4-8-25 → "C4-8"（取到第二个"-"之前）
            parts = main_qid.split("-")
            if len(parts) >= 2:
                main_quota_prefix = f"{parts[0]}-{parts[1]}"

            related_quotas = data.get("related_quotas", [])
            if not isinstance(related_quotas, list):
                related_quotas = []

            for related in related_quotas:
                if not isinstance(related, dict):
                    continue
                rel_idx = self._to_int(related.get("index"))
                rel_id = str(related.get("quota_id", "")).strip()
                if rel_id.lower() in ("none", "null"):
                    rel_id = ""

                # 确定关联定额候选
                rel_candidate = None
                if rel_idx is not None and 1 <= rel_idx <= len(candidates):
                    rel_candidate = candidates[rel_idx - 1]
                elif rel_id:
                    for c in candidates:
                        if c["quota_id"] == rel_id:
                            rel_candidate = c
                            break

                if not rel_candidate:
                    continue

                # 过滤同类定额：册号+章节相同的不算关联（如C4-8-25和C4-8-45都是电缆敷设）
                rel_qid = rel_candidate["quota_id"]
                rel_parts = rel_qid.split("-")
                if len(rel_parts) >= 2 and main_quota_prefix:
                    rel_prefix = f"{rel_parts[0]}-{rel_parts[1]}"
                    if rel_prefix == main_quota_prefix:
                        logger.debug(f"过滤同类关联定额: {rel_qid}（与主定额{main_qid}同属{main_quota_prefix}）")
                        continue

                result["quotas"].append({
                    "quota_id": rel_candidate["quota_id"],
                    "name": rel_candidate["name"],
                    "unit": rel_candidate.get("unit", ""),
                    "reason": related.get("reason", ""),
                    "db_id": rel_candidate.get("id"),
                })

        # 防止“高分但无定额”：无主定额/无有效定额时强制降为0分
        if not result["quotas"]:
            result["confidence"] = 0
            if not result.get("no_match_reason"):
                result["no_match_reason"] = "大模型未选中有效主定额"

        return result

    @staticmethod
    def _to_int(value):
        """把大模型返回的索引值安全转为int，失败返回None。"""
        try:
            if value is None:
                return None
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_bool(value) -> bool:
        """兼容 bool/数字/字符串 的布尔语义。"""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            v = value.strip().lower()
            if v in {"true", "1", "yes", "y", "是"}:
                return True
            if v in {"false", "0", "no", "n", "否", ""}:
                return False
        return bool(value)

    def _extract_json(self, text: str) -> str | None:
        """
        从大模型回复中提取JSON字符串

        大模型可能返回：
        1. 纯JSON
        2. ```json ... ``` 包裹的JSON
        3. 带前后说明文字的JSON
        """
        text = text.strip()

        # 情况1：直接以{开头
        if text.startswith("{"):
            return text

        # 情况2：markdown代码块
        if "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                return text[start:end].strip()

        if "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            if end > start:
                extracted = text[start:end].strip()
                if extracted.startswith("{"):
                    return extracted

        # 情况3：找第一个{到最后一个}
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            return text[first_brace:last_brace + 1]

        return None


# ================================================================
# 完整匹配流程：搜索 → 参数验证 → 大模型精选
# ================================================================

def match_single_item(bill_item: dict, searcher=None, validator=None,
                      matcher=None) -> dict:
    """
    匹配单条清单项目的完整流程

    参数:
        bill_item: 清单项目 {name, description, unit, quantity}
        searcher: HybridSearcher实例（如果没有会自动创建）
        validator: ParamValidator实例
        matcher: LLMMatcher实例

    返回:
        完整的匹配结果
    """
    from src.hybrid_searcher import HybridSearcher
    from src.param_validator import ParamValidator

    if searcher is None:
        searcher = HybridSearcher()
    if validator is None:
        validator = ParamValidator()
    if matcher is None:
        matcher = LLMMatcher()

    # 构建搜索文本
    query = f"{bill_item.get('name', '')} {bill_item.get('description', '')}".strip()

    # 第1步：混合搜索召回候选
    candidates = searcher.search(query, top_k=config.HYBRID_TOP_K)
    logger.info(f"混合搜索召回 {len(candidates)} 条候选")

    if not candidates:
        return {
            "bill_item": bill_item,
            "quotas": [],
            "confidence": 0,
            "explanation": "搜索无结果",
            "candidates_count": 0,
        }

    # 第2步：参数验证，过滤和重排序
    candidates = validator.validate_candidates(query, candidates)
    logger.info(f"参数验证后，匹配的候选: {sum(1 for c in candidates if c.get('param_match', True))} 条")

    # 第3步：大模型精选
    result = matcher.match(bill_item, candidates)
    result["bill_item"] = bill_item
    result["candidates_count"] = len(candidates)

    return result


# ================================================================
# 命令行入口：测试完整匹配流程
# ================================================================

if __name__ == "__main__":
    # 测试时只用BM25搜索+参数验证（不调大模型API，避免费用）
    from src.hybrid_searcher import HybridSearcher
    from src.param_validator import ParamValidator

    searcher = HybridSearcher()
    validator = ParamValidator()

    test_items = [
        {"name": "镀锌钢管管道安装", "description": "DN150 沟槽连接", "unit": "m", "quantity": 100},
        {"name": "干式变压器安装", "description": "800kVA", "unit": "台", "quantity": 1},
        {"name": "电力电缆敷设", "description": "YJV-4×185+1×95 沿桥架敷设", "unit": "m", "quantity": 200},
    ]

    for item in test_items:
        query = f"{item['name']} {item['description']}"
        candidates = searcher.search(query, top_k=10)
        validated = validator.validate_candidates(query, candidates)

        logger.info(f"\n清单: {item['name']} {item['description']}")
        logger.info(f"  候选 {len(candidates)} 条，参数匹配 {sum(1 for c in validated if c.get('param_match'))} 条")
        for c in validated[:5]:
            match_str = "匹配" if c.get("param_match") else "不匹配"
            logger.info(
                f"  [{c['hybrid_score']:.6f}|参数{c['param_score']:.2f}] "
                f"{c['quota_id']} | {c['name'][:50]} | {match_str} {c.get('param_detail', '')}"
            )
