/**
 * 主布局组件
 *
 * 登录后的页面框架：左侧菜单栏 + 顶部用户信息栏 + 中间内容区。
 * 菜单根据用户角色动态显示：
 * - 普通用户：首页、新建任务、我的任务
 * - 管理员：额外显示 所有任务、经验库、反馈审核、准确率分析、用户管理、系统设置
 */

import { useState, useMemo } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { Layout, Menu, Button, Dropdown, Tag, Modal, Timeline } from 'antd';
import type { MenuProps } from 'antd';
import {
  DashboardOutlined,
  PlusCircleOutlined,
  UnorderedListOutlined,
  LogoutOutlined,
  UserOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  DatabaseOutlined,
  BarChartOutlined,
  TeamOutlined,
  SettingOutlined,
  MessageOutlined,
  AppstoreOutlined,
  BookOutlined,
  FileTextOutlined,
  BulbOutlined,
  WalletOutlined,
  CloudServerOutlined,
  AlertOutlined,
  FolderOpenOutlined,
  DollarOutlined,
} from '@ant-design/icons';
import { useAuthStore } from '../../stores/auth';
import { APP_VERSION, CHANGELOG } from '../../constants/changelog';
import type { ChangelogEntry } from '../../constants/changelog';

const { Header, Sider, Content } = Layout;

