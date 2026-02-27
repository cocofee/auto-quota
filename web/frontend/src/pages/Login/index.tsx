/**
 * 登录页
 *
 * 邮箱 + 密码登录，支持切换到注册。
 */

import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { Card, Form, Input, Button, App, Tabs } from 'antd';
import { MailOutlined, LockOutlined, UserOutlined, KeyOutlined } from '@ant-design/icons';
import { useAuthStore } from '../../stores/auth';
import { getErrorMessage } from '../../utils/error';

export default function LoginPage() {
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState('login');
  const navigate = useNavigate();
  const location = useLocation();
  const { login, register } = useAuthStore();
  const { message } = App.useApp();

  // 登录前访问的页面（RequireAuth 跳转时传过来的）
  const from = (location.state as { from?: string })?.from || '/dashboard';

  /** 登录 */
  const onLogin = async (values: { email: string; password: string }) => {
    setLoading(true);
    try {
      await login(values.email, values.password);
      message.success('登录成功');
      navigate(from);
    } catch (err: unknown) {
      message.error(getErrorMessage(err, '登录失败'));
    } finally {
      setLoading(false);
    }
  };

  /** 注册 */
  const onRegister = async (values: { email: string; password: string; nickname?: string; invite_code: string }) => {
    setLoading(true);
    try {
      await register(values.email, values.password, values.nickname, values.invite_code);
      message.success('注册成功，已自动登录');
      navigate(from);
    } catch (err: unknown) {
      message.error(getErrorMessage(err, '注册失败'));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        // 渐变背景：从浅蓝到浅灰，比纯灰色更有层次感
        background: 'linear-gradient(135deg, #dbeafe 0%, #f1f5f9 50%, #e2e8f0 100%)',
      }}
    >
      <Card
        style={{
          width: 420,
          borderRadius: 12,
          border: 'none',
          boxShadow: '0 4px 24px rgba(0,0,0,0.08)',
        }}
      >
        <h2 style={{
          textAlign: 'center',
          marginBottom: 4,
          letterSpacing: 4,
          fontSize: 24,
          fontWeight: 700,
          color: '#2563eb',
        }}>
          J.A.R.V.I.S
        </h2>
        <p style={{ textAlign: 'center', color: '#64748b', marginBottom: 28, fontSize: 14 }}>
          智能造价系统
        </p>

        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          centered
          items={[
            {
              key: 'login',
              label: '登录',
              children: (
                <Form onFinish={onLogin} size="large">
                  <Form.Item
                    name="email"
                    rules={[
                      { required: true, message: '请输入邮箱' },
                      { type: 'email', message: '邮箱格式不正确' },
                    ]}
                  >
                    <Input prefix={<MailOutlined />} placeholder="邮箱" />
                  </Form.Item>
                  <Form.Item
                    name="password"
                    rules={[{ required: true, message: '请输入密码' }]}
                  >
                    <Input.Password prefix={<LockOutlined />} placeholder="密码" />
                  </Form.Item>
                  <Form.Item>
                    <Button type="primary" htmlType="submit" loading={loading} block>
                      登录
                    </Button>
                  </Form.Item>
                </Form>
              ),
            },
            {
              key: 'register',
              label: '注册',
              children: (
                <Form onFinish={onRegister} size="large">
                  <Form.Item
                    name="email"
                    rules={[
                      { required: true, message: '请输入邮箱' },
                      { type: 'email', message: '邮箱格式不正确' },
                    ]}
                  >
                    <Input prefix={<MailOutlined />} placeholder="邮箱" />
                  </Form.Item>
                  <Form.Item
                    name="password"
                    rules={[
                      { required: true, message: '请输入密码' },
                      { min: 8, message: '密码至少8位' },
                    ]}
                  >
                    <Input.Password prefix={<LockOutlined />} placeholder="密码（至少8位）" />
                  </Form.Item>
                  <Form.Item name="nickname">
                    <Input prefix={<UserOutlined />} placeholder="昵称（可选）" />
                  </Form.Item>
                  <Form.Item
                    name="invite_code"
                    rules={[{ required: true, message: '请输入邀请码' }]}
                  >
                    <Input prefix={<KeyOutlined />} placeholder="邀请码（向管理员获取）" />
                  </Form.Item>
                  <Form.Item>
                    <Button type="primary" htmlType="submit" loading={loading} block>
                      注册
                    </Button>
                  </Form.Item>
                </Form>
              ),
            },
          ]}
        />
      </Card>
    </div>
  );
}
