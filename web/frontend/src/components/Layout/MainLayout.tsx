import { useMemo, useState } from 'react';
import { Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Button, Dropdown, Layout, Menu, Modal, Tag, Timeline } from 'antd';
import type { MenuProps } from 'antd';
import {
  AimOutlined,
  BarChartOutlined,
  BookOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  GoldOutlined,
  LogoutOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  TeamOutlined,
  UnorderedListOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { APP_VERSION, CHANGELOG } from '../../constants/changelog';
import type { ChangelogEntry } from '../../constants/changelog';
import { useAuthStore } from '../../stores/auth';

const { Header, Sider, Content } = Layout;

export default function MainLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [changelogOpen, setChangelogOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;
  const adminTab = new URLSearchParams(location.search).get('tab');
  const selectedMenuKey =
    location.pathname === '/admin' && adminTab === 'staging'
      ? '/admin/knowledge-staging'
      : location.pathname;

  const menuItems: MenuProps['items'] = useMemo(() => {
    const toolColors = {
      bill: '#1a56db',
      quota: '#16a34a',
      material: '#ea580c',
      backfill: '#7c3aed',
    };

    const base: MenuProps['items'] = [
      {
        key: '/dashboard',
        icon: <DashboardOutlined />,
        label: '首页',
      },
      { type: 'divider' },
      {
        key: 'tools-group',
        type: 'group',
        label: '智能工具',
        children: [
          {
            key: '/tools/bill-compiler',
            icon: <FileTextOutlined style={{ color: toolColors.bill }} />,
            label: '智能编清单',
          },
          {
            key: '/tasks/create',
            icon: <AimOutlined style={{ color: toolColors.quota }} />,
            label: '智能套定额',
          },
          {
            key: '/tools/material-price',
            icon: <GoldOutlined style={{ color: toolColors.material }} />,
            label: '智能查主材',
          },
          {
            key: '/tools/price-backfill',
            icon: <BarChartOutlined style={{ color: toolColors.backfill }} />,
            label: '智能填价',
          },
        ],
      },
      { type: 'divider' },
      {
        key: 'tasks-group',
        type: 'group',
        label: '任务',
        children: [
          {
            key: '/tasks',
            icon: <UnorderedListOutlined />,
            label: '我的任务',
          },
        ],
      },
    ];

    if (!isAdmin) return base;

    return [
      ...base,
      { type: 'divider' },
      {
        key: '/admin/tasks',
        icon: <UnorderedListOutlined />,
        label: '全部任务',
      },
      { type: 'divider' },
      {
        key: 'admin-data-group',
        type: 'group',
        label: '数据与治理',
        children: [
          {
            key: '/admin',
            icon: <DatabaseOutlined />,
            label: '管理中心',
          },
          {
            key: '/admin/knowledge-staging',
            icon: <SafetyCertificateOutlined />,
            label: '候选确认与晋升',
          },
          {
            key: '/admin/quotas',
            icon: <BookOutlined />,
            label: '定额库',
          },
        ],
      },
      { type: 'divider' },
      {
        key: 'admin-system-group',
        type: 'group',
        label: '系统管理',
        children: [
          {
            key: '/admin/users',
            icon: <TeamOutlined />,
            label: '用户管理',
          },
          {
            key: '/admin/settings',
            icon: <SettingOutlined />,
            label: '系统设置',
          },
          {
            key: '/admin/logs',
            icon: <FileSearchOutlined />,
            label: '系统日志',
          },
        ],
      },
    ];
  }, [isAdmin]);

  const onMenuClick = ({ key }: { key: string }) => {
    navigate(key);
  };

  const userMenuItems = [
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: async () => {
        await logout();
        navigate('/login');
      },
    },
  ];

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        width={220}
        collapsedWidth={64}
        style={{
          background: '#ffffff',
          borderRight: '1px solid #e2e8f0',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          <div
            style={{
              height: 64,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderBottom: '1px solid #f1f5f9',
            }}
          >
            <span
              style={{
                fontSize: collapsed ? 18 : 20,
                fontWeight: 700,
                color: '#2563eb',
                letterSpacing: collapsed ? 0 : 2,
              }}
            >
              {collapsed ? 'J' : 'J.A.R.V.I.S'}
            </span>
          </div>

          <div style={{ flex: 1, overflow: 'auto' }}>
            <Menu
              mode="inline"
              selectedKeys={[selectedMenuKey]}
              items={menuItems}
              onClick={onMenuClick}
              style={{ borderRight: 0, padding: '8px 0' }}
            />
          </div>

          <div
            onClick={() => setChangelogOpen(true)}
            style={{
              padding: collapsed ? '8px 0' : '10px 16px',
              borderTop: '1px solid #f1f5f9',
              textAlign: 'center',
              cursor: 'pointer',
              color: '#94a3b8',
              fontSize: 12,
              transition: 'color 0.2s',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = '#2563eb';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = '#94a3b8';
            }}
          >
            <div>{collapsed ? `v${APP_VERSION.split('.').pop()}` : `v${APP_VERSION}`}</div>
            {!collapsed && (
              <div
                style={{
                  marginTop: 4,
                  fontSize: 11,
                  lineHeight: 1.4,
                }}
              >
                版本说明
              </div>
            )}
          </div>
        </div>
      </Sider>

      <Layout>
        <Header
          style={{
            padding: '0 24px',
            background: '#ffffff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: '1px solid #e2e8f0',
            boxShadow: '0 1px 2px 0 rgba(0,0,0,0.03)',
          }}
        >
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
            style={{ color: '#64748b' }}
          />
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Button type="text" icon={<UserOutlined />} style={{ color: '#334155' }}>
              {user?.nickname || user?.email || '用户'}
              {isAdmin && (
                <Tag color="blue" style={{ marginLeft: 8 }}>
                  管理员
                </Tag>
              )}
            </Button>
          </Dropdown>
        </Header>

        <Content style={{ margin: 24, minHeight: 280 }}>
          <Outlet />
        </Content>
      </Layout>

      <Modal
        title={isAdmin ? '版本说明（完整）' : '版本说明'}
        open={changelogOpen}
        onCancel={() => setChangelogOpen(false)}
        footer={null}
        width={520}
      >
        <Timeline
          style={{ marginTop: 20 }}
          items={CHANGELOG
            .map((entry): ChangelogEntry => {
              if (isAdmin) return entry;
              return { ...entry, changes: entry.changes.filter((change) => change.type === 'user') };
            })
            .filter((entry) => entry.changes.length > 0)
            .map((entry, index) => ({
              color: index === 0 ? 'blue' : 'gray',
              children: (
                <div>
                  <div style={{ fontWeight: 600, marginBottom: 4 }}>
                    v{entry.version}
                    <span style={{ fontWeight: 400, color: '#94a3b8', marginLeft: 8, fontSize: 12 }}>
                      {entry.date}
                    </span>
                  </div>
                  <ul style={{ margin: 0, paddingLeft: 18, color: '#475569' }}>
                    {entry.changes.map((change, changeIndex) => (
                      <li key={changeIndex} style={{ marginBottom: 2 }}>
                        {isAdmin && change.type === 'admin' ? (
                          <span style={{ color: '#94a3b8' }}>
                            <Tag
                              style={{
                                fontSize: 10,
                                lineHeight: '16px',
                                padding: '0 4px',
                                marginRight: 4,
                                borderColor: '#e2e8f0',
                                color: '#94a3b8',
                              }}
                            >
                              系统
                            </Tag>
                            {change.text}
                          </span>
                        ) : (
                          change.text
                        )}
                      </li>
                    ))}
                  </ul>
                </div>
              ),
            }))}
        />
      </Modal>
    </Layout>
  );
}
