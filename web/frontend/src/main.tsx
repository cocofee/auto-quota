/**
 * 应用入口
 *
 * 初始化：
 * 1. React Query — API 请求缓存和状态管理
 * 2. React Router — 页面路由
 * 3. Ant Design — 中文语言包
 * 4. 自动恢复登录状态 — 页面刷新时通过 HttpOnly Cookie 拉取会话
 */

import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ConfigProvider, App as AntdApp } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import dayjs from 'dayjs';
import 'dayjs/locale/zh-cn';

import router from './routes';
import { useAuthStore } from './stores/auth';
import ErrorBoundary from './components/ErrorBoundary';
import './index.css';

// dayjs 中文
dayjs.locale('zh-cn');

// React Query 客户端（全局缓存配置）
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,               // 失败重试1次
      staleTime: 30 * 1000,   // 30秒内认为数据是新鲜的，不重新请求
      refetchOnWindowFocus: false, // 切换窗口时不自动刷新
    },
  },
});

// 应用启动时恢复登录状态
useAuthStore.getState().fetchUser();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        locale={zhCN}
        theme={{
          token: {
            // 主色调：专业蓝
            colorPrimary: '#2563eb',
            // 圆角：8px，现代感
            borderRadius: 8,
            // 布局背景：极浅灰，比默认的 #f5f5f5 更柔和
            colorBgLayout: '#f1f5f9',
            // 文字颜色：深灰，比纯黑更舒适
            colorText: '#1e293b',
            colorTextSecondary: '#64748b',
            // 成功/警告/错误色微调
            colorSuccess: '#059669',
            colorWarning: '#d97706',
            colorError: '#dc2626',
            // 边框颜色：更淡，减少视觉噪音
            colorBorder: '#e2e8f0',
            colorBorderSecondary: '#f1f5f9',
          },
          components: {
            // 侧边菜单：选中项高亮更明显
            Menu: {
              itemSelectedBg: '#eff6ff',
              itemSelectedColor: '#2563eb',
              itemHoverBg: '#f8fafc',
            },
            // 卡片：加轻阴影
            Card: {
              boxShadowTertiary: '0 1px 3px 0 rgba(0,0,0,0.06), 0 1px 2px -1px rgba(0,0,0,0.06)',
            },
            // 按钮：圆角稍大
            Button: {
              borderRadius: 8,
            },
            // 输入框：圆角
            Input: {
              borderRadius: 8,
            },
          },
        }}
      >
        <AntdApp>
          <ErrorBoundary>
            <RouterProvider router={router} />
          </ErrorBoundary>
        </AntdApp>
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>,
);
