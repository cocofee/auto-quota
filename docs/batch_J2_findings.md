# J-Batch2 Findings：向量/重排/LLM 三路熔断缺失

## 问题描述

### P1-2：向量不可用后缺少全局短路，导致异常风暴与性能劣化

`ModelCache` 冷却期返回 `None`，但多条链路直接 `self.model.encode(...)` 导致 `NoneType` 异常。
每条清单、每个 query variant 重复触发并记录 warning。

- 日志证据：`NoneType' object has no attribute 'encode'` 达 **6479** 次

### P1-3：LLM链路缺少故障熔断，低置信重试放大失败成本

LLM 请求失败后没有"连续失败熔断"，每条清单继续请求。
低置信策略触发"全库重试搜索 + 再次LLM"，进一步放大失败。

- 日志证据：`Agent大模型调用失败=36`（36条全失败），仍触发 `全库重试搜索=20` 次

### Reranker 状态

Reranker 已有 model==None 守卫（`reranker.py:68-73`），无需修复。

## 影响范围

- 向量异常：日志噪声巨大（7000+ warning/轮）、吞吐下降
- LLM 异常：主动放大 CPU/IO 消耗、拉长总时延
- 两者叠加时，系统虽能跑但运行代价 10x+ 上升
