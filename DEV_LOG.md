# 自动套定额系统 - 开发日志

每天记录做了什么、效果如何、下一步方向。

---

## 2026-02-18（HVAC参数提取 + 贾维斯审核 + 经验库纠正）

### 做了什么

**1. 周长/大边长参数提取（通风空调专用）**
- `src/text_parser.py`：
  - 新增 `_extract_perimeter()`：从规格"W*H"计算周长=(W+H)×2，用于调节阀/风口/消声器等按周长取档的定额
  - 新增 `_extract_large_side()`：从规格"W*H"提取大边长=max(W,H)，用于弯头导流叶片等按大边长取档的定额
  - 修复电缆截面误提取：规格"800*500"不再被当成截面
- `src/param_validator.py`：
  - 新增周长校验（section 8）和大边长校验（section 9）
  - 两者均加入 TIER_PARAMS 列表参与打分

**2. 运行匹配.bat 重写为交互循环**
- 省份和模式只选一次，然后循环匹配多个文件
- 匹配完成后有操作菜单：
  - [1] 打开结果文件
  - [2] 导入修正（对比学习）
  - [3] 重新匹配当前文件
  - [4] 匹配新文件
  - [5] 贾维斯审核（Agent模式重跑）
  - [m] 切换模式  [q] 退出

**3. 贾维斯审核工具（Claude Code直接参与审核）**
- 新建 `tools/jarvis_store.py`：
  - 支持单条存入（--name/--quota-ids）、批量存入（--file）、查询（--lookup）
  - 存入经验库权威层（source=user_confirmed, confidence=95）
- 工作流：Claude Code 读审核结果 → 识别错误 → 生成纠正JSON → 存入经验库

**4. 第一轮贾维斯审核（7#配套楼 95条通风空调）**
- 审核全部5批95条结果，识别出13条明确错误
- 错误类型：
  - 电动对开多叶调节阀（5条）：错套"带调节阀百叶风口/三通调节阀"→ 应套"多叶调节阀安装"
  - 单层百叶风口（6条）：错套"带调节阀百叶风口"→ 应套"百叶风口安装"（单层不带调节阀）
  - 双层百叶风口（2条）：同上
- 13条纠正全部存入经验库（id=1328~1340）

### 测试结果

| 阶段 | 绿色 | 黄色 | 红色 | 经验库命中 |
|------|------|------|------|-----------|
| 基准（无经验库） | 48 | 42 | 5 | 0 |
| 存入13条纠正后 | **56** | **38** | **1** | **14** |

- 绿色 +8（48→56），红色 -4（5→1）
- 经验库命中14条（13条纠正 + 1条近似匹配）
- 1条经验库命中被参数校验拦截（止回阀DN50→C10-5-45，跨专业）

### 待后续审核的项目（需用户专业判断）

以下约29条需要造价人员确认：
- 碳钢通风管道（材质空）：δ=1.2mm咬口 还是 δ=2mm焊接？
- 柔性软风管（大边长规格）：应该套矩形柔性软管还是圆形？
- 风机盘管 FP系列：卡式嵌入型 还是 吊顶式？
- 轴流风机 7#：档位应该取多大？
- 消声器：管式 / 阻抗复合式 / 微穿孔板 哪种？
- 事故风机手动开关、一般填料套管、开孔打洞：完全匹配错误，需人工指定

### 改动文件

| 文件 | 改动 |
|------|------|
| src/text_parser.py | 新增 _extract_perimeter() + _extract_large_side() + 电缆截面误提取修复 |
| src/param_validator.py | 新增周长校验(section 8) + 大边长校验(section 9) |
| 运行匹配.bat | 重写为交互循环 + 操作菜单 + 贾维斯审核选项 |
| tools/jarvis_store.py | 新建：贾维斯纠正存入工具 |
| output/temp/jarvis_corrections.json | 新建：13条纠正数据 |

---

## 2026-02-17（P0/P1修复补记）

### 做了什么

**1. 省份上下文透传修复（防止跨省串库）**
- `main.py`：`try_experience_match/match_search_only/match_full/match_agent` 新增 `province` 参数并全链路透传
- `src/llm_matcher.py`：规则检索与Prompt使用运行时省份，不再写死“北京2024”
- `src/agent_matcher.py`：Prompt使用运行时省份
- `src/experience_db.py:get_reference_cases()` 支持按省份取参考案例

**2. 经验库定额校验修复（按省加载quota map）**
- `src/experience_db.py`：
  - `_get_quota_map()` 改为按省份缓存
  - `_validate_quota_ids()` 增加 `province` 参数
  - `add_experience()` 校验时传入 `province`

**3. add_experience“假成功计数”修复**
- 只有 `record_id > 0` 才记成功，避免把被校验拦截（返回 `-1`）也算入成功
- 修复文件：
  - `pages/1_匹配定额.py`
  - `tests/eval_golden.py`
  - `tools/import_reference.py`
  - `tools/agent_review.py`
  - `tools/batch_fix_experience.py`
  - `src/experience_db.py:import_from_project`

**4. 页面/工具链稳定性修复**
- `pages/1_匹配定额.py`：匹配结果临时JSON改为唯一文件名，避免并发覆盖
- `pages/2_定额数据库.py`：`search_by_keyword(..., book=...)` 改为 `search_by_keywords(..., book=...)`
- `pages/4_设置.py`：保存API配置改为“定向更新.env键值”，保留原有注释和其他键
- `tests/eval_golden.py`：
  - `compare_last` 先对比上次结果，再保存本次结果（修复回归对比时序）
  - `bill_desc` 键名修正为 `bill_description`
- `tools/llm_compare_test.py`：修复 `"不一致"` 被 `"一致" in ...` 误判为一致的问题
- `tools/migrate_experience_dedup.py`：去重分组改为 `(province, bill_text)`，避免跨省误合并

**5. 学习模块写入拦截可见化**
- `src/feedback_learner.py`：修正条目统计仅在 `record_id > 0` 时累计，拦截时打 warning
- `src/diff_learner.py`：对 `add_experience` 返回值做校验，写入被拦截时落 warning（避免静默失败）
- `src/feedback_learner.py`：`learn_from_corrected_excel()` 改为仅在 `_save_bill_quota_pair()` 成功时累计 `learned`

**6. 残留问题修复（pytest收集与脚本CLI）**
- `tests/test_accuracy.py`：改为 `main()` 入口 + argparse，移除导入即执行副作用
- `tests/debug_sheets.py`：改为 `main()` 入口 + argparse，移除导入即执行副作用
- `tools/migrate_experience_dedup.py`：补充 argparse，支持标准 `--help`
- `tools/llm_compare_test.py`：补充 argparse，支持 `--help` 与路径参数覆盖
- 验证：`pytest --collect-only -q` 从“超时卡死”恢复为 0.33s 返回（当前无单元测试用例，`no tests collected`）

### 验收

- `python -m py_compile` 通过：
  - `main.py`
  - `src/experience_db.py`
  - `src/llm_matcher.py`
  - `src/agent_matcher.py`
  - `pages/1_匹配定额.py`
  - `pages/2_定额数据库.py`
  - `pages/4_设置.py`
  - `tests/eval_golden.py`
  - `tools/import_reference.py`
  - `tools/agent_review.py`
  - `tools/batch_fix_experience.py`
  - `tools/llm_compare_test.py`
  - `tools/migrate_experience_dedup.py`
  - `src/feedback_learner.py`
  - `src/diff_learner.py`

### 备注

- 按沟通要求，本次没有启用/恢复多Agent触发流程，只修复已发现错误和一致性问题。

---

## 2026-02-18（全量代码审查 + P1修复）

### 做了什么

**全量代码审查（46个文件，4批）**

按优先级分4批审查了全部Python代码：

| 批次 | 范围 | 文件数 | P0 | P1 | P2 |
|------|------|--------|----|----|-----|
| 第1批 | 核心匹配路径（text_parser/搜索/验证/规则） | 9 | 0 | 0 | 3 |
| 第2批 | 知识库+匹配引擎（经验库/LLM/Agent/main） | 7 | 0 | 1 | 5 |
| 第3批 | 数据IO+学习机制（reader/writer/learner） | 7 | 0 | 1 | 3 |
| 第4批 | Web界面+工具脚本（pages/tools/tests） | 23 | 0 | 0 | 3 |
| **合计** | | **46** | **0** | **2** | **14** |

**P1修复（2个，已完成）**

**P1-1: match_full/match_agent 缺 exp_backup 对比逻辑 [main.py]**
- 问题：match_search_only 区分经验库精确/相似匹配，相似匹配暂存后和搜索结果比较取高分。但 match_full 和 match_agent 对所有经验库结果一律直通，跳过了搜索+LLM，可能选更差的结果
- 修复：三个 match 函数统一行为——精确匹配直通，相似匹配走 exp_backup 比较逻辑

**P1-2: output_writer._write_quota_rows() 方法不存在 [src/output_writer.py]**
- 问题：兜底模式（无原始文件时新建工作簿）调用了不存在的 _write_quota_rows()，运行会崩溃
- 修复：补写该方法，循环调用 _write_single_quota_row，无匹配时写红色提示行
- 未触发原因：正常使用都传入原始文件走 _write_preserve_structure 路径

### P2问题清单（14个，记录备查，不阻塞）

**重复代码（可后续重构）**
1. `_extract_json()` 在 llm_matcher / agent_matcher / multi_agent_review 中重复3份
2. `_create_client()` 在 llm_matcher / agent_matcher 中重复2份，且 llm_matcher 缺 qwen 支持
3. `MEASURE_KEYWORDS` + 措施费跳过逻辑在 main.py 三个 match 函数中重复3份
4. 表头检测逻辑在 bill_reader / diff_learner / output_writer 中重复3份

**不一致**
5. text_parser 和 rule_validator 的 DE→DN 换算表不一致
6. param_validator 和 rule_validator 的连接方式兼容性检查词组不一致

**逻辑/数据问题**
7. feedback_learner.get_accuracy_stats() 统计逻辑与当前数据模型不匹配（auto_match已清空）
8. experience_db.py 第835行模块级单例未被使用
9. multi_agent_review.py 整个文件已不被调用（482行死代码）

**边界/安全**
10. text_parser power exclusion regex `\b` 在中文字符边界可能不生效
11. pages/1_匹配定额.py unsafe_allow_html 渲染用户数据（本地工具风险极低）
12. pages/1_匹配定额.py 上传文件名未 sanitize（本地工具风险极低）

**硬编码/死文件**
13. tools/ 和 tests/ 约10个文件含硬编码绝对路径，换机器跑不了
14. tools/analyze_nongreen.py 仅1行 `# placeholder`，空文件

### 改动文件

| 文件 | 改动 |
|------|------|
| main.py | match_full/match_agent 增加 exp_backup 精确/相似区分+对比逻辑（+51行，-13行） |
| src/output_writer.py | 补写 _write_quota_rows() 方法（+36行） |

### 验收结果

- py_compile 语法检查：main.py ✅ output_writer.py ✅
- 三个 match 函数均含 exp_backup 变量 ✅
- OutputWriter._write_quota_rows 方法存在 ✅

---

## 2026-02-18（Codex审查 + 系统级修复）

### 做了什么

**1. Codex 5.3 代码审查机制建立**
- 安装 codex-cli 0.101.0，配置 `.codex/instructions.md` 审查标准
- 创建 `代码审查.bat` 一键运行 `codex review --uncommitted`
- 问题：Codex CLI 连 api.openai.com 经常断连（国内网络不稳），成功率约50%
- 工作流：改代码 → 跑审查 → 有问题修 → 通过后提交 → 下次只审查新改动

**2. 多轮Codex审查发现并修复的问题**

| 轮次 | 问题 | 等级 | 修复 |
|------|------|------|------|
| 第1轮 | quota_db.py init_db() 在旧库上建idx_book报错 | P0 | PRAGMA table_info检查+ALTER TABLE |
| 第1轮 | bm25/vector SELECT book旧库报错 | P0 | has_book_col检测+降级查询 |
| 第1轮 | quota_db.py _parse_row缺book字段 | P1 | 从quota_id前缀提取book |
| 第1轮 | text_parser.py _is_panel_size误判电缆 | P1 | 改用标准截面集合 |
| 第1轮 | int(cable_section)截断2.5→2 | P1 | 保留小数格式 |
| 第2轮 | book列存在但值为空不回填 | P1 | WHERE改为 `book IS NULL OR book = ''` |
| 第2轮 | BM25按册过滤空结果无条件降级全库 | P1 | 区分旧索引和正常空结果，不跨专业 |
| 第2轮 | 向量搜索ChromaDB metadata过滤异常 | P1 | try/except + 旧索引检测(peek 10条) |
| 第2轮 | config.py copytree迁移无异常保护 | P2 | try/except + 回退旧路径 |
| 第3轮 | 电缆带material走管道分支 | High | is_electrical判断跳过管道分支 |
| 第3轮 | match_full/agent缺措施费跳过 | High | 三模式统一measures skip逻辑 |
| 第3轮 | peek(1)误判旧索引 | Medium | 改为peek(10)多条采样 |
| 第4轮 | 措施费跳过结果缺bill_item | P1 | result schema与search模式统一 |

