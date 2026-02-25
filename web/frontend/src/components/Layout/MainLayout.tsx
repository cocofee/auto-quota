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
import { Layout, Menu, Button, Dropdown, theme, Tag } from 'antd';
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
} from '@ant-design/icons';
import { useAuthStore } from '../../stores/auth';

const { Header, Sider, Content } = Layout;

export default function MainLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuthStore();
  const { token: themeToken } = theme.useToken();

  const isAdmin = user?.is_admin ?? false;

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
        style={{ background: themeToken.colorBgContainer }}
      >
        <div
          style={{
            height: 64,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderBottom: `1px solid ${themeToken.colorBorderSecondary}`,
          }}
        >
          <span style={{ fontSize: collapsed ? 16 : 20, fontWeight: 'bold' }}>
            {collapsed ? 'AQ' : 'auto-quota'}
          </span>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname]}
          items={menuItems}
          onClick={onMenuClick}
          style={{ borderRight: 0 }}
        />
      </Sider>

      <Layout>
        {/* 顶部栏 */}
        <Header
          style={{
            padding: '0 24px',
            background: themeToken.colorBgContainer,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            borderBottom: `1px solid ${themeToken.colorBorderSecondary}`,
          }}
        >
          <Button
            type="text"
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed(!collapsed)}
          />
          <Dropdown menu={{ items: userMenuItems }} placement="bottomRight">
            <Button type="text" icon={<UserOutlined />}>
              {user?.nickname || user?.email || '用户'}
              {isAdmin && <Tag color="red" style={{ marginLeft: 8 }}>管理员</Tag>}
            </Button>
          </Dropdown>
        </Header>

        {/* 内容区（子路由渲染在这里） */}
        <Content style={{ margin: 24, minHeight: 280 }}>
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
}
