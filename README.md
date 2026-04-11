# 自动套定额系统

根据工程量清单，自动匹配定额子目。

## OpenClaw 桥接快速联调

Windows PowerShell：

```powershell
$env:OPENCLAW_API_KEY = "你的X-OpenClaw-Key"
powershell -ExecutionPolicy Bypass -File .\tools\openclaw_bridge_smoke.ps1
```

可选带任务查看审核列表：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\openclaw_bridge_smoke.ps1 -TaskId "你的task_id"
```

可选拉取桥接 OpenAPI：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\openclaw_bridge_smoke.ps1 -IncludeOpenApi
```

## OpenClaw 审核写链路最小脚本

保存审核建议（review-draft）：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\openclaw_bridge_review.ps1 `
  -Action review-draft `
  -TaskId "你的task_id" `
  -ResultId "你的result_id" `
  -QuotaId "03-10-3-42" `
  -QuotaName "法兰阀门安装 公称直径100mm以内" `
  -QuotaUnit "个" `
  -QuotaSource "search" `
  -ParamScore 0.88 `
  -RerankScore 0.88 `
  -ReviewNote "OpenClaw 建议改判" `
  -ReviewConfidence 88 `
  -DecisionType "agree"
```

注意：

- `review-draft` 走 `PUT /api/openclaw/tasks/{task_id}/results/{result_id}/review-draft`
- `DecisionType` 必须使用后端认可值：`agree` / `override_within_candidates` / `retry_search_then_select` / `candidate_pool_insufficient` / `abstain`
- `QuotaItem` 建议完整传 `quota_id / name / unit / source / param_score / rerank_score`
- 当前懒猫部署里的 `OPENCLAW_API_KEY` 可在 `lzc-manifest.yml` 查到，前端是验收面，接口才是执行面

人工二次确认（review-confirm）：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\openclaw_bridge_review.ps1 `
  -Action review-confirm `
  -TaskId "你的task_id" `
  -ResultId "你的result_id" `
  -Decision approve `
  -ReviewNote "人工确认通过"
```

## OpenClaw 批量写 review-draft

先 dry-run 看命中哪些条：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\openclaw_bridge_batch_review.ps1 `
  -TaskId "你的task_id" `
  -LightStatus yellow `
  -Limit 5 `
  -QuotaId "03-10-3-42" `
  -QuotaName "法兰阀门安装 公称直径100mm以内" `
  -QuotaUnit "个" `
  -WhatIf
```

确认没问题再正式写入：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\openclaw_bridge_batch_review.ps1 `
  -TaskId "你的task_id" `
  -LightStatus yellow `
  -Limit 5 `
  -QuotaId "03-10-3-42" `
  -QuotaName "法兰阀门安装 公称直径100mm以内" `
  -QuotaUnit "个"
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 一键匹配（推荐）

```bash
python tools/jarvis_pipeline.py "清单.xlsx" --province "北京2024"
```

自动完成：匹配定额 → 自动审核 → 纠正Excel，结果在 `output/` 目录下。

### 3. 单独匹配（备用）

```bash
# 纯搜索模式（免费，不需要API Key）
python main.py 清单文件.xlsx --mode search