**3. 经验库审计与清理**
- 发现91条quota_ids带后缀（` 换`、` *1.15`）→ 清理
- 发现87条参数不匹配（77条配电箱→接线箱、4条回路浪费等）→ 修正
- 703条 project_import 记录全部从权威层降级到候选层
- 新增 `_validate_quota_ids()` 方法（6项验证），防止未来导入脏数据

**4. 关联定额同类过滤**
- 问题：一条电缆清单出3个敷设定额（沿桥架70+穿导管70+沿桥架120）
- 根因：大模型把同类不同方式当"关联定额"返回
- 修复：Prompt明确约束 + 代码过滤同册同章节的关联定额

**5. 电气材料代码系统修复**
- conduit_map修正：PC→"PVC阻燃塑料管"（原来错成"刚性难燃线管"）
- wire_map补全：新增BYJ/BLV/RVVP/RVV等型号
- 前缀剥离：WDZB1N-BYJ4 → 剥离WDZB1N- → BYJ4 → 匹配"管内穿铜芯线"
- 新增G/DG(镀锌钢管)、RC/MT(镀锌电线管)配管代码

**6. 日志写入文件**
- 之前loguru只输出到终端，关窗口就没了
- 现在写入 `logs/auto_quota_YYYY-MM-DD.log`，按天轮转保留30天

### 改动文件

| 文件 | 关键改动 |
|------|---------|
| config.py | copytree异常保护+回退旧路径 |
| main.py | 措施费跳过三模式统一(bill_item schema) + 日志文件写入 |
| src/quota_db.py | book回填覆盖NULL和空字符串 |
| src/bm25_engine.py | 按册过滤降级区分旧索引/正常空结果 |
| src/vector_engine.py | ChromaDB过滤异常保护 + peek(10)旧索引检测 |
| src/text_parser.py | 电气is_electrical判断 + conduit_map/wire_map修正 |
| src/llm_matcher.py | 关联定额同类过滤(同册同章节) |
| src/agent_matcher.py | 关联定额同类过滤 + Prompt约束 |
| src/experience_db.py | _validate_quota_ids() 6项验证 + project_import降级候选层 |
| .codex/instructions.md | Codex审查标准(P0/P1/P2) |
| CLAUDE.md | 审查提醒 + 协议更新 |
| 代码审查.bat | Codex 5.3一键审查 |

### 当前状态

- **核心匹配逻辑**：经过4轮Codex审查，主要兼容性和逻辑问题已修复
- **经验库**：703条project_import已降级候选层，新增导入验证防止脏数据
- **日志**：已写入文件，可追溯问题
- **按册过滤**：严守专业边界，不再无条件降级全库污染候选池
- **关联定额**：同类过滤已加，不会再出一条电缆3个敷设定额

### 待解决

1. **P2: test_accuracy.py 硬编码路径** — 测试文件用绝对路径，其他机器跑不了
2. **电气匹配准确率** — conduit_map/wire_map修正后需要重跑华佑电气系统验证
3. **电缆"综合考虑"敷设方式** — 清单写"综合考虑"时，应该默认选哪种敷设？需要业务规则
4. **Codex CLI 稳定性** — 国内连OpenAI API不稳定，成功率约50%，考虑备用方案

### 做了什么

**1. 系统性扫描工具运行**
- 对engineering_dict、extracted_vocab、材质兼容性、BM25分词、query构建做全面扫描
- 输出26KB详细报告（output/temp/systematic_scan.txt）

**2. search_query重复bug修复**
- 问题："碳钢通风管道" → query变成"碳钢碳钢通风管道"（材质重复拼接）
- 根因：text_parser.py第509行 `core = material + name`，没检查name是否已包含material
- 修复：增加 `if material in name` 判断，已包含则不重复拼接

**3. 工程词典再清理（3个严重+2个中等问题）**
- 删除"管内穿铜芯线动力线路"（10字整体token），拆为"管内穿铜芯线"+"动力线路"+"照明线路"
- 删除"配电箱墙上明装"/"配电箱嵌入式安装"/"配电箱落地安装"（让jieba自然拆分为"配电箱"+"墙上明装"等）
- 删除"钢板矩形风管"和"不锈钢板矩形风管"（让jieba拆为"钢板"+"矩形风管"）
- 添加"PVC管道"词条（避免被拆为"PVC管"+"道"）

**4. 材质兼容性扩展**
- 新增"玻璃钢族": ["玻璃钢", "玻璃钢管", "FRP管", "FRP"]
- 碳钢板族加入"镀锌钢板"
- 钢塑族加入"涂塑碳钢管"、"热浸塑钢管"
- 铜族加入"铜"、"紫铜管"、"黄铜管"

**5. extracted_vocab过滤增强**
- equipment_words增加：穿导管、穿放、顶管、钻孔、网管、风机盘管、安装等

### 测试结果

| 文件 | 改进前 | 改进后 | 关键变化 |
|------|--------|--------|---------|
| 6#配套楼 (137条) | 绿74 黄38 红25 | **绿88 黄40 红9** | 16条碳钢通风管道从C12→C7 ✅ |

### 改动文件

| 文件 | 改动 |
|------|------|
| src/text_parser.py | query重复bug修复 + equipment_words扩展 |
| data/dict/engineering_dict.txt | 删除5条整体token + 添加4条子词 |
| src/param_validator.py | 4个材质族扩展 |

---

## 2026-02-16 夜间（第二轮改进）

### 做了什么

**1. BM25分词修复（根因：工程词典污染）**
- **问题**：engineering_dict.txt 中5017条词条有3446条（69%）是完整定额名称
  - 例："薄钢板通风管 5 n" 让jieba分词错误：'薄钢板通风管道制作'→['薄钢板通风管','道','制作']
  - 导致查询中的"通风管道"无法匹配到索引中的碎片token
- **修复**：清理词典 5017→1470条，补充51个专业词汇
- **效果**：BM25在C7范围内从0个结果变为正确返回10个通风管道定额

**2. 材质提取修复（根因：extracted_vocab.txt 污染）**
- **问题**：extracted_vocab.txt [materials]部分有38个含"风管"等设备名的错误词条
  - 例："薄钢板通风管"作为材质 → 参数验证判定 碳钢≠薄钢板通风管 → C7定额全部fail
- **修复**：
  - 清理 extracted_vocab.txt（删除38个错误材质）
  - text_parser.py 添加 equipment_words 兜底过滤
  - 基础材质列表添加：薄钢板、镀锌钢板、不锈钢板、钢板、玻璃钢

**3. 材质兼容性扩展**
- 新增碳钢板族：["碳钢", "薄钢板", "钢板", "钢板制"]
- 焊接钢族添加碳钢管
- 泛称映射新增："钢板" → ["薄钢板", "镀锌钢板", "不锈钢板", "碳钢板"]

**4. 负向关键词扩展 + 同义词机制**
- 新规则：绝热(罚0.3)、保温(罚0.3)、人防(罚0.3)
- 同义词：保温≈绝热互不惩罚
- 效果：通风管道→C12绝热 被正确识别为错误匹配

**5. 回退候选置信度修复**
- 问题：rule_validator 强制 confidence=75 覆盖了回退候选的低分
- 修复：检测"回退候选"标记，限制最高55分

### 测试结果

| 文件 | 改进前 | 改进后 | 说明 |
|------|--------|--------|------|
| 6#配套楼 (137条) | 绿74 黄38 红25 | 待重测 | 16条碳钢通风管道 + 4条弯头导流叶片 |
| A6 7#配套楼 (95条) | 绿38 黄35 红22 | 待重测 | |

### 改动文件

| 文件 | 改动 |
|------|------|
| data/dict/engineering_dict.txt | 清理：5017→1470条 |
| data/dict/extracted_vocab.txt | 删除38个错误材质 |
| src/text_parser.py | 基础材质扩展 + equipment_words兜底 |
| src/param_validator.py | 碳钢板族 + 负向关键词 + 同义词 |
| src/rule_validator.py | 回退候选置信度修复 |
| tools/clean_vocab.py | 新建：词汇清理脚本 |

### 待修复

1. search_query重复bug：碳钢碳钢通风管道
2. 系统性扫描结果待整合

---

## 2026-02-16（白天）

### 做了什么

**1. 添加 Reranker 重排模块**
- 新建 `src/reranker.py`，使用 `BAAI/bge-reranker-v2-m3` 模型（568M参数，2GB显存）
- 原理：交叉编码器把查询和候选拼在一起逐字对比，比向量搜索的"双塔模型"理解更深入
- 插入位置：混合搜索之后、参数验证之前
- 延迟加载：规则命中的项目不走搜索，不会触发模型加载

**2. 修复规则匹配误报（修饰词过滤）**
- 问题：灯具"嵌装射灯 嵌入安装"误匹配到"配电箱嵌入式安装"家族
- 原因：命中的关键词全是通用修饰词（"嵌入"、"安装"），没有核心名词（"配电箱"）
- 修复：在 `_match_by_param_driven()` 中增加检查，如果所有命中的关键词都是修饰词则跳过
- 新增 `GENERIC_MODIFIERS` 集合：安装/明装/暗装/嵌入/落地/敷设/连接/焊接等

**3. 修复截面提取误识别**
- 问题：灯具功率15W、面板尺寸600x600mm 被误识别为电缆截面
- 修复：`_extract_cable_section()` 增加三层排除（功率/二维尺寸/保留电缆格式）

**4. 增强电气类清单的搜索query构建**
- 问题：配管/配线/电缆类清单的描述信息（材质、规格、敷设方式）没有提取到搜索query中
- 改进 `build_quota_query()`：
  - 配管材质代号转换：PC→刚性难燃线管, SC→钢管, JDG→紧定式薄壁钢管
  - 配管规格提取：公称直径
  - 配置形式：暗敷→暗配, 明敷→明配
  - 导线型号：BV→硬绝缘导线管内穿线
  - 电缆型号：YJV→铜芯电力电缆敷设
  - 敷设方式：管道内/桥架/直埋等

**5. 找到D盘广联达数据用于测试**
- D:\广联达临时文件\ 下有45+个项目、1100+个Excel文件
- 找到有定额答案的项目（小栗AI自动加定额），可对比验证

### 测试数据

**丰台安置房 733条（纯搜索模式，无经验库）**

| 指标 | 数值 |
|------|------|
| 总清单 | 733 |
| 高置信度(绿) | 440 (60%) |
| 中置信度(黄) | 258 (35%) |
| 低置信度(红) | 35 (5%) |
| 规则命中 | 229 (31%) |
| 耗时 | 140秒 |

**广州监测站强电 40条（与小栗AI对比）**

| 评级 | 我们 | 小栗AI | 说明 |
|------|------|--------|------|
| A 正确 | 4 (10%) | 14 (37%) | 类型+参数都对 |
| B 接近 | 13 (32%) | 12 (32%) | 类型对,参数小偏差 |
| C 偏差 | 6 (15%) | 6 (16%) | 同类但明显偏差 |
| D 错类型 | 12 (30%) | 5 (13%) | 匹配到错误类型 |
| E 完全错 | 5 (12%) | 0 (0%) | 完全不相关 |
| A+B可用率 | **42%** | **70%** | |

**我们更好的地方：**
- 配管SC40暗配：我们DN40正确，小栗DN125错误
- 开关类：我们识别"跷板式"正确，小栗全部匹配到"扳式"

**我们更差的地方：**
- 穿线类（BV导线）、接线盒、送配电调试、微型电机：搜索找不到或匹配到不相关定额
- 插座类：缺少明/暗装区分

### 当前瓶颈

1. **搜索query信息不够**：电气类清单描述提取还不够完善（改进后配管好了，但穿线/插座还不行）
2. **搜索兜底太差**：找不到匹配时回退到"电缆沟挖填土"等不相关结果，不如返回空
3. **缺少经验库/通用知识库数据**：冷启动，全靠搜索

### 明天方向

- 继续改进 `build_quota_query`（插座明暗装、穿线类导线型号）
- 搜索无匹配时的兜底策略优化（宁可标红不匹配，也不返回不相关结果）
- 用更多项目测试（给排水、暖通），看不同专业的表现差异

---

## 2026-02-15

### 做了什么

**1. 规则匹配算法重写（核心改进）**
- 从纯关键词驱动改为**参数驱动匹配**
- 策略A（参数驱动）：先提取参数(回路/DN/kVA/截面等) → 找参数类型匹配的家族 → 关键词做辅助确认
- 策略B（关键词驱动）：兜底，纯关键词匹配（门槛高，需50%覆盖率）
- 效果：规则命中率从 **3%（23/750）→ 27%（208/750）**

**2. 经验库参数校验**
- 新增 `_validate_experience_params()`
- 防止经验库返回参数不匹配的结果（如7回路不再沿用4回路的经验）

**3. 删除多Agent审核**
- 原因：每条65秒、3次API调用、零质量提升
- 从 `match_full()` 中完全移除

**4. 清单清洗修复**
- `bill_cleaner.py`：修复型号替换bug（"成套配电箱"不再被型号"APE-Z"替换）
- 判断逻辑：中文字符不到一半 → 是型号，不替换

**5. 搜索query构建改进**
- 非管道类设备（配电箱/灯具/电缆等）从描述字段提取安装方式、回路数、敷设方式
- 新增 `_extract_description_fields()`：解析"N.标签:值"格式
- 新增 `_extract_circuits()`：提取回路数

