import type { ReactNode } from 'react';
import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  AppstoreOutlined,
  ExperimentOutlined,
  FundOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { Card, Tabs, Typography } from 'antd';
import type { TabsProps } from 'antd';
import AnalyticsPage from './AnalyticsPage';
import ErrorAnalysis from './ErrorAnalysis';
import ExperienceManage from './ExperienceManage';
import KnowledgeStagingPage from './KnowledgeStagingPage';
import OpenClawReviewPage from './OpenClawReviewPage';

const DEFAULT_TAB = 'error';

type AdminTabKey = 'error' | 'analytics' | 'experience' | 'openclaw' | 'staging';

interface AdminTabDefinition {
  key: AdminTabKey;
  label: string;
  icon: ReactNode;
  description: string;
  children: ReactNode;
}

const TAB_DEFINITIONS: AdminTabDefinition[] = [
  {
    key: 'error',
    label: '错误分析',
    icon: <AppstoreOutlined />,
    description: '查看主链错误模式和问题分布。',
    children: <ErrorAnalysis />,
  },
  {
    key: 'analytics',
    label: '效果分析',
    icon: <FundOutlined />,
    description: '查看整体指标、命中情况和趋势。',
    children: <AnalyticsPage />,
  },
  {
    key: 'experience',
    label: '经验管理',
    icon: <ExperimentOutlined />,
    description: '管理 ExperienceDB 的候选与权威经验。',
    children: <ExperienceManage />,
  },
  {
    key: 'openclaw',
    label: 'OpenClaw 复核',
    icon: <RobotOutlined />,
    description: '查看待人工二次确认的 OpenClaw 审核结果。',
    children: <OpenClawReviewPage />,
  },
  {
    key: 'staging',
    label: '候选确认与晋升',
    icon: <SafetyCertificateOutlined />,
    description: '查看业务已写入的候选，并决定确认、驳回或执行晋升。',
    children: <KnowledgeStagingPage />,
  },
];

const VALID_TABS = new Set<string>(TAB_DEFINITIONS.map((item) => item.key));

export default function AdminHub() {
  const [searchParams, setSearchParams] = useSearchParams();
  const currentTab = searchParams.get('tab') || '';
  const activeTab = VALID_TABS.has(currentTab) ? (currentTab as AdminTabKey) : DEFAULT_TAB;

  const items: TabsProps['items'] = useMemo(
    () =>
      TAB_DEFINITIONS.map((item) => ({
        key: item.key,
        label: (
          <span>
            {item.icon}
            <span style={{ marginLeft: 8 }}>{item.label}</span>
          </span>
        ),
        children: (
          <div style={{ display: 'grid', gap: 16 }}>
            <Card size="small">
              <Typography.Text type="secondary">{item.description}</Typography.Text>
            </Card>
            {item.children}
          </div>
        ),
      })),
    [],
  );

  const handleTabChange = (nextTab: string) => {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('tab', nextTab);
    setSearchParams(nextParams, { replace: true });
  };

  return (
    <Tabs
      activeKey={activeTab}
      items={items}
      onChange={handleTabChange}
      destroyInactiveTabPane={false}
    />
  );
}
