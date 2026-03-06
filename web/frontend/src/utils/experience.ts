/**
 * 经验库通用工具函数
 *
 * 从 Results/index.tsx 提取可复用的逻辑，供经验库管理页面等共享使用。
 * 包括：置信度颜色/标签、清单特征描述解析。
 */

// ============================================================
// 置信度阈值常量（全局统一，所有页面共用）
// ============================================================

export const GREEN_THRESHOLD = 85;   // 绿灯：系统有把握
export const YELLOW_THRESHOLD = 70;  // 黄灯：需要人工看一眼

// ============================================================
// 统一颜色常量（全系统唯一来源，禁止在页面中硬编码）
// ============================================================

export const COLORS = {
  // 绿灯（高置信度）
  greenBg:    '#E8F5E9',  // 行背景
  greenCell:  '#C8E6C9',  // 单元格背景（比行背景深）
  greenText:  '#2e7d32',  // 文字
  greenSolid: '#52c41a',  // 实色（进度条、数字）
  // 黄灯（中置信度）
  yellowBg:    '#FFF8E1',
  yellowCell:  '#FFE082',
  yellowText:  '#e65100',
  yellowSolid: '#faad14',
  // 红灯（低置信度）
  redBg:    '#FFEBEE',
  redCell:  '#EF9A9A',
  redText:  '#c62828',
  redSolid: '#ff4d4f',
};

// ============================================================
// 行背景色（按置信度着淡色，用于整行）
// ============================================================

export function getBillRowBgColor(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return COLORS.greenBg;
  if (confidence >= YELLOW_THRESHOLD) return COLORS.yellowBg;
  return COLORS.redBg;
}

// ============================================================
// 单元格背景色（比行背景深一档，用于推荐度列等需要强调的单元格）
// ============================================================

export function getConfidenceCellBgColor(confidence: number, hasQuotas = true): string {
  if (!hasQuotas) return 'transparent';
  if (confidence >= GREEN_THRESHOLD) return COLORS.greenCell;
  if (confidence >= YELLOW_THRESHOLD) return COLORS.yellowCell;
  return COLORS.redCell;
}

// ============================================================
// 文字颜色（深色，用于置信度数字/标签的文字）
// ============================================================

export function getConfidenceTextColor(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return COLORS.greenText;
  if (confidence >= YELLOW_THRESHOLD) return COLORS.yellowText;
  return COLORS.redText;
}

// ============================================================
// 实色值（用于进度条、比例条、彩色数字等）
// ============================================================

export function getConfidenceSolidColor(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return COLORS.greenSolid;
  if (confidence >= YELLOW_THRESHOLD) return COLORS.yellowSolid;
  return COLORS.redSolid;
}

// ============================================================
// Tag 颜色（antd Tag 的 color 属性值）
// ============================================================

export function confidenceToTagColor(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return 'success';
  if (confidence >= YELLOW_THRESHOLD) return 'warning';
  return 'error';
}

// ============================================================
// 置信度文字标签
// ============================================================

export function confidenceToLabel(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return `高(${confidence}%)`;
  if (confidence >= YELLOW_THRESHOLD) return `中(${confidence}%)`;
  return `低(${confidence}%)`;
}

// ============================================================
// 置信度星标（用于匹配结果页）
// ============================================================

export function confidenceToStars(confidence: number, hasQuotas = true): string {
  if (!hasQuotas) return '—';
  if (confidence >= GREEN_THRESHOLD) return `★★★推荐(${confidence}%)`;
  if (confidence >= YELLOW_THRESHOLD) return `★★参考(${confidence}%)`;
  return `★待审(${confidence}%)`;
}

// ============================================================
// 专业册号 → 中文名（和后端 bill_cleaner.py 保持一致）
// ============================================================

const SPECIALTY_NAMES: Record<string, string> = {
  // 安装（C册）
  C1: '机械设备安装', C2: '热力设备安装', C3: '静置设备安装',
  C4: '电气设备安装', C5: '智能化系统', C6: '自动化仪表',
  C7: '通风空调', C8: '工业管道', C9: '消防工程',
  C10: '给排水', C11: '通信设备', C12: '刷油防腐保温',
  C13: '其他及附属工程',
  // 土建、装饰、市政、园林
  A: '土建工程', B: '装饰装修工程', D: '市政工程', E: '园林绿化工程',
  // 其他可能出现的大类前缀
  G: '轨道交通',
};

