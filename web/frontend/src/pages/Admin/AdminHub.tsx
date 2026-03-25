import type { ReactNode } from 'react';
import { useMemo } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ExperimentOutlined,
  FundOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons';
import { Button, Card, Space, Tabs, Typography } from 'antd';
import type { TabsProps } from 'antd';
import AnalyticsPage from './AnalyticsPage';
import ExperienceManage from './ExperienceManage';
import OpenClawReviewPage from './OpenClawReviewPage';

const DEFAULT_TAB = 'analytics';

type AdminTabKey = 'analytics' | 'experience' | 'openclaw';

interface AdminTabDefinition {
  key: AdminTabKey;
  label: string;
  icon: ReactNode;
  children: ReactNode;
}

const TAB_DEFINITIONS: AdminTabDefinition[] = [
  {
    key: 'analytics',
    label: '效果分析',
    icon: <FundOutlined />,
    children: <AnalyticsPage />,
  },
  {
    key: 'experience',
    label: '正式经验库',
    icon: <ExperimentOutlined />,
    children: <ExperienceManage />,
  },
  {
    key: 'openclaw',
    label: 'OpenClaw 复核',
    icon: <RobotOutlined />,
    children: <OpenClawReviewPage />,
  },
];

const VALID_TABS = new Set<string>(TAB_DEFINITIONS.map((item) => item.key));

export default function AdminHub() {
  const navigate = useNavigate();
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
        children: item.children,
      })),
    [],
  );

  const handleTabChange = (nextTab: string) => {
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('tab', nextTab);
    setSearchParams(nextParams, { replace: true });
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card size="small">
        <div
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            gap: 12,
            flexWrap: 'wrap',
          }}
        >
          <Typography.Title level={4} style={{ margin: 0 }}>
            后台工作台
          </Typography.Title>
          <Button
            type="primary"
            icon={<SafetyCertificateOutlined />}
            onClick={() => navigate('/admin/knowledge-staging')}
          >
            去候选知识晋升
          </Button>
        </div>
      </Card>

      <Tabs
        activeKey={activeTab}
        items={items}
        onChange={handleTabChange}
        destroyInactiveTabPane={false}
      />
    </Space>
  );
}