**6. tokenize增强**
- 去除"标签:"格式（如"回路数:"、"安装方式:"）
- 避免无意义的标签文字干扰关键词匹配

### 测试数据（北京大学电教厅 750条）

| Section | 规则命中 | 说明 |
|---------|---------|------|
| 水阀门 | 51/53 (96%) | DN参数驱动 |
| 配电箱 | 43/48 (89%) | 回路参数驱动 |
| 给水 | 20/22 (90%) | DN参数驱动 |
| 水管道 | 23/27 (85%) | DN参数驱动 |
| 喷淋 | 17/29 (58%) | DN参数驱动 |
| 设备 | 13/38 (34%) | 容量参数驱动 |
| **总计** | **208/750 (27%)** | vs改进前3% |

---

## 2026-02-14

### 做了什么

**1. 专业分类模块** (`src/specialty_classifier.py`)
- 12大册定义：C1机械~C12刷油防腐
- 跨专业借用规则（C10给排水可借用C8工业管道等）
- `classify()` 函数：根据清单名称+描述+分部标题自动分类

**2. 按册搜索**
- `quota.db` 加 `book` 字段（从定额编号前缀提取）
- BM25 和向量搜索均支持 `books` 参数过滤
- 混合搜索透传 `books` 参数

**3. 清单数据清洗** (`src/bill_cleaner.py`)
- 分部标题行不再被当成清单项
- 名称修正（从描述中的"1.名称:xxx"提取真实名称）
- 自动打专业标签

**4. 级联搜索策略**
- 先在主专业范围内搜索 → 结果不够好则扩展到借用专业 → 最后全库兜底

---

## 更早（2026-02-13及之前）

### 已完成的核心模块

| 模块 | 说明 |
|------|------|
| `src/quota_db.py` | 定额数据库：安装定额11000+条，SQLite存储 |
| `src/vector_engine.py` | 向量搜索：BGE-large-zh-v1.5 + ChromaDB |
| `src/bm25_engine.py` | BM25关键词搜索：jieba分词+专业词典 |
| `src/hybrid_searcher.py` | 混合搜索：向量70%+BM25 30%，RRF融合 |
| `src/text_parser.py` | 参数提取：DN/截面/电流/材质/连接方式 |
| `src/param_validator.py` | 参数验证：数值档位匹配检查 |
| `src/llm_matcher.py` | 大模型精选：DeepSeek/Claude/GPT可切换 |
| `src/experience_db.py` | 经验库：两层机制(权威层+候选层) |
| `src/universal_kb.py` | 通用知识库：跨省份匹配知识 |
| `src/output_writer.py` | 输出：广联达可导入的Excel格式 |
| `src/rule_validator.py` | 规则匹配：从定额规则JSON自动匹配 |
| `src/reranker.py` | Reranker重排：交叉编码器语义精排 |
| Streamlit Web界面 | 匹配/审核/换定额/经验库管理 |
| 命令行入口 `main.py` | search/full两种模式 |

### 定额规则提取工具
- `tools/extract_quota_rules.py`：自动扫描103个章节，提取1740个家族的匹配规则
- 输出JSON规则文件供 `rule_validator.py` 使用

### 造价Home数据导入
- `tools/import_reference.py`：解析造价Home导出的Excel
- 支持拖拽导入和批量导入

---

## 整体进度追踪

| 日期 | 规则命中率 | A+B可用率(电气) | 关键改进 |
|------|-----------|----------------|---------|
| 02-14 | 3% (23/750) | - | 基线（纯关键词匹配） |
| 02-15 | 27% (208/750) | - | 参数驱动匹配 |
| 02-16 | 31% (229/733) | 42% (vs小栗70%) | Reranker+修饰词过滤+电气query增强 |

### 待解决的核心问题

1. **搜索query质量**：电气类清单描述信息提取不够完善
2. **搜索兜底**：找不到时返回不相关结果（应标红不匹配）
3. **经验库为空**：冷启动全靠搜索，积累经验后会好很多
4. **与小栗AI差距**：42% vs 70%（电气），主要差在插座/穿线/调试类
5. **未测给排水/暖通**：目前主要测了电气，其他专业可能表现不同

---

## 2026-02-17（算法增强：快速进化学习第一轮）

### 本轮改造

1. **混合检索升级为“自适应 + 多查询融合”**（`src/hybrid_searcher.py`）
- 新增 query 自适应权重：根据 query 是否含明显规格信号（DN/回路/型号/参数单位）动态调整 BM25 与向量权重。
- 新增多查询变体融合：原始 query + 规范化 query + 知识提示（可选）做加权 RRF 融合，降低单一 query 的召回盲区。
- 新增单路兜底融合：当 BM25 或向量一路为空时，仍对另一条路的多变体结果做 RRF 融合，避免直接退化为单次排序。
- 在返回候选中写入融合元信息（`fusion_mode`、`effective_bm25_weight`、`effective_vector_weight`、`fusion_weight_reason`、`query_variants`），为后续在线校准提供可观测字段。

2. **新增算法配置开关**（`config.py`）
- `HYBRID_ADAPTIVE_FUSION`
- `HYBRID_MULTI_QUERY_FUSION`
- `HYBRID_QUERY_VARIANTS`
- `HYBRID_VARIANT_WEIGHTS`
- `HYBRID_ADAPTIVE_BOOST`
- `HYBRID_FEEDBACK_ADAPTIVE_BIAS`
- `HYBRID_FEEDBACK_BIAS_MAX`
- `HYBRID_FEEDBACK_BIAS_REFRESH_SEC`
- `HYBRID_FEEDBACK_MIN_SAMPLES`

3. **快速进化学习闭环（反馈驱动权重偏置）**
- 新增经验库反馈偏置计算：读取 `experiences` 最近样本（`user_correction/user_confirmed`），比较“规格型清单”与“语义型清单”的纠错率差异。
- 若规格型纠错率更高，自动提高 BM25 权重；反之提高向量权重。
- 加入缓存刷新周期，避免每次检索都查库，控制时延与抖动。
- 当前自检样本上已出现非零偏置（约 `+0.037` 向 BM25 偏移），说明学习链路生效。

### 为什么这样改
- 目标是“快速进化学习”：先用训练无关算法提升在线效果，再把融合元信息暴露出来，后续可直接基于用户修正数据做权重自动校准（不再纯人工调参）。

### 已完成验证
- `python -m py_compile config.py src/hybrid_searcher.py` 通过。
- 轻量自检通过：`HybridSearcher` 可正确生成 query 变体并输出自适应权重。

---

## 2026-02-18（安全修复：逻辑漏洞与可利用链路）

### 修复内容

1. **上传文件路径穿越修复**（`pages/1_匹配定额.py`）
- 问题：`save_uploaded_file()` 直接使用 `uploaded_file.name` 拼接路径，存在 `../` 路径穿越风险。
- 修复：仅取 `basename`，清洗文件名字符，限定扩展名为 `.xlsx/.xls`，并追加随机后缀防覆盖。

2. **BM25不安全反序列化修复**（`src/bm25_engine.py`）
- 问题：`pickle.load()` 可在恶意索引文件场景触发代码执行。
- 修复：索引缓存改为 `bm25_index.json`（仅保存数据），加载时重建 `BM25Okapi`；旧 `bm25_index.pkl` 检测到后直接弃用并重建，不再反序列化。

3. **反馈学习跨省污染修复**（`src/hybrid_searcher.py`）
- 问题：反馈偏置计算未按省份过滤，可能把其他省份修正数据混入当前省份权重学习。
- 修复：经验样本查询增加 `province = self.province` 过滤，确保偏置学习省份隔离。

### 验证
- `python -m py_compile pages/1_匹配定额.py src/bm25_engine.py src/hybrid_searcher.py` 通过。
- `python main.py --help` 通过（入口参数可正常加载）。

### 补充修复（第二轮）

4. **.env 换行注入防护**（`pages/4_设置.py`）
- 问题：设置页保存API配置时，值未做换行/空字节清洗，理论上可注入额外环境变量行。
- 修复：新增 `_sanitize_env_value()`，写入前统一去除 `\\x00/\\r/\\n` 并 `strip`。
- 补充：保存策略改为“临时文件 + `os.replace` 原子替换”，避免写入中断导致 `.env` 部分损坏。

5. **上传文件大小限制**（`config.py`, `pages/1_匹配定额.py`）
- 问题：上传文件无大小上限，可能导致内存与磁盘压力异常。
- 修复：新增 `UPLOAD_MAX_MB=30`，超限时在页面报错并停止处理。

6. **Excel公式注入防护**（`src/output_writer.py`）
- 问题：清单/定额文本直接写入Excel，若文本以 `= + - @` 开头，打开表格时可能被当作公式执行。
- 修复：新增 `safe_excel_text()`，对上述前缀自动加单引号，并应用到清单名称、描述、定额文本、说明与备选等输出字段。
- 补充：判定升级为“首个非空白字符”检查，防止通过前置空格/Tab绕过。

7. **准确率统计口径修复**（`src/feedback_learner.py`, `pages/3_经验库.py`）
- 问题：`get_accuracy_stats()` 仍按 `auto_match` 统计“正确样本”，在新来源模型下会失真。
- 修复：改为统计 `user_confirmed + auto_match(兼容旧数据)` 作为正确样本；页面来源标签补充 `user_confirmed=用户确认`。

8. **审核工具临时文件竞争/覆盖修复**（`tools/review_test.py`）
- 问题：固定使用 `_temp_review.json` 作为中间文件，存在并发冲突和误删风险。
- 修复：改为 `tempfile.NamedTemporaryFile` 在 `output/temp/` 下创建唯一文件，读取后安全清理。

9. **经验库并发写入一致性修复**（`src/experience_db.py`）
- 问题：`add_experience()` 之前是“先查重再写入”的两阶段流程，缺少事务包裹，并发下可能重复插入；同时高并发时易出现 `database is locked`。
- 修复：
  - 新增 `_connect()`，统一设置 SQLite `timeout/busy_timeout`；
  - `add_experience()` 改为 `BEGIN IMMEDIATE` 事务化查重+更新/插入，避免并发竞态；
  - `_update_experience()` 支持复用外部事务连接；
  - 初始化阶段启用 `WAL + synchronous=NORMAL + busy_timeout`，改善写入稳定性。

10. **Agent审核学习笔记标错ID修复**（`tools/agent_review.py`）
- 问题：写学习笔记后用 `notebook.get_stats()['total']` 当作 `note_id` 标记反馈，存在并发/分页场景标错记录风险。
- 修复：改为使用 `record_note()` 返回的真实 `note_id` 调用 `mark_user_feedback()`。

11. **经验库页面脏数据容错修复**（`pages/3_经验库.py`）
- 问题：页面直接 `json.loads(quota_ids/quota_names)`，遇到坏数据会导致页面渲染报错。
- 修复：新增 `_safe_json_list()`，解析失败时降级空列表，避免单条脏数据拖垮页面。

12. **经验库来源口径与版本号更新修复**（`src/experience_db.py`）
- 问题A：`user_confirmed` 分支未更新 `source`，导致来源统计与反馈学习偏置口径失真。
- 问题B：更新已有记录时未刷新 `quota_db_version`，可能导致后续被误判为旧版本经验（stale）。
- 修复：
  - `user_confirmed` 更新时写入 `source='user_confirmed'`（若原来源是 `user_correction` 则保持更高优先级）；
  - `_update_experience()` 支持 `quota_db_version`，并在各更新分支同步刷新版本号；
  - `import_from_project()` 更新已有记录时透传当前版本号。

13. **经验库查重稳定性与脏数据容错增强**（`src/experience_db.py`）
- 精确匹配查询增加确定性排序：`confidence/confirm_count/updated_at/id`，避免重复记录下随机命中低质量样本。
- `search_similar()` 中 `quota_ids/quota_names` 改为安全JSON解析，避免脏数据触发异常中断。
- 新增组合索引 `idx_province_bill_text`，提升按省份+文本查重效率。

14. **通用知识库脏数据容错增强**（`src/universal_kb.py`）
- 问题：`quota_patterns/associated_patterns/param_hints/province_list` 直接 `json.loads`，遇到脏数据会在搜索增强链路触发异常。
- 修复：新增 `_safe_json_list/_safe_json_dict`，统一安全解析；应用于结果格式化、相似合并、省份统计等关键路径。

15. **导出单位换算类型稳健性修复**（`src/output_writer.py`）
- 问题：工程量是字符串（如 `"1,200"`）时，单位换算会触发类型错误，可能导致导出失败。
- 修复：`convert_quantity()` 新增工程量数值归一化（支持字符串逗号格式）；无法解析为数值时降级返回原值，不中断导出流程。

16. **Web导出文件名冲突修复**（`pages/1_匹配定额.py`）
- 问题：导出文件名仅按秒级时间戳生成，并发导出可能同名覆盖。
- 修复：导出文件名追加随机后缀（UUID短串），确保同秒并发也不覆盖。
- 同步修复：`src/output_writer.py` 默认输出文件名同样追加随机后缀，覆盖CLI并发场景。

17. **经验保存静默失败可见化**（`pages/1_匹配定额.py`）
- 问题：批量保存经验时对异常直接 `pass`，用户无感知，导致“看起来已保存，实际丢失”。
- 修复：改为统计失败条数，结束后统一 `st.warning` 提示，不中断主流程。

