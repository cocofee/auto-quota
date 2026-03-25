import { useEffect, useMemo, useState } from 'react';
import { Tabs } from 'antd';
import {
  DashboardOutlined,
  GlobalOutlined,
  AppstoreOutlined,
  LineChartOutlined,
} from '@ant-design/icons';
import api from '../../../services/api';
import type { BenchmarkRecord } from './utils';
import OverviewTab from './OverviewTab';
import ProvinceTab from './ProvinceTab';
import SpecialtyTab from './SpecialtyTab';
import BenchmarkTab from './BenchmarkTab';

export default function AnalyticsPage() {
  const [hasBenchmarkHistory, setHasBenchmarkHistory] = useState(false);

  useEffect(() => {
    let alive = true;

    const loadBenchmarkMeta = async () => {
      try {
        const res = await api.get<{ items: BenchmarkRecord[] }>('/admin/analytics/benchmark-history');
        if (!alive) return;
        setHasBenchmarkHistory((res.data.items || []).length > 0);
      } catch {
        if (!alive) return;
        setHasBenchmarkHistory(false);
      }
    };

    void loadBenchmarkMeta();
    return () => {
      alive = false;
    };
  }, []);

  const items = useMemo(() => {
    const base = [
      {
        key: 'overview',
        label: <span><DashboardOutlined /> 总览</span>,
        children: <OverviewTab />,
      },
      {
        key: 'province',
        label: <span><GlobalOutlined /> 省份分析</span>,
        children: <ProvinceTab />,
      },
      {
        key: 'specialty',
        label: <span><AppstoreOutlined /> 专业分析</span>,
        children: <SpecialtyTab />,
      },
    ];

    if (hasBenchmarkHistory) {
      base.push({
        key: 'benchmark',
        label: <span><LineChartOutlined /> 跑分趋势</span>,
        children: <BenchmarkTab />,
      });
    }

    return base;
  }, [hasBenchmarkHistory]);

  return (
    <Tabs
      defaultActiveKey="overview"
      items={items}
      destroyInactiveTabPane={false}
    />
  );
}
