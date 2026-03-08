/**
 * 分析页面共享工具函数
 */

import { Tooltip } from 'antd';
import { ArrowUpOutlined, ArrowDownOutlined, MinusOutlined } from '@ant-design/icons';
import { createElement } from 'react';
import { COLORS } from '../../../utils/experience';

/* ========== 类型定义 ========== */

export interface OverviewData {
  total_tasks: number;
  completed_tasks: number;
  total_results: number;
  high_confidence: number;
  mid_confidence: number;
  low_confidence: number;
  avg_confidence: number;
  confirmed_results: number;
  total_users: number;
}

export interface ProvinceItem {
  province: string;
  task_count: number;
  match_count: number;
  avg_confidence: number;
}

export interface SpecialtyItem {
  specialty: string;
  count: number;
  avg_confidence: number;
}

export interface TrendItem {
  date: string;
  task_count: number;
}

export interface DatasetMetrics {
  total: number;
  skip_measure?: number;
  green_rate: number;
  yellow_rate: number;
  red_rate: number;
  exp_hit_rate: number;
  fallback_rate: number;
  avg_time_sec: number;
}

export interface BenchmarkRecord {
  version: string;
  date: string;
  mode: string;
  note?: string;
  datasets: Record<string, DatasetMetrics>;
}

/* ========== 工具函数 ========== */

/** 格式化比率为百分比字符串（0.95 → "95.0%"） */
export function fmtRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

/** 趋势箭头：对比前一次跑分的某个指标，显示涨跌 */
export function TrendArrow({ current, previous, higherIsBetter }: {
  current: number;
  previous: number | undefined;
  higherIsBetter: boolean;
}) {
  if (previous === undefined) {
    return null;
  }
  const diff = current - previous;
  if (Math.abs(diff) < 0.001) {
    return createElement(MinusOutlined, { style: { color: '#999', fontSize: 10, marginLeft: 4 } });
  }
  const isGood = higherIsBetter ? diff > 0 : diff < 0;
  const diffPp = `${diff > 0 ? '+' : ''}${(diff * 100).toFixed(1)}pp`;
  return createElement(Tooltip, { title: diffPp },
    isGood
      ? createElement(ArrowUpOutlined, { style: { color: COLORS.greenSolid, fontSize: 10, marginLeft: 4 } })
      : createElement(ArrowDownOutlined, { style: { color: COLORS.redSolid, fontSize: 10, marginLeft: 4 } })
  );
}

/** 省份名缩短：从定额库全名提取"省份·专业年份"格式
 *  "江苏省安装工程计价定额(2014)" → "江苏·安装2014"
 *  "北京市建设工程计价依据(2024)" → "北京2024"
 */
export function shortenProvince(name: string): string {
  if (!name) return '未知';
  // 提取年份
  const yearMatch = name.match(/(\d{4})/);
  const year = yearMatch ? yearMatch[1] : '';
  // 提取省份简称（贪婪匹配2-4个中文字，靠后面的"省/市/自治区"边界截断）
  // 注意：必须用贪婪{2,4}，懒惰{2,4}?会把"黑龙江"截成"黑龙"
  const provinceMatch = name.match(/^([\u4e00-\u9fa5]{2,4})(省|市|自治区|特别行政区)/)
    || name.match(/^([\u4e00-\u9fa5]{2,3})/);
  const province = provinceMatch ? provinceMatch[1] : name.slice(0, 4);
  // 提取专业关键词
  let specialty = '';
  if (name.includes('安装')) specialty = '安装';
  else if (name.includes('市政')) specialty = '市政';
  else if (name.includes('建筑') || name.includes('房屋')) specialty = '建筑';
  else if (name.includes('装饰')) specialty = '装饰';
  else if (name.includes('园林')) specialty = '园林';
  // 拼接
  if (specialty) return `${province}·${specialty}${year}`;
  return `${province}${year}`;
}
