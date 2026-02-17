"""
多Agent纠偏审核器
功能：
1. 当单Agent匹配的置信度低于阈值（<85%）时，启动多Agent审核
2. 三个审核Agent各司其职：
   - 参数审核Agent：检查DN、截面、单位等数值参数是否正确
   - 规则审核Agent：检查是否需要附加试压、冲洗、调试等关联定额
   - 裁判Agent：综合两个审核意见 + 原始匹配结果，做出最终判断
3. 审核结果可能修正原始匹配、调整置信度、补充遗漏的关联定额

设计思想：
- 每个Agent用不同的Prompt，聚焦不同维度
- 比一个Prompt做所有事更可靠（分工明确，减少遗漏）
- 只对低置信度结果触发，高置信度直接通过（节省API费用）
- 随着经验库积累，越来越多结果在经验库直通，触发审核的比例逐渐降低
"""

import json
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
import config


class MultiAgentReview:
    """多Agent纠偏审核器"""

    def __init__(self, llm_type: str = None):
        """
        参数:
            llm_type: 使用哪个大模型，默认用config配置
        """
        self.llm_type = llm_type or config.DEFAULT_LLM
        self._client = None

    @property
    def client(self):
        """延迟初始化API客户端（复用llm_matcher的创建逻辑）"""
        if self._client is None:
            from src.llm_matcher import LLMMatcher
            temp_matcher = LLMMatcher(self.llm_type)
            self._client = temp_matcher.client
            self._call_llm = temp_matcher._call_llm  # 复用调用方法
        return self._client

    def _ensure_client(self):
        """确保客户端已初始化"""
        _ = self.client

    def review(self, bill_item: dict, match_result: dict,
               candidates: list[dict]) -> dict:
        """
        多Agent审核主流程

        触发条件：match_result["confidence"] < MULTI_AGENT_THRESHOLD

        参数:
            bill_item: 清单项目信息 {name, description, unit, quantity}
            match_result: 单Agent匹配结果 {quotas, confidence, explanation, ...}
            candidates: 候选定额列表（参数验证后的完整列表）

        返回:
            审核后的匹配结果（格式与match_result相同，可能修正了定额/置信度）
        """
        self._ensure_client()

        bill_name = bill_item.get("name", "")
        bill_desc = bill_item.get("description", "")
        original_confidence = match_result.get("confidence", 0)

        logger.info(f"多Agent审核启动: '{bill_name}' (原置信度{original_confidence}%)")

        # ===== Agent 1: 参数审核 =====
        param_review = self._param_audit_agent(bill_item, match_result, candidates)

        # ===== Agent 2: 规则审核 =====
        rule_review = self._rule_audit_agent(bill_item, match_result, candidates)

        # ===== Agent 3: 裁判 =====
        final_result = self._judge_agent(
            bill_item, match_result, param_review, rule_review, candidates
        )

        # 标记经过了多Agent审核
        final_result["review_applied"] = True
        final_result["param_review"] = param_review
        final_result["rule_review"] = rule_review

        new_confidence = final_result.get("confidence", 0)
        logger.info(
            f"多Agent审核完成: '{bill_name}' "
            f"置信度 {original_confidence}% → {new_confidence}%"
        )

        return final_result

    def _param_audit_agent(self, bill_item: dict, match_result: dict,
                           candidates: list[dict]) -> dict:
        """
        参数审核Agent

        检查内容：
        1. DN管径是否正确（清单DN和定额DN是否对应）
        2. 截面积是否在正确档位
        3. 单位是否兼容（m vs 10m, m³ vs m² 等）
        4. 容量/重量等数值参数
        5. 材质是否匹配

        返回:
            {
                "issues": ["问题1", "问题2"],
                "suggestion": "建议...",
                "param_ok": True/False
            }
        """
        bill_name = bill_item.get("name", "")
        bill_desc = bill_item.get("description", "")
        bill_unit = bill_item.get("unit", "")

        # 构建当前匹配的定额信息
        current_quotas = match_result.get("quotas", [])
        quota_text = ""
        if current_quotas:
            quota_lines = []
            for q in current_quotas:
                quota_lines.append(f"- {q.get('quota_id', '?')} {q.get('name', '?')} (单位:{q.get('unit', '?')})")
            quota_text = "\n".join(quota_lines)
        else:
            quota_text = "（未匹配到定额）"

        # 构建候选列表（给审核Agent参考，看有没有更好的选择）
        alt_lines = []
        for i, c in enumerate(candidates[:10], start=1):
            param_info = c.get("param_detail", "")
            alt_lines.append(
                f"{i}. [{c['quota_id']}] {c['name']} | 单位:{c.get('unit','?')} | {param_info}"
            )
        alt_text = "\n".join(alt_lines)

        prompt = f"""你是一位严谨的工程造价参数审核员。
请检查以下清单项目和匹配的定额，**只关注数值参数和单位是否正确**。

## 清单项目
- 项目名称：{bill_name}
- 项目特征：{bill_desc}
- 计量单位：{bill_unit}

## 当前匹配的定额
{quota_text}

## 其他候选定额（供参考）
{alt_text}

## 检查要点
1. DN管径：清单的DN值和定额的DN值是否对应？"DN150以内"表示适用于DN≤150
2. 截面积：电缆截面是否在正确档位？
3. 单位：清单单位和定额单位是否兼容？（m和10m需要换算，m³和m²不兼容）
4. 容量/重量：kVA、吨等数值是否匹配？
5. 材质：清单描述的材质和定额的材质是否一致？

## 输出格式（JSON）
```json
{{
    "param_ok": true,
    "issues": ["发现的问题1", "发现的问题2"],
    "better_candidate_index": null,
    "suggestion": "建议说明"
}}
```
如果参数都正确，issues为空数组，param_ok为true。
如果有更合适的候选，给出序号（better_candidate_index）。"""

        try:
            response = self._call_llm(prompt)
            return self._parse_review_json(response, default={"param_ok": True, "issues": [], "suggestion": ""})
        except Exception as e:
            logger.warning(f"参数审核Agent调用失败: {e}")
            return {"param_ok": True, "issues": [], "suggestion": f"审核失败: {e}"}

    def _rule_audit_agent(self, bill_item: dict, match_result: dict,
                          candidates: list[dict]) -> dict:
        """
        规则审核Agent

        检查内容：
        1. 是否需要附加管道试压定额（给排水管道通常需要）
        2. 是否需要附加冲洗消毒定额（给水管道需要）
        3. 是否需要附加系统调试定额（消防/暖通系统需要）
        4. 是否需要附加支架制安定额（管道安装通常需要管卡/支架）
        5. 是否需要附加刷油防腐/保温定额
        6. 定额选择的章节是否合理

        返回:
            {
                "missing_quotas": ["试压", "冲洗"],
                "unnecessary_quotas": [],
                "suggestion": "建议...",
                "rules_ok": True/False
            }
        """
        bill_name = bill_item.get("name", "")
        bill_desc = bill_item.get("description", "")

        current_quotas = match_result.get("quotas", [])
        quota_text = ""
        if current_quotas:
            quota_lines = []
            for q in current_quotas:
                quota_lines.append(f"- {q.get('quota_id', '?')} {q.get('name', '?')}")
            quota_text = "\n".join(quota_lines)
        else:
            quota_text = "（未匹配到定额）"

        # 候选列表（看有没有试压/冲洗等关联定额可以补充）
        alt_lines = []
        for i, c in enumerate(candidates[:15], start=1):
            alt_lines.append(f"{i}. [{c['quota_id']}] {c['name']}")
        alt_text = "\n".join(alt_lines)

        # 如果有规则知识库，从中检索相关规则
        rule_context = self._get_rule_context(bill_name, bill_desc)

        prompt = f"""你是一位经验丰富的工程造价规则审核员。
请检查以下清单项目和匹配的定额，**只关注是否遗漏了必要的关联定额**。

## 清单项目
- 项目名称：{bill_name}
- 项目特征：{bill_desc}

## 当前匹配的定额
{quota_text}

## 其他候选定额（可从中补充关联定额）
{alt_text}
{rule_context}
## 常见规则（安装工程）
1. 管道安装 → 通常需要：管卡/支架安装、水压试验
2. 给水管道 → 还需要：管道冲洗消毒
3. 消防管道 → 还需要：系统调试
4. 暖通管道 → 可能需要：保温
5. 电缆敷设 → 可能需要：电缆头制作、接线
6. 设备安装 → 可能需要：系统调试
7. 刷油防腐 → 金属管道可能需要

## 输出格式（JSON）
```json
{{
    "rules_ok": true,
    "missing_quotas": ["遗漏的关联定额类型1"],
    "missing_candidate_indices": [5, 8],
    "unnecessary_quotas": [],
    "suggestion": "建议说明"
}}
```
如果不需要补充关联定额，missing_quotas为空数组。
如果候选中有合适的关联定额，给出其序号（missing_candidate_indices）。"""

        try:
            response = self._call_llm(prompt)
            return self._parse_review_json(response, default={
                "rules_ok": True, "missing_quotas": [],
                "missing_candidate_indices": [], "suggestion": ""
            })
        except Exception as e:
            logger.warning(f"规则审核Agent调用失败: {e}")
            return {
                "rules_ok": True, "missing_quotas": [],
                "missing_candidate_indices": [], "suggestion": f"审核失败: {e}"
            }

    def _judge_agent(self, bill_item: dict, match_result: dict,
                     param_review: dict, rule_review: dict,
                     candidates: list[dict]) -> dict:
        """
        裁判Agent

        综合参数审核和规则审核的意见，做出最终判断：
        1. 是否需要更换主定额（参数审核建议更好的候选）
        2. 是否需要补充关联定额（规则审核发现遗漏）
        3. 最终置信度

        返回:
            更新后的match_result（格式不变，内容可能修正）
        """
        bill_name = bill_item.get("name", "")
        bill_desc = bill_item.get("description", "")

        current_quotas = match_result.get("quotas", [])
        original_confidence = match_result.get("confidence", 0)
        original_explanation = match_result.get("explanation", "")

        # 构建审核意见摘要
        param_issues = param_review.get("issues", [])
        param_ok = param_review.get("param_ok", True)
        param_suggestion = param_review.get("suggestion", "")
        better_idx = param_review.get("better_candidate_index")

        rule_ok = rule_review.get("rules_ok", True)
        missing_quotas = rule_review.get("missing_quotas", [])
        missing_indices = rule_review.get("missing_candidate_indices", [])
        rule_suggestion = rule_review.get("suggestion", "")

        # 如果两个审核都通过，不需要裁判（直接微调置信度）
        if param_ok and rule_ok and not param_issues and not missing_quotas:
            # 两个审核Agent都确认无问题 → 置信度可以适当提升
            result = dict(match_result)
            result["confidence"] = min(original_confidence + 10, 95)
            result["explanation"] = f"{original_explanation} [多Agent审核通过]"
            return result

        # 有问题需要裁判处理
        quota_text = ""
        if current_quotas:
            for q in current_quotas:
                quota_text += f"- {q.get('quota_id', '?')} {q.get('name', '?')}\n"

        alt_lines = []
        for i, c in enumerate(candidates[:15], start=1):
            alt_lines.append(f"{i}. [{c['quota_id']}] {c['name']} | 单位:{c.get('unit','?')}")
        alt_text = "\n".join(alt_lines)

        prompt = f"""你是最终裁判，综合两位审核员的意见，做出最终匹配决定。

## 清单项目
- 项目名称：{bill_name}
- 项目特征：{bill_desc}

## 当前匹配结果（置信度{original_confidence}%）
{quota_text}
原因：{original_explanation}

## 参数审核意见
- 参数正确：{"是" if param_ok else "否"}
- 发现问题：{'; '.join(param_issues) if param_issues else '无'}
- 建议：{param_suggestion}
- 推荐更换为候选序号：{better_idx if better_idx else '无需更换'}

## 规则审核意见
- 规则合规：{"是" if rule_ok else "否"}
- 遗漏的关联定额：{', '.join(missing_quotas) if missing_quotas else '无'}
- 建议补充候选序号：{missing_indices if missing_indices else '无'}
- 建议：{rule_suggestion}

## 候选定额列表
{alt_text}

## 请做出最终决定
1. 是否更换主定额？如果更换，从候选中选择序号
2. 是否补充关联定额？如果补充，从候选中选择序号
3. 最终置信度(0-100)

## 输出格式（JSON）
```json
{{
    "keep_original": true,
    "new_main_index": null,
    "add_related_indices": [],
    "final_confidence": 75,
    "explanation": "最终判断说明"
}}
```"""

        try:
            response = self._call_llm(prompt)
            judge_data = self._parse_review_json(response, default={
                "keep_original": True, "new_main_index": None,
                "add_related_indices": [], "final_confidence": original_confidence,
                "explanation": original_explanation
            })
        except Exception as e:
            logger.warning(f"裁判Agent调用失败: {e}")
            # 裁判失败，保持原结果
            return match_result

        # 构建最终结果
        result = dict(match_result)

        # 处理裁判决定
        if not judge_data.get("keep_original") and judge_data.get("new_main_index"):
            # 裁判决定更换主定额
            new_idx = judge_data["new_main_index"]
            if 1 <= new_idx <= len(candidates):
                new_main = candidates[new_idx - 1]
                # 替换主定额（quotas列表第一条）
                new_quotas = [{
                    "quota_id": new_main["quota_id"],
                    "name": new_main["name"],
                    "unit": new_main.get("unit", ""),
                    "reason": f"多Agent审核后更换: {judge_data.get('explanation', '')}",
                    "db_id": new_main.get("id"),
                }]
                # 保留原来的关联定额（如果有）
                if len(current_quotas) > 1:
                    new_quotas.extend(current_quotas[1:])
                result["quotas"] = new_quotas

        # 补充关联定额
        add_indices = judge_data.get("add_related_indices", [])
        if add_indices:
            for idx in add_indices:
                if 1 <= idx <= len(candidates):
                    related = candidates[idx - 1]
                    # 检查是否已存在（避免重复添加）
                    existing_ids = {q.get("quota_id") for q in result.get("quotas", [])}
                    if related["quota_id"] not in existing_ids:
                        result["quotas"].append({
                            "quota_id": related["quota_id"],
                            "name": related["name"],
                            "unit": related.get("unit", ""),
                            "reason": "多Agent审核补充的关联定额",
                            "db_id": related.get("id"),
                        })

        # 更新置信度和说明
        result["confidence"] = judge_data.get("final_confidence", original_confidence)
        result["explanation"] = judge_data.get("explanation", original_explanation)

        return result

    def _get_rule_context(self, bill_name: str, bill_desc: str) -> str:
        """
        从定额规则知识库中检索相关规则（如果有的话）

        返回:
            格式化的规则上下文字符串，如果没有规则库则返回空字符串
        """
        try:
            from src.rule_knowledge import RuleKnowledge
            rule_kb = RuleKnowledge()
            rules = rule_kb.search_rules(f"{bill_name} {bill_desc}", top_k=3)
            if rules:
                rule_lines = ["\n## 相关定额规则（来自定额说明）"]
                for r in rules:
                    rule_lines.append(f"- [{r.get('chapter', '')}] {r.get('content', '')[:200]}")
                return "\n".join(rule_lines) + "\n"
        except Exception:
            pass  # 规则知识库尚未建立或加载失败，不影响审核
        return ""

    def _parse_review_json(self, response_text: str, default: dict) -> dict:
        """从大模型回复中解析JSON，失败则返回默认值"""
        text = response_text.strip()

        # 尝试提取JSON
        json_str = None

        if text.startswith("{"):
            json_str = text
        elif "```json" in text:
            start = text.find("```json") + 7
            end = text.find("```", start)
            if end > start:
                json_str = text[start:end].strip()
        elif "```" in text:
            start = text.find("```") + 3
            end = text.find("```", start)
            if end > start:
                extracted = text[start:end].strip()
                if extracted.startswith("{"):
                    json_str = extracted

        if not json_str:
            first = text.find("{")
            last = text.rfind("}")
            if first >= 0 and last > first:
                json_str = text[first:last + 1]

        if json_str:
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        logger.warning(f"多Agent审核JSON解析失败，使用默认值: {response_text[:100]}")
        return default


# 模块级单例
reviewer = MultiAgentReview()
