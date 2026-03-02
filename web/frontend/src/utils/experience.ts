/**
 * 经验库通用工具函数
 *
 * 从 Results/index.tsx 提取可复用的逻辑，供经验库管理页面等共享使用。
 * 包括：置信度颜色/标签、清单特征描述解析。
 */

// ============================================================
// 置信度阈值常量
// ============================================================

export const GREEN_THRESHOLD = 85;   // 绿灯：系统有把握
export const YELLOW_THRESHOLD = 60;  // 黄灯：参考（经验库用60而非结果页的70）

// ============================================================
// 行背景色（按置信度着淡色，用于整行）
// ============================================================

export function getBillRowBgColor(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return '#E8F5E9';  // 浅绿
  if (confidence >= YELLOW_THRESHOLD) return '#FFF8E1';  // 浅黄
  return '#FFEBEE';                                       // 浅红
}

// ============================================================
// Tag 颜色（antd Tag 的 color 属性值）
// ============================================================

export function confidenceToTagColor(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return 'success';    // 绿
  if (confidence >= YELLOW_THRESHOLD) return 'warning';   // 黄
  return 'error';                                          // 红
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
