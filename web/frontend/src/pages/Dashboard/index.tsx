/**
 * 首页看板
 *
 * 客户：简洁卡片（总任务、已完成）+ 最近任务 + 新建按钮
 * 管理员：额外显示 平均置信度、进行中任务数、模式列
 */

import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Row, Col, Statistic, Table, Tag, Button, Space, App } from 'antd';
import {
  FileTextOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  ExperimentOutlined,
  PlusOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../services/api';
import { useAuthStore } from '../../stores/auth';
import type { TaskInfo, TaskListResponse, TaskStatus } from '../../types';
import { STATUS_MAP } from '../../constants/task';

export default function DashboardPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { user } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;

  const [loading, setLoading] = useState(false);
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [total, setTotal] = useState(0);

  const loadRecentTasks = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get<TaskListResponse>('/tasks', {
        params: { page: 1, size: 10 },
      });
      setTasks(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载任务列表失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    loadRecentTasks();
  }, [loadRecentTasks]);

  const completedTasks = tasks.filter((t) => t.status === 'completed');
  const runningTasks = tasks.filter((t) => t.status === 'running' || t.status === 'pending');

  // 平均置信度（仅管理员关心）
  const tasksWithStats = completedTasks.filter((t) => t.stats?.total);
  const avgConfidence = tasksWithStats.length > 0
    ? Math.round(
        tasksWithStats.reduce((sum, t) => {
          const stats = t.stats!;
          const total = stats.total || 1;
          return sum + ((stats.high_conf * 95 + stats.mid_conf * 77 + stats.low_conf * 50) / total);
        }, 0) / tasksWithStats.length,
      )
    : 0;

  // 客户表格列（简化）
  const baseColumns = [
    {
      title: '任务名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
      render: (name: string, record: TaskInfo) => (
        <a onClick={() => {
          if (record.status === 'completed') {
            navigate(`/tasks/${record.id}/results`);
          }
        }}>
          {name}
        </a>
      ),
    },
    {
      title: '省份',
      dataIndex: 'province',
      key: 'province',
      width: 120,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: TaskStatus) => {
        const info = STATUS_MAP[status] || { color: 'default', text: status };
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (t: string) => dayjs(t).format('MM-DD HH:mm'),
    },
  ];

  // 管理员额外的列
  const adminColumns = [
    ...baseColumns.slice(0, 2),
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
    ...baseColumns.slice(2),
    {
      title: '匹配率',
      key: 'match_rate',
      width: 80,
      render: (_: unknown, record: TaskInfo) => {
        if (!record.stats || !record.stats.total) return '-';
        const rate = Math.round(((record.stats.matched ?? 0) / record.stats.total) * 100);
        return `${rate}%`;
      },
    },
  ];

  const columns = isAdmin ? adminColumns : baseColumns;

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {/* 统计卡片 */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={isAdmin ? 6 : 12}>
          <Card hoverable onClick={() => navigate('/tasks')}>
            <Statistic
              title="总任务数"
              value={total}
              prefix={<FileTextOutlined />}
            />
          </Card>
        </Col>
        <Col xs={12} sm={isAdmin ? 6 : 12}>
          <Card>
            <Statistic
              title="已完成"
              value={completedTasks.length}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: '#52c41a' }}
            />
          </Card>
        </Col>
        {/* 以下卡片仅管理员可见 */}
        {isAdmin && (
          <>
            <Col xs={12} sm={6}>
              <Card>
                <Statistic
                  title="进行中"
                  value={runningTasks.length}
                  prefix={<ClockCircleOutlined />}
                  valueStyle={{ color: '#1677ff' }}
                />
              </Card>
            </Col>
            <Col xs={12} sm={6}>
              <Card>
                <Statistic
                  title="平均置信度"
                  value={avgConfidence}
                  suffix="%"
                  prefix={<ExperimentOutlined />}
                  valueStyle={{ color: avgConfidence >= 85 ? '#52c41a' : avgConfidence >= 70 ? '#faad14' : '#ff4d4f' }}
                />
              </Card>
            </Col>
          </>
        )}
      </Row>

      {/* 最近任务 */}
      <Card
        title="最近任务"
        extra={
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={() => navigate('/tasks/create')}
          >
            新建任务
          </Button>
        }
      >
        <Table
          rowKey="id"
          dataSource={tasks}
          columns={columns}
          loading={loading}
          pagination={false}
          size="middle"
          locale={{ emptyText: '暂无任务，点击右上角新建' }}
        />
      </Card>
    </Space>
  );
}
