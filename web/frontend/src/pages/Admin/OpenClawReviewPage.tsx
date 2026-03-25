import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  App,
  Button,
  Card,
  Empty,
  Input,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { EyeOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '../../services/api';
import type { MatchResult, ResultListResponse, TaskInfo, TaskListResponse } from '../../types';
import { resolveLightStatus } from '../../utils/experience';

type LightFilter = 'need_check' | 'all' | 'green' | 'yellow' | 'red';

const LIGHT_STATUS_MAP: Record<string, { color: string; text: string }> = {
  green: { color: 'success', text: '绿灯' },
  yellow: { color: 'warning', text: '黄灯' },
  red: { color: 'error', text: '红灯' },
};

const CONFIRM_STATUS_MAP: Record<string, { color: string; text: string }> = {
  pending: { color: 'orange', text: '待确认' },
  approved: { color: 'blue', text: '已通过' },
  rejected: { color: 'red', text: '已驳回' },
};

function taskLabel(task: TaskInfo): string {
  const user = task.username ? ` / ${task.username}` : '';
  return `${task.name} / ${task.province}${user}`;
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
    ...quotaLines(item.quotas),
    ...quotaLines(item.openclaw_suggested_quotas),
  ]
    .map((part) => String(part || '').toLowerCase())
    .join('\n');
}

