import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
  Input,
  Radio,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  EyeOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import type {
  MatchResult,
  OpenClawBatchAutoReviewResponse,
  OpenClawReviewJob,
  OpenClawReviewJobScope,
  QuotaItem,
  ResultListResponse,
  TaskInfo,
  TaskListResponse,
} from '../../types';
import { resolveLightStatus } from '../../utils/experience';
import { repairMojibakeText } from '../../utils/text';

type ResultFilter =
  | 'drafted_pending'
  | 'need_review'
  | 'conflict'
  | 'all'
  | 'green'
  | 'yellow'
  | 'red';

const LIGHT_STATUS_MAP: Record<string, { color: string; text: string }> = {
  green: { color: 'success', text: '绿灯' },
  yellow: { color: 'warning', text: '黄灯' },
  red: { color: 'error', text: '红灯' },
};

const REVIEW_JOB_STATUS_MAP: Record<string, { color: string; text: string }> = {
  ready: { color: 'default', text: '待执行' },
  running: { color: 'processing', text: '执行中' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
};

const REVIEW_SCOPE_MAP: Record<OpenClawReviewJobScope, string> = {
  need_review: '优先复核项',
  all_pending: '全部待处理项',
  yellow_red_pending: '仅黄灯/红灯待确认项',
};

const DECISION_TYPE_MAP: Record<string, { color: string; text: string }> = {
  agree: { color: 'success', text: '保持 Jarvis' },
  override_within_candidates: { color: 'gold', text: '候选内改判' },
  retry_search_then_select: { color: 'blue', text: '建议重搜' },
  candidate_pool_insufficient: { color: 'volcano', text: '候选不足' },
  abstain: { color: 'default', text: '弃权' },
};

const CONFIRM_STATUS_MAP: Record<string, { color: string; text: string }> = {
  pending: { color: 'orange', text: '待人工确认' },
  approved: { color: 'blue', text: '人工已通过' },
  rejected: { color: 'red', text: '人工已驳回' },
};

const OPENCLAW_REVIEW_CONTEXT_KEY = 'openclaw-review-context-v1';
const OPENCLAW_TASK_PAGE_SIZE = 100;
const OPENCLAW_TASK_MAX_PAGES = 10;

function formatTime(value?: string | null): string {
  if (!value) {
    return '-';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString('zh-CN', { hour12: false });
}

function extractUuid(value: string): string {
  const match = value.match(
    /[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i,
  );
  return match?.[0] || '';
}

function normalizeDisplayText(value?: string | null): string {
  return repairMojibakeText(String(value || ''), true)?.trim() || '';
}

type QmdRecallHit = {
  chunk_id?: string;
  score?: number;
  title?: string;
  heading?: string;
  category?: string;
  page_type?: string;
  path?: string;
  specialty?: string;
  source_kind?: string;
  preview?: string;
};

type QmdRecallPayload = {
  query?: string;
  count?: number;
  hits?: QmdRecallHit[];
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function toQmdRecallHit(value: unknown): QmdRecallHit | null {
  const record = asRecord(value);
  if (!record) {
    return null;
  }
  return {
    chunk_id: normalizeDisplayText(String(record.chunk_id || '')),
    score: typeof record.score === 'number' ? record.score : undefined,
    title: normalizeDisplayText(String(record.title || '')),
    heading: normalizeDisplayText(String(record.heading || '')),
    category: normalizeDisplayText(String(record.category || '')),
    page_type: normalizeDisplayText(String(record.page_type || '')),
    path: normalizeDisplayText(String(record.path || '')),
    specialty: normalizeDisplayText(String(record.specialty || '')),
    source_kind: normalizeDisplayText(String(record.source_kind || '')),
    preview: normalizeDisplayText(String(record.preview || '')),
  };
}

function getQmdRecall(item: MatchResult): QmdRecallPayload | null {
  const payload = asRecord(item.openclaw_review_payload);
  const reviewContext = asRecord(payload?.review_context);
  const qmdRecall = asRecord(reviewContext?.qmd_recall);
  if (!qmdRecall) {
    return null;
  }
  const rawHits = Array.isArray(qmdRecall.hits) ? qmdRecall.hits : [];
  const hits = rawHits
    .map((raw) => toQmdRecallHit(raw))
    .filter((raw): raw is QmdRecallHit => Boolean(raw));
  return {
    query: normalizeDisplayText(String(qmdRecall.query || '')),
    count: typeof qmdRecall.count === 'number' ? qmdRecall.count : hits.length,
    hits,
  };
}

function quotaLines(quotas: MatchResult['quotas'] | MatchResult['openclaw_suggested_quotas']) {
  return (quotas || []).map((item) => {
    const quotaId = normalizeDisplayText(item.quota_id);
    const name = normalizeDisplayText(item.name);
    return [quotaId, name].filter(Boolean).join(' ');
  });
}

function buildKeywordText(item: MatchResult): string {
  return [
    item.bill_name,
    item.bill_description,
    item.section,
    item.sheet_name,
    item.openclaw_review_note,
    item.openclaw_decision_type,
    ...(item.openclaw_reason_codes || []),
    ...quotaLines(item.quotas),
    ...quotaLines(item.openclaw_suggested_quotas),
  ]
    .map((part) => String(part || '').toLowerCase())
    .join('\n');
}

function renderQuotaStack(quotas: MatchResult['quotas'] | MatchResult['openclaw_suggested_quotas']) {
  if (!quotas || quotas.length === 0) {
    return <span style={{ color: '#94a3b8' }}>暂无</span>;
  }
  return (
    <div style={{ fontSize: 12 }}>
      {quotas.map((item) => (
        <div key={`${item.quota_id}-${item.name}`}>
          {normalizeDisplayText(item.quota_id)} {normalizeDisplayText(item.name)}
        </div>
      ))}
    </div>
  );
}

function buildTaskStatsText(task: TaskInfo): string {
  const stats = task.stats;
  if (!stats) {
    return '-';
  }
  return `总 ${stats.total || 0} / 高 ${stats.high_conf || 0} / 中 ${stats.mid_conf || 0} / 低 ${stats.low_conf || 0}`;
}

type SuggestionStrength = 'strong_change' | 'optional_change' | 'keep';

const SUGGESTION_STRENGTH_MAP: Record<SuggestionStrength, { color: string; text: string }> = {
  strong_change: { color: 'red', text: '强建议改' },
  optional_change: { color: 'gold', text: '可改可不改' },
  keep: { color: 'blue', text: '建议维持' },
};

function hasPendingSuggestion(item: MatchResult): boolean {
  return item.openclaw_review_status === 'reviewed' && item.openclaw_review_confirm_status === 'pending';
}

function getSuggestionStrength(item: MatchResult): SuggestionStrength {
  if (item.openclaw_decision_type === 'agree') {
    return 'keep';
  }
  if (
    item.openclaw_error_type === 'wrong_family' ||
    item.openclaw_error_type === 'wrong_book' ||
    item.openclaw_error_type === 'wrong_param' ||
    item.openclaw_decision_type === 'override_within_candidates' ||
    item.openclaw_decision_type === 'candidate_pool_insufficient'
  ) {
    return 'strong_change';
  }
  return 'optional_change';
}

function shortenReason(text?: string | null): string {
  const normalized = String(text || '').replace(/\s+/g, ' ').trim();
  if (!normalized) {
    return '';
  }
  const firstSentence = normalized.split(/[。！？!?\n]/)[0]?.trim() || normalized;
  return firstSentence.length > 52 ? `${firstSentence.slice(0, 52)}...` : firstSentence;
}

function getSuggestionReason(item: MatchResult): string {
  const errorReasonMap: Record<string, string> = {
    wrong_family: '原结果错大类，建议改到正确对象。',
    wrong_book: '原结果错册或错专业，建议切到正确定额册。',
    wrong_param: '原结果参数不匹配，建议换成更贴近规格的定额。',
    synonym_gap: '原结果方向接近，但名称映射还不够准。',
    low_confidence_override: '当前建议比 Jarvis 更稳，但仍建议人工扫一眼。',
    missing_candidate: '现有候选不足，建议人工补选或改写检索。',
    unknown: '需要人工复核差异后决定。',
  };
  if (item.openclaw_error_type && errorReasonMap[item.openclaw_error_type]) {
    return errorReasonMap[item.openclaw_error_type];
  }
  if (item.openclaw_decision_type === 'agree') {
    return 'OpenClaw 认为 Jarvis 原结果可接受。';
  }
  return shortenReason(item.openclaw_review_note) || shortenReason(item.explanation) || '需要人工复核差异后决定。';
}

function normalizeAlternativeQuota(raw: Record<string, unknown>): QuotaItem | null {
  const quotaId = normalizeDisplayText(String(raw.quota_id || ''));
  const name = normalizeDisplayText(String(raw.name || ''));
  if (!quotaId || !name) {
    return null;
  }
  return {
    quota_id: quotaId,
    name,
    unit: normalizeDisplayText(String(raw.unit || '')),
    param_score: typeof raw.param_score === 'number' ? raw.param_score : null,
    rerank_score: typeof raw.rerank_score === 'number' ? raw.rerank_score : null,
    source: normalizeDisplayText(String(raw.source || 'alternative')),
  };
}

function buildInlineQuotaOptions(item: MatchResult): Array<{
  key: string;
  quota: QuotaItem;
  origin: 'suggested' | 'alternative';
}> {
  const merged: Array<{
    key: string;
    quota: QuotaItem;
    origin: 'suggested' | 'alternative';
  }> = [];
  const seen = new Set<string>();

  for (const quota of item.openclaw_suggested_quotas || []) {
    const key = `${quota.quota_id}::${quota.name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push({ key, quota, origin: 'suggested' });
  }

  for (const raw of item.alternatives || []) {
    if (!raw || typeof raw !== 'object') continue;
    const quota = normalizeAlternativeQuota(raw as Record<string, unknown>);
    if (!quota) continue;
    const key = `${quota.quota_id}::${quota.name}`;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push({ key, quota, origin: 'alternative' });
  }

  return merged.slice(0, 4);
}

function readStoredReviewContext(): {
  taskId?: string;
  reviewJobId?: string;
  resultFilter?: ResultFilter;
} {
  if (typeof window === 'undefined') {
    return {};
  }
  try {
    const raw = window.localStorage.getItem(OPENCLAW_REVIEW_CONTEXT_KEY);
    if (!raw) {
      return {};
    }
    return JSON.parse(raw) as {
      taskId?: string;
      reviewJobId?: string;
      resultFilter?: ResultFilter;
    };
  } catch {
    return {};
  }
}

export default function OpenClawReviewPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const storedContext = useMemo(() => readStoredReviewContext(), []);

  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [items, setItems] = useState<MatchResult[]>([]);
  const [reviewJob, setReviewJob] = useState<OpenClawReviewJob | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState(
    searchParams.get('task_id') || storedContext.taskId || '',
  );
  const [selectedReviewJobId, setSelectedReviewJobId] = useState(
    searchParams.get('review_job_id') || storedContext.reviewJobId || '',
  );
  const [scope, setScope] = useState<OpenClawReviewJobScope>('yellow_red_pending');
  const [note, setNote] = useState('');
  const [resultFilter, setResultFilter] = useState<ResultFilter>(
    (searchParams.get('result_filter') as ResultFilter) || storedContext.resultFilter || 'drafted_pending',
  );
  const [keyword, setKeyword] = useState('');
  const [loadingTasks, setLoadingTasks] = useState(false);
  const [loadingItems, setLoadingItems] = useState(false);
  const [loadingReviewJob, setLoadingReviewJob] = useState(false);
  const [creatingReviewJob, setCreatingReviewJob] = useState(false);
  const [runningBatch, setRunningBatch] = useState(false);
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());
  const [actingIds, setActingIds] = useState<Set<string>>(new Set());
  const [batchActing, setBatchActing] = useState(false);

  const orderedTasks = useMemo(
    () =>
      [...tasks].sort((left, right) => {
        const leftTime = new Date(left.completed_at || left.created_at).getTime();
        const rightTime = new Date(right.completed_at || right.created_at).getTime();
        return rightTime - leftTime;
      }),
    [tasks],
  );

  const selectedTask = useMemo(
    () => orderedTasks.find((task) => task.id === selectedTaskId) || null,
    [orderedTasks, selectedTaskId],
  );

  const draftedPendingItems = useMemo(
    () =>
      items.filter(
        (item) =>
          item.openclaw_review_status === 'reviewed' &&
          item.openclaw_review_confirm_status === 'pending',
      ),
    [items],
  );

  const reviewableItems = useMemo(
    () =>
      items.filter((item) => {
        const light = resolveLightStatus(item);
        return (
          (item.review_status === 'pending' && (light === 'yellow' || light === 'red')) ||
          (item.openclaw_review_status === 'reviewed' &&
            item.openclaw_review_confirm_status === 'pending')
        );
      }),
    [items],
  );

  const conflictItems = useMemo(
    () =>
      draftedPendingItems.filter(
        (item) => item.openclaw_decision_type && item.openclaw_decision_type !== 'agree',
      ),
    [draftedPendingItems],
  );

  const resultCounts = useMemo(
    () => ({
      total: items.length,
      pendingFormal: items.filter((item) => item.review_status === 'pending').length,
      draftedPending: draftedPendingItems.length,
      reviewable: reviewableItems.length,
      conflict: conflictItems.length,
      green: items.filter((item) => resolveLightStatus(item) === 'green').length,
      yellow: items.filter((item) => resolveLightStatus(item) === 'yellow').length,
      red: items.filter((item) => resolveLightStatus(item) === 'red').length,
    }),
    [conflictItems.length, draftedPendingItems.length, items, reviewableItems.length],
  );

  const filteredItems = useMemo(() => {
    let base = items;
    if (resultFilter === 'drafted_pending') {
      base = draftedPendingItems;
    } else if (resultFilter === 'need_review') {
      base = reviewableItems;
    } else if (resultFilter === 'conflict') {
      base = conflictItems;
    } else if (resultFilter !== 'all') {
      base = items.filter((item) => resolveLightStatus(item) === resultFilter);
    }

    const normalizedKeyword = keyword.trim().toLowerCase();
    if (!normalizedKeyword) {
      return base;
    }
    return base.filter((item) => buildKeywordText(item).includes(normalizedKeyword));
  }, [conflictItems, draftedPendingItems, items, keyword, resultFilter, reviewableItems]);

  const strongSuggestionItems = useMemo(
    () => filteredItems.filter((item) => hasPendingSuggestion(item) && getSuggestionStrength(item) === 'strong_change'),
    [filteredItems],
  );
  const keepSuggestionItems = useMemo(
    () => filteredItems.filter((item) => hasPendingSuggestion(item) && getSuggestionStrength(item) === 'keep'),
    [filteredItems],
  );

  const syncSearchParams = useCallback(
    (taskId: string, reviewJobId: string, nextResultFilter: ResultFilter) => {
      const next = new URLSearchParams(searchParams);
      if (taskId) {
        next.set('task_id', taskId);
      } else {
        next.delete('task_id');
      }
      if (reviewJobId) {
        next.set('review_job_id', reviewJobId);
      } else {
        next.delete('review_job_id');
      }
      if (nextResultFilter) {
        next.set('result_filter', nextResultFilter);
      } else {
        next.delete('result_filter');
      }
      if (next.toString() !== searchParams.toString()) {
        setSearchParams(next, { replace: true });
      }
    },
    [searchParams, setSearchParams],
  );

  const loadTasks = useCallback(async () => {
    setLoadingTasks(true);
    try {
      const allItems: TaskInfo[] = [];
      for (let page = 1; page <= OPENCLAW_TASK_MAX_PAGES; page += 1) {
        const { data } = await api.get<TaskListResponse>('/openclaw/tasks', {
          params: { page, size: OPENCLAW_TASK_PAGE_SIZE, status_filter: 'completed' },
        });
        allItems.push(...data.items);
        if (
          allItems.length >= data.total ||
          data.items.length < OPENCLAW_TASK_PAGE_SIZE
        ) {
          break;
        }
      }

      setTasks(allItems);
      if (!selectedTaskId && allItems.length > 0) {
        setSelectedTaskId(allItems[0].id);
      }
      if (selectedTaskId && !allItems.some((item) => item.id === selectedTaskId)) {
        setSelectedTaskId(allItems[0]?.id || '');
        setSelectedReviewJobId('');
        setReviewJob(null);
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载 Jarvis 已完成任务失败');
    } finally {
      setLoadingTasks(false);
    }
  }, [message, selectedTaskId]);

  const loadReviewItems = useCallback(
    async (taskId: string) => {
      if (!taskId) {
        setItems([]);
        return;
      }
      setLoadingItems(true);
      try {
        const { data } = await api.get<ResultListResponse>(`/openclaw/tasks/${taskId}/review-items`);
        setItems(data.items);
      } catch (error: any) {
        message.error(error?.response?.data?.detail || '加载 OpenClaw 工作台结果失败');
        setItems([]);
      } finally {
        setLoadingItems(false);
      }
    },
    [message],
  );

  const loadReviewJob = useCallback(
    async (reviewJobId: string) => {
      if (!reviewJobId) {
        setReviewJob(null);
        return null;
      }
      setLoadingReviewJob(true);
      try {
        const { data } = await api.get<OpenClawReviewJob>(`/openclaw/review-jobs/${reviewJobId}`);
        setReviewJob(data);
        setScope(data.scope);
        return data;
      } catch (error: any) {
        message.error(error?.response?.data?.detail || '加载审核作业失败');
        setReviewJob(null);
        return null;
      } finally {
        setLoadingReviewJob(false);
      }
    },
    [message],
  );

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    syncSearchParams(selectedTaskId, selectedReviewJobId, resultFilter);
  }, [resultFilter, selectedReviewJobId, selectedTaskId, syncSearchParams]);

  useEffect(() => {
    if (typeof window === 'undefined') {
      return;
    }
    window.localStorage.setItem(OPENCLAW_REVIEW_CONTEXT_KEY, JSON.stringify({
      taskId: selectedTaskId,
      reviewJobId: selectedReviewJobId,
      resultFilter,
    }));
  }, [resultFilter, selectedReviewJobId, selectedTaskId]);

  useEffect(() => {
    if (!selectedTaskId) {
      setItems([]);
      return;
    }
    void loadReviewItems(selectedTaskId);
  }, [loadReviewItems, selectedTaskId]);

  useEffect(() => {
    if (!selectedReviewJobId) {
      setReviewJob(null);
      return;
    }
    void loadReviewJob(selectedReviewJobId);
  }, [loadReviewJob, selectedReviewJobId]);

  const buildResultPagePath = useCallback((resultId?: string) => {
    if (!selectedTaskId) {
      return '/admin/openclaw-reviews';
    }
    const next = new URLSearchParams();
    if (resultId) {
      next.set('result_id', resultId);
    }
    next.set('source', 'openclaw-review');
    const returnParams = new URLSearchParams();
    returnParams.set('task_id', selectedTaskId);
    if (selectedReviewJobId) {
      returnParams.set('review_job_id', selectedReviewJobId);
    }
    if (resultFilter) {
      returnParams.set('result_filter', resultFilter);
    }
    next.set('return_to', `/admin/openclaw-reviews?${returnParams.toString()}`);
    return `/tasks/${selectedTaskId}/results?${next.toString()}`;
  }, [resultFilter, selectedReviewJobId, selectedTaskId]);

  const handleSelectTask = (taskId: string) => {
    setSelectedTaskId(taskId);
    setSelectedReviewJobId('');
    setReviewJob(null);
    setResultFilter('drafted_pending');
  };

  const ensureReviewJob = useCallback(async () => {
    if (!selectedTaskId) {
      message.warning('请先选择一个 Jarvis 已完成任务');
      return null;
    }

    if (
      reviewJob &&
      reviewJob.source_task_id === selectedTaskId &&
      reviewJob.scope === scope &&
      (reviewJob.status === 'ready' || reviewJob.status === 'running')
    ) {
      return reviewJob;
    }

    setCreatingReviewJob(true);
    try {
      const { data } = await api.post<OpenClawReviewJob>('/openclaw/review-jobs', {
        source_task_id: selectedTaskId,
        scope,
        note: note.trim(),
      });
      setReviewJob(data);
      setSelectedReviewJobId(data.id);
      message.success('已创建 OpenClaw 审核作业');
      return data;
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '创建审核作业失败';
      const existingReviewJobId = extractUuid(String(detail));
      if (existingReviewJobId) {
        const existingReviewJob = await loadReviewJob(existingReviewJobId);
        if (existingReviewJob) {
          setSelectedReviewJobId(existingReviewJob.id);
          message.info('已切换到现有活动审核作业');
          return existingReviewJob;
        }
      }
      message.error(detail);
      return null;
    } finally {
      setCreatingReviewJob(false);
    }
  }, [loadReviewJob, message, note, reviewJob, scope, selectedTaskId]);

  const handleRunBatchAutoReview = useCallback(async () => {
    if (!selectedTaskId) {
      message.warning('请先选择一个 Jarvis 已完成任务');
      return;
    }

    const activeReviewJob = await ensureReviewJob();
    if (!activeReviewJob) {
      return;
    }

    setRunningBatch(true);
    try {
      const { data } = await api.post<OpenClawBatchAutoReviewResponse>(
        `/openclaw/tasks/${selectedTaskId}/results/batch-auto-review`,
        {
          review_job_id: activeReviewJob.id,
          scope: activeReviewJob.scope,
        },
      );
      setSelectedReviewJobId(activeReviewJob.id);
      setResultFilter('drafted_pending');
      await Promise.all([
        loadTasks(),
        loadReviewItems(selectedTaskId),
        loadReviewJob(activeReviewJob.id),
      ]);
      if (data.failed_count > 0) {
        message.warning(
          `批量复判完成：生成 ${data.drafted_count} 条建议，跳过 ${data.skipped_count} 条，失败 ${data.failed_count} 条`,
        );
      } else {
        message.success(
          `批量复判完成：生成 ${data.drafted_count} 条建议，跳过 ${data.skipped_count} 条`,
        );
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '执行批量复判失败');
    } finally {
      setRunningBatch(false);
    }
  }, [ensureReviewJob, loadReviewItems, loadReviewJob, loadTasks, message, selectedTaskId]);

  const handleRefresh = useCallback(async () => {
    await loadTasks();
    if (selectedTaskId) {
      await loadReviewItems(selectedTaskId);
    }
    if (selectedReviewJobId) {
      await loadReviewJob(selectedReviewJobId);
    }
  }, [loadReviewItems, loadReviewJob, loadTasks, selectedReviewJobId, selectedTaskId]);

  const markActing = useCallback((resultId: string, active: boolean) => {
    setActingIds((prev) => {
      const next = new Set(prev);
      if (active) {
        next.add(resultId);
      } else {
        next.delete(resultId);
      }
      return next;
    });
  }, []);

  const confirmKeepJarvisRequest = useCallback(async (item: MatchResult, reviewNote: string) => {
    if (!selectedTaskId) {
      return;
    }
    if (hasPendingSuggestion(item)) {
      await api.post(`/openclaw/tasks/${selectedTaskId}/results/${item.id}/review-confirm`, {
        decision: 'reject',
        review_note: reviewNote,
      });
      return;
    }
    if (item.review_status === 'pending') {
      await api.post(`/tasks/${selectedTaskId}/results/confirm`, {
        result_ids: [item.id],
      });
    }
  }, [selectedTaskId]);

  const confirmApproveSuggestionRequest = useCallback(async (item: MatchResult, reviewNote: string) => {
    if (!selectedTaskId || !hasPendingSuggestion(item)) {
      return;
    }
    await api.post(`/openclaw/tasks/${selectedTaskId}/results/${item.id}/review-confirm`, {
      decision: 'approve',
      review_note: reviewNote,
    });
  }, [selectedTaskId]);

  const refreshWorkbenchState = useCallback(async () => {
    await Promise.all([
      loadTasks(),
      selectedTaskId ? loadReviewItems(selectedTaskId) : Promise.resolve(),
      selectedReviewJobId ? loadReviewJob(selectedReviewJobId) : Promise.resolve(null),
    ]);
  }, [loadReviewItems, loadReviewJob, loadTasks, selectedReviewJobId, selectedTaskId]);

  const handleKeepJarvis = useCallback(async (item: MatchResult) => {
    if (!selectedTaskId) {
      return;
    }
    markActing(item.id, true);
    try {
      await confirmKeepJarvisRequest(item, 'OpenClaw 工作台维持 Jarvis 原结果');
      message.success('已维持 Jarvis 原结果');
      await refreshWorkbenchState();
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '维持 Jarvis 失败';
      if (error?.response?.status === 409 && String(detail).includes('无需再次确认')) {
        message.info('这条结果已经正式应用，无需再次确认');
        await refreshWorkbenchState();
      } else {
        message.error(detail);
      }
    } finally {
      markActing(item.id, false);
    }
  }, [confirmKeepJarvisRequest, markActing, message, refreshWorkbenchState, selectedTaskId]);

  const handleApproveSuggestion = useCallback(async (item: MatchResult) => {
    if (!selectedTaskId) {
      return;
    }
    markActing(item.id, true);
    try {
      await confirmApproveSuggestionRequest(item, 'OpenClaw 工作台采纳建议');
      message.success('已采纳 OpenClaw 建议');
      await refreshWorkbenchState();
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '采纳 OpenClaw 建议失败';
      if (error?.response?.status === 409 && String(detail).includes('无需再次确认')) {
        message.info('这条建议已经正式应用，无需再次确认');
        await refreshWorkbenchState();
      } else {
        message.error(detail);
      }
    } finally {
      markActing(item.id, false);
    }
  }, [confirmApproveSuggestionRequest, markActing, message, refreshWorkbenchState, selectedTaskId]);

  const handlePickQuota = useCallback(async (
    item: MatchResult,
    quota: QuotaItem,
    origin: 'suggested' | 'alternative',
  ) => {
    if (!selectedTaskId) {
      return;
    }
    markActing(item.id, true);
    try {
      if (hasPendingSuggestion(item)) {
        await confirmKeepJarvisRequest(item, 'OpenClaw 工作台改为人工处理后另选定额');
      }
      await api.put(`/tasks/${selectedTaskId}/results/${item.id}`, {
        corrected_quotas: [{
          quota_id: quota.quota_id,
          name: quota.name,
          unit: quota.unit || '',
          source: quota.source || (origin === 'suggested' ? 'openclaw_manual' : 'manual_candidate'),
        }],
        review_note: `OpenClaw 工作台人工选定${origin === 'suggested' ? '建议' : '候选'}定额: ${quota.quota_id}`,
      });
      message.success(`已改为 ${quota.quota_id}`);
      await loadReviewItems(selectedTaskId);
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '人工选定定额失败');
    } finally {
      markActing(item.id, false);
    }
  }, [confirmKeepJarvisRequest, loadReviewItems, markActing, message, selectedTaskId]);

  const toggleItemExpanded = useCallback((itemId: string) => {
    setExpandedItems((prev) => {
      const next = new Set(prev);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
  }, []);

  const runBatchAction = useCallback(async (
    targetItems: MatchResult[],
    handler: (item: MatchResult) => Promise<void>,
    successText: string,
  ) => {
    if (!selectedTaskId || targetItems.length === 0) {
      return;
    }
    setBatchActing(true);
    try {
      for (const item of targetItems) {
        await handler(item);
      }
      message.success(successText);
      await refreshWorkbenchState();
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '批量处理失败');
    } finally {
      setBatchActing(false);
    }
  }, [message, refreshWorkbenchState, selectedTaskId]);

  const taskColumns: ColumnsType<TaskInfo> = [
    {
      title: 'Jarvis 任务',
      key: 'name',
      width: 320,
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{normalizeDisplayText(record.name) || '-'}</div>
          <div style={{ color: '#64748b', fontSize: 12, marginTop: 4 }}>
            {normalizeDisplayText(record.province)}
            {record.username ? ` / ${normalizeDisplayText(record.username)}` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '完成时间',
      dataIndex: 'completed_at',
      key: 'completed_at',
      width: 180,
      render: (value: string | null, record) => formatTime(value || record.created_at),
    },
    {
      title: '统计',
      key: 'stats',
      width: 220,
      render: (_value, record) => buildTaskStatsText(record),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (value: string) => <Tag color={value === 'completed' ? 'success' : 'default'}>{value}</Tag>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (_value, record) => (
        <Button
          type={record.id === selectedTaskId ? 'primary' : 'default'}
          size="small"
          onClick={() => handleSelectTask(record.id)}
        >
          {record.id === selectedTaskId ? '当前任务' : '进入工作台'}
        </Button>
      ),
    },
  ];

  const resultColumns: ColumnsType<MatchResult> = [
    {
      title: '序号',
      dataIndex: 'index',
      key: 'index',
      width: 72,
      render: (value: number) => value + 1,
    },
    {
      title: '清单名称',
      dataIndex: 'bill_name',
      key: 'bill_name',
      width: 260,
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{normalizeDisplayText(record.bill_name) || '-'}</div>
          {record.bill_description ? (
            <div style={{ color: '#64748b', fontSize: 12, marginTop: 4 }}>
              {normalizeDisplayText(record.bill_description)}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      title: '灯色',
      key: 'light_status',
      width: 96,
      render: (_value, record) => {
        const info = LIGHT_STATUS_MAP[resolveLightStatus(record)] || { color: 'default', text: '-' };
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: 'Jarvis 原结果',
      dataIndex: 'quotas',
      key: 'quotas',
      width: 220,
      render: (quotas: MatchResult['quotas']) => renderQuotaStack(quotas),
    },
    {
      title: 'OpenClaw 建议',
      key: 'openclaw_review',
      width: 280,
      render: (_value, record) => {
        const decisionInfo = DECISION_TYPE_MAP[record.openclaw_decision_type || ''] || null;
        return (
          <Space direction="vertical" size={6} style={{ width: '100%' }}>
            {decisionInfo ? <Tag color={decisionInfo.color}>{decisionInfo.text}</Tag> : <Tag>未生成</Tag>}
            {renderQuotaStack(record.openclaw_suggested_quotas)}
          </Space>
        );
      },
    },
    {
      title: '人工确认',
      key: 'confirm_status',
      width: 120,
      render: (_value, record) => {
        const info = CONFIRM_STATUS_MAP[record.openclaw_review_confirm_status] || {
          color: 'default',
          text: record.openclaw_review_confirm_status || '-',
        };
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: '复判说明',
      key: 'note',
      render: (_value, record) => (
        <Space direction="vertical" size={6} style={{ width: '100%' }}>
          <span>{normalizeDisplayText(record.openclaw_review_note) || <span style={{ color: '#94a3b8' }}>-</span>}</span>
          {record.openclaw_reason_codes && record.openclaw_reason_codes.length > 0 ? (
            <Space wrap size={[4, 4]}>
              {record.openclaw_reason_codes.slice(0, 4).map((item) => (
                <Tag key={item}>{item}</Tag>
              ))}
            </Space>
          ) : null}
        </Space>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 110,
      fixed: 'right',
      render: (_value, record) => (
        <Button
          size="small"
          icon={<EyeOutlined />}
          onClick={() => navigate(buildResultPagePath(record.id))}
          disabled={!selectedTaskId}
        >
          查看详情
        </Button>
      ),
    },
  ];

  const renderReviewCard = useCallback((record: MatchResult) => {
    const lightInfo = LIGHT_STATUS_MAP[resolveLightStatus(record)] || { color: 'default', text: '-' };
    const strengthInfo = SUGGESTION_STRENGTH_MAP[getSuggestionStrength(record)];
    const confirmInfo = CONFIRM_STATUS_MAP[record.openclaw_review_confirm_status] || {
      color: 'default',
      text: record.openclaw_review_confirm_status || '-',
    };
    const decisionInfo = DECISION_TYPE_MAP[record.openclaw_decision_type || ''] || null;
    const acting = actingIds.has(record.id) || batchActing;
    const expanded = expandedItems.has(record.id);
    const quotaOptions = buildInlineQuotaOptions(record);
    const qmdRecall = getQmdRecall(record);
    const jarvisLine = quotaLines(record.quotas)[0] || '暂无 Jarvis 原结果';
    const suggestedLine = quotaLines(record.openclaw_suggested_quotas)[0]
      || (record.openclaw_decision_type === 'agree' ? '建议维持 Jarvis 原结果' : '暂无 OpenClaw 建议定额');

    return (
      <Card key={record.id} size="small" styles={{ body: { padding: 16 } }} style={{ borderRadius: 12 }}>
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
            <div>
              <div style={{ fontSize: 16, fontWeight: 600 }}>{normalizeDisplayText(record.bill_name) || '-'}</div>
              <div style={{ marginTop: 4, fontSize: 12, color: '#64748b' }}>
                #{record.index + 1}
                {record.bill_code ? ` / ${record.bill_code}` : ''}
                {record.bill_unit ? ` / ${record.bill_unit}` : ''}
                {record.bill_quantity != null ? ` / 工程量 ${record.bill_quantity}` : ''}
              </div>
            </div>
            <Space wrap size={[6, 6]}>
              <Tag color={lightInfo.color}>{lightInfo.text}</Tag>
              <Tag color={strengthInfo.color}>{strengthInfo.text}</Tag>
              {decisionInfo ? <Tag color={decisionInfo.color}>{decisionInfo.text}</Tag> : null}
              <Tag color={confirmInfo.color}>{confirmInfo.text}</Tag>
            </Space>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 12 }}>
            <div style={{ padding: 12, borderRadius: 10, border: '1px solid #dbeafe', background: '#f8fbff' }}>
              <Typography.Text type="secondary">Jarvis 原定额</Typography.Text>
              <div style={{ marginTop: 6, fontWeight: 600 }}>{jarvisLine}</div>
            </div>
            <div style={{ padding: 12, borderRadius: 10, border: '1px solid #ffe7ba', background: '#fffaf0' }}>
              <Typography.Text type="secondary">OpenClaw 建议定额</Typography.Text>
              <div style={{ marginTop: 6, fontWeight: 600 }}>{suggestedLine}</div>
            </div>
          </div>

          <div style={{ padding: '10px 12px', borderRadius: 10, background: '#fafafa' }}>
            <Typography.Text type="secondary">理由</Typography.Text>
            <div style={{ marginTop: 4 }}>{getSuggestionReason(record)}</div>
          </div>

          <Space wrap>
            <Button
              type="primary"
              loading={acting}
              disabled={!hasPendingSuggestion(record)}
              onClick={() => void handleApproveSuggestion(record)}
            >
              采纳建议
            </Button>
            <Button loading={acting} onClick={() => void handleKeepJarvis(record)}>
              维持原结果
            </Button>
            <Button onClick={() => toggleItemExpanded(record.id)}>
              {expanded ? '收起人工处理' : '人工处理'}
            </Button>
            <Button
              icon={<EyeOutlined />}
              onClick={() => navigate(buildResultPagePath(record.id))}
              disabled={!selectedTaskId}
            >
              打开详情页
            </Button>
          </Space>

          {expanded ? (
            <div style={{ padding: 12, borderRadius: 10, border: '1px dashed #d9d9d9', background: '#fcfcfc' }}>
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <div>
                  <Typography.Text strong>可直接选用的定额</Typography.Text>
                  <div style={{ marginTop: 8 }}>
                    {quotaOptions.length > 0 ? (
                      <Space wrap size={[8, 8]}>
                        {quotaOptions.map((option) => (
                          <Button
                            key={option.key}
                            size="small"
                            loading={acting}
                            onClick={() => void handlePickQuota(record, option.quota, option.origin)}
                          >
                            {option.origin === 'suggested' ? '建议' : '候选'}: {option.quota.quota_id}
                          </Button>
                        ))}
                      </Space>
                    ) : (
                      <Typography.Text type="secondary">当前没有可直接点选的候选，请打开详情页人工处理。</Typography.Text>
                    )}
                  </div>
                </div>

                <div>
                  <Typography.Text strong>完整清单描述</Typography.Text>
                  <div style={{ marginTop: 6, color: '#475569', whiteSpace: 'pre-wrap' }}>
                    {normalizeDisplayText(record.bill_description) || '-'}
                  </div>
                </div>

                <div>
                  <Typography.Text strong>OpenClaw 备注</Typography.Text>
                  <div style={{ marginTop: 6, color: '#475569', whiteSpace: 'pre-wrap' }}>
                    {normalizeDisplayText(record.openclaw_review_note) || '-'}
                  </div>
                </div>

                <div>
                  <Typography.Text strong>QMD 证据</Typography.Text>
                  <div
                    style={{
                      marginTop: 8,
                      padding: 12,
                      borderRadius: 10,
                      border: '1px solid #e2e8f0',
                      background: '#f8fafc',
                    }}
                  >
                    {qmdRecall ? (
                      <Space direction="vertical" size={10} style={{ width: '100%' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                          <Typography.Text type="secondary">
                            查询词: {qmdRecall.query || '-'}
                          </Typography.Text>
                          <Typography.Text type="secondary">
                            命中 {qmdRecall.count || 0} 条
                          </Typography.Text>
                        </div>
                        {qmdRecall.hits && qmdRecall.hits.length > 0 ? (
                          qmdRecall.hits.slice(0, 3).map((hit, index) => (
                            <div
                              key={hit.chunk_id || hit.path || `${record.id}-qmd-${index}`}
                              style={{
                                padding: 10,
                                borderRadius: 8,
                                background: '#fff',
                                border: '1px solid #e5e7eb',
                              }}
                            >
                              <Space direction="vertical" size={6} style={{ width: '100%' }}>
                                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                                  <Typography.Text strong>
                                    {hit.title || hit.heading || '未命名证据'}
                                  </Typography.Text>
                                  <Typography.Text type="secondary">
                                    {typeof hit.score === 'number' ? `score ${hit.score.toFixed(3)}` : ''}
                                  </Typography.Text>
                                </div>
                                <Space wrap size={[6, 6]}>
                                  {hit.category ? <Tag color="blue">{hit.category}</Tag> : null}
                                  {hit.page_type ? <Tag>{hit.page_type}</Tag> : null}
                                  {hit.specialty ? <Tag color="gold">{hit.specialty}</Tag> : null}
                                  {hit.source_kind ? <Tag color="cyan">{hit.source_kind}</Tag> : null}
                                </Space>
                                {hit.path ? (
                                  <Typography.Text type="secondary">{hit.path}</Typography.Text>
                                ) : null}
                                <div style={{ color: '#475569', whiteSpace: 'pre-wrap' }}>
                                  {hit.preview || '暂无摘要'}
                                </div>
                              </Space>
                            </div>
                          ))
                        ) : (
                          <Typography.Text type="secondary">本条目前还没有召回到可展示的 QMD 证据。</Typography.Text>
                        )}
                      </Space>
                    ) : (
                      <Typography.Text type="secondary">当前草稿里还没有写入 QMD recall。</Typography.Text>
                    )}
                  </div>
                </div>

                <div>
                  <Typography.Text strong>Jarvis 解释</Typography.Text>
                  <div style={{ marginTop: 6, color: '#475569', whiteSpace: 'pre-wrap' }}>
                    {normalizeDisplayText(record.explanation) || '-'}
                  </div>
                </div>

                {record.openclaw_reason_codes && record.openclaw_reason_codes.length > 0 ? (
                  <Space wrap size={[6, 6]}>
                    {record.openclaw_reason_codes.map((item) => (
                      <Tag key={item}>{item}</Tag>
                    ))}
                  </Space>
                ) : null}
              </Space>
            </div>
          ) : null}
        </Space>
      </Card>
    );
  }, [
    actingIds,
    batchActing,
    expandedItems,
    handleApproveSuggestion,
    handleKeepJarvis,
    handlePickQuota,
    navigate,
    selectedTaskId,
    toggleItemExpanded,
  ]);

  const reviewJobInfo = reviewJob ? REVIEW_JOB_STATUS_MAP[reviewJob.status] : null;
  const selectedTaskStats = selectedTask?.stats;

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card
        size="small"
        title="OpenClaw 工作台"
        extra={
          <Button icon={<ReloadOutlined />} onClick={() => void handleRefresh()}>
            刷新工作台
          </Button>
        }
      >
        <Typography.Paragraph style={{ marginBottom: 0, color: '#475569' }}>
          Jarvis 先跑主任务，OpenClaw 在这里主动拉取已完成任务，创建审核作业并批量生成复判建议。
          人工只需要在最后看冲突项和建议项，不用再去任务列表里一条条点。
        </Typography.Paragraph>
      </Card>

      <Card
        size="small"
        title="Jarvis 已完成任务"
        extra={<Typography.Text type="secondary">按完成时间倒序</Typography.Text>}
      >
        <Table
          rowKey="id"
          dataSource={orderedTasks}
          columns={taskColumns}
          loading={loadingTasks}
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
          scroll={{ x: 900 }}
          locale={{ emptyText: '暂无已完成任务' }}
        />
      </Card>

      {!selectedTask ? (
        <Empty description="先从上面的 Jarvis 已完成任务里选一个任务" />
      ) : (
        <>
          <Card
            size="small"
            title={`当前任务：${normalizeDisplayText(selectedTask.name) || selectedTask.id}`}
            extra={
              <Space>
                <Button onClick={() => navigate(buildResultPagePath())}>
                  打开结果页
                </Button>
              </Space>
            }
          >
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="任务标识">{selectedTask.id}</Descriptions.Item>
              <Descriptions.Item label="省份">{normalizeDisplayText(selectedTask.province) || '-'}</Descriptions.Item>
              <Descriptions.Item label="模式">{normalizeDisplayText(selectedTask.mode) || '-'}</Descriptions.Item>
              <Descriptions.Item label="完成时间">
                {formatTime(selectedTask.completed_at || selectedTask.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label="原文件">{normalizeDisplayText(selectedTask.original_filename) || '-'}</Descriptions.Item>
              <Descriptions.Item label="Jarvis 统计">
                {selectedTaskStats
                  ? `总 ${selectedTaskStats.total || 0} / 高 ${selectedTaskStats.high_conf || 0} / 中 ${selectedTaskStats.mid_conf || 0} / 低 ${selectedTaskStats.low_conf || 0}`
                  : '-'}
              </Descriptions.Item>
            </Descriptions>

            <Space direction="vertical" size="middle" style={{ width: '100%', marginTop: 16 }}>
              <Space wrap>
                <Radio.Group
                  optionType="button"
                  buttonStyle="solid"
                  value={scope}
                  onChange={(event) => setScope(event.target.value as OpenClawReviewJobScope)}
                  options={[
                    { label: REVIEW_SCOPE_MAP.yellow_red_pending, value: 'yellow_red_pending' },                  ]}
                />
                <Input
                  style={{ width: 320 }}
                  value={note}
                  onChange={(event) => setNote(event.target.value)}
                  placeholder="本轮审核作业备注，可选"
                  allowClear
                />
                <Button
                  icon={<PlusOutlined />}
                  loading={creatingReviewJob}
                  onClick={() => void ensureReviewJob()}
                >
                  创建审核作业
                </Button>
                <Button
                  type="primary"
                  icon={<PlayCircleOutlined />}
                  loading={runningBatch}
                  onClick={() => void handleRunBatchAutoReview()}
                >
                  创建并执行复判
                </Button>
              </Space>

              {reviewJob ? (
                <Alert
                  showIcon
                  type={reviewJob.status === 'failed' ? 'error' : reviewJob.status === 'completed' ? 'success' : 'info'}
                  message={
                    <Space wrap>
                      <Typography.Text strong>当前审核作业</Typography.Text>
                      <Tag color={reviewJobInfo?.color || 'default'}>{reviewJobInfo?.text || reviewJob.status}</Tag>
                      <Tag>{REVIEW_SCOPE_MAP[reviewJob.scope]}</Tag>
                      <Typography.Text type="secondary">作业 ID: {reviewJob.id}</Typography.Text>
                    </Space>
                  }
                  description={
                    <Space wrap size={[12, 8]}>
                      <span>待正式确认：{reviewJob.pending_results}</span>
                      <span>可复判：{reviewJob.reviewable_results}</span>
                      <span>已生成待确认建议：{reviewJob.reviewed_pending_count}</span>
                      <span>创建时间：{formatTime(reviewJob.created_at)}</span>
                      <span>完成时间：{formatTime(reviewJob.completed_at)}</span>
                      {reviewJob.error_message ? <span>错误：{normalizeDisplayText(reviewJob.error_message)}</span> : null}
                    </Space>
                  }
                />
              ) : (
                <Alert
                  showIcon
                  type="info"
                  message="当前还没有审核作业"
                  description="先创建审核作业，再让 OpenClaw 批量生成结构化复判草稿。这样所有系统都知道它审核的是同一个 Jarvis 任务。"
                />
              )}
            </Space>
          </Card>

          <Card size="small" title="复判概览">
            <Space wrap size={[16, 16]}>
              <Statistic title="总结果" value={resultCounts.total} />
              <Statistic title="待正式确认" value={resultCounts.pendingFormal} />
              <Statistic title="可复判" value={reviewJob?.reviewable_results ?? resultCounts.reviewable} />
              <Statistic title="待人工确认建议" value={resultCounts.draftedPending} />
              <Statistic title="冲突建议" value={resultCounts.conflict} />
              <Statistic title="黄灯" value={resultCounts.yellow} />
              <Statistic title="红灯" value={resultCounts.red} />
            </Space>
          </Card>

          {resultCounts.draftedPending === 0 && resultCounts.reviewable > 0 ? (
            <Alert
              showIcon
              type="warning"
              message="当前还没有 OpenClaw 批量复判结果"
              description="这个任务里还有可复判项。直接在上面的工作台点击“创建并执行复判”，让 OpenClaw 批量生成 draft。"
            />
          ) : null}

          <Card
            size="small"
            title="OpenClaw 复判结果"
            extra={
              <Space wrap>
                <Radio.Group
                  optionType="button"
                  value={resultFilter}
                  onChange={(event) => setResultFilter(event.target.value as ResultFilter)}
                  options={[
                    { label: `待人工确认建议 (${resultCounts.draftedPending})`, value: 'drafted_pending' },
                    { label: `优先核对 (${resultCounts.reviewable})`, value: 'need_review' },
                    { label: `冲突建议 (${resultCounts.conflict})`, value: 'conflict' },
                    { label: `全部 (${resultCounts.total})`, value: 'all' },
                    { label: `绿灯 (${resultCounts.green})`, value: 'green' },
                    { label: `黄灯 (${resultCounts.yellow})`, value: 'yellow' },
                    { label: `红灯 (${resultCounts.red})`, value: 'red' },
                  ]}
                />
                <Input.Search
                  allowClear
                  style={{ width: 280 }}
                  placeholder="搜索清单、建议、原因码"
                  value={keyword}
                  onChange={(event) => setKeyword(event.target.value)}
                />
              </Space>
            }
          >
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Space wrap>
                <Button
                  type="primary"
                  danger
                  disabled={strongSuggestionItems.length === 0}
                  loading={batchActing}
                  onClick={() => void runBatchAction(
                    strongSuggestionItems,
                    (item) => confirmApproveSuggestionRequest(item, 'OpenClaw 工作台批量采纳强建议'),
                    `已批量采纳 ${strongSuggestionItems.length} 条强建议`,
                  )}
                >
                  批量采纳强建议 ({strongSuggestionItems.length})
                </Button>
                <Button
                  disabled={keepSuggestionItems.length === 0}
                  loading={batchActing}
                  onClick={() => void runBatchAction(
                    keepSuggestionItems,
                    (item) => confirmKeepJarvisRequest(item, 'OpenClaw 工作台批量维持 Jarvis 原结果'),
                    `已批量维持 ${keepSuggestionItems.length} 条 Jarvis 原结果`,
                  )}
                >
                  批量维持原结果 ({keepSuggestionItems.length})
                </Button>
                <Typography.Text type="secondary">
                  默认先处理强建议和维持项，剩下的再点人工处理。
                </Typography.Text>
              </Space>

              {loadingItems || loadingReviewJob ? (
                <Card loading size="small" />
              ) : filteredItems.length === 0 ? (
                <Empty description="当前筛选条件下没有建议结果" />
              ) : (
                <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                  {filteredItems.map((item) => renderReviewCard(item))}
                </Space>
              )}

              <div style={{ display: 'none' }}>
            <Table
              rowKey="id"
              dataSource={filteredItems}
              columns={resultColumns}
              loading={loadingItems || loadingReviewJob}
              pagination={{ pageSize: 20, hideOnSinglePage: true }}
              scroll={{ x: 1500 }}
              locale={{ emptyText: '当前筛选条件下没有数据' }}
            />
              </div>
            </Space>
          </Card>
        </>
      )}
    </Space>
  );
}
