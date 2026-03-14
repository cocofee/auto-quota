/**
 * 版本更新日志
 *
 * 每次发版往数组最前面加一条即可。
 * APP_VERSION 保持和 lzc-manifest.yml 中的 version 一致。
 *
 * 分类规则：
 * - type: 'user'  → 所有用户可见（定额匹配、经验库、准确率等用户关心的改动）
 * - type: 'admin' → 仅管理员可见（部署、重构、CI/CD、内部优化等技术改动）
 */

export const APP_VERSION = '0.2.23';

/** 更新类型：user=用户可见, admin=仅管理员可见 */
export type ChangeType = 'user' | 'admin';

/** 单条更新 */
export interface ChangeItem {
  type: ChangeType;
  text: string;
}

export interface ChangelogEntry {
  version: string;
  date: string;       // YYYY-MM-DD
  changes: ChangeItem[];
}

export const CHANGELOG: ChangelogEntry[] = [
  {
    version: '0.2.23',
    date: '2026-03-14',
    changes: [
      { type: 'user', text: '修复Sheet选择不生效的bug（选了特定Sheet但后端仍跑全部）' },
      { type: 'user', text: '新建任务页面大改版：紧凑布局+左右分栏（Sheet列表+预览表格）' },
      { type: 'user', text: '智能Sheet识别：按内容检测清单表（含"分部分项"），自动跳过汇总/措施/规费表' },
      { type: 'user', text: '每个Sheet显示预估清单条数，勾选时自动预览内容' },
    ],
  },
  {
    version: '0.2.17',
    date: '2026-03-14',
    changes: [
      { type: 'user', text: '新增匹配模式选择：快速匹配（纯搜索秒出）/ 精准匹配（大模型分析）' },
      { type: 'admin', text: 'Agent精简为1次LLM调用（关闭重试+后验证），切换DeepSeek模型' },
    ],
  },
  {
    version: '0.2.16',
    date: '2026-03-12',
    changes: [
      { type: 'admin', text: '阶段零完成（全量出卷21省5207题，基线33.6%）' },
      { type: 'admin', text: '实战工具三件套 + jarvis_learn分层存储' },
      { type: 'admin', text: 'Autoresearch方案（program.md）+ 脏数据试卷（9份666题）' },
      { type: 'admin', text: 'Benchmark试卷扩充（7省→10省，2177→3431题）' },
      { type: 'admin', text: '更新批量匹配bat脚本' },
      { type: 'admin', text: 'V4训练准备（LTR特征扩展+OSS批量导入+知识库快速模式）' },
      { type: 'admin', text: 'PDF信息价导入工具（8省6个profile+上海API，77万条信息价）' },
      { type: 'admin', text: '离线诊断分桶（bucket子命令，5维深度分析）' },
      { type: 'admin', text: '合并4个经验库工具为experience_manager.py统一入口' },
      { type: 'admin', text: '清理4个冗余工具脚本' },
      { type: 'admin', text: '同义词错题精补（+7对，benchmark未退化）' },
      { type: 'admin', text: 'jarvis_diagnose统一入口（benchmark-fix+4工具合并）' },
      { type: 'admin', text: '添加批量扫描bat脚本' },
      { type: 'admin', text: '批量匹配优化（省份轮转+白天/晚上模式+进度显示）' },
      { type: 'admin', text: '同义词自动挖掘扩展（25→39条，+14条全局词）' },
      { type: 'admin', text: '算法版本自动计算（替代手动写死的版本号）' },
      { type: 'admin', text: '过滤分部分项小节行（C.4.3等章节标题不套定额）' },
      { type: 'admin', text: 'LTR排序优化v3（21→23维特征+超参优化，48.6%→51.8%）' },
      { type: 'admin', text: '电缆截面规则匹配+BTLY矿物电缆路由修复' },
      { type: 'admin', text: 'V3部署准备（模型路径切换+安装方式兼容修正+全流程bat）' },
    ],
  },
  {
    version: '0.2.15',
    date: '2026-03-09',
    changes: [
      { type: 'user', text: 'Excel上传后可预览Sheet内容（点击Sheet名查看前30行数据）' },
    ],
  },
  {
    version: '0.2.14',
    date: '2026-03-09',
    changes: [
      { type: 'admin', text: 'API模式改造——懒猫轻量前端+本地电脑算力（方案B）' },
      { type: 'admin', text: 'benchmark基线更新（LTR模型后44.4%）' },
      { type: 'admin', text: '懒猫部署优化（离线模型+向量搜索+LTR依赖）' },
      { type: 'admin', text: '懒猫部署配置更新+前端changelog' },
      { type: 'admin', text: 'LTR排序模型上线（LightGBM LambdaRank，39.0%→44.4%）' },
      { type: 'admin', text: 'Docker添加向量搜索依赖（sentence-transformers+chromadb）' },
      { type: 'admin', text: '刷油防腐保温同义词+8条（C12册术语映射）' },
      { type: 'admin', text: '安装方式参数提取与验证（明装/暗装/落地/挂墙/嵌入/吊装）' },
      { type: 'admin', text: '前端页面细节优化（日志查看器/看板/任务列表）' },
      { type: 'admin', text: '品类词硬路由（从9.8万经验库挖掘品类词→册号映射）' },
      { type: 'admin', text: '分析页面细节修复（用户反馈+Codex审查）' },
    ],
  },
  {
    version: '0.2.13',
    date: '2026-03-08',
    changes: [
      { type: 'admin', text: '懒猫部署配置更新+前端changelog' },
      { type: 'admin', text: 'LTR排序模型上线（LightGBM LambdaRank，39.0%→44.4%）' },
      { type: 'admin', text: 'Docker添加向量搜索依赖（sentence-transformers+chromadb）' },
      { type: 'admin', text: '刷油防腐保温同义词+8条（C12册术语映射）' },
      { type: 'admin', text: '安装方式参数提取与验证（明装/暗装/落地/挂墙/嵌入/吊装）' },
      { type: 'admin', text: '前端页面细节优化（日志查看器/看板/任务列表）' },
      { type: 'admin', text: '品类词硬路由（从9.8万经验库挖掘品类词→册号映射）' },
      { type: 'admin', text: '分析页面细节修复（用户反馈+Codex审查）' },
    ],
  },
  {
    version: '0.2.12',
    date: '2026-03-08',
    changes: [
      { type: 'admin', text: 'Docker添加向量搜索依赖（sentence-transformers+chromadb）' },
      { type: 'admin', text: '刷油防腐保温同义词+8条（C12册术语映射）' },
      { type: 'admin', text: '安装方式参数提取与验证（明装/暗装/落地/挂墙/嵌入/吊装）' },
      { type: 'admin', text: '前端页面细节优化（日志查看器/看板/任务列表）' },
      { type: 'admin', text: '品类词硬路由（从9.8万经验库挖掘品类词→册号映射）' },
      { type: 'admin', text: '分析页面细节修复（用户反馈+Codex审查）' },
    ],
  },
  {
    version: '0.2.11',
    date: '2026-03-08',
    changes: [
      { type: 'admin', text: '删除未使用的COLORS导入（修复TS构建失败）' },
      { type: 'admin', text: '准确率分析页面拆分为4个Tab组件' },
      { type: 'admin', text: '省份统计API增加匹配条数和平均置信度' },
      { type: 'admin', text: 'analytics API防御性修复（Codex审查）' },
      { type: 'admin', text: '三级审核流程漏洞修复+Codex审查修复' },
    ],
  },
  {
    version: '0.2.10',
    date: '2026-03-08',
    changes: [
      { type: 'admin', text: '准确率分析页面拆分为4个Tab组件' },
      { type: 'admin', text: '省份统计API增加匹配条数和平均置信度' },
      { type: 'admin', text: 'analytics API防御性修复（Codex审查）' },
      { type: 'admin', text: '三级审核流程漏洞修复+Codex审查修复' },
    ],
  },
  {
    version: '0.2.9',
    date: '2026-03-08',
    changes: [
      { type: 'admin', text: '准确率分析页面加入折线图（任务趋势+跑分趋势）' },
    ],
  },
  {
    version: '0.2.8',
    date: '2026-03-08',
    changes: [
      { type: 'admin', text: '管理员视图显示任务所属用户名' },
      { type: 'admin', text: 'TaskResponse增加username字段供管理员查看' },
      { type: 'admin', text: '更新benchmark基线和历史记录（3个同义词写入后）' },
      { type: 'admin', text: '诊断报告OB命名优化 + v0.2.7版本号' },
      { type: 'admin', text: '前端工作表智能选择修复 + 移除知识库页面' },
    ],
  },
  {
    version: '0.2.7',
    date: '2026-03-08',
    changes: [
      { type: 'admin', text: 'v0.2.7 更新' },
    ],
  },
  {
    version: '0.2.6',
    date: '2026-03-08',
    changes: [
      { type: 'admin', text: 'param_validator增加param_tier字段用于候选排序' },
      { type: 'admin', text: '添加extract_manual_items辅助提取工具' },
      { type: 'admin', text: '新增jarvis_diagnose诊断工具，分析人工审核项根因' },
      { type: 'admin', text: 'pipeline自动挂载兄弟库，用户不需要手动指定--aux-province' },
      { type: 'admin', text: '跨库纠正+清单编码辅助专业分类' },
      { type: 'admin', text: 'export_quota_excel支持flat编码格式和行业版目录结构' },
      { type: 'admin', text: '统一兼容性词典+修复2个bug+删7个废弃工具（净删1100+行）' },
      { type: 'admin', text: '加回38个自映射"保护伞"同义词+2个有效映射，修复北京退化' },
      { type: 'admin', text: 'Codex审查修复——同义词排序+循环引用+异常收窄+速率限制+De外径' },
      { type: 'admin', text: 'P0防御性修复——空列表IndexError+浮点比较精度+int转换防护' },
      { type: 'admin', text: 'Codex 5.3审查意见修复——密钥占位符+测试适配+编码兼容' },
    ],
  },
  {
    version: '0.2.5',
    date: '2026-03-07',
    changes: [
      { type: 'admin', text: '补漏——pull_history加gitignore并移出git，删docs/archive空目录' },
      { type: 'admin', text: '修复test_connection_close_resilience引用已删模块导致测试阻断' },
      { type: 'admin', text: '系统清理——删除30+废弃文件，补.gitignore' },
      { type: 'admin', text: '同义词表清理——删39个精确自映射+修5个错误映射+新增分析工具' },
      { type: 'admin', text: '未匹配定额不再插入提示行，没有定额时不写主材行' },
      { type: 'admin', text: '主材行只用输入文件的source_materials，不从经验库凭空加' },
      { type: 'admin', text: '输出Excel带上主材行——读取提取+输出写入+学习保存' },
      { type: 'admin', text: '辅助库搜索路由修复——主库辅助库并行搜索+清单编码过滤' },
    ],
  },
  {
    version: '0.2.4',
    date: '2026-03-07',
    changes: [
      { type: 'admin', text: '补漏——pull_history加gitignore并移出git，删docs/archive空目录' },
      { type: 'admin', text: '修复test_connection_close_resilience引用已删模块导致测试阻断' },
      { type: 'admin', text: '系统清理——删除30+废弃文件，补.gitignore' },
      { type: 'admin', text: '同义词表清理——删39个精确自映射+修5个错误映射+新增分析工具' },
      { type: 'admin', text: '未匹配定额不再插入提示行，没有定额时不写主材行' },
      { type: 'admin', text: '主材行只用输入文件的source_materials，不从经验库凭空加' },
      { type: 'admin', text: '输出Excel带上主材行——读取提取+输出写入+学习保存' },
      { type: 'admin', text: '辅助库搜索路由修复——主库辅助库并行搜索+清单编码过滤' },
    ],
  },
  {
    version: '0.2.3',
    date: '2026-03-07',
    changes: [
      { type: 'admin', text: 'fix(web): 前端26项Bug修复与体验优化' },
      { type: 'admin', text: '积累的防御性修复和规则优化' },
      { type: 'admin', text: 'M2召回改善——新增14条同义词覆盖高频缺口' },
      { type: 'admin', text: '排序权重调整——name_bonus提权，减少品类选错' },
      { type: 'admin', text: '置信度校准v2——多信号加权替代param_score×95' },
    ],
  },
  {
    version: '0.2.2',
    date: '2026-03-07',
    changes: [
      { type: 'admin', text: 'fix(web): 前端26项Bug修复与体验优化' },
      { type: 'admin', text: '积累的防御性修复和规则优化' },
      { type: 'admin', text: 'M2召回改善——新增14条同义词覆盖高频缺口' },
      { type: 'admin', text: '排序权重调整——name_bonus提权，减少品类选错' },
      { type: 'admin', text: '置信度校准v2——多信号加权替代param_score×95' },
    ],
  },
  {
    version: '0.2.0',
    date: '2026-03-06',
    changes: [
      { type: 'user', text: '验证模型升级为Claude Opus 4.6（匹配准确率大幅提升）' },
      { type: 'admin', text: '懒猫验证模型从Kimi切换为Claude Opus（中转直连，无需代理）' },
      { type: 'admin', text: 'Agent匹配prompt增加专业分流注意事项和易混淆品类提示' },
    ],
  },
  {
    version: '0.1.99',
    date: '2026-03-06',
    changes: [
      { type: 'user', text: 'Agent匹配质量大幅提升：修复数据库配置含不可见字符导致LLM调用ASCII编码错误' },
      { type: 'admin', text: '数据库LLM配置注入前增加ASCII清洗（去BOM/零宽空格/非ASCII字符）' },
      { type: 'admin', text: 'agent_matcher和llm_verifier的httpx调用增加防御性ASCII清洗' },
    ],
  },
  {
    version: '0.1.97',
    date: '2026-03-06',
    changes: [
      { type: 'admin', text: 'v0.1.97 更新' },
    ],
  },
  {
    version: '0.1.96',
    date: '2026-03-06',
    changes: [
      { type: 'user', text: 'Agent匹配质量大幅提升：修复大模型调用编码错误，LLM验证不再全部降级' },
      { type: 'user', text: 'LLM调用加429限流自动重试（等2秒×3次），避免熔断' },
      { type: 'admin', text: 'LLM调用从OpenAI SDK改为httpx直接请求（绕过容器ascii编码bug）' },
      { type: 'admin', text: '懒猫manifest补全千问+Kimi真实API Key和BASE_URL' },
      { type: 'admin', text: 'LLM验证并发从8路降为3路（避免千问429限流）' },
      { type: 'admin', text: '向量搜索开关补全：experience_db/reranker在VECTOR_ENABLED=false时跳过' },
      { type: 'admin', text: 'Docker镜像精简：去torch/chromadb，13GB→~1GB' },
      { type: 'admin', text: 'Dockerfile写死UTF-8编码环境变量' },
    ],
  },
  {
    version: '0.1.91',
    date: '2026-03-06',
    changes: [
      { type: 'admin', text: '更新benchmark基线和历史数据' },
      { type: 'admin', text: '向量搜索开关+Docker精简（懒猫无GPU场景优化）' },
      { type: 'admin', text: 'Codex 5.3审查修复7项批量工具问题' },
    ],
  },
  {
    version: '0.1.90',
    date: '2026-03-06',
    changes: [
      { type: 'admin', text: 'v0.1.90 更新' },
    ],
  },
  {
    version: '0.1.89',
    date: '2026-03-06',
    changes: [
      { type: 'admin', text: 'v0.1.89 更新' },
    ],
  },
  {
    version: '0.1.88',
    date: '2026-03-06',
    changes: [
      { type: 'admin', text: 'v0.1.88 更新' },
    ],
  },
  {
    version: '0.1.87',
    date: '2026-03-06',
    changes: [
      { type: 'admin', text: 'v0.1.87 更新' },
    ],
  },
  {
    version: '0.1.86',
    date: '2026-03-06',
    changes: [
      { type: 'admin', text: 'v0.1.86 更新' },
    ],
  },
  {
    version: '0.1.85',
    date: '2026-03-06',
    changes: [
      { type: 'admin', text: 'v0.1.85 更新' },
    ],
  },
  {
    version: '0.1.84',
    date: '2026-03-06',
    changes: [
      { type: 'admin', text: 'v0.1.84 更新' },
    ],
  },
  {
    version: '0.1.83',
    date: '2026-03-05',
    changes: [
      { type: 'admin', text: 'v0.1.83 更新' },
    ],
  },
  {
    version: '0.1.82',
    date: '2026-03-05',
    changes: [
      { type: 'admin', text: 'v0.1.82 更新' },
    ],
  },
  {
    version: '0.1.81',
    date: '2026-03-05',
    changes: [
      { type: 'admin', text: '更新benchmark试卷（清洗后11省2174条）和工具' },
      { type: 'admin', text: '搜索词优化4项，基线30.3%→33.4%（+3.1%）' },
      { type: 'admin', text: '删除model_cache和document挂载，按懒猫客服要求' },
      { type: 'admin', text: 'pkgout改为./lpk/，按懒猫客服建议' },
      { type: 'admin', text: '创建独立content目录，避免杂文件干扰LPK构建' },
      { type: 'admin', text: 'lzc-build.yml contentdir改回./，修复LPK构建' },
    ],
  },
  {
    version: '0.1.80',
    date: '2026-03-05',
    changes: [
      { type: 'admin', text: '更新benchmark试卷（清洗后11省2174条）和工具' },
      { type: 'admin', text: '搜索词优化4项，基线30.3%→33.4%（+3.1%）' },
      { type: 'admin', text: '删除model_cache和document挂载，按懒猫客服要求' },
      { type: 'admin', text: 'pkgout改为./lpk/，按懒猫客服建议' },
      { type: 'admin', text: '创建独立content目录，避免杂文件干扰LPK构建' },
      { type: 'admin', text: 'lzc-build.yml contentdir改回./，修复LPK构建' },
    ],
  },
  {
    version: '0.1.79',
    date: '2026-03-05',
    changes: [
      { type: 'admin', text: '更新benchmark试卷（清洗后11省2174条）和工具' },
      { type: 'admin', text: '搜索词优化4项，基线30.3%→33.4%（+3.1%）' },
      { type: 'admin', text: '删除model_cache和document挂载，按懒猫客服要求' },
      { type: 'admin', text: 'pkgout改为./lpk/，按懒猫客服建议' },
      { type: 'admin', text: '创建独立content目录，避免杂文件干扰LPK构建' },
      { type: 'admin', text: 'lzc-build.yml contentdir改回./，修复LPK构建' },
    ],
  },
  {
    version: '0.1.78',
    date: '2026-03-05',
    changes: [
      { type: 'admin', text: '更新benchmark试卷（清洗后11省2174条）和工具' },
      { type: 'admin', text: '搜索词优化4项，基线30.3%→33.4%（+3.1%）' },
      { type: 'admin', text: '删除model_cache和document挂载，按懒猫客服要求' },
      { type: 'admin', text: 'pkgout改为./lpk/，按懒猫客服建议' },
      { type: 'admin', text: '创建独立content目录，避免杂文件干扰LPK构建' },
      { type: 'admin', text: 'lzc-build.yml contentdir改回./，修复LPK构建' },
    ],
  },
  {
    version: '0.1.76',
    date: '2026-03-04',
    changes: [
      { type: 'admin', text: 'v0.1.76 更新' },
    ],
  },
  {
    version: '0.1.75',
    date: '2026-03-04',
    changes: [
      { type: 'admin', text: 'v0.1.75 更新' },
    ],
  },
  {
    version: '0.1.74',
    date: '2026-03-04',
    changes: [
      { type: 'admin', text: 'v0.1.74 更新' },
    ],
  },
  {
    version: '0.1.73',
    date: '2026-03-04',
    changes: [
      { type: 'admin', text: 'v0.1.73 更新' },
    ],
  },
  {
    version: '0.1.72',
    date: '2026-03-04',
    changes: [
      { type: 'admin', text: '移除未使用的CheckCircleOutlined导入，修复TS编译报错' },
      { type: 'admin', text: '新建任务页改进 — 上传文件显示+左侧Sheet智能选择面板' },
      { type: 'admin', text: '电气算法改进 — 周长计算+桥架路由+同义词修复' },
      { type: 'admin', text: '算法改进 — 批量红灯根因修复×9' },
    ],
  },
  {
    version: '0.1.71',
    date: '2026-03-04',
    changes: [
      { type: 'admin', text: '新建任务页改进 — 上传文件显示+左侧Sheet智能选择面板' },
      { type: 'admin', text: '电气算法改进 — 周长计算+桥架路由+同义词修复' },
      { type: 'admin', text: '算法改进 — 批量红灯根因修复×9' },
    ],
  },
  {
    version: '0.1.70',
    date: '2026-03-04',
    changes: [
      { type: 'admin', text: '双模型支持 + Excel原子写入重试 + API Key脱敏' },
      { type: 'admin', text: '算法改进 — 费用过滤+标题行过滤+通用定额降权调优' },
      { type: 'admin', text: '批量匹配改进 + 微信文件自动提取工具' },
      { type: 'admin', text: '环境配置优化 + 前端提示改进 + 文档更新' },
    ],
  },
  {
    version: '0.1.69',
    date: '2026-03-03',
    changes: [
      { type: 'admin', text: '三Agent架构后端优化 + Codex审核修复' },
      { type: 'admin', text: '双模型支持 + 验证并发优化 + 懒猫部署自动同步本地Docker' },
      { type: 'admin', text: '移除oss_samples + 更新跨省基线数据' },
      { type: 'admin', text: '新疆地区分组选择 + 侧边栏显示最新更新' },
    ],
  },
  {
    version: '0.1.68',
    date: '2026-03-03',
    changes: [
      { type: 'admin', text: '三Agent架构后端优化 + Codex审核修复' },
      { type: 'admin', text: '双模型支持 + 验证并发优化 + 懒猫部署自动同步本地Docker' },
      { type: 'admin', text: '移除oss_samples + 更新跨省基线数据' },
      { type: 'admin', text: '新疆地区分组选择 + 侧边栏显示最新更新' },
    ],
  },
  {
    version: '0.1.67',
    date: '2026-03-03',
    changes: [
      { type: 'admin', text: '双模型支持 + 验证并发优化 + 懒猫部署自动同步本地Docker' },
      { type: 'admin', text: '移除oss_samples + 更新跨省基线数据' },
      { type: 'admin', text: '新疆地区分组选择 + 侧边栏显示最新更新' },
    ],
  },
  {
    version: '0.1.66',
    date: '2026-03-03',
    changes: [
      { type: 'admin', text: '双模型支持 + 验证并发优化 + 懒猫部署自动同步本地Docker' },
      { type: 'admin', text: '移除oss_samples + 更新跨省基线数据' },
      { type: 'admin', text: '新疆地区分组选择 + 侧边栏显示最新更新' },
    ],
  },
  {
    version: '0.1.65',
    date: '2026-03-03',
    changes: [
      { type: 'admin', text: '双模型支持 + 验证并发优化 + 懒猫部署自动同步本地Docker' },
      { type: 'admin', text: '移除oss_samples + 更新跨省基线数据' },
      { type: 'admin', text: '新疆地区分组选择 + 侧边栏显示最新更新' },
    ],
  },
  {
    version: '0.1.64',
    date: '2026-03-03',
    changes: [
      { type: 'admin', text: '双模型支持 + 验证并发优化 + 懒猫部署自动同步本地Docker' },
      { type: 'admin', text: '移除oss_samples + 更新跨省基线数据' },
      { type: 'admin', text: '新疆地区分组选择 + 侧边栏显示最新更新' },
    ],
  },
  {
    version: '0.1.63',
    date: '2026-03-03',
    changes: [
      { type: 'user', text: '新疆地区分组选择（先选新疆，再选地区）' },
      { type: 'admin', text: '新疆18地区定额库合并为二级选择' },
      { type: 'admin', text: '全国202个定额库向量索引构建完成' },
    ],
  },
  {
    version: '0.1.62',
    date: '2026-03-03',
    changes: [
      { type: 'admin', text: 'v0.1.62 更新' },
    ],
  },
  {
    version: '0.1.61',
    date: '2026-03-02',
    changes: [
      { type: 'admin', text: '经验库管理页精简为数据看板' },
    ],
  },
  {
    version: '0.1.60',
    date: '2026-03-02',
    changes: [
      { type: 'admin', text: '清理残留品牌信息（benchmark数据集名+changelog）' },
    ],
  },
  {
    version: '0.1.59',
    date: '2026-03-02',
    changes: [
      { type: 'admin', text: 'Results页补漏GREEN_THRESHOLD导入' },
      { type: 'admin', text: '跨省基线数据+query_builder微调' },
      { type: 'admin', text: '经验库广联达风格改版+全站颜色统一' },
      { type: 'admin', text: '清理第三方品牌信息' },
    ],
  },
  {
    version: '0.1.58',
    date: '2026-03-02',
    changes: [
      { type: 'admin', text: '跨省基线数据+query_builder微调' },
      { type: 'admin', text: '经验库广联达风格改版+全站颜色统一' },
      { type: 'admin', text: '清理第三方品牌信息' },
    ],
  },
  {
    version: '0.1.57',
    date: '2026-03-02',
    changes: [
      { type: 'admin', text: 'v0.1.57 更新' },
    ],
  },
  {
    version: '0.1.56',
    date: '2026-03-02',
    changes: [
      { type: 'admin', text: '跨省试卷扩充到11省+基线更新（22.1%）' },
      { type: 'admin', text: '外部XML解析修复（清单记录嵌套在标题内）' },
      { type: 'admin', text: '经验库向量索引重建改为GPU批量编码+大批写入' },
      { type: 'admin', text: '配管材质代号映射优化（SC/JDG/KBG等→定额名称）' },
      { type: 'admin', text: '分类器添加C13其他及附属工程（BOOKS/借用/路由全套）' },
      { type: 'admin', text: '分类器添加通用跨册路由（烘手器→C4、医疗气体→C8）' },
      { type: 'admin', text: '同义词表+15条（山东2025交底13册缺口修补）' },
    ],
  },
  {
    version: '0.1.55',
    date: '2026-03-01',
    changes: [
      { type: 'admin', text: 'v0.1.55 更新' },
    ],
  },
  {
    version: '0.1.54',
    date: '2026-03-01',
    changes: [
      { type: 'user', text: '经验库验证增强，参数验证relaxed模式（容错更好）' },
      { type: 'admin', text: '自进化 — 从13222张经验卡片挖掘90+同义词' },
      { type: 'admin', text: '清理旧版lpk安装包（已加入.gitignore）' },
    ],
  },
  {
    version: '0.1.29',
    date: '2026-02-28',
    changes: [
      { type: 'user', text: '圆形风管/阀门周长参数修复（φ直径自动转周长）' },
      { type: 'admin', text: '管理页面切回浏览器自动刷新数据' },
      { type: 'admin', text: 'ChromaDB索引格式不兼容时自动重建' },
      { type: 'admin', text: '经验库删除同步清理向量索引' },
    ],
  },
  {
    version: '0.1.26',
    date: '2026-02-27',
    changes: [
      { type: 'admin', text: 'CI/CD自动部署配置' },
      { type: 'admin', text: 'manifest改回使用ACR镜像' },
    ],
  },
  {
    version: '0.1.25',
    date: '2026-02-26',
    changes: [
      { type: 'admin', text: 'Web端大模型在线配置管理' },
      { type: 'admin', text: 'Token自动刷新机制' },
    ],
  },
  {
    version: '0.1.24',
    date: '2026-02-25',
    changes: [
      { type: 'user', text: 'Jarvis类别不匹配时自动清空错配定额（减少误匹配）' },
      { type: 'admin', text: 'output_writer列自适应' },
    ],
  },
];
