import type { ComponentType } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';

type LazyModule = Promise<{ default: ComponentType<any> }>;

function lazyPage(importPage: () => LazyModule) {
  return async () => {
    const module = await importPage();
    return { Component: module.default };
  };
}

function lazyWrappedPage(
  importWrapper: () => LazyModule,
  importPage: () => LazyModule,
) {
  return async () => {
    const [wrapperModule, pageModule] = await Promise.all([importWrapper(), importPage()]);
    const Wrapper = wrapperModule.default;
    const Page = pageModule.default;

    function WrappedPage() {
      return (
        <Wrapper>
          <Page />
        </Wrapper>
      );
    }

    return { Component: WrappedPage };
  };
}

const router = createBrowserRouter([
  {
    path: '/login',
    lazy: lazyPage(() => import('../pages/Login')),
  },
  {
    path: '/',
    element: <Navigate to="/login" replace />,
  },
  {
    path: '/',
    lazy: lazyWrappedPage(
      () => import('../components/Layout/RequireAuth'),
      () => import('../components/Layout/MainLayout'),
    ),
    children: [
      { index: true, element: <Navigate to="/dashboard" replace /> },

      { path: 'dashboard', lazy: lazyPage(() => import('../pages/Dashboard')) },
      { path: 'tasks/create', lazy: lazyPage(() => import('../pages/Task/CreatePage')) },
      { path: 'tasks', lazy: lazyPage(() => import('../pages/Task/ListPage')) },
      { path: 'tasks/:taskId/results', lazy: lazyPage(() => import('../pages/Results')) },

      { path: 'quota/logs', lazy: lazyPage(() => import('../pages/Quota/LogsPage')) },
      {
        path: 'quota/purchase',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Quota/PurchasePage'),
        ),
      },
      {
        path: 'quota/pay-result',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Quota/PayResultPage'),
        ),
      },

      { path: 'tools/price-backfill', lazy: lazyPage(() => import('../pages/Tools/PriceBackfill')) },
      { path: 'tools/bill-compiler', lazy: lazyPage(() => import('../pages/Tools/BillCompiler')) },
      { path: 'tools/material-price', lazy: lazyPage(() => import('../pages/Tools/MaterialPrice')) },

      {
        path: 'admin',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/AdminHub'),
        ),
      },
      { path: 'admin/batch', element: <Navigate to="/admin?tab=analytics" replace /> },
      { path: 'admin/data', element: <Navigate to="/admin?tab=analytics" replace /> },
      { path: 'admin/error-analysis', element: <Navigate to="/admin?tab=analytics" replace /> },
      { path: 'admin/analytics', element: <Navigate to="/admin?tab=analytics" replace /> },
      { path: 'admin/experience', element: <Navigate to="/admin?tab=experience" replace /> },
      {
        path: 'admin/knowledge-staging',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/KnowledgeStagingPage'),
        ),
      },
      {
        path: 'admin/tasks',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/TaskListAll'),
        ),
      },
      {
        path: 'admin/quotas',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/QuotaManage'),
        ),
      },
      {
        path: 'admin/feedback',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/FeedbackReview'),
        ),
      },
      {
        path: 'admin/openclaw-reviews',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/OpenClawReviewPage'),
        ),
      },
      {
        path: 'admin/users',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/UserManage'),
        ),
      },
      {
        path: 'admin/settings',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/SettingsPage'),
        ),
      },
      {
        path: 'admin/logs',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/LogViewer'),
        ),
      },
      {
        path: 'admin/billing',
        lazy: lazyWrappedPage(
          () => import('../components/Layout/RequireAdmin'),
          () => import('../pages/Admin/BillingAdmin'),
        ),
      },
    ],
  },
]);

export default router;
