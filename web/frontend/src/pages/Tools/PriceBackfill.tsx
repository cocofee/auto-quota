/**
 * 智能填价页面
 *
 * 上传甲方原始清单 + 广联达导出文件 → 自动匹配价格 → 下载已回填的Excel。
 * 流程：上传两个文件 → 预览映射 → 确认后执行 → 下载结果。
 */

import { useState } from 'react';
import { Card, Upload, Button, Table, Tag, Space, App, Statistic, Row, Col, Result } from 'antd';
import {
  InboxOutlined,
  EyeOutlined,
  DownloadOutlined,
  DollarOutlined,
  CheckCircleOutlined,
  FileExcelOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { utils as xlsxUtils, writeFile as xlsxWriteFile } from 'xlsx';
import api from '../../services/api';
import { getErrorMessage } from '../../utils/error';

const { Dragger } = Upload;

// 映射结果的类型
interface MappingItem {
  row: number;
  original_name: string;
  matched_name: string | null;
  matched_row: number | null;
  matched_index: string | null;
  unit_price: number | null;
  total_price: number | null;
  match_method: string;
  warnings: string[];
}

interface PreviewResult {
  original_count: number;
  gld_count: number;
  mapping: MappingItem[];
  stats: {
    total: number;
    matched_by_index: number;
    matched_by_name: number;
    unmatched: number;
  };
}

export default function PriceBackfill() {
  const { message } = App.useApp();

  // 两个文件的上传状态
  const [originalFile, setOriginalFile] = useState<UploadFile[]>([]);
  const [gldFile, setGldFile] = useState<UploadFile[]>([]);

  // 预览和执行状态
  const [previewLoading, setPreviewLoading] = useState(false);
  const [executeLoading, setExecuteLoading] = useState(false);
  const [previewResult, setPreviewResult] = useState<PreviewResult | null>(null);

  // 下载链接状态（填价完成后显示）
  const [downloadInfo, setDownloadInfo] = useState<{ url: string; filename: string } | null>(null);

  // 预览映射
  const handlePreview = async () => {
    if (!originalFile.length || !gldFile.length) {
      message.warning('请先上传两个文件');
      return;
    }

    const formData = new FormData();
    formData.append('original_file', originalFile[0].originFileObj as File);
    formData.append('gld_file', gldFile[0].originFileObj as File);

    setPreviewLoading(true);
    try {
      const res = await api.post('/tools/price-backfill/preview', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      setPreviewResult(res.data);
      message.success('映射预览完成');
    } catch (err) {
      message.error(getErrorMessage(err, '预览失败'));
    } finally {
      setPreviewLoading(false);
    }
  };

  // 执行回填并下载
  const handleExecute = async () => {
    if (!originalFile.length || !gldFile.length) {
      message.warning('请先上传两个文件');
      return;
    }

    const formData = new FormData();
    formData.append('original_file', originalFile[0].originFileObj as File);
    formData.append('gld_file', gldFile[0].originFileObj as File);

    setExecuteLoading(true);
    try {
      const res = await api.post('/tools/price-backfill/execute', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        responseType: 'blob',  // 返回的是文件，用 blob 接收
      });

      // 从响应头提取文件名
      const disposition = res.headers['content-disposition'] || '';
      let filename = '清单_已回填.xlsx';
      const match = disposition.match(/filename\*?=(?:UTF-8'')?(.+)/i);
      if (match) {
        filename = decodeURIComponent(match[1].replace(/['"]/g, ''));
      }

      // 释放旧的下载链接（防止内存泄漏）
      if (downloadInfo?.url) {
        window.URL.revokeObjectURL(downloadInfo.url);
      }

      // 创建下载链接并保存（页面上持续可用）
      const url = window.URL.createObjectURL(new Blob([res.data]));
      setDownloadInfo({ url, filename });

      // 同时自动触发一次下载
      const link = document.createElement('a');
      link.href = url;
      link.download = filename;
      link.click();

      message.success('填价完成！');
    } catch (err) {
      message.error(getErrorMessage(err, '回填失败'));
    } finally {
      setExecuteLoading(false);
    }
  };

  // 导出匹配报告（把映射表导出成Excel）
  const handleExportMapping = () => {
    if (!previewResult) return;

    const rows = previewResult.mapping.map((m) => ({
      '原始行号': m.row,
      '原始名称': m.original_name,
      '匹配名称': m.matched_name || '未匹配',
      '广联达行号': m.matched_row || '',
      '匹配方式': m.match_method === 'index' ? '序号匹配'
        : m.match_method.startsWith('name') ? '名称匹配'
        : '未匹配',
      '综合单价': m.unit_price,
      '合价': m.total_price,
    }));

    const ws = xlsxUtils.json_to_sheet(rows);
    // 设置列宽
    ws['!cols'] = [
      { wch: 8 },   // 原始行号
      { wch: 30 },  // 原始名称
      { wch: 30 },  // 匹配名称
      { wch: 10 },  // 广联达行号
      { wch: 12 },  // 匹配方式
      { wch: 12 },  // 综合单价
      { wch: 12 },  // 合价
    ];
    const wb = xlsxUtils.book_new();
    xlsxUtils.book_append_sheet(wb, ws, '匹配报告');
    const origName = originalFile[0]?.name?.replace(/\.[^.]+$/, '') || '清单';
    xlsxWriteFile(wb, `${origName}_匹配报告.xlsx`);
  };

  // 映射表格列定义
  const columns = [
    {
      title: '状态',
      dataIndex: 'match_method',
      key: 'status',
      width: 80,
      render: (_: string, record: MappingItem) => {
        if (record.match_method === '未匹配') {
          return <Tag color="red">未匹配</Tag>;
        }
        if (record.warnings?.length > 0) {
          return <Tag color="orange" icon={<WarningOutlined />}>疑似错配</Tag>;
        }
        return <Tag color="green">已匹配</Tag>;
      },
    },
    {
      title: '行号',
      dataIndex: 'row',
      key: 'row',
      width: 70,
    },
    {
      title: '原始名称',
      dataIndex: 'original_name',
      key: 'original_name',
      ellipsis: true,
    },
    {
      title: '匹配名称',
      dataIndex: 'matched_name',
      key: 'matched_name',
      ellipsis: true,
      render: (name: string | null) => name || <span style={{ color: '#ccc' }}>—</span>,
    },
    {
      title: '匹配方式',
      dataIndex: 'match_method',
      key: 'match_method',
      width: 110,
      render: (method: string) => {
        if (method === 'index') return <Tag color="blue">序号匹配</Tag>;
        if (method.startsWith('name')) return <Tag color="orange">名称匹配</Tag>;
        return <Tag>—</Tag>;
      },
    },
    {
      title: '综合单价',
      dataIndex: 'unit_price',
      key: 'unit_price',
      width: 100,
      align: 'right' as const,
      render: (v: number | null) => v != null ? v.toFixed(2) : '—',
    },
    {
      title: '合价',
      dataIndex: 'total_price',
      key: 'total_price',
      width: 100,
      align: 'right' as const,
      render: (v: number | null) => v != null ? v.toFixed(2) : '—',
    },
  ];

  const stats = previewResult?.stats;
  // 统计有警告的条数
  const warningCount = previewResult?.mapping.filter(
    (m) => m.warnings?.length > 0
  ).length ?? 0;

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      {/* 页面标题 */}
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <DollarOutlined style={{ fontSize: 24, color: '#2563eb' }} />
          <div>
            <h2 style={{ margin: 0 }}>智能填价</h2>
            <span style={{ color: '#64748b' }}>
              把广联达组价结果自动回填到甲方原始清单
            </span>
          </div>
        </div>
      </Card>

      {/* 上传区域：左右并排 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={12}>
          <Card title="甲方原始清单" size="small">
            <Dragger
              fileList={originalFile}
              maxCount={1}
              accept=".xlsx,.xls"
              beforeUpload={() => false}
              onChange={({ fileList }) => {
                setOriginalFile(fileList.slice(-1));
                setPreviewResult(null);
                setDownloadInfo(null);
              }}
              style={{ padding: '16px 0' }}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖拽上传</p>
              <p className="ant-upload-hint">甲方给的原始清单（单价列为空）</p>
            </Dragger>
          </Card>
        </Col>
        <Col span={12}>
          <Card title="广联达导出文件" size="small">
            <Dragger
              fileList={gldFile}
              maxCount={1}
              accept=".xlsx,.xls"
              beforeUpload={() => false}
              onChange={({ fileList }) => {
                setGldFile(fileList.slice(-1));
                setPreviewResult(null);
                setDownloadInfo(null);
              }}
              style={{ padding: '16px 0' }}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖拽上传</p>
              <p className="ant-upload-hint">广联达组价后导出的Excel（带价格）</p>
            </Dragger>
          </Card>
        </Col>
      </Row>

      {/* 操作按钮 */}
      <Card style={{ marginBottom: 16 }}>
        <Space>
          <Button
            icon={<EyeOutlined />}
            loading={previewLoading}
            onClick={handlePreview}
            disabled={!originalFile.length || !gldFile.length}
          >
            预览映射
          </Button>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            loading={executeLoading}
            onClick={handleExecute}
            disabled={!originalFile.length || !gldFile.length}
          >
            开始填价
          </Button>
        </Space>
      </Card>

      {/* 映射统计 */}
      {stats && (
        <Card style={{ marginBottom: 16 }}>
          <Row gutter={24}>
            <Col flex="1">
              <Statistic title="原始清单" value={previewResult.original_count} suffix="条" />
            </Col>
            <Col flex="1">
              <Statistic
                title="序号匹配"
                value={stats.matched_by_index}
                valueStyle={{ color: '#3f8600' }}
              />
            </Col>
            <Col flex="1">
              <Statistic
                title="名称匹配"
                value={stats.matched_by_name}
                valueStyle={{ color: '#d48806' }}
              />
            </Col>
            <Col flex="1">
              <Statistic
                title="未匹配"
                value={stats.unmatched}
                valueStyle={{ color: stats.unmatched > 0 ? '#cf1322' : '#3f8600' }}
              />
            </Col>
            <Col flex="1">
              <Statistic
                title="疑似错配"
                value={warningCount}
                valueStyle={{ color: warningCount > 0 ? '#d48806' : '#3f8600' }}
              />
            </Col>
          </Row>
        </Card>
      )}

      {/* 映射结果表格 */}
      {previewResult && (
        <Card
          title="映射详情"
          style={{ marginBottom: 16 }}
          extra={
            <Button
              icon={<FileExcelOutlined />}
              onClick={handleExportMapping}
              size="small"
            >
              导出匹配报告
            </Button>
          }
        >
          <Table
            dataSource={previewResult.mapping}
            columns={columns}
            rowKey={(r) => `${r.row}-${r.original_name}`}
            size="small"
            pagination={{ pageSize: 50 }}
            scroll={{ y: 500 }}
            expandable={{
              expandedRowRender: (record) => (
                <div style={{ padding: '4px 0', color: '#666', fontSize: 13 }}>
                  {record.matched_row != null ? (
                    <>
                      <div>
                        <span>来源：广联达第 <b>{record.matched_row}</b> 行</span>
                        {record.matched_index && (
                          <span style={{ marginLeft: 16 }}>序号：{record.matched_index}</span>
                        )}
                        <span style={{ marginLeft: 16 }}>
                          名称：{record.matched_name}
                        </span>
                        {record.unit_price != null && (
                          <span style={{ marginLeft: 16 }}>
                            单价：<b>{record.unit_price.toFixed(2)}</b>
                          </span>
                        )}
                        {record.total_price != null && (
                          <span style={{ marginLeft: 16 }}>
                            合价：<b>{record.total_price.toFixed(2)}</b>
                          </span>
                        )}
                      </div>
                      {record.warnings?.length > 0 && (
                        <div style={{ marginTop: 6 }}>
                          {record.warnings.map((w, i) => (
                            <Tag color="orange" key={i} style={{ marginBottom: 2 }}>
                              <WarningOutlined /> {w}
                            </Tag>
                          ))}
                        </div>
                      )}
                    </>
                  ) : (
                    <span style={{ color: '#999' }}>未找到匹配的广联达数据</span>
                  )}
                </div>
              ),
              rowExpandable: () => true,
            }}
          />
        </Card>
      )}

      {/* 填价完成 — 下载链接 */}
      {downloadInfo && (
        <Card>
          <Result
            status="success"
            icon={<CheckCircleOutlined />}
            title="填价完成！"
            subTitle={`文件已自动下载，也可以点击下方按钮再次下载`}
            extra={
              <Button
                type="primary"
                size="large"
                icon={<FileExcelOutlined />}
                href={downloadInfo.url}
                download={downloadInfo.filename}
              >
                下载 {downloadInfo.filename}
              </Button>
            }
          />
        </Card>
      )}
    </div>
  );
}
