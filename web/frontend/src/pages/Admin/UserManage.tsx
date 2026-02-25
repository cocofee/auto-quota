/**
 * 管理员 — 用户管理
 *
 * 查看所有注册用户，支持启用/禁用账号、设置/取消管理员。
 */

import { useEffect, useState } from 'react';
import {
  Card, Table, Tag, Button, Space, App, Switch, Popconfirm,
  Statistic, Row, Col,
} from 'antd';
import {
  ReloadOutlined, UserOutlined, TeamOutlined, SafetyOutlined,
} from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../services/api';

interface UserItem {
  id: string;
  email: string;
  nickname: string;
  is_active: boolean;
  is_admin: boolean;
  created_at: string;
  last_login_at: string | null;
  task_count: number;
}

interface UserListResponse {
  items: UserItem[];
  total: number;
}

export default function UserManage() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [users, setUsers] = useState<UserItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);

  const loadUsers = async (p = page) => {
    setLoading(true);
    try {
      const { data } = await api.get<UserListResponse>('/admin/users', {
        params: { page: p, size: 20 },
      });
      setUsers(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载用户列表失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadUsers(); }, [page]);

  /** 修改用户属性 */
  const updateUser = async (userId: string, field: string, value: boolean) => {
    try {
      await api.put(`/admin/users/${userId}`, { [field]: value });
      message.success('修改成功');
      loadUsers();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      message.error(detail || '修改失败');
    }
  };

  const adminCount = users.filter((u) => u.is_admin).length;
  const activeCount = users.filter((u) => u.is_active).length;

  const columns = [
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
      render: (v: string) => v || '-',
    },
    {
      title: '任务数',
      dataIndex: 'task_count',
      key: 'task_count',
      width: 80,
    },
    {
      title: '状态',
      key: 'is_active',
      width: 80,
      render: (_: unknown, record: UserItem) => (
        <Tag color={record.is_active ? 'green' : 'red'}>
          {record.is_active ? '正常' : '已禁用'}
        </Tag>
      ),
    },
    {
      title: '角色',
      key: 'is_admin',
      width: 80,
      render: (_: unknown, record: UserItem) => (
        record.is_admin
          ? <Tag color="red">管理员</Tag>
          : <Tag>普通用户</Tag>
      ),
    },
    {
      title: '注册时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 140,
      render: (t: string) => t ? dayjs(t).format('YYYY-MM-DD HH:mm') : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: unknown, record: UserItem) => (
        <Space>
          <Popconfirm
            title={record.is_active ? '确定禁用此用户？' : '确定启用此用户？'}
            onConfirm={() => updateUser(record.id, 'is_active', !record.is_active)}
          >
            <Switch
              checked={record.is_active}
              checkedChildren="启用"
              unCheckedChildren="禁用"
              size="small"
            />
          </Popconfirm>

          <Popconfirm
            title={record.is_admin ? '确定取消管理员？' : '确定设为管理员？'}
            onConfirm={() => updateUser(record.id, 'is_admin', !record.is_admin)}
          >
            <Button size="small" type={record.is_admin ? 'default' : 'primary'}>
              {record.is_admin ? '取消管理员' : '设为管理员'}
            </Button>
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Row gutter={16}>
        <Col span={8}>
          <Card>
            <Statistic title="总用户" value={total} prefix={<UserOutlined />} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="活跃用户" value={activeCount} prefix={<TeamOutlined />} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic title="管理员" value={adminCount} prefix={<SafetyOutlined />} valueStyle={{ color: '#ff4d4f' }} />
          </Card>
        </Col>
      </Row>

      <Card
        title="用户列表"
        extra={<Button icon={<ReloadOutlined />} onClick={() => loadUsers()}>刷新</Button>}
      >
        <Table
          rowKey="id"
          dataSource={users}
          columns={columns}
          loading={loading}
          size="middle"
          pagination={{
            current: page,
            total,
            showTotal: (t) => `共 ${t} 人`,
            onChange: (p) => setPage(p),
          }}
        />
      </Card>
    </Space>
  );
}
