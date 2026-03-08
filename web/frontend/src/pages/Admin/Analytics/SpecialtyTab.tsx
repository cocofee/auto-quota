/**
 * Tab3：专业分析
 *
 * 环形图（Top10专业按条数分布）+ 中文专业名表格
 */

import { useEffect, useState } from 'react';
import { Card, Table, Tag, Space, Button, App } from 'antd';
import { Pie } from '@ant-design/charts';
import api from '../../../services/api';
import { GREEN_THRESHOLD, YELLOW_THRESHOLD, specialtyLabel } from '../../../utils/experience';
import type { SpecialtyItem } from './utils';

export default function SpecialtyTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [specialties, setSpecialties] = useState<SpecialtyItem[]>([]);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await api.get<{ items: SpecialtyItem[] }>('/admin/analytics/by-specialty');
      setSpecialties(res.data.items);
    } catch {
      message.error('加载专业数据失败');
    } finally {
      setLoading(false);
    }
  };

  // 饼图数据：Top 10，其余合并为"其他"
  const pieData = (() => {
    if (specialties.length <= 10) {
      return specialties.map(s => ({
        type: specialtyLabel(s.specialty),
        value: s.count,
      }));
    }
    const top10 = specialties.slice(0, 10);
    const otherCount = specialties.slice(10).reduce((sum, s) => sum + s.count, 0);
    return [
      ...top10.map(s => ({ type: specialtyLabel(s.specialty), value: s.count })),
      { type: '其他', value: otherCount },
    ];
  })();

  // 表格数据：默认Top10或全部
  const tableData = showAll ? specialties : specialties.slice(0, 10);

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 环形图 */}
      <Card title="专业分布" loading={loading}>
        {pieData.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#999', padding: 60 }}>暂无数据</div>
        ) : (
          <Pie
            data={pieData}
            angleField="value"
            colorField="type"
            innerRadius={0.6}
            height={320}
            label={{
              text: (d: { type: string; value: number }) => `${d.type}\n${d.value}条`,
              style: { fontSize: 11 },
            }}
            legend={{ color: { position: 'right', layout: { justifyContent: 'center' } } }}
            tooltip={{ title: 'type', items: [{ channel: 'y', name: '条数' }] }}
          />
        )}
      </Card>

      {/* 专业明细表格 */}
      <Card
        title="专业明细"
        loading={loading}
        extra={
          specialties.length > 10 && (
            <Button type="link" size="small" onClick={() => setShowAll(!showAll)}>
              {showAll ? '收起' : `查看全部(${specialties.length})`}
            </Button>
          )
        }
      >
        <Table
          rowKey="specialty"
          dataSource={tableData}
          size="small"
          pagination={false}
          columns={[
            {
              title: '专业编码',
              dataIndex: 'specialty',
              key: 'specialty',
              width: 100,
            },
            {
              title: '专业名称',
              key: 'label',
              render: (_: unknown, record: SpecialtyItem) => specialtyLabel(record.specialty),
            },
            {
              title: '条数',
              dataIndex: 'count',
              key: 'count',
              width: 80,
              sorter: (a: SpecialtyItem, b: SpecialtyItem) => a.count - b.count,
            },
            {
              title: '平均置信度',
              dataIndex: 'avg_confidence',
              key: 'avg_confidence',
              width: 120,
              sorter: (a: SpecialtyItem, b: SpecialtyItem) => a.avg_confidence - b.avg_confidence,
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