# Agent模式（造价员贾维斯，需要API Key）
python main.py 清单文件.xlsx --mode agent
```

### 4. 查看结果

输出文件在 `output/` 目录下，格式匹配广联达标准，可直接导入。

## 系统体检与代码审查（统一入口）

推荐使用根目录批处理：

```bat
系统体检.bat
```

进入菜单后可选：

- 快速检查（语法 + 导入 + 回归）
- 全量检查（快速 + 全量 pytest + 数据库 schema + 经验库体检）
- CI 门禁检查（严格）
- 代码审查（`codex review --uncommitted`）
- 全量检查 + 代码审查

也可以直接批处理执行（便于做批任务/计划任务）：

```bat
系统体检.bat quick
系统体检.bat full
系统体检.bat ci
系统体检.bat review
系统体检.bat all
```

检查报告会输出到：

- `output/health_reports/health_<mode>_<timestamp>.json`
- `output/health_reports/health_<mode>_<timestamp>.md`

## 项目结构

```
auto-quota/
├── main.py              # 主程序入口
├── config.py            # 配置文件（置信度阈值、路径等）
├── .env                 # API密钥（不要泄露！不会上传git）
├── requirements.txt     # Python依赖
├── src/                 # 源代码（核心逻辑）
│   ├── bill_reader.py       # 读取清单Excel
│   ├── text_parser.py       # 从文字中提取参数（DN、材质、连接方式等）
│   ├── hybrid_searcher.py   # 混合搜索引擎（BM25 + 向量搜索）
│   ├── bm25_engine.py       # BM25关键词搜索
│   ├── vector_engine.py     # BGE向量语义搜索
│   ├── param_validator.py   # 参数验证（管径、材质匹配检查）
│   ├── llm_verifier.py      # LLM 后验证与自动纠正（需 API Key）
│   ├── output_writer.py     # 生成结果Excel（广联达可导入格式）
│   ├── experience_db.py     # 经验库（越用越准）
│   ├── feedback_learner.py  # 用户修正学习
│   └── quota_db.py          # 定额数据库管理
│
├── tests/               # pytest 测试用例
│   └── test_*.py            # 按模块分组的回归/单测
│
├── data/                # 数据文件
│   ├── quota_data/          # 定额Excel源文件
│   └── dict/                # 专业词典（jieba分词用）
│
├── db/                  # 数据库文件（自动生成，不要手动改）
│   ├── quota.db             # 定额数据库
│   ├── experience.db        # 经验库
│   └── chroma_db/           # 向量索引
│
├── output/              # 输出结果（每次匹配生成一个Excel）
├── tools/               # 批处理工具
│   ├── jarvis_pipeline.py    # 一键全流程（匹配+审核+纠正）
│   ├── jarvis_auto_review.py # 自动审核
│   ├── jarvis_correct.py     # 纠正写回Excel
│   ├── jarvis_store.py       # 存入经验库
│   ├── experience_view.py    # 经验库查看/搜索
│   ├── import_all.py         # 导入定额数据
│   └── import_reference.py   # 导入预算数据
├── knowledge/           # 知识库文件
├── logs/                # 运行日志
└── docs/                # 文档
```

## 重要注意事项

### 输出格式（广联达导入）

输出Excel的列A-I完全匹配广联达"云计价分部分项清单带定额表"格式：

- **清单行**：A列有序号数字，B列是12位项目编码
- **子目行（定额行）**：A列为空，B列是定额编号（如 C10-2-123）
- **金额列（G-I）**：留空，导入广联达后软件自动计算
- **扩展列（J-K）**：置信度和匹配说明，广联达会忽略，不影响导入

广联达靠A列是否有数字来区分清单行和子目行，所以**定额行的A列必须为空**。

### 匹配模式

| 模式 | 命令参数 | 需要API Key | 费用 | 精度 |
|------|---------|------------|------|------|
| 纯搜索 | `--mode search` | 不需要 | 免费 | 一般 |
| 贾维斯 | `--mode agent` | 需要 | 约几分钱/条 | 最高 |

### 经验库

- 系统会自动把高置信度的匹配结果存入经验库
- 下次遇到相似清单，直接从经验库取结果（免费且快）
- 用 `--no-experience` 参数可关闭经验库
- 经验库越积累，匹配越准确

### 文件安全

- `.env` 里存着API密钥，**绝对不要分享给别人**
- `db/` 目录下的数据库文件是自动生成的，**不要手动修改**
- `data/quota_data/` 里的定额Excel是源数据，**不要删除**

### 测试和调试

- 所有测试脚本放在 `tests/` 目录下，**不要放在根目录**
- 推荐使用 `pytest` 或按模块运行 `pytest tests/<module>`
- 测试需要标准文件：`云计价分部分项清单带定额表.xlsx`（从广联达导出的带正确定额的清单）

## 常用命令

```bash
# 一键全流程（推荐）
python tools/jarvis_pipeline.py "清单.xlsx" --province "北京2024"

# 单独匹配
python main.py 清单文件.xlsx

# 只处理前10条（快速测试）
python main.py 清单文件.xlsx --limit 10

# 指定输出路径
python main.py 清单文件.xlsx -o 结果.xlsx

# 不使用经验库
python main.py 清单文件.xlsx --no-experience

# 查看帮助
python main.py --help
```
