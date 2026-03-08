/**
 * Tab4：跑分趋势（核心重构）
 *
 * 原来 111列×188行 的巨型表格，改为：
 * 筛选器 + 折线图 + 分页汇总表（可展开行看详情）+ 运行跑分按钮
 */

import { useEffect, useState, useMemo, useRef, useCallback } from 'react';
import { Card, Table, Tag, Space, Select, Button, Modal, Input, App } from 'antd';
import type { ColumnsType } from 'antd/es/table';
import { PlayCircleOutlined, LoadingOutlined } from '@ant-design/icons';
import { Line } from '@ant-design/charts';
import api from '../../../services/api';
import { TrendArrow, fmtRate } from './utils';
import type { BenchmarkRecord, DatasetMetrics } from './utils';

/** 带综合指标的跑分记录 */
interface EnrichedRecord extends BenchmarkRecord {
  overallGreen: number;
  overallRed: number;
}

export default function BenchmarkTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [benchmarkHistory, setBenchmarkHistory] = useState<BenchmarkRecord[]>([]);

  // 筛选器状态
  const [selectedDatasets, setSelectedDatasets] = useState<string[]>([]);

  // 跑分相关状态
  const [bmRunning, setBmRunning] = useState(false);
  const [bmProgress, setBmProgress] = useState('');
  const [bmModalOpen, setBmModalOpen] = useState(false);
  const [bmNote, setBmNote] = useState('');
  const pollTimer = useRef<ReturnType<typeof setInterval>>(undefined);

  useEffect(() => {
    loadData();
    return () => { if (pollTimer.current) clearInterval(pollTimer.current); };
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

  // 收集所有出现过的数据集名称（用于筛选器下拉选项）
  const allDatasetNames = useMemo(() => {
    const names = new Set<string>();
    for (const record of benchmarkHistory) {
      if (record.datasets) {
        for (const dsName of Object.keys(record.datasets)) {
          names.add(dsName);
        }
      }
    }
    return Array.from(names);
  }, [benchmarkHistory]);

  // 初始化筛选器：默认全选
  useEffect(() => {
    if (allDatasetNames.length > 0 && selectedDatasets.length === 0) {
      setSelectedDatasets(allDatasetNames);
    }
  }, [allDatasetNames]);

  // 折线图数据：按选中的数据集过滤
  const chartData = useMemo(() => {
    const points: { date: string; dataset: string; green_rate: number }[] = [];
    const filterSet = new Set(selectedDatasets);
    for (const record of benchmarkHistory) {
      if (!record.datasets) continue;
      const dateStr = record.date?.split(' ')[0] || '';
      for (const [dsName, metrics] of Object.entries(record.datasets)) {
        if (!filterSet.has(dsName)) continue;
        points.push({
          date: dateStr,
          dataset: dsName,
          green_rate: Math.round(metrics.green_rate * 1000) / 10,
        });
      }
    }
    return points;
  }, [benchmarkHistory, selectedDatasets]);

  // 为每条记录计算综合绿率和红率（按 total 加权平均）
  const enrichedHistory = useMemo<EnrichedRecord[]>(() => {
    return benchmarkHistory.map(record => {
      const entries = Object.values(record.datasets || {});
      const totalItems = entries.reduce((s, m) => s + m.total, 0);
      if (totalItems === 0) return { ...record, overallGreen: 0, overallRed: 0 };
      const weightedGreen = entries.reduce((s, m) => s + m.green_rate * m.total, 0) / totalItems;
      const weightedRed = entries.reduce((s, m) => s + m.red_rate * m.total, 0) / totalItems;
      return { ...record, overallGreen: weightedGreen, overallRed: weightedRed };
    });
  }, [benchmarkHistory]);

  // 汇总表列定义（精简为5列）
  const summaryColumns: ColumnsType<EnrichedRecord> = [
    {
      title: '#',
      key: '_index',
      width: 50,
      align: 'center',
      render: (_: unknown, __: EnrichedRecord, index: number) => index + 1,
    },
    {
      title: '日期',
      dataIndex: 'date',
      key: 'date',
      width: 120,
      render: (v: string) => v?.split(' ')[0] || v,
    },
    {
      title: '备注',
      dataIndex: 'note',
      key: 'note',
      ellipsis: true,
      render: (v: string) => v || '-',
    },
    {
      title: '综合绿率',
      key: 'overallGreen',
      width: 110,
      align: 'center',
      sorter: (a, b) => a.overallGreen - b.overallGreen,
      render: (_: unknown, record: EnrichedRecord, index: number) => {
        const prev = index > 0 ? enrichedHistory[index - 1]?.overallGreen : undefined;
        return (
          <span>
            <Tag color="green" style={{ margin: 0 }}>{fmtRate(record.overallGreen)}</Tag>
            <TrendArrow current={record.overallGreen} previous={prev} higherIsBetter />
          </span>
        );
      },
    },
    {
      title: '综合红率',
      key: 'overallRed',
      width: 110,
      align: 'center',
      sorter: (a, b) => a.overallRed - b.overallRed,
      render: (_: unknown, record: EnrichedRecord, index: number) => {
        const prev = index > 0 ? enrichedHistory[index - 1]?.overallRed : undefined;
        return (
          <span>
            <Tag
              color={record.overallRed > 0.05 ? 'red' : record.overallRed > 0 ? 'orange' : 'green'}
              style={{ margin: 0 }}
            >
              {fmtRate(record.overallRed)}
            </Tag>
            <TrendArrow current={record.overallRed} previous={prev} higherIsBetter={false} />
          </span>
        );
      },
    },
  ];

  // 展开行：该次跑分各数据集的详细指标
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
        title: '绿率', dataIndex: 'green_rate', key: 'green_rate', width: 80, align: 'center',
        render: (v: number) => <Tag color="green">{fmtRate(v)}</Tag>,
      },
      {
        title: '黄率', dataIndex: 'yellow_rate', key: 'yellow_rate', width: 80, align: 'center',
        render: (v: number) => <Tag color="orange">{fmtRate(v)}</Tag>,
      },
      {
        title: '红率', dataIndex: 'red_rate', key: 'red_rate', width: 80, align: 'center',
        render: (v: number) => (
          <Tag color={v > 0.05 ? 'red' : v > 0 ? 'orange' : 'green'}>{fmtRate(v)}</Tag>
        ),
      },
      {
        title: '经验命中', dataIndex: 'exp_hit_rate', key: 'exp_hit_rate', width: 90, align: 'center',
        render: (v: number) => fmtRate(v),
      },
      {
        title: '耗时', dataIndex: 'avg_time_sec', key: 'avg_time_sec', width: 80, align: 'center',
        render: (v: number) => v ? `${v.toFixed(1)}s` : '-',
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

  /** 轮询跑分任务状态 */
  const pollBenchmarkStatus = useCallback((taskId: string) => {
    pollTimer.current = setInterval(async () => {
      try {
        const res = await api.get(`/admin/analytics/benchmark-status/${taskId}`);
        const { state, progress, result, error } = res.data;
        if (state === 'PROGRESS' && progress) {
          setBmProgress(`正在跑 ${progress.dataset} (${progress.current + 1}/${progress.total})`);
        } else if (state === 'SUCCESS') {
          clearInterval(pollTimer.current!);
          setBmRunning(false);
          setBmProgress('');
          message.success(result?.message || '跑分完成');
          loadData(); // 重新加载跑分历史
        } else if (state === 'FAILURE') {
          clearInterval(pollTimer.current!);
          setBmRunning(false);
          setBmProgress('');
          message.error(`跑分失败: ${error || '未知错误'}`);
        }
      } catch {
        // 网络错误不终止轮询
      }
    }, 3000);
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
      {/* 筛选器 + 运行跑分按钮 */}
      <Card size="small">
        <Space wrap>
          <span style={{ color: '#666' }}>数据集筛选：</span>
          <Select
            mode="multiple"
            value={selectedDatasets}
            onChange={setSelectedDatasets}
            style={{ minWidth: 300 }}
            maxTagCount={3}
            placeholder="选择要对比的数据集"
            options={allDatasetNames.map(name => ({ value: name, label: name }))}
            allowClear
          />
          <span style={{ flex: 1 }} />
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
      </Card>

      {benchmarkHistory.length === 0 ? (
        <Card loading={loading}>
          <div style={{ textAlign: 'center', color: '#999', padding: 60 }}>
            暂无跑分历史数据
            <br />
            <span style={{ fontSize: 12 }}>点击"运行跑分"按钮开始第一次跑分</span>
          </div>
        </Card>
      ) : (
        <>
          {/* 折线图：各数据集绿率趋势 */}
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
                  label: { formatter: (v: string) => v.slice(5) },
                },
                y: {
                  title: '绿率 %',
                  labelFormatter: (v: number) => `${v}%`,
                },
              }}
              style={{ lineWidth: 2 }}
              point={{ size: 3 }}
              interaction={{ tooltip: true }}
              legend={{ position: 'top' }}
            />
          </Card>

          {/* 汇总表格（精简5列 + 可展开行） */}
          <Card title="跑分明细" loading={loading}>
            <Table
              rowKey={(_, index) => String(index)}
              dataSource={enrichedHistory}
              columns={summaryColumns}
              size="small"
              pagination={{ pageSize: 20, showSizeChanger: true, showTotal: total => `共 ${total} 条` }}
              expandable={{
                expandedRowRender,
                rowExpandable: (record) => Object.keys(record.datasets || {}).length > 0,
              }}
              locale={{ emptyText: '暂无数据' }}
            />
          </Card>
        </>
      )}

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
