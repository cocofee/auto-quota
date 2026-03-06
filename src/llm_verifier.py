# -*- coding: utf-8 -*-
"""
LLM后验证模块 — 匹配结果的质量关卡

功能：
在搜索引擎返回匹配结果后，用大模型验证：
1. 清单描述和匹配的定额是否属于同一类东西？
2. 如果不对，正确方向是什么？
3. 错误的结果用新方向重新搜索并替换

v2 改进（双模型+并发+定向验证）：
- 支持独立VERIFY_LLM/VERIFY_MODEL配置（验证可用不同于匹配的模型）
- verify_batch 改为并发执行（ThreadPoolExecutor）
- 定向验证：只验低置信度+高风险项，绿灯抽检5%
- max_tokens/timeout 独立配置，验证任务更精简

调用位置：
- main.py 的 run() 函数中，Agent匹配完成后调用
"""

import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from loguru import logger

import config


class LLMVerifier:
    """匹配结果的LLM后验证器"""

    def __init__(self, llm_type: str = None):
        """
        参数:
            llm_type: 大模型类型（claude/kimi/deepseek等），默认读config.VERIFY_LLM
        """
        self.llm_type = llm_type or config.VERIFY_LLM or config.AGENT_LLM
        # 模型型号：优先用VERIFY_MODEL，没配则用对应厂商的默认型号
        self._verify_model = config.VERIFY_MODEL or ""
        self._client = None
        self._client_lock = threading.Lock()
        # 统计计数器（并发安全）
        self._stats_lock = threading.Lock()
        self.stats = {
            "verified": 0,       # 已验证条数
            "correct": 0,        # 判定正确
            "wrong": 0,          # 判定错误
            "corrected": 0,      # 成功纠正
            "correct_failed": 0, # 纠正失败（重搜也没找到）
            "skipped": 0,        # 跳过（高置信度/经验库直通等）
            "llm_error": 0,      # LLM调用失败
            "spot_checked": 0,   # 绿灯抽检条数
        }

    def _inc_stat(self, key: str, delta: int = 1):
        """线程安全地增加统计计数"""
        with self._stats_lock:
            self.stats[key] += delta

    @property
    def client(self):
        """延迟创建LLM客户端"""
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = self._create_client()
        return self._client

    def _create_client(self):
        """创建LLM客户端"""
        if self.llm_type == "claude":
            if config.CLAUDE_BASE_URL:
                # 中转模式用httpx
                return httpx.Client(timeout=config.VERIFY_TIMEOUT)
            else:
                import anthropic
                return anthropic.Anthropic(api_key=config.CLAUDE_API_KEY)
        else:
            # OpenAI兼容的模型
            from openai import OpenAI
            key_map = {
                "deepseek": config.DEEPSEEK_API_KEY,
                "kimi": config.KIMI_API_KEY,
                "qwen": config.QWEN_API_KEY,
                "openai": config.OPENAI_API_KEY,
            }
            url_map = {
                "deepseek": config.DEEPSEEK_BASE_URL,
                "kimi": config.KIMI_BASE_URL,
                "qwen": config.QWEN_BASE_URL,
                "openai": getattr(config, "OPENAI_BASE_URL", None),
            }
            api_key = key_map.get(self.llm_type)
            base_url = url_map.get(self.llm_type)
            if not api_key:
                raise ValueError(f"未配置{self.llm_type}的API Key")
            return OpenAI(api_key=api_key, base_url=base_url)

    def _get_model_name(self) -> str:
        """获取实际使用的模型型号"""
        # 优先用 VERIFY_MODEL 指定的型号
        if self._verify_model:
            return self._verify_model
        # 否则用对应厂商的默认型号
        model_map = {
            "deepseek": config.DEEPSEEK_MODEL,
            "kimi": config.KIMI_MODEL,
            "qwen": config.QWEN_MODEL,
            "openai": config.OPENAI_MODEL,
            "claude": config.CLAUDE_MODEL,
        }
        return model_map.get(self.llm_type, config.DEEPSEEK_MODEL)

    def _call_llm(self, prompt: str) -> str:
        """调用大模型"""
        if self.llm_type == "claude":
            return self._call_claude(prompt)
        else:
            return self._call_openai_compatible(prompt)

    def _call_claude(self, prompt: str) -> str:
        """调用Claude API"""
        model = self._get_model_name()
        if config.CLAUDE_BASE_URL:
            # 防御性清洗
            def _safe_ascii(val):
                if not val or not isinstance(val, str):
                    return val or ""
                return val.strip().encode("ascii", errors="ignore").decode("ascii")
            api_key = _safe_ascii(config.CLAUDE_API_KEY)
            base_url = _safe_ascii(config.CLAUDE_BASE_URL)
            model = _safe_ascii(model)

            url = f"{base_url.rstrip('/')}/v1/messages"
            headers = {
                "x-api-key": api_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            data = {
                "model": model,
                "max_tokens": config.VERIFY_MAX_TOKENS,
                "temperature": 0.0,
                "messages": [{"role": "user", "content": prompt}],
            }
            response = self.client.post(url, headers=headers, json=data)
            response.raise_for_status()
            result = response.json()
            return result["content"][0]["text"]
        else:
            response = self.client.messages.create(
                model=model,
                max_tokens=config.VERIFY_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            return response.content[0].text

    def _call_openai_compatible(self, prompt: str) -> str:
        """调用OpenAI兼容API（用httpx直接发请求，避免SDK编码问题）"""
        model = self._get_model_name()

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

        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": config.VERIFY_MAX_TOKENS,
        }
        response = httpx.post(url, headers=headers, json=data, timeout=config.VERIFY_TIMEOUT)
        # 429限流自动重试（等2秒，最多3次）
        for retry in range(3):
            if response.status_code != 429:
                break
            import time
            time.sleep(2 * (retry + 1))
            response = httpx.post(url, headers=headers, json=data, timeout=config.VERIFY_TIMEOUT)
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]

    # ============================================================
    # 核心验证逻辑
    # ============================================================

    def _should_verify(self, result: dict) -> str:
        """
        判断一条结果是否需要验证

        返回:
            "skip" — 跳过验证
            "verify" — 需要验证
            "spot_check" — 绿灯抽检
        """
        confidence = result.get("confidence", 0) or 0
        match_source = result.get("match_source", "")

        # 无匹配结果的跳过（没东西可验证）
        quotas = result.get("quotas", [])
        if not quotas:
            return "skip"

        # 经验库直通的跳过（人工验证过的数据，质量有保障）
        if match_source == "experience":
            return "skip"

        # 快通道的跳过（参数验证高分直通的）
        if match_source == "agent_fastpath":
            return "skip"

        # 高置信度跳过验证
        skip_threshold = getattr(config, "VERIFY_SKIP_THRESHOLD", 88)
        if confidence >= skip_threshold:
            # 绿灯随机抽检（保底质量监控）
            spot_rate = getattr(config, "VERIFY_SPOT_CHECK_RATE", 0.05)
            if spot_rate > 0 and random.random() < spot_rate:
                return "spot_check"
            return "skip"

        # 其他情况都需要验证
        return "verify"

    def verify_result(self, result: dict, searcher=None) -> dict:
        """
        验证单条匹配结果，如果错误则尝试纠正

        参数:
            result: 匹配结果字典（包含 bill_item, quotas, confidence 等）
            searcher: 搜索引擎实例（用于纠正时重新搜索）

        返回:
            验证/纠正后的结果（原地修改并返回）
        """
        confidence = result.get("confidence", 0) or 0

        bill_item = result.get("bill_item", {})
        bill_name = bill_item.get("name", "")
        bill_desc = bill_item.get("description", "")
        quotas = result.get("quotas", [])
        main_quota = quotas[0]
        quota_name = main_quota.get("name", "")

        # 构造验证prompt
        prompt = self._build_verify_prompt(bill_name, bill_desc, quota_name)

        try:
            llm_response = self._call_llm(prompt)
            verdict = self._parse_verdict(llm_response)
        except Exception as e:
            logger.warning(f"LLM验证调用失败: {e}")
            self._inc_stat("llm_error")
            return result

        self._inc_stat("verified")

        if verdict["correct"]:
            # 验证通过
            self._inc_stat("correct")
            # 可以适当提升置信度
            if confidence < 85:
                result["confidence"] = min(confidence + 10, 90)
                result["confidence_text"] = self._confidence_text(result["confidence"])
            result["verify_status"] = "verified_ok"
            return result

        # 验证失败 — 尝试纠正
        self._inc_stat("wrong")
        correct_direction = verdict.get("direction", "")
        reason = verdict.get("reason", "")
        logger.info(f"LLM验证: [{bill_name}] 匹配错误 "
                    f"({quota_name} → 应为: {correct_direction})")

        if searcher and correct_direction:
            corrected = self._try_correct(
                result, correct_direction, searcher)
            if corrected:
                self._inc_stat("corrected")
                result["verify_status"] = "corrected"
                result["verify_original"] = quota_name
                result["verify_direction"] = correct_direction

                # 把纠正信息写入explanation字段（存入数据库，前端能读到）
                new_quota_name = result["quotas"][0]["name"] if result.get("quotas") else ""
                correction_note = (
                    f"[AI纠正] 原匹配「{quota_name}」→ 纠正为「{new_quota_name}」"
                )
                if reason:
                    correction_note += f"\n理由: {reason}"
                result["explanation"] = correction_note

                # 纠正后的知识写入通用知识库（积累经验）
                self._sync_to_kb(bill_name, bill_desc, correct_direction)
                return result

        # 纠正失败（重搜也没找到正确的）
        self._inc_stat("correct_failed")
        result["verify_status"] = "wrong_unfixed"
        result["verify_direction"] = correct_direction
        # 降低置信度，标记需要人工处理
        result["confidence"] = max(confidence - 20, 10)
        result["confidence_text"] = self._confidence_text(result["confidence"])
        # 把存疑信息写入explanation字段
        wrong_note = f"[AI存疑] 匹配「{quota_name}」可能有误"
        if correct_direction:
            wrong_note += f"，建议方向: {correct_direction}"
        if reason:
            wrong_note += f"\n理由: {reason}"
        result["explanation"] = wrong_note
        return result

    def _build_verify_prompt(self, bill_name: str, bill_desc: str,
                              quota_name: str) -> str:
        """构造验证用的prompt"""
        prompt = (
            "你是工程造价专家。请判断以下匹配是否正确。\n\n"
            f"清单项目: {bill_name}\n"
            f"清单描述: {bill_desc}\n"
            f"匹配的定额: {quota_name}\n\n"
            "判断标准:\n"
            "1. 清单描述的设备/材料 和 匹配的定额 是否属于同一类东西?\n"
            "2. 比如管道安装匹配管道定额=正确, 管道安装匹配阀门定额=错误\n"
            "3. 参数(DN/规格)方向大致对应即可\n\n"
            "请用JSON格式回答:\n"
            '{"correct": true/false, "reason": "一句话理由", '
            '"direction": "如果错误,应该搜什么定额(关键词)"}\n\n'
            "只输出JSON,不要其他文字。"
        )
        return prompt

    def _parse_verdict(self, response: str) -> dict:
        """解析LLM验证回复"""
        # 提取JSON
        text = response.strip()

        # 尝试提取被```json包裹的内容
        if "```json" in text:
            start = text.index("```json") + 7
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()
        elif "```" in text:
            start = text.index("```") + 3
            end = text.index("```", start) if "```" in text[start:] else len(text)
            text = text[start:end].strip()

        # 找到第一个 { 和最后一个 }
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            text = text[brace_start:brace_end + 1]

        try:
            data = json.loads(text)
            return {
                "correct": bool(data.get("correct", True)),
                "reason": str(data.get("reason", "")),
                "direction": str(data.get("direction", "")),
            }
        except (json.JSONDecodeError, ValueError):
            # 解析失败时，根据文本内容简单判断
            lower = response.lower()
            if '"correct": false' in lower or '"correct":false' in lower:
                return {"correct": False, "reason": "解析失败但检测到错误标记",
                        "direction": ""}
            # 默认认为正确（保守策略，不误改）
            return {"correct": True, "reason": "解析失败,默认通过", "direction": ""}

    def _try_correct(self, result: dict, direction: str,
                      searcher) -> bool:
        """
        用LLM给出的方向重新搜索，替换匹配结果

        参数:
            result: 原匹配结果
            direction: LLM建议的正确搜索方向
            searcher: 搜索引擎

        返回:
            True=纠正成功, False=纠正失败
        """
        # 用LLM给的方向作为搜索词
        try:
            new_candidates = searcher.search(direction, top_k=5)
        except Exception as e:
            logger.warning(f"纠正重搜失败: {e}")
            return False

        if not new_candidates:
            return False

        # 取第一个候选作为新结果
        best = new_candidates[0]
        new_quota = {
            "quota_id": best.get("quota_id", ""),
            "name": best.get("name", ""),
            "unit": best.get("unit", ""),
            "reason": f"LLM纠正: {direction}",
            "db_id": best.get("id"),
        }

        # 替换结果
        result["quotas"] = [new_quota]
        result["confidence"] = 75  # 纠正后给一个中等置信度
        result["confidence_text"] = self._confidence_text(75)
        result["match_source"] = "llm_corrected"
        return True

    def _sync_to_kb(self, bill_name: str, bill_desc: str,
                     correct_direction: str):
        """将纠正结果同步到通用知识库"""
        try:
            from src.universal_kb import UniversalKB
            kb = UniversalKB()
            # 用清单名称+描述作为模式
            pattern = bill_name
            if bill_desc and len(bill_desc) < 100:
                pattern = f"{bill_name} {bill_desc[:50]}"

            kb.add_knowledge(
                bill_pattern=pattern,
                quota_patterns=[correct_direction],
                layer="candidate",   # 自动纠正的进候选层
                confidence=70,
                source_project="llm_verifier_auto",
            )
            logger.debug(f"纠正知识已同步到通用知识库: {pattern} → {correct_direction}")
        except Exception as e:
            logger.debug(f"同步通用知识库失败（不影响主流程）: {e}")

    def _confidence_text(self, confidence: int) -> str:
        """生成置信度文本"""
        if confidence >= 85:
            return f"★★★推荐({confidence}%)"
        elif confidence >= 60:
            return f"★★参考({confidence}%)"
        else:
            return f"★待审({confidence}%)"

    # ============================================================
    # 批量验证（并发执行）
    # ============================================================

    def verify_batch(self, results: list[dict], searcher=None,
                      progress_callback=None) -> list[dict]:
        """
        批量验证匹配结果（并发执行，定向验证）

        改进：
        - 并发执行：ThreadPoolExecutor 多路并行验证
        - 定向验证：只验低置信度+高风险项，跳过经验库直通和快通道
        - 绿灯抽检：高置信度结果随机5%抽检，保底质量监控

        参数:
            results: 匹配结果列表
            searcher: 搜索引擎（用于纠正）
            progress_callback: 进度回调 callback(percent, idx, message)

        返回:
            验证后的结果列表（原地修改）
        """
        total = len(results)
        model_name = self._get_model_name()
        logger.info(f"LLM验证开始: 共{total}条，模型:{self.llm_type}({model_name})")

        # 第1步：筛选需要验证的项
        verify_tasks = []  # [(idx, result, task_type)]
        for idx, result in enumerate(results):
            decision = self._should_verify(result)
            if decision == "skip":
                self._inc_stat("skipped")
            elif decision == "spot_check":
                verify_tasks.append((idx, result, "spot_check"))
                self._inc_stat("spot_checked")
            else:
                verify_tasks.append((idx, result, "verify"))

        skip_count = self.stats["skipped"]
        spot_count = self.stats["spot_checked"]
        logger.info(f"  筛选结果: 需验证{len(verify_tasks)}条"
                    f"（含抽检{spot_count}条），跳过{skip_count}条")

        if not verify_tasks:
            logger.info("LLM验证完成: 全部跳过，无需验证")
            return results

        # 第2步：并发执行验证
        concurrent = max(1, getattr(config, "VERIFY_CONCURRENT", 8))
        completed = 0

        def _verify_one(task):
            """单条验证任务（线程安全）"""
            idx, result, task_type = task
            self.verify_result(result, searcher=searcher)
            return idx

        with ThreadPoolExecutor(max_workers=concurrent) as pool:
            futures = {pool.submit(_verify_one, task): task
                       for task in verify_tasks}

            for future in as_completed(futures):
                completed += 1
                try:
                    future.result()
                except Exception as e:
                    task = futures[future]
                    logger.warning(f"验证任务异常(idx={task[0]}): {e}")

                # 进度回调
                if progress_callback and (completed % 5 == 0
                                          or completed == len(verify_tasks)):
                    try:
                        pct = int(90 + 9 * completed / max(len(verify_tasks), 1))
                        progress_callback(
                            pct, completed,
                            f"验证中 {completed}/{len(verify_tasks)} "
                            f"(纠正{self.stats['corrected']}条)")
                    except Exception:
                        pass

        # 打印汇总
        s = self.stats
        logger.info(
            f"LLM验证完成: "
            f"验证{s['verified']}条, "
            f"正确{s['correct']}, "
            f"错误{s['wrong']}("
            f"纠正{s['corrected']}, "
            f"未纠正{s['correct_failed']}), "
            f"跳过{s['skipped']}, "
            f"抽检{s['spot_checked']}, "
            f"LLM失败{s['llm_error']}"
        )

        return results