/** 专业册号转中文名，如 "C4" → "电气设备安装" */
export function specialtyLabel(code: string | undefined | null): string {
  if (!code) return '未分类';
  // 直接命中
  if (SPECIALTY_NAMES[code]) return SPECIALTY_NAMES[code];
  // 纯数字（如 "10"、"13"）→ 自动加 C 前缀重试（后端部分省份编号不带C前缀）
  if (/^\d+$/.test(code)) {
    const withC = `C${code}`;
    if (SPECIALTY_NAMES[withC]) return SPECIALTY_NAMES[withC];
  }
  return '未分类';
}

// ============================================================
// 来源显示名称映射
// ============================================================

const SOURCE_LABELS: Record<string, string> = {
  auto_match: '自动匹配',
  jarvis_correction: 'Jarvis纠正',
  project_import: '项目导入',
  project_import_suspect: '项目导入(待审)',
  user_confirmed: '用户确认',
  user_correction: '用户修正',
  manual: '手动录入',
  batch_import: '批量导入',
  promote_from_can: '候选晋升',
  promote_from_candidate: '候选晋升',
  auto_review: '自动审核',
  xml_import: 'XML导入',
  feedback_learn: '反馈学习',
};

export function sourceToLabel(source: string): string {
  return SOURCE_LABELS[source] || source;
}

// ============================================================
// 清单特征描述解析
// ============================================================

/**
 * 从 bill_text（项目特征描述原文）中解析出结构化行
 *
 * 做了什么：
 * 1. 按换行拆分；如果没换行但有编号前缀（1. 2. 3.），按编号拆
 * 2. 过滤废话行（"详见图纸"、"按规范"等无实际信息的行）
 *
 * 返回过滤后的字符串数组，每条是一行特征描述
 */
export function parseBillFeatures(text: string | undefined | null): string[] {
  if (!text) return [];

  // 按换行拆分
  let lines = text.split(/[\r\n]+/).map(s => s.trim()).filter(Boolean);

  // 如果原文没换行但有多个编号（如"1.名称:xx 2.规格:yy"），按编号拆
  if (lines.length <= 1 && /\d+[.、．]/.test(text)) {
    lines = text.split(/(?=\d+[.、．])/).map(s => s.trim()).filter(Boolean);
  }

  // 过滤废话行
  const filtered = lines.filter(line => {
    const clean = line.replace(/^\d+[.、．]\s*/, '');
    if (!clean.trim()) return false;
    if (/详见图纸|详见设计|按图施工|按规范/.test(clean)) return false;
    if (/^其他[：:]\s*(详见|见|按|\/|无|—|-)\s*$/.test(clean)) return false;
    return true;
  });

  return filtered;
}

// ============================================================
// 从 bill_text 中分离清单名称和项目特征
// ============================================================

/**
 * 经验库的 bill_text 通常是"清单名称 + 项目特征"拼在一起的长文本。
 * 这个函数尝试分离出名称部分和特征部分。
 *
 * 逻辑：
 * - 如果有 bill_name 字段且非空，直接用它做名称，剩余部分做特征
 * - 否则取第一行（或第一个换行前的内容）做名称
 */
export function splitBillTextAndFeatures(
  billText: string,
  billName?: string,
): { name: string; features: string[] } {
  // 如果有独立的 bill_name，直接用它
  if (billName && billName.trim()) {
    // 从 bill_text 中去掉 bill_name 部分，剩余的当做特征
    const remaining = billText.replace(billName, '').trim();
    return {
      name: billName.trim(),
      features: parseBillFeatures(remaining),
    };
  }

  // 没有 bill_name，尝试拆分
  const lines = billText.split(/[\r\n]+/).map(s => s.trim()).filter(Boolean);
  if (lines.length === 0) return { name: '', features: [] };

  // 第一行当名称，后续行当特征
  const name = lines[0];
  const rest = lines.slice(1).join('\n');
  return {
    name,
    features: parseBillFeatures(rest),
  };
}
