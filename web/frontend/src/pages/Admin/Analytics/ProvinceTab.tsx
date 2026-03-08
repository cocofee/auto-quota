/**
 * Tab2：省份分析
 *
 * 横向柱状图（匹配率排行）+ 省份明细表格
 */

import { useEffect, useState } from 'react';
import { Card, Table, Tag, Space, App } from 'antd';
import { Bar } from '@ant-design/charts';
import api from '../../../services/api';
import { COLORS, GREEN_THRESHOLD, YELLOW_THRESHOLD } from '../../../utils/experience';
import { shortenProvince } from './utils';
import type { ProvinceItem } from './utils';

export default function ProvinceTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [provinces, setProvinces] = useState<ProvinceItem[]>([]);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await api.get<{ items: ProvinceItem[] }>('/admin/analytics/by-province');
      setProvinces(res.data.items);
    } catch {
      message.error('加载省份数据失败');
    } finally {
      setLoading(false);
    }
  };

  // 柱状图数据：按平均匹配率排序，显示缩短的省份名
  const chartData = provinces
    .filter(p => p.avg_confidence > 0)
    .map(p => ({
      province: shortenProvince(p.province),
      avg_confidence: Math.round(p.avg_confidence * 10) / 10,
    }))
    .sort((a, b) => a.avg_confidence - b.avg_confidence); // 升序，让高的在图表上方

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 横向柱状图 */}
      <Card title="各省平均匹配率" loading={loading}>
        {chartData.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#999', padding: 60 }}>暂无数据</div>
        ) : (
          <Bar
            data={chartData}
            xField="avg_confidence"
            yField="province"
            height={Math.max(300, chartData.length * 36)}
            axis={{
              x: {
                title: '平均置信度 %',
                labelFormatter: (v: number) => `${v}%`,
              },
            }}
            style={{
              fill: ({ avg_confidence }: { avg_confidence: number }) =>
                avg_confidence >= GREEN_THRESHOLD ? COLORS.greenSolid
                  : avg_confidence >= YELLOW_THRESHOLD ? COLORS.yellowSolid : COLORS.redSolid,
              radius: 4,
            }}
            label={{
              text: (d: { avg_confidence: number }) => `${d.avg_confidence}%`,
              position: 'right',
              style: { fontSize: 11 },
            }}
            tooltip={{ title: 'province', items: [{ channel: 'x', name: '匹配率' }] }}
          />
        )}
      </Card>

      {/* 明细表格 */}
      <Card title="省份明细" loading={loading}>
        <Table
          rowKey="province"
          dataSource={provinces}
          size="small"
          pagination={provinces.length > 20 ? { pageSize: 20 } : false}
          columns={[
            {
              title: '省份',
              dataIndex: 'province',
              key: 'province',
              render: (v: string) => shortenProvince(v),
            },
            { title: '任务数', dataIndex: 'task_count', key: 'task_count', width: 80, sorter: (a: ProvinceItem, b: ProvinceItem) => a.task_count - b.task_count },
            { title: '匹配条数', dataIndex: 'match_count', key: 'match_count', width: 100, sorter: (a: ProvinceItem, b: ProvinceItem) => a.match_count - b.match_count },
            {
              title: '平均匹配率',
              dataIndex: 'avg_confidence',
              key: 'avg_confidence',
              width: 120,
              sorter: (a: ProvinceItem, b: ProvinceItem) => a.avg_confidence - b.avg_confidence,
              render: (v: number) => (
                <Tag color={v >= GREEN_THRESHOLD ? 'green' : v >= YELLOW_THRESHOLD ? 'orange' : 'red'}>
                  {v}%
                </Tag>
              ),
            },
          ]}
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>
    </Space>
  );
}
