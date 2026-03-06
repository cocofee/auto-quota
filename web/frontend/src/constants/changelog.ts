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

export const APP_VERSION = '0.1.97';

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
