/**
 * 编清单页面
 *
 * 上传工程量Excel → 选择清单版本(2013/2024) → 自动匹配12位清单编码 → 预览结果 → 下载。
 */

import { useState } from 'react';
import {
  Card, Upload, Button, Table, Tag, Space, App, Statistic, Row, Col,
  Result, Radio, Typography, Progress,
} from 'antd';
import {
  InboxOutlined,
  DownloadOutlined,
  FileTextOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons';
import type { UploadFile } from 'antd';
import api from '../../services/api';
import { getErrorMessage } from '../../utils/error';

const { Dragger } = Upload;
const { Text } = Typography;

// 编清单预览结果
interface CompileResult {
  total: number;
  matched: number;
  unmatched: number;
  bill_version: string;
  items: CompileItem[];
}

// 单条清单项
interface CompileItem {
  index: number;
  name: string;
  description: string;
  unit: string;
  quantity: number | null;
  bill_code: string;          // 12位清单编码
  bill_code_source: string;   // matched / original / unmatched
  matched_name: string;       // 匹配到的标准清单名称
  sheet_name: string;
  section: string;
}

export default function BillCompiler() {
  const { message } = App.useApp();

  // === 状态 ===
  const [billVersion, setBillVersion] = useState<string>('2024');
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [result, setResult] = useState<CompileResult | null>(null);

  // === 上传并预览 ===
  const handlePreview = async () => {
    if (fileList.length === 0) {
      message.warning('请先上传Excel文件');
      return;
    }

    const file = fileList[0].originFileObj;
    if (!file) {
      message.error('文件读取失败，请重新上传');
      return;
    }

    setLoading(true);
    setResult(null);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('bill_version', billVersion);

      const res = await api.post('/tools/bill-compiler/preview', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 120000, // 编清单可能较慢，2分钟超时
      });

      setResult(res.data);
      message.success(`编清单完成: ${res.data.matched}/${res.data.total} 条匹配成功`);
    } catch (err) {
      message.error('编清单失败: ' + getErrorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  // === 下载结果Excel ===
  const handleDownload = async () => {
    if (fileList.length === 0) return;

    const file = fileList[0].originFileObj;
    if (!file) return;

    setDownloading(true);

    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('bill_version', billVersion);

      const res = await api.post('/tools/bill-compiler/execute', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        responseType: 'blob',
        timeout: 120000,
      });

      // 从响应头获取文件名
      const disposition = res.headers['content-disposition'] || '';
      let filename = '工程量清单.xlsx';
      const match = disposition.match(/filename\*=UTF-8''(.+)/);
      if (match) {
        filename = decodeURIComponent(match[1]);
      } else {
        const match2 = disposition.match(/filename="?(.+?)"?$/);
        if (match2) filename = match2[1];
      }

      // 触发下载
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);

      message.success('下载成功');
    } catch (err) {
      message.error('下载失败: ' + getErrorMessage(err));
    } finally {
      setDownloading(false);
    }
  };

  // === 重新开始 ===
  const handleReset = () => {
    setFileList([]);
    setResult(null);
  };

  // === 表格列定义 ===
  const columns = [
    {
      title: '序号',
      dataIndex: 'index',
      key: 'index',
      width: 60,
    },
    {
      title: 'Sheet',
      dataIndex: 'sheet_name',
      key: 'sheet_name',
      width: 100,
      ellipsis: true,
    },
    {
      title: '项目名称',
      dataIndex: 'name',
      key: 'name',
      width: 200,
      ellipsis: true,
    },
    {
      title: '项目编码',
      dataIndex: 'bill_code',
      key: 'bill_code',
      width: 160,
      render: (code: string, record: CompileItem) => {
        if (!code) return <Tag color="red">未匹配</Tag>;
        const color = record.bill_code_source === 'matched' ? 'blue' :
                      record.bill_code_source === 'original' ? 'green' : 'red';
        const label = record.bill_code_source === 'matched' ? '自动' :
                      record.bill_code_source === 'original' ? '原有' : '未匹配';
        return (
          <Space size={4}>
            <Text code style={{ fontSize: 12 }}>{code}</Text>
            <Tag color={color} style={{ fontSize: 10 }}>{label}</Tag>
          </Space>
        );
      },
    },
    {
      title: '匹配标准名称',
      dataIndex: 'matched_name',
      key: 'matched_name',
      width: 180,
      ellipsis: true,
      render: (name: string) => name || '-',
    },
    {
      title: '单位',
      dataIndex: 'unit',
      key: 'unit',
      width: 60,
    },
    {
      title: '工程量',
      dataIndex: 'quantity',
      key: 'quantity',
      width: 80,
      render: (v: number | null) => v != null ? v : '-',
    },
  ];

  // === 匹配率 ===
  const matchRate = result ? Math.round((result.matched / result.total) * 100) : 0;

  return (
    <div>
      {/* 标题 */}
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 600 }}>
          <FileTextOutlined style={{ marginRight: 8, color: '#2563eb' }} />
          编清单
        </h2>
        <Text type="secondary" style={{ fontSize: 14 }}>
          上传工程量Excel，自动匹配12位标准清单编码，生成工程量清单
        </Text>
      </div>

      {/* 第一步：上传文件 + 选版本 */}
      {!result && (
        <Card style={{ marginBottom: 16 }}>
          <Space direction="vertical" style={{ width: '100%' }} size="large">
            {/* 版本选择 */}
            <div>
              <Text strong style={{ marginRight: 16, fontSize: 15 }}>清单版本：</Text>
              <Radio.Group
                value={billVersion}
                onChange={e => setBillVersion(e.target.value)}
                optionType="button"
                buttonStyle="solid"
                size="large"
              >
                <Radio.Button value="2024">
                  2024版（新标准）
                </Radio.Button>
                <Radio.Button value="2013">
                  2013版（旧标准）
                </Radio.Button>
              </Radio.Group>
              <Text type="secondary" style={{ marginLeft: 16 }}>
                {billVersion === '2024'
                  ? '适用于2025年9月后新开工的项目'
                  : '适用于2025年9月前已开工的项目'}
              </Text>
            </div>

            {/* 文件上传 */}
            <Dragger
              fileList={fileList}
              onChange={({ fileList: fl }) => setFileList(fl.slice(-1))}
              beforeUpload={() => false}
              accept=".xlsx,.xls"
              maxCount={1}
            >
              <p className="ant-upload-drag-icon">
                <InboxOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽上传工程量Excel文件</p>
              <p className="ant-upload-hint">
                支持算量软件导出（GQI等）、工程量汇总表、手工清单等格式
              </p>
            </Dragger>

            {/* 开始编清单 */}
            <div style={{ textAlign: 'center' }}>
              <Button
                type="primary"
                size="large"
                onClick={handlePreview}
                loading={loading}
                disabled={fileList.length === 0}
                style={{ width: 200, height: 44 }}
              >
                {loading ? '正在编清单...' : '开始编清单'}
              </Button>
            </div>
          </Space>
        </Card>
      )}

      {/* 第二步：结果预览 */}
      {result && (
        <>
          {/* 统计卡片 */}
          <Row gutter={16} style={{ marginBottom: 16 }}>
            <Col span={5}>
              <Card size="small">
                <Statistic title="清单项总数" value={result.total} suffix="条" />
              </Card>
            </Col>
            <Col span={5}>
              <Card size="small">
                <Statistic
                  title="匹配成功"
                  value={result.matched}
                  suffix="条"
                  valueStyle={{ color: '#52c41a' }}
                  prefix={<CheckCircleOutlined />}
                />
              </Card>
            </Col>
            <Col span={5}>
              <Card size="small">
                <Statistic
                  title="未匹配"
                  value={result.unmatched}
                  suffix="条"
                  valueStyle={{ color: result.unmatched > 0 ? '#ff4d4f' : '#52c41a' }}
                  prefix={<CloseCircleOutlined />}
                />
              </Card>
            </Col>
            <Col span={4}>
              <Card size="small">
                <div style={{ textAlign: 'center' }}>
                  <div style={{ color: '#8c8c8c', marginBottom: 4, fontSize: 13 }}>匹配率</div>
                  <Progress
                    type="circle"
                    percent={matchRate}
                    size={50}
                    strokeColor={matchRate >= 80 ? '#52c41a' : matchRate >= 50 ? '#faad14' : '#ff4d4f'}
                  />
                </div>
              </Card>
            </Col>
            <Col span={5}>
              <Card size="small">
                <div style={{ textAlign: 'center', paddingTop: 8 }}>
                  <Tag color={result.bill_version === '2024' ? 'blue' : 'purple'} style={{ fontSize: 14, padding: '4px 12px' }}>
                    {result.bill_version}版清单
                  </Tag>
                </div>
              </Card>
            </Col>
          </Row>

          {/* 操作按钮 */}
          <Card size="small" style={{ marginBottom: 16 }}>
            <Space>
              <Button
                type="primary"
                icon={<DownloadOutlined />}
                onClick={handleDownload}
                loading={downloading}
                size="large"
              >
                下载工程量清单
              </Button>
              <Button
                icon={<ReloadOutlined />}
                onClick={handleReset}
                size="large"
              >
                重新上传
              </Button>
            </Space>
          </Card>

          {/* 结果表格 */}
          <Card>
            <Table
              columns={columns}
              dataSource={result.items}
              rowKey="index"
              size="small"
              pagination={{
                pageSize: 50,
                showSizeChanger: true,
                pageSizeOptions: ['20', '50', '100'],
                showTotal: (total) => `共 ${total} 条`,
              }}
              scroll={{ y: 500 }}
              rowClassName={(record) =>
                record.bill_code_source === 'unmatched' ? 'row-unmatched' : ''
              }
            />
          </Card>

          {/* 未匹配行的红色背景样式 */}
          <style>{`
            .row-unmatched {
              background-color: #fff2f0 !important;
            }
            .row-unmatched:hover > td {
              background-color: #ffccc7 !important;
            }
          `}</style>
        </>
      )}

      {/* 空状态提示：还没上传也没结果时不显示，避免视觉噪音 */}
      {!result && fileList.length === 0 && !loading && (
        <Result
          icon={<FileTextOutlined style={{ color: '#d9d9d9' }} />}
          title="上传工程量Excel，自动生成标准工程量清单"
          subTitle="系统会自动识别清单项，匹配国标12位清单编码，输出可直接使用的工程量清单"
        />
      )}
    </div>
  );
}
