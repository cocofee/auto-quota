/**
 * 首页工作台（v0.3.0 改版）
 *
 * 布局：统计卡片(4列) → 四功能卡片(4列) → 最近任务表格(带类型筛选)
 * 按 JARVIS v0.3.0 前端改版方案 实现
 */

import { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Row, Col, Statistic, Table, Tag, Button, App, Tooltip } from 'antd';
import {
  FileTextOutlined,
  CheckCircleOutlined,
  ThunderboltOutlined,
  AimOutlined,
  GoldOutlined,
  BarChartOutlined,
  RightOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../services/api';
import { useAuthStore } from '../../stores/auth';
import { COLORS } from '../../utils/experience';
import type { TaskInfo, TaskListResponse, TaskStatus, QuotaBalance } from '../../types';
import { STATUS_MAP } from '../../constants/task';

// 四功能色值（文档07章）
const TOOL_COLORS = {
  bill:     { primary: '#1a56db', light: '#eff6ff', gradient: 'linear-gradient(135deg, #3b82f6, #60a5fa)' },
  quota:    { primary: '#16a34a', light: '#f0fdf4', gradient: 'linear-gradient(135deg, #16a34a, #4ade80)' },
  material: { primary: '#ea580c', light: '#fff7ed', gradient: 'linear-gradient(135deg, #ea580c, #fb923c)' },
  backfill: { primary: '#7c3aed', light: '#f5f3ff', gradient: 'linear-gradient(135deg, #7c3aed, #a78bfa)' },
};

// 任务类型标签映射（用于最近任务表格的"类型"列）
const TASK_TYPE_MAP: Record<string, { color: string; text: string }> = {
  quota:    { color: TOOL_COLORS.quota.primary, text: '套定额' },
  bill:     { color: TOOL_COLORS.bill.primary, text: '编清单' },
  material: { color: TOOL_COLORS.material.primary, text: '填主材' },
  backfill: { color: TOOL_COLORS.backfill.primary, text: '填价' },
};

// 从任务信息推断类型（目前大部分是套定额任务）
function inferTaskType(task: TaskInfo): string {
  // 未来后端会返回 task_type 字段，目前先按路径/模式推断
  if (task.mode === 'search' || task.mode === 'agent') return 'quota';
  return 'quota';
}

export default function DashboardPage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { user } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;

  const [loading, setLoading] = useState(false);
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [total, setTotal] = useState(0);
  const [completedTotal, setCompletedTotal] = useState(0);
  const [quotaBalance, setQuotaBalance] = useState<number | null>(null);
  const [statsTasks, setStatsTasks] = useState<TaskInfo[]>([]); // 更多已完成任务（用于准确率计算）

  // 最近任务类型筛选
  const [typeFilter, setTypeFilter] = useState<string>('all');

  const loadRecentTasks = useCallback(async () => {
    setLoading(true);
    try {
      // 最近10个任务（用于表格展示）
      const { data } = await api.get<TaskListResponse>('/tasks', {
        params: { page: 1, size: 10 },
      });
      setTasks(data.items);
      setTotal(data.total);
      // 本月完成：只统计当月创建的已完成任务
      const monthStart = dayjs().startOf('month').format('YYYY-MM-DD');
      const completedRes = await api.get<TaskListResponse>('/tasks', {
        params: { page: 1, size: 1, status_filter: 'completed', created_after: monthStart },
      });
      setCompletedTotal(completedRes.data.total);
      // 加载更多已完成任务用于计算准确率（最多100个）
      const statsRes = await api.get<TaskListResponse>('/tasks', {
        params: { page: 1, size: 100, status_filter: 'completed' },
      });
      setStatsTasks(statsRes.data.items);
    } catch {
      message.error('加载任务列表失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => {
    let cancelled = false;
    loadRecentTasks();
    api.get<QuotaBalance>('/quota/balance')
      .then(({ data }) => { if (!cancelled) setQuotaBalance(data.balance); })
      .catch(() => { if (!cancelled) setQuotaBalance(null); });
    return () => { cancelled = true; };
  }, [loadRecentTasks]);

  // 计算绿灯率（基于所有已完成任务的加权平均，样本量最多100个）
  const completedWithStats = statsTasks.filter(t => t.stats?.total);
  const avgStats = completedWithStats.reduce(
    (acc, t) => {
      const s = t.stats!;
      acc.total += s.total;
      acc.high += s.high_conf || 0;
      acc.mid += s.mid_conf || 0;
      acc.low += s.low_conf || 0;
      return acc;
    },
    { total: 0, high: 0, mid: 0, low: 0 },
  );
  const avgAccuracy = avgStats.total > 0
    ? Math.round((avgStats.high / avgStats.total) * 100) : 0;
  const greenPct = avgStats.total > 0 ? Math.round((avgStats.high / avgStats.total) * 100) : 0;
  const yellowPct = avgStats.total > 0 ? Math.round((avgStats.mid / avgStats.total) * 100) : 0;
  const redPct = avgStats.total > 0 ? Math.round((avgStats.low / avgStats.total) * 100) : 0;

  // 表格列
  const columns = [
    // 类型列（彩色胶囊标签）
    {
      title: '类型',
      key: 'task_type',
      width: 90,
      render: (_: unknown, record: TaskInfo) => {
        const type = inferTaskType(record);
        const info = TASK_TYPE_MAP[type] || { color: '#999', text: '未知' };
        return (
          <Tag style={{
            color: info.color,
            borderColor: info.color,
            background: `${info.color}10`,
            fontWeight: 500,
            margin: 0,
          }}>
            {info.text}
          </Tag>
        );
      },
    },
    {
      title: '任务名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: { showTitle: false },
      render: (name: string, record: TaskInfo) => (
        <Tooltip title={name || record.original_filename} placement="topLeft">
          <a onClick={() => {
            if (record.status === 'completed') navigate(`/tasks/${record.id}/results`);
          }}>
            {name || record.original_filename || '未命名'}
          </a>
        </Tooltip>
      ),
    },
    {
      title: '省份/定额',
      dataIndex: 'province',
      key: 'province',
      width: 180,
      ellipsis: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 90,
      render: (status: TaskStatus) => {
        const info = STATUS_MAP[status] || { color: 'default', text: status };
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    // 准确率列（进度条+百分比）
    {
      title: '准确率',
      key: 'accuracy',
      width: 120,
      render: (_: unknown, record: TaskInfo) => {
        if ((record.status === 'running' || record.status === 'pending') && record.progress > 0) {
          return <span style={{ color: '#1677ff', fontSize: 12 }}>{record.progress}%</span>;
        }
        if (!record.stats || !record.stats.total) return <span style={{ color: '#ccc' }}>—</span>;
        const { total: t, high_conf = 0, mid_conf = 0, low_conf = 0 } = record.stats;
        const rate = t > 0 ? Math.round((high_conf / t) * 100) : 0;
        const gPct = t > 0 ? (high_conf / t) * 100 : 0;
        const yPct = t > 0 ? (mid_conf / t) * 100 : 0;
        const rPct = t > 0 ? (low_conf / t) * 100 : 0;
        const barColor = rate >= 80 ? COLORS.greenSolid : rate >= 40 ? COLORS.yellowSolid : COLORS.redSolid;
        return (
          <Tooltip title={`高${high_conf} / 中${mid_conf} / 低${low_conf}`}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 80, height: 6, borderRadius: 3, overflow: 'hidden', display: 'flex', background: '#f0f0f0' }}>
                {gPct > 0 && <div style={{ width: `${gPct}%`, background: COLORS.greenSolid }} />}
                {yPct > 0 && <div style={{ width: `${yPct}%`, background: COLORS.yellowSolid }} />}
                {rPct > 0 && <div style={{ width: `${rPct}%`, background: COLORS.redSolid }} />}
              </div>
              <span style={{ fontSize: 12, fontWeight: 600, color: barColor }}>{rate}%</span>
            </div>
          </Tooltip>
        );
      },
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 80,
      render: (t: string) => {
        const d = dayjs(t);
        const today = dayjs();
        if (d.isSame(today, 'day')) return d.format('HH:mm');
        if (d.isSame(today.subtract(1, 'day'), 'day')) return '昨天';
        return d.format('M月D日');
      },
    },
  ];

  // 按类型筛选（目前都是quota，未来扩展）
  const filteredTasks = typeFilter === 'all'
    ? tasks
    : tasks.filter(t => inferTaskType(t) === typeFilter);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* ========== 统计卡片（4列） ========== */}
      <Row gutter={16}>
        <Col xs={12} sm={6}>
          <Card hoverable onClick={() => navigate('/tasks')}
            styles={{ body: { padding: '20px 24px' } }}
          >
            <Statistic
              title="总任务数"
              value={total}
              valueStyle={{ fontSize: 32, fontWeight: 700 }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable onClick={() => navigate('/tasks?status=completed')}
            styles={{ body: { padding: '20px 24px' } }}
          >
            <Statistic
              title="本月完成"
              value={completedTotal}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ fontSize: 32, fontWeight: 700 }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable styles={{ body: { padding: '20px 24px' } }}>
            <Statistic
              title={<Tooltip title={`基于${completedWithStats.length}个已完成任务，绿灯(≥85%)占比`}>绿灯率</Tooltip>}
              value={avgAccuracy}
              suffix="%"
              valueStyle={{
                fontSize: 32,
                fontWeight: 700,
                color: avgAccuracy >= 80 ? '#16a34a' : avgAccuracy >= 40 ? '#d97706' : '#dc2626',
              }}
            />
            {avgStats.total > 0 && (
              <div style={{ marginTop: 4, fontSize: 12 }}>
                <span style={{ color: COLORS.greenSolid }}>● {greenPct}%</span>
                {' '}
                <span style={{ color: COLORS.yellowSolid }}>● {yellowPct}%</span>
                {' '}
                <span style={{ color: COLORS.redSolid }}>● {redPct}%</span>
              </div>
            )}
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card hoverable onClick={() => navigate(isAdmin ? '/admin/billing' : '/quota/logs')}
            styles={{ body: { padding: '20px 24px' } }}
          >
            <Statistic
              title="剩余额度"
              value={quotaBalance ?? '-'}
              suffix={quotaBalance !== null ? '条' : ''}
              prefix={<ThunderboltOutlined />}
              valueStyle={{
                fontSize: 32,
                fontWeight: 700,
                color: quotaBalance !== null && quotaBalance < 100 ? '#dc2626' : undefined,
              }}
            />
          </Card>
        </Col>
      </Row>

      {/* ========== 四功能卡片 ========== */}
      <div>
        <h3 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>智能工具</h3>
        <Row gutter={16}>
          {/* 智能编清单 */}
          <Col xs={12} sm={6}>
            <Card
              hoverable
              onClick={() => navigate('/tools/bill-compiler')}
              styles={{ body: { padding: '20px' } }}
              style={{ borderTop: `4px solid ${TOOL_COLORS.bill.primary}`, borderRadius: 12 }}
            >
              <div style={{
                width: 48, height: 48, borderRadius: 12,
                background: TOOL_COLORS.bill.light,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginBottom: 12,
              }}>
                <FileTextOutlined style={{ fontSize: 24, color: TOOL_COLORS.bill.primary }} />
              </div>
              <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 4 }}>智能编清单</div>
              <div style={{ fontSize: 13, color: '#888', marginBottom: 12, lineHeight: 1.5 }}>
                输入工程量描述，自动生成标准清单编码和项目特征
              </div>
              <div style={{
                fontSize: 12, color: '#666', background: '#f5f5f5',
                padding: '4px 10px', borderRadius: 12, display: 'inline-block', marginBottom: 12,
              }}>
                工程量 → <b>标准清单</b>
              </div>
              <div>
                <Button type="link" style={{ color: TOOL_COLORS.bill.primary, padding: 0, fontWeight: 500 }}>
                  开始编制 <RightOutlined style={{ fontSize: 10 }} />
                </Button>
              </div>
            </Card>
          </Col>

          {/* 智能套定额 */}
          <Col xs={12} sm={6}>
            <Card
              hoverable
              onClick={() => navigate('/tasks/create')}
              styles={{ body: { padding: '20px' } }}
              style={{ borderTop: `4px solid ${TOOL_COLORS.quota.primary}`, borderRadius: 12 }}
            >
              <div style={{
                width: 48, height: 48, borderRadius: 12,
                background: TOOL_COLORS.quota.light,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginBottom: 12,
              }}>
                <AimOutlined style={{ fontSize: 24, color: TOOL_COLORS.quota.primary }} />
              </div>
              <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 4 }}>智能套定额</div>
              <div style={{ fontSize: 13, color: '#888', marginBottom: 12, lineHeight: 1.5 }}>
                上传清单文件，自动匹配省份定额库中的最佳子目
              </div>
              <div style={{
                fontSize: 12, color: '#666', background: '#f5f5f5',
                padding: '4px 10px', borderRadius: 12, display: 'inline-block', marginBottom: 12,
              }}>
                清单文件 → <b>定额匹配</b>
              </div>
              <div>
                <Button type="link" style={{ color: TOOL_COLORS.quota.primary, padding: 0, fontWeight: 500 }}>
                  上传清单 <RightOutlined style={{ fontSize: 10 }} />
                </Button>
              </div>
            </Card>
          </Col>

          {/* 智能填主材 */}
          <Col xs={12} sm={6}>
            <Card
              hoverable
              onClick={() => navigate('/tools/material-price')}
              styles={{ body: { padding: '20px' } }}
              style={{ borderTop: `4px solid ${TOOL_COLORS.material.primary}`, borderRadius: 12 }}
            >
              <div style={{
                width: 48, height: 48, borderRadius: 12,
                background: TOOL_COLORS.material.light,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginBottom: 12,
              }}>
                <GoldOutlined style={{ fontSize: 24, color: TOOL_COLORS.material.primary }} />
              </div>
              <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 4 }}>智能填主材</div>
              <div style={{ fontSize: 13, color: '#888', marginBottom: 12, lineHeight: 1.5 }}>
                根据定额子目，自动查询信息价和市场价填入主材价格
              </div>
              <div style={{
                fontSize: 12, color: '#666', background: '#f5f5f5',
                padding: '4px 10px', borderRadius: 12, display: 'inline-block', marginBottom: 12,
              }}>
                定额子目 → <b>主材价格</b>
              </div>
              <div>
                <Button type="link" style={{ color: TOOL_COLORS.material.primary, padding: 0, fontWeight: 500 }}>
                  开始填价 <RightOutlined style={{ fontSize: 10 }} />
                </Button>
              </div>
            </Card>
          </Col>

          {/* 智能填价 */}
          <Col xs={12} sm={6}>
            <Card
              hoverable
              onClick={() => navigate('/tools/price-backfill')}
              styles={{ body: { padding: '20px' } }}
              style={{ borderTop: `4px solid ${TOOL_COLORS.backfill.primary}`, borderRadius: 12 }}
            >
              <div style={{
                width: 48, height: 48, borderRadius: 12,
                background: TOOL_COLORS.backfill.light,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                marginBottom: 12,
              }}>
                <BarChartOutlined style={{ fontSize: 24, color: TOOL_COLORS.backfill.primary }} />
              </div>
              <div style={{ fontWeight: 600, fontSize: 16, marginBottom: 4 }}>智能填价</div>
              <div style={{ fontSize: 13, color: '#888', marginBottom: 12, lineHeight: 1.5 }}>
                将广联达导出的组价数据，智能回填到甲方清单格式中
              </div>
              <div style={{
                fontSize: 12, color: '#666', background: '#f5f5f5',
                padding: '4px 10px', borderRadius: 12, display: 'inline-block', marginBottom: 12,
              }}>
                广联达组价 → <b>甲方清单</b>
              </div>
              <div>
                <Button type="link" style={{ color: TOOL_COLORS.backfill.primary, padding: 0, fontWeight: 500 }}>
                  开始回填 <RightOutlined style={{ fontSize: 10 }} />
                </Button>
              </div>
            </Card>
          </Col>
        </Row>
      </div>

      {/* ========== 最近任务 ========== */}
      <Card
        title={<span style={{ fontWeight: 600 }}>最近任务</span>}
        extra={
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            {[
              { key: 'all', label: '全部' },
              { key: 'bill', label: '编清单', color: TOOL_COLORS.bill.primary },
              { key: 'quota', label: '套定额', color: TOOL_COLORS.quota.primary },
              { key: 'material', label: '填主材', color: TOOL_COLORS.material.primary },
              { key: 'backfill', label: '填价', color: TOOL_COLORS.backfill.primary },
            ].map(({ key, label, color }) => (
              <Button
                key={key}
                type={typeFilter === key ? 'primary' : 'text'}
                size="small"
                style={{
                  fontWeight: typeFilter === key ? 600 : 400,
                  color: typeFilter === key ? undefined : (color || '#666'),
                }}
                onClick={() => setTypeFilter(key)}
              >
                {label}
              </Button>
            ))}
          </div>
        }
      >
        <Table
          rowKey="id"
          dataSource={filteredTasks}
          columns={columns}
          loading={loading}
          pagination={false}
          size="middle"
          locale={{ emptyText: '暂无任务' }}
          onRow={(record: TaskInfo) => ({
            onClick: () => {
              if (record.status === 'completed') navigate(`/tasks/${record.id}/results`);
            },
            style: { cursor: record.status === 'completed' ? 'pointer' : 'default' },
          })}
        />
      </Card>
    </div>
  );
}
