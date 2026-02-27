/**
 * 购买额度页面
 *
 * 展示额度包卡片，选择支付方式，点击后跳转到好易支付收银台。
 */

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Card, Row, Col, Button, Radio, Statistic, Tag, Space, App, Spin } from 'antd';
import {
  ThunderboltOutlined,
  AlipayCircleOutlined,
  WechatOutlined,
  ShoppingCartOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import type { QuotaBalance, QuotaPackage, CreateOrderResponse } from '../../types';

export default function PurchasePage() {
  const navigate = useNavigate();
  const { message } = App.useApp();

  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [balance, setBalance] = useState<QuotaBalance | null>(null);
  const [packages, setPackages] = useState<QuotaPackage[]>([]);
  const [selectedPkg, setSelectedPkg] = useState<string>('');
  const [payType, setPayType] = useState<string>('alipay');

  // 加载余额和额度包列表
  useEffect(() => {
    Promise.all([
      api.get<QuotaBalance>('/quota/balance'),
      api.get<{ items: QuotaPackage[] }>('/quota/packages'),
    ])
      .then(([balanceRes, pkgRes]) => {
        setBalance(balanceRes.data);
        setPackages(pkgRes.data.items);
        if (pkgRes.data.items.length > 0) {
          setSelectedPkg(pkgRes.data.items[0].id);
        }
      })
      .catch(() => message.error('加载数据失败'))
      .finally(() => setLoading(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 当前选中的额度包
  const currentPkg = packages.find((p) => p.id === selectedPkg);

  // 发起支付
  const handlePay = async () => {
    if (!selectedPkg || !payType) {
      message.warning('请选择额度包和支付方式');
      return;
    }

    setSubmitting(true);
    try {
      const { data } = await api.post<CreateOrderResponse>('/quota/create-order', {
        package_id: selectedPkg,
        pay_type: payType,
      });

      // 新窗口打开支付页面
      window.open(data.pay_url, '_blank');

      // 跳转到支付结果页（轮询订单状态）
      navigate(`/quota/pay-result?order_id=${data.order_id}`);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || '创建订单失败');
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div style={{ textAlign: 'center', padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      {/* 当前余额 */}
      <Card>
        <Statistic
          title="当前剩余额度"
          value={balance?.balance ?? 0}
          suffix="条"
          prefix={<ThunderboltOutlined />}
          valueStyle={{
            color: (balance?.balance ?? 0) < 100 ? '#ff4d4f' : '#1677ff',
            fontSize: 32,
          }}
        />
        <Button
          type="link"
          size="small"
          style={{ padding: 0, marginTop: 4 }}
          onClick={() => navigate('/quota/logs')}
        >
          查看使用记录
        </Button>
      </Card>

      {/* 额度包选择 */}
      <Card title="选择额度包">
        <Row gutter={[16, 16]}>
          {packages.map((pkg, index) => (
            <Col xs={24} sm={8} key={pkg.id}>
              <Card
                hoverable
                style={{
                  borderColor: selectedPkg === pkg.id ? '#1677ff' : undefined,
                  borderWidth: selectedPkg === pkg.id ? 2 : 1,
                }}
                onClick={() => setSelectedPkg(pkg.id)}
              >
                <div style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: 24, fontWeight: 'bold', color: '#1677ff' }}>
                    {pkg.quota.toLocaleString()} 条
                  </div>
                  <div style={{ fontSize: 28, fontWeight: 'bold', color: '#ff4d4f', margin: '8px 0' }}>
                    ¥{pkg.price}
                  </div>
                  <div style={{ color: '#999' }}>
                    约 ¥{(Math.round(pkg.price * 1000 / pkg.quota * 10) / 10).toFixed(1)}/千条
                  </div>
                  {index === packages.length - 1 && (
                    <Tag color="red" style={{ marginTop: 8 }}>最划算</Tag>
                  )}
                </div>
              </Card>
            </Col>
          ))}
        </Row>
      </Card>

      {/* 支付方式 + 提交 */}
      <Card title="支付方式">
        <Radio.Group
          value={payType}
          onChange={(e) => setPayType(e.target.value)}
          size="large"
          style={{ marginBottom: 24 }}
        >
          <Radio.Button value="alipay">
            <AlipayCircleOutlined style={{ color: '#1677ff', marginRight: 4 }} />
            支付宝
          </Radio.Button>
          <Radio.Button value="wxpay">
            <WechatOutlined style={{ color: '#52c41a', marginRight: 4 }} />
            微信支付
          </Radio.Button>
        </Radio.Group>

        <div>
          <Button
            type="primary"
            size="large"
            icon={<ShoppingCartOutlined />}
            loading={submitting}
            onClick={handlePay}
            style={{ width: '100%', height: 48, fontSize: 16 }}
          >
            {currentPkg
              ? `立即支付 ¥${currentPkg.price}`
              : '请选择额度包'}
          </Button>
        </div>
      </Card>
    </Space>
  );
}
