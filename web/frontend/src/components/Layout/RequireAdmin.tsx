/**
 * 管理员路由守卫
 *
 * 包裹管理员专属页面，非管理员用户访问时显示 403 提示。
 */

import { Navigate } from 'react-router-dom';
import { Result, Button, Spin } from 'antd';
import { useAuthStore } from '../../stores/auth';

export default function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuthStore();

  // 还在加载用户信息，显示加载状态（和 RequireAuth 一致）
  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" tip="加载中...">
          <div style={{ padding: 50 }} />
        </Spin>
      </div>
    );
  }

  // 未登录直接跳转登录页
  if (!user) return <Navigate to="/login" replace />;

  // 非管理员显示 403
  if (!user.is_admin) {
    return (
      <Result
        status="403"
        title="无权限"
        subTitle="此页面仅管理员可访问"
        extra={
          <Button type="primary" onClick={() => window.location.href = '/dashboard'}>
            返回首页
          </Button>
        }
      />
    );
  }

  return <>{children}</>;
}
