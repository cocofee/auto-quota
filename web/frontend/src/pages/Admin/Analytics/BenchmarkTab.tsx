import { useEffect, useState, useMemo, useRef, useCallback } from 'react';
import { Card, Table, Tag, Space, Select, Button, Modal, Input, App, Tooltip } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { PlayCircleOutlined, LoadingOutlined, InfoCircleOutlined } from '@ant-design/icons';
import { Line } from '@ant-design/charts';
import api from '../../../services/api';
import { TrendArrow, fmtRate } from './utils';
import type { BenchmarkRecord, DatasetMetrics } from './utils';

interface EnrichedRecord extends BenchmarkRecord {
  overallGreen: number;
  overallRed: number;
}

export default function BenchmarkTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [benchmarkHistory, setBenchmarkHistory] = useState<BenchmarkRecord[]>([]);
  const [selectedDatasets, setSelectedDatasets] = useState<string[]>([]);
  const [bmRunning, setBmRunning] = useState(false);
  const [bmProgress, setBmProgress] = useState('');
  const [bmModalOpen, setBmModalOpen] = useState(false);
  const [bmNote, setBmNote] = useState('');
  const pollTimer = useRef<ReturnType<typeof setInterval>>(undefined);

  useEffect(() => {
    void loadData();
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const res = await api.get<{ items: BenchmarkRecord[] }>('/admin/analytics/benchmark-history');
      setBenchmarkHistory(res.data.items);
    } catch {
      message.error('加载跑分历史失败');
    } finally {
      setLoading(false);
    }
  };

  const allDatasetNames = useMemo(() => {
    const names = new Set<string>();
    for (const record of benchmarkHistory) {
      if (!record.datasets) continue;
      Object.keys(record.datasets).forEach((name) => names.add(name));
    }
    return Array.from(names);
  }, [benchmarkHistory]);

  useEffect(() => {
    if (allDatasetNames.length > 0 && selectedDatasets.length === 0) {
      setSelectedDatasets(allDatasetNames);
    }
  }, [allDatasetNames, selectedDatasets.length]);

  const chartData = useMemo(() => {
    const points: { date: string; dataset: string; green_rate: number }[] = [];
    const filterSet = new Set(selectedDatasets);
    for (const record of benchmarkHistory) {
      if (!record.datasets) continue;
      const date = record.date?.split(' ')[0] || '';
      for (const [dataset, metrics] of Object.entries(record.datasets)) {
        if (!filterSet.has(dataset)) continue;
        points.push({
          date,
          dataset,
          green_rate: Math.round(metrics.green_rate * 1000) / 10,
        });
      }
    }
    return points;
  }, [benchmarkHistory, selectedDatasets]);

  const enrichedHistory = useMemo<EnrichedRecord[]>(() => {
    return benchmarkHistory.map((record) => {
      const entries = Object.values(record.datasets || {});
      const totalItems = entries.reduce((sum, item) => sum + item.total, 0);
      if (totalItems === 0) {
        return { ...record, overallGreen: 0, overallRed: 0 };
      }
      const weightedGreen = entries.reduce((sum, item) => sum + item.green_rate * item.total, 0) / totalItems;
      const weightedRed = entries.reduce((sum, item) => sum + item.red_rate * item.total, 0) / totalItems;
      return { ...record, overallGreen: weightedGreen, overallRed: weightedRed };
    });
  }, [benchmarkHistory]);

  const summaryColumns: ColumnsType<EnrichedRecord> = [
    {
      title: '#',
      key: '_index',
      width: 50,
      align: 'center',
      render: (_value, _record, index) => index + 1,
    },
    {
      title: '日期',
      dataIndex: 'date',
      key: 'date',
      width: 120,
      render: (value: string) => value?.split(' ')[0] || value,
    },
    {
      title: '备注',
      dataIndex: 'note',
      key: 'note',
      ellipsis: true,
      render: (value: string) => value || '-',
    },
    {
      title: '数据集',
      key: 'dsCount',
      width: 80,
      align: 'center',
      render: (_value, record) => <Tag>{Object.keys(record.datasets || {}).length} 个</Tag>,
    },
    {
      title: '综合绿率',
      key: 'overallGreen',
      width: 120,
      align: 'center',
      sorter: (a, b) => a.overallGreen - b.overallGreen,
      render: (_value, record, index) => {
        const previous = index > 0 ? enrichedHistory[index - 1]?.overallGreen : undefined;
        return (
          <span>
            <Tag color="green" style={{ margin: 0 }}>{fmtRate(record.overallGreen)}</Tag>
            <TrendArrow current={record.overallGreen} previous={previous} higherIsBetter />
          </span>
        );
      },
    },
    {
      title: '综合红率',
      key: 'overallRed',
      width: 120,
      align: 'center',
      sorter: (a, b) => a.overallRed - b.overallRed,
      render: (_value, record, index) => {
        const previous = index > 0 ? enrichedHistory[index - 1]?.overallRed : undefined;
        return (
          <span>
            <Tag
              color={record.overallRed > 0.05 ? 'red' : record.overallRed > 0 ? 'orange' : 'green'}
              style={{ margin: 0 }}
            >
              {fmtRate(record.overallRed)}
            </Tag>
            <TrendArrow current={record.overallRed} previous={previous} higherIsBetter={false} />
          </span>
        );
      },
    },
  ];

  const expandedRowRender = (record: EnrichedRecord) => {
    const datasets = Object.entries(record.datasets || {}).map(([name, metrics]) => ({
      key: name,
      name,
      ...metrics,
    }));

    const detailColumns: ColumnsType<{ key: string; name: string } & DatasetMetrics> = [
      { title: '数据集', dataIndex: 'name', key: 'name', width: 200 },
      { title: '条数', dataIndex: 'total', key: 'total', width: 70, align: 'center' },
      {
        title: '绿率',
        dataIndex: 'green_rate',
        key: 'green_rate',
        width: 80,
        align: 'center',
        render: (value: number) => <Tag color="green">{fmtRate(value)}</Tag>,
      },
      {
        title: '黄率',
        dataIndex: 'yellow_rate',
        key: 'yellow_rate',
        width: 80,
        align: 'center',
        render: (value: number) => <Tag color="orange">{fmtRate(value)}</Tag>,
      },
      {
        title: '红率',
        dataIndex: 'red_rate',
        key: 'red_rate',
        width: 80,
        align: 'center',
        render: (value: number) => (
          <Tag color={value > 0.05 ? 'red' : value > 0 ? 'orange' : 'green'}>{fmtRate(value)}</Tag>
        ),
      },
      {
        title: '经验命中',
        dataIndex: 'exp_hit_rate',
        key: 'exp_hit_rate',
        width: 90,
        align: 'center',
        render: (value: number) => fmtRate(value),
      },
      {
        title: '平均耗时',
        dataIndex: 'avg_time_sec',
        key: 'avg_time_sec',
        width: 90,
        align: 'center',
        render: (value: number) => (value ? `${value.toFixed(1)}s` : '-'),
      },
    ];

    return (
      <Table
        dataSource={datasets}
        columns={detailColumns}
        size="small"
        pagination={false}
        style={{ margin: 0 }}
      />
    );
  };

  const pollBenchmarkStatus = useCallback((taskId: string) => {
    pollTimer.current = setInterval(async () => {
      try {
        const res = await api.get(`/admin/analytics/benchmark-status/${taskId}`);
        const { state, progress, result, error } = res.data;
        if (state === 'PROGRESS' && progress) {
          setBmProgress(`正在跑 ${progress.dataset}（${progress.current + 1}/${progress.total}）`);
        } else if (state === 'SUCCESS') {
          clearInterval(pollTimer.current!);
          setBmRunning(false);
          setBmProgress('');
          message.success(result?.message || '跑分完成');
          void loadData();
        } else if (state === 'FAILURE') {
          clearInterval(pollTimer.current!);
          setBmRunning(false);
          setBmProgress('');
          message.error(`跑分失败：${error || '未知错误'}`);
        }
      } catch {
        // 保持轮询，忽略瞬时网络错误
      }
    }, 3000);
  }, [message]);

  const startBenchmark = async () => {
    setBmModalOpen(false);
    setBmRunning(true);
    setBmProgress('正在启动...');
    try {
      const res = await api.post('/admin/analytics/run-benchmark', {
        mode: 'search',
        note: bmNote.trim(),
      });
      pollBenchmarkStatus(res.data.task_id);
    } catch {
      setBmRunning(false);
      setBmProgress('');
      message.error('启动跑分失败');
    }
    setBmNote('');
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Card size="small">
        <Space wrap>
          <span style={{ color: '#666' }}>数据集筛选：</span>
          <Select
            mode="multiple"
            value={selectedDatasets}
            onChange={setSelectedDatasets}
            style={{ minWidth: 320 }}
            maxTagCount={3}
            placeholder="选择要对比的数据集"
            options={allDatasetNames.map((name) => ({ value: name, label: name }))}
            allowClear
          />
        </Space>
      </Card>

      {benchmarkHistory.length === 0 ? (
        <Card
          loading={loading}
          extra={(
            <Button
              type="primary"
              icon={bmRunning ? <LoadingOutlined /> : <PlayCircleOutlined />}
              disabled={bmRunning}
              onClick={() => setBmModalOpen(true)}
              size="small"
            >
              {bmRunning ? '跑分中...' : '运行跑分'}
            </Button>
          )}
        >
          <div style={{ textAlign: 'center', color: '#999', padding: 60 }}>
            还没有跑分历史
            <br />
            <span style={{ fontSize: 12 }}>需要时再运行一次跑分，这里才会开始显示趋势。</span>
          </div>
        </Card>
      ) : (
        <>
          <Card title="绿率趋势" loading={loading}>
            <Line
              data={chartData}
              xField="date"
              yField="green_rate"
              colorField="dataset"
              height={320}
              axis={{
                x: {
                  labelAutoRotate: true,
                  label: { formatter: (value: string) => value.slice(5) },
                },
                y: {
                  title: '绿率 %',
                  labelFormatter: (value: number) => `${value}%`,
                },
              }}
              style={{ lineWidth: 2 }}
              point={{ size: 3 }}
              interaction={{ tooltip: true }}
              legend={{ position: 'top' }}
            />
          </Card>

          <Card
            title="跑分明细"
            loading={loading}
            extra={(
              <Space>
                {bmRunning ? (
                  <span style={{ fontSize: 12, color: '#1677ff' }}>
                    <LoadingOutlined style={{ marginRight: 4 }} />
                    {bmProgress}
                  </span>
                ) : null}
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
            )}
          >
            <Table
              rowKey={(_record, index) => String(index)}
              dataSource={enrichedHistory}
              columns={summaryColumns}
              size="small"
              pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (total) => `共 ${total} 条` }}
              expandable={{
                expandedRowRender,
                rowExpandable: (record) => Object.keys(record.datasets || {}).length > 0,
                expandIcon: ({ expanded, onExpand, record }) =>
                  Object.keys(record.datasets || {}).length > 0 ? (
                    <Tooltip title="点击查看各数据集详情">
                      <InfoCircleOutlined
                        style={{ color: expanded ? '#1677ff' : '#999', cursor: 'pointer' }}
                        onClick={(event) => onExpand(record, event)}
                      />
                    </Tooltip>
                  ) : null,
              }}
              locale={{ emptyText: '暂时没有可展示的跑分记录' }}
            />
          </Card>
        </>
      )}

      <Modal
        title="运行跑分"
        open={bmModalOpen}
        onOk={startBenchmark}
        onCancel={() => setBmModalOpen(false)}
        okText="开始跑分"
        cancelText="取消"
      >
        <p style={{ color: '#666', marginBottom: 12 }}>
          将对所有可用测试数据集运行一次完整跑分，通常需要 5 到 15 分钟。
        </p>
        <Input
          placeholder="这次改了什么？可选，例如：优化了管道参数匹配"
          value={bmNote}
          onChange={(event) => setBmNote(event.target.value)}
          onPressEnter={startBenchmark}
        />
      </Modal>
    </Space>
  );
}
