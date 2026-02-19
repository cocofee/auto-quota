# 自动套定额系统

根据工程量清单，自动匹配定额子目。

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 启动Web界面（推荐）

```bash
streamlit run app.py
```

浏览器会自动打开 `http://localhost:8501`，在网页上操作：
- **匹配定额**：拖拽上传清单Excel → 选择匹配模式 → 一键匹配 → 下载结果
- **定额数据库**：查看/搜索/导入定额
- **经验库**：查看历史匹配记录和统计
- **设置**：配置API密钥、查看系统状态

### 3. 命令行方式（备用）

```bash
# 纯搜索模式（免费，不需要API Key）
python main.py 清单文件.xlsx --mode search

# 完整模式（需要在 .env 中配置 API Key，精度更高）
python main.py 清单文件.xlsx --mode full
```

### 4. 查看结果

输出文件在 `output/` 目录下，格式匹配广联达标准，可直接导入。

## 项目结构

```
auto-quota/
├── main.py              # 主程序入口
├── config.py            # 配置文件（置信度阈值、路径等）
├── .env                 # API密钥（不要泄露！不会上传git）
├── requirements.txt     # Python依赖
├── 运行匹配.bat          # 双击运行的快捷方式
│
├── src/                 # 源代码（核心逻辑）
│   ├── bill_reader.py       # 读取清单Excel
│   ├── text_parser.py       # 从文字中提取参数（DN、材质、连接方式等）
│   ├── hybrid_searcher.py   # 混合搜索引擎（BM25 + 向量搜索）
│   ├── bm25_engine.py       # BM25关键词搜索
│   ├── vector_engine.py     # BGE向量语义搜索
│   ├── param_validator.py   # 参数验证（管径、材质匹配检查）
│   ├── llm_matcher.py       # 大模型精选（需API Key）
│   ├── output_writer.py     # 生成结果Excel（广联达可导入格式）
│   ├── experience_db.py     # 经验库（越用越准）
│   ├── feedback_learner.py  # 用户修正学习
│   └── quota_db.py          # 定额数据库管理
│
├── tests/               # 测试脚本（调试用的代码放这里）
│   └── test_accuracy.py     # 准确率测试（对比广联达标准答案）
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
├── app.py               # Streamlit Web界面入口（streamlit run app.py）
├── pages/               # Streamlit多页面
│   ├── 1_匹配定额.py       # 核心功能：上传清单→匹配→下载结果
│   ├── 2_定额数据库.py     # 查看/搜索/导入定额
│   ├── 3_经验库.py         # 历史匹配记录和统计
│   └── 4_设置.py           # API配置、系统信息
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
| 完整模式 | `--mode full` | 需要 | 约几分钱/条 | 更高 |

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
- 准确率测试：`python tests/test_accuracy.py`
- 测试需要标准文件：`云计价分部分项清单带定额表.xlsx`（从广联达导出的带正确定额的清单）

## 常用命令

```bash
# 启动Web界面（推荐方式）
streamlit run app.py

# 命令行匹配（备用）
python main.py 清单文件.xlsx

# 只处理安装专业的清单（编码以03开头）
python main.py 清单文件.xlsx --filter-code 03

# 只处理前10条（快速测试）
python main.py 清单文件.xlsx --limit 10

# 指定输出路径
python main.py 清单文件.xlsx -o 结果.xlsx

# 不使用经验库
python main.py 清单文件.xlsx --no-experience

# 查看帮助
python main.py --help
```
