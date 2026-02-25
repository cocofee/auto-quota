/**
 * 任务列表页（同时用于"我的任务"和"管理员-所有任务"）
 *
 * 通过 adminView 属性区分两种模式：
 * - 默认模式：显示当前用户自己的任务
 * - 管理员模式：显示所有用户的任务（调用 API 时带 all_users=true）
 *
 * 功能：按状态筛选、分页、查看结果、下载Excel、上传反馈、删除任务、进度条、自动刷新
 */

import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card, Table, Tag, Button, Space, Progress, Select, Popconfirm, App, Upload, Modal,
} from 'antd';
import {
  EyeOutlined,
  DeleteOutlined,
  ReloadOutlined,
  DownloadOutlined,
  UploadOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../services/api';
import type { TaskInfo, TaskListResponse, TaskStatus } from '../../types';
import { STATUS_MAP, STATUS_OPTIONS } from '../../constants/task';

/** 组件属性 */
interface TaskListPageProps {
  /** 管理员视图：显示所有用户的任务 */
  adminView?: boolean;
}

export default function TaskListPage({ adminView = false }: TaskListPageProps) {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [statusFilter, setStatusFilter] = useState('');

  // 加载任务列表
  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { page, size: pageSize };
      if (statusFilter) params.status_filter = statusFilter;
      // 管理员模式：请求所有用户的任务
      if (adminView) params.all_users = true;

      const { data } = await api.get<TaskListResponse>('/tasks', { params });
      setTasks(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载任务列表失败');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, statusFilter, adminView, message]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  // 定时刷新（有进行中的任务时每5秒刷新一次）
  useEffect(() => {
    const hasRunning = tasks.some((t) => t.status === 'running' || t.status === 'pending');
    if (!hasRunning) return;

    const timer = setInterval(loadTasks, 5000);
    return () => clearInterval(timer);
  }, [tasks, loadTasks]);

  /** 删除任务 */
  const deleteTask = async (taskId: string) => {
    try {
      await api.delete(`/tasks/${taskId}`);
      message.success('任务已删除');
      loadTasks();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      message.error(detail || '删除失败');
    }
  };

  /** 下载Excel结果 */
  const downloadExcel = async (taskId: string, filename: string) => {
    try {
      const response = await api.get(`/tasks/${taskId}/export`, {
        responseType: 'blob',
      });
      // 创建下载链接
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `${filename}_定额匹配结果.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败');
    }
  };

  /** 上传反馈（纠正后的Excel） */
  const [feedbackUploading, setFeedbackUploading] = useState(false);
  const uploadFeedback = async (taskId: string, file: File) => {
    setFeedbackUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const { data } = await api.post(`/tasks/${taskId}/feedback/upload`, formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 120000, // 学习过程可能较慢，设2分钟超时
      });
      const stats = data.stats || {};
      Modal.success({
        title: '反馈上传成功',
        content: `已从Excel中识别 ${stats.total || 0} 条清单，学习了 ${stats.learned || 0} 条经验。`,
      });
      loadTasks(); // 刷新列表，更新反馈状态
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      message.error(detail || '反馈上传失败');
    } finally {
      setFeedbackUploading(false);
    }
  };

  // 表格列定义
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
      width: 100,
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
        // 进行中的任务额外显示进度条
        if (status === 'running') {
          return (
            <Space direction="vertical" size={0} style={{ width: '100%' }}>
              <Tag color={info.color}>{info.text}</Tag>
              <Progress
                percent={record.progress}
                size="small"
                showInfo={false}
                style={{ marginTop: 4 }}
              />
            </Space>
          );
        }
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: '统计',
      key: 'stats',
      width: 200,
      render: (_: unknown, record: TaskInfo) => {
        if (!record.stats || !record.stats.total) return '-';
        const { total: t, high_conf, mid_conf, low_conf } = record.stats;
        return (
          <Space size={4}>
            <Tag color="green">{high_conf}高</Tag>
            <Tag color="orange">{mid_conf}中</Tag>
            <Tag color="red">{low_conf}低</Tag>
            <span style={{ color: '#999', fontSize: 12 }}>/ {t}条</span>
          </Space>
        );
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (t: string) => dayjs(t).format('MM-DD HH:mm:ss'),
    },
    {
      title: '操作',
      key: 'action',
      width: 160,
      render: (_: unknown, record: TaskInfo) => (
        <Space size={4}>
          {record.status === 'completed' && (
            <>
              <Button
                type="link"
                size="small"
                icon={<EyeOutlined />}
                onClick={() => navigate(`/tasks/${record.id}/results`)}
              >
                结果
              </Button>
              <Button
                type="link"
                size="small"
                icon={<DownloadOutlined />}
                onClick={() => downloadExcel(record.id, record.name)}
              >
                下载
              </Button>
              {/* 上传反馈按钮：已上传过的显示"已反馈"（禁用） */}
              {record.feedback_path ? (
                <Button type="link" size="small" disabled>
                  已反馈
                </Button>
              ) : (
                <Upload
                  accept=".xlsx"
                  showUploadList={false}
                  beforeUpload={(file) => {
                    uploadFeedback(record.id, file as unknown as File);
                    return false; // 阻止默认上传，用自定义逻辑
                  }}
                >
                  <Button
                    type="link"
                    size="small"
                    icon={<UploadOutlined />}
                    loading={feedbackUploading}
                  >
                    反馈
                  </Button>
                </Upload>
              )}
            </>
          )}
          {record.status !== 'running' && (
            <Popconfirm
              title="确定删除此任务？"
              description="关联的匹配结果也会一起删除"
              onConfirm={() => deleteTask(record.id)}
            >
              <Button type="link" size="small" danger icon={<DeleteOutlined />}>
                删除
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Card
      title={adminView ? '所有任务（管理员）' : '任务列表'}
      extra={
        <Space>
          <Select
            value={statusFilter}
            options={STATUS_OPTIONS}
            style={{ width: 120 }}
            onChange={(val) => {
              setStatusFilter(val);
              setPage(1);
            }}
          />
          <Button icon={<ReloadOutlined />} onClick={loadTasks}>
            刷新
          </Button>
        </Space>
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
          onChange: (p, s) => {
            setPage(p);
            setPageSize(s);
          },
        }}
        locale={{ emptyText: '暂无任务' }}
      />
    </Card>
  );
}
