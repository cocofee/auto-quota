import type { TaskStatus } from '../types';

export const STATUS_MAP: Record<TaskStatus, { color: string; text: string }> = {
  pending: { color: 'default', text: '等待中' },
  running: { color: 'processing', text: '匹配中' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
  cancelled: { color: 'warning', text: '已取消' },
};

export const STATUS_OPTIONS = [
  { label: '全部状态', value: '' },
  { label: '等待中', value: 'pending' },
  { label: '匹配中', value: 'running' },
  { label: '已完成', value: 'completed' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
];
