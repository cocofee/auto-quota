---
name: auto-quota-watcher
description: JARVIS / auto-quota 的单文件任务处理、结果审核、OpenClaw 桥接联调、资料学习候选抽取。用于：处理定额匹配任务、查看 task/result 状态、生成审核报告、用 source-list/source-show/source-learn 做资料学习候选抽取，或排查 OpenClaw 到 auto-quota 的桥接链路。不要用于盲目批量自动处理；用户未明确授权时，只做读取、诊断、整理和报告，不做确认、纠正、导出外发。
---

# Auto Quota Watcher

用于处理 `auto-quota` / JARVIS 的三类工作：
- 单文件定额任务的读取、诊断、审核建议
- OpenClaw 桥接接口的本地联调与状态核查
- 资料学习链 `source-list / source-show / source-learn` 的候选抽取

## 核心边界

- 默认只处理**单个任务 / 单个文件**。
- 用户未明确授权前，**不自动确认、不自动纠正、不自动导出、不对外发送**。
- 遇到“停止 / 取消 / 别跑了”立即停。
- 优先走**内网 / 本地桥接**，能不走外网页面就不走。
- 不把“高置信度”直接当正确，尤其是 `match_source=experience` 时要额外警惕经验库污染。

## 默认工作模式

### 1. 任务审核模式

用于用户说：
- “跑一下这个文件”
- “看看这个任务结果”
- “整理审核报告”
- “分析为什么不准 / 为什么慢”

执行顺序：
1. 明确文件或 `task_id`
2. 读取任务状态、统计、结果明细
3. 按 `绿 / 黄 / 红` 分层整理
4. 先看系统级问题，再看单条问题
5. 输出完整审核报告或诊断结论

默认报告结构：
- 标题：文件名 / Sheet / 省份定额 / 任务编号
- 统计总览：总条数、确认数、待人工数
- 已确认明细
- 待人工明细
- 问题汇总
- 系统级观察

## 结果分层规则

- `>=90%`：可视为绿灯，但**仍要抽检 5~10 条**后再决定是否确认
- `70%~89%`：黄灯，只做审核建议，不直接确认
- `<70%`：红灯，只做诊断、搜索建议、待人工清单

额外规则：
- 单文件绿灯率 `>40%` 时，优先怀疑经验库污染或回流偏差，必须抽检。
- `100+` 条任务分批看，每批最多 `50` 条。
- 措施项、非标准项、学习型材料表，不要硬塞进正常定额审核链。

## 系统级观察框架

每次看任务都优先检查这五类：
- 是否有大量本不该进检索的文件 / 条目被推进主链
- 是否出现经验库回流污染导致高分误判
- 是否存在专业错位、连接方式错位、主材特征缺失
- 是否是召回过宽 / 串行过重导致“慢但没明显变准”
- 是否有接口异常、结果缺失、搜索异常、状态卡死

默认判断口径：
- **慢但没明显变准 = 优先怀疑召回过宽和串行过重，不优先怪模型。**

## 资料学习模式

用于用户说：
- “让系统学习这份资料”
- “这个写到哪里去了”
- “看看 source-learn 提了什么候选”
- “先 dry-run 看看质量”

执行顺序：
1. 用 `source-list` 找资料
2. 用 `source-show` 看资料元数据 / 摘要
3. 先执行 `source-learn --dry-run`
4. 候选质量可以，再正式 `source-learn`
5. 说明写入位置与候选数量

重要口径：
- `source-learn` **不会直接写到 Obsidian / 正式知识库**
- 当前默认写入 `promotion_queue` 候选层
- 重点抽取 `rule / method / experience`
- 目录、封面、章节导航通常不适合抽候选，优先挑高知识密度段落

高知识密度关键词示例：
- `适用范围`
- `编制依据`
- `计算规则`
- `工作内容`
- `包括 / 不包括`
- `系数`
- `调整`
- `注意事项`

## 桥接联调模式

用于用户说：
- “桥接通了吗”
- “本地先联调一下 OpenClaw 接口”
- “别先进前端，先打一下 API”
- “看 review-draft / review-confirm 能不能走通”

优先使用本地脚本：
- `tools/openclaw_bridge_smoke.ps1`：读链路探活、拉任务 / review-items
- `tools/openclaw_bridge_review.ps1`：单条 `review-draft / review-confirm`
- `tools/openclaw_bridge_batch_review.ps1`：按 `light_status` 批量写 `review-draft`，支持 `WhatIf`

执行原则：
- 先 smoke，再单条 review，再批量 review
- 先验证读链路，再验证写链路
- 批量写入前优先 `WhatIf`
- 默认记录失败点：认证、参数、状态码、返回体、耗时

## 用户明确授权后才能做的动作

只有在用户明确要求时，才执行这些写操作：
- 绿灯确认
- 黄灯修正提交
- 导出最终结果
- 正式 `source-learn`
- 批量 `review-draft / review-confirm`

如果授权不明确，就停在：
- 状态读取
- 结果整理
- 搜索候选
- 报告输出
- dry-run

## 搜索与审核习惯

- 先看文件 / 任务是否值得进主链，再看匹配对不对。
- 先判断方向，再判断参数，再判断编号。
- 对类似 `灭火器 / 含箱体`、`甲供 / 乙供`、`主材 / 安装内容` 这类容易误判的项，优先拆语义，不要只看关键词。
- 设备询价、实时状态、最新接口情况，一律先查再说，不凭印象报。

## 常用命令参考

按实际环境调整路径；常见入口是 `auto_match.py`。

```bash
python3 auto_match.py status
python3 auto_match.py match "文件路径" --province "完整定额名"
python3 auto_match.py source-list --query "山东" --limit 20
python3 auto_match.py source-show doc-001
python3 auto_match.py source-learn doc-001 --dry-run
python3 auto_match.py source-learn doc-001 --llm openai
```

本地桥接脚本：

```bash
powershell -File tools/openclaw_bridge_smoke.ps1
powershell -File tools/openclaw_bridge_review.ps1
powershell -File tools/openclaw_bridge_batch_review.ps1 -WhatIf
```

## 输出要求

- 结论优先说人话，不先堆接口细节。
- 报告必须带：文件/任务身份、统计、关键异常、下一步建议。
- 待人工明细尽量带：清单名摘要、置信度、候选搜索方向、判断理由。
- 如果是资料学习链，明确写清：是否 dry-run、候选数量、是否已写入候选层。

## 不要做的事

- 不要把未核实的信息说成事实。
- 不要未经授权自动处理多个文件。
- 不要把经验库结果当成绝对正确。
- 不要把外部深化系统整套提前算进本单体。
- 不要在没看日志 / 返回体时就下结论说“接口坏了”。

## 维护建议

如果后续继续扩这个技能，优先把细节下沉到：
- `references/`：报告模板、错误分类、专业判断规则
- `scripts/`：稳定可复用的读取、汇总、校验脚本

保持 `SKILL.md` 只放核心边界、流程和判断口径。