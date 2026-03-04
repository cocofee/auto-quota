/**
 * 管理员 — 批量任务看板
 *
 * 显示批量扫描和匹配的处理状态：
 * 1. 状态概览卡片（总文件/已扫描/已匹配/错误）
 * 2. 格式/省份/专业分布
 * 3. 文件列表（分页+筛选）
 * 4. 操作按钮（启动扫描/启动匹配/重跑文件）
 */

import { useEffect, useState, useCallback } from 'react';
import {
  Card, Row, Col, Statistic, Table, Tag, Space, App, Button,
  Select, Input, Progress,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ScanOutlined, PlayCircleOutlined, ReloadOutlined,
  FileExcelOutlined, CheckCircleOutlined, CloseCircleOutlined,
  ClockCircleOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

// 状态标签颜色映射
const STATUS_COLORS: Record<string, string> = {
  pending: 'default',
  scanned: 'processing',
  matched: 'success',
  skipped: 'warning',
  error: 'error',
};

// 状态中文名
const STATUS_LABELS: Record<string, string> = {
  pending: '待扫描',
  scanned: '已扫描',
  matched: '已匹配',
  skipped: '已跳过',
  error: '错误',
};

// 格式中文名
const FORMAT_LABELS: Record<string, string> = {
  standard_bill: '标准清单',
  work_list: '工作量清单',
  equipment_list: '设备材料清单',
  summary_only: '纯汇总',
  unknown: '未识别',
  non_excel: '非Excel',
};

interface BatchStatus {
  total: number;
  by_status: Record<string, number>;
  by_format: Record<string, number>;
  by_province: Record<string, number>;
  by_specialty: Record<string, number>;
}

interface FileItem {
  file_path: string;
  file_name: string;
  file_size: number;
  province: string;
  specialty: string;
  format: string;
  status: string;
  skip_reason: string;
  error_msg: string;
  estimated_items: number;
  scan_time: string;
  match_time: string;
  algo_version: string;
}

export default function BatchDashboard() {
  const { message } = App.useApp();
  const [status, setStatus] = useState<BatchStatus | null>(null);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(50);
  const [loading, setLoading] = useState(false);
  const [taskLoading, setTaskLoading] = useState(false);

  // 筛选条件
  const [filterStatus, setFilterStatus] = useState<string | undefined>();
  const [filterFormat, setFilterFormat] = useState<string | undefined>();
  const [filterProvince, setFilterProvince] = useState<string | undefined>();
  const [filterKeyword, setFilterKeyword] = useState('');

  // 扫描目录（用户可自定义，留空用后端默认值）
  const [scanDir, setScanDir] = useState('');
  const [defaultDir, setDefaultDir] = useState('');

  // 加载状态概览
  const loadStatus = useCallback(async () => {
    try {
      const res = await api.get('/admin/batch/status');
      setStatus(res.data);
    } catch {
      // 静默处理
    }
  }, []);

  // 加载文件列表
  const loadFiles = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, any> = { page, page_size: pageSize };
      if (filterStatus) params.status = filterStatus;
      if (filterFormat) params.format = filterFormat;
      if (filterProvince) params.province = filterProvince;
      if (filterKeyword) params.keyword = filterKeyword;

      const res = await api.get('/admin/batch/files', { params });
      setFiles(res.data.items);
      setTotal(res.data.total);
    } catch {
      // 静默处理
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, filterStatus, filterFormat, filterProvince, filterKeyword]);

  useEffect(() => { loadStatus(); }, [loadStatus]);
  useEffect(() => { loadFiles(); }, [loadFiles]);

  // 获取默认扫描目录（页面加载时调一次）
  useEffect(() => {
    api.get('/admin/batch/scan-dirs')
      .then(res => { setDefaultDir(res.data.default || ''); })
      .catch(() => {});
  }, []);

  // 启动扫描
  const handleScan = async () => {
    setTaskLoading(true);
    try {
      const payload: Record<string, any> = {};
      if (scanDir.trim()) {
        payload.directory = scanDir.trim();
      }
      // directory 不传时后端自动检测环境（容器用/app/raw_files，本地用F:/jarvis）
      const res = await api.post('/admin/batch/scan', payload);
      message.success(`扫描已启动，任务ID: ${res.data.task_id}`);
      // 延迟刷新
      setTimeout(() => { loadStatus(); loadFiles(); }, 3000);
    } catch (e: any) {
      message.error(`启动扫描失败: ${e.response?.data?.detail || e.message}`);
    } finally {
      setTaskLoading(false);
    }
  };

  // 启动批量匹配
  const handleRun = async () => {
    setTaskLoading(true);
    try {
      const res = await api.post('/admin/batch/run', {
        format: filterFormat,
        province: filterProvince,
      });
      message.success(`批量匹配已启动，任务ID: ${res.data.task_id}`);
      setTimeout(() => { loadStatus(); loadFiles(); }, 5000);
    } catch (e: any) {
      message.error(`启动匹配失败: ${e.response?.data?.detail || e.message}`);
    } finally {
      setTaskLoading(false);
    }
  };

  // 重跑单个文件
  const handleRetry = async (filePath: string) => {
    try {
      await api.post('/admin/batch/retry', { file_path: filePath });
      message.success('已重置为待匹配状态');
      loadFiles();
    } catch (e: any) {
      message.error(`重跑失败: ${e.response?.data?.detail || e.message}`);
    }
  };

  // 表格列定义
  const columns: ColumnsType<FileItem> = [
    {
      title: '文件名',
      dataIndex: 'file_name',
      width: 280,
      ellipsis: true,
    },
    {
      title: '省份',
      dataIndex: 'province',
      width: 80,
      render: (v: string) => v || '-',
    },
    {
      title: '专业',
      dataIndex: 'specialty',
      width: 80,
      render: (v: string) => v || '-',
    },
    {
      title: '格式',
      dataIndex: 'format',
      width: 110,
      render: (v: string) => FORMAT_LABELS[v] || v || '-',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (v: string) => (
        <Tag color={STATUS_COLORS[v] || 'default'}>
          {STATUS_LABELS[v] || v}
        </Tag>
      ),
    },
    {
      title: '预估条数',
      dataIndex: 'estimated_items',
      width: 90,
      align: 'right',
    },
    {
      title: '备注',
      width: 200,
      ellipsis: true,
      render: (_: any, record: FileItem) => {
        if (record.error_msg) return <span style={{ color: '#ef4444' }}>{record.error_msg}</span>;
        if (record.skip_reason) return <span style={{ color: '#f59e0b' }}>{record.skip_reason}</span>;
        return '-';
      },
    },
    {
      title: '操作',
      width: 80,
      render: (_: any, record: FileItem) => {
        if (record.status === 'matched' || record.status === 'error') {
          return (
            <Button size="small" onClick={() => handleRetry(record.file_path)}>
              重跑
            </Button>
          );
        }
        return null;
      },
    },
  ];

  // 概览统计
  const byStatus = status?.by_status || {};
  const totalFiles = status?.total || 0;
  const matchedCount = byStatus['matched'] || 0;
  const scannedCount = byStatus['scanned'] || 0;
  const errorCount = byStatus['error'] || 0;
  const skippedCount = byStatus['skipped'] || 0;

  return (
    <div>
      <div style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h2 style={{ margin: 0 }}>批量任务看板</h2>
        <Space>
          <Input
            placeholder={defaultDir || '扫描目录（默认自动检测）'}
            style={{ width: 260 }}
            value={scanDir}
            onChange={e => setScanDir(e.target.value)}
            allowClear
          />
          <Button
            icon={<ScanOutlined />}
            onClick={handleScan}
            loading={taskLoading}
          >
            启动扫描
          </Button>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={handleRun}
            loading={taskLoading}
          >
            启动匹配
          </Button>
          <Button
            icon={<ReloadOutlined />}
            onClick={() => { loadStatus(); loadFiles(); }}
          >
            刷新
          </Button>
        </Space>
      </div>

      {/* 概览卡片 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={6}>
          <Card>
            <Statistic
              title="总文件数"
              value={totalFiles}
              prefix={<FileExcelOutlined />}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="已匹配"
              value={matchedCount}
              prefix={<CheckCircleOutlined style={{ color: '#22c55e' }} />}
              valueStyle={{ color: '#22c55e' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="待匹配"
              value={scannedCount}
              prefix={<ClockCircleOutlined style={{ color: '#3b82f6' }} />}
              valueStyle={{ color: '#3b82f6' }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="错误"
              value={errorCount}
              prefix={<CloseCircleOutlined style={{ color: '#ef4444' }} />}
              valueStyle={{ color: errorCount > 0 ? '#ef4444' : undefined }}
            />
          </Card>
        </Col>
      </Row>

      {/* 处理进度条 */}
      {totalFiles > 0 && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
            <span>处理进度:</span>
            <Progress
              percent={Math.round(((matchedCount + skippedCount) / totalFiles) * 100)}
              style={{ flex: 1 }}
              format={(p) => `${p}% (${matchedCount + skippedCount}/${totalFiles})`}
            />
          </div>
        </Card>
      )}

      {/* 筛选栏 */}
      <Card size="small" style={{ marginBottom: 16 }}>
        <Space wrap>
          <Select
            allowClear
            placeholder="状态"
            style={{ width: 120 }}
            value={filterStatus}
            onChange={v => { setFilterStatus(v); setPage(1); }}
            options={Object.entries(STATUS_LABELS).map(([k, v]) => ({ value: k, label: v }))}
          />
          <Select
            allowClear
            placeholder="格式"
            style={{ width: 140 }}
            value={filterFormat}
            onChange={v => { setFilterFormat(v); setPage(1); }}
            options={Object.entries(FORMAT_LABELS).map(([k, v]) => ({ value: k, label: v }))}
          />
          <Select
            allowClear
            showSearch
            placeholder="省份"
            style={{ width: 120 }}
            value={filterProvince}
            onChange={v => { setFilterProvince(v); setPage(1); }}
            options={Object.keys(status?.by_province || {}).map(p => ({ value: p, label: p }))}
          />
          <Input.Search
            placeholder="文件名搜索"
            style={{ width: 200 }}
            onSearch={v => { setFilterKeyword(v); setPage(1); }}
            allowClear
          />
        </Space>
      </Card>

      {/* 文件列表 */}
      <Card>
        <Table
          columns={columns}
          dataSource={files}
          rowKey="file_path"
          loading={loading}
          size="small"
          scroll={{ x: 1100 }}
          pagination={{
            current: page,
            pageSize,
            total,
            showTotal: (t) => `共 ${t} 个文件`,
            onChange: setPage,
          }}
        />
      </Card>
    </div>
  );
}