export default function OpenClawReviewPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [loadingTasks, setLoadingTasks] = useState(false);
  const [loadingItems, setLoadingItems] = useState(false);
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [items, setItems] = useState<MatchResult[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState(searchParams.get('task_id') || '');
  const [lightFilter, setLightFilter] = useState<LightFilter>('need_check');
  const [keyword, setKeyword] = useState('');

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) || null,
    [tasks, selectedTaskId],
  );

  const pendingSuggestionItems = useMemo(
    () =>
      items.filter(
        (item) =>
          item.openclaw_review_status === 'reviewed' &&
          item.openclaw_review_confirm_status === 'pending',
      ),
    [items],
  );

  const resultPendingCount = useMemo(
    () => items.filter((item) => item.review_status === 'pending').length,
    [items],
  );

  const counts = useMemo(
    () => ({
      total: pendingSuggestionItems.length,
      green: pendingSuggestionItems.filter((item) => resolveLightStatus(item) === 'green').length,
      yellow: pendingSuggestionItems.filter((item) => resolveLightStatus(item) === 'yellow').length,
      red: pendingSuggestionItems.filter((item) => resolveLightStatus(item) === 'red').length,
    }),
    [pendingSuggestionItems],
  );

  const needCheckCount = counts.yellow + counts.red;
  const hasOpenClawItems = pendingSuggestionItems.length > 0;
  const showResultPageHint = !!selectedTask && !hasOpenClawItems && resultPendingCount > 0;

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
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载 OpenClaw 任务列表失败');
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
        message.error(error?.response?.data?.detail || '加载 OpenClaw 建议列表失败');
        setItems([]);
      } finally {
        setLoadingItems(false);
      }
    },
    [message],
  );

  useEffect(() => {
    void loadTasks();
  }, [loadTasks]);

  useEffect(() => {
    if (!selectedTaskId) return;
    const next = new URLSearchParams(searchParams);
    next.set('task_id', selectedTaskId);
    setSearchParams(next, { replace: true });
    void loadReviewItems(selectedTaskId);
  }, [loadReviewItems, searchParams, selectedTaskId, setSearchParams]);

  const filteredItems = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return pendingSuggestionItems.filter((item) => {
      const lightStatus = resolveLightStatus(item);
      if (lightFilter === 'need_check' && !['yellow', 'red'].includes(lightStatus)) {
        return false;
      }
      if (lightFilter !== 'need_check' && lightFilter !== 'all' && lightStatus !== lightFilter) {
        return false;
      }
      if (!normalizedKeyword) {
        return true;
      }
      return buildKeywordText(item).includes(normalizedKeyword);
    });
  }, [keyword, lightFilter, pendingSuggestionItems]);

  const columns: ColumnsType<MatchResult> = [
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
      title: '原始结果',
      dataIndex: 'quotas',
      key: 'quotas',
      width: 220,
      render: (quotas: MatchResult['quotas']) => (
        <div style={{ fontSize: 12 }}>
          {(quotas || []).length === 0 ? <span style={{ color: '#94a3b8' }}>无匹配</span> : null}
          {(quotas || []).map((item) => (
            <div key={item.quota_id}>
              {item.quota_id} {item.name}
            </div>
          ))}
        </div>
      ),
    },
    {
      title: 'OpenClaw 建议',
      dataIndex: 'openclaw_suggested_quotas',
      key: 'openclaw_suggested_quotas',
      width: 220,
      render: (quotas: MatchResult['openclaw_suggested_quotas']) => (
        <div style={{ fontSize: 12 }}>
          {(quotas || []).length === 0 ? <span style={{ color: '#94a3b8' }}>暂无建议</span> : null}
          {(quotas || []).map((item) => (
            <div key={item.quota_id}>
              {item.quota_id} {item.name}
            </div>
          ))}
        </div>
      ),
    },
    {
      title: '状态',
      key: 'confirm_status',
      width: 110,
      render: (_value, record) => {
        const info = CONFIRM_STATUS_MAP[record.openclaw_review_confirm_status] || {
          color: 'default',
          text: record.openclaw_review_confirm_status || '-',
        };
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: '说明',
      dataIndex: 'openclaw_review_note',
      key: 'openclaw_review_note',
      render: (value: string) => value || <span style={{ color: '#94a3b8' }}>-</span>,
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
          去核对
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card size="small" title="OpenClaw 复核">
        <Space wrap>
          <Select
            showSearch
            style={{ minWidth: 520 }}
            placeholder="选择一个已完成任务"
            loading={loadingTasks}
            value={selectedTaskId || undefined}
            onChange={(value) => setSelectedTaskId(value)}
            options={tasks.map((task) => ({ label: taskLabel(task), value: task.id }))}
            optionFilterProp="label"
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              void loadTasks();
              if (selectedTaskId) void loadReviewItems(selectedTaskId);
            }}
          >
            刷新
          </Button>
          {selectedTask ? (
            <Button onClick={() => navigate(`/tasks/${selectedTask.id}/results`)}>
              打开结果页
            </Button>
          ) : null}
        </Space>
      </Card>

      {!selectedTask && !loadingTasks ? <Empty description="先选择任务" /> : null}

      {selectedTask ? (
        <Card size="small" title={selectedTask.name}>
          {showResultPageHint ? (
            <Space wrap>
              <Typography.Text>结果页还有 {resultPendingCount} 条待审核。</Typography.Text>
              <Button type="primary" onClick={() => navigate(`/tasks/${selectedTaskId}/results`)}>
                去结果页
              </Button>
            </Space>
          ) : (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Space wrap>
                <Select
                  value={lightFilter}
                  style={{ width: 220 }}
                  onChange={(value) => setLightFilter(value)}
                  options={[
                    { label: `黄灯+红灯 (${needCheckCount})`, value: 'need_check' },
                    { label: `全部 (${counts.total})`, value: 'all' },
                    { label: `绿灯 (${counts.green})`, value: 'green' },
                    { label: `黄灯 (${counts.yellow})`, value: 'yellow' },
                    { label: `红灯 (${counts.red})`, value: 'red' },
                  ]}
                />
                <Input.Search
                  allowClear
                  style={{ width: 320 }}
                  placeholder="搜索清单、建议或说明"
                  value={keyword}
                  onChange={(event) => setKeyword(event.target.value)}
                />
              </Space>

              <Table
                rowKey="id"
                dataSource={filteredItems}
                columns={columns}
                loading={loadingItems}
                pagination={{ pageSize: 20, hideOnSinglePage: true }}
                scroll={{ x: 1300 }}
                locale={{
                  emptyText: lightFilter === 'need_check' ? '没有黄灯或红灯建议' : '没有数据',
                }}
              />
            </Space>
          )}
        </Card>
      ) : null}
    </Space>
  );
}
