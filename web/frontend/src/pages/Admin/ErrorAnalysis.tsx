/**
 * 管理员 — 错误分析页
 *
 * 显示批量匹配的错误分析结果：
 * 1. 总体统计摘要
 * 2. 低置信度模式排行
 * 3. 按省份统计
 * 4. 按专业统计
 * 5. 省份覆盖矩阵
 */

import { useEffect, useState, useCallback } from 'react';
import {
  Card, Row, Col, Statistic, Table, Tag, Space,
  Button, Select, Descriptions, Empty,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  WarningOutlined, CheckCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

// 根因中文名
const ROOT_CAUSE_LABELS: Record<string, string> = {
  search_miss: '搜索无结果',
  synonym_gap: '同义词缺口',
  wrong_tier: '选错档位',
};

const ROOT_CAUSE_COLORS: Record<string, string> = {
  search_miss: 'red',
  synonym_gap: 'orange',
  wrong_tier: 'blue',
};

interface Summary {
  total_files: number;
  total_items: number;
  high_confidence: number;
  mid_confidence: number;
  low_confidence: number;
  avg_confidence: number;
  high_rate: number;
  low_rate: number;
}

interface PatternItem {
  pattern: string;
  count: number;
  provinces: string[];
  typical_bill: string;
  typical_match: string;
  avg_confidence: number;
  root_cause_guess: string;
}

interface ProvinceStats {
  province: string;
  files: number;
  items: number;
  avg_confidence: number;
  high_rate: number;
  low_rate: number;
}

interface SpecialtyStats {
  specialty: string;
  items: number;
  avg_confidence: number;
  high_rate: number;
  low_rate: number;
}

export default function ErrorAnalysis() {
  const [hasData, setHasData] = useState(false);
  const [reportDate, setReportDate] = useState('');
  const [algoVersion, setAlgoVersion] = useState('');
  const [summary, setSummary] = useState<Summary | null>(null);
  const [patterns, setPatterns] = useState<PatternItem[]>([]);
  const [patternTotal, setPatternTotal] = useState(0);
  const [patternPage, setPatternPage] = useState(1);
  const [provinces, setProvinces] = useState<ProvinceStats[]>([]);
  const [specialties, setSpecialties] = useState<SpecialtyStats[]>([]);
  const [coverage, setCoverage] = useState<Record<string, string[]>>({});
  const [loading, setLoading] = useState(false);

  // 筛选
  const [rootCauseFilter, setRootCauseFilter] = useState<string | undefined>();

  // 加载报告概览
  const loadReport = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api.get('/admin/analysis/error-report');
      setHasData(res.data.has_data);
      if (res.data.has_data) {
        setReportDate(res.data.report_date);
        setAlgoVersion(res.data.algo_version);
        setSummary(res.data.summary);
        setCoverage(res.data.province_coverage || {});
      }
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  }, []);

  // 加载错误模式
  const loadPatterns = useCallback(async () => {
    try {
      const params: Record<string, any> = { page: patternPage, page_size: 20 };
      if (rootCauseFilter) params.root_cause = rootCauseFilter;
      const res = await api.get('/admin/analysis/patterns', { params });
      setPatterns(res.data.items);
      setPatternTotal(res.data.total);
    } catch {
      // 静默处理
    }
  }, [patternPage, rootCauseFilter]);

  // 加载省份/专业统计
  const loadStats = useCallback(async () => {
    try {
      const [provRes, specRes] = await Promise.all([
        api.get('/admin/analysis/by-province'),
        api.get('/admin/analysis/by-specialty'),
      ]);
      setProvinces(provRes.data.items);
      setSpecialties(specRes.data.items);
    } catch {
      // 静默处理
    }
  }, []);

  useEffect(() => { loadReport(); loadStats(); }, [loadReport, loadStats]);
  useEffect(() => { loadPatterns(); }, [loadPatterns]);

  // 模式表格列
  const patternColumns: ColumnsType<PatternItem> = [
    {
      title: '模式',
      dataIndex: 'pattern',
      width: 120,
    },
    {
      title: '出现次数',
      dataIndex: 'count',
      width: 90,
      sorter: (a, b) => a.count - b.count,
    },
    {
      title: '平均置信度',
      dataIndex: 'avg_confidence',
      width: 100,
      render: (v: number) => (
        <span style={{ color: v < 40 ? '#ef4444' : v < 60 ? '#f59e0b' : '#22c55e' }}>
          {v}%
        </span>
      ),
    },
    {
      title: '根因',
      dataIndex: 'root_cause_guess',
      width: 110,
      render: (v: string) => (
        <Tag color={ROOT_CAUSE_COLORS[v] || 'default'}>
          {ROOT_CAUSE_LABELS[v] || v}
        </Tag>
      ),
    },
    {
      title: '典型清单',
      dataIndex: 'typical_bill',
      width: 200,
      ellipsis: true,
    },
    {
      title: '匹配结果',
      dataIndex: 'typical_match',
      width: 200,
      ellipsis: true,
    },
    {
      title: '涉及省份',
      dataIndex: 'provinces',
      render: (v: string[]) => v?.slice(0, 3).join('、') + (v?.length > 3 ? '...' : ''),
    },
  ];

  // 省份统计列
  const provinceColumns: ColumnsType<ProvinceStats> = [
    { title: '省份', dataIndex: 'province', width: 80 },
    { title: '文件数', dataIndex: 'files', width: 80 },
    { title: '清单数', dataIndex: 'items', width: 80 },
    {
      title: '平均置信度',
      dataIndex: 'avg_confidence',
      width: 100,
      render: (v: number) => `${v}%`,
      sorter: (a, b) => a.avg_confidence - b.avg_confidence,
    },
    {
      title: '绿灯率',
      dataIndex: 'high_rate',
      width: 90,
      render: (v: number) => <span style={{ color: '#22c55e' }}>{v}%</span>,
      sorter: (a, b) => a.high_rate - b.high_rate,
    },
    {
      title: '红灯率',
      dataIndex: 'low_rate',
      width: 90,
      render: (v: number) => <span style={{ color: v > 30 ? '#ef4444' : '#f59e0b' }}>{v}%</span>,
      sorter: (a, b) => a.low_rate - b.low_rate,
    },
  ];

  // 专业统计列
  const specialtyColumns: ColumnsType<SpecialtyStats> = [
    { title: '专业', dataIndex: 'specialty', width: 100 },
    { title: '清单数', dataIndex: 'items', width: 80 },
    {
      title: '平均置信度',
      dataIndex: 'avg_confidence',
      width: 100,
      render: (v: number) => `${v}%`,
    },
    {
      title: '绿灯率',
      dataIndex: 'high_rate',
      width: 90,
      render: (v: number) => <span style={{ color: '#22c55e' }}>{v}%</span>,
    },
    {
      title: '红灯率',
      dataIndex: 'low_rate',
      width: 90,
      render: (v: number) => <span style={{ color: v > 30 ? '#ef4444' : '#f59e0b' }}>{v}%</span>,
    },
  ];

  if (!hasData && !loading) {
    return (
      <div>
        <h2>错误分析</h2>
        <Empty description="暂无错误分析数据，请先运行批量匹配并生成报告" />
      </div>
    );
  }

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>错误分析</h2>
        <Space>
          {reportDate && <span style={{ color: '#94a3b8', fontSize: 13 }}>报告时间: {reportDate}</span>}
          {algoVersion && <Tag>{algoVersion}</Tag>}
          <Button icon={<ReloadOutlined />} onClick={() => { loadReport(); loadPatterns(); loadStats(); }}>
            刷新
          </Button>
        </Space>
      </div>

      {/* 总体统计 */}
      {summary && (
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={4}>
            <Card size="small">
              <Statistic title="总清单数" value={summary.total_items} />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic
                title="平均置信度"
                value={summary.avg_confidence}
                suffix="%"
                valueStyle={{ color: summary.avg_confidence >= 60 ? '#22c55e' : '#ef4444' }}
              />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic
                title="绿灯率"
                value={summary.high_rate}
                suffix="%"
                prefix={<CheckCircleOutlined style={{ color: '#22c55e' }} />}
                valueStyle={{ color: '#22c55e' }}
              />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic
                title="红灯率"
                value={summary.low_rate}
                suffix="%"
                prefix={<WarningOutlined style={{ color: '#ef4444' }} />}
                valueStyle={{ color: '#ef4444' }}
              />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic title="绿灯数" value={summary.high_confidence} valueStyle={{ color: '#22c55e' }} />
            </Card>
          </Col>
          <Col span={4}>
            <Card size="small">
              <Statistic title="红灯数" value={summary.low_confidence} valueStyle={{ color: '#ef4444' }} />
            </Card>
          </Col>
        </Row>
      )}

      {/* 低置信度模式排行 */}
      <Card
        title="低置信度模式排行"
        style={{ marginBottom: 16 }}
        extra={
          <Select
            allowClear
            placeholder="根因筛选"
            style={{ width: 140 }}
            value={rootCauseFilter}
            onChange={v => { setRootCauseFilter(v); setPatternPage(1); }}
            options={Object.entries(ROOT_CAUSE_LABELS).map(([k, v]) => ({ value: k, label: v }))}
          />
        }
      >
        <Table
          columns={patternColumns}
          dataSource={patterns}
          rowKey="pattern"
          size="small"
          scroll={{ x: 900 }}
          pagination={{
            current: patternPage,
            pageSize: 20,
            total: patternTotal,
            showTotal: (t) => `共 ${t} 个模式`,
            onChange: setPatternPage,
          }}
        />
      </Card>

      {/* 省份 + 专业统计 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card title="按省份统计">
            <Table
              columns={provinceColumns}
              dataSource={provinces}
              rowKey="province"
              size="small"
              pagination={{ pageSize: 10 }}
              scroll={{ x: 520 }}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="按专业统计">
            <Table
              columns={specialtyColumns}
              dataSource={specialties}
              rowKey="specialty"
              size="small"
              pagination={{ pageSize: 10 }}
              scroll={{ x: 450 }}
            />
          </Card>
        </Col>
      </Row>

      {/* 覆盖矩阵 */}
      {Object.keys(coverage).length > 0 && (
        <Card title="省份覆盖矩阵">
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label={<Tag color="green">有定额库且有文件</Tag>}>
              {(coverage['有定额库且有文件'] || []).join('、') || '无'}
            </Descriptions.Item>
            <Descriptions.Item label={<Tag color="orange">有文件无定额库</Tag>}>
              {(coverage['有文件无定额库'] || []).join('、') || '无'}
            </Descriptions.Item>
            <Descriptions.Item label={<Tag color="default">有定额库无文件</Tag>}>
              {(coverage['有定额库无文件'] || []).join('、') || '无'}
            </Descriptions.Item>
          </Descriptions>
        </Card>
      )}
    </div>
  );
}
