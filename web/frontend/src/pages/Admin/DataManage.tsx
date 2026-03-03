/**
 * 管理员 — 数据管理页
 *
 * 显示文件数据的整体概况：
 * 1. 扫描结果概览（文件总数/有清单文件/预估条数）
 * 2. 格式分布
 * 3. 省份分布（TOP20）
 * 4. 专业分布
 * 5. 定额库覆盖矩阵
 * 6. 经验库增长趋势
 */

import { useEffect, useState, useCallback } from 'react';
import {
  Card, Row, Col, Statistic, Table, Tag, Space, Empty,
  Button, Descriptions, Progress,
} from 'antd';
import {
  DatabaseOutlined, FileExcelOutlined, ReloadOutlined,
  CheckCircleOutlined, WarningOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

interface ScanSummary {
  has_data: boolean;
  message?: string;
  total_files: number;
  bill_files: number;
  estimated_items: number;
  format_distribution: Array<{ format: string; count: number }>;
  province_distribution: Array<{ province: string; count: number }>;
  specialty_distribution: Array<{ specialty: string; count: number }>;
  status_distribution: Array<{ status: string; count: number }>;
}

interface CoverageData {
  has_db_and_files: string[];
  has_files_no_db: string[];
  has_db_no_files: string[];
  db_provinces: string[];
  file_provinces: string[];
}

interface ExperienceTrend {
  total: number;
  authority: number;
  candidate: number;
  by_province: Record<string, any>;
}

// 格式中文名
const FORMAT_LABELS: Record<string, string> = {
  standard_bill: '标准清单',
  work_list: '工作量清单',
  equipment_list: '设备材料清单',
  summary_only: '纯汇总',
  unknown: '未识别',
  non_excel: '非Excel',
};

// 状态中文名
const STATUS_LABELS: Record<string, string> = {
  pending: '待扫描',
  scanned: '已扫描',
  matched: '已匹配',
  skipped: '已跳过',
  error: '错误',
};

export default function DataManage() {
  const [scanData, setScanData] = useState<ScanSummary | null>(null);
  const [coverage, setCoverage] = useState<CoverageData | null>(null);
  const [experience, setExperience] = useState<ExperienceTrend | null>(null);
  const [loading, setLoading] = useState(false);

  const loadAll = useCallback(async () => {
    setLoading(true);
    try {
      const [scanRes, covRes, expRes] = await Promise.all([
        api.get('/admin/data/scan-summary'),
        api.get('/admin/data/coverage'),
        api.get('/admin/data/experience-trend'),
      ]);
      setScanData(scanRes.data);
      setCoverage(covRes.data);
      setExperience(expRes.data);
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadAll(); }, [loadAll]);

  if (!scanData?.has_data && !loading) {
    return (
      <div>
        <h2>数据管理</h2>
        <Empty description="尚未扫描文件，请先在批量任务看板中启动扫描" />
      </div>
    );
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>数据管理</h2>
        <Button icon={<ReloadOutlined />} onClick={loadAll} loading={loading}>刷新</Button>
      </div>

      {/* 概览统计 */}
      {scanData?.has_data && (
        <>
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={8}>
              <Card>
                <Statistic
                  title="文件总数"
                  value={scanData.total_files}
                  prefix={<FileExcelOutlined />}
                />
              </Card>
            </Col>
            <Col span={8}>
              <Card>
                <Statistic
                  title="有清单数据的文件"
                  value={scanData.bill_files}
                  prefix={<CheckCircleOutlined style={{ color: '#22c55e' }} />}
                  valueStyle={{ color: '#22c55e' }}
                />
              </Card>
            </Col>
            <Col span={8}>
              <Card>
                <Statistic
                  title="预估清单总条数"
                  value={scanData.estimated_items}
                  prefix={<DatabaseOutlined />}
                />
              </Card>
            </Col>
          </Row>

          {/* 格式分布 + 状态分布 */}
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={12}>
              <Card title="格式分布" size="small">
                <Table
                  dataSource={scanData.format_distribution}
                  rowKey="format"
                  size="small"
                  pagination={false}
                  columns={[
                    {
                      title: '格式',
                      dataIndex: 'format',
                      render: (v: string) => FORMAT_LABELS[v] || v,
                    },
                    {
                      title: '文件数',
                      dataIndex: 'count',
                      width: 100,
                      align: 'right',
                    },
                    {
                      title: '占比',
                      width: 120,
                      render: (_: any, record: any) => (
                        <Progress
                          percent={Math.round((record.count / scanData.total_files) * 100)}
                          size="small"
                          showInfo
                        />
                      ),
                    },
                  ]}
                />
              </Card>
            </Col>
            <Col span={12}>
              <Card title="处理状态分布" size="small">
                <Table
                  dataSource={scanData.status_distribution}
                  rowKey="status"
                  size="small"
                  pagination={false}
                  columns={[
                    {
                      title: '状态',
                      dataIndex: 'status',
                      render: (v: string) => (
                        <Tag color={v === 'matched' ? 'green' : v === 'error' ? 'red' : 'blue'}>
                          {STATUS_LABELS[v] || v}
                        </Tag>
                      ),
                    },
                    {
                      title: '文件数',
                      dataIndex: 'count',
                      width: 100,
                      align: 'right',
                    },
                    {
                      title: '占比',
                      width: 120,
                      render: (_: any, record: any) => (
                        <Progress
                          percent={Math.round((record.count / scanData.total_files) * 100)}
                          size="small"
                          showInfo
                        />
                      ),
                    },
                  ]}
                />
              </Card>
            </Col>
          </Row>

          {/* 省份分布 + 专业分布 */}
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={12}>
              <Card title="省份分布 (TOP 20)" size="small">
                <Table
                  dataSource={scanData.province_distribution}
                  rowKey="province"
                  size="small"
                  pagination={false}
                  scroll={{ y: 400 }}
                  columns={[
                    { title: '省份', dataIndex: 'province', width: 80 },
                    { title: '文件数', dataIndex: 'count', width: 80, align: 'right' },
                    {
                      title: '占比',
                      width: 120,
                      render: (_: any, record: any) => (
                        <Progress
                          percent={Math.round((record.count / scanData.total_files) * 100)}
                          size="small"
                        />
                      ),
                    },
                  ]}
                />
              </Card>
            </Col>
            <Col span={12}>
              <Card title="专业分布" size="small">
                <Table
                  dataSource={scanData.specialty_distribution}
                  rowKey="specialty"
                  size="small"
                  pagination={false}
                  scroll={{ y: 400 }}
                  columns={[
                    { title: '专业', dataIndex: 'specialty', width: 100 },
                    { title: '文件数', dataIndex: 'count', width: 80, align: 'right' },
                    {
                      title: '占比',
                      width: 120,
                      render: (_: any, record: any) => (
                        <Progress
                          percent={Math.round((record.count / scanData.total_files) * 100)}
                          size="small"
                        />
                      ),
                    },
                  ]}
                />
              </Card>
            </Col>
          </Row>
        </>
      )}

      {/* 覆盖矩阵 */}
      {coverage && (
        <Card title="定额库覆盖矩阵" style={{ marginBottom: 16 }}>
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item
              label={
                <Space>
                  <CheckCircleOutlined style={{ color: '#22c55e' }} />
                  <span>有定额库且有文件 ({coverage.has_db_and_files.length})</span>
                </Space>
              }
            >
              {coverage.has_db_and_files.length > 0 ? (
                <Space wrap>
                  {coverage.has_db_and_files.map(p => <Tag key={p} color="green">{p}</Tag>)}
                </Space>
              ) : '无'}
            </Descriptions.Item>
            <Descriptions.Item
              label={
                <Space>
                  <WarningOutlined style={{ color: '#f59e0b' }} />
                  <span>有文件无定额库 ({coverage.has_files_no_db.length})</span>
                </Space>
              }
            >
              {coverage.has_files_no_db.length > 0 ? (
                <Space wrap>
                  {coverage.has_files_no_db.map(p => <Tag key={p} color="orange">{p}</Tag>)}
                </Space>
              ) : '无'}
            </Descriptions.Item>
            <Descriptions.Item
              label={
                <Space>
                  <DatabaseOutlined style={{ color: '#94a3b8' }} />
                  <span>有定额库无文件 ({coverage.has_db_no_files.length})</span>
                </Space>
              }
            >
              {coverage.has_db_no_files.length > 0 ? (
                <Space wrap>
                  {coverage.has_db_no_files.map(p => <Tag key={p}>{p}</Tag>)}
                </Space>
              ) : '无'}
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      {/* 经验库概况 */}
      {experience && (
        <Card title="经验库概况">
          <Row gutter={16}>
            <Col span={8}>
              <Statistic title="总记录数" value={experience.total} />
            </Col>
            <Col span={8}>
              <Statistic
                title="权威层"
                value={experience.authority}
                valueStyle={{ color: '#22c55e' }}
              />
            </Col>
            <Col span={8}>
              <Statistic
                title="候选层"
                value={experience.candidate}
                valueStyle={{ color: '#3b82f6' }}
              />
            </Col>
          </Row>
        </Card>
      )}
    </div>
  );
}
