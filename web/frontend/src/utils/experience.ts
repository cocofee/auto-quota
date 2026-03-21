/**
 * Shared confidence / light-status helpers.
 */

export const GREEN_THRESHOLD = 90;
export const YELLOW_THRESHOLD = 75;

export type LightStatus = 'green' | 'yellow' | 'red';

type LightLike = {
  confidence?: number | null;
  confidence_score?: number | null;
  light_status?: string | null;
};

export const COLORS = {
  greenBg: '#E8F5E9',
  greenCell: '#C8E6C9',
  greenText: '#2e7d32',
  greenSolid: '#52c41a',
  yellowBg: '#FFF8E1',
  yellowCell: '#FFE082',
  yellowText: '#e65100',
  yellowSolid: '#faad14',
  redBg: '#FFEBEE',
  redCell: '#EF9A9A',
  redText: '#c62828',
  redSolid: '#ff4d4f',
};

export function getEffectiveConfidence(item: LightLike | number): number {
  if (typeof item === 'number') return item;
  const value = item.confidence_score ?? item.confidence ?? 0;
  const score = Number(value);
  if (!Number.isFinite(score)) return 0;
  return Math.max(0, Math.min(100, Math.round(score)));
}

export function resolveLightStatus(item: LightLike | number): LightStatus {
  if (typeof item !== 'number') {
    const light = String(item.light_status || '').toLowerCase();
    if (light === 'green' || light === 'yellow' || light === 'red') {
      return light;
    }
  }
  const confidence = getEffectiveConfidence(item);
  if (confidence >= GREEN_THRESHOLD) return 'green';
  if (confidence >= YELLOW_THRESHOLD) return 'yellow';
  return 'red';
}

function colorByLight<T>(light: LightStatus, colors: { green: T; yellow: T; red: T }): T {
  if (light === 'green') return colors.green;
  if (light === 'yellow') return colors.yellow;
  return colors.red;
}

export function getBillRowBgColor(item: LightLike | number): string {
  return colorByLight(resolveLightStatus(item), {
    green: COLORS.greenBg,
    yellow: COLORS.yellowBg,
    red: COLORS.redBg,
  });
}

export function getConfidenceCellBgColor(item: LightLike | number, hasQuotas = true): string {
  if (!hasQuotas) return 'transparent';
  return colorByLight(resolveLightStatus(item), {
    green: COLORS.greenCell,
    yellow: COLORS.yellowCell,
    red: COLORS.redCell,
  });
}

export function getConfidenceTextColor(item: LightLike | number): string {
  return colorByLight(resolveLightStatus(item), {
    green: COLORS.greenText,
    yellow: COLORS.yellowText,
    red: COLORS.redText,
  });
}

export function getConfidenceSolidColor(item: LightLike | number): string {
  return colorByLight(resolveLightStatus(item), {
    green: COLORS.greenSolid,
    yellow: COLORS.yellowSolid,
    red: COLORS.redSolid,
  });
}

export function confidenceToTagColor(item: LightLike | number): string {
  return colorByLight(resolveLightStatus(item), {
    green: 'success',
    yellow: 'warning',
    red: 'error',
  });
}

export function confidenceToLabel(item: LightLike | number): string {
  const confidence = getEffectiveConfidence(item);
  const light = resolveLightStatus(item);
  if (light === 'green') return `高(${confidence}%)`;
  if (light === 'yellow') return `中(${confidence}%)`;
  return `低(${confidence}%)`;
}

export function confidenceToStars(item: LightLike | number, hasQuotas = true): string {
  if (!hasQuotas) return '—';
  const confidence = getEffectiveConfidence(item);
  const light = resolveLightStatus(item);
  if (light === 'green') return `★★★推荐(${confidence}%)`;
  if (light === 'yellow') return `★★参考(${confidence}%)`;
  return `★待审(${confidence}%)`;
}

const SPECIALTY_NAMES: Record<string, string> = {
  C1: '机械设备安装',
  C2: '热力设备安装',
  C3: '静置设备安装',
  C4: '电气设备安装',
  C5: '智能化系统',
  C6: '自动化仪表',
  C7: '通风空调',
  C8: '工业管道',
  C9: '消防工程',
  C10: '给排水',
  C11: '通信设备',
  C12: '刷油防腐保温',
  C13: '其他及附属工程',
  A: '土建工程',
  B: '装饰装修工程',
  D: '市政工程',
  E: '园林绿化工程',
  G: '轨道交通',
  NT4: '电气(NT4)',
  NT9: '消防(NT9)',
};

export function specialtyLabel(code: string | undefined | null): string {
  if (!code) return '未分类';
  if (SPECIALTY_NAMES[code]) return SPECIALTY_NAMES[code];
  if (/^\d+$/.test(code)) {
    const withC = `C${code}`;
    if (SPECIALTY_NAMES[withC]) return SPECIALTY_NAMES[withC];
  }
  return `未分类(${code})`;
}

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

export function parseBillFeatures(text: string | undefined | null): string[] {
  if (!text) return [];

  let lines = text.split(/[\r\n]+/).map((s) => s.trim()).filter(Boolean);
  if (lines.length <= 1 && /\d+[.、）)]/.test(text)) {
    lines = text.split(/(?=\d+[.、）)])/).map((s) => s.trim()).filter(Boolean);
  }

  return lines.filter((line) => {
    const clean = line.replace(/^\d+[.、）)]\s*/, '');
    if (!clean.trim()) return false;
    if (/详见图纸|详见设计|按图施工|按规范/.test(clean)) return false;
    if (/^其他[：:]\s*(详见|见\/|无|-)\s*$/.test(clean)) return false;
    return true;
  });
}

export function splitBillTextAndFeatures(
  billText: string,
  billName?: string,
): { name: string; features: string[] } {
  if (billName && billName.trim()) {
    const remaining = billText.replace(billName, '').trim();
    return {
      name: billName.trim(),
      features: parseBillFeatures(remaining),
    };
  }

  const lines = billText.split(/[\r\n]+/).map((s) => s.trim()).filter(Boolean);
  if (lines.length === 0) return { name: '', features: [] };

  const name = lines[0];
  const rest = lines.slice(1).join('\n');
  return {
    name,
    features: parseBillFeatures(rest),
  };
}
