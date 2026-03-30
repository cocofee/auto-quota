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

export const APP_VERSION = '0.2.117';

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
    version: '0.2.117',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'v0.2.117 更新' },
    ],
  },
  {
    version: '0.2.116',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'v0.2.116 更新' },
    ],
  },
  {
    version: '0.2.115',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'v0.2.115 更新' },
    ],
  },
  {
    version: '0.2.114',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'v0.2.114 更新' },
    ],
  },
  {
    version: '0.2.113',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'v0.2.113 更新' },
    ],
  },
  {
    version: '0.2.112',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'Encode remote match form params safely' },
    ],
  },
  {
    version: '0.2.111',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'Bypass env proxies for local match calls' },
    ],
  },
  {
    version: '0.2.110',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'v0.2.110 更新' },
    ],
  },
  {
    version: '0.2.109',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'Tighten primary subject query guards' },
      { type: 'admin', text: 'Close wrong_book scope leakage path' },
      { type: 'admin', text: 'Align real eval context and soften title routing' },
      { type: 'admin', text: 'Tighten install routing and guard CGR overrides' },
      { type: 'admin', text: '收口定额检索里的北京2024旧简称' },
    ],
  },
  {
    version: '0.2.108',
    date: '2026-03-30',
    changes: [
      { type: 'admin', text: 'Tighten primary subject query guards' },
      { type: 'admin', text: 'Close wrong_book scope leakage path' },
      { type: 'admin', text: 'Align real eval context and soften title routing' },
      { type: 'admin', text: 'Tighten install routing and guard CGR overrides' },
      { type: 'admin', text: '收口定额检索里的北京2024旧简称' },
    ],
  },
  {
    version: '0.2.107',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: 'enable ltr v2 baseline and rule-candidate rerank' },
    ],
  },
  {
    version: '0.2.106',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '收口管理员前端工作台' },
    ],
  },
  {
    version: '0.2.105',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: 'v0.2.105 更新' },
    ],
  },
  {
    version: '0.2.104',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: 'v0.2.104 更新' },
    ],
  },
  {
    version: '0.2.103',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '收口 OpenClaw 复核页数据语义' },
    ],
  },
  {
    version: '0.2.102',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '收口 OpenClaw 内网复核链路' },
      { type: 'admin', text: '补齐知识晋升与 OpenClaw 接入链路' },
      { type: 'admin', text: '前端统一知识入口并补治理中心主入口卡' },
    ],
  },
  {
    version: '0.2.101',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: 'v0.2.101 更新' },
    ],
  },
  {
    version: '0.2.100',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '治理中心首屏补成三张主入口卡，突出候选知识晋升、OpenClaw 复核和正式经验库。' },
    ],
  },
  {
    version: '0.2.99',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '统一前端知识入口术语，明确区分正式经验库与候选知识晋升页面。' },
    ],
  },
  {
    version: '0.2.98',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '修复独立知识卡片晋升与候选详情展示' },
    ],
  },
  {
    version: '0.2.97',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: 'v0.2.97 更新' },
    ],
  },
  {
    version: '0.2.96',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: 'v0.2.96 更新' },
    ],
  },
  {
    version: '0.2.95',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '写死 OpenClaw 固定接入密钥' },
    ],
  },
  {
    version: '0.2.94',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '补充 OpenClaw 接入与审核回流说明' },
    ],
  },
  {
    version: '0.2.93',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '优化知识候选确认入口与工作台文案' },
    ],
  },
  {
    version: '0.2.92',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: 'v0.2.92 更新' },
    ],
  },
  {
    version: '0.2.91',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: 'v0.2.91 更新' },
    ],
  },
  {
    version: '0.2.90',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: 'v0.2.90 更新' },
    ],
  },
  {
    version: '0.2.89',
    date: '2026-03-25',
    changes: [
      { type: 'admin', text: '修复知识晋升 staging 初始化锁冲突' },
      { type: 'admin', text: '优化知识晋升工作台首屏与中文语义' },
    ],
  },
  {
    version: '0.2.88',
    date: '2026-03-24',
    changes: [
      { type: 'admin', text: 'v0.2.88 更新' },
    ],
  },
  {
    version: '0.2.87',
    date: '2026-03-24',
    changes: [
      { type: 'admin', text: '修复懒猫部署缺少 knowledge staging 依赖' },
    ],
  },
  {
    version: '0.2.86',
    date: '2026-03-24',
    changes: [
      { type: 'admin', text: 'v0.2.86 更新' },
    ],
  },
  {
    version: '0.2.85',
    date: '2026-03-24',
    changes: [
      { type: 'admin', text: 'v0.2.85 更新' },
    ],
  },
  {
    version: '0.2.84',
    date: '2026-03-24',
    changes: [
      { type: 'admin', text: '基准集重排权重与路由保护规则重平衡' },
    ],
  },
  {
    version: '0.2.83',
    date: '2026-03-23',
    changes: [
      { type: 'admin', text: '重平衡 hybrid_score、param_score 与 family gate 的基准集重排权重' },
      { type: 'admin', text: '为显式类别候选覆盖增加 hybrid-score 安全保护' },
      { type: 'admin', text: '收紧主册路由与置信度阈值，提升基准集恢复能力' },
      { type: 'admin', text: '优先保证 Top1 正确，并拆分红黄绿灯状态' },
      { type: 'admin', text: '跳过剩余措施费项目' },
      { type: 'admin', text: '收紧支撑类兜底路由' },
      { type: 'admin', text: '收紧支撑类主题路由' },
      { type: 'admin', text: '修复安装专业路由与仲裁框架' },
      { type: 'admin', text: '统一 canonical 查询上下文流转' },
      { type: 'admin', text: '细化安装电缆与支撑类路由' },
    ],
  },
  {
    version: '0.2.82',
    date: '2026-03-22',
    changes: [
      { type: 'admin', text: '优先保证 Top1 正确，并拆分红黄绿灯状态' },
      { type: 'admin', text: '跳过剩余措施费项目' },
      { type: 'admin', text: '收紧支撑类兜底路由' },
      { type: 'admin', text: '收紧支撑类主题路由' },
      { type: 'admin', text: '修复安装专业路由与仲裁框架' },
      { type: 'admin', text: '统一 canonical 查询上下文流转' },
      { type: 'admin', text: '细化安装电缆与支撑类路由' },
    ],
  },
  {
    version: '0.2.80',
    date: '2026-03-20',
    changes: [
      { type: 'admin', text: 'v0.2.80 更新' },
    ],
  },
  {
    version: '0.2.79',
    date: '2026-03-20',
    changes: [
      { type: 'admin', text: 'v0.2.79 更新' },
    ],
  },
  {
    version: '0.2.78',
    date: '2026-03-20',
    changes: [
      { type: 'admin', text: 'v0.2.78 更新' },
    ],
  },
  {
    version: '0.2.77',
    date: '2026-03-20',
    changes: [
      { type: 'admin', text: '新增带原因解释的诊断报告' },
      { type: 'admin', text: '新增安装专业规则锚点' },
      { type: 'admin', text: '根据型号推断电缆芯材' },
      { type: 'admin', text: '拆分电缆敷设与电缆头匹配逻辑' },
    ],
  },
  {
    version: '0.2.76',
    date: '2026-03-20',
    changes: [
      { type: 'admin', text: '新增带原因解释的诊断报告' },
      { type: 'admin', text: '新增安装专业规则锚点' },
      { type: 'admin', text: '根据型号推断电缆芯材' },
      { type: 'admin', text: '拆分电缆敷设与电缆头匹配逻辑' },
    ],
  },
  {
    version: '0.2.75',
    date: '2026-03-20',
    changes: [
      { type: 'admin', text: '新增带原因解释的诊断报告' },
      { type: 'admin', text: '新增安装专业规则锚点' },
      { type: 'admin', text: '根据型号推断电缆芯材' },
      { type: 'admin', text: '拆分电缆敷设与电缆头匹配逻辑' },
    ],
  },
  {
    version: '0.2.74',
    date: '2026-03-20',
    changes: [
      { type: 'admin', text: '新增带原因解释的诊断报告' },
      { type: 'admin', text: '新增安装专业规则锚点' },
      { type: 'admin', text: '根据型号推断电缆芯材' },
      { type: 'admin', text: '拆分电缆敷设与电缆头匹配逻辑' },
    ],
  },
  {
    version: '0.2.73',
    date: '2026-03-19',
    changes: [
      { type: 'admin', text: '记录当前工作区检查点状态' },
      { type: 'admin', text: '记录 family 对齐架构检查点' },
      { type: 'admin', text: '新增结构化逻辑桶评分' },
      { type: 'admin', text: '新增候选特征对齐校验' },
      { type: 'admin', text: '新增特征对齐版安装匹配流水线' },
      { type: 'admin', text: '修复 Jarvis 定额排序与跨省纠正溯源' },
      { type: 'admin', text: '新增清单特征构建流程' },
    ],
  },
  {
    version: '0.2.72',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: '按省份和专业拆分经验过滤逻辑' },
    ],
  },
  {
    version: '0.2.71',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: '恢复主材展示与导出' },
    ],
  },
  {
    version: '0.2.70',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: 'v0.2.70 更新' },
    ],
  },
  {
    version: '0.2.69',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: '修复导入经验统计' },
    ],
  },
  {
    version: '0.2.68',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: 'v0.2.68 更新' },
    ],
  },
  {
    version: '0.2.67',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: 'v0.2.67 更新' },
    ],
  },
  {
    version: '0.2.66',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: 'v0.2.66 更新' },
    ],
  },
  {
    version: '0.2.65',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: 'v0.2.65 更新' },
    ],
  },
  {
    version: '0.2.64',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: 'v0.2.64 更新' },
    ],
  },
  {
    version: '0.2.63',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: '移除 agent 批量审核流程' },
      { type: 'admin', text: '修复编清单安全性与远程可达性' },
    ],
  },
  {
    version: '0.2.62',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: 'v0.2.62 更新' },
    ],
  },
  {
    version: '0.2.61',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: '前端改版——经验库Table化+管理中心精简+项目特征原样显示+绿灯率修正' },
    ],
  },
  {
    version: '0.2.60',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: 'v0.2.60 更新' },
    ],
  },
  {
    version: '0.2.59',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: '删除LogViewer未使用的List/Text导入，修复TS编译错误' },
      { type: 'admin', text: '修复广材网查价500错误——删除废弃的错误import' },
    ],
  },
  {
    version: '0.2.58',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: '修复广材网查价500错误——删除废弃的错误import' },
    ],
  },
  {
    version: '0.2.57',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: '前端细节优化——来源标签中文化+本月完成数据修正+确认率标签+星级颜色+日志入口' },
    ],
  },
  {
    version: '0.2.56',
    date: '2026-03-18',
    changes: [
      { type: 'admin', text: '智能查价——名称清洗+同义词+广材网品名映射，大幅提升查价命中率' },
      { type: 'admin', text: '智能填主材——级联查价+广材网实时爬取+防封策略' },
      { type: 'admin', text: 'v0.3.0 Phase3完——红灯Top3候选一键纠正+固定底部操作栏' },
      { type: 'admin', text: 'v0.3.0 Phase3——按颜色全选+任务列表类型标签' },
      { type: 'admin', text: 'v0.3.0 Phase2——套定额页省份自动识别+定额类型推荐' },
      { type: 'admin', text: 'v0.3.0 Phase1——侧边栏重构+首页四功能卡片+统计卡片改版' },
      { type: 'admin', text: 'book字段修复限定"册"编号+非标book直接映射' },
      { type: 'admin', text: '智能填主材——紧凑布局+信息价/市场价选择+项目特征+导出原名' },
    ],
  },
  {
    version: '0.2.55',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: '智能填主材层级预览——分部标题→清单→定额→主材行' },
    ],
  },
  {
    version: '0.2.54',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: '删除未使用的taskSourceName变量，修复TS编译' },
      { type: 'admin', text: '智能填主材——写回原Excel主材行单价列' },
    ],
  },
  {
    version: '0.2.53',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: '智能填主材——写回原Excel主材行单价列' },
    ],
  },
  {
    version: '0.2.52',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: '上海定额book字段修复——从"03"统一改为正确册号' },
      { type: 'admin', text: '智能填主材支持两种输入——上传文件/从任务拉取' },
    ],
  },
  {
    version: '0.2.51',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: '智能填主材支持远程模式——懒猫转发查价请求到本地电脑' },
      { type: 'admin', text: '新增湖南+青海+深圳信息价导入工具' },
      { type: 'admin', text: 'rule_validator支持compound双参数家族的DN取档' },
    ],
  },
  {
    version: '0.2.50',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: '清理MaterialPrice.tsx未使用的导入，修复TS编译报错' },
      { type: 'admin', text: '智能填主材支持广联达导出的纯材料表' },
      { type: 'admin', text: '新增"智能填主材"功能——选地区自动查价+手填+众包收集' },
      { type: 'admin', text: '新增同义词"钢柱散热器→柱式散热器安装"' },
      { type: 'admin', text: '定额行/主材行C:D列合并遗漏——措施费编号+主材标记识别' },
      { type: 'admin', text: '新增同义词"小厨宝→电热水器"' },
      { type: 'admin', text: 'Codex审核修复——洗手盆→洗脸盆+地暖/暖气PPR走采暖' },
      { type: 'admin', text: 'P0同义词补充——卫生器具+衬塑PP-R钢管映射' },
      { type: 'admin', text: 'OpenClaw skill v5.1——红灯AI判断纠正+跳过xls文件' },
      { type: 'admin', text: 'PPR材质提取修复+附属计量条目跳过+热水归给水方向' },
      { type: 'admin', text: '措施费关键词补充——施工脚手架+综合脚手架' },
    ],
  },
  {
    version: '0.2.49',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: '智能填主材支持广联达导出的纯材料表' },
      { type: 'admin', text: '新增"智能填主材"功能——选地区自动查价+手填+众包收集' },
      { type: 'admin', text: '新增同义词"钢柱散热器→柱式散热器安装"' },
      { type: 'admin', text: '定额行/主材行C:D列合并遗漏——措施费编号+主材标记识别' },
      { type: 'admin', text: '新增同义词"小厨宝→电热水器"' },
      { type: 'admin', text: 'Codex审核修复——洗手盆→洗脸盆+地暖/暖气PPR走采暖' },
      { type: 'admin', text: 'P0同义词补充——卫生器具+衬塑PP-R钢管映射' },
      { type: 'admin', text: 'OpenClaw skill v5.1——红灯AI判断纠正+跳过xls文件' },
      { type: 'admin', text: 'PPR材质提取修复+附属计量条目跳过+热水归给水方向' },
      { type: 'admin', text: '措施费关键词补充——施工脚手架+综合脚手架' },
    ],
  },
  {
    version: '0.2.48',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: 'v0.2.48 更新' },
    ],
  },
  {
    version: '0.2.47',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: 'v0.2.47 更新' },
    ],
  },
  {
    version: '0.2.46',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: 'smart接口远程超时加大+任务防重复提交' },
      { type: 'admin', text: 'OpenClaw skill v5.0——分工明确+防偷懒铁律' },
      { type: 'admin', text: '装修材料编号清理+成品门同义词修正+金属饰线同义词' },
      { type: 'admin', text: '装修材料代号过滤+装饰同义词补充' },
      { type: 'admin', text: 'OpenClaw搜索API稳定性修复——补logger导入+超时加大+自动重试' },
      { type: 'admin', text: '重庆信息价PDF导入工具——造价期刊pdfplumber提取+侧边栏水印清洗' },
    ],
  },
  {
    version: '0.2.45',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: '天津+北京信息价PDF导入工具——京津冀共享平台RAR包解析' },
      { type: 'admin', text: 'book推断只在C前缀时介入，避免覆盖match_core已翻译的book' },
      { type: 'admin', text: '新疆信息价Excel导入工具——xjzj.com API批量下载+xlrd解析' },
      { type: 'admin', text: '措施费识别器扩充+消防模块输入/输出/输入输出细分' },
      { type: 'admin', text: '补充控制柜同义词（风机控制柜/电气控制柜→成套配电柜安装）' },
      { type: 'admin', text: '非标准book省份搜索全面修复——classify_to_books词频推断替代C-prefix硬翻译' },
      { type: 'admin', text: '档位纠偏器扫描范围10→20 + 风管周长参数提取修复' },
      { type: 'admin', text: '拆除类清单搜索词去掉"安装"，避免BM25偏向安装定额' },
      { type: 'admin', text: '补充8个风机类同义词（排烟/送风/补风/排风/防爆/轴流/离心/柜式离心→通风机）' },
      { type: 'admin', text: '手动调节阀路由+补充同义词（碳钢风管/挡烟垂壁/马桶/散热器）' },
      { type: 'admin', text: '吉林信息价Word导入工具——支持docx+COM转换doc/wps/rtf' },
      { type: 'admin', text: '移除旧版batch_runner（功能已被data-agent和批量匹配API替代）' },
      { type: 'admin', text: 'build_quota_query透传section_title参数到query_builder' },
      { type: 'admin', text: '贵州信息价PDF导入工具——pdfplumber表格提取+全角转半角+9城市自动识别' },
      { type: 'admin', text: '电力技改审核修复——22条同义词+拆除互斥+介质冲突检查' },
      { type: 'admin', text: '新增南宁信息价导入工具——从电子书JS文本提取材料价格' },
      { type: 'admin', text: 'M1档位纠偏器——LTR排序后强制选同家族内参数最匹配的档位' },
      { type: 'admin', text: '新增武汉信息价OCR导入工具——扫描件PDF用RapidOCR识别入库' },
    ],
  },
  {
    version: '0.2.44',
    date: '2026-03-17',
    changes: [
      { type: 'admin', text: '天津+北京信息价PDF导入工具——京津冀共享平台RAR包解析' },
      { type: 'admin', text: 'book推断只在C前缀时介入，避免覆盖match_core已翻译的book' },
      { type: 'admin', text: '新疆信息价Excel导入工具——xjzj.com API批量下载+xlrd解析' },
      { type: 'admin', text: '措施费识别器扩充+消防模块输入/输出/输入输出细分' },
      { type: 'admin', text: '补充控制柜同义词（风机控制柜/电气控制柜→成套配电柜安装）' },
      { type: 'admin', text: '非标准book省份搜索全面修复——classify_to_books词频推断替代C-prefix硬翻译' },
      { type: 'admin', text: '档位纠偏器扫描范围10→20 + 风管周长参数提取修复' },
      { type: 'admin', text: '拆除类清单搜索词去掉"安装"，避免BM25偏向安装定额' },
      { type: 'admin', text: '补充8个风机类同义词（排烟/送风/补风/排风/防爆/轴流/离心/柜式离心→通风机）' },
      { type: 'admin', text: '手动调节阀路由+补充同义词（碳钢风管/挡烟垂壁/马桶/散热器）' },
      { type: 'admin', text: '吉林信息价Word导入工具——支持docx+COM转换doc/wps/rtf' },
      { type: 'admin', text: '移除旧版batch_runner（功能已被data-agent和批量匹配API替代）' },
      { type: 'admin', text: 'build_quota_query透传section_title参数到query_builder' },
      { type: 'admin', text: '贵州信息价PDF导入工具——pdfplumber表格提取+全角转半角+9城市自动识别' },
      { type: 'admin', text: '电力技改审核修复——22条同义词+拆除互斥+介质冲突检查' },
      { type: 'admin', text: '新增南宁信息价导入工具——从电子书JS文本提取材料价格' },
      { type: 'admin', text: 'M1档位纠偏器——LTR排序后强制选同家族内参数最匹配的档位' },
      { type: 'admin', text: '新增武汉信息价OCR导入工具——扫描件PDF用RapidOCR识别入库' },
    ],
  },
  {
    version: '0.2.43',
    date: '2026-03-16',
    changes: [
      { type: 'admin', text: 'v0.2.43 更新' },
    ],
  },
  {
    version: '0.2.42',
    date: '2026-03-16',
    changes: [
      { type: 'admin', text: '导出Excel主材开关——管理员设置页全局控制，默认不带主材' },
      { type: 'admin', text: '修复主材库省份/城市字段缺失——导入链路补传province参数+海南重导' },
      { type: 'admin', text: '专业标题行(电气工程/给排水工程等)强过滤，不再依赖单位/工程量判断' },
      { type: 'admin', text: '新增17条电力技改同义词（配电装置/五防/远动/电容器等）' },
      { type: 'admin', text: '主材名称拼接规格——"电磁阀"→"电磁阀 DN15"，解决不同规格查到同一价格的问题' },
      { type: 'admin', text: 'CLAUDE.md添加压缩优先级 + batch_runner采样逻辑优化' },
      { type: 'admin', text: 'OpenClaw策略调整——禁止自动纠正，只出诊断报告' },
      { type: 'admin', text: 'batch_runner新增--sample均匀采样和--review审核模式' },
      { type: 'admin', text: '新增同义词映射（机械进出场/人孔井/镁合金阳极/事故风机/墙纸等）' },
      { type: 'admin', text: '主材查价自动拆分规格——"不锈钢管 DN100"拆为name+spec分别查询' },
      { type: 'admin', text: '主材行改进——去掉清单旁无用主材列 + 自动从描述提取主材' },
      { type: 'admin', text: '同义词扩展回滚为单次匹配（多次扩展实验失败-0.4%）' },
      { type: 'admin', text: '批量确认置信度<70%硬拦截 + 空清单快速返回 + 超时60秒' },
      { type: 'admin', text: '主材行自动查价——从价格库查单价填入Excel，支持吨→米等单位换算' },
      { type: 'admin', text: '/smart跨库搜索改用关键词触发，不只看结果数量' },
    ],
  },
  {
    version: '0.2.41',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: '经验库争议标记——纠正经验库直通结果时自动标记权威记录' },
    ],
  },
  {
    version: '0.2.40',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: 'OpenClaw增强——备选定额+金额字段+措施项标记' },
      { type: 'admin', text: '同义词+6条（龙虾审核报告缺口：穿墙套管/成品支架/脚手架/凿槽/开孔封堵）' },
      { type: 'admin', text: '配电箱型号过滤——只保留已知产品系列，过滤项目内部编号' },
      { type: 'admin', text: 'OpenClaw SKILL.md API路径规范化（补齐完整URL+请求格式）' },
    ],
  },
  {
    version: '0.2.39',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: 'httpx加入后端依赖 + 经验库写入错误日志增强' },
      { type: 'admin', text: '同义词扩充——80万清单数据挖掘 + benchmark缺口补充' },
    ],
  },
  {
    version: '0.2.38',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: 'useState初始值补充审核字段（修复TS编译错误）' },
      { type: 'admin', text: '准确率统计加入审核状态 + 经验库远程写入' },
    ],
  },
  {
    version: '0.2.37',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: 'useState初始值补充审核字段（修复TS编译错误）' },
      { type: 'admin', text: '准确率统计加入审核状态 + 经验库远程写入' },
    ],
  },
  {
    version: '0.2.36',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: '准确率统计加入审核状态 + 经验库远程写入' },
    ],
  },
  {
    version: '0.2.35',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: 'v0.2.35 更新' },
    ],
  },
  {
    version: '0.2.34',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: 'v0.2.34 更新' },
    ],
  },
  {
    version: '0.2.33',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: '智能搜索API + 纠正后Excel导出' },
      { type: 'admin', text: '编清单接入历史描述库——按用户参数智能匹配项目特征建议' },
    ],
  },
  {
    version: '0.2.32',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: 'v0.2.32 更新' },
    ],
  },
  {
    version: '0.2.31',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: '五步法第2+4步——清单模板/素材库生成 + 同义词挖掘接入Jarvis' },
      { type: 'admin', text: 'LTR模型重训 + 懒猫配置更新 + changelog' },
      { type: 'admin', text: '历史清单数据提取工具（五步法第1步，80万条清单入库）' },
      { type: 'admin', text: 'OpenClaw自动套定额闭环（quota-search API + 审核确认接口 + Skill v2.0）' },
      { type: 'admin', text: '清单扫描器工具（GUI+CLI，快速摸底清单数量/专业/去重）' },
      { type: 'admin', text: '新建任务页UI优化——工具栏整齐对齐+空行过滤+模式提醒精简' },
      { type: 'admin', text: '编清单支持API模式（懒猫转发到本地匹配服务）' },
      { type: 'admin', text: '编清单Web页面（上传Excel→自动匹配12位清单编码→下载工程量清单）' },
      { type: 'admin', text: '新建任务页重构——Sheet选择修复+智能识别+紧凑布局' },
      { type: 'admin', text: '编清单路由二轮优化（邻居投票+描述暗示+分类器兜底+名称清洗），56.1%(+0.1%)' },
      { type: 'admin', text: 'LTR重训+通用知识库质量加权排序，benchmark 38.8%→41.8%(+3.0%)' },
      { type: 'admin', text: '通用知识库排序改质量加权+match_service防None崩溃' },
      { type: 'admin', text: '编清单路由三连优化（消歧+同义词+路由模型），56.0%(+2.1%)' },
      { type: 'admin', text: '数据库bill_unit字段长度不足导致大清单保存失败' },
    ],
  },
  {
    version: '0.2.30',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: 'LTR模型重训 + 懒猫配置更新 + changelog' },
      { type: 'admin', text: '历史清单数据提取工具（五步法第1步，80万条清单入库）' },
      { type: 'admin', text: 'OpenClaw自动套定额闭环（quota-search API + 审核确认接口 + Skill v2.0）' },
      { type: 'admin', text: '清单扫描器工具（GUI+CLI，快速摸底清单数量/专业/去重）' },
      { type: 'admin', text: '新建任务页UI优化——工具栏整齐对齐+空行过滤+模式提醒精简' },
      { type: 'admin', text: '编清单支持API模式（懒猫转发到本地匹配服务）' },
      { type: 'admin', text: '编清单Web页面（上传Excel→自动匹配12位清单编码→下载工程量清单）' },
      { type: 'admin', text: '新建任务页重构——Sheet选择修复+智能识别+紧凑布局' },
      { type: 'admin', text: '编清单路由二轮优化（邻居投票+描述暗示+分类器兜底+名称清洗），56.1%(+0.1%)' },
      { type: 'admin', text: 'LTR重训+通用知识库质量加权排序，benchmark 38.8%→41.8%(+3.0%)' },
      { type: 'admin', text: '通用知识库排序改质量加权+match_service防None崩溃' },
      { type: 'admin', text: '编清单路由三连优化（消歧+同义词+路由模型），56.0%(+2.1%)' },
      { type: 'admin', text: '数据库bill_unit字段长度不足导致大清单保存失败' },
    ],
  },
  {
    version: '0.2.29',
    date: '2026-03-15',
    changes: [
      { type: 'admin', text: '清单扫描器工具（GUI+CLI，快速摸底清单数量/专业/去重）' },
      { type: 'admin', text: '新建任务页UI优化——工具栏整齐对齐+空行过滤+模式提醒精简' },
      { type: 'admin', text: '编清单支持API模式（懒猫转发到本地匹配服务）' },
      { type: 'admin', text: '编清单Web页面（上传Excel→自动匹配12位清单编码→下载工程量清单）' },
      { type: 'admin', text: '新建任务页重构——Sheet选择修复+智能识别+紧凑布局' },
      { type: 'admin', text: '编清单路由二轮优化（邻居投票+描述暗示+分类器兜底+名称清洗），56.1%(+0.1%)' },
      { type: 'admin', text: 'LTR重训+通用知识库质量加权排序，benchmark 38.8%→41.8%(+3.0%)' },
      { type: 'admin', text: '通用知识库排序改质量加权+match_service防None崩溃' },
      { type: 'admin', text: '编清单路由三连优化（消歧+同义词+路由模型），56.0%(+2.1%)' },
      { type: 'admin', text: '数据库bill_unit字段长度不足导致大清单保存失败' },
    ],
  },
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
