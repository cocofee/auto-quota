/**
 * 路由守卫：需要登录才能访问的页面
 *
 * 如果用户未登录，自动跳转到 /login。
 */

import { Navigate, useLocation } from 'react-router-dom';
import { useAuthStore } from '../../stores/auth';
import { Spin } from 'antd';

export default function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuthStore();
  const location = useLocation();

  // 正在加载用户信息时显示loading
  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" tip="加载中...">
          {/* Ant Design 5 的 Spin 需要有子元素才会显示 tip */}
          <div style={{ padding: 50 }} />
        </Spin>
      </div>
    );
  }

  // 未登录，跳转到登录页（保存当前路径，登录后跳回来）
  if (!user) {
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }

  return <>{children}</>;
}
