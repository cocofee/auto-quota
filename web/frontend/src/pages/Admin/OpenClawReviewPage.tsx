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
  ResultListResponse,
  TaskInfo,
  TaskListResponse,
} from '../../types';
import { resolveLightStatus } from '../../utils/experience';

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
  need_review: '优先要核的项',
  all_pending: '全部待正式确认项',
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

function quotaLines(quotas: MatchResult['quotas'] | MatchResult['openclaw_suggested_quotas']) {
  return (quotas || []).map((item) => `${item.quota_id} ${item.name}`);
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
          {item.quota_id} {item.name}
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

export default function OpenClawReviewPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [items, setItems] = useState<MatchResult[]>([]);
  const [reviewJob, setReviewJob] = useState<OpenClawReviewJob | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState(searchParams.get('task_id') || '');
  const [selectedReviewJobId, setSelectedReviewJobId] = useState(
    searchParams.get('review_job_id') || '',
  );
  const [scope, setScope] = useState<OpenClawReviewJobScope>('need_review');
  const [note, setNote] = useState('');
  const [resultFilter, setResultFilter] = useState<ResultFilter>('drafted_pending');
  const [keyword, setKeyword] = useState('');
  const [loadingTasks, setLoadingTasks] = useState(false);
  const [loadingItems, setLoadingItems] = useState(false);
  const [loadingReviewJob, setLoadingReviewJob] = useState(false);
  const [creatingReviewJob, setCreatingReviewJob] = useState(false);
  const [runningBatch, setRunningBatch] = useState(false);

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

  const syncSearchParams = useCallback(
    (taskId: string, reviewJobId: string) => {
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
      if (next.toString() !== searchParams.toString()) {
        setSearchParams(next, { replace: true });
      }
    },
    [searchParams, setSearchParams],
  );

  const loadTasks = useCallback(async () => {
    setLoadingTasks(true);
    try {
      const { data } = await api.get<TaskListResponse>('/openclaw/tasks', {
        params: { page: 1, size: 100, status_filter: 'completed' },
      });
      setTasks(data.items);
      if (!selectedTaskId && data.items.length > 0) {
        setSelectedTaskId(data.items[0].id);
      }
      if (selectedTaskId && !data.items.some((item) => item.id === selectedTaskId)) {
        setSelectedTaskId(data.items[0]?.id || '');
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
    syncSearchParams(selectedTaskId, selectedReviewJobId);
  }, [selectedReviewJobId, selectedTaskId, syncSearchParams]);

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

  const taskColumns: ColumnsType<TaskInfo> = [
    {
      title: 'Jarvis 任务',
      key: 'name',
      width: 320,
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{record.name}</div>
          <div style={{ color: '#64748b', fontSize: 12, marginTop: 4 }}>
            {record.province}
            {record.username ? ` / ${record.username}` : ''}
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
          <div style={{ fontWeight: 600 }}>{record.bill_name}</div>
          {record.bill_description ? (
            <div style={{ color: '#64748b', fontSize: 12, marginTop: 4 }}>{record.bill_description}</div>
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
          <span>{record.openclaw_review_note || <span style={{ color: '#94a3b8' }}>-</span>}</span>
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
          onClick={() => navigate(`/tasks/${selectedTaskId}/results?result_id=${record.id}`)}
          disabled={!selectedTaskId}
        >
          查看详情
        </Button>
      ),
    },
  ];

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
          Jarvis 先跑主任务，OpenClaw 在这里主动拉取已完成任务，创建审核作业并批量生成复判建议。人工只需要在最后看冲突项和建议项，不用再去任务列表里一条条点。
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
            title={`当前任务：${selectedTask.name}`}
            extra={
              <Space>
                <Button onClick={() => navigate(`/tasks/${selectedTask.id}/results`)}>
                  打开结果页
                </Button>
              </Space>
            }
          >
            <Descriptions size="small" column={2} bordered>
              <Descriptions.Item label="任务标识">{selectedTask.id}</Descriptions.Item>
              <Descriptions.Item label="省份">{selectedTask.province}</Descriptions.Item>
              <Descriptions.Item label="模式">{selectedTask.mode}</Descriptions.Item>
              <Descriptions.Item label="完成时间">
                {formatTime(selectedTask.completed_at || selectedTask.created_at)}
              </Descriptions.Item>
              <Descriptions.Item label="原文件">{selectedTask.original_filename}</Descriptions.Item>
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
                    { label: REVIEW_SCOPE_MAP.need_review, value: 'need_review' },
                    { label: REVIEW_SCOPE_MAP.yellow_red_pending, value: 'yellow_red_pending' },
                    { label: REVIEW_SCOPE_MAP.all_pending, value: 'all_pending' },
                  ]}
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
                      {reviewJob.error_message ? <span>错误：{reviewJob.error_message}</span> : null}
                    </Space>
                  }
                />
              ) : (
                <Alert
                  showIcon
                  type="info"
                  message="当前还没有审核作业"
                  description="先创建 review_job，再让 OpenClaw 批量生成结构化 review-draft。这样所有系统都知道它审核的是同一个 Jarvis task_id。"
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
            <Table
              rowKey="id"
              dataSource={filteredItems}
              columns={resultColumns}
              loading={loadingItems || loadingReviewJob}
              pagination={{ pageSize: 20, hideOnSinglePage: true }}
              scroll={{ x: 1500 }}
              locale={{ emptyText: '当前筛选条件下没有数据' }}
            />
          </Card>
        </>
      )}
    </Space>
  );
}
