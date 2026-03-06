/**
 * 管理员 — 反馈审核
 *
 * 显示所有用户上传的反馈列表，包含学习统计。
 * 管理员可以查看每条反馈的详细信息。
 * 支持直接导入带定额的清单Excel。
 */

import { useEffect, useState, useCallback } from 'react';
import {
  Card, Table, Statistic, Row, Col, Tag, App, Modal, Descriptions,
  Button, Upload, Select, Space,
} from 'antd';
import { MessageOutlined, BookOutlined, UploadOutlined, ImportOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import api from '../../services/api';

/** 反馈列表项 */
interface FeedbackItem {
  task_id: string;
  task_name: string;
  original_filename: string;
  province: string;
  feedback_uploaded_at: string | null;
  feedback_stats: { total: number; learned: number } | null;
}

/** 反馈详情 */
interface FeedbackDetail {
  task_id: string;
  task_name: string;
  original_filename: string;
  province: string;
  mode: string;
  feedback_uploaded_at: string | null;
  feedback_stats: { total: number; learned: number } | null;
  task_stats: Record<string, unknown> | null;
  created_at: string;
  completed_at: string | null;
}

/* 常用省份选项 */
const PROVINCE_OPTIONS = [
  '北京市建设工程施工消耗量标准(2024)',
  '湖北省建设工程公共专业消耗量定额及全费用基价表(2018)',
  '河南省建设工程标准定额(2016)',
  '广东省建设工程计价通则(2018)',
];

export default function FeedbackReview() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [items, setItems] = useState<FeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  // 详情弹窗
  const [detailVisible, setDetailVisible] = useState(false);
  const [detail, setDetail] = useState<FeedbackDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  // 导入弹窗
  const [importVisible, setImportVisible] = useState(false);
  const [importProvince, setImportProvince] = useState(PROVINCE_OPTIONS[0]);
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);

  // 加载反馈列表
  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get('/admin/feedback/list', {
        params: { page, size: pageSize },
      });
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch {
      message.error('加载反馈列表失败');
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, message]);

  useEffect(() => {
    loadList();
  }, [loadList]);

  // 查看详情
  const showDetail = async (taskId: string) => {
    setDetailLoading(true);
    setDetailVisible(true);
    try {
      const { data } = await api.get(`/admin/feedback/${taskId}/details`);
      setDetail(data);
    } catch {
      message.error('加载反馈详情失败');
    } finally {
      setDetailLoading(false);
    }
  };

  // 执行导入
  const handleImport = async () => {
    if (!importFile) {
      message.warning('请先选择Excel文件');
      return;
    }
    setImporting(true);
    try {
      const formData = new FormData();
      formData.append('file', importFile);
      formData.append('province', importProvince);
      const { data } = await api.post('/admin/feedback/import', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        params: { province: importProvince },
      });
      const stats = data.stats || {};
      message.success(
        `导入成功！共 ${stats.total || 0} 条，新增 ${stats.added || stats.imported || 0} 条`
      );
      setImportVisible(false);
      setImportFile(null);
      loadList(); // 刷新列表
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || '导入失败');
    } finally {
      setImporting(false);
    }
  };

  // 汇总统计
  const totalFeedbacks = total;
  const totalLearned = items.reduce(
    (sum, item) => sum + (item.feedback_stats?.learned || 0),
    0
  );

  const columns = [
    {
      title: '任务名称',
      dataIndex: 'task_name',
      key: 'task_name',
      ellipsis: true,
    },
    {
      title: '原始文件',
      dataIndex: 'original_filename',
      key: 'original_filename',
      ellipsis: true,
    },
    {
      title: '省份',
      dataIndex: 'province',
      key: 'province',
      width: 100,
    },
    {
      title: '学习统计',
      key: 'stats',
      width: 180,
      render: (_: unknown, record: FeedbackItem) => {
        if (!record.feedback_stats) return '-';
        const { total: t, learned } = record.feedback_stats;
        return (
          <>
            <Tag color="blue">{t} 条清单</Tag>
            <Tag color="green">{learned} 条学习</Tag>
          </>
        );
      },
    },
    {
      title: '上传时间',
      dataIndex: 'feedback_uploaded_at',
      key: 'feedback_uploaded_at',
      width: 160,
      render: (t: string | null) => (t ? dayjs(t).format('MM-DD HH:mm:ss') : '-'),
    },
    {
      title: '操作',
      key: 'action',
      width: 80,
      render: (_: unknown, record: FeedbackItem) => (
        <a onClick={() => showDetail(record.task_id)}>详情</a>
      ),
    },
  ];

  return (
    <>
      {/* 顶部统计卡片 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={8}>
          <Card>
            <Statistic
              title="总反馈数"
              value={totalFeedbacks}
              prefix={<MessageOutlined />}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="总学习条数"
              value={totalLearned}
              prefix={<BookOutlined />}
              valueStyle={{ color: '#3f8600' }}
            />
          </Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="学习率"
              value={totalFeedbacks > 0 ? Math.round((totalLearned / Math.max(totalFeedbacks, 1)) * 100) : 0}
              suffix="%"
              prefix={<ImportOutlined />}
              valueStyle={{ color: '#1677ff' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 反馈列表 */}
      <Card
        title="反馈列表"
        extra={
          <Button
            type="primary"
            icon={<ImportOutlined />}
            onClick={() => setImportVisible(true)}
          >
            导入带定额清单
          </Button>
        }
      >
        <Table
          rowKey="task_id"
          dataSource={items}
          columns={columns}
          loading={loading}
          size="middle"
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p, s) => {
              setPage(p);
              setPageSize(s);
            },
          }}
          locale={{ emptyText: '暂无反馈记录' }}
        />
      </Card>

      {/* 详情弹窗 */}
      <Modal
        title="反馈详情"
        open={detailVisible}
        onCancel={() => setDetailVisible(false)}
        footer={null}
        width={600}
        loading={detailLoading}
      >
        {detail && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="任务名称">{detail.task_name}</Descriptions.Item>
            <Descriptions.Item label="原始文件">{detail.original_filename}</Descriptions.Item>
            <Descriptions.Item label="省份">{detail.province}</Descriptions.Item>
            <Descriptions.Item label="匹配模式">
              <Tag color={detail.mode === 'agent' ? 'purple' : 'blue'}>
                {detail.mode === 'agent' ? 'Agent' : '搜索'}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="任务创建时间">
              {dayjs(detail.created_at).format('YYYY-MM-DD HH:mm:ss')}
            </Descriptions.Item>
            <Descriptions.Item label="匹配完成时间">
              {detail.completed_at
                ? dayjs(detail.completed_at).format('YYYY-MM-DD HH:mm:ss')
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="反馈上传时间">
              {detail.feedback_uploaded_at
                ? dayjs(detail.feedback_uploaded_at).format('YYYY-MM-DD HH:mm:ss')
                : '-'}
            </Descriptions.Item>
            <Descriptions.Item label="识别清单数">
              {detail.feedback_stats?.total ?? '-'}
            </Descriptions.Item>
            <Descriptions.Item label="学习条数">
              <span style={{ color: '#3f8600', fontWeight: 'bold' }}>
                {detail.feedback_stats?.learned ?? '-'}
              </span>
            </Descriptions.Item>
          </Descriptions>
        )}
      </Modal>

      {/* 导入弹窗 */}
      <Modal
        title="导入带定额清单"
        open={importVisible}
        onCancel={() => { setImportVisible(false); setImportFile(null); }}
        onOk={handleImport}
        confirmLoading={importing}
        okText="开始导入"
        cancelText="取消"
        width={500}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>选择省份</div>
            <Select
              value={importProvince}
              onChange={setImportProvince}
              style={{ width: '100%' }}
              showSearch
              options={PROVINCE_OPTIONS.map(p => ({ label: p, value: p }))}
            />
          </div>
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>选择Excel文件</div>
            <Upload
              accept=".xlsx"
              maxCount={1}
              beforeUpload={(file) => {
                setImportFile(file);
                return false; // 阻止自动上传
              }}
              onRemove={() => setImportFile(null)}
              fileList={importFile ? [{ uid: '-1', name: importFile.name, status: 'done' }] : []}
            >
              <Button icon={<UploadOutlined />}>选择文件</Button>
            </Upload>
            <div style={{ color: '#999', fontSize: 12, marginTop: 4 }}>
              支持 .xlsx 格式，文件中需包含清单行和定额行
            </div>
          </div>
        </Space>
      </Modal>
    </>
  );
}
