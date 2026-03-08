/**
 * Tab1：总览
 *
 * 4张统计卡片 + 置信度分布饼图 + 任务趋势折线图
 */

import { useEffect, useState } from 'react';
import { Card, Row, Col, Statistic, Space, Select, App } from 'antd';
import {
  CheckCircleOutlined, BarChartOutlined, FileTextOutlined, ExperimentOutlined,
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
    loadOverview();
  }, []);

  useEffect(() => {
    loadTrends(trendDays);
  }, [trendDays]);

  const loadOverview = async () => {
    setLoading(true);
    try {
      const res = await api.get<OverviewData>('/admin/analytics/overview');
      setOverview(res.data);
    } catch {
      message.error('加载概览数据失败');
    } finally {
      setLoading(false);
    }
  };

  const loadTrends = async (days: number) => {
    try {
      const res = await api.get<{ items: TrendItem[] }>('/admin/analytics/trends', { params: { days } });
      setTrends(res.data.items);
    } catch {
      // 静默失败
    }
  };

  // 置信度饼图数据
  const pieData = overview ? [
    { type: '高置信度(≥85%)', value: overview.high_confidence },
    { type: '中置信度(70-84%)', value: overview.mid_confidence },
    { type: '低置信度(<70%)', value: overview.low_confidence },
  ] : [];

  // 颜色映射
  const colorMap: Record<string, string> = {
    '高置信度(≥85%)': COLORS.greenSolid,
    '中置信度(70-84%)': COLORS.yellowSolid,
    '低置信度(<70%)': COLORS.redSolid,
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 4张统计卡片 */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="总任务" value={overview?.total_tasks || 0} prefix={<FileTextOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="已完成" value={overview?.completed_tasks || 0} prefix={<CheckCircleOutlined />} valueStyle={{ color: COLORS.greenSolid }} />
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
                color: (overview?.avg_confidence || 0) >= GREEN_THRESHOLD ? COLORS.greenSolid
                  : (overview?.avg_confidence || 0) >= YELLOW_THRESHOLD ? COLORS.yellowSolid : COLORS.redSolid,
              }}
            />
          </Card>
        </Col>
      </Row>

      {/* 置信度分布饼图 + 任务趋势折线图（并排） */}
      <Row gutter={16}>
        <Col xs={24} md={10}>
          <Card title="置信度分布" loading={loading}>
            {pieData.length > 0 && pieData.some(d => d.value > 0) ? (
              <Pie
                data={pieData}
                angleField="value"
                colorField="type"
                innerRadius={0.6}
                height={280}
                style={{ fill: ({ type }: { type: string }) => colorMap[type] || '#ccc' }}
                label={{
                  text: (d: { type: string; value: number }) => `${d.value}条`,
                  style: { fontSize: 12 },
                }}
                legend={{ color: { position: 'bottom', layout: { justifyContent: 'center' } } }}
                tooltip={{ title: 'type', items: [{ channel: 'y' }] }}
              />
            ) : (
              <div style={{ textAlign: 'center', color: '#999', padding: 60 }}>暂无数据</div>
            )}
          </Card>
        </Col>
        <Col xs={24} md={14}>
          <Card
            title="任务趋势"
            loading={loading}
            extra={
              <Select
                value={trendDays}
                onChange={setTrendDays}
                style={{ width: 110 }}
                options={[
                  { value: 7, label: '最近7天' },
                  { value: 30, label: '最近30天' },
                  { value: 90, label: '最近90天' },
                ]}
              />
            }
          >
            {trends.length === 0 ? (
              <div style={{ textAlign: 'center', color: '#999', padding: 60 }}>暂无任务数据</div>
            ) : (
              <Line
                data={trends}
                xField="date"
                yField="task_count"
                height={280}
                axis={{
                  x: {
                    labelAutoRotate: true,
                    label: { formatter: (v: string) => v.slice(5) },
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
