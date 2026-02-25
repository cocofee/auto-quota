/**
 * 管理员 — 所有任务列表
 *
 * 管理员视角的全部用户任务列表，比普通任务列表多一个"用户"列。
 * 调用 GET /api/tasks?all_users=true 获取全部任务。
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Table, Tag, Button, Space, App, Progress } from 'antd';
import { EyeOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../services/api';
import type { TaskInfo, TaskListResponse, TaskStatus } from '../../types';

const STATUS_MAP: Record<TaskStatus, { color: string; text: string }> = {
  pending: { color: 'default', text: '等待中' },
  running: { color: 'processing', text: '匹配中' },
  completed: { color: 'success', text: '已完成' },
  failed: { color: 'error', text: '失败' },
  cancelled: { color: 'warning', text: '已取消' },
};

export default function TaskListAll() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  const loadTasks = async (p = page, s = pageSize) => {
    setLoading(true);
    try {
      const { data } = await api.get<TaskListResponse>('/tasks', {
        params: { page: p, size: s, all_users: true },
      });
      setTasks(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载任务列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadTasks(); }, [page, pageSize]);

  const columns = [
    {
      title: '任务名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
    },
    {
      title: '省份',
      dataIndex: 'province',
      key: 'province',
      width: 120,
    },
    {
      title: '模式',
      dataIndex: 'mode',
      key: 'mode',
      width: 80,
      render: (mode: string) => (
        <Tag color={mode === 'agent' ? 'purple' : 'blue'}>
          {mode === 'agent' ? 'Agent' : '搜索'}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: TaskStatus, record: TaskInfo) => {
        const info = STATUS_MAP[status] || { color: 'default', text: status };
        if (status === 'running') {
          return <Progress percent={record.progress} size="small" />;
        }
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: '统计',
      key: 'stats',
      width: 150,
      render: (_: unknown, record: TaskInfo) => {
        if (!record.stats) return '-';
        const s = record.stats;
        return (
          <Space size={4}>
            <Tag color="green">{s.high_conf}</Tag>
            <Tag color="orange">{s.mid_conf}</Tag>
            <Tag color="red">{s.low_conf}</Tag>
          </Space>
        );
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (t: string) => dayjs(t).format('MM-DD HH:mm'),
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_: unknown, record: TaskInfo) => (
        <Space>
          {record.status === 'completed' && (
            <>
              <Button
                size="small"
                icon={<EyeOutlined />}
                onClick={() => navigate(`/tasks/${record.id}/results`)}
              >
                查看
              </Button>
              <Button
                size="small"
                icon={<DownloadOutlined />}
                onClick={async () => {
                  try {
                    const response = await api.get(`/tasks/${record.id}/export`, { responseType: 'blob' });
                    const url = window.URL.createObjectURL(new Blob([response.data]));
                    const link = document.createElement('a');
                    link.href = url;
                    link.setAttribute('download', `${record.name}_定额匹配结果.xlsx`);
                    document.body.appendChild(link);
                    link.click();
                    link.remove();
                    window.URL.revokeObjectURL(url);
                  } catch {
                    message.error('下载失败');
                  }
                }}
              />
            </>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title="所有任务（管理员）"
      extra={
        <Button icon={<ReloadOutlined />} onClick={() => loadTasks()}>
          刷新
        </Button>
      }
    >
      <Table
        rowKey="id"
        dataSource={tasks}
        columns={columns}
        loading={loading}
        size="middle"
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t) => `共 ${t} 条`,
          onChange: (p, s) => { setPage(p); setPageSize(s); },
        }}
      />
    </Card>
  );
}
