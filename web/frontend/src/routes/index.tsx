import { createBrowserRouter, Navigate } from 'react-router-dom';
import MainLayout from '../components/Layout/MainLayout';
import RequireAuth from '../components/Layout/RequireAuth';
import RequireAdmin from '../components/Layout/RequireAdmin';
import LoginPage from '../pages/Login';
import DashboardPage from '../pages/Dashboard';
import TaskCreatePage from '../pages/Task/CreatePage';
import TaskListPage from '../pages/Task/ListPage';
import ResultsPage from '../pages/Results';
import LogsPage from '../pages/Quota/LogsPage';
import PurchasePage from '../pages/Quota/PurchasePage';
import PayResultPage from '../pages/Quota/PayResultPage';
import TaskListAll from '../pages/Admin/TaskListAll';
import FeedbackReview from '../pages/Admin/FeedbackReview';
import UserManage from '../pages/Admin/UserManage';
import SettingsPage from '../pages/Admin/SettingsPage';
import QuotaManage from '../pages/Admin/QuotaManage';
import LogViewer from '../pages/Admin/LogViewer';
import BillingAdmin from '../pages/Admin/BillingAdmin';
import AdminHub from '../pages/Admin/AdminHub';
import OpenClawReviewPage from '../pages/Admin/OpenClawReviewPage';
import KnowledgeStagingPage from '../pages/Admin/KnowledgeStagingPage';
import PriceBackfill from '../pages/Tools/PriceBackfill';
import BillCompiler from '../pages/Tools/BillCompiler';
import MaterialPrice from '../pages/Tools/MaterialPrice';

const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    path: '/',
    element: (
      <RequireAuth>
        <MainLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },

      { path: 'dashboard', element: <DashboardPage /> },
      { path: 'tasks/create', element: <TaskCreatePage /> },
      { path: 'tasks', element: <TaskListPage /> },
      { path: 'tasks/:taskId/results', element: <ResultsPage /> },

      { path: 'quota/logs', element: <LogsPage /> },
      {
        path: 'quota/purchase',
        element: (
          <RequireAdmin>
            <PurchasePage />
          </RequireAdmin>
        ),
      },
      {
        path: 'quota/pay-result',
        element: (
          <RequireAdmin>
            <PayResultPage />
          </RequireAdmin>
        ),
      },

      { path: 'tools/price-backfill', element: <PriceBackfill /> },
      { path: 'tools/bill-compiler', element: <BillCompiler /> },
      { path: 'tools/material-price', element: <MaterialPrice /> },

      {
        path: 'admin',
        element: (
          <RequireAdmin>
            <AdminHub />
          </RequireAdmin>
        ),
      },
      { path: 'admin/batch', element: <Navigate to="/admin?tab=analytics" replace /> },
      { path: 'admin/data', element: <Navigate to="/admin?tab=analytics" replace /> },
      { path: 'admin/error-analysis', element: <Navigate to="/admin?tab=analytics" replace /> },
      { path: 'admin/analytics', element: <Navigate to="/admin?tab=analytics" replace /> },
      { path: 'admin/experience', element: <Navigate to="/admin?tab=experience" replace /> },
      {
        path: 'admin/knowledge-staging',
        element: (
          <RequireAdmin>
            <KnowledgeStagingPage />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/tasks',
        element: (
          <RequireAdmin>
            <TaskListAll />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/quotas',
        element: (
          <RequireAdmin>
            <QuotaManage />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/feedback',
        element: (
          <RequireAdmin>
            <FeedbackReview />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/openclaw-reviews',
        element: (
          <RequireAdmin>
            <OpenClawReviewPage />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/users',
        element: (
          <RequireAdmin>
            <UserManage />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/settings',
        element: (
          <RequireAdmin>
            <SettingsPage />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/logs',
        element: (
          <RequireAdmin>
            <LogViewer />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/billing',
        element: (
          <RequireAdmin>
            <BillingAdmin />
          </RequireAdmin>
        ),
      },
    ],
  },
]);

export default router;