18. **Agent降级路径可观测性增强**（`main.py`）
- 问题：Agent模式下规则知识库/参考案例/规则上下文获取失败时静默 `pass`，排障困难。
- 修复：改为 `logger.debug(...)` 记录降级原因，主流程保持不中断。

19. **Agent审核输出文件名安全清洗**（`tools/agent_review.py`）
- 问题：审核输出文件名由源文件名直接截取，遇到非法路径字符时可能写文件失败。
- 修复：对 `basename` 做 Windows 非法字符清洗并兜底默认名，保证输出路径稳定可写。

20. **通用知识库并发连接稳定性增强**（`src/universal_kb.py`）
- 问题：通用知识库仍使用默认SQLite连接参数，批量学习/查询并发场景有锁冲突风险。
- 修复：
  - 初始化阶段启用 `WAL + synchronous=NORMAL + busy_timeout`；
  - 新增 `_connect()` 统一连接参数，并替换全文件连接调用。

21. **学习笔记写入与反馈容错增强**（`src/learning_notebook.py`）
- 问题A：`result_quota_ids/result_quota_names` 写入前缺少统一归一化，脏类型可能被原样落库，后续聚类统计不稳定。
- 问题B：`mark_user_feedback()` 对 `corrected_quota_ids` 类型不校验，异常输入可能污染反馈字段。
- 问题C：`_row_to_dict()` JSON解析失败时会保留原始字符串，导致同字段在调用方出现“有时是list、有时是str”的类型漂移。
- 修复：
  - 新增 `_as_json_list_text()`，统一把输入归一化为 JSON 数组文本后入库；
  - `mark_user_feedback()` 增加反馈值白名单与 `corrected_quota_ids` 列表类型校验；
  - 结果解析统一走 `_safe_json_list()`，确保输出字段类型稳定为 `list`。

22. **经验库剩余读路径连接稳定性补齐**（`src/experience_db.py`）
- 问题：`search_similar()/rebuild_vector_index()/get_stats()` 仍有直接 `sqlite3.connect()` 路径，连接参数不统一，锁冲突时行为不一致。
- 修复：
  - 上述路径统一切到 `_connect()`（含 `timeout + busy_timeout`）；
  - `_get_quota_map()` 读取定额库时补充连接超时与 `busy_timeout`；
  - `import_from_project()` 文本规范化失败由静默改为 `logger.debug`，增强可观测性。

23. **经验库页面读取链路稳定性修复**（`pages/3_经验库.py`）
- 问题：历史记录/搜索页直接 `sqlite3.connect()` 且连接关闭分散在分支内，异常时可能连接未释放，放大并发锁冲突。
- 修复：
  - 新增 `_open_db_conn()` 统一 `timeout + busy_timeout + row_factory`；
  - `show_records()` 与 `show_search()` 改为 `try/finally` 统一关闭连接；
  - `_safe_json_list()` 增强类型判定，避免 list/脏类型误解析。

24. **定额数据库页面查询连接稳定性修复**（`pages/2_定额数据库.py`）
- 问题：按章浏览仍直接使用默认 SQLite 连接，异常路径可能连接释放不及时；并发读写时更易出现锁等待失败。
- 修复：
  - 新增 `_open_quota_conn()` 统一 `timeout + busy_timeout + row_factory`；
  - `show_browse()` 查询改为 `try/finally` 统一关闭连接；
  - 搜索页初始化异常信息透传到页面告警，便于排障。

25. **匹配页临时文件膨胀风险修复**（`pages/1_匹配定额.py`）
- 问题：上传文件和匹配中间JSON长期累积在 `output/temp`，服务持续运行时可能造成磁盘膨胀和性能退化。
- 修复：
  - 新增 `_cleanup_temp_files()`，按“保留最新数量 + 最大文件年龄”双策略清理；
  - 在上传保存和匹配启动前触发清理；
  - 清理失败改为 `logger.debug` 记录，不影响主流程。

26. **反馈学习链路SQLite连接稳定性补齐**（`src/hybrid_searcher.py`, `src/feedback_learner.py`）
- 问题：反馈偏置计算与准确率统计仍使用默认 SQLite 连接参数，数据库繁忙时可能出现锁等待失败或异常路径连接未及时释放。
- 修复：
  - `HybridSearcher._get_feedback_bias()` 读取经验库时补充 `timeout + busy_timeout` 并改为 `try/finally` 关闭连接；
  - `FeedbackLearner.get_accuracy_stats()` 统计查询同样补充统一连接参数与 `try/finally` 关闭。

27. **检索引擎SQLite连接策略统一**（`src/bm25_engine.py`, `src/vector_engine.py`）
- 问题：BM25/向量检索在“建索引 + 回表查询”路径仍使用默认连接参数，并发读写下锁等待行为不一致。
- 修复：
  - 两个引擎均新增 `_connect()`（`timeout + busy_timeout`）；
  - 构建索引读取和搜索回表查询统一改为 `try/finally` 关闭连接。

28. **规则知识库SQLite连接策略统一**（`src/rule_knowledge.py`）
- 问题：规则导入、关键词检索、向量索引刷新、统计查询等路径仍为默认连接参数，异常时可能出现连接释放不及时与锁等待不稳定。
- 修复：
  - 新增 `_connect()`（`timeout + busy_timeout`）；
  - `_init_db/import_file/_keyword_search/_update_vector_index/get_stats` 全部切换为统一连接；
  - 关键查询路径改为 `try/finally` 关闭连接，避免异常分支泄露连接。

29. **定额数据库模块SQLite连接策略统一**（`src/quota_db.py`）
- 问题：`quota_db` 多处查询与导入写入路径仍直接连接数据库，连接参数不一致，重负载下锁等待表现不稳定。
- 修复：
  - 新增 `_connect()`（`timeout + busy_timeout`，可选 `row_factory`）；
  - 版本管理、导入写入、统计与检索等各连接入口统一切换到 `_connect()`。

30. **词汇反向提取器连接参数补齐**（`src/vocab_extractor.py`）
- 问题：词汇提取读取定额库仍使用默认连接参数，并发场景可能受锁等待影响。
- 修复：新增 `_connect()` 并替换读取路径，统一 `timeout + busy_timeout + row_factory`。

31. **Agent/LLM 规则上下文降级可观测性增强**（`src/llm_matcher.py`, `src/multi_agent_review.py`）
- 问题：规则知识库加载失败时仍有 `except ...: pass`，会吞掉降级原因，排障成本高。
- 修复：改为 `logger.debug(...)` 记录降级原因，同时保持主流程不中断。

32. **定额库连接泄漏边界修复**（`src/quota_db.py`）
- 问题：`get_version()` 在异常路径下可能提前返回，连接未必及时关闭；`get_stats()` 也未使用统一异常关闭结构。
- 修复：两处都改为 `try/finally` 关闭连接，确保异常分支不泄露连接句柄。

33. **经验库去重迁移脚本稳定性修复**（`tools/migrate_experience_dedup.py`）
- 问题A：迁移脚本直接 `json.loads(quota_ids/quota_names)`，脏数据会中断整批迁移。
- 问题B：迁移与候选层去重阶段缺少统一事务回滚保护，异常中断时可能留下半完成状态。
- 问题C：脚本内多处默认 SQLite 连接参数，不利于批量迁移时的锁稳定性。
- 修复：
  - 新增 `_safe_json_list()`，脏 JSON 降级为空列表；
  - 新增 `_connect()`（`timeout + busy_timeout`），并替换全文件数据库连接；
  - 迁移写入与候选层去重改为 `commit/rollback + finally close` 结构，异常时自动回滚。

34. **对比/基准测试脚本连接参数补齐**（`tools/llm_compare_test.py`, `tools/opus_test_match.py`）
- 问题：两个测试脚本仍使用默认 SQLite 连接参数，数据库繁忙时容易出现锁等待失败。
- 修复：
  - 新增 `_open_quota_conn()`（`timeout + busy_timeout`）；
  - 查询路径改为 `try/finally` 统一关闭连接。

35. **黄金集评测写入链路可观测性修复**（`tests/eval_golden.py`）
- 问题A：黄金集写入经验库失败时存在静默吞错，导致“部分样本未入库”不易发现。
- 问题B：黄金集JSON读取失败直接降级为空，但未记录具体原因。
- 修复：
  - `_save_pairs_to_experience()` 对单条失败记录输出 `logger.warning`，并汇总成功/失败计数；
  - `load_golden_cases()` 读取失败时输出告警原因，避免无感降级。
  - `load_golden_cases()` 增加文件结构校验（必须是 `list`），异常结构降级为空并告警。

36. **贾维斯纠正工具输入校验增强**（`tools/jarvis_store.py`, `tools/agent_debug.py`）
- 问题A：`jarvis_store.py` 对 `--quota-ids/--quota-names` 直接 `json.loads`，非法输入会抛异常中断且提示不清晰。
- 问题B：批量JSON文件结构不合法时缺少明确错误提示。
- 问题C：`agent_debug.py` 的经验库失败提示缺少异常原因。
- 修复：
  - 新增 `_parse_json_list()`，对CLI JSON参数做“合法JSON + 必须数组”校验；
  - `store_batch()` 增加文件读取与 `corrections` 结构校验；
  - `agent_debug` 经验库降级提示中带上异常信息。

37. **审核/迁移/评测脚本静默降级可见化**（`tools/review_test.py`, `tools/migrate_experience_dedup.py`, `tests/eval_golden.py`）
- 问题：部分脚本在临时文件清理失败、向量集合删除失败、对比文件损坏等场景只静默处理，定位问题困难。
- 修复：
  - `review_test.py` 临时文件删除失败时输出 WARN；
  - `migrate_experience_dedup.py` 的旧集合删除失败与CUDA降级增加显式提示；
  - `eval_golden.py` 对比历史结果文件损坏时输出具体异常信息。

38. **全局安全模式复扫（本轮收尾）**
- 复扫项：`except ...: pass`、`eval/exec`、`pickle.load`、`yaml.load`、`subprocess(shell=True)`。
- 结果：代码区未发现新命中；本轮改动后继续保持“无静默吞错/无高危动态执行”状态。

39. **Excel输出原子写入修复**（`src/output_writer.py`）
- 问题：结果Excel直接 `wb.save(output_path)`，若进程中断可能留下半写入文件（损坏或不可打开）。
- 修复：
  - 新增 `_save_workbook_atomic()`：先写同目录临时文件，再 `os.replace` 原子替换；
  - 保留原结构模式与新建模式都切换到原子保存；
  - 增加 `wb.close()` 收口，避免资源句柄长期占用。

40. **CLI JSON输出与评测结果文件原子写入补齐**（`main.py`, `tests/eval_golden.py`）
- 问题A：`main.py --json-output` 直接写目标文件，中断时可能产生半JSON，影响Web读取。
- 问题B：黄金集与评测结果JSON直接覆盖写入，异常中断时有文件损坏风险。
- 修复：
  - `main.py` 新增 `_atomic_write_json()`，`--json-output` 改为临时文件+`os.replace`；
  - `tests/eval_golden.py` 新增 `_atomic_write_json()`，`save_golden_cases()` 与 `_save_eval_results()` 改为原子写入。

41. **工具导出脚本Excel原子写入补齐**（`tools/llm_compare_test.py`, `tools/opus_test_match.py`, `tools/export_matching_result.py`）
- 问题：工具脚本仍直接 `wb.save(目标路径)`，中断时可能生成半写入Excel。
- 修复：
  - 三个脚本新增 `_atomic_save_workbook()`（同目录临时文件 + `os.replace`）；
  - 导出路径全部切换为原子保存，降低批量导出损坏风险。

42. **索引/词汇缓存写入原子化补齐**（`src/bm25_engine.py`, `src/vocab_extractor.py`）
- 问题：BM25索引与提取词汇缓存仍是直接覆盖写入，中断时可能导致文件损坏（后续加载失败或行为异常）。
- 修复：
  - `BM25Engine._save_index()` 改为临时JSON + `os.replace`；
  - `VocabExtractor._save_vocab_cache()` 改为临时TXT + `os.replace`；
  - 补充回归：重建 BM25 索引与词汇缓存，确认输出恢复为真实数据。

43. **审核与批处理脚本JSON原子写入补齐**（`tools/agent_review.py`, `tools/batch_test_all.py`, `tools/batch_fix_experience.py`）
- 问题：上述脚本仍直接覆盖写JSON，运行中断时可能留下损坏报告/审核文件，影响后续流水线。
- 修复：
  - 三个脚本新增 `_atomic_write_json()`；
  - 审核导出、批测报告、修正预览输出统一切换为临时文件 + `os.replace`；
  - `batch_test_all.py` 的 `run_test` 异常路径增加 WARN 输出，提升可观测性。

44. **词汇清洗脚本写回原子化**（`tools/clean_vocab.py`）
- 问题：`clean_vocab.py` 写回 `extracted_vocab.txt` 仍为直接覆盖，异常中断可能破坏词汇文件。
- 修复：改为 `NamedTemporaryFile + os.replace` 原子替换，确保文件完整性。

45. **审核链路工具输出原子化补齐**（`tools/review_test.py`）
- 问题：审核分批TXT与完整JSON输出仍为直接写入，长批次运行中断时可能留下半文件。
- 修复：
  - 新增 `_atomic_write_text()` 和 `_atomic_write_json()`；
  - 批次文本输出与完整JSON输出全部切换为原子写入。

