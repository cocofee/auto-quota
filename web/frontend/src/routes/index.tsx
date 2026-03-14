/**
 * 路由配置
 *
 * 定义前端所有页面的 URL 路径和对应组件。
 * 管理员页面用 RequireAdmin 包裹，普通用户访问显示 403。
 */

import { createBrowserRouter, Navigate } from 'react-router-dom';
import MainLayout from '../components/Layout/MainLayout';
import RequireAuth from '../components/Layout/RequireAuth';
import RequireAdmin from '../components/Layout/RequireAdmin';
import LoginPage from '../pages/Login';
import DashboardPage from '../pages/Dashboard';
import TaskCreatePage from '../pages/Task/CreatePage';
import TaskListPage from '../pages/Task/ListPage';
import ResultsPage from '../pages/Results';
// 额度管理页面
import LogsPage from '../pages/Quota/LogsPage';
import PurchasePage from '../pages/Quota/PurchasePage';
import PayResultPage from '../pages/Quota/PayResultPage';
// 管理员页面
import TaskListAll from '../pages/Admin/TaskListAll';
import ExperienceManage from '../pages/Admin/ExperienceManage';
import FeedbackReview from '../pages/Admin/FeedbackReview';
import AnalyticsPage from '../pages/Admin/AnalyticsPage';
import UserManage from '../pages/Admin/UserManage';
import SettingsPage from '../pages/Admin/SettingsPage';
import QuotaManage from '../pages/Admin/QuotaManage';
import LogViewer from '../pages/Admin/LogViewer';
import BillingAdmin from '../pages/Admin/BillingAdmin';
import BatchDashboard from '../pages/Admin/BatchDashboard';
import ErrorAnalysis from '../pages/Admin/ErrorAnalysis';
import DataManage from '../pages/Admin/DataManage';
// 工具页面
import PriceBackfill from '../pages/Tools/PriceBackfill';
import BillCompiler from '../pages/Tools/BillCompiler';

const router = createBrowserRouter([
  // 登录页（不需要布局和登录状态）
  {
    path: '/login',
    element: <LoginPage />,
  },

  // 需要登录的页面（包裹在 MainLayout + RequireAuth 中）
  {
    path: '/',
    element: (
      <RequireAuth>
        <MainLayout />
      </RequireAuth>
    ),
    children: [
      // 根路径重定向到看板
      { index: true, element: <Navigate to="/dashboard" replace /> },

      // === 所有用户可访问 ===
      { path: 'dashboard', element: <DashboardPage /> },
      { path: 'tasks/create', element: <TaskCreatePage /> },
      { path: 'tasks', element: <TaskListPage /> },
      { path: 'tasks/:taskId/results', element: <ResultsPage /> },

      // === 额度相关 ===
      { path: 'quota/logs', element: <LogsPage /> },
      { path: 'quota/purchase', element: <RequireAdmin><PurchasePage /></RequireAdmin> },
      { path: 'quota/pay-result', element: <RequireAdmin><PayResultPage /></RequireAdmin> },

      // === 工具 ===
      { path: 'tools/price-backfill', element: <PriceBackfill /> },
      { path: 'tools/bill-compiler', element: <BillCompiler /> },

      // === 管理员专属页面（RequireAdmin 包裹） ===
      { path: 'admin/tasks', element: <RequireAdmin><TaskListAll /></RequireAdmin> },
      { path: 'admin/experience', element: <RequireAdmin><ExperienceManage /></RequireAdmin> },
      { path: 'admin/quotas', element: <RequireAdmin><QuotaManage /></RequireAdmin> },
      { path: 'admin/feedback', element: <RequireAdmin><FeedbackReview /></RequireAdmin> },
      { path: 'admin/analytics', element: <RequireAdmin><AnalyticsPage /></RequireAdmin> },
      { path: 'admin/users', element: <RequireAdmin><UserManage /></RequireAdmin> },
      { path: 'admin/settings', element: <RequireAdmin><SettingsPage /></RequireAdmin> },
      { path: 'admin/logs', element: <RequireAdmin><LogViewer /></RequireAdmin> },
      { path: 'admin/billing', element: <RequireAdmin><BillingAdmin /></RequireAdmin> },
      { path: 'admin/batch', element: <RequireAdmin><BatchDashboard /></RequireAdmin> },
      { path: 'admin/error-analysis', element: <RequireAdmin><ErrorAnalysis /></RequireAdmin> },
      { path: 'admin/data', element: <RequireAdmin><DataManage /></RequireAdmin> },
    ],
  },
]);

export default router;
