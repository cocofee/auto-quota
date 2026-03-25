import { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Space, Select, App } from 'antd';
import {
  CheckCircleOutlined,
  BarChartOutlined,
  FileTextOutlined,
  DashboardOutlined,
} from '@ant-design/icons';
import { Pie, Line } from '@ant-design/charts';
import api from '../../../services/api';
import { COLORS, GREEN_THRESHOLD, YELLOW_THRESHOLD } from '../../../utils/experience';
import type { OverviewData, TrendItem } from './utils';

export default function OverviewTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [trends, setTrends] = useState<TrendItem[]>([]);
  const [trendDays, setTrendDays] = useState(30);

  useEffect(() => {
    void loadOverview();
  }, []);

  useEffect(() => {
    void loadTrends(trendDays);
  }, [trendDays]);

  const loadOverview = async () => {
    setLoading(true);
    try {
      const res = await api.get<OverviewData>('/admin/analytics/overview');
      setOverview(res.data);
    } catch {
      message.error('加载效果总览失败');
    } finally {
      setLoading(false);
    }
  };

  const loadTrends = async (days: number) => {
    try {
      const res = await api.get<{ items: TrendItem[] }>('/admin/analytics/trends', { params: { days } });
      setTrends(res.data.items);
    } catch {
      setTrends([]);
    }
  };

  const pieData = overview ? [
    { type: `高置信度（≥${GREEN_THRESHOLD}%）`, value: overview.high_confidence },
    { type: `中置信度（${YELLOW_THRESHOLD}% - ${GREEN_THRESHOLD - 1}%）`, value: overview.mid_confidence },
    { type: `低置信度（<${YELLOW_THRESHOLD}%）`, value: overview.low_confidence },
  ] : [];

  const colorMap: Record<string, string> = {
    [`高置信度（≥${GREEN_THRESHOLD}%）`]: COLORS.greenSolid,
    [`中置信度（${YELLOW_THRESHOLD}% - ${GREEN_THRESHOLD - 1}%）`]: COLORS.yellowSolid,
    [`低置信度（<${YELLOW_THRESHOLD}%）`]: COLORS.redSolid,
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="任务总数" value={overview?.total_tasks || 0} prefix={<FileTextOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic
              title="已完成任务"
              value={overview?.completed_tasks || 0}
              prefix={<CheckCircleOutlined />}
              valueStyle={{ color: COLORS.greenSolid }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="匹配结果数" value={overview?.total_results || 0} prefix={<BarChartOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic
              title="平均置信度"
              value={overview?.avg_confidence || 0}
              suffix="%"
              prefix={<DashboardOutlined />}
              valueStyle={{
                color: (overview?.avg_confidence || 0) >= GREEN_THRESHOLD
                  ? COLORS.greenSolid
                  : (overview?.avg_confidence || 0) >= YELLOW_THRESHOLD
                    ? COLORS.yellowSolid
                    : COLORS.redSolid,
              }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={16}>
        <Col xs={24} md={10}>
          <Card title="置信度分布" loading={loading}>
            {pieData.length > 0 && pieData.some((item) => item.value > 0) ? (
              <Pie
                data={pieData}
                angleField="value"
                colorField="type"
                innerRadius={0.6}
                height={280}
                style={{ fill: ({ type }: { type: string }) => colorMap[type] || '#ccc' }}
                label={{
                  text: (item: { value: number }) => `${item.value} 条`,
                  style: { fontSize: 12 },
                }}
                legend={{ color: { position: 'bottom', layout: { justifyContent: 'center' } } }}
                tooltip={{ title: 'type', items: [{ channel: 'y' }] }}
              />
            ) : (
              <div style={{ textAlign: 'center', color: '#999', padding: 60 }}>暂时没有分布数据</div>
            )}
          </Card>
        </Col>

        <Col xs={24} md={14}>
          <Card
            title="任务趋势"
            loading={loading}
            extra={(
              <Select
                value={trendDays}
                onChange={setTrendDays}
                style={{ width: 120 }}
                options={[
                  { value: 7, label: '最近 7 天' },
                  { value: 30, label: '最近 30 天' },
                  { value: 90, label: '最近 90 天' },
                ]}
              />
            )}
          >
            {trends.length === 0 ? (
              <div style={{ textAlign: 'center', color: '#999', padding: 60 }}>暂时没有任务趋势数据</div>
            ) : (
              <Line
                data={trends}
                xField="date"
                yField="task_count"
                height={280}
                axis={{
                  x: {
                    labelAutoRotate: true,
                    label: { formatter: (value: string) => value.slice(5) },
                  },
                  y: { title: '任务数' },
                }}
                style={{ lineWidth: 2 }}
                point={{ size: 3 }}
                interaction={{ tooltip: true }}
              />
            )}
          </Card>
        </Col>
      </Row>
    </Space>
  );
}