46. **决策/规则生成脚本写入原子化补齐**（`tools/generate_decisions.py`, `tools/fix_decisions_text.py`, `tools/extract_quota_rules.py`）
- 问题：决策文件、规则JSON、规则摘要仍直接覆盖写入，中断时可能损坏产物，影响后续导入/审核流程。
- 修复：
  - 三个脚本统一改为临时文件 + `os.replace` 原子替换；
  - `extract_quota_rules.py` 的 JSON 与摘要两个输出都已原子化。

47. **批测脚本异常可观测性增强**（`tools/batch_test_all.py`）
- 问题：`run_test()` 异常时仅返回错误码，缺少原因输出，定位失败来源困难。
- 修复：异常分支增加 `[WARN] run_test异常` 输出，保留原返回语义不变。

48. **上传文件保存原子化补齐**（`pages/1_匹配定额.py`）
- 问题：上传文件此前直接写目标路径，上传中断时可能留下半文件，后续读取失败且不易定位。
- 修复：
  - `save_uploaded_file()` 改为同目录临时文件写入后 `os.replace`；
  - 保留现有安全文件名与扩展名校验逻辑不变。

49. **批测报告链路路径解析与容错修复**（`tools/batch_test_all.py`）
- 问题A：批测脚本通过“模糊 token 匹配”提取 review JSON 路径，输出格式变化时可能误取路径，导致统计错读。
- 问题B：结果JSON读取失败时未显式提示原因，批测排障困难。
- 修复：
  - `run_test()` 优先解析标准输出行 `完整JSON: <path>`，并兼容相对路径归一化；
  - 保留旧输出格式的正则兜底提取；
  - 子进程失败时输出尾部错误摘要；
  - `analyze_results()` 增加 JSON 读取异常告警并安全返回。

50. **Agent审核决策导入入口鲁棒性修复**（`tools/agent_review.py`）
- 问题A：`--store` 缺少参数时直接索引 `sys.argv[2]`，会触发边界错误。
- 问题B：决策文件读取/结构异常时缺少明确校验，可能在中途抛异常中断。
- 问题C：单条决策缺少 `name` 或 `correct_quota_ids` 时会导致批量导入提前失败或统计不透明。
- 修复：
  - `--store` 模式增加参数个数校验和明确用法提示；
  - `store_decisions()` 增加文件读取异常与 `dict/list` 结构校验；
  - 对非法条目做跳过计数，最终输出“已存入/已跳过”汇总。

### 补充验证
- `python -m py_compile config.py pages/1_匹配定额.py pages/4_设置.py src/output_writer.py` 通过。
- `safe_excel_text('=1+1')` 输出 `'=1+1`，防护生效。
- `python -m py_compile src/feedback_learner.py pages/3_经验库.py` 通过。
- `python -m py_compile tools/review_test.py` 通过。
- `python -m py_compile src/experience_db.py` 通过。
- `python -m py_compile tools/agent_review.py` 通过。
- `python -m py_compile pages/3_经验库.py` 通过。
- `python -m py_compile src/experience_db.py src/feedback_learner.py src/hybrid_searcher.py` 通过。
- `python -m py_compile src/universal_kb.py` 通过。
- `python -m py_compile src/output_writer.py` 通过。
- `python -m py_compile pages/1_匹配定额.py` 通过。
- `python -m py_compile src/learning_notebook.py src/experience_db.py` 通过。
- `LearningNotebook().get_stats()` 轻量自检通过。
- `ExperienceDB().get_stats()` 轻量自检通过（`experience_total=1150`）。
- `python -m py_compile pages/3_经验库.py src/learning_notebook.py src/experience_db.py` 通过。
- `ExperienceDB().get_stats()` + `LearningNotebook().get_stats()` 联合自检通过（`exp_total=1150`, `note_total=327`）。
- `python -m py_compile pages/2_定额数据库.py pages/3_经验库.py src/learning_notebook.py src/experience_db.py` 通过。
- `python -m py_compile pages/1_匹配定额.py pages/2_定额数据库.py pages/3_经验库.py src/learning_notebook.py src/experience_db.py` 通过。
- `python -m py_compile src/hybrid_searcher.py src/feedback_learner.py` 通过。
- `FeedbackLearner().get_accuracy_stats()` 轻量自检通过。
- `python -m py_compile src/bm25_engine.py src/vector_engine.py src/hybrid_searcher.py src/feedback_learner.py` 通过。
- `FeedbackLearner().get_accuracy_stats()['accuracy_rate']` 自检输出 `68.9`。
- `python -m py_compile src/rule_knowledge.py src/bm25_engine.py src/vector_engine.py src/hybrid_searcher.py src/feedback_learner.py` 通过。
- `RuleKnowledge().get_stats()` 轻量自检通过。
- `python -m py_compile src/quota_db.py src/vocab_extractor.py src/rule_knowledge.py src/bm25_engine.py src/vector_engine.py src/hybrid_searcher.py src/feedback_learner.py` 通过。
- `QuotaDB().get_quota_count()` + `RuleKnowledge().get_stats()` 联合自检通过（`quota_total=11412`, `rule_total=180`）。
- `python -m py_compile src/llm_matcher.py src/multi_agent_review.py src/quota_db.py src/rule_knowledge.py src/bm25_engine.py src/vector_engine.py src/hybrid_searcher.py src/feedback_learner.py src/learning_notebook.py pages/1_匹配定额.py pages/2_定额数据库.py pages/3_经验库.py` 通过。
- `FeedbackLearner().get_accuracy_stats()` + `QuotaDB().get_quota_count()` 联合自检通过（`accuracy=68.9`, `quota_count=11412`）。
- `python -m py_compile src/quota_db.py` 通过。
- `QuotaDB().get_version()` + `QuotaDB().get_stats()` 轻量自检通过（`total=11412`）。
- `python -m py_compile tools/migrate_experience_dedup.py tools/llm_compare_test.py tools/opus_test_match.py` 通过。
- `tools.migrate_experience_dedup.get_all_records()` 轻量自检通过（`authority_rows=307`）。
- `python -m py_compile tests/eval_golden.py tools/jarvis_store.py tools/agent_debug.py tools/migrate_experience_dedup.py tools/llm_compare_test.py tools/opus_test_match.py` 通过。
- `python tools/jarvis_store.py --help` / `python tests/eval_golden.py --help` 通过。
- `python tools/jarvis_store.py --name test --quota-ids not_json` 会返回清晰参数错误（JSON格式校验生效）。
- `python -m py_compile tests/eval_golden.py` 与 `python tests/eval_golden.py --info` 通过。
- `python -m py_compile tools/review_test.py tools/migrate_experience_dedup.py tests/eval_golden.py` 通过。
- `python tests/eval_golden.py --compare-last` 运行通过（当前黄金集为空，返回提示符合预期）。
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python -m py_compile src/output_writer.py main.py tests/eval_golden.py` 通过。
- `python tests/eval_golden.py --info` 运行通过（黄金集读取正常）。
- `_atomic_write_json` 轻量烟测通过（`output/temp/_atomic_json_smoke.json` 成功创建并清理）。
- `python -m py_compile tools/llm_compare_test.py tools/opus_test_match.py tools/export_matching_result.py src/output_writer.py main.py tests/eval_golden.py` 通过。
- `OutputWriter._save_workbook_atomic()` 轻量烟测通过（`output/temp/_atomic_excel_smoke.xlsx` 成功创建并清理）。
- `python -m py_compile src/bm25_engine.py src/vocab_extractor.py src/output_writer.py main.py tests/eval_golden.py tools/llm_compare_test.py tools/opus_test_match.py tools/export_matching_result.py` 通过。
- `BM25Engine.build_index()` 回归重建通过（`bm25_rebuilt=11412`）。
- `VocabExtractor.extract_all()` 回归重建通过（`vocab_terms=4886`, `vocab_stems=4775`）。
- `python -m py_compile tools/agent_review.py tools/batch_test_all.py tools/batch_fix_experience.py tools/clean_vocab.py` 通过。
- `python -m py_compile tools/review_test.py tools/agent_review.py tools/batch_test_all.py tools/batch_fix_experience.py tools/clean_vocab.py` 通过。
- `python -m py_compile tools/generate_decisions.py tools/fix_decisions_text.py tools/extract_quota_rules.py tools/review_test.py tools/agent_review.py tools/batch_test_all.py tools/batch_fix_experience.py tools/clean_vocab.py` 通过。
- `python -m py_compile pages/1_匹配定额.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python -m py_compile tools/batch_test_all.py` 通过。
- `python tools/batch_test_all.py --help` 通过。
- `python -m py_compile tools/agent_review.py` 通过。
- `python tools/agent_review.py --store` 会返回明确用法提示。

51. **规则校验器加载容错修复**（`src/rule_validator.py`）
- 问题：规则文件读取/JSON损坏/结构异常时会直接抛错，可能影响匹配流程启动。
- 修复：
  - 初始化阶段统一默认降级状态（`rules=None`, 空索引）；
  - 增加 `FileNotFoundError`、`JSONDecodeError`、`OSError` 等分支捕获；
  - 增加规则结构校验（根节点必须是对象、`chapters` 必须是对象）；
  - 加载成功但无可用家族时给出明确告警，避免“看似加载成功但实际不可用”。

52. **匹配页面关键吞异常去除 + 可观测性增强**（`pages/1_匹配定额.py`）
- 问题A：匹配子进程临时结果文件清理处存在多处 `except: pass`，失败不可见。
- 问题B：经验库保存链路中通用知识库同步失败与单条保存失败无日志，排障困难。
- 修复：
  - 新增 `_safe_unlink()` 统一临时文件删除并记录调试日志；
  - 匹配超时/失败/JSON解析失败/完成后的清理都改为可观测；
  - 增加匹配结果JSON根结构校验（非对象直接报错并记录日志）；
  - `UniversalKB` 初始化失败、`learn_from_correction()` 失败、`add_experience()` 失败均记录上下文日志（含 idx/bill）。

53. **预算导入工具鲁棒性修复**（`tools/import_reference.py`）
- 问题A：`read_excel_pairs()` 在异常路径下可能未关闭工作簿句柄。
- 问题B：导入经验库失败日志缺少清单上下文，定位单条坏数据困难。
- 修复：
  - `read_excel_pairs()` 改为 `try/finally` 确保 `wb.close()` 总能执行；
  - `import_to_experience()` 增加输入结构校验（`pair`/`quotas` 类型）；
  - 导入失败日志追加 `bill_name`/`bill_code` 上下文，便于定位源数据。

54. **状态与索引维护路径异常可观测性补齐**（`src/hybrid_searcher.py`, `src/vector_engine.py`, `src/universal_kb.py`, `src/experience_db.py`）
- 问题：部分“可忽略异常”使用 `pass`，导致状态检测与索引维护失败时不可见。
- 修复：
  - `HybridSearcher.get_status()` 的 BM25/向量状态检查异常改为 `logger.debug`；
  - 各库 `delete_collection()` 的预期异常改为 `logger.debug` 记录；
  - 经验库/通用知识库统计阶段的向量计数或省份解析异常改为日志可见降级。

### 本轮补充验证
- `python -m py_compile src/rule_validator.py tools/import_reference.py pages/1_匹配定额.py src/hybrid_searcher.py src/experience_db.py src/universal_kb.py src/vector_engine.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

55. **导入记录有效性再收敛 + 全局静默吞错复扫**（`tools/import_reference.py`）
- 问题：导入链路仍可能把空 `bill_pattern` 写入经验库/通用知识库，造成低质量经验污染。
- 修复：
  - `convert_to_kb_records()` 与 `import_to_experience()` 增加 `bill_pattern` 非空校验；
  - 无效记录统一跳过并输出告警。
- 复扫结果：项目范围内 `except ...: pass` 已清零（关键路径无静默吞错残留）。

### 本轮再次验证
- `python -m py_compile tools/import_reference.py src/rule_validator.py pages/1_匹配定额.py src/hybrid_searcher.py src/experience_db.py src/universal_kb.py src/vector_engine.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

56. **设置页GPU信息字段错误修复**（`pages/4_设置.py`）
- 问题：GPU显存读取使用 `total_mem`，在常见 PyTorch 版本中属性为 `total_memory`，有GPU时可能触发属性错误导致页面异常。
- 修复：
  - 改为兼容读取：优先 `total_memory`，兼容 `total_mem`；
  - 增加异常兜底提示 `GPU状态检测失败`，避免页面崩溃。

### 本轮补充验证（追加）
- `python -m py_compile pages/4_设置.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

57. **设置页知识库状态异常原因可见化**（`pages/4_设置.py`）
- 问题：通用知识库/规则知识库/经验库状态加载失败时仅显示“未初始化”，缺少异常原因，排障成本高。
- 修复：三个分支统一改为 `except Exception as e`，并在页面增加 `原因: ...` 提示。

