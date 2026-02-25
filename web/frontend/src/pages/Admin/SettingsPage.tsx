/**
 * 管理员 — 系统设置
 *
 * 显示系统配置信息和运行状态：
 * 1. 已导入的省份定额库
 * 2. 大模型配置状态
 * 3. 系统参数
 * 4. 服务健康状态
 */

import { useEffect, useState } from 'react';
import {
  Card, Space, App, Tag, Descriptions, Row, Col, Statistic, Table, Badge,
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined,
  DatabaseOutlined, CloudOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

export default function SettingsPage() {
  const { message } = App.useApp();
  const [provinces, setProvinces] = useState<string[]>([]);
  const [healthOk, setHealthOk] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      // 并行加载省份和健康状态
      const [provRes, healthRes] = await Promise.allSettled([
        api.get<{ provinces: string[] }>('/provinces'),
        api.get('/health'),
      ]);

      if (provRes.status === 'fulfilled') {
        setProvinces(provRes.value.data.provinces);
      }
      if (healthRes.status === 'fulfilled') {
        setHealthOk(true);
      }
    } catch {
      message.error('加载配置失败');
    } finally {
      setLoading(false);
    }
  };

  // 大模型配置（从环境变量读取，前端只显示状态，不暴露Key）
  const modelConfigs = [
    { name: 'DeepSeek', envKey: 'DEEPSEEK_API_KEY', description: '推荐用于search模式' },
    { name: 'Claude', envKey: 'CLAUDE_API_KEY', description: '推荐用于agent模式（通过中转）' },
    { name: 'Kimi', envKey: 'KIMI_API_KEY', description: '阿里云DashScope代理' },
    { name: '通义千问', envKey: 'QWEN_API_KEY', description: '阿里云DashScope' },
    { name: 'OpenAI', envKey: 'OPENAI_API_KEY', description: 'GPT系列' },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 服务状态 */}
      <Card title="服务状态" loading={loading}>
        <Row gutter={16}>
          <Col span={6}>
            <Card>
              <Statistic
                title="API服务"
                value={healthOk ? '运行中' : '异常'}
                prefix={healthOk ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
                valueStyle={{ color: healthOk ? '#52c41a' : '#ff4d4f' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="数据库"
                value={healthOk ? '已连接' : '异常'}
                prefix={<DatabaseOutlined />}
                valueStyle={{ color: healthOk ? '#52c41a' : '#ff4d4f' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="定额库数量"
                value={provinces.length}
                prefix={<DatabaseOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="任务队列"
                value={healthOk ? '就绪' : '异常'}
                prefix={<CloudOutlined />}
                valueStyle={{ color: healthOk ? '#52c41a' : '#ff4d4f' }}
              />
            </Card>
          </Col>
        </Row>
      </Card>

      {/* 已导入的省份定额库 */}
      <Card title="已导入的省份定额库" loading={loading}>
        {provinces.length === 0 ? (
          <div style={{ color: '#999', textAlign: 'center', padding: 20 }}>
            暂无定额库，请先导入省份定额数据
          </div>
        ) : (
          <Space wrap>
            {provinces.map((p) => (
              <Tag key={p} color="blue" style={{ fontSize: 14, padding: '4px 12px' }}>
                {p}
              </Tag>
            ))}
          </Space>
        )}
      </Card>

      {/* 大模型配置 */}
      <Card title="大模型配置">
        <Table
          rowKey="name"
          dataSource={modelConfigs}
          pagination={false}
          size="small"
          columns={[
            { title: '模型', dataIndex: 'name', key: 'name', width: 120 },
            { title: '说明', dataIndex: 'description', key: 'description' },
            {
              title: '配置方式',
              key: 'config',
              width: 200,
              render: () => (
                <Tag>通过 .env 文件配置</Tag>
              ),
            },
          ]}
        />
        <div style={{ marginTop: 16, color: '#666', fontSize: 13 }}>
          大模型 API Key 通过服务器 .env 文件配置，修改后需重启后端服务生效。
        </div>
      </Card>

      {/* 系统参数 */}
      <Card title="匹配参数">
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="高置信度阈值">
            <Badge status="success" text="≥ 85 分" />
          </Descriptions.Item>
          <Descriptions.Item label="中置信度阈值">
            <Badge status="warning" text="70 - 84 分" />
          </Descriptions.Item>
          <Descriptions.Item label="低置信度阈值">
            <Badge status="error" text="< 70 分" />
          </Descriptions.Item>
          <Descriptions.Item label="经验库直通阈值">
            <Badge status="processing" text="权威层精确匹配" />
          </Descriptions.Item>
          <Descriptions.Item label="默认匹配模式">
            <Tag color="blue">搜索模式（search）</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="默认经验库">
            <Tag color="green">启用</Tag>
          </Descriptions.Item>
        </Descriptions>
        <div style={{ marginTop: 16, color: '#666', fontSize: 13 }}>
          匹配参数在 config.py 中配置，修改后需重启后端服务生效。后续版本将支持在线调整。
        </div>
      </Card>
    </Space>
  );
}
