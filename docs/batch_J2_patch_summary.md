# J-Batch2 Patch Summary：三路熔断机制

## 改动点

| 文件 | 位置 | 改动 | 影响面 |
|------|------|------|--------|
| `src/vector_engine.py` | L25-27 | 新增类级 `_model_unavailable_warned` / `_model_skip_count` 标志 | 向量搜索降级 |
| `src/vector_engine.py` | L155-161 | `encode_queries()` 添加 model==None 守卫，只警告一次 | 阻止 NoneType.encode 风暴 |
| `src/vector_engine.py` | L199-204 | `search()` 添加 model==None 且无预计算向量时返回空 | 同上 |
| `src/experience_db.py` | L741-743 | `search_similar()` 添加 model==None 守卫 | 经验库向量搜索降级 |
| `src/universal_kb.py` | L366-368 | `find_similar()` 添加 model==None 守卫 | 通用知识库降级 |
| `src/agent_matcher.py` | L33-37 | 新增类级LLM熔断器属性 | LLM连续失败处理 |
| `src/agent_matcher.py` | L155-170 | `match_single()` LLM调用加入熔断检查和计数 | 核心改动 |
| `src/match_engine.py` | L617-619 | 低置信度重试前检查LLM熔断状态 | 跳过无效重试 |
| `src/model_cache.py` | L213-228 | `preload_all()` 加启动自检提示 + `get_degradation_summary()` | 可观测性 |
| `tests/test_circuit_breaker.py` | 新文件 | 7个回归测试 | 测试覆盖 |

## 核心设计

### 向量熔断（run-level 快速跳过）
```python
# VectorEngine 类级标志
_model_unavailable_warned = False  # 只警告一次
_model_skip_count = 0              # 跳过计数（供统计）

def encode_queries(self, queries):
    if self.model is None:
        if not VectorEngine._model_unavailable_warned:
            logger.warning("向量模型不可用，本轮所有向量搜索将跳过")
            VectorEngine._model_unavailable_warned = True
        VectorEngine._model_skip_count += 1
        return [None] * len(queries)  # 快速返回，不崩溃
```

### LLM熔断（连续失败 5 次后断路）
```python
# AgentMatcher 类级属性
_llm_consecutive_fails = 0
_llm_circuit_open = False
_LLM_CIRCUIT_THRESHOLD = 5

# match_single 中：
if AgentMatcher._llm_circuit_open:
    return self._fallback_result(...)  # 直接走确定性兜底
try:
    response = self._call_llm(prompt)
    AgentMatcher._llm_consecutive_fails = 0  # 成功重置
except:
    AgentMatcher._llm_consecutive_fails += 1
    if count >= threshold: AgentMatcher._llm_circuit_open = True
```

### 启动自检提示
```
[ModelCache] 向量模型不可用，本轮将仅使用BM25关键词搜索（精度有所下降）
[ModelCache] Reranker模型不可用，本轮将跳过语义重排（排序精度有所下降）
```

## 预期效果

| 场景 | 修复前 | 修复后 |
|------|--------|--------|
| 向量不可用 | 6479 次异常 + warning | 1 次 warning + 静默计数 |
| LLM 全部失败 | 每条都尝试 + 低置信度重试 | 5 次后熔断，剩余走兜底 |
| 日志噪声 | 7000+ warning/轮 | <10 条降级提示 |

## 回滚方式

```bash
git checkout src/vector_engine.py src/experience_db.py src/universal_kb.py src/agent_matcher.py src/match_engine.py src/model_cache.py
git rm tests/test_circuit_breaker.py
```
