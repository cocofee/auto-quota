import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert,
  App,
  Button,
  Card,
  Descriptions,
  Empty,
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


interface TaskOption {
  label: string;
  value: string;
  task: TaskInfo;
}

interface OpenClawKeyStatus {
  configured: boolean;
  masked_key: string;
  service_email: string;
  service_nickname: string;
  openapi_url: string;
  public_path: string;
  sync_targets: string[];
  update_hint: string;
}

interface OpenClawKeySuggestion {
  suggested_key: string;
  env_name: string;
  sync_targets: string[];
  manifest_paths: string[];
  rollout_steps: string[];
}


const REVIEW_STATUS_MAP: Record<string, { color: string; text: string }> = {
  pending: { color: 'default', text: '未建议' },
  reviewed: { color: 'orange', text: '待复核' },
  approved: { color: 'blue', text: '已通过' },
  rejected: { color: 'red', text: '已驳回' },
  applied: { color: 'processing', text: '已应用' },
};


function taskLabel(task: TaskInfo): string {
  const user = task.username ? ` / ${task.username}` : '';
  return `${task.name} / ${task.province}${user}`;
}


export default function OpenClawReviewPage() {
  const { message } = App.useApp();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [loadingTasks, setLoadingTasks] = useState(false);
  const [loadingItems, setLoadingItems] = useState(false);
  const [loadingKeyStatus, setLoadingKeyStatus] = useState(false);
  const [generatingKey, setGeneratingKey] = useState(false);
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [items, setItems] = useState<MatchResult[]>([]);
  const [keyStatus, setKeyStatus] = useState<OpenClawKeyStatus | null>(null);
  const [suggestedKey, setSuggestedKey] = useState<OpenClawKeySuggestion | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string>(searchParams.get('task_id') || '');

  const selectedTask = useMemo(
    () => tasks.find((task) => task.id === selectedTaskId) || null,
    [tasks, selectedTaskId],
  );

  const taskOptions: TaskOption[] = useMemo(
    () => tasks.map((task) => ({ label: taskLabel(task), value: task.id, task })),
    [tasks],
  );

  const loadTasks = useCallback(async () => {
    setLoadingTasks(true);
    try {
      const { data } = await api.get<TaskListResponse>('/tasks', {
        params: { all_users: true, page: 1, size: 100, status_filter: 'completed' },
      });
      setTasks(data.items);
      if (!selectedTaskId && data.items.length > 0) {
        setSelectedTaskId(data.items[0].id);
      }
    } catch {
      message.error('加载任务列表失败');
    } finally {
      setLoadingTasks(false);
    }
  }, [message, selectedTaskId]);

  const loadPendingItems = useCallback(async (taskId: string) => {
    if (!taskId) {
      setItems([]);
      return;
    }

    setLoadingItems(true);
    try {
      const { data } = await api.get<ResultListResponse>(`/openclaw/tasks/${taskId}/review-pending`);
      setItems(data.items);
    } catch {
      message.error('加载 OpenClaw 待复核项失败');
      setItems([]);
    } finally {
      setLoadingItems(false);
    }
  }, [message]);

  const loadKeyStatus = useCallback(async () => {
    setLoadingKeyStatus(true);
    try {
      const { data } = await api.get<OpenClawKeyStatus>('/openclaw/admin/key-status');
      setKeyStatus(data);
    } catch {
      message.error('加载 OpenClaw 接入状态失败');
      setKeyStatus(null);
    } finally {
      setLoadingKeyStatus(false);
    }
  }, [message]);

  const generateKeySuggestion = useCallback(async () => {
    setGeneratingKey(true);
    try {
      const { data } = await api.post<OpenClawKeySuggestion>('/openclaw/admin/key-suggestion', {
        prefix: 'oc_',
      });
      setSuggestedKey(data);
      message.success('已生成新的 OpenClaw 建议 key');
    } catch {
      message.error('生成 OpenClaw 建议 key 失败');
    } finally {
      setGeneratingKey(false);
    }
  }, [message]);

  useEffect(() => {
    loadTasks();
    loadKeyStatus();
  }, [loadKeyStatus, loadTasks]);

  useEffect(() => {
    if (!selectedTaskId) return;
    const next = new URLSearchParams(searchParams);
    next.set('task_id', selectedTaskId);
    setSearchParams(next, { replace: true });
    loadPendingItems(selectedTaskId);
  }, [selectedTaskId, searchParams, setSearchParams, loadPendingItems]);

  const columns: ColumnsType<MatchResult> = [
    {
      title: '序号',
      dataIndex: 'index',
      key: 'index',
      width: 70,
      render: (value: number) => value + 1,
    },
    {
      title: '清单名称',
      dataIndex: 'bill_name',
      key: 'bill_name',
      width: 220,
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{record.bill_name}</div>
          {record.bill_description && (
            <div style={{ color: '#64748b', fontSize: 12, marginTop: 4 }}>{record.bill_description}</div>
          )}
        </div>
      ),
    },
    {
      title: '原始结果',
      dataIndex: 'quotas',
      key: 'quotas',
      width: 240,
      render: (quotas: MatchResult['quotas']) => (
        <div style={{ fontSize: 12 }}>
          {(quotas || []).length === 0 && <span style={{ color: '#94a3b8' }}>无匹配</span>}
          {(quotas || []).map((item) => (
            <div key={item.quota_id}>{item.quota_id} {item.name}</div>
          ))}
        </div>
      ),
    },
    {
      title: 'OpenClaw建议',
      dataIndex: 'openclaw_suggested_quotas',
      key: 'openclaw_suggested_quotas',
      width: 240,
      render: (quotas: MatchResult['openclaw_suggested_quotas']) => (
        <div style={{ fontSize: 12 }}>
          {(quotas || []).length === 0 && <span style={{ color: '#94a3b8' }}>无建议</span>}
          {(quotas || []).map((item) => (
            <div key={item.quota_id}>{item.quota_id} {item.name}</div>
          ))}
        </div>
      ),
    },
    {
      title: '建议状态',
      key: 'openclaw_status',
      width: 140,
      render: (_value, record) => {
        const info = REVIEW_STATUS_MAP[record.openclaw_review_status] || {
          color: 'default',
          text: record.openclaw_review_status || '未知',
        };
        return (
          <Space direction="vertical" size={4}>
            <Tag color={info.color} style={{ margin: 0 }}>{info.text}</Tag>
            {record.openclaw_review_actor && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {record.openclaw_review_actor}
              </Typography.Text>
            )}
          </Space>
        );
      },
    },
    {
      title: '审核备注',
      dataIndex: 'openclaw_review_note',
      key: 'openclaw_review_note',
      render: (value: string) => value || <span style={{ color: '#94a3b8' }}>-</span>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      fixed: 'right',
      render: () => (
        <Button
          size="small"
          icon={<EyeOutlined />}
          onClick={() => navigate(`/tasks/${selectedTaskId}/results`)}
          disabled={!selectedTaskId}
        >
          去复核
        </Button>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card
        title="OpenClaw 接入密钥"
        extra={(
          <Space>
            <Button loading={loadingKeyStatus} onClick={loadKeyStatus}>
              刷新状态
            </Button>
            <Button type="primary" loading={generatingKey} onClick={generateKeySuggestion}>
              生成建议 key
            </Button>
          </Space>
        )}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Alert
            type={keyStatus?.configured ? 'success' : 'warning'}
            showIcon
            message={keyStatus?.configured ? '当前运行环境已配置 OpenClaw Key' : '当前运行环境还没有配置 OpenClaw Key'}
            description={keyStatus?.update_hint || '这里只提供查看与生成建议值。真正生效仍然要回到懒猫环境变量里更新，然后重启或重部署服务。'}
          />
          <Descriptions size="small" column={2} bordered>
            <Descriptions.Item label="当前状态">
              <Tag color={keyStatus?.configured ? 'green' : 'orange'}>
                {keyStatus?.configured ? '已配置' : '未配置'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="当前 key 掩码">
              {keyStatus?.masked_key || '-'}
            </Descriptions.Item>
            <Descriptions.Item label="服务账号">
              {keyStatus ? `${keyStatus.service_nickname} / ${keyStatus.service_email}` : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="桥接 OpenAPI">
              {keyStatus?.openapi_url || '/api/openclaw/openapi.json'}
            </Descriptions.Item>
            <Descriptions.Item label="懒猫放行路径">
              {keyStatus?.public_path || '/api/openclaw/'}
            </Descriptions.Item>
            <Descriptions.Item label="同步更新目标">
              {(keyStatus?.sync_targets || []).length > 0 ? keyStatus?.sync_targets.join(' / ') : 'backend / celery-worker'}
            </Descriptions.Item>
          </Descriptions>

          {suggestedKey ? (
            <Card size="small" title="本次建议 key">
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                <Typography.Paragraph
                  copyable={{ text: suggestedKey.suggested_key }}
                  style={{ marginBottom: 0, fontFamily: 'monospace', wordBreak: 'break-all' }}
                >
                  {suggestedKey.suggested_key}
                </Typography.Paragraph>
                <Typography.Text type="secondary">
                  把它写入 `{suggestedKey.env_name}`，并同步更新 {suggestedKey.sync_targets.join(' / ')}。
                </Typography.Text>
                <Typography.Text type="secondary">
                  推荐修改位置：{suggestedKey.manifest_paths.join('、')}
                </Typography.Text>
                <div>
                  {suggestedKey.rollout_steps.map((step, index) => (
                    <div key={step} style={{ color: '#475569', fontSize: 13, marginTop: index === 0 ? 0 : 4 }}>
                      {index + 1}. {step}
                    </div>
                  ))}
                </div>
              </Space>
            </Card>
          ) : null}
        </Space>
      </Card>

      <Card
        title="OpenClaw 待复核"
        extra={(
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              loadTasks();
              loadKeyStatus();
              if (selectedTaskId) loadPendingItems(selectedTaskId);
            }}
          >
            刷新
          </Button>
        )}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="这里只显示 OpenClaw 已提交建议、但尚未人工二次确认的结果。正式纠正仍在结果页执行。"
          />
          <Space wrap>
            <Typography.Text strong>任务</Typography.Text>
            <Select
              showSearch
              style={{ minWidth: 460 }}
              placeholder="选择任务"
              loading={loadingTasks}
              value={selectedTaskId || undefined}
              onChange={(value) => setSelectedTaskId(value)}
              options={taskOptions}
              optionFilterProp="label"
            />
            {selectedTask && (
              <Button onClick={() => navigate(`/tasks/${selectedTask.id}/results`)}>
                打开结果页
              </Button>
            )}
          </Space>
        </Space>
      </Card>

      <Card
        title={selectedTask ? `${selectedTask.name} 的待复核项` : '待复核项'}
        extra={selectedTask ? `共 ${items.length} 条` : undefined}
      >
        {!selectedTask && !loadingTasks ? (
          <Empty description="先选择一个任务" />
        ) : (
          <Table
            rowKey="id"
            dataSource={items}
            columns={columns}
            loading={loadingItems}
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
            scroll={{ x: 1200 }}
            locale={{ emptyText: selectedTask ? '当前任务没有 OpenClaw 待复核项' : '先选择任务' }}
          />
        )}
      </Card>
    </Space>
  );
}
