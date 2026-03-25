import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert,
  App,
  Button,
  Card,
  Empty,
  Input,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { EyeOutlined, ReloadOutlined } from '@ant-design/icons';
import api from '../../services/api';
import type { MatchResult, ResultListResponse, TaskInfo, TaskListResponse } from '../../types';
import { resolveLightStatus } from '../../utils/experience';

const LIGHT_STATUS_MAP: Record<string, { color: string; text: string }> = {
  green: { color: 'success', text: '绿灯' },
  yellow: { color: 'warning', text: '黄灯' },
  red: { color: 'error', text: '红灯' },
};

const CONFIRM_STATUS_MAP: Record<string, { color: string; text: string }> = {
  pending: { color: 'orange', text: '待人工确认' },
  approved: { color: 'blue', text: '已确认通过' },
  rejected: { color: 'red', text: '已人工驳回' },
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
  const [lightFilter, setLightFilter] = useState<'all' | 'green' | 'yellow' | 'red'>('all');
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
    if (!selectedTaskId) {
      return;
    }
    const next = new URLSearchParams(searchParams);
    next.set('task_id', selectedTaskId);
    setSearchParams(next, { replace: true });
    void loadReviewItems(selectedTaskId);
  }, [loadReviewItems, searchParams, selectedTaskId, setSearchParams]);

  const filteredItems = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return pendingSuggestionItems.filter((item) => {
      const lightStatus = resolveLightStatus(item);
      if (lightFilter !== 'all' && lightStatus !== lightFilter) {
        return false;
      }
      if (!normalizedKeyword) {
        return true;
      }
      return buildKeywordText(item).includes(normalizedKeyword);
    });
  }, [keyword, lightFilter, pendingSuggestionItems]);

  const counts = useMemo(
    () => ({
      total: pendingSuggestionItems.length,
      green: pendingSuggestionItems.filter((item) => resolveLightStatus(item) === 'green').length,
      yellow: pendingSuggestionItems.filter((item) => resolveLightStatus(item) === 'yellow').length,
      red: pendingSuggestionItems.filter((item) => resolveLightStatus(item) === 'red').length,
    }),
    [pendingSuggestionItems],
  );

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
      width: 280,
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
      width: 100,
      render: (_value, record) => {
        const info = LIGHT_STATUS_MAP[resolveLightStatus(record)] || { color: 'default', text: '-' };
        return (
          <Tag color={info.color} style={{ margin: 0 }}>
            {info.text}
          </Tag>
        );
      },
    },
    {
      title: '原始结果',
      dataIndex: 'quotas',
      key: 'quotas',
      width: 260,
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
      width: 260,
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
      title: '确认状态',
      key: 'confirm_status',
      width: 140,
      render: (_value, record) => {
        const info = CONFIRM_STATUS_MAP[record.openclaw_review_confirm_status] || {
          color: 'default',
          text: record.openclaw_review_confirm_status || '-',
        };
        return (
          <Tag color={info.color} style={{ margin: 0 }}>
            {info.text}
          </Tag>
        );
      },
    },
    {
      title: '建议说明',
      dataIndex: 'openclaw_review_note',
      key: 'openclaw_review_note',
      render: (value: string) => value || <span style={{ color: '#94a3b8' }}>-</span>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 132,
      fixed: 'right',
      render: (_value, record) => (
        <Button
          size="small"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/tasks/${selectedTaskId}/results?result_id=${record.id}`)}
          disabled={!selectedTaskId}
        >
          定位这条
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <Typography.Title level={4} style={{ margin: 0 }}>
            待确认的 OpenClaw 建议
          </Typography.Title>
          <Typography.Text type="secondary">
            这里不是“所有待审核结果”，这里只显示 OpenClaw 已经给出建议、但还没有人工二次确认的记录。
          </Typography.Text>
        </Space>
      </Card>

      <Alert
        type="info"
        showIcon
        message="当前页面看的不是主链待审核总量"
        description="结果页里的“待审核”表示主链结果还没正式确认；这里的“待确认建议”只表示 OpenClaw 已经提交建议，且还没有人工二次确认。两者不是同一个池子。"
      />

      <Card
        title="先选任务"
        extra={
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              void loadTasks();
              if (selectedTaskId) {
                void loadReviewItems(selectedTaskId);
              }
            }}
          >
            刷新
          </Button>
        }
      >
        <Space wrap>
          <Typography.Text strong>任务</Typography.Text>
          <Select
            showSearch
            style={{ minWidth: 460 }}
            placeholder="选择一个已完成任务"
            loading={loadingTasks}
            value={selectedTaskId || undefined}
            onChange={(value) => setSelectedTaskId(value)}
            options={tasks.map((task) => ({ label: taskLabel(task), value: task.id }))}
            optionFilterProp="label"
          />
          {selectedTask ? (
            <Button onClick={() => navigate(`/tasks/${selectedTask.id}/results`)}>
              打开结果页
            </Button>
          ) : null}
        </Space>
      </Card>

      <Space wrap size="middle">
        <Card size="small">
          <Statistic title="待确认建议" value={counts.total} />
        </Card>
        <Card size="small">
          <Statistic title="绿灯" value={counts.green} valueStyle={{ color: '#16a34a' }} />
        </Card>
        <Card size="small">
          <Statistic title="黄灯" value={counts.yellow} valueStyle={{ color: '#d97706' }} />
        </Card>
        <Card size="small">
          <Statistic title="红灯" value={counts.red} valueStyle={{ color: '#dc2626' }} />
        </Card>
      </Space>

      <Card
        title={selectedTask ? `${selectedTask.name} 的待确认建议` : '待确认建议'}
        extra={selectedTask ? `共 ${filteredItems.length} / ${pendingSuggestionItems.length} 条` : undefined}
      >
        {!selectedTask && !loadingTasks ? (
          <Empty description="先选择一个任务" />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space wrap>
              <Select
                value={lightFilter}
                style={{ width: 180 }}
                onChange={(value) => setLightFilter(value)}
                options={[
                  { label: `全部灯色 (${counts.total})`, value: 'all' },
                  { label: `绿灯 (${counts.green})`, value: 'green' },
                  { label: `黄灯 (${counts.yellow})`, value: 'yellow' },
                  { label: `红灯 (${counts.red})`, value: 'red' },
                ]}
              />
              <Input.Search
                allowClear
                style={{ width: 360 }}
                placeholder="按清单名称、描述、分部、建议内容筛选"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
            </Space>

            <Alert
              type="warning"
              showIcon
              message="审批前先看这里"
              description="这里保留的是 OpenClaw 已提交建议、但还没有人工拍板的记录。点击“定位这条”可回到原始结果页，看 alternatives、confidence 和上下文后再决定。"
            />

            <Table
              rowKey="id"
              dataSource={filteredItems}
              columns={columns}
              loading={loadingItems}
              pagination={{ pageSize: 20, hideOnSinglePage: true }}
              scroll={{ x: 1500 }}
              locale={{
                emptyText: selectedTask
                  ? '当前任务还没有 OpenClaw 已提交建议的待确认项。结果页里的“待审核”不等于这里的“待确认建议”。'
                  : '先选择一个任务',
              }}
            />
          </Space>
        )}
      </Card>
    </Space>
  );
}
