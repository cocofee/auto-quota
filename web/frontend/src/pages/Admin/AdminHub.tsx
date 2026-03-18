/**
 * 管理中心 — 统一管理页面
 *
 * 3个Tab：
 * 1. 错误分析（原 ErrorAnalysis）
 * 2. 准确率分析（原 AnalyticsPage）
 * 3. 经验库（原 ExperienceManage）
 *
 * 支持 URL 参数切换Tab：/admin?tab=error
 */

import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Tabs } from 'antd';
import {
  WarningOutlined,
  LineChartOutlined,
  SafetyOutlined,
} from '@ant-design/icons';
import ErrorAnalysis from './ErrorAnalysis';
import AnalyticsPage from './AnalyticsPage';
import ExperienceManage from './ExperienceManage';

// Tab配置（key对应URL参数）
const TAB_ITEMS = [
  {
    key: 'error',
    label: <span><WarningOutlined /> 错误分析</span>,
    children: <ErrorAnalysis />,
  },
  {
    key: 'analytics',
    label: <span><LineChartOutlined /> 准确率分析</span>,
    children: <AnalyticsPage />,
  },
  {
    key: 'experience',
    label: <span><SafetyOutlined /> 经验库</span>,
    children: <ExperienceManage />,
  },
];

// 合法的tab key集合
const VALID_TABS = new Set(TAB_ITEMS.map(t => t.key));

export default function AdminHub() {
  const [searchParams, setSearchParams] = useSearchParams();

  // 从URL读取当前Tab，默认错误分析
  const activeTab = useMemo(() => {
    const tab = searchParams.get('tab');
    return tab && VALID_TABS.has(tab) ? tab : 'error';
  }, [searchParams]);

  const onTabChange = (key: string) => {
    setSearchParams({ tab: key }, { replace: true });
  };

  return (
    <Tabs
      activeKey={activeTab}
      onChange={onTabChange}
      items={TAB_ITEMS}
      destroyInactiveTabPane={false}
      size="large"
      style={{ marginTop: -8 }}
    />
  );
}
