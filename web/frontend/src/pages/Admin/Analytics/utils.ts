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

/** 省份名缩短：去掉"省建设工程""市建设工程"等后缀，保留核心名+年份 */
export function shortenProvince(name: string): string {
  if (!name) return '未知';
  return name
    .replace(/省建设工程.*?(?=\d{4}|$)/, '')
    .replace(/市建设工程.*?(?=\d{4}|$)/, '')
    .replace(/(维吾尔|壮族|回族)自治区建设工程.*?(?=\d{4}|$)/, '')
    .replace(/自治区建设工程.*?(?=\d{4}|$)/, '')
    .trim();
}
