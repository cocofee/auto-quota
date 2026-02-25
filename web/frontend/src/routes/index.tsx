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
import ConsultPage from '../pages/Consult';

// 管理员页面
import TaskListAll from '../pages/Admin/TaskListAll';
import ExperienceManage from '../pages/Admin/ExperienceManage';
import FeedbackReview from '../pages/Admin/FeedbackReview';
import ConsultReview from '../pages/Admin/ConsultReview';
import AnalyticsPage from '../pages/Admin/AnalyticsPage';
import UserManage from '../pages/Admin/UserManage';
import SettingsPage from '../pages/Admin/SettingsPage';
import QuotaManage from '../pages/Admin/QuotaManage';
import LogViewer from '../pages/Admin/LogViewer';

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
      { path: 'consult', element: <ConsultPage /> },

      // === 管理员专属页面（RequireAdmin 包裹） ===
      { path: 'admin/tasks', element: <RequireAdmin><TaskListAll /></RequireAdmin> },
      { path: 'admin/experience', element: <RequireAdmin><ExperienceManage /></RequireAdmin> },
      { path: 'admin/quotas', element: <RequireAdmin><QuotaManage /></RequireAdmin> },
      { path: 'admin/feedback', element: <RequireAdmin><FeedbackReview /></RequireAdmin> },
      { path: 'admin/consult-review', element: <RequireAdmin><ConsultReview /></RequireAdmin> },
      { path: 'admin/analytics', element: <RequireAdmin><AnalyticsPage /></RequireAdmin> },
      { path: 'admin/users', element: <RequireAdmin><UserManage /></RequireAdmin> },
      { path: 'admin/settings', element: <RequireAdmin><SettingsPage /></RequireAdmin> },
      { path: 'admin/logs', element: <RequireAdmin><LogViewer /></RequireAdmin> },
    ],
  },
]);

export default router;