### 本轮补充验证（追加）
- `python -m py_compile pages/4_设置.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

58. **通用知识库连接收口与事务健壮性修复**（`src/universal_kb.py`）
- 问题A：`add_knowledge/_update_knowledge/search_hints/_find_similar/_find_exact/get_stats` 多处连接在异常路径下无 `finally`，长期运行有句柄泄漏风险。
- 问题B：写入路径缺少回滚保护，异常时可能留下未提交事务状态并影响后续请求。
- 修复：
  - 关键读写路径统一补 `try/finally` 关闭连接；
  - 写入路径补 `except -> rollback -> raise`；
  - `_update_knowledge` 对不存在记录显式抛错，避免静默更新空对象。
- 可观测性补充：
  - `model` GPU降级增加 warning；
  - `get_stats()` 向量计数异常增加 debug 日志。

59. **经验库/向量引擎降级可观测性与容错补齐**（`src/experience_db.py`, `src/vector_engine.py`, `tools/batch_fix_experience.py`）
- `src/experience_db.py`：
  - `model` GPU降级增加 warning；
  - `_find_exact_match` 增加 `try/finally` 关闭连接，避免异常分支泄漏连接。
- `src/vector_engine.py`：
  - metadata采样失败增加 debug 日志；
  - `get_index_count()` 异常分支增加 debug 日志，避免静默返回0。
- `tools/batch_fix_experience.py`：
  - 输入文件读取增加 `FileNotFound/JSONDecode/OSError` 明确报错；
  - 增加根节点与 `items` 结构校验；
  - 单条坏记录（缺 `bill_name/current_quota_id` 或结构非法）跳过并计数；
  - 汇总输出新增 `非法记录` 统计，批量修复不再因脏数据中断。

### 本轮补充验证
- `python -m py_compile src/universal_kb.py src/experience_db.py src/vector_engine.py tools/batch_fix_experience.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

60. **通用知识库剩余连接收口收尾**（`src/universal_kb.py`）
- 问题：`batch_import` 统计前后计数与 `rebuild_vector_index` 读取阶段仍有非 `finally` 关闭连接路径。
- 修复：
  - 三处连接统一改为 `try/finally` 关闭；
  - 保持原业务逻辑不变，仅提升异常路径资源释放稳定性。

### 本轮补充验证（追加）
- `python -m py_compile src/universal_kb.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

61. **定额库查询层连接收口与事务回滚补齐**（`src/quota_db.py`）
- 问题：多个高频查询方法直接 `conn.close()`，异常路径下可能未释放连接；`upgrade_add_book_field()` 写路径缺少回滚保护。
- 修复：
  - `get_all_quotas/get_quota_count/get_quota_by_id/search_by_keyword/get_chapters/get_specialties/get_chapters_by_specialty/get_quotas_by_chapter/search_by_keywords/get_books/get_chapters_by_book` 统一改为 `try/finally` 关闭连接；
  - `upgrade_add_book_field()` 增加 `except -> rollback -> raise`，并统一 `finally` 关闭连接；
  - `get_version()` 异常分支增加 debug 日志，保留原“返回空版本”降级行为。

### 本轮补充验证
- `python -m py_compile src/quota_db.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

62. **批测/审核工具JSON结构校验补齐**（`tools/batch_test_all.py`, `tools/review_test.py`）
- `tools/batch_test_all.py`：
  - `analyze_results()` 增加根节点与 `results` 类型校验；
  - 非法结果项改为“跳过并告警”而非直接抛错中断；
  - `bill_item/quotas/first_quota` 结构异常时降级为空结构处理。
- `tools/review_test.py`：
  - `run_matching()` 增加结果文件不存在/JSON损坏/读取失败的明确异常信息；
  - 增加结果根结构校验（`dict` + `results` 数组）；
  - 临时JSON清理放入 `finally`，避免异常分支遗留临时文件；
  - 主流程增加 `run_matching` 异常拦截，错误信息可见并以非0退出。

63. **决策修正脚本输入健壮性修复**（`tools/fix_decisions_text.py`）
- 问题：脚本顶层直接读取两个JSON并强依赖字段，坏文件/结构异常时会直接崩溃。
- 修复：
  - 新增 `_load_json()`，对文件不存在/JSON损坏/IO失败给出明确提示并退出；
  - 增加 `old_decisions/decisions/report/red_items` 结构校验；
  - 遍历决策与红色项时增加 `dict` 类型校验与关键字段兜底，避免 `KeyError`；
  - 末尾验证输出改为 `get()` 安全读取。

### 本轮补充验证
- `python -m py_compile tools/review_test.py tools/batch_test_all.py tools/fix_decisions_text.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

64. **规则检索双路去重失效修复 + 导入事务回滚补齐**（`src/rule_knowledge.py`）
- 问题A：向量路返回ID形如 `rule_123`，关键词路返回ID形如 `123`，原去重键不一致，导致同一规则可能重复进入结果。
- 修复：
  - 新增 `_normalize_result_id()`，统一去重键格式；
  - `search_rules()` 在向量路与关键词路都使用统一ID归一化后去重。
- 问题B：`import_file()` 写库异常时缺少回滚保护。
- 修复：
  - 增加 `except -> rollback -> raise`，避免导入失败后事务状态异常。
- 可观测性补充：
  - `_update_vector_index()` 读取现有索引ID失败时增加 debug 日志。

### 本轮补充验证
- `python -m py_compile src/rule_knowledge.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

65. **词汇提取与BM25索引鲁棒性补强**（`src/vocab_extractor.py`, `src/bm25_engine.py`）
- `src/vocab_extractor.py`：
  - `extract_all()` 数据库读取改为 `try/finally` 关闭连接，避免异常分支连接遗留；
  - `update_jieba_dict()` 增加词典目录自动创建；
  - 读取现有词典时跳过空行与注释行，避免把注释当词条。
- `src/bm25_engine.py`：
  - `_save_index()` 临时文件清理失败改为 debug 可观测；
  - `load_index()` 增加索引结构校验（根节点必须对象、`tokenized_corpus` 必须数组）；
  - `quota_books` 非对象时安全降级为空字典。

### 本轮补充验证
- `python -m py_compile src/vocab_extractor.py src/bm25_engine.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

66. **贾维斯批量纠正导入容错修复**（`tools/jarvis_store.py`）
- 问题：`store_batch()` 对输入项强依赖 `item["name"]/item["quota_ids"]`，坏数据会触发 `KeyError` 并中断整批导入。
- 修复：
  - 增加根节点 `dict` 校验；
  - 单条记录增加类型与关键字段校验（`name`、`quota_ids`）；
  - 非法记录改为跳过计数，不影响后续条目；
  - 汇总输出新增 `非法` 数量。

### 本轮补充验证
- `python -m py_compile tools/jarvis_store.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

67. **黄金集回归对比结构容错修复**（`tests/eval_golden.py`）
- 问题：`_compare_with_last()` 在 `last_eval_result.json` 结构异常时仍可能因字段硬索引崩溃。
- 修复：
  - 增加 `last_data` 根节点与 `results` 类型校验；
  - 对 `results` 中非法项做过滤，仅使用含有效 `bill_text` 的记录；
  - 无有效历史条目时明确提示并跳过对比。

### 本轮补充验证
- `python -m py_compile tests/eval_golden.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

68. **学习笔记模块事务与连接收口补齐**（`src/learning_notebook.py`）
- 问题：`record_note/mark_user_feedback/get_notes_by_pattern/get_extractable_patterns/get_stats` 在异常路径下存在连接未关闭风险；写路径缺少回滚保护。
- 修复：
  - 写操作 `record_note/mark_user_feedback` 增加 `except -> rollback -> raise`；
  - 所有上述读写接口统一 `try/finally` 关闭连接；
  - 保持现有业务语义不变，仅增强异常路径稳定性。

### 本轮补充验证
- `python -m py_compile src/learning_notebook.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

69. **LLM对比与Opus基准脚本稳健性补强**（`tools/llm_compare_test.py`, `tools/opus_test_match.py`）
- `tools/llm_compare_test.py`：
  - 输入文件前置校验：清单文件不存在/Sheet不存在时给出明确错误，避免直接崩溃；
  - `read_bill_items()` 改为 `try/finally` 关闭 workbook，防止异常路径资源泄漏；
  - `_atomic_save_workbook()` 临时文件清理失败从静默 `pass` 改为告警输出，提升可观测性；
  - `parse_response()` 增加 JSON 根节点与 `quota_ids` 类型归一化（兼容字符串/元组/集合），并对 `confidence` 做安全数值转换；
  - `compare_quota_ids()` 增加输入类型归一化，避免异常数据导致误判或隐藏错误；
  - 主流程增加定额库路径存在性校验，不再在空数据库场景下继续执行。
- `tools/opus_test_match.py`：
  - 输入文件与Sheet存在性校验补齐，错误信息更明确；
  - `read_bill_items()` 改为 `try/finally` 关闭 workbook；
  - `_atomic_save_workbook()` 临时文件清理失败从静默 `pass` 改为告警输出；
  - 统计百分比统一使用 `max(total, 1)` 防护，避免空清单时除零崩溃。

### 本轮补充验证
- `python -m py_compile tools/opus_test_match.py tools/llm_compare_test.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

70. **Agent审核脚本容错与可观测性补强**（`tools/agent_review.py`）
- 问题：
  - `_atomic_write_json()` 临时文件清理失败仍是静默 `pass`；
  - 候选列表简化阶段对 `c["quota_id"]/c["name"]` 与 `best["quota_id"]/best["name"]` 使用硬索引，脏候选数据可能触发 `KeyError` 中断审核导出；
  - 命令行 `--limit` 参数非法时直接抛 `ValueError`。
- 修复：
  - 新增 `_safe_unlink()`，临时文件清理失败改为告警输出；
  - 候选项增加 `dict` 与关键字段校验，非法候选跳过；
  - `current_best` 改为 `get()` 安全读取并字符串归一化；
  - `--limit` 增加 `ValueError` 捕获与明确错误提示，非法参数以非0退出。

### 本轮补充验证
- `python -m py_compile tools/agent_review.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

71. **系统逻辑性问题修复（回写对齐/LLM解析/Agent统计/Web选行）**
- `src/output_writer.py`：
  - 修复过滤场景错位回写：`_process_bill_sheet()` 优先按 `bill_item.sheet_bill_seq` 精准定位清单行；
  - 兼容旧结果（无定位字段）时保留顺序回写降级；
  - 解决 `--filter-code` / `--limit` 子集处理时结果写错行的逻辑风险。
- `src/bill_reader.py`：
  - 读取阶段为每条清单新增 `sheet_bill_seq`（Sheet内清单序号）和 `source_row`（原始行号）；
  - 为输出层精准回写提供稳定定位信息。
- `src/llm_matcher.py`：
  - 增加大模型返回根节点 `dict` 校验；
  - `confidence` 强制数值化并限制到 `0..100`；
  - `related_quotas` 增加 `list[dict]` 结构校验，脏结构安全跳过。
- `src/agent_matcher.py`：
  - 增加根节点 `dict` 校验；
  - `confidence` 数值归一化；
  - `related_quotas` 结构校验，避免 `AttributeError` 中断整批。
- `main.py`：
  - `match_agent()` 中 `agent_hits` 改为仅统计 `match_source` 为 `agent*` 的结果；
  - 修复“回退经验库后仍计入Agent命中”导致的统计虚高。
- `pages/1_匹配定额.py`：
  - 表格数据新增唯一键 `row_uid`；
  - 行点击改为按 `row_uid` 精确定位，修复重名/重编码时选错行问题。

### 本轮补充验证
- `python -m py_compile main.py src/bill_reader.py src/output_writer.py src/llm_matcher.py src/agent_matcher.py pages/1_匹配定额.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

72. **无效代码清理（占位脚本与空壳函数）**
- `tools/analyze_nongreen.py`：
  - 原文件仅 `# placeholder`，属于无效脚本；
  - 已改为可执行分析工具：支持读取 `main.py --json-output` 结果，统计非绿色条目（低于阈值）、未匹配数量，并输出最低置信度TopN清单；
  - 增加参数：`--threshold`、`--top`，并补充输入结构校验与错误码返回。
- `pages/1_匹配定额.py`：
  - 删除未使用的空壳函数 `show_swap_panel()`；
  - 将按钮区空 `pass` 替换为 `st.empty()`，去除无意义占位代码。

### 本轮补充验证
- `python -m py_compile tools/analyze_nongreen.py pages/1_匹配定额.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

73. **无引用模块级单例清理（减少导入副作用）**
- 问题：
  - `src/output_writer.py` 存在无引用单例 `writer = OutputWriter()`；
  - `src/experience_db.py` 存在无引用单例 `experience_db = ExperienceDB()`；
  - `src/multi_agent_review.py` 存在无引用单例 `reviewer = MultiAgentReview()`。
- 风险：
  - 单例在模块导入时即初始化，可能触发不必要的数据库/模型相关副作用；
  - 但全仓无调用，属于典型无效代码与维护噪音。
- 修复：
  - 移除上述3个无引用模块级单例定义，保留类与主流程逻辑不变。

### 本轮补充验证
- `python -m py_compile src/output_writer.py src/experience_db.py src/multi_agent_review.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

74. **无引用测试路径文件清理**
- 问题：`tools/test_files.py` 全仓无引用，且内容为本机绝对路径常量集合，不具备可移植性与实际执行价值。
- 修复：删除 `tools/test_files.py`，避免无效代码长期占位并误导维护。

