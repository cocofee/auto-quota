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
      <ConfigProvider locale={zhCN}>
        <AntdApp>
          <RouterProvider router={router} />
        </AntdApp>
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>,
);
