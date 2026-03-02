/**
 * 管理员 — 准确率分析
 *
 * 显示匹配系统的整体表现：
 * 1. 概览统计卡片
 * 2. 省份分布
 * 3. 专业统计（置信度对比）
 * 4. 任务趋势
 * 5. 算法跑分趋势（Benchmark历史）
 */

import { useEffect, useState, useMemo, useRef, useCallback } from 'react';
import {
  Card, Row, Col, Statistic, Table, Tag, Space, App, Progress, Tooltip,
  Button, Modal, Input,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CheckCircleOutlined, BarChartOutlined, FileTextOutlined,
  UserOutlined, SafetyOutlined, ExperimentOutlined,
  ArrowUpOutlined, ArrowDownOutlined, MinusOutlined,
  PlayCircleOutlined, LoadingOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { COLORS, GREEN_THRESHOLD, YELLOW_THRESHOLD } from '../../utils/experience';

interface OverviewData {
  total_tasks: number;
  completed_tasks: number;
  total_results: number;
  high_confidence: number;
  mid_confidence: number;
  low_confidence: number;
  avg_confidence: number;
  confirmed_results: number;
  total_users: number;
}

interface ProvinceItem {
  province: string;
  task_count: number;
}

interface SpecialtyItem {
  specialty: string;
  count: number;
  avg_confidence: number;
}

interface TrendItem {
  date: string;
  task_count: number;
}

/* Benchmark 历史记录的数据结构 */
interface DatasetMetrics {
  total: number;
  skip_measure?: number;
  green_rate: number;
  yellow_rate: number;
  red_rate: number;
  exp_hit_rate: number;
  fallback_rate: number;
  avg_time_sec: number;
}

interface BenchmarkRecord {
  version: string;
  date: string;
  mode: string;
  note?: string;
  datasets: Record<string, DatasetMetrics>;
}

/* 趋势箭头：对比前一次跑分的某个指标 */
function TrendArrow({ current, previous, higherIsBetter }: {
  current: number;
  previous: number | undefined;
  higherIsBetter: boolean;  // true = 数值越高越好（如绿率），false = 越低越好（如红率）
}) {
  if (previous === undefined) {
    // 第一条记录，没有对比对象
    return null;
  }
  const diff = current - previous;
  if (Math.abs(diff) < 0.001) {
    // 变化太小，视为不变
    return <MinusOutlined style={{ color: '#999', fontSize: 10, marginLeft: 4 }} />;
  }
  const isGood = higherIsBetter ? diff > 0 : diff < 0;
  const diffPp = `${diff > 0 ? '+' : ''}${(diff * 100).toFixed(1)}pp`;
  return (
    <Tooltip title={diffPp}>
      {isGood
        ? <ArrowUpOutlined style={{ color: COLORS.greenSolid, fontSize: 10, marginLeft: 4 }} />
        : <ArrowDownOutlined style={{ color: COLORS.redSolid, fontSize: 10, marginLeft: 4 }} />
      }
    </Tooltip>
  );
}

/* 格式化比率为百分比字符串 */
function fmtRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`;
}

export default function AnalyticsPage() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [provinces, setProvinces] = useState<ProvinceItem[]>([]);
  const [specialties, setSpecialties] = useState<SpecialtyItem[]>([]);
  const [trends, setTrends] = useState<TrendItem[]>([]);
  const [benchmarkHistory, setBenchmarkHistory] = useState<BenchmarkRecord[]>([]);

  // 跑分相关状态
  const [bmRunning, setBmRunning] = useState(false);           // 是否正在跑分
  const [bmProgress, setBmProgress] = useState('');            // 进度文字（如"正在跑 B2_电气 (2/4)"）
  const [bmModalOpen, setBmModalOpen] = useState(false);       // 确认弹窗是否打开
  const [bmNote, setBmNote] = useState('');                    // 用户输入的备注
  const pollTimer = useRef<ReturnType<typeof setInterval>>(undefined);  // 轮询定时器

  useEffect(() => {
    loadAll();
  }, []);

  const loadAll = async () => {
    setLoading(true);
    try {
      const [ovRes, provRes, specRes, trendRes, bmRes] = await Promise.all([
        api.get<OverviewData>('/admin/analytics/overview'),
        api.get<{ items: ProvinceItem[] }>('/admin/analytics/by-province'),
        api.get<{ items: SpecialtyItem[] }>('/admin/analytics/by-specialty'),
        api.get<{ items: TrendItem[] }>('/admin/analytics/trends'),
        api.get<{ items: BenchmarkRecord[] }>('/admin/analytics/benchmark-history'),
      ]);
      setOverview(ovRes.data);
      setProvinces(provRes.data.items);
      setSpecialties(specRes.data.items);
      setTrends(trendRes.data.items);
      setBenchmarkHistory(bmRes.data.items);
    } catch {
      message.error('加载分析数据失败');
    } finally {
      setLoading(false);
    }
  };

  // 组件卸载时清理轮询定时器
  useEffect(() => {
    return () => { if (pollTimer.current) clearInterval(pollTimer.current); };
  }, []);

  /** 轮询跑分任务状态 */
  const pollBenchmarkStatus = useCallback((taskId: string) => {
    pollTimer.current = setInterval(async () => {
      try {
        const res = await api.get(`/admin/analytics/benchmark-status/${taskId}`);
        const { state, progress, result, error } = res.data;

        if (state === 'PROGRESS' && progress) {
          // 正在跑分中，更新进度文字
          setBmProgress(`正在跑 ${progress.dataset} (${progress.current + 1}/${progress.total})`);
        } else if (state === 'SUCCESS') {
          // 跑分完成
          clearInterval(pollTimer.current!);
          setBmRunning(false);
          setBmProgress('');
          message.success(result?.message || '跑分完成');
          // 刷新历史表格
          const bmRes = await api.get<{ items: BenchmarkRecord[] }>('/admin/analytics/benchmark-history');
          setBenchmarkHistory(bmRes.data.items);
        } else if (state === 'FAILURE') {
          // 跑分失败
          clearInterval(pollTimer.current!);
          setBmRunning(false);
          setBmProgress('');
          message.error(`跑分失败: ${error || '未知错误'}`);
        }
        // PENDING 状态继续等待
      } catch {
        // 网络错误不终止轮询，等下次重试
      }
    }, 3000); // 每3秒轮询一次
  }, [message]);

  /** 确认并启动跑分 */
  const startBenchmark = async () => {
    setBmModalOpen(false);
    setBmRunning(true);
    setBmProgress('正在启动...');

    try {
      const res = await api.post('/admin/analytics/run-benchmark', {
        mode: 'search',
        note: bmNote.trim(),
      });
      const { task_id } = res.data;
      // 开始轮询任务状态
      pollBenchmarkStatus(task_id);
    } catch {
      setBmRunning(false);
      setBmProgress('');
      message.error('启动跑分失败');
    }

    setBmNote(''); // 清空备注
  };

  // 置信度分布百分比
  const totalResults = overview?.total_results || 1;
  const highPct = Math.round(((overview?.high_confidence || 0) / totalResults) * 100);
  const midPct = Math.round(((overview?.mid_confidence || 0) / totalResults) * 100);
  const lowPct = Math.round(((overview?.low_confidence || 0) / totalResults) * 100);

  // 收集所有出现过的数据集名称（用于动态列）
  const datasetNames = useMemo(() => {
    const names = new Set<string>();
    for (const record of benchmarkHistory) {
      for (const dsName of Object.keys(record.datasets)) {
        names.add(dsName);
      }
    }
    return Array.from(names);
  }, [benchmarkHistory]);

  // Benchmark 历史表格列定义（动态生成）
  const benchmarkColumns = useMemo<ColumnsType<BenchmarkRecord>>(() => {
    // 基础列：序号、日期、备注
    const baseCols: ColumnsType<BenchmarkRecord> = [
      {
        title: '#',
        key: '_index',
        width: 45,
        align: 'center',
        render: (_: unknown, __: BenchmarkRecord, index: number) => index + 1,
      },
      {
        title: '日期',
        dataIndex: 'date',
        key: 'date',
        width: 140,
        render: (v: string) => {
          // 只显示日期部分（去掉时间）
          return v?.split(' ')[0] || v;
        },
      },
      {
        title: '备注',
        dataIndex: 'note',
        key: 'note',
        width: 180,
        ellipsis: true,
        render: (v: string) => v || '-',
      },
    ];

    // 为每个数据集生成 绿率 + 红率 两列
    const dsCols: ColumnsType<BenchmarkRecord> = [];
    for (const dsName of datasetNames) {
      // 数据集名称简化显示（去掉 B1_ B2_ 等前缀里的下划线后面部分太长时截断）
      const shortName = dsName.replace(/^B\d+_/, '');
      dsCols.push({
        title: <Tooltip title={dsName}>{shortName}</Tooltip>,
        key: dsName,
        children: [
          {
            title: '绿率',
            key: `${dsName}_green`,
            width: 65,
            align: 'center',
            render: (_: unknown, record: BenchmarkRecord, index: number) => {
              const metrics = record.datasets[dsName];
              if (!metrics) return '-';
              const prev = index > 0 ? benchmarkHistory[index - 1]?.datasets[dsName]?.green_rate : undefined;
              return (
                <span>
                  <Tag color="green" style={{ margin: 0 }}>{fmtRate(metrics.green_rate)}</Tag>
                  <TrendArrow current={metrics.green_rate} previous={prev} higherIsBetter />
                </span>
              );
            },
          },
          {
            title: '红率',
            key: `${dsName}_red`,
            width: 65,
            align: 'center',
            render: (_: unknown, record: BenchmarkRecord, index: number) => {
              const metrics = record.datasets[dsName];
              if (!metrics) return '-';
              const prev = index > 0 ? benchmarkHistory[index - 1]?.datasets[dsName]?.red_rate : undefined;
              return (
                <span>
                  <Tag color={metrics.red_rate > 0.05 ? 'red' : metrics.red_rate > 0 ? 'orange' : 'green'}
                    style={{ margin: 0 }}>
                    {fmtRate(metrics.red_rate)}
                  </Tag>
                  <TrendArrow current={metrics.red_rate} previous={prev} higherIsBetter={false} />
                </span>
              );
            },
          },
        ],
      });
    }

    return [...baseCols, ...dsCols];
  }, [datasetNames, benchmarkHistory]);

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 概览卡片 */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="总任务" value={overview?.total_tasks || 0} prefix={<FileTextOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="已完成" value={overview?.completed_tasks || 0} prefix={<CheckCircleOutlined />} valueStyle={{ color: COLORS.greenSolid }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="总匹配条数" value={overview?.total_results || 0} prefix={<BarChartOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic
              title="平均置信度"
              value={overview?.avg_confidence || 0}
              suffix="%"
              prefix={<ExperimentOutlined />}
              valueStyle={{
                color: (overview?.avg_confidence || 0) >= GREEN_THRESHOLD ? COLORS.greenSolid
                  : (overview?.avg_confidence || 0) >= YELLOW_THRESHOLD ? COLORS.yellowSolid : COLORS.redSolid,
              }}
            />
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="总用户" value={overview?.total_users || 0} prefix={<UserOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="已确认结果" value={overview?.confirmed_results || 0} prefix={<SafetyOutlined />} valueStyle={{ color: '#1677ff' }} />
          </Card>
        </Col>
        <Col xs={24} sm={12}>
          <Card loading={loading} title="置信度分布">
            <Space direction="vertical" style={{ width: '100%' }}>
              <div>
                <span style={{ display: 'inline-block', width: 80 }}>高置信度</span>
                <Progress percent={highPct} strokeColor={COLORS.greenSolid} format={() => `${overview?.high_confidence || 0}条`} />
              </div>
              <div>
                <span style={{ display: 'inline-block', width: 80 }}>中置信度</span>
                <Progress percent={midPct} strokeColor={COLORS.yellowSolid} format={() => `${overview?.mid_confidence || 0}条`} />
              </div>
              <div>
                <span style={{ display: 'inline-block', width: 80 }}>低置信度</span>
                <Progress percent={lowPct} strokeColor={COLORS.redSolid} format={() => `${overview?.low_confidence || 0}条`} />
              </div>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* 省份分布 + 专业统计 */}
      <Row gutter={16}>
        <Col span={12}>
          <Card title="按省份统计" loading={loading}>
            <Table
              rowKey="province"
              dataSource={provinces}
              size="small"
              pagination={false}
              columns={[
                { title: '省份', dataIndex: 'province', key: 'province' },
                { title: '任务数', dataIndex: 'task_count', key: 'task_count', width: 80 },
              ]}
              locale={{ emptyText: '暂无数据' }}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="按专业统计" loading={loading}>
            <Table
              rowKey="specialty"
              dataSource={specialties}
              size="small"
              pagination={false}
              columns={[
                { title: '专业', dataIndex: 'specialty', key: 'specialty' },
                { title: '条数', dataIndex: 'count', key: 'count', width: 60 },
                {
                  title: '平均置信度',
                  dataIndex: 'avg_confidence',
                  key: 'avg_confidence',
                  width: 110,
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
        </Col>
      </Row>

      {/* 任务趋势 */}
      <Card title="最近任务趋势" loading={loading}>
        {trends.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>暂无完成的任务数据</div>
        ) : (
          <Table
            rowKey="date"
            dataSource={trends}
            size="small"
            pagination={false}
            columns={[
              { title: '日期', dataIndex: 'date', key: 'date' },
              {
                title: '完成任务数',
                dataIndex: 'task_count',
                key: 'task_count',
                width: 120,
                render: (v: number) => (
                  <Progress
                    percent={Math.min(v * 20, 100)}
                    format={() => `${v}`}
                    size="small"
                  />
                ),
              },
            ]}
          />
        )}
      </Card>

      {/* 算法跑分趋势（Benchmark历史） */}
      <Card
        title="算法跑分趋势"
        loading={loading}
        extra={
          <Space>
            {bmRunning && (
              <span style={{ fontSize: 12, color: '#1677ff' }}>
                <LoadingOutlined style={{ marginRight: 4 }} />
                {bmProgress}
              </span>
            )}
            <Button
              type="primary"
              icon={bmRunning ? <LoadingOutlined /> : <PlayCircleOutlined />}
              disabled={bmRunning}
              onClick={() => setBmModalOpen(true)}
              size="small"
            >
              {bmRunning ? '跑分中...' : '运行跑分'}
            </Button>
          </Space>
        }
      >
        {benchmarkHistory.length === 0 ? (
          <div style={{ textAlign: 'center', color: '#999', padding: 40 }}>
            暂无跑分历史数据
            <br />
            <span style={{ fontSize: 12 }}>
              点击右上角"运行跑分"按钮开始第一次跑分
            </span>
          </div>
        ) : (
          <Table
            rowKey={(_, index) => String(index)}
            dataSource={benchmarkHistory}
            columns={benchmarkColumns}
            size="small"
            pagination={false}
            bordered
            scroll={{ x: 'max-content' }}
            locale={{ emptyText: '暂无数据' }}
          />
        )}
      </Card>

      {/* 跑分确认弹窗 */}
      <Modal
        title="运行跑分"
        open={bmModalOpen}
        onOk={startBenchmark}
        onCancel={() => setBmModalOpen(false)}
        okText="开始跑分"
        cancelText="取消"
      >
        <p style={{ color: '#666', marginBottom: 12 }}>
          将对所有可用的测试数据集运行一次完整跑分，耗时约5-15分钟。
        </p>
        <Input
          placeholder="这次改了什么？（选填，如：优化了管道参数匹配）"
          value={bmNote}
          onChange={e => setBmNote(e.target.value)}
          onPressEnter={startBenchmark}
        />
      </Modal>
    </Space>
  );
}
