# 自动套定额系统（auto-quota）

## 用户背景

我是造价人员（非程序员），通过 Claude Code 辅助编程。
遇到高风险改动或多种方案时，先说明再动手，等我确认后再执行。

## 项目概述

自动套定额系统：读取工程量清单Excel → 自动匹配最合适的定额子目 → 输出广联达可导入的Excel。
目标准确率 85%+，支持持续学习，一省通全国。

## 项目结构约定

- 测试和评测工具放在 `tests/` 下
- 辅助导入工具放在 `tools/` 下

## 常用命令

```bash
# 匹配定额（两种模式）
python main.py "<Excel路径>"                    # search模式（默认，纯搜索，免费）
python main.py "<Excel路径>" --mode agent       # agent模式（Jarvis，需API Key）

# 常用参数
python main.py "<Excel路径>" --limit 10         # 只处理前10条（调试用）
python main.py "<Excel路径>" --sheet "给排水"   # 只处理指定Sheet

# Jarvis全流程（匹配+审核+纠正，俗称"自动炮"）
python tools/jarvis_pipeline.py "<Excel路径>" --province "北京2024"
python tools/jarvis_pipeline.py "<Excel路径>" --province "北京2024" --no-store  # 不存经验库
python tools/jarvis_pipeline.py "<Excel路径>" --province "北京2024" --no-experience  # 不用经验库

# 经验库查看
python tools/experience_view.py stats            # 统计
python tools/experience_view.py search "镀锌钢管"  # 搜索
```

## Jarvis 自动炮（全流程说明）

Jarvis 流水线是系统的核心功能，一键完成"匹配→审核→纠正→存经验库"4个步骤。

```
清单Excel → 第1步:匹配定额 → 第2步:AI审核(找出错误) → 第3步:自动纠正(改Excel)
         → 第4步:存经验库(下次同类清单直接命中) → 输出已审核Excel
```

### 4个步骤做了什么

| 步骤 | 做什么 | 对应代码 |
|------|--------|----------|
| 第1步 匹配 | 读Excel，每条清单搜索最合适的定额 | `main.py` (mode=agent) |
| 第2步 审核 | AI检查匹配结果，找出明显错误 | `tools/jarvis_auto_review.py` |
| 第3步 纠正 | 把审核发现的错误自动改到Excel里 | `tools/jarvis_correct.py` |
| 第4步 存库 | 纠正结果写入经验库候选层，人工确认后晋升权威层 | `tools/jarvis_store.py` |

### 输出结果怎么看

运行结束后会打印汇总，例如：
```
汇总: 总283 正确240 自动纠正12 人工31 措施0
```

| 状态 | 含义 |
|------|------|
| 正确 | 系统匹配的定额没问题，直接用 |
| 自动纠正 | AI发现错了并自动改好了 |
| 人工 | AI不确定，需要你自己看一眼 |
| 措施 | 措施费项目（脚手架等），不需要套定额 |

### 相关工具

```bash
# 查看经验库
python tools/experience_view.py stats            # 统计
python tools/experience_view.py search "镀锌钢管"  # 搜索

# 学习已确认的匹配结果（把人工确认的结果导入经验库权威层）
python tools/jarvis_learn.py "<已确认Excel>"

# 查定额（按编号或名称搜索）
python tools/jarvis_lookup.py "C10-1-10"          # 按编号查
python tools/jarvis_lookup.py "管道安装"           # 按名称搜
```

## Benchmark 跑分（给系统打分的考试）

每次改完代码后跑一遍 benchmark，用固定试卷对比前后分数，看改动是帮了忙还是帮倒忙。

```bash
# 跑分（自动对比基线）
python tools/run_benchmark.py

# 基线文件（记录上次的成绩，跑分时自动对比）
tests/benchmark_baseline.json

# 试卷配置（4套固定数据集）
tests/benchmark_config.json
```

### 分数怎么看

