/**
 * 任务相关共享常量
 *
 * STATUS_MAP、STATUS_OPTIONS 等在多个页面中重复定义的常量，
 * 统一放在这里，避免改一处漏一处。
 */

import type { TaskStatus } from '../types';

/** 任务状态对应的 Tag 颜色和中文文本 */
export const STATUS_MAP: Record<TaskStatus, { color: string; text: string }> = {
  pending: { color: 'default', text: '等待中' },
  running: { color: 'processing', text: '匹配中' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
  cancelled: { color: 'warning', text: '已取消' },
};

/** 任务状态下拉选项（带"全部"） */
export const STATUS_OPTIONS = [
  { label: '全部状态', value: '' },
  { label: '等待中', value: 'pending' },
  { label: '匹配中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
];