### 本轮补充验证
- 全仓检索未发现 `tools.test_files` 相关引用。
- `python -m compileall -q src pages tools tests main.py` 通过。

75. **导入工具死函数清理**
- 问题：`tools/import_reference.py` 中 `_build_bill_pattern()` 为历史遗留函数，全仓无调用，属于无效维护负担。
- 修复：删除 `_build_bill_pattern()`，保留其余导入流程不变。

### 本轮补充验证
- 全仓检索未发现 `_build_bill_pattern` 残留引用。
- `python -m py_compile tools/import_reference.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

76. **删除无用文件：多Agent旧审核模块**
- 删除文件：`src/multi_agent_review.py`
- 删除理由：
  - 全仓无导入、无调用，主流程未接线；
  - 逻辑已由当前 `agent_matcher`/`main.py --mode agent` 路径覆盖，不再实际生效。

### 本轮补充验证
- 全仓检索无 `multi_agent_review/MultiAgentReview` 代码引用残留（仅保留业务数据中的字符串字段 `reviewer`）。
- `python -m compileall -q src pages tools tests main.py` 通过。

77. **删除无入口一次性测试脚本（硬编码路径）**
- 删除文件：
  - `tools/export_matching_result.py`
  - `tools/opus_test_match.py`
  - `tools/llm_compare_test.py`
- 删除理由：
  - 全仓无代码调用、无 `.bat` 入口；
  - 脚本内部硬编码本机桌面路径，属于一次性测试产物，不具备通用可维护价值。

### 本轮补充验证
- 全仓检索无 `export_matching_result/opus_test_match/llm_compare_test` 代码引用残留。
- `python -m compileall -q src pages tools tests main.py` 通过。

78. **删除无入口历史批处理脚本（强依赖固定数据路径）**
- 删除文件：
  - `tools/batch_test_all.py`
  - `tools/generate_decisions.py`
  - `tools/fix_decisions_text.py`
- 删除理由：
  - 全仓无代码调用、无 `.bat` 入口；
  - 三者均面向历史批测/历史决策产物，依赖固定目录或固定输出文件，属于一次性运维脚本，不再属于当前主流程。

79. **删除测试目录下无用调试产物文件**
- 删除文件：`tests/debug_sheets.txt`
- 删除理由：
  - 内容为本机绝对路径下Excel结构的静态导出结果；
  - 无代码读取、无测试链路依赖，属于一次性调试输出，不应长期入库。

80. **删除无入口手工调试脚本**
- 删除文件：`tests/debug_sheets.py`
- 删除理由：
  - 全仓无引用，且用途为一次性手工排查 Sheet 结构；
  - 与自动化测试链路无关，保留会增加维护噪音。

81. **清理跨机失效的绝对路径调试入口**
- `src/bill_reader.py`：
  - 将 `__main__` 入口从固定本机路径读取改为命令行参数化：`python -m src.bill_reader <input> [--limit N]`。
- `tests/test_accuracy.py`：
  - `--input` 改为必填参数，删除桌面绝对路径默认值，避免在非开发机误运行失败。

### 本轮补充验证
- `python -m py_compile src/bill_reader.py tests/test_accuracy.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python tests/test_accuracy.py --help` 通过（输入参数已显示为必填 `--input`）。

82. **删除无入口一次性批修脚本**
- 删除文件：`tools/batch_fix_experience.py`
- 删除理由：
  - 脚本说明与逻辑强绑定 2026-02-17 某批次“322条黄/红项”修正任务；
  - 全仓无代码调用、无 `.bat` 入口，属于历史一次性运维脚本。

83. **删除无入口经验库迁移脚本**
- 删除文件：`tools/migrate_experience_dedup.py`
- 删除理由：
  - 脚本定位为历史迁移任务（重算 bill_text + 去重合并 + 重建索引）；
  - 全仓无代码调用、无 `.bat` 入口，不属于当前在线流程。

84. **删除无引用文档拆包残留目录**
- 删除目录：`docs/chm_dump/`
- 删除理由：
  - 全仓无任何代码/脚本/入口引用；
  - 目录内容为 CHM 拆包临时产物（含大量空文件或极小碎片文件），不属于系统运行与维护必需资产。

### 本轮补充验证
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python main.py --help` 通过（主入口参数解析正常）。
- 全仓检索未发现 `batch_fix_experience/migrate_experience_dedup` 代码引用残留。

85. **经验库“确认后不直通”逻辑修复**
- 文件：`pages/1_匹配定额.py`
- 问题：
  - 用户点击“确认正确”后，保存经验沿用当前低置信度，常低于直通阈值（`EXPERIENCE_DIRECT_THRESHOLD=90`）。
  - 结果是“确认了但下次仍不走经验库直通”。
- 修复：
  - `user_confirmed` 保存置信度改为至少 `EXPERIENCE_DIRECT_THRESHOLD`；
  - `user_correction` 保存置信度改为至少 `95`；
  - `add_experience` 返回 `<=0` 计入失败数，避免静默丢失。

86. **经验库stale首条阻断问题修复**
- 文件：`main.py`
- 问题：
  - 经验匹配只取 `top_k=1`，若首条是 `stale` 会直接放弃，不再尝试次优有效记录。
- 修复：
  - `try_experience_match()` 检索改为 `top_k=3`；
  - 从返回结果中选择第一条非 `stale` 记录用于直通，全部 `stale` 才放弃。

87. **经验库检索降级链路优化（stale精确命中不阻断后续）**
- 文件：`src/experience_db.py`
- 问题：
  - 精确命中若版本过期会直接返回单条 `stale`，导致后续向量相似记录无法参与。
- 修复：
  - 精确命中为 `exact` 时仍直接返回；
  - 精确命中为 `stale` 时继续执行向量检索并合并结果（去重），避免阻断。

88. **few-shot 参考案例过滤过期经验**
- 文件：`src/experience_db.py`
- 问题：
  - `get_reference_cases()` 未过滤 `stale`，可能把旧版定额注入 LLM/Agent 上下文。
- 修复：
  - 组装参考案例时跳过 `match_type == 'stale'` 记录。

89. **学习入口与当前导出格式兼容性修复 + 参数校验覆盖补齐**
- 文件：
  - `src/feedback_learner.py`
  - `src/param_validator.py`
- 问题A（学习入口）：
  - `learn_from_corrected_excel()` 仅识别“清单/定额”标签行，和当前导出格式（序号清单行 + A列空白定额行）不一致。
  - `import_completed_project()` 对定额行仅识别 `C*`，漏掉其他编号前缀。
- 修复A：
  - 学习入口新增双格式识别：旧标签格式 + 当前导出格式；
  - 跨工作表读取并跳过“待审核/统计汇总”辅助页；
  - 定额行识别统一为通用正则 `^[A-Za-z]?\d{1,2}-\d+`。
- 问题B（参数校验）：
  - 系统已提取 `circuits/ampere`，但 `_check_params()` 未参与判定。
- 修复B：
  - 新增“回路数/电流”硬参数校验（精确匹配、向上取档、超档失败）。

### 本轮补充验证
- `python -m py_compile main.py src/experience_db.py src/param_validator.py src/feedback_learner.py pages/1_匹配定额.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python main.py --help` 通过。

90. **经验命中空定额防护**
- 文件：`main.py`
- 问题：
  - 脏数据场景下经验记录可能 `quota_ids` 为空，直通后会形成“高分但无定额”的异常结果。
- 修复：
  - `try_experience_match()` 在精确/相似分支都新增空定额防护；
  - 命中但定额列表为空时直接跳过该经验记录。

91. **经验更新置信度口径修复（尊重调用方下限）**
- 文件：`src/experience_db.py`
- 问题：
  - `_update_experience()` 在 `user_confirmed/user_correction/project_import` 分支仅做“现有分数+增量”，未保证调用方传入的 `confidence` 下限生效。
  - 会导致“页面已按阈值传入置信度，但更新老记录后仍低于阈值”。
- 修复：
  - `user_correction`：`confidence = MIN(MAX(confidence + 10, 入参下限), 100)`；
  - `user_confirmed`：`confidence = MIN(MAX(confidence + 5, 入参下限), 100)`；
  - `project_import`：`confidence = MIN(MAX(confidence + 2, 入参下限), 95)`。

92. **反馈学习输入长度不一致可观测性修复**
- 文件：`src/feedback_learner.py`
- 问题：
  - `learn_from_corrections()` 使用 `zip()` 静默截断，原始结果与修正结果长度不一致时会悄悄丢数据。
- 修复：
  - 改为先计算 `pair_count=min(len(original), len(corrected))`；
  - 长度不一致时输出明确告警，并只处理前 `pair_count` 条；
  - `stats.total` 改为实际处理条数，统计口径与执行一致。

### 本轮补充验证
- `python -m py_compile main.py src/experience_db.py src/feedback_learner.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python main.py --help` 通过。

93. **项目导入学习链路格式兼容修复（单位/描述列位次纠正）**
- 文件：`src/feedback_learner.py`
- 问题：
  - `import_completed_project()` 对当前导出格式兼容不足：
    - 清单行识别过窄；
    - 当前格式下把 `unit/description` 列位次读反（应为 D=描述，E=单位）。
- 修复：
  - 清单行识别改为“双格式兼容”：旧标签格式（清单/定额）+ 当前导出格式（A列数字序号 + A空且B为定额编号）；
  - 按格式分别映射字段，修正当前格式 `unit/description` 位次；
  - 定额编号识别统一使用正则 `^[A-Za-z]?\d{1,2}-\d+`。

### 本轮补充验证
- `python -m py_compile src/feedback_learner.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python src/feedback_learner.py --help` 通过。

94. **项目导入忽略辅助Sheet，避免脏经验注入**
- 文件：`src/feedback_learner.py`
- 问题：
  - 导入已完成项目时会遍历全部Sheet，包含“待审核/统计汇总”辅助页时可能误识别为清单数据。
- 修复：
  - `import_completed_project()` 增加辅助页跳过：`{"待审核", "统计汇总"}`。

### 本轮补充验证
- `python -m py_compile src/feedback_learner.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

95. **LLM结果“无主定额但高置信度”逻辑修复**
- 文件：`src/llm_matcher.py`
- 问题：
  - 解析大模型返回时，`confidence` 先被读取；若主定额未选中，仍可能保留高分。
  - 且在无主定额场景下仍可能接收 `related_quotas`，形成“无主定额但有匹配项”的异常结构。
- 修复：
  - 仅在主定额存在时才处理 `related_quotas`；
  - 当 `quotas` 为空时，强制 `confidence = 0`，并补默认 `no_match_reason`。

96. **Agent结果“无主定额但高置信度”逻辑修复**
- 文件：`src/agent_matcher.py`
- 问题：
  - Agent解析链路与LLM链路一致，存在“未选中主定额但保留高分”的风险。
  - 关联定额在无主定额时也可能被误接收。
- 修复：
  - 仅在主定额存在时才接收关联定额；
  - `quotas` 为空时强制将 `confidence` 归零，并保留无匹配原因。

97. **导入工具健壮性修复（坏行/异常结构不再中断）**
- 文件：`tools/import_reference.py`
- 问题：
  - 多处使用 `pair["..."]` / `q["..."]` 直接索引，遇到异常结构会抛 `KeyError`；
  - 摘要/示例/dry-run 输出对脏数据容错不足。
- 修复：
  - `convert_to_kb_records()` 改为 `get()` + 类型检查；
  - 解析摘要统计改为容错计算（含平均值防御）；
  - 示例与 dry-run 输出改为安全读取，异常结构自动跳过。

98. **规则匹配异常规则项容错修复（防止单条坏规则拖垮全流程）**
- 文件：`src/rule_validator.py`
- 问题：
  - 参数驱动/关键词驱动分支对规则项字段使用硬索引（如 `entry["family"]`、`entry["keywords"]`）；
  - 当规则数据出现异常结构时会抛异常，导致整批匹配中断。
- 修复：
  - 两条主匹配分支都改为安全读取并做类型校验；
  - 对坏规则项执行 `continue` 跳过，不影响其余规则匹配；
  - `best_entry` 收尾阶段增加 `family` 结构校验，避免尾部路径异常。

### 本轮补充验证
- `python -m py_compile src/llm_matcher.py src/agent_matcher.py tools/import_reference.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python tools/import_reference.py --help` 通过。
- `python main.py --help` 通过（仅有 `jieba/pkg_resources` 非阻断告警）。
- `python -m py_compile src/rule_validator.py` 通过。

99. **LLM/Agent JSON类型鲁棒性修复（防止字符串索引导致崩溃/误判）**
- 文件：
  - `src/llm_matcher.py`
  - `src/agent_matcher.py`
- 问题：
  - 模型常返回字符串类型字段（如 `"main_quota_index":"1"`、`"no_match":"false"`）；
  - 原逻辑直接做数值比较（`1 <= main_idx <= len(candidates)`）和布尔判断（`if not data.get("no_match", False)`），可能触发 `TypeError` 或把 `"false"` 误判为真。
- 修复：
  - 新增 `_to_int()` 与 `_to_bool()`，统一做索引与布尔语义解析；
  - 主定额索引、关联定额索引都改为安全整型解析；
  - `main_quota_id/related.quota_id` 对 `"null"/"none"` 字符串做空值归一；
  - Agent链路增加 `no_match` 门控，`no_match=true` 时不再解析主定额与关联定额。

### 本轮补充验证（追加）
- `python -m py_compile src/llm_matcher.py src/agent_matcher.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- 解析健壮性自测通过：
  - 字符串索引 `"1"` 可正常选中候选，不抛异常；
  - 字符串布尔 `"false"` 按假处理、`"true"` 按真处理；
  - `no_match=true` 时结果强制为空定额且 `confidence=0`。