export default function MainLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [changelogOpen, setChangelogOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;

  // 取最新一条用户可见的更新摘要（侧边栏底部展示用）
  const latestUserChange = useMemo(() => {
    for (const entry of CHANGELOG) {
      const userChange = entry.changes.find(c => c.type === 'user');
      if (userChange) {
        return { version: entry.version, date: entry.date, text: userChange.text };
      }
    }
    return null;
  }, []);

  // 根据角色动态生成菜单
  const menuItems: MenuProps['items'] = useMemo(() => {
    // 所有用户都能看到的基础菜单
    const base: MenuProps['items'] = [
      {
        key: '/dashboard',
        icon: <DashboardOutlined />,
        label: '首页',
      },
      {
        key: '/tasks/create',
        icon: <PlusCircleOutlined />,
        label: '新建任务',
      },
      {
        key: '/tasks',
        icon: <UnorderedListOutlined />,
        label: '我的任务',
      },
      { type: 'divider' },
      {
        key: '/quota/logs',
        icon: <WalletOutlined />,
        label: '使用记录',
      },
      { type: 'divider' },
      {
        key: 'tools-group',
        type: 'group',
        label: '工具',
        children: [
          {
            key: '/tools/price-backfill',
            icon: <DollarOutlined />,
            label: '智能填价',
          },
        ],
      },
    ];

    if (!isAdmin) return base;

    // 管理员额外菜单
    const adminItems: MenuProps['items'] = [
      { type: 'divider' },
      {
        key: 'admin-group',
        type: 'group',
        label: '管理后台',
        children: [
          {
            key: '/admin/tasks',
            icon: <AppstoreOutlined />,
            label: '所有任务',
          },
          {
            key: '/admin/experience',
            icon: <DatabaseOutlined />,
            label: '经验库',
          },
          {
            key: '/admin/quotas',
            icon: <BookOutlined />,
            label: '定额库',
          },
          {
            key: '/admin/knowledge',
            icon: <BulbOutlined />,
            label: '知识库',
          },
          {
            key: '/admin/feedback',
            icon: <MessageOutlined />,
            label: '反馈审核',
          },
          {
            key: '/admin/analytics',
            icon: <BarChartOutlined />,
            label: '准确率分析',
          },
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
            icon: <FileTextOutlined />,
            label: '系统日志',
          },
          {
            key: '/admin/billing',
            icon: <WalletOutlined />,
            label: '额度管理',
          },
          {
            key: '/admin/batch',
            icon: <CloudServerOutlined />,
            label: '批量处理',
          },
          {
            key: '/admin/error-analysis',
            icon: <AlertOutlined />,
            label: '错误分析',
          },
          {
            key: '/admin/data',
            icon: <FolderOpenOutlined />,
            label: '数据管理',
          },
        ],
      },
    ];

    return [...base, ...adminItems];
  }, [isAdmin]);

  // 菜单点击跳转
  const onMenuClick = ({ key }: { key: string }) => {
    navigate(key);
  };

  // 用户下拉菜单
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
      {/* 左侧菜单栏 */}
      <Sider
        trigger={null}
        collapsible
        collapsed={collapsed}
        width={isAdmin ? 200 : 180}
        style={{
          background: '#ffffff',
          borderRight: '1px solid #e2e8f0',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
          {/* 品牌Logo区域 */}
          <div
            style={{
              height: 64,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderBottom: '1px solid #f1f5f9',
            }}
          >
            <span style={{
              fontSize: collapsed ? 18 : 20,
              fontWeight: 700,
              color: '#2563eb',
              letterSpacing: collapsed ? 0 : 2,
            }}>
              {collapsed ? 'J' : 'J.A.R.V.I.S'}
            </span>
          </div>
          {/* 菜单区域（占满剩余空间） */}
          <div style={{ flex: 1, overflow: 'auto' }}>
            <Menu
              mode="inline"
              selectedKeys={[location.pathname]}
              items={menuItems}
              onClick={onMenuClick}
              style={{ borderRight: 0, padding: '8px 0' }}
            />
          </div>
          {/* 底部版本号 + 最新更新摘要 */}
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
            onMouseEnter={e => (e.currentTarget.style.color = '#2563eb')}
            onMouseLeave={e => (e.currentTarget.style.color = '#94a3b8')}
          >
            <div>{collapsed ? `v${APP_VERSION.split('.').pop()}` : `v${APP_VERSION}`}</div>
            {/* 展开状态下显示最新更新摘要 */}
            {!collapsed && latestUserChange && (
              <div style={{
                marginTop: 4,
                fontSize: 11,
                lineHeight: 1.4,
              }}>
                <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {latestUserChange.text}
                </div>
                <div style={{ marginTop: 2 }}>{latestUserChange.date}</div>
              </div>
            )}
          </div>
        </div>
      </Sider>

      <Layout>
        {/* 顶部栏 */}
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
              {isAdmin && <Tag color="blue" style={{ marginLeft: 8 }}>管理员</Tag>}
            </Button>
          </Dropdown>
        </Header>

        {/* 内容区 */}
        <Content style={{ margin: 24, minHeight: 280 }}>
          <Outlet />
        </Content>
      </Layout>

      {/* 更新日志弹窗 */}
      <Modal
        title={isAdmin ? '更新日志（完整）' : '更新日志'}
        open={changelogOpen}
        onCancel={() => setChangelogOpen(false)}
        footer={null}
        width={520}
      >
        <Timeline
          style={{ marginTop: 20 }}
          items={
            // 按角色过滤：普通用户只看 user 类型，管理员看全部
            CHANGELOG
              .map((entry): ChangelogEntry => {
                if (isAdmin) return entry;
                // 普通用户：只保留 type='user' 的条目
                return { ...entry, changes: entry.changes.filter(c => c.type === 'user') };
              })
              .filter(entry => entry.changes.length > 0) // 整版无可见条目则跳过
              .map((entry, i) => ({
                color: i === 0 ? 'blue' : 'gray',
                children: (
                  <div>
                    <div style={{ fontWeight: 600, marginBottom: 4 }}>
                      v{entry.version}
                      <span style={{ fontWeight: 400, color: '#94a3b8', marginLeft: 8, fontSize: 12 }}>
                        {entry.date}
                      </span>
                    </div>
                    <ul style={{ margin: 0, paddingLeft: 18, color: '#475569' }}>
                      {entry.changes.map((c, j) => (
                        <li key={j} style={{ marginBottom: 2 }}>
                          {/* 管理员模式下，admin 条目加 [系统] 标签 + 灰色 */}
                          {isAdmin && c.type === 'admin' ? (
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
                              {c.text}
                            </span>
                          ) : (
                            c.text
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                ),
              }))
          }
        />
      </Modal>
    </Layout>
  );
}
