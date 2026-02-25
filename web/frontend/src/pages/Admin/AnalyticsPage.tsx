/**
 * 管理员 — 准确率分析
 *
 * 显示匹配系统的整体表现：
 * 1. 概览统计卡片
 * 2. 省份分布
 * 3. 专业统计（置信度对比）
 * 4. 任务趋势
 */

import { useEffect, useState } from 'react';
import {
  Card, Row, Col, Statistic, Table, Tag, Space, App, Progress,
} from 'antd';
import {
  CheckCircleOutlined, BarChartOutlined, FileTextOutlined,
  UserOutlined, SafetyOutlined, ExperimentOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

interface OverviewData {
  total_tasks: number;
  completed_tasks: number;
  total_results: number;
  high_confidence: number;
  mid_confidence: number;
  low_confidence: number;
  avg_confidence: number;
  confirmed_results: number;
  total_users: number;
}

interface ProvinceItem {
  province: string;
  task_count: number;
}

interface SpecialtyItem {
  specialty: string;
  count: number;
  avg_confidence: number;
}

interface TrendItem {
  date: string;
  task_count: number;
}

export default function AnalyticsPage() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [provinces, setProvinces] = useState<ProvinceItem[]>([]);
  const [specialties, setSpecialties] = useState<SpecialtyItem[]>([]);
  const [trends, setTrends] = useState<TrendItem[]>([]);

  useEffect(() => {
    loadAll();
  }, []);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [ovRes, provRes, specRes, trendRes] = await Promise.all([
        api.get<OverviewData>('/admin/analytics/overview'),
        api.get<{ items: ProvinceItem[] }>('/admin/analytics/by-province'),
        api.get<{ items: SpecialtyItem[] }>('/admin/analytics/by-specialty'),
        api.get<{ items: TrendItem[] }>('/admin/analytics/trends'),
      ]);
      setOverview(ovRes.data);
      setProvinces(provRes.data.items);
      setSpecialties(specRes.data.items);
      setTrends(trendRes.data.items);
    } catch {
      message.error('加载分析数据失败');
    } finally {
      setLoading(false);
    }
  };

  // 置信度分布百分比
  const totalResults = overview?.total_results || 1;
  const highPct = Math.round(((overview?.high_confidence || 0) / totalResults) * 100);
  const midPct = Math.round(((overview?.mid_confidence || 0) / totalResults) * 100);
  const lowPct = Math.round(((overview?.low_confidence || 0) / totalResults) * 100);

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 概览卡片 */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="总任务" value={overview?.total_tasks || 0} prefix={<FileTextOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="已完成" value={overview?.completed_tasks || 0} prefix={<CheckCircleOutlined />} valueStyle={{ color: '#52c41a' }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="总匹配条数" value={overview?.total_results || 0} prefix={<BarChartOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic
              title="平均置信度"
              value={overview?.avg_confidence || 0}
              suffix="%"
              prefix={<ExperimentOutlined />}
              valueStyle={{
                color: (overview?.avg_confidence || 0) >= 85 ? '#52c41a'
                  : (overview?.avg_confidence || 0) >= 70 ? '#faad14' : '#ff4d4f',
              }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="总用户" value={overview?.total_users || 0} prefix={<UserOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="已确认结果" value={overview?.confirmed_results || 0} prefix={<SafetyOutlined />} valueStyle={{ color: '#1677ff' }} />
          </Card>
        </Col>
        <Col xs={24} sm={12}>
          <Card loading={loading} title="置信度分布">
            <Space direction="vertical" style={{ width: '100%' }}>
              <div>
                <span style={{ display: 'inline-block', width: 80 }}>高置信度</span>
                <Progress percent={highPct} strokeColor="#52c41a" format={() => `${overview?.high_confidence || 0}条`} />
              </div>
              <div>
                <span style={{ display: 'inline-block', width: 80 }}>中置信度</span>
                <Progress percent={midPct} strokeColor="#faad14" format={() => `${overview?.mid_confidence || 0}条`} />
              </div>
              <div>
                <span style={{ display: 'inline-block', width: 80 }}>低置信度</span>
                <Progress percent={lowPct} strokeColor="#ff4d4f" format={() => `${overview?.low_confidence || 0}条`} />
              </div>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* 省份分布 + 专业统计 */}
      <Row gutter={16}>
        <Col span={12}>
          <Card title="按省份统计" loading={loading}>
            <Table
              rowKey="province"
              dataSource={provinces}
              size="small"
              pagination={false}
              columns={[
                { title: '省份', dataIndex: 'province', key: 'province' },
                { title: '任务数', dataIndex: 'task_count', key: 'task_count', width: 80 },
              ]}
              locale={{ emptyText: '暂无数据' }}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="按专业统计" loading={loading}>
            <Table
              rowKey="specialty"
              dataSource={specialties}
              size="small"
              pagination={false}
              columns={[
                { title: '专业', dataIndex: 'specialty', key: 'specialty' },
                { title: '条数', dataIndex: 'count', key: 'count', width: 60 },
                {
                  title: '平均置信度',
                  dataIndex: 'avg_confidence',
                  key: 'avg_confidence',
                  width: 110,
                  render: (v: number) => (
                    <Tag color={v >= 85 ? 'green' : v >= 70 ? 'orange' : 'red'}>
                      {v}%
                    </Tag>
                  ),
                },
              ]}
              locale={{ emptyText: '暂无数据' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 任务趋势 */}
      <Card title="最近任务趋势" loading={loading}>
        {trends.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>暂无完成的任务数据</div>
        ) : (
          <Table
            rowKey="date"
            dataSource={trends}
            size="small"
            pagination={false}
            columns={[
              { title: '日期', dataIndex: 'date', key: 'date' },
              {
                title: '完成任务数',
                dataIndex: 'task_count',
                key: 'task_count',
                width: 120,
                render: (v: number) => (
                  <Progress
                    percent={Math.min(v * 20, 100)}
                    format={() => `${v}`}
                    size="small"
                  />
                ),
              },
            ]}
          />
        )}
      </Card>
    </Space>
  );
}
