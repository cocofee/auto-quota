/**
 * 支付结果页面
 *
 * 用户支付完成后跳转到此页面。
 * 通过轮询订单状态来确认支付是否成功（异步回调可能有延迟）。
 */

import { useEffect, useState, useRef } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { Card, Result, Button, Spin, Statistic, Space } from 'antd';
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import type { OrderInfo } from '../../types';

export default function PayResultPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const orderId = searchParams.get('order_id');

  const [order, setOrder] = useState<OrderInfo | null>(null);
  const [polling, setPolling] = useState(true);
  const pollCount = useRef(0);
  const maxPolls = 20; // 最多轮询20次（约60秒）
  // 用计数器触发重新轮询（解决"重新检查"按钮点击后useEffect不重新执行的问题）
  const [pollTrigger, setPollTrigger] = useState(0);

  // 轮询订单状态
  useEffect(() => {
    if (!orderId) return;

    // 重置轮询状态
    setPolling(true);
    pollCount.current = 0;
    let stopped = false; // 标记是否已停止（支付成功或超时）

    const pollOrder = async () => {
      if (stopped) return;
      try {
        const { data } = await api.get<OrderInfo>(`/quota/order/${orderId}`);
        setOrder(data);

        if (data.status === 'paid') {
          stopped = true;
          setPolling(false);
          return;
        }

        pollCount.current += 1;
        if (pollCount.current >= maxPolls) {
          stopped = true;
          setPolling(false);
          return;
        }
      } catch {
        // 查询失败，继续轮询
        pollCount.current += 1;
        if (pollCount.current >= maxPolls) {
          stopped = true;
          setPolling(false);
        }
      }
    };

    // 立即查一次
    pollOrder();

    // 每3秒轮询一次
    const timer = setInterval(() => {
      if (!stopped && pollCount.current < maxPolls) {
        pollOrder();
      } else {
        clearInterval(timer);
      }
    }, 3000);

    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [orderId, pollTrigger]);

  if (!orderId) {
    return (
      <Card>
        <Result
          status="error"
          title="缺少订单信息"
          subTitle="页面参数不完整，请从购买页面重新发起支付。"
          extra={
            <Button type="primary" onClick={() => navigate('/quota/purchase')}>
              去购买额度
            </Button>
          }
        />
      </Card>
    );
  }

  // 支付成功
  if (order?.status === 'paid') {
    return (
      <Card>
        <Result
          icon={<CheckCircleOutlined style={{ color: '#52c41a' }} />}
          title="支付成功！"
          subTitle={`已充值 ${order.package_quota.toLocaleString()} 条额度`}
          extra={
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Space size="large">
                <Statistic title="额度包" value={order.package_name} />
                <Statistic title="支付金额" value={order.amount} prefix="¥" />
              </Space>
              <Space>
                <Button type="primary" onClick={() => navigate('/dashboard')}>
                  返回首页
                </Button>
                <Button onClick={() => navigate('/quota/purchase')}>
                  继续购买
                </Button>
              </Space>
            </Space>
          }
        />
      </Card>
    );
  }

  // 等待中
  if (polling) {
    return (
      <Card>
        <Result
          icon={<Spin size="large" />}
          title="支付处理中..."
          subTitle="正在确认支付结果，请稍候。如果您已完成支付，系统将在几秒内确认。"
          extra={
            <Space>
              <Button type="primary" onClick={() => navigate('/dashboard')}>
                返回首页
              </Button>
              <Button onClick={() => navigate('/quota/logs')}>
                查看使用记录
              </Button>
            </Space>
          }
        />
      </Card>
    );
  }

  // 超时（轮询结束仍未支付成功）
  return (
    <Card>
      <Result
        icon={<ClockCircleOutlined style={{ color: '#faad14' }} />}
        title="支付状态未确认"
        subTitle="未检测到支付成功。如果您已完成支付，请稍后刷新页面或查看使用记录。"
        extra={
          <Space>
            <Button type="primary" onClick={() => setPollTrigger((n) => n + 1)}>
              重新检查
            </Button>
            <Button onClick={() => navigate('/quota/purchase')}>
              重新购买
            </Button>
            <Button onClick={() => navigate('/quota/logs')}>
              查看使用记录
            </Button>
          </Space>
        }
      />
    </Card>
  );
}
