"""
Agent匹配器 - "造价员贾维斯"核心模块
功能：
1. 代码自动执行搜索+参数验证（复用现有流程，不花API钱）
2. 把候选结果喂给大模型，大模型像造价师一样分析判断
3. 每次处理自动记录学习笔记（为后续规则提炼积累数据）
4. 高置信度结果存入经验库候选层

和现有 match_full 模式的区别：
- Prompt更强：造价员角色，包含专业推理指引
- 上下文更丰富：候选+经验库案例+规则说明+整表概览
- 自动学习：记录学习笔记+存经验库，越用越聪明

使用位置：main.py 中 --mode agent 时调用
"""

import json
import time
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
import config
from src.learning_notebook import LearningNotebook, extract_pattern_key


class AgentMatcher:
    """
    造价员贾维斯 - Agent匹配器

    工作方式：
    1. 代码自动跑搜索+参数验证（和现有search模式一样）
    2. 把搜索结果喂给大模型分析（大模型只做判断，不做搜索）
    3. 记录推理过程到学习笔记（为后续进化积累数据）
    """

    def __init__(self, llm_type: str = None, province: str = None):
        """
        参数:
            llm_type: 使用哪个大模型后端
                "claude" → Claude API（推理能力强，开发阶段推荐）
                "deepseek" → DeepSeek API（便宜，生产阶段推荐）
                None → 用 config.py 里的 DEFAULT_LLM 配置
            province: 省份版本（用于Prompt上下文）
        """
        self.llm_type = llm_type or config.DEFAULT_LLM
        self.province = province or config.CURRENT_PROVINCE
        self._client = None
        self.notebook = LearningNotebook()

    @property
    def client(self):
        """延迟初始化API客户端（和 llm_matcher.py 相同的方式）"""
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _create_client(self):
        """根据 llm_type 创建API客户端"""
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
        elif self.llm_type == "qwen":
            from openai import OpenAI
            if not config.QWEN_API_KEY:
                raise ValueError("未配置QWEN_API_KEY，请在.env文件中设置")
            return OpenAI(
                api_key=config.QWEN_API_KEY,
                base_url=config.QWEN_BASE_URL,
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

    def match_single(self, bill_item: dict, candidates: list[dict],
                     reference_cases: list[dict] = None,
                     rules_context: list[dict] = None,
                     overview_context: str = "",
                     search_query: str = "") -> dict:
        """
        Agent匹配单条清单

        参数:
            bill_item: 清单项 {name, description, unit, quantity, specialty, ...}
            candidates: 搜索+参数验证后的候选定额列表
            reference_cases: 经验库中的参考案例
            rules_context: 规则知识库中的相关规则
            overview_context: 整表概览上下文
            search_query: 搜索时使用的query（记录到笔记中）

        返回:
            标准匹配结果字典（和 match_search_only 格式一致）
        """
        start_time = time.time()

        bill_name = bill_item.get("name", "")
        bill_desc = bill_item.get("description", "") or ""
        full_text = f"{bill_name} {bill_desc}".strip()

        # 如果没有候选，直接返回无匹配
        if not candidates:
            return {
                "bill_item": bill_item,
                "quotas": [],
                "confidence": 0,
                "explanation": "搜索无候选结果",
                "match_source": "agent",
                "no_match_reason": "搜索无候选结果",
            }

        # 构建造价员Prompt
        prompt = self._build_agent_prompt(
            bill_item, candidates, reference_cases,
            rules_context, overview_context
        )

        # 调用大模型
        try:
            response_text = self._call_llm(prompt)
        except Exception as e:
            logger.error(f"Agent大模型调用失败: {e}")
            # 降级：直接用参数验证第1名
            return self._fallback_result(bill_item, candidates, str(e))

        # 解析大模型返回
        result = self._parse_response(response_text, bill_item, candidates)

        elapsed = time.time() - start_time

        # 记录学习笔记
        try:
            self.notebook.record_note({
                "bill_text": full_text,
                "bill_name": bill_name,
                "bill_description": bill_desc,
                "bill_unit": bill_item.get("unit", ""),
                "specialty": bill_item.get("specialty", ""),
                "reasoning": result.get("explanation", ""),
                "search_query": search_query,
                "result_quota_ids": [q["quota_id"] for q in result.get("quotas", [])],
                "result_quota_names": [q["name"] for q in result.get("quotas", [])],
                "confidence": result.get("confidence", 0),
                "llm_type": self.llm_type,
                "elapsed_seconds": elapsed,
            })
        except Exception as e:
            logger.warning(f"学习笔记记录失败: {e}")

        return result

    def _build_agent_prompt(self, bill_item: dict, candidates: list[dict],
                            reference_cases: list[dict] = None,
                            rules_context: list[dict] = None,
                            overview_context: str = "") -> str:
        """
        构建造价员Agent的Prompt

        和现有 llm_matcher 的区别：
        - 角色设定更强（像造价员一样分析推理，不是简单选一个）
        - 包含专业推理指引（材质识别、参数取档、关联定额等）
        - 上下文更丰富（整表概览、规则说明）
        """
        bill_name = bill_item.get("name", "")
        bill_desc = bill_item.get("description", "") or ""
        bill_unit = bill_item.get("unit", "")
        bill_qty = bill_item.get("quantity", "")
        specialty = bill_item.get("specialty", "")
        specialty_name = bill_item.get("specialty_name", "")
        params = bill_item.get("params", {})

        # 格式化候选定额列表（最多20条）
        candidate_lines = []
        for i, c in enumerate(candidates[:20], start=1):
            param_info = c.get("param_detail", "")
            param_match = "✓参数匹配" if c.get("param_match", True) else "✗参数不匹配"
            score = c.get("param_score", 0)
            candidate_lines.append(
                f"{i}. [{c['quota_id']}] {c['name']} | 单位:{c.get('unit', '?')} "
                f"| {param_match}({score:.0%}) {param_info}"
            )
        candidates_text = "\n".join(candidate_lines)

        # 格式化参考案例
        cases_text = ""
        if reference_cases:
            case_lines = []
            for i, case in enumerate(reference_cases[:3], start=1):
                bill = case.get("bill", "")
                quotas = case.get("quotas", [])
                quotas_str = ", ".join(quotas) if isinstance(quotas, list) else str(quotas)
                case_lines.append(f"  案例{i}: \"{bill}\" → {quotas_str}")
            cases_text = "\n## 历史参考案例（类似清单的正确匹配）\n" + "\n".join(case_lines)

        # 格式化规则上下文
        rules_text = ""
        if rules_context:
            rule_lines = []
            for r in rules_context[:3]:
                chapter = r.get("chapter", "")
                content = r.get("content", "")[:300]
                rule_lines.append(f"  [{chapter}] {content}")
            rules_text = "\n## 相关定额规则说明\n" + "\n".join(rule_lines)

        # 格式化提取的参数
        params_text = ""
        if params:
            param_parts = []
            if params.get("dn"):
                param_parts.append(f"管径DN{params['dn']}")
            if params.get("cable_section"):
                param_parts.append(f"截面{params['cable_section']}mm²")
            if params.get("material"):
                param_parts.append(f"材质:{params['material']}")
            if params.get("connection"):
                param_parts.append(f"连接:{params['connection']}")
            if params.get("kva"):
                param_parts.append(f"容量{params['kva']}kVA")
            if param_parts:
                params_text = f"\n- 提取参数：{', '.join(param_parts)}"

        # 整表概览上下文
        overview_text = ""
        if overview_context:
            overview_text = f"\n## 整表概览\n{overview_context}"

        prompt = f"""你是一位经验丰富的工程造价师，精通{self.province}版安装工程定额。
请像真正的造价师一样分析这条清单，从候选定额中选出最合适的。

## 清单项目
- 项目名称：{bill_name}
- 特征描述：{bill_desc}
- 计量单位：{bill_unit}
- 工程量：{bill_qty}
- 所属专业：{specialty} {specialty_name}{params_text}
{overview_text}
## 候选定额（代码已搜索并按匹配度排序）
{candidates_text}
{cases_text}{rules_text}

## 分析要求
请按以下步骤思考：
1. **理解清单**：这条清单描述的是什么工作？（管道安装/阀门/设备/线路/...）
2. **识别关键特征**：材质是什么？连接方式？关键参数？
3. **比对候选**：哪条定额的类型、材质、参数最吻合？
4. **参数取档**：数值参数要"向上取档"（如DN32应选DN40以内的定额）
5. **关联定额**：是否需要配套定额？（管道需要管卡/试压，设备需要调试等）

## 注意事项
- "以内"表示≤，如"DN150以内"适用于DN≤150
- 材质必须一致（镀锌钢管≠不锈钢管≠PPR管）
- 连接方式要对应（丝接≠沟槽≠法兰≠卡压）
- 普通套管≠防水套管，橡塑保温≠聚氨酯保温
- 灭火器≠灭火装置，水泵≠水泵接合器
- 关联定额只能是**不同类型**的配套工作（如管道+管卡、设备+调试），不能是同类型的不同规格或不同方式（如不能同时选"沿桥架敷设"和"穿导管敷设"）
- 一条清单只选一条主定额，不确定时选最可能的那一条

## 输出格式
请严格按JSON格式回答：
```json
{{
    "main_quota_index": 1,
    "main_quota_id": "定额编号",
    "main_reason": "选择原因（简要说明为什么这条最合适）",
    "related_quotas": [
        {{"index": 5, "quota_id": "编号", "reason": "需要配套XX定额"}}
    ],
    "confidence": 85,
    "explanation": "整体分析说明"
}}
```"""
        return prompt

    def _call_llm(self, prompt: str) -> str:
        """调用大模型API（复用 llm_matcher 的调用方式）"""
        if self.llm_type == "claude":
            return self._call_claude(prompt)
        else:
            return self._call_openai_compatible(prompt)

    def _call_openai_compatible(self, prompt: str) -> str:
        """调用OpenAI兼容API（DeepSeek/OpenAI/Kimi/Qwen）"""
        model_map = {
            "deepseek": config.DEEPSEEK_MODEL,
            "kimi": config.KIMI_MODEL,
            "qwen": config.QWEN_MODEL,
            "openai": config.OPENAI_MODEL,
        }
        model = model_map.get(self.llm_type, config.DEEPSEEK_MODEL)

        response = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1500,
            timeout=config.LLM_TIMEOUT,
        )
        return response.choices[0].message.content

    def _call_claude(self, prompt: str) -> str:
        """调用Claude API"""
        response = self.client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        return response.content[0].text

    def _parse_response(self, response_text: str, bill_item: dict,
                        candidates: list[dict]) -> dict:
        """
        解析大模型返回的JSON，构建标准匹配结果

        返回格式和 match_search_only 一致，方便下游处理
        """
        # 提取JSON
        json_str = self._extract_json(response_text)
        if not json_str:
            logger.warning(f"Agent无法提取JSON: {response_text[:200]}")
            return self._fallback_result(bill_item, candidates, "回复格式错误")

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"Agent JSON解析失败: {e}")
            return self._fallback_result(bill_item, candidates, f"JSON解析失败: {e}")
        if not isinstance(data, dict):
            logger.warning(f"Agent JSON根节点不是对象: {type(data).__name__}")
            return self._fallback_result(bill_item, candidates, "JSON结构错误")

        # 构建 quotas 列表
        quotas = []
        no_match = self._to_bool(data.get("no_match", False))

        # 主定额
        main_idx = self._to_int(data.get("main_quota_index"))
        main_id = str(data.get("main_quota_id", "")).strip()
        if main_id.lower() in ("none", "null"):
            main_id = ""

        if not no_match:
            if main_idx is not None and 1 <= main_idx <= len(candidates):
                main_c = candidates[main_idx - 1]
                quotas.append({
                    "quota_id": main_c["quota_id"],
                    "name": main_c["name"],
                    "unit": main_c.get("unit", ""),
                    "reason": data.get("main_reason", ""),
                    "db_id": main_c.get("id"),
                })
            elif main_id:
                # 按编号查找（备用）
                for c in candidates:
                    if c["quota_id"] == main_id:
                        quotas.append({
                            "quota_id": c["quota_id"],
                            "name": c["name"],
                            "unit": c.get("unit", ""),
                            "reason": data.get("main_reason", ""),
                            "db_id": c.get("id"),
                        })
                        break

        # 关联定额（过滤同类：关联定额不能和主定额同册同章节）
        main_quota_prefix = ""
        if quotas:
            main_qid = quotas[0].get("quota_id", "")
            parts = main_qid.split("-")
            if len(parts) >= 2:
                main_quota_prefix = f"{parts[0]}-{parts[1]}"

        # 只在“主定额存在”时才接受关联定额，避免无主定额时误入关联项
        if quotas:
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

                rel_c = None
                if rel_idx is not None and 1 <= rel_idx <= len(candidates):
                    rel_c = candidates[rel_idx - 1]
                elif rel_id:
                    for c in candidates:
                        if c["quota_id"] == rel_id:
                            rel_c = c
                            break

                if not rel_c:
                    continue

                # 过滤同类定额：册号+章节相同的不算关联
                rel_qid = rel_c["quota_id"]
                rel_parts = rel_qid.split("-")
                if len(rel_parts) >= 2 and main_quota_prefix:
                    rel_prefix = f"{rel_parts[0]}-{rel_parts[1]}"
                    if rel_prefix == main_quota_prefix:
                        logger.debug(f"过滤同类关联定额: {rel_qid}（与主定额同属{main_quota_prefix}）")
                        continue

                quotas.append({
                    "quota_id": rel_c["quota_id"],
                    "name": rel_c["name"],
                    "unit": rel_c.get("unit", ""),
                    "reason": related.get("reason", ""),
                    "db_id": rel_c.get("id"),
                })

        raw_confidence = data.get("confidence", 0)
        try:
            confidence = int(raw_confidence)
        except (ValueError, TypeError):
            confidence = 0
        confidence = max(0, min(100, confidence))
        explanation = data.get("explanation", "")

        # 备选候选（排除已选的）
        selected_ids = {q["quota_id"] for q in quotas}
        alternatives = []
        for c in candidates:
            if c["quota_id"] not in selected_ids:
                ps = c.get("param_score", 0.5)
                alt_conf = int(ps * 85) if c.get("param_match", True) else max(int(ps * 40), 15)
                alternatives.append({
                    "quota_id": c["quota_id"],
                    "name": c["name"],
                    "unit": c.get("unit", ""),
                    "confidence": alt_conf,
                    "reason": c.get("param_detail", ""),
                })
                if len(alternatives) >= 3:
                    break

        if not quotas:
            confidence = 0

        result = {
            "bill_item": bill_item,
            "quotas": quotas,
            "confidence": confidence,
            "explanation": explanation,
            "candidates_count": len(candidates),
            "match_source": "agent",
            "alternatives": alternatives,
        }

        if not quotas:
            result["no_match_reason"] = data.get("no_match_reason") or "大模型未选中任何定额"

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

    def _fallback_result(self, bill_item: dict, candidates: list[dict],
                         error_msg: str) -> dict:
        """
        降级处理：大模型调用失败时，回退到参数验证第1名

        和 match_search_only 的逻辑一样
        """
        best = None
        confidence = 0

        if candidates:
            matched = [c for c in candidates if c.get("param_match", True)]
            if matched:
                best = matched[0]
                confidence = int(best.get("param_score", 0.5) * 85)
            else:
                best = candidates[0]
                confidence = max(int(best.get("param_score", 0.0) * 40), 15)

        return {
            "bill_item": bill_item,
            "quotas": [{
                "quota_id": best["quota_id"],
                "name": best["name"],
                "unit": best.get("unit", ""),
                "reason": f"Agent降级(搜索模式): {error_msg}",
                "db_id": best.get("id"),
            }] if best else [],
            "confidence": confidence,
            "explanation": f"Agent降级为搜索模式: {error_msg}",
            "match_source": "agent_fallback",
            "candidates_count": len(candidates),
        }

    def _extract_json(self, text: str) -> str | None:
        """从大模型回复中提取JSON字符串（和 llm_matcher 同逻辑）"""
        text = text.strip()

        if text.startswith("{"):
            return text

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

        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            return text[first_brace:last_brace + 1]

        return None
