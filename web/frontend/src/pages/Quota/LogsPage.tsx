/**
 * 额度使用记录页面
 *
 * 展示用户的额度变动历史：注册赠送、任务扣减、购买充值、管理员调整。
 */

import { useState } from 'react';
import { Card, Table, Tag, Statistic, Space, App, Tooltip } from 'antd';
import { ThunderboltOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../services/api';
import type { QuotaBalance, QuotaLogItem, QuotaLogListResponse } from '../../types';
import { useEffect } from 'react';

// 变动类型显示映射
const CHANGE_TYPE_MAP: Record<string, { text: string; color: string }> = {
  register_gift: { text: '注册赠送', color: 'green' },
  task_deduct: { text: '任务扣减', color: 'red' },
  purchase: { text: '购买充值', color: 'blue' },
  admin_adjust: { text: '管理员调整', color: 'orange' },
};

export default function LogsPage() {
  const { message } = App.useApp();

  const [loading, setLoading] = useState(false);
  const [balance, setBalance] = useState<QuotaBalance | null>(null);
  const [logs, setLogs] = useState<QuotaLogItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const pageSize = 20;

  // 加载余额
  useEffect(() => {
    api.get<QuotaBalance>('/quota/balance')
      .then(({ data }) => setBalance(data))
      .catch(() => {});
  }, []);

  // 加载变动记录
  useEffect(() => {
    setLoading(true);
    api.get<QuotaLogListResponse>('/quota/logs', { params: { page, size: pageSize } })
      .then(({ data }) => {
        setLogs(data.items);
        setTotal(data.total);
      })
      .catch(() => message.error('加载记录失败'))
      .finally(() => setLoading(false));
  }, [page, message]);

  const columns = [
    {
      title: '时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 160,
      render: (t: string) => dayjs(t).format('MM-DD HH:mm'),
    },
    {
      title: '类型',
      dataIndex: 'change_type',
      key: 'change_type',
      width: 120,
      render: (type: string) => {
        const info = CHANGE_TYPE_MAP[type] || { text: type, color: 'default' };
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: '变动',
      dataIndex: 'amount',
      key: 'amount',
      width: 100,
      render: (amount: number) => (
        <span style={{ color: amount > 0 ? '#52c41a' : '#ff4d4f', fontWeight: 'bold', fontSize: 15 }}>
          {amount > 0 ? '+' : ''}{amount}
        </span>
      ),
    },
    {
      title: '余额',
      dataIndex: 'balance_after',
      key: 'balance_after',
      width: 100,
      render: (v: number) => `${v} 条`,
    },
    {
      title: '说明',
      dataIndex: 'note',
      key: 'note',
      ellipsis: { showTitle: false },
      render: (note: string) => (
        <Tooltip title={note} placement="topLeft">
          <span>{note || '-'}</span>
        </Tooltip>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {/* 余额卡片 */}
      <Card>
        <Space size="large">
          <Statistic
            title="当前余额"
            value={balance?.balance ?? '-'}
            suffix={balance ? '条' : ''}
            prefix={<ThunderboltOutlined />}
            valueStyle={{
              color: (balance?.balance ?? 0) < 100 ? '#ff4d4f' : '#1677ff',
            }}
          />
          <Statistic title="已使用" value={balance?.total_used ?? '-'} suffix={balance ? '条' : ''} />
          <Statistic title="已购买" value={balance?.total_purchased ?? '-'} suffix={balance ? '条' : ''} />
        </Space>
      </Card>

      {/* 变动记录表格 */}
      <Card title="额度使用记录">
        <Table
          rowKey="id"
          dataSource={logs}
          columns={columns}
          loading={loading}
          pagination={{
            current: page,
            pageSize,
            total,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条记录`,
          }}
          size="middle"
          locale={{ emptyText: '暂无记录' }}
        />
      </Card>
    </Space>
  );
}
