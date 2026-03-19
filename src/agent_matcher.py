"""
Agent匹配器 - "造价员贾维斯"核心模块
功能：
1. 代码自动执行搜索+参数验证（复用现有流程，不花API钱）
2. 把候选结果喂给大模型，大模型像造价师一样分析判断
3. 每次处理自动记录学习笔记（为后续规则提炼积累数据）
4. 匹配结果不自动写经验库（需人工审核修正后通过导入修正.bat导入）

和现有 match_full 模式的区别：
- Prompt更强：造价员角色，包含专业推理指引
- 上下文更丰富：候选+经验库案例+规则说明+整表概览
- 学习笔记：记录匹配推理过程，为后续规则提炼积累数据

使用位置：main.py 中 --mode agent 时调用
"""

import json
import threading
import time

from loguru import logger

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

    _LLM_CIRCUIT_THRESHOLD = 5
    _LLM_COOLDOWN_SEC = 60

    def _ensure_client_lock(self):
        lock = getattr(self, "_client_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._client_lock = lock
        return lock

    def _ensure_circuit_lock(self):
        lock = getattr(self, "_circuit_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._circuit_lock = lock
        return lock

    def is_circuit_open(self) -> bool:
        with self._ensure_circuit_lock():
            return bool(self._llm_circuit_open)

    def reset_circuit_breaker(self):
        with self._ensure_circuit_lock():
            self._llm_consecutive_fails = 0
            self._llm_circuit_open = False
            self._llm_circuit_open_time = 0.0

    def _check_half_open(self) -> bool:
        with self._ensure_circuit_lock():
            if not self._llm_circuit_open:
                return False
            elapsed = time.time() - self._llm_circuit_open_time
        return elapsed >= self._LLM_COOLDOWN_SEC

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
        self.province = province or config.get_current_province()
        self._client = None
        self.notebook = LearningNotebook()
        self._llm_consecutive_fails = 0
        self._llm_circuit_open = False
        self._llm_circuit_open_time = 0.0
        self._client_lock = threading.Lock()
        self._circuit_lock = threading.Lock()

    @property
    def client(self):
        """延迟初始化API客户端（和 llm_matcher.py 相同的方式）"""
        if self._client is None:
            with self._ensure_client_lock():
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
            if not config.CLAUDE_API_KEY:
                raise ValueError("未配置CLAUDE_API_KEY，请在.env文件中设置")
            if config.CLAUDE_BASE_URL:
                # 中转模式：用httpx直接调用，绕开SDK的认证头冲突
                import httpx
                return httpx.Client(timeout=config.LLM_TIMEOUT)
            else:
                # 官方API：用Anthropic SDK
                import anthropic
                return anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)
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
                     method_cards: list[dict] = None,
                     reasoning_packet: dict = None,
                     overview_context: str = "",
                     search_query: str = "") -> dict:
        """
        Agent匹配单条清单

        参数:
            bill_item: 清单项 {name, description, unit, quantity, specialty, ...}
            candidates: 搜索+参数验证后的候选定额列表
            reference_cases: 经验库中的参考案例
            rules_context: 规则知识库中的相关规则
            method_cards: 方法论卡片列表（从经验中提炼的选定额方法）
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
            rules_context, method_cards, reasoning_packet, overview_context
        )

        # 调用大模型
        if self.is_circuit_open() and not self._check_half_open():
            return self._fallback_result(bill_item, candidates, "LLM熔断（冷却中）",
                                         match_source="agent_circuit_break")
        try:
            response_text = self._call_llm(prompt)
            with self._ensure_circuit_lock():
                self._llm_consecutive_fails = 0
                self._llm_circuit_open = False
                self._llm_circuit_open_time = 0.0
        except Exception as e:
            with self._ensure_circuit_lock():
                self._llm_consecutive_fails += 1
                if self._llm_consecutive_fails >= self._LLM_CIRCUIT_THRESHOLD:
                    self._llm_circuit_open = True
                    self._llm_circuit_open_time = time.time()
            import traceback
            logger.error(f"Agent大模型调用失败: {e}\n{traceback.format_exc()}")
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
                "province": self.province,
            })
        except Exception as e:
            logger.warning(f"学习笔记记录失败: {e}")

        return result

    # 专业分流规则：根据清单所属专业注入不同的注意事项
    _SPECIALTY_WARNINGS = {
        "C4": (  # 电气
            "### 电气专业注意\n"
            "- 配电箱按安装方式分：落地式/明装/暗装，不能混用\n"
            "- 电缆按敷设方式分：沿桥架、穿导管、直埋，选对方式再选截面档位\n"
            "- 配管按材质分：SC钢管(焊接)/JDG/KBG(扣压)/PVC(塑料)，不能混用\n"
            "- 电机功率kW是硬参数，按不小于实际功率的最小档位向上取档"
        ),
        "C10": (  # 给排水
            "### 给排水专业注意\n"
            "- 管道按材质+连接方式分：镀锌钢管丝接/焊接、PPR热熔、PE热熔/电熔、PVC粘接\n"
            "- DN是硬参数，必须向上取档（DN32选DN40以内定额）\n"
            "- 管道安装和管卡/支架是配套关系，不要漏选关联定额\n"
            "- 给水管≠排水管≠雨水管，管材和连接方式不同"
        ),
        "C9": (  # 消防
            "### 消防专业注意\n"
            "- 消防管道和给水管道定额不同，即使材质相同也要选消防册(C9)\n"
            "- 喷头按类型分：下垂型/直立型/侧壁型，具体分档以当地定额册为准\n"
            "- 灭火器和灭火器箱是两个独立定额，不能合并\n"
            "- 消火栓≠喷头≠灭火器，三者属不同品类"
        ),
        "C7": (  # 通风空调
            "### 通风空调专业注意\n"
            "- 风管按材质分：镀锌钢板/不锈钢/玻镁复合/玻璃纤维\n"
            "- 风管按形状分：矩形/圆形，按周长或直径分档\n"
            "- 风口和风阀是独立定额，不混入风管"
        ),
        "C12": (  # 刷油防腐保温
            "### 刷油防腐保温注意\n"
            "- 保温材料不能混用：橡塑≠聚氨酯≠玻璃棉≠岩棉\n"
            "- 保温按管径/壁厚分档，注意向上取档\n"
            "- 防腐做法不可混套：刷油≠喷涂≠缠绕"
        ),
    }

    def _get_specialty_warnings(self, specialty: str) -> str:
        """根据专业返回对应的注意事项文本"""
        warning = self._SPECIALTY_WARNINGS.get(specialty, "")
        if warning:
            return f"\n{warning}\n"
        return ""

    def _build_agent_prompt(self, bill_item: dict, candidates: list[dict],
                            reference_cases: list[dict] = None,
                            rules_context: list[dict] = None,
                            method_cards: list[dict] = None,
                            reasoning_packet: dict = None,
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

        # 格式化候选定额列表
        # 前5条详细展示（含参数匹配信息），后15条精简展示（省token）
        candidate_lines = []
        for i, c in enumerate(candidates[:20], start=1):
            quota_id = str(c.get("quota_id", "")).strip() or "UNKNOWN"
            quota_name = str(c.get("name", "")).strip() or "未命名候选"
            if i <= 5:
                # 前5条：详细展示
                param_info = c.get("param_detail", "")
                param_match = "✓参数匹配" if c.get("param_match", True) else "✗参数不匹配"
                try:
                    score = float(c.get("param_score", 0))
                except (TypeError, ValueError):
                    score = 0.0
                candidate_lines.append(
                    f"{i}. [{quota_id}] {quota_name} | 单位:{c.get('unit', '?')} "
                    f"| {param_match}({score:.0%}) {param_info}"
                )
            else:
                # 后15条：精简展示（编号+名称）
                candidate_lines.append(f"{i}. [{quota_id}] {quota_name}")
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

        # 格式化方法论卡片（从经验中提炼的选定额方法）
        method_text = ""
        if method_cards:
            card_lines = []
            for card in method_cards[:2]:  # 最多注入2张卡片，避免prompt过长
                category = card.get("category", "")
                scope = card.get("_scope", "local")  # local=同省, universal=跨省
                universal = card.get("universal_method", "")
                province_ref = card.get("method_text", "")
                errors = card.get("common_errors", "")
                source = card.get("source_province", "")

                if scope == "universal":
                    # 跨省卡片：只注入通用方法论，不含省份编号
                    content = universal or province_ref  # 降级兜底
                    card_block = f"### {category}（通用方法论，来自{source}经验）\n{content}"
                else:
                    # 同省卡片：注入完整内容
                    if universal:
                        card_block = f"### {category}\n{universal}"
                        if province_ref:
                            card_block += f"\n\n**本省定额参考：**\n{province_ref}"
                    else:
                        # 旧卡片降级：直接用method_text
                        card_block = f"### {category}\n{province_ref}"

                if errors:
                    card_block += f"\n**常见错误:** {errors}"
                card_lines.append(card_block)
            method_text = "\n## 方法论指导（从历史经验中提炼的选定额方法）\n" + "\n\n".join(card_lines)

        # 格式化提取的参数
        params_text = ""
        if params:
            param_parts = []
            # 线缆类型标签（来自 bill_cleaner 的自动识别）
            cable_type = bill_item.get("cable_type", "")
            if cable_type:
                param_parts.append(f"线缆类型:{cable_type}")
            if params.get("dn"):
                param_parts.append(f"管径DN{params['dn']}")
            if params.get("cable_section"):
                param_parts.append(f"截面{params['cable_section']}mm²")
            if params.get("material"):
                param_parts.append(f"材质:{params['material']}")
            if params.get("connection"):
                param_parts.append(f"连接:{params['connection']}")
            if params.get("kw") is not None:
                param_parts.append(f"功率{params['kw']}kW")
            if params.get("kva"):
                param_parts.append(f"容量{params['kva']}kVA")
            if param_parts:
                params_text = f"\n- 提取参数：{', '.join(param_parts)}"

        # 整表概览上下文
        overview_text = ""
        if overview_context:
            overview_text = f"\n## 整表概览\n{overview_context}"

        reasoning_text = ""
        if isinstance(reasoning_packet, dict) and reasoning_packet.get("engaged"):
            lines = []
            for summary in (reasoning_packet.get("conflict_summaries") or [])[:6]:
                lines.append(f"- {summary}")
            for point in (reasoning_packet.get("compare_points") or [])[:4]:
                lines.append(f"- 仲裁重点: {point}")
            if lines:
                reasoning_text = "\n## 候选差异仲裁摘要\n" + "\n".join(lines)

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
{cases_text}{method_text}{rules_text}{reasoning_text}

## 分析要求
请按以下步骤思考：
1. **理解清单**：这条清单描述的是什么工作？（管道安装/阀门/设备/线路/...）
2. **识别关键特征**：材质是什么？连接方式？关键参数？
3. **比对候选**：哪条定额的类型、材质、参数最吻合？
4. **参数取档**：数值参数要"向上取档"（如DN32应选DN40以内的定额）
5. **关联定额**：是否需要配套定额？（管道需要管卡/试压，设备需要调试等）
6. **搜索建议**：如果候选列表中没有你认为正确类型的定额（如清单是水泵但候选全是冷水机组），请在 suggested_search 填写你建议的搜索关键词（如"水泵安装 离心泵"），帮助系统找到正确方向。如果候选中已有合适定额，此字段留空字符串。

## 注意事项
- "以内"表示≤，如"DN150以内"适用于DN≤150
- 材质必须一致（镀锌钢管≠不锈钢管≠PPR管）
- 连接方式要对应（丝接≠沟槽≠法兰≠卡压）
- 关联定额只能是**不同类型**的配套工作（如管道+管卡、设备+调试），不能是同类型的不同规格或不同方式（如不能同时选"沿桥架敷设"和"穿导管敷设"）
- 一条清单只选一条主定额，不确定时选最可能的那一条
{self._get_specialty_warnings(specialty)}
## 常见易混淆品类（必须区分，选错直接判错）
- 配电箱≠控制箱≠动力柜≠端子箱（按箱体类型区分）
- 普通套管≠防水套管（防水套管用于穿越防水层/外墙）
- 灭火器≠灭火装置≠灭火器箱（三者定额完全不同）
- 水泵≠水泵接合器（接合器是消防设施，不是泵）
- 橡塑保温≠聚氨酯保温≠玻璃棉保温（按保温材料分）
- 桥架≠线槽（桥架是大截面金属托盘，线槽是小截面PVC/金属槽）
- 电缆敷设≠电线穿管（电缆沿桥架/直埋，电线穿导管）
- 阀门≠弯头≠三通≠异径管（管件和阀门是不同品类）
- 泵≠风机≠风口（三者属完全不同的设备类型）
- 防火阀≠蝶阀≠球阀（防火阀属消防，蝶阀/球阀属管道阀门）

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
    "explanation": "整体分析说明",
    "suggested_search": "建议搜索关键词（候选不合适时填写，否则留空）"
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
        """调用OpenAI兼容API（DeepSeek/OpenAI/Kimi/Qwen）

        优先用httpx直接发请求（避免OpenAI SDK在某些Docker环境下的ascii编码bug），
        SDK方式作为降级备选。
        """
        model_map = {
            "deepseek": config.DEEPSEEK_MODEL,
            "kimi": config.KIMI_MODEL,
            "qwen": config.QWEN_MODEL,
            "openai": config.OPENAI_MODEL,
        }
        model = model_map.get(self.llm_type, config.DEEPSEEK_MODEL)

        # 获取API配置
        key_map = {
            "deepseek": config.DEEPSEEK_API_KEY,
            "kimi": config.KIMI_API_KEY,
            "qwen": config.QWEN_API_KEY,
            "openai": getattr(config, "OPENAI_API_KEY", ""),
        }
        url_map = {
            "deepseek": config.DEEPSEEK_BASE_URL,
            "kimi": config.KIMI_BASE_URL,
            "qwen": config.QWEN_BASE_URL,
            "openai": getattr(config, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
        }
        api_key = key_map.get(self.llm_type, "")
        base_url = url_map.get(self.llm_type, "")

        # 防御性清洗：去除不可见非ASCII字符（数据库注入的值可能含BOM/零宽空格）
        def _safe_ascii(val):
            if not val or not isinstance(val, str):
                return val or ""
            return val.strip().encode("ascii", errors="ignore").decode("ascii")
        api_key = _safe_ascii(api_key)
        base_url = _safe_ascii(base_url)
        model = _safe_ascii(model)

        # httpx直接调用（绕过SDK编码问题）
        import httpx
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 1500,
        }
        response = httpx.post(url, headers=headers, json=data, timeout=config.LLM_TIMEOUT)
        # 429限流自动重试（等2秒，最多3次）
        for retry in range(3):
            if response.status_code != 429:
                break
            time.sleep(2 * (retry + 1))
            response = httpx.post(url, headers=headers, json=data, timeout=config.LLM_TIMEOUT)
        response.raise_for_status()
        result = response.json()
        # 安全取值，避免中转服务返回异常格式时 KeyError
        try:
            return result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ValueError(f"OpenAI兼容API返回格式异常: {str(result)[:200]}") from e

    def _call_claude(self, prompt: str) -> str:
        """调用Claude API（支持中转和官方两种模式）"""
        if config.CLAUDE_BASE_URL:
            # 中转模式：用httpx原始请求（避免SDK认证头冲突）
            url = f"{config.CLAUDE_BASE_URL.rstrip('/')}/v1/messages"
            headers = {
                "x-api-key": config.CLAUDE_API_KEY,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            data = {
                "model": config.CLAUDE_MODEL,
                "max_tokens": 1500,
                "temperature": 0.1,
                "messages": [{"role": "user", "content": prompt}],
            }
            response = self.client.post(url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            # 安全取值，避免中转服务返回异常格式时 KeyError
            try:
                return result["content"][0]["text"]
            except (KeyError, IndexError, TypeError) as e:
                raise ValueError(f"Claude中转API返回格式异常: {str(result)[:200]}") from e
        else:
            # 官方API：用Anthropic SDK
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
                if isinstance(main_c, dict):
                    main_quota_id = str(main_c.get("quota_id", "")).strip()
                    if main_quota_id:
                        quotas.append({
                            "quota_id": main_quota_id,
                            "name": str(main_c.get("name", "")).strip() or "未命名候选",
                            "unit": main_c.get("unit", ""),
                            "reason": data.get("main_reason", ""),
                            "db_id": main_c.get("id"),
                        })
            elif main_id:
                # 按编号查找（备用）
                for c in candidates:
                    if not isinstance(c, dict):
                        continue
                    c_id = str(c.get("quota_id", "")).strip()
                    if c_id == main_id:
                        quotas.append({
                            "quota_id": c_id,
                            "name": str(c.get("name", "")).strip() or "未命名候选",
                            "unit": c.get("unit", ""),
                            "reason": data.get("main_reason", ""),
                            "db_id": c.get("id"),
                        })
                        break

        # AI推荐的定额编号不在候选中 — 标记为需要AI引导重新搜索
        _ai_recommended_not_found = False
        if not no_match and main_id and not quotas:
            _ai_recommended_not_found = True
            logger.info(f"Agent推荐定额 {main_id} 不在候选列表中，标记为需要重搜")

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
                        if not isinstance(c, dict):
                            continue
                        c_id = str(c.get("quota_id", "")).strip()
                        if c_id == rel_id:
                            rel_c = c
                            break

                if not isinstance(rel_c, dict):
                    continue

                # 过滤同类定额：册号+章节相同的不算关联
                rel_qid = str(rel_c.get("quota_id", "")).strip()
                if not rel_qid:
                    continue
                rel_parts = rel_qid.split("-")
                if len(rel_parts) >= 2 and main_quota_prefix:
                    rel_prefix = f"{rel_parts[0]}-{rel_parts[1]}"
                    if rel_prefix == main_quota_prefix:
                        logger.debug(f"过滤同类关联定额: {rel_qid}（与主定额同属{main_quota_prefix}）")
                        continue

                quotas.append({
                    "quota_id": rel_qid,
                    "name": str(rel_c.get("name", "")).strip() or "未命名候选",
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
            if not isinstance(c, dict):
                continue
            c_quota_id = str(c.get("quota_id", "")).strip()
            if not c_quota_id or c_quota_id in selected_ids:
                continue
            try:
                ps = float(c.get("param_score", 0.5))
            except (TypeError, ValueError):
                ps = 0.5
            from src.match_core import calculate_confidence as _calc_conf
            alt_conf = _calc_conf(
                ps, c.get("param_match", True),
                name_bonus=c.get("name_bonus", 0.0),
                rerank_score=c.get("rerank_score", c.get("hybrid_score", 0.0)),
            )
            alternatives.append({
                "quota_id": c_quota_id,
                "name": str(c.get("name", "")).strip() or "未命名候选",
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
            "suggested_search": str(data.get("suggested_search", "")).strip(),
        }

        # AI推荐的定额不在候选中 — 传递标记给上游触发重搜
        if _ai_recommended_not_found:
            result["_ai_recommended_id"] = main_id
            result["_ai_recommended_not_found"] = True

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
                         error_msg: str,
                         match_source: str = "agent_fallback") -> dict:
        """
        降级处理：大模型调用失败时，回退到参数验证第1名

        和 match_search_only 的逻辑一样
        """
        best = None
        confidence = 0

        if candidates:
            valid_candidates = []
            for c in candidates:
                if not isinstance(c, dict):
                    continue
                quota_id = str(c.get("quota_id", "")).strip()
                if not quota_id:
                    continue
                valid_candidates.append(c)
            from src.match_core import calculate_confidence
            matched = [c for c in valid_candidates if c.get("param_match", True)]
            if matched:
                best = matched[0]
                confidence = calculate_confidence(
                    best.get("param_score", 0.5), param_match=True,
                    name_bonus=best.get("name_bonus", 0.0),
                    rerank_score=best.get("rerank_score", best.get("hybrid_score", 0.0)),
                    candidates_count=len(valid_candidates),
                    is_ambiguous_short=bill_item.get("_is_ambiguous_short", False),
                )
            else:
                best = valid_candidates[0] if valid_candidates else None
                if best:
                    confidence = calculate_confidence(
                        best.get("param_score", 0.0), param_match=False,
                        candidates_count=len(valid_candidates),
                        is_ambiguous_short=bill_item.get("_is_ambiguous_short", False),
                    )

        best_quota_id = str((best or {}).get("quota_id", "")).strip()
        best_name = str((best or {}).get("name", "")).strip() or "未命名候选"
        has_valid_best = bool(best and best_quota_id)
        if not has_valid_best:
            confidence = 0

        result = {
            "bill_item": bill_item,
            "quotas": [{
                "quota_id": best_quota_id,
                "name": best_name,
                "unit": best.get("unit", ""),
                "reason": f"Agent降级(候选策略): {error_msg}",
                "db_id": best.get("id"),
            }] if has_valid_best else [],
            "confidence": confidence,
            "explanation": f"Agent降级为候选策略: {error_msg}",
            "match_source": match_source,  # 区分 agent_fallback（普通失败）和 agent_circuit_break（熔断降级）
            "candidates_count": len(candidates),
        }
        if not has_valid_best:
            result["no_match_reason"] = "降级候选缺少有效定额编号"
        return result

    def _extract_json(self, text: str) -> str | None:
        """从大模型回复中提取JSON字符串（和 llm_matcher 同逻辑）"""
        text = text.strip()

        # 纯JSON（对象或数组）— 先验证是否真的是合法JSON
        if text.startswith("{") or text.startswith("["):
            try:
                json.loads(text)
                return text
            except json.JSONDecodeError:
                pass  # 不是纯JSON（尾部有非JSON内容），继续后续提取逻辑

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
                if extracted.startswith("{") or extracted.startswith("["):
                    return extracted

        # 最后尝试：找第一个 { 或 [ 到最后一个 } 或 ]
        first_brace = text.find("{")
        first_bracket = text.find("[")
        last_brace = text.rfind("}")
        last_bracket = text.rfind("]")

        # 谁先出现就以谁为外层容器（避免 "[{...},{...}]" 被截成非法片段）
        has_brace = first_brace >= 0 and last_brace > first_brace
        has_bracket = first_bracket >= 0 and last_bracket > first_bracket

        if has_brace and has_bracket:
            # [ 在 { 前面 → 外层是数组，取 [...]
            if first_bracket < first_brace:
                return text[first_bracket:last_bracket + 1]
            else:
                return text[first_brace:last_brace + 1]
        elif has_brace:
            return text[first_brace:last_brace + 1]
        elif has_bracket:
            return text[first_bracket:last_bracket + 1]

        return None

