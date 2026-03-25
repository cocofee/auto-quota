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
import { Button, Card, Col, Row, Space, Tabs, Tag, Typography } from 'antd';
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

interface QuickEntryDefinition {
  key: Extract<AdminTabKey, 'staging' | 'openclaw' | 'experience'>;
  title: string;
  description: string;
  actionLabel: string;
  accentColor: string;
  icon: ReactNode;
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
    label: '正式经验库',
    icon: <ExperimentOutlined />,
    description: '管理 ExperienceDB 中已确认的正式经验，以及待确认的经验记录。',
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
    label: '候选知识晋升',
    icon: <SafetyCertificateOutlined />,
    description: '查看 OpenClaw 和其他业务入口写入的候选知识，并决定确认、驳回或执行晋升。',
    children: <KnowledgeStagingPage />,
  },
];

const VALID_TABS = new Set<string>(TAB_DEFINITIONS.map((item) => item.key));

const QUICK_ENTRIES: QuickEntryDefinition[] = [
  {
    key: 'staging',
    title: '候选知识晋升',
    description: '先看这里。OpenClaw 和其他业务入口写进来的候选知识，会在这里等待你确认、驳回或晋升。',
    actionLabel: '进入候选区',
    accentColor: '#2563eb',
    icon: <SafetyCertificateOutlined />,
  },
  {
    key: 'openclaw',
    title: 'OpenClaw 复核',
    description: '这里处理待人工二次确认的 OpenClaw 审核结果，适合先做复核，再决定是否转成候选知识。',
    actionLabel: '进入复核区',
    accentColor: '#7c3aed',
    icon: <RobotOutlined />,
  },
  {
    key: 'experience',
    title: '正式经验库',
    description: '这里查看和维护 ExperienceDB 中已经导入、确认或人工整理过的正式经验记录。',
    actionLabel: '进入经验库',
    accentColor: '#059669',
    icon: <ExperimentOutlined />,
  },
];

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
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card>
        <Space direction="vertical" size="small" style={{ width: '100%' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <Typography.Title level={4} style={{ margin: 0 }}>
              治理中心
            </Typography.Title>
            <Tag color="blue">先处理候选，再看分析</Tag>
          </div>
          <Typography.Text type="secondary">
            这里把知识治理入口集中在一起。优先看候选知识晋升和 OpenClaw 复核，分析类页面放在下面标签里按需查看。
          </Typography.Text>
        </Space>
      </Card>

      <Row gutter={[16, 16]}>
        {QUICK_ENTRIES.map((entry) => {
          const isActive = activeTab === entry.key;
          return (
            <Col xs={24} md={8} key={entry.key}>
              <Card
                hoverable
                style={{
                  height: '100%',
                  borderColor: isActive ? entry.accentColor : '#e2e8f0',
                  boxShadow: isActive ? `0 0 0 1px ${entry.accentColor} inset` : undefined,
                }}
                styles={{
                  body: {
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 12,
                    minHeight: 190,
                  },
                }}
                onClick={() => handleTabChange(entry.key)}
              >
                <div
                  style={{
                    width: 40,
                    height: 40,
                    borderRadius: 12,
                    display: 'inline-flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    color: entry.accentColor,
                    background: `${entry.accentColor}14`,
                    fontSize: 18,
                  }}
                >
                  {entry.icon}
                </div>
                <div>
                  <Typography.Title level={5} style={{ margin: 0 }}>
                    {entry.title}
                  </Typography.Title>
                  <Typography.Paragraph type="secondary" style={{ margin: '8px 0 0 0' }}>
                    {entry.description}
                  </Typography.Paragraph>
                </div>
                <div style={{ marginTop: 'auto' }}>
                  <Button
                    type={isActive ? 'primary' : 'default'}
                    onClick={(event) => {
                      event.stopPropagation();
                      handleTabChange(entry.key);
                    }}
                  >
                    {entry.actionLabel}
                  </Button>
                </div>
              </Card>
            </Col>
          );
        })}
      </Row>

      <Tabs
        activeKey={activeTab}
        items={items}
        onChange={handleTabChange}
        destroyInactiveTabPane={false}
      />
    </Space>
  );
}