| 颜色 | 含义 | 置信度 |
|------|------|--------|
| 绿灯 | 系统有把握，大概率套对了 | >=85% |
| 黄灯 | 不太确定，需要人工看一眼 | 60-84% |
| 红灯 | 系统没信心，很可能套错了 | <60% |

目标：绿灯越多越好，红灯越少越好。

### 4套试卷

| 试卷 | 条数 | 考什么 |
|------|------|--------|
| B1 公厕给排水 | 20条 | 管道为主，参数匹配（DN/材质） |
| B2 华佑电气 | 283条 | 大题量，电气专业（配电箱/配管/电缆/灯具） |
| B3 配套楼混合 | 95条 | 多专业混在一起（给排水+电气+消防+通风） |
| B4 脏数据 | 19条 | 故意放的烂数据，测系统容错能力 |

### 历史成绩

| 版本 | B1绿率 | B1红率 | B2绿率 | B2红率 | B3绿率 | B3红率 | 主要改动 |
|------|--------|--------|--------|--------|--------|--------|----------|
| L8 | 80% | 15% | 84.8% | 11.3% | 93.7% | 0% | 参数验证排序优化 |
| L9 | 85% | 10% | 83.8% | 11.3% | 94.7% | 0% | 品类硬排斥（泵!=喷头） |
| L9+ | 90% | 5% | 83.8% | 11.3% | 94.7% | 0% | 土建重分类（土方归A册） |

## 健康检查

```bash
# 快速检查（语法+导入+回归测试）
python tools/system_health_check.py --mode quick

# 完整检查（快速检查+全量pytest+数据库结构+经验库健康）
python tools/system_health_check.py --mode full

# 也可以用批处理脚本
scripts\dev\代码审查.bat
```

## 执行协议

### 代码改动的完整流程

```
改代码 → 自测(pytest) → 健康检查(quick/full) → benchmark跑分
      → Codex审查 → 根据审查意见修改 → 重新自测 → 提交
```

1. **改代码**：按需求修改，一次只改一个目标问题
2. **自测**：`python -m pytest tests/ -q`（405+条全部通过才算过）
3. **健康检查**：`python tools/system_health_check.py --mode full`（9项全通过）
4. **benchmark跑分**：`python tools/run_benchmark.py`（对比基线，确认不退化）
5. **Codex审查**：`scripts\dev\代码审查.bat`（需要codex命令行工具+网络）
6. **处理审查意见**：Codex会列出问题，逐条修复后重新跑自测
7. **提交**：全部通过后才提交代码

### 执行规则
1. 默认直接改代码并自测，除非用户要求"只给方案"。
2. 一次只处理一个目标问题（small patch）。
3. 只允许修改用户指定文件；未授权文件禁止改动。
4. 未完成验收命令前，不得宣称完成。
5. 涉及多文件联动、删除功能、改数据库结构时，先报告方案等用户确认。

### 改完代码后输出
1. 改了什么（文件 + 关键改动）
2. 验收命令与结果
3. 风险点（有真实风险时才写，无风险不用凑）

### 质量门禁与放行条件

质量门禁（全部必须通过）：
1. 语法/启动通过。
2. 回归不退化（关键指标不得下降）。
3. 异常有兜底，不允许静默返回空结果。
4. 兼容旧数据/旧索引/旧库结构。

满足以下**全部条件**时可自动放行，否则必须停下来等用户确认：
1. 只改了用户指定的文件。
2. 质量门禁全部通过。
3. 无P0/P1问题。
4. 改动行数 ≤ 200行（超过需主动说明）。

### 问题严重等级
- **P0 致命**：系统无法启动/核心流程崩溃/数据损毁 → 必须立即修复
- **P1 严重**：功能明显错误/准确率大幅下降 → 当次任务内修复，未修复视为失败
- **P2 一般**：边缘case/体验问题 → 记录待修，不阻塞当前任务

## 核心架构

### 两层知识体系

