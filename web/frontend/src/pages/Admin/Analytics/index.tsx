/**
 * 管理员 — 准确率分析（Tab分页版）
 *
 * 4个Tab：总览 | 省份分析 | 专业分析 | 跑分趋势
 */

import { Tabs } from 'antd';
import {
  DashboardOutlined, GlobalOutlined, AppstoreOutlined, LineChartOutlined,
} from '@ant-design/icons';
import OverviewTab from './OverviewTab';
import ProvinceTab from './ProvinceTab';
import SpecialtyTab from './SpecialtyTab';
import BenchmarkTab from './BenchmarkTab';

export default function AnalyticsPage() {
  return (
    <Tabs
      defaultActiveKey="overview"
      items={[
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
        {
          key: 'benchmark',
          label: <span><LineChartOutlined /> 跑分趋势</span>,
          children: <BenchmarkTab />,
        },
      ]}
      destroyInactiveTabPane={false}
    />
  );
}
