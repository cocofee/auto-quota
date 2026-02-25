/**
 * 匹配结果页
 *
 * 客户（普通用户）：简化视图 — 清单名 + 匹配定额 + 下载按钮
 * 管理员：完整视图 — 置信度颜色标记、来源、候选列表、批量确认等
 */

import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Card, Table, Tag, Button, Space, Statistic, Row, Col, Typography,
  Descriptions, App, Tooltip,
} from 'antd';
import {
  ArrowLeftOutlined,
  DownloadOutlined,
  CheckCircleOutlined,
  CheckOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { useAuthStore } from '../../stores/auth';
import type {
  MatchResult, ResultListResponse, TaskInfo, ReviewStatus,
} from '../../types';

/** 置信度分档（和后端一致） */
const GREEN_THRESHOLD = 85;
const YELLOW_THRESHOLD = 70;

/** 获取置信度对应的颜色 */
function getConfidenceColor(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return '#52c41a';
  if (confidence >= YELLOW_THRESHOLD) return '#faad14';
  return '#ff4d4f';
}

/** 获取置信度对应的Tag颜色名 */
function getConfidenceTag(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return 'success';
  if (confidence >= YELLOW_THRESHOLD) return 'warning';
  return 'error';
}

/** 审核状态配置 */
const REVIEW_MAP: Record<ReviewStatus, { color: string; text: string }> = {
  pending: { color: 'default', text: '待审核' },
  confirmed: { color: 'success', text: '已确认' },
  corrected: { color: 'processing', text: '已纠正' },
};

export default function ResultsPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { user } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;

  const [loading, setLoading] = useState(false);
  const [task, setTask] = useState<TaskInfo | null>(null);
  const [results, setResults] = useState<MatchResult[]>([]);
  const [summary, setSummary] = useState({
    total: 0,
    high_confidence: 0,
    mid_confidence: 0,
    low_confidence: 0,
    no_match: 0,
  });
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([]);
  const [confirmLoading, setConfirmLoading] = useState(false);

  useEffect(() => {
    if (!taskId) return;
    loadData();
  }, [taskId]);

  const loadData = async () => {
    if (!taskId) return;
    setLoading(true);
    try {
      const [taskRes, resultsRes] = await Promise.all([
        api.get<TaskInfo>(`/tasks/${taskId}`),
        api.get<ResultListResponse>(`/tasks/${taskId}/results`),
      ]);
      setTask(taskRes.data);
      setResults(resultsRes.data.items);
      setSummary(resultsRes.data.summary);
    } catch {
      message.error('加载匹配结果失败');
    } finally {
      setLoading(false);
    }
  };

  /** 批量确认选中的结果（仅管理员） */
  const confirmSelected = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择要确认的结果');
      return;
    }
    setConfirmLoading(true);
    try {
      const { data } = await api.post(`/tasks/${taskId}/results/confirm`, {
        result_ids: selectedRowKeys,
      });
      message.success(`成功确认 ${data.confirmed} 条结果`);
      setSelectedRowKeys([]);
      loadData();
    } catch {
      message.error('确认失败');
    } finally {
      setConfirmLoading(false);
    }
  };

  /** 一键确认所有高置信度（仅管理员） */
  const confirmAllHigh = async () => {
    const highConfIds = results
      .filter((r) => r.confidence >= GREEN_THRESHOLD && r.review_status === 'pending')
      .map((r) => r.id);
    if (highConfIds.length === 0) {
      message.info('没有待确认的高置信度结果');
      return;
    }
    setConfirmLoading(true);
    try {
      const { data } = await api.post(`/tasks/${taskId}/results/confirm`, {
        result_ids: highConfIds,
      });
      message.success(`一键确认 ${data.confirmed} 条高置信度结果`);
      setSelectedRowKeys([]);
      loadData();
    } catch {
      message.error('确认失败');
    } finally {
      setConfirmLoading(false);
    }
  };

  /** 下载Excel */
  const downloadExcel = async () => {
    try {
      const response = await api.get(`/tasks/${taskId}/export`, {
        responseType: 'blob',
      });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `${task?.name || 'result'}_定额匹配结果.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败');
    }
  };

  /** 展开行：显示匹配详情（仅管理员可展开） */
  const expandedRowRender = (record: MatchResult) => {
    const quotas = record.corrected_quotas || record.quotas || [];
    return (
      <div style={{ padding: '8px 0' }}>
        <Descriptions size="small" column={2} bordered>
          <Descriptions.Item label="清单描述" span={2}>
            {record.bill_description || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="专业">
            {record.specialty || '-'}
          </Descriptions.Item>
          <Descriptions.Item label="单位 / 数量">
            {record.bill_unit || '-'} / {record.bill_quantity ?? '-'}
          </Descriptions.Item>
          <Descriptions.Item label="匹配来源">
            <Tag>{record.match_source}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="候选数量">
            {record.candidates_count}
          </Descriptions.Item>
          <Descriptions.Item label="匹配说明" span={2}>
            {record.explanation || '-'}
          </Descriptions.Item>
        </Descriptions>

        {quotas.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <Typography.Text strong>匹配定额：</Typography.Text>
            <Table
              size="small"
              dataSource={quotas}
              rowKey="quota_id"
              pagination={false}
              style={{ marginTop: 8 }}
              columns={[
                { title: '定额编号', dataIndex: 'quota_id', width: 120 },
                { title: '名称', dataIndex: 'name' },
                { title: '单位', dataIndex: 'unit', width: 60 },
                {
                  title: '参数分',
                  dataIndex: 'param_score',
                  width: 80,
                  render: (v: number | null) => v != null ? `${(v * 100).toFixed(0)}%` : '-',
                },
                { title: '来源', dataIndex: 'source', width: 80 },
              ]}
            />
          </div>
        )}
      </div>
    );
  };

  // ============================================================
  // 表格列定义：客户简化 vs 管理员完整
  // ============================================================
  const columns = [
    {
      title: '#',
      dataIndex: 'index',
      key: 'index',
      width: 50,
    },
    {
      title: '清单项名称',
      dataIndex: 'bill_name',
      key: 'bill_name',
      ellipsis: true,
    },
    {
      title: '匹配定额',
      key: 'quota',
      width: 200,
      ellipsis: true,
      render: (_: unknown, record: MatchResult) => {
        const quotas = record.corrected_quotas || record.quotas || [];
        if (quotas.length === 0) return <Tag color="default">未匹配</Tag>;
        const first = quotas[0];
        return (
          <Tooltip title={first.name}>
            <Tag color="blue">{first.quota_id}</Tag>
            <span style={{ fontSize: 12 }}>{first.name}</span>
          </Tooltip>
        );
      },
    },
    // 以下列仅管理员可见
    ...(isAdmin ? [
      {
        title: '置信度',
        dataIndex: 'confidence',
        key: 'confidence',
        width: 90,
        sorter: (a: MatchResult, b: MatchResult) => a.confidence - b.confidence,
        render: (confidence: number) => (
          <Tag color={getConfidenceTag(confidence)} style={{ fontWeight: 'bold' }}>
            {confidence}
          </Tag>
        ),
      },
      {
        title: '来源',
        dataIndex: 'match_source',
        key: 'match_source',
        width: 80,
        render: (source: string) => <Tag>{source}</Tag>,
      },
      {
        title: '审核',
        dataIndex: 'review_status',
        key: 'review_status',
        width: 80,
        render: (status: ReviewStatus) => {
          const info = REVIEW_MAP[status] || { color: 'default', text: status };
          return <Tag color={info.color}>{info.text}</Tag>;
        },
      },
    ] : []),
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 顶部操作栏 */}
      <Card>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space>
            <Button
              icon={<ArrowLeftOutlined />}
              onClick={() => navigate('/tasks')}
            >
              返回列表
            </Button>
            <Typography.Title level={4} style={{ margin: 0 }}>
              {task?.name || '匹配结果'}
            </Typography.Title>
            {task && (
              <Tag>{task.province}</Tag>
            )}
            {/* 管理员才显示模式标签 */}
            {isAdmin && task && (
              <Tag color={task.mode === 'agent' ? 'purple' : 'blue'}>
                {task.mode === 'agent' ? 'Agent' : '搜索'}
              </Tag>
            )}
          </Space>
          <Space>
            {/* 管理员才显示确认按钮 */}
            {isAdmin && (
              <>
                <Button
                  icon={<CheckOutlined />}
                  onClick={confirmAllHigh}
                  loading={confirmLoading}
                >
                  一键确认高置信度
                </Button>
                {selectedRowKeys.length > 0 && (
                  <Button
                    type="primary"
                    icon={<CheckCircleOutlined />}
                    onClick={confirmSelected}
                    loading={confirmLoading}
                  >
                    确认选中({selectedRowKeys.length})
                  </Button>
                )}
              </>
            )}
            <Button type="primary" icon={<DownloadOutlined />} onClick={downloadExcel}>
              下载Excel
            </Button>
          </Space>
        </div>
      </Card>

      {/* 置信度统计 — 仅管理员可见 */}
      {isAdmin && (
        <Row gutter={16}>
          <Col span={5}>
            <Card>
              <Statistic title="总条数" value={summary.total} />
            </Card>
          </Col>
          <Col span={5}>
            <Card>
              <Statistic
                title="高置信度"
                value={summary.high_confidence}
                valueStyle={{ color: '#52c41a' }}
                suffix={summary.total > 0 ? `(${Math.round(summary.high_confidence / summary.total * 100)}%)` : ''}
              />
            </Card>
          </Col>
          <Col span={5}>
            <Card>
              <Statistic
                title="中置信度"
                value={summary.mid_confidence}
                valueStyle={{ color: '#faad14' }}
              />
            </Card>
          </Col>
          <Col span={5}>
            <Card>
              <Statistic
                title="低置信度"
                value={summary.low_confidence}
                valueStyle={{ color: '#ff4d4f' }}
              />
            </Card>
          </Col>
          <Col span={4}>
            <Card>
              <Statistic
                title="未匹配"
                value={summary.no_match}
                valueStyle={{ color: '#999' }}
              />
            </Card>
          </Col>
        </Row>
      )}

      {/* 客户看到简洁统计 */}
      {!isAdmin && (
        <Card>
          <Space size="large">
            <Statistic title="匹配结果" value={summary.total} suffix="条" />
            <Button type="primary" icon={<DownloadOutlined />} onClick={downloadExcel}>
              下载结果
            </Button>
          </Space>
        </Card>
      )}

      {/* 结果表格 */}
      <Card>
        <Table
          rowKey="id"
          dataSource={results}
          columns={columns}
          loading={loading}
          size="middle"
          // 管理员可展开行查看详情，客户不能
          expandable={isAdmin ? {
            expandedRowRender,
            expandRowByClick: true,
          } : undefined}
          // 管理员可勾选批量确认，客户不能
          rowSelection={isAdmin ? {
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys as string[]),
          } : undefined}
          pagination={{
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            defaultPageSize: 50,
            pageSizeOptions: ['20', '50', '100'],
          }}
          // 管理员：按置信度显示左侧颜色条；客户：无颜色条
          onRow={isAdmin ? (record) => ({
            style: {
              borderLeft: `3px solid ${getConfidenceColor(record.confidence)}`,
            },
          }) : undefined}
          locale={{ emptyText: '暂无匹配结果' }}
        />
      </Card>
    </Space>
  );
}