```
通用知识库（全国共享）                    经验库（省份专属）
  清单模式 → 定额名称模式                  清单文本 → 当地定额编号
  "给水管道DN25" → "管道安装+管卡+试压"    "给水管道DN25" → 北京C5-1-10
```

### 两层数据质量机制

```
权威层（Ground Truth）：只存用户确认/修正的数据
  → 用于直通匹配、few-shot参考、评测基准

候选层（Suggestion Cache）：存系统自动匹配和外部导入数据
  → 不参与直通，不涨分，用户确认后才"晋升"到权威层
```

### 匹配流程

```
清单 → ⓪清单清洗(名称修正+专业分类+参数提取)
     → ①查经验库(同省)直通 → ②查通用知识库获取搜索提示
     → ③级联搜索(主专业→借用专业→全库) × (BM25+向量)
     → ④参数验证 → ⑤大模型精选(可选)
     → ⑥低置信度(<85%)自动触发多Agent纠偏审核
     → ⑦输出定额编号
```

### 12大册专业分类

定额按编号前缀分为12册：C1机械设备、C4电气、C5智能化、C7通风空调、C8工业管道、C9消防、C10给排水、C12刷油防腐等。搜索时先按清单的专业分类在对应册内搜索，结果不够再扩展到借用专业和全库。

## 关键设计决策

1. **不依赖清单编码**：完全基于文字语义匹配，编码仅供参考
2. **经验库只存人工验证数据**：杜绝auto_match污染
3. **一省通全国**：通用知识库存定额名称模式（不存编号），跨省复用
4. **定额版本绑定**：定额库更新后旧经验自动标记stale

## 大模型配置（.env文件）

Agent模式需要大模型API。在 `.env` 文件中配置，支持多个模型切换。

### Claude（当前默认，通过中转服务）

```bash
# 变量名用 CLAUDE_ 前缀（不能用 ANTHROPIC_，会和 Claude Code 自身环境变量冲突）
CLAUDE_API_KEY=你的中转API Key
CLAUDE_BASE_URL=http://你的中转地址:端口    # 留空则走官方API
CLAUDE_MODEL=claude-opus-4-6               # 可选: claude-opus-4-6, claude-sonnet-4-6

# Agent模式切换为Claude
AGENT_LLM=claude
```

**为什么不用 ANTHROPIC_ 前缀？**
Claude Code（就是这个开发工具）会在系统环境变量中设置 `ANTHROPIC_API_KEY` 和 `ANTHROPIC_BASE_URL`。
如果 `.env` 也用这个名字，`load_dotenv()` 不会覆盖已有环境变量，导致读到 Claude Code 的值而非中转的值。
所以用 `CLAUDE_` 前缀避免冲突。

**中转模式的技术细节：**
中转模式下不走 Anthropic SDK（SDK 会自动添加 `authorization: Bearer PROXY_MANAGED` 头导致中转认证失败），
而是用 httpx 直接发送 HTTP 请求，只带 `x-api-key` 头。代码在 `src/agent_matcher.py` 的 `_call_claude()` 方法。

### Kimi（备选）

```bash
KIMI_API_KEY=你的DashScope API Key
KIMI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
KIMI_MODEL=kimi-k2.5

AGENT_LLM=kimi
```

### 其他支持的模型

- `deepseek`：DeepSeek（需 DEEPSEEK_API_KEY）
- `qwen`：通义千问（需 QWEN_API_KEY）
- `openai`：OpenAI GPT（需 OPENAI_API_KEY）

切换方法：修改 `.env` 中的 `AGENT_LLM=模型名` 即可。

## 测试文件位置

测试用的清单Excel文件统一放在D盘：
- `D:\广联达临时文件\2025\` — 2025年项目
- `D:\广联达临时文件\2026\` — 2026年项目

常用测试文件：
- 公厕给排水：`D:\广联达临时文件\2025\2025.11.15-文浩-门卫、公厕\公厕-给排水-小栗AI自动编清单202511152049.xlsx`
- 7#配套楼：`output\temp\7#配套楼-小栗AI自动编清单202602072236.xlsx`
