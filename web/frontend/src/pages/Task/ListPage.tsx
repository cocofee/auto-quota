/**
 * 任务列表页（同时用于"我的任务"和"管理员-所有任务"）
 *
 * 通过 adminView 属性区分两种模式：
 * - 默认模式：显示当前用户自己的任务
 * - 管理员模式：显示所有用户的任务（调用 API 时带 all_users=true）
 *
 * 功能：按状态筛选、分页、查看结果、下载Excel、上传反馈、删除任务、进度条、自动刷新
 */

import { useEffect, useState, useCallback, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card, Table, Tag, Button, Space, Progress, Select, Popconfirm, App, Upload, Modal, Tooltip,
} from 'antd';
import {
  EyeOutlined,
  DeleteOutlined,
  ReloadOutlined,
  DownloadOutlined,
  UploadOutlined,
  StopOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../services/api';
import { COLORS } from '../../utils/experience';
import type { TaskInfo, TaskListResponse, TaskStatus } from '../../types';
import { STATUS_MAP, STATUS_OPTIONS } from '../../constants/task';
import { getErrorMessage } from '../../utils/error';

/** 从定额库全名提取简短标签，如 "广东·安装" */
function shortenProvince(full: string): string {
  if (!full) return '-';
  // 提取省份名（2~3个字，遇到"省/市/回族/壮族"等行政后缀就截断）
  const provMatch = full.match(/^(.{2,3}?)(省|市|回族|壮族|维吾尔)/);
  const prov = provMatch ? provMatch[1] : full.substring(0, 2);
  // 从定额库全名中识别工程类别关键词
  const categories: [RegExp, string][] = [
    [/安装/, '安装'],
    [/市政/, '市政'],
    [/房屋建筑|建筑装饰|房屋修|建筑与装饰/, '土建'],
    [/园林绿化/, '园林'],
    [/道路养护/, '养护'],
    [/综合管廊/, '管廊'],
    [/轨道交通/, '轨道'],
    [/环境卫生/, '环卫'],
    [/海绵城市/, '海绵'],
    [/装配式/, '装配式'],
    [/古驿道|传统建筑/, '修缮'],
    [/绿色建筑/, '绿建'],
    [/建设工程|施工消耗/, '综合'],
  ];
  let cat = '';
  for (const [re, label] of categories) {
    if (re.test(full)) { cat = label; break; }
  }
  return cat ? `${prov}·${cat}` : prov;
}

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
  const [totalBills, setTotalBills] = useState(0);
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
      setTotalBills(data.total_bills ?? 0);
    } catch {
      message.error('加载任务列表失败');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, statusFilter, adminView, message]);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  // 用 ref 保存最新的 loadTasks（避免闭包捕获旧引用）
  const loadTasksRef = useRef(loadTasks);
  useEffect(() => {
    loadTasksRef.current = loadTasks;
  }, [loadTasks]);

  // 找出当前正在运行的任务 ID（用于建立 SSE 连接）
  const runningIds = useMemo(
    () => tasks.filter((t) => t.status === 'running' || t.status === 'pending').map((t) => t.id),
    [tasks],
  );
  const runningKey = runningIds.join(',');

  // SSE 实时进度推送（替代旧的 5s 轮询）
  const sseMapRef = useRef<Map<string, EventSource>>(new Map());
  useEffect(() => {
    const sseMap = sseMapRef.current;
    const apiBase = import.meta.env.VITE_API_BASE || '/api';

    // 关闭不再运行的任务的 SSE 连接
    for (const [id, es] of sseMap) {
      if (!runningIds.includes(id)) {
        es.close();
        sseMap.delete(id);
      }
    }

    // 为新的运行中任务建立 SSE 连接
    for (const id of runningIds) {
      if (sseMap.has(id)) continue; // 已连接

      const url = `${apiBase}/tasks/${id}/progress`;
      const es = new EventSource(url, { withCredentials: true });

      es.addEventListener('progress', (event) => {
        try {
          const data = JSON.parse(event.data);
          // 实时更新这条任务的进度（不需要重新请求整个列表）
          setTasks((prev) =>
            prev.map((t) =>
              t.id === id
                ? {
                    ...t,
                    progress: data.progress ?? t.progress,
                    progress_current: data.current_idx ?? t.progress_current,
                    progress_message: data.message ?? t.progress_message,
                    status: data.status ?? t.status,
                    stats: data.stats || t.stats,
                    error_message: data.error ?? t.error_message,
                    started_at: data.started_at ?? t.started_at,
                  }
                : t,
            ),
          );
          // 任务结束：关闭 SSE，重新加载完整列表获取最终状态
          if (data.status === 'completed' || data.status === 'failed' || data.status === 'cancelled') {
            es.close();
            sseMap.delete(id);
            loadTasksRef.current();
          }
        } catch {
          // JSON 解析失败，忽略
        }
      });

      es.onerror = () => {
        es.close();
        sseMap.delete(id);
      };

      sseMap.set(id, es);
    }

    return () => {
      // 组件卸载时关闭所有 SSE 连接
      for (const es of sseMap.values()) {
        es.close();
      }
      sseMap.clear();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningKey]);

  // 兜底轮询：SSE 可能连接失败，每 10 秒刷新一次（仅在有运行中任务时）
  useEffect(() => {
    if (runningIds.length === 0) return;
    const timer = setInterval(() => loadTasksRef.current(), 10000);
    return () => clearInterval(timer);
  }, [runningIds.length]);

  // 每秒刷新一次当前时间，让"已用时间"实时跳动
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (runningIds.length === 0) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [runningIds.length]);

  /** 取消运行中/排队中的任务 */
  const cancelTask = async (taskId: string) => {
    try {
      await api.post(`/tasks/${taskId}/cancel`);
      message.success('任务已取消');
      loadTasks();
    } catch (err: unknown) {
      message.error(getErrorMessage(err, '取消失败'));
    }
  };

  /** 删除任务 */
  const deleteTask = async (taskId: string) => {
    try {
      await api.delete(`/tasks/${taskId}`);
      message.success('任务已删除');
      loadTasks();
    } catch (err: unknown) {
      message.error(getErrorMessage(err, '删除失败'));
    }
  };

  /** 下载Excel结果 */
  const downloadExcel = async (taskId: string, filename: string) => {
    try {
      const response = await api.get(`/tasks/${taskId}/export?materials=true`, {
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

  /** 上传反馈（纠正后的Excel）——用 Set 追踪正在上传的 taskId，避免影响其他行 */
  const [uploadingTaskIds, setUploadingTaskIds] = useState<Set<string>>(new Set());
  const uploadFeedback = async (taskId: string, file: File) => {
    setUploadingTaskIds((prev) => new Set(prev).add(taskId));
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
      message.error(getErrorMessage(err, '反馈上传失败'));
    } finally {
      setUploadingTaskIds((prev) => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    }
  };

  // 表格列定义
  const columns = [
    {
      title: '#',
      key: 'index',
      width: 50,
      render: (_: unknown, __: TaskInfo, index: number) => (
        <span style={{ color: '#999' }}>{(page - 1) * pageSize + index + 1}</span>
      ),
    },
    // 类型标签列（彩色胶囊，文档06章）
    {
      title: '类型',
      key: 'task_type',
      width: 80,
      render: () => (
        <Tag style={{
          color: '#16a34a',
          borderColor: '#16a34a',
          background: '#16a34a10',
          fontWeight: 500,
          margin: 0,
        }}>
          套定额
        </Tag>
      ),
    },
    {
      title: '任务名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      ellipsis: { showTitle: false },
      render: (name: string, record: TaskInfo) => (
        <Tooltip title={name || record.original_filename} placement="topLeft">
          <span>{name || record.original_filename || '未命名'}</span>
        </Tooltip>
      ),
    },
    // 管理员视图：显示任务所属用户
    ...(adminView ? [{
      title: '用户',
      dataIndex: 'username',
      key: 'username',
      width: 100,
      ellipsis: true,
      render: (username: string) => username || '-',
    }] : []),
    {
      title: '定额库',
      dataIndex: 'province',
      key: 'province',
      width: 100,
      render: (province: string) => (
        <Tooltip title={province}>
          <Tag style={{ margin: 0 }}>{shortenProvince(province)}</Tag>
        </Tooltip>
      ),
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
          return (
            <Space direction="vertical" size={0} style={{ width: '100%' }}>
              <Tag color={info.color}>{info.text}</Tag>
              <Progress
                percent={record.progress}
                size="small"
                style={{ marginTop: 4 }}
              />
              {record.progress_message && (
                <span style={{ fontSize: 11, color: '#999' }}>{record.progress_message}</span>
              )}
            </Space>
          );
        }
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: '清单',
      key: 'bill_total',
      width: 70,
      render: (_: unknown, record: TaskInfo) => {
        // 优先从 stats.total 取（完成后有）；运行中从 progress_message 提取（格式"匹配中 3/100"）
        if (record.stats?.total) return `${record.stats.total}条`;
        const msg = record.progress_message || '';
        const m = msg.match(/\/(\d+)/);
        if (m) return `${m[1]}条`;
        return '-';
      },
    },
    {
      title: '用时',
      key: 'elapsed',
      width: 80,
      render: (_: unknown, record: TaskInfo) => {
        // 已完成：用 stats.elapsed（后端返回的总耗时秒数）
        if (record.stats?.elapsed) {
          const sec = Math.round(record.stats.elapsed);
          const min = Math.floor(sec / 60);
          const s = sec % 60;
          return min > 0 ? `${min}分${s}秒` : `${s}秒`;
        }
        // 运行中：用 now - started_at 实时计算
        if (record.status === 'running' && record.started_at) {
          const elapsed = Math.floor((now - new Date(record.started_at).getTime()) / 1000);
          const min = Math.floor(elapsed / 60);
          const sec = elapsed % 60;
          return min > 0 ? `${min}分${sec}秒` : `${sec}秒`;
        }
        return '-';
      },
    },
    {
      title: (
        <Tooltip title="高=推荐直接用 / 中=需复核 / 低=大概率要改">
          <span>统计 <span style={{ fontSize: 10, color: '#999' }}>高/中/低</span></span>
        </Tooltip>
      ),
      key: 'stats',
      width: 180,
      render: (_: unknown, record: TaskInfo) => {
        // 进行中的任务：显示实时进度
        if ((record.status === 'running' || record.status === 'pending') && record.progress > 0) {
          return (
            <span style={{ fontSize: 12, color: '#1677ff' }}>
              匹配中 {record.progress}%
            </span>
          );
        }
        if (!record.stats || !record.stats.total) return '-';
        const { total: t, high_conf, mid_conf, low_conf } = record.stats;
        const gPct = t > 0 ? (high_conf / t) * 100 : 0;
        const yPct = t > 0 ? (mid_conf / t) * 100 : 0;
        const rPct = t > 0 ? (low_conf / t) * 100 : 0;
        return (
          <Tooltip title={`推荐: ${high_conf}条 / 参考: ${mid_conf}条 / 待审: ${low_conf}条`}>
            <div style={{ minWidth: 120 }}>
              {/* 比例条 */}
              <div style={{ display: 'flex', height: 6, borderRadius: 3, overflow: 'hidden', marginBottom: 4 }}>
                {gPct > 0 && <div style={{ width: `${gPct}%`, background: COLORS.greenSolid }} />}
                {yPct > 0 && <div style={{ width: `${yPct}%`, background: COLORS.yellowSolid }} />}
                {rPct > 0 && <div style={{ width: `${rPct}%`, background: COLORS.redSolid }} />}
              </div>
              {/* 数字摘要：带标签 */}
              <span style={{ fontSize: 12 }}>
                <span style={{ color: COLORS.greenSolid }}>高{high_conf}</span>
                {' '}
                <span style={{ color: COLORS.yellowSolid }}>中{mid_conf}</span>
                {' '}
                <span style={{ color: COLORS.redSolid }}>低{low_conf}</span>
              </span>
            </div>
          </Tooltip>
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
                    loading={uploadingTaskIds.has(record.id)}
                  >
                    反馈
                  </Button>
                </Upload>
              )}
            </>
          )}
          {(record.status === 'running' || record.status === 'pending') && (
            <Popconfirm
              title="确定取消此任务？"
              onConfirm={() => cancelTask(record.id)}
            >
              <Button type="link" size="small" danger icon={<StopOutlined />}>
                取消
              </Button>
            </Popconfirm>
          )}
          <Popconfirm
            title="确定删除此任务？"
            description="关联的匹配结果也会一起删除"
            onConfirm={() => deleteTask(record.id)}
          >
            <Button type="link" size="small" danger icon={<DeleteOutlined />}>
              删除
            </Button>
          </Popconfirm>
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
          showTotal: (t) => `共 ${t} 个任务，${totalBills} 条清单`,
          onChange: (p, s) => {
            setPage(p);
            setPageSize(s);
          },
        }}
        locale={{ emptyText: '暂无任务' }}
        footer={() => total > 0 ? (
          <div style={{ textAlign: 'right', color: '#666', fontSize: 13 }}>
            合计：<b>{total}</b> 个任务，<b>{totalBills}</b> 条清单
          </div>
        ) : null}
      />
    </Card>
  );
}