100. **向量检索长度对齐与非法ID容错修复（防止静默截断/虚高分）**
- 文件：
  - `src/vector_engine.py`
  - `src/experience_db.py`
  - `src/universal_kb.py`
- 问题：
  - 向量返回的 `ids` 与 `distances` 在异常后端/旧索引场景可能长度不一致；
  - 原逻辑依赖 `zip` 会静默截断，或在缺失距离时出现默认高分风险；
  - `ids` 出现非整数字符串时可能抛异常中断。
- 修复：
  - 三处检索统一加“长度一致性防御”：截断/补齐并记录告警；
  - 缺失距离统一按最大距离 `1.0`（相似度 `0`）补齐，避免置信度虚高；
  - `id` 改为安全 `int` 转换，非法ID跳过并告警；
  - 有效ID为空时安全返回空结果/兜底结果，不抛异常。

### 本轮补充验证（追加）
- `python -m py_compile src/vector_engine.py src/experience_db.py src/universal_kb.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

114. **补齐ExperienceDB缺失查询接口（修复jarvis_store运行时崩溃）**
- 文件：
  - `src/experience_db.py`
- 问题：
  - `tools/jarvis_store.py` 的 `lookup()` 调用 `ExperienceDB.find_experience(...)`；
  - `ExperienceDB` 中缺失该方法，执行 `--lookup` 会触发 `AttributeError`。
- 修复：
  - 新增 `find_experience(self, bill_text, province=None, limit=20)`；
  - 支持按 `bill_text/bill_name` 精确命中 + `LIKE` 模糊命中；
  - 排序规则：精确命中优先，再按 `confidence/confirm_count/updated_at/id` 降序；
  - 输出前统一将 `quota_ids/quota_names` 用 `_safe_json_list()` 收敛为列表，避免脏数据影响调用方。

### 本轮补充验证（追加）
- `python -m py_compile src/experience_db.py tools/jarvis_store.py` 通过。
- `python tools/jarvis_store.py --help` 通过。

115. **新增pytest收集范围配置（修复无权限目录导致的测试中断）**
- 文件：
  - `pytest.ini`
- 问题：
  - 直接执行 `python -m pytest` 会从仓库根目录递归收集；
  - 根目录存在 `pytest-cache-files-*` 无权限路径，导致 `PermissionError` 并在收集阶段中断。
- 修复：
  - 新增 `pytest.ini`，将收集范围限定为 `tests`；
  - 显式配置 `norecursedirs = ... pytest-cache-files-*`，避免扫描无权限目录。

### 本轮补充验证（追加）
- `python -m pytest -q` 可正常进入测试执行阶段（不再因根目录权限问题中断）。

116. **评测脚本序号识别修复（避免Excel中的1.0序号被漏判）**
- 文件：
  - `tests/test_accuracy.py`
  - `tests/eval_golden.py`
- 问题：
  - 两个评测脚本都使用 `str(a).isdigit()` 判断清单行；
  - 当Excel把序号读取为 `1.0/2.0`（float）时会判定失败，导致清单-定额配对丢失，评测结果失真。
- 修复：
  - 新增 `_is_bill_serial()`，统一兼容 `int/float/字符串(含*.0)` 序号；
  - `load_test_cases()` 与 `_read_sheet_pairs()` 改为使用该函数识别清单行。

### 本轮补充验证（追加）
- `python -m py_compile tests/test_accuracy.py tests/eval_golden.py` 通过。
- `python tests/test_accuracy.py --help` 通过。
- `python tests/eval_golden.py --help` 通过。

107. **匹配页面结果状态与统计容错修复（空结果/脏统计可稳定展示）**
- 文件：`pages/1_匹配定额.py`
- 问题：
  - 匹配完成后使用 `if results:` 判定，空列表会被误认为失败，页面无法切到结果态；
  - `stats` 缺字段或类型异常时，顶部指标 `stats[...]` 直接索引可能报错；
  - JSON返回 `results` 非列表时缺少明确拦截。
- 修复：
  - 新增 `_normalize_stats()`、`_safe_int()`、`_safe_float()`；
  - 匹配成功判定改为 `if results is not None`，空结果也能进入结果页；
  - 结果页统一使用标准化 `stats`，避免KeyError/类型错误；
  - `run_matching()` 增加 `results` 类型校验（非列表直接报错并中止）。

108. **匹配页面交互与保存链路防崩修复（脏数据结构兼容）**
- 文件：`pages/1_匹配定额.py`
- 问题：
  - `quotas/confidence` 假定类型固定，脏数据会在详情展示、弹窗插入/替换、经验保存时报错；
  - `st.dataframe` 选中行在不同版本返回结构不一致，原逻辑可能索引越界/类型错误；
  - `save_to_experience_db()` 对 `confidence` 与 quota项结构缺少鲁棒处理。
- 修复：
  - 新增 `_ensure_list()`、`_safe_confidence()`、`_resolve_selected_quota()`；
  - 详情面板/表格构建/批量确认/弹窗插入替换删除/经验保存全部改为安全列表与安全置信度口径；
  - 弹窗对无效选中行给出提示并安全退出；
  - 保存经验时对索引、定额项、置信度做类型收敛，避免批量保存中途崩溃。

### 本轮补充验证（追加）
- `python -m py_compile pages/1_匹配定额.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

109. **审核批次序号错位修复（最后一批序号漂移）**
- 文件：`tools/review_test.py`
- 问题：
  - `write_batch()` 用 `(batch_num - 1) * len(results)` 计算批次起点；
  - 当最后一批条数不足 `batch_size` 时，全局序号会错位（影响人工审核定位）。
- 修复：
  - `write_batch()` 改为显式接收 `start_idx`（由调用方传入真实切片起点）；
  - 同步更新调用处参数传递。
- 附加稳健性：
  - 增加 `_safe_confidence()`；
  - 备选定额与主定额输出增加类型校验，避免脏数据 `KeyError/TypeError`。

110. **AG Grid选中行类型守卫修复（防止非dict结构触发崩溃）**
- 文件：`pages/1_匹配定额.py`
- 问题：
  - 兼容分支中 `selected_rows[0]` 默认按 dict 使用；
  - 若组件返回非dict结构，后续 `row_data.get(...)` 会触发异常。
- 修复：
  - 仅当 `selected_rows[0]` 为 dict 时才进入该分支，其他结构安全忽略。

### 本轮补充验证（追加）
- `python -m py_compile tools/review_test.py` 通过。
- `python tools/review_test.py --help` 通过。
- `python -m py_compile pages/1_匹配定额.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

111. **设置页.env读写异常可观测化修复**
- 文件：`pages/4_设置.py`
- 问题：
  - `.env` 读写异常会直接冒泡，导致设置页中断；
  - 失败场景下临时文件可能残留。
- 修复：
  - `_merge_env_updates()` 增加读写异常封装与明确错误信息；
  - 写入过程增加 `finally` 清理临时文件；
  - 设置页读取 `.env` 失败时改为 `st.warning` + 默认值回退；
  - 点击“保存API配置”时增加异常捕获并在页面显示失败原因。

112. **经验库页面统计字段缺失容错修复**
- 文件：`pages/3_经验库.py`
- 问题：
  - 统计面板直接索引 `stats['total']` 等字段；
  - 若统计结构变更或局部缺字段可能触发 `KeyError`。
- 修复：
  - 统计指标改为 `stats.get(...)` 读取并提供默认值，避免页面崩溃。

### 本轮补充验证（追加）
- `python -m py_compile pages/3_经验库.py pages/4_设置.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

113. **定额数据库页统计与搜索结果容错修复**
- 文件：`pages/2_定额数据库.py`
- 问题：
  - 概览页统计字段使用直接索引，字段缺失时可能 `KeyError`；
  - 搜索结果默认假定为列表且元素为dict，异常返回结构会触发渲染错误。
- 修复：
  - 新增 `_ensure_list()`；
  - 统计指标改为 `stats.get(...)` 读取；
  - 搜索结果统一收敛为列表，并对非dict结果项跳过处理。

### 本轮补充验证（追加）
- `python -m py_compile pages/2_定额数据库.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。

105. **输出链路防崩增强（序号识别/定额识别/类型收敛）**
- 文件：`src/output_writer.py`
- 问题：
  - 清单行识别仅用 `str(a_val).isdigit()`，`1.0` 这类序号会漏识别；
  - 旧定额行删除仅识别 `X-XXX`，`D00003`/带`换`后缀可能残留；
  - `confidence/quotas/alternatives` 若为脏类型（字符串/None）会在导出阶段触发类型异常。
- 修复：
  - 新增 `_is_bill_serial()`，兼容 `int/float/字符串(含*.0)` 序号识别；
  - 新增 `_is_quota_code()`，扩展定额编号识别（`X-XXX` + `D00003` + `*换`）；
  - 新增 `_safe_confidence()` 与 `_ensure_list()`，统一收敛导出输入类型，避免导出崩溃；
  - 统计页中高/中/低置信度分组改为安全数值口径。

106. **清单读取稳定性修复（资源释放与数量解析）**
- 文件：`src/bill_reader.py`
- 问题：
  - `read_excel()` / `get_sheet_info()` 未用 `finally` 关闭工作簿，异常路径可能资源泄露；
  - 工程量字符串含千分位逗号时（如 `1,200`）会解析失败，影响后续逻辑判定；
  - 定额行过滤未覆盖 `D00003/AD0003` 风格编号，存在误读为清单项风险。
- 修复：
  - 两个入口都改为 `try/finally` 关闭 `openpyxl` 工作簿；
  - 数量解析增加逗号清洗后再 `float()`；
  - 新增 `_is_quota_code()` 并用于定额行过滤（支持 `X-XXX`、字母前缀数字、`换`后缀）。

### 本轮补充验证（追加）
- `python -m py_compile src/output_writer.py src/bill_reader.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- 关键函数自测通过：
  - `_is_bill_serial(1/1.0/'2.0') == True`
  - `_is_quota_code('C4-4-31') == True`
  - `_is_quota_code('D00003') == True`
  - `_is_quota_code('010101') == False`

102. **专业分类fallback标准化修复（防止类型异常导致主流程崩溃）**
- 文件：`main.py`
- 问题：
  - 分类结果中的 `fallbacks` 假定为 `list`，但脏数据场景可能是 `None/字符串/其他类型`；
  - 现有逻辑直接做 `[primary] + fallbacks`，会触发 `TypeError` 中断匹配流程。
- 修复：
  - 新增 `_normalize_fallbacks()` 与 `_normalize_classification()`；
  - 在 `cascade_search()` 和 `search/full/agent` 三个主流程入口统一做分类结果标准化；
  - 自动去重并剔除与 `primary` 重复的 fallback，保证后续拼接稳定。

### 本轮补充验证（追加）
- `python -m py_compile main.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python main.py --help` 通过（仅有 `jieba/pkg_resources` 非阻断告警）。

103. **Agent审核/调试工具fallback容错修复（防止工具链崩溃）**
- 文件：
  - `tools/agent_review.py`
  - `tools/agent_debug.py`
- 问题：
  - 两个工具默认假设 `fallbacks` 一定是列表；
  - 当输入是 `None/字符串/脏类型` 时会在 `[primary] + fallbacks` 处触发 `TypeError`。
- 修复：
  - 两个工具增加 fallback 归一化逻辑（类型收敛、去重、去空、剔除与 `primary` 重复项）；
  - 保证工具链在脏输入下继续可用。

104. **Agent审核工具CLI标准化修复（--help 不再误读为文件）**
- 文件：`tools/agent_review.py`
- 问题：
  - 原入口手写 `sys.argv` 解析，`--help` 会被当作 Excel 路径触发 `FileNotFoundError`。
- 修复：
  - 改为 `argparse` 标准入口，支持：
    - 导出模式：`python tools/agent_review.py <excel_path> --limit N`
    - 回写模式：`python tools/agent_review.py --store <decisions.json>`
  - `--help` 可正常显示参数说明和示例。

### 本轮补充验证（追加）
- `python -m py_compile tools/agent_review.py tools/agent_debug.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
- `python tools/agent_review.py --help` 通过。
- `python main.py --help` 通过（仅有 `jieba/pkg_resources` 非阻断告警）。

101. **向量查询结果字段缺失容错修复（防止KeyError）**
- 文件：
  - `src/vector_engine.py`
  - `src/experience_db.py`
  - `src/universal_kb.py`
- 问题：
  - 多处直接使用 `results["ids"]` / `search_results["ids"]`；
  - 当向量后端返回结构不完整时可能触发 `KeyError`，导致查询流程异常中断。
- 修复：
  - 全部改为 `get("ids")` 安全读取；
  - 无 `ids` 时直接返回空结果/兜底结果，不抛异常。

### 本轮补充验证（追加）
- `python -m py_compile src/vector_engine.py src/experience_db.py src/universal_kb.py` 通过。
- `python -m compileall -q src pages tools tests main.py` 通过。
