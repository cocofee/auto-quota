/**
 * 管理员额度管理页面
 *
 * 两个Tab：
 * 1. 用户额度 — 查看所有用户的额度情况，支持搜索和调整
 * 2. 订单列表 — 查看所有购买订单，支持按状态筛选
 */

import { useEffect, useState, useCallback } from 'react';
import {
  Card, Tabs, Table, Tag, Button, Input, Space, Statistic, Row, Col,
  Modal, InputNumber, Form, App,
} from 'antd';
import {
  WalletOutlined,
  ShoppingCartOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../services/api';
import type { OrderInfo, OrderListResponse } from '../../types';

// 管理员API返回类型（不需要全局导出）
interface AdminUserQuota {
  user_id: string;
  email: string;
  nickname: string;
  quota_balance: number;
  total_used: number;
  total_purchased: number;
}

interface AdminUserQuotaList {
  items: AdminUserQuota[];
  total: number;
  page: number;
  size: number;
}

// 订单状态映射
const ORDER_STATUS_MAP: Record<string, { text: string; color: string }> = {
  pending: { text: '待支付', color: 'orange' },
  paid: { text: '已支付', color: 'green' },
  expired: { text: '已过期', color: 'default' },
};

export default function BillingAdmin() {
  const { message } = App.useApp();

  // ============== 用户额度 Tab ==============
  const [usersLoading, setUsersLoading] = useState(false);
  const [users, setUsers] = useState<AdminUserQuota[]>([]);
  const [usersTotal, setUsersTotal] = useState(0);
  const [usersPage, setUsersPage] = useState(1);
  const [searchText, setSearchText] = useState('');

  // ============== 订单列表 Tab ==============
  const [ordersLoading, setOrdersLoading] = useState(false);
  const [orders, setOrders] = useState<OrderInfo[]>([]);
  const [ordersTotal, setOrdersTotal] = useState(0);
  const [ordersPage, setOrdersPage] = useState(1);
  const [totalAmount, setTotalAmount] = useState(0);
  const [statusFilter, setStatusFilter] = useState('');

  // ============== 调整额度弹窗 ==============
  const [adjustVisible, setAdjustVisible] = useState(false);
  const [adjustUser, setAdjustUser] = useState<AdminUserQuota | null>(null);
  const [adjustForm] = Form.useForm();

  const pageSize = 20;

  // 加载用户额度列表
  const loadUsers = useCallback(async () => {
    setUsersLoading(true);
    try {
      const { data } = await api.get<AdminUserQuotaList>('/admin/billing/users', {
        params: { page: usersPage, size: pageSize, search: searchText },
      });
      setUsers(data.items);
      setUsersTotal(data.total);
    } catch {
      message.error('加载用户额度列表失败');
    } finally {
      setUsersLoading(false);
    }
  }, [usersPage, searchText, message]);

  // 加载订单列表
  const loadOrders = useCallback(async () => {
    setOrdersLoading(true);
    try {
      const { data } = await api.get<OrderListResponse>('/admin/billing/orders', {
        params: { page: ordersPage, size: pageSize, status_filter: statusFilter },
      });
      setOrders(data.items);
      setOrdersTotal(data.total);
      setTotalAmount(data.total_amount);
    } catch {
      message.error('加载订单列表失败');
    } finally {
      setOrdersLoading(false);
    }
  }, [ordersPage, statusFilter, message]);

  useEffect(() => { loadUsers(); }, [loadUsers]);
  useEffect(() => { loadOrders(); }, [loadOrders]);

  // 打开调整额度弹窗
  const openAdjust = (user: AdminUserQuota) => {
    setAdjustUser(user);
    adjustForm.resetFields();
    setAdjustVisible(true);
  };

  // 提交调整
  const handleAdjust = async () => {
    if (!adjustUser) return;
    try {
      const values = await adjustForm.validateFields();
      await api.post('/admin/billing/adjust', {
        user_id: adjustUser.user_id,
        amount: values.amount,
        note: values.note,
      });
      message.success('额度调整成功');
      setAdjustVisible(false);
      loadUsers();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || '调整额度失败，请重试');
    }
  };

  // 用户额度表格列
  const userColumns = [
    {
      title: '邮箱',
      dataIndex: 'email',
      key: 'email',
      ellipsis: true,
    },
    {
      title: '昵称',
      dataIndex: 'nickname',
      key: 'nickname',
      width: 120,
    },
    {
      title: '剩余额度',
      dataIndex: 'quota_balance',
      key: 'quota_balance',
      width: 120,
      render: (v: number) => (
        <span style={{ color: v < 100 ? '#ff4d4f' : undefined, fontWeight: 'bold' }}>
          {v.toLocaleString()} 条
        </span>
      ),
    },
    {
      title: '已使用',
      dataIndex: 'total_used',
      key: 'total_used',
      width: 100,
      render: (v: number) => `${v.toLocaleString()} 条`,
    },
    {
      title: '已购买',
      dataIndex: 'total_purchased',
      key: 'total_purchased',
      width: 100,
      render: (v: number) => `${v.toLocaleString()} 条`,
    },
    {
      title: '操作',
      key: 'action',
      width: 100,
      render: (_: unknown, record: AdminUserQuota) => (
        <Button type="link" size="small" onClick={() => openAdjust(record)}>
          调整额度
        </Button>
      ),
    },
  ];

  // 订单表格列
  const orderColumns = [
    {
      title: '订单号',
      dataIndex: 'out_trade_no',
      key: 'out_trade_no',
      width: 200,
      ellipsis: true,
    },
    {
      title: '额度包',
      dataIndex: 'package_name',
      key: 'package_name',
      width: 140,
    },
    {
      title: '金额',
      dataIndex: 'amount',
      key: 'amount',
      width: 80,
      render: (v: number) => `¥${v}`,
    },
    {
      title: '支付方式',
      dataIndex: 'pay_type',
      key: 'pay_type',
      width: 100,
      render: (v: string) => (
        <Tag color={v === 'alipay' ? 'blue' : 'green'}>
          {v === 'alipay' ? '支付宝' : '微信'}
        </Tag>
      ),
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 80,
      render: (s: string) => {
        const info = ORDER_STATUS_MAP[s] || { text: s, color: 'default' };
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 140,
      render: (t: string) => dayjs(t).format('MM-DD HH:mm'),
    },
    {
      title: '支付时间',
      dataIndex: 'paid_at',
      key: 'paid_at',
      width: 140,
      render: (t: string | null) => t ? dayjs(t).format('MM-DD HH:mm') : '-',
    },
  ];

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {/* 统计卡片 */}
      <Row gutter={16}>
        <Col span={8}>
          <Card>
            <Statistic title="总订单数" value={ordersTotal} prefix={<ShoppingCartOutlined />} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="总收入" value={totalAmount} prefix="¥" precision={2} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="用户总数" value={usersTotal} prefix={<WalletOutlined />} />
          </Card>
        </Col>
      </Row>

      {/* Tab 切换 */}
      <Card>
        <Tabs
          items={[
            {
              key: 'users',
              label: '用户额度',
              children: (
                <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                  <Input.Search
                    placeholder="搜索邮箱或昵称"
                    allowClear
                    enterButton={<SearchOutlined />}
                    style={{ maxWidth: 400 }}
                    onSearch={(v) => { setSearchText(v); setUsersPage(1); }}
                  />
                  <Table
                    rowKey="user_id"
                    dataSource={users}
                    columns={userColumns}
                    loading={usersLoading}
                    pagination={{
                      current: usersPage,
                      pageSize,
                      total: usersTotal,
                      onChange: setUsersPage,
                      showTotal: (t) => `共 ${t} 个用户`,
                    }}
                    size="middle"
                  />
                </Space>
              ),
            },
            {
              key: 'orders',
              label: '订单列表',
              children: (
                <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                  <Space>
                    <Button
                      type={statusFilter === '' ? 'primary' : 'default'}
                      size="small"
                      onClick={() => { setStatusFilter(''); setOrdersPage(1); }}
                    >
                      全部
                    </Button>
                    <Button
                      type={statusFilter === 'paid' ? 'primary' : 'default'}
                      size="small"
                      onClick={() => { setStatusFilter('paid'); setOrdersPage(1); }}
                    >
                      已支付
                    </Button>
                    <Button
                      type={statusFilter === 'pending' ? 'primary' : 'default'}
                      size="small"
                      onClick={() => { setStatusFilter('pending'); setOrdersPage(1); }}
                    >
                      待支付
                    </Button>
                  </Space>
                  <Table
                    rowKey="id"
                    dataSource={orders}
                    columns={orderColumns}
                    loading={ordersLoading}
                    pagination={{
                      current: ordersPage,
                      pageSize,
                      total: ordersTotal,
                      onChange: setOrdersPage,
                      showTotal: (t) => `共 ${t} 笔订单`,
                    }}
                    size="middle"
                  />
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* 调整额度弹窗 */}
      <Modal
        title="调整用户额度"
        open={adjustVisible}
        onOk={handleAdjust}
        onCancel={() => setAdjustVisible(false)}
        okText="确认调整"
        cancelText="取消"
      >
        {adjustUser && (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <div>
              <strong>用户：</strong>{adjustUser.email}
              <br />
              <strong>当前余额：</strong>{adjustUser.quota_balance.toLocaleString()} 条
            </div>
            <Form form={adjustForm} layout="vertical">
              <Form.Item
                name="amount"
                label="调整数量（正数增加，负数扣减）"
                rules={[
                  { required: true, message: '请输入调整数量' },
                  { type: 'number', min: -999999, max: 999999, message: '数量不合理' },
                ]}
              >
                <InputNumber style={{ width: '100%' }} placeholder="如: 500 或 -100" />
              </Form.Item>
              <Form.Item
                name="note"
                label="调整原因（必填）"
                rules={[{ required: true, message: '请填写调整原因' }]}
              >
                <Input.TextArea rows={2} placeholder="如: 补偿赠送、活动奖励等" maxLength={200} />
              </Form.Item>
            </Form>
          </Space>
        )}
      </Modal>
    </Space>
  );
}
