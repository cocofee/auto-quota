/**
 * 咨询审核页面（管理员）
 *
 * 管理员审核用户通过截图提交的清单→定额对应关系。
 * 通过后自动存入经验库权威层。
 */

import { useState, useEffect, useCallback } from 'react';
import {
  Card, Table, Button, Tag, Space, App, Modal, Input,
  Typography, Descriptions, Row, Col,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CheckOutlined, CloseOutlined, ReloadOutlined, EyeOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { getErrorMessage } from '../../utils/error';

const { Title, Text } = Typography;
const { TextArea } = Input;

// 提交记录
interface SubmissionRecord {
  id: string;
  user_id: string;
  province: string;
  item_count: number;
  submitted_items: Array<{
    bill_name: string;
    quota_id: string;
    quota_name: string;
    unit: string;
  }>;
  image_path: string;
  status: string;
  review_note: string | null;
  created_at: string;
  reviewed_at: string | null;
}

export default function ConsultReviewPage() {
  const { message } = App.useApp();

  const [submissions, setSubmissions] = useState<SubmissionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [statusFilter, setStatusFilter] = useState('pending');

  // 详情弹窗
  const [detailVisible, setDetailVisible] = useState(false);
  const [currentRecord, setCurrentRecord] = useState<SubmissionRecord | null>(null);

  // 审核弹窗
  const [reviewVisible, setReviewVisible] = useState(false);
  const [reviewAction, setReviewAction] = useState<'approve' | 'reject'>('approve');
  const [reviewNote, setReviewNote] = useState('');
  const [reviewing, setReviewing] = useState(false);

  // 加载数据
  const loadData = useCallback(() => {
    setLoading(true);
    api.get('/consult/admin/pending', {
      params: { page, size: 20, status_filter: statusFilter },
    })
      .then((res) => {
        setSubmissions(res.data.items || []);
        setTotal(res.data.total || 0);
      })
      .catch(() => {
        message.error('加载数据失败');
      })
      .finally(() => setLoading(false));
  }, [page, statusFilter, message]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // 查看详情
  const showDetail = (record: SubmissionRecord) => {
    setCurrentRecord(record);
    setDetailVisible(true);
  };

  // 打开审核弹窗
  const openReview = (record: SubmissionRecord, action: 'approve' | 'reject') => {
    setCurrentRecord(record);
    setReviewAction(action);
    setReviewNote('');
    setReviewVisible(true);
  };

  // 执行审核
  const handleReview = async () => {
    if (!currentRecord) return;

    setReviewing(true);
    try {
      const res = await api.post(`/consult/admin/${currentRecord.id}/review`, {
        action: reviewAction,
        note: reviewNote,
      });
      if (reviewAction === 'approve') {
        message.success(`审核通过，已写入经验库 ${res.data.stored_count} 条`);
      } else {
        message.info(`已拒绝`);
      }
      setReviewVisible(false);
      loadData();
    } catch (err: unknown) {
      message.error(getErrorMessage(err, '审核失败'));
    } finally {
      setReviewing(false);
    }
  };

  // 状态标签
  const statusMap: Record<string, { color: string; text: string }> = {
    pending: { color: 'orange', text: '待审核' },
    approved: { color: 'green', text: '已通过' },
    rejected: { color: 'red', text: '已拒绝' },
  };

  // 主表格列
  const columns: ColumnsType<SubmissionRecord> = [
    {
      title: '省份',
      dataIndex: 'province',
      key: 'province',
      width: 200,
      ellipsis: true,
    },
    {
      title: '条目数',
      dataIndex: 'item_count',
      key: 'item_count',
      width: 80,
      align: 'center',
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 100,
      render: (status: string) => {
        const info = statusMap[status] || { color: 'default', text: status };
        return <Tag color={info.color}>{info.text}</Tag>;
      },
    },
    {
      title: '审核备注',
      dataIndex: 'review_note',
      key: 'review_note',
      ellipsis: true,
      render: (note: string | null) => note || '-',
    },
    {
      title: '提交时间',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      render: (t: string) => t ? new Date(t).toLocaleString('zh-CN') : '-',
    },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_, record) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => showDetail(record)}>
            详情
          </Button>
          {record.status === 'pending' && (
            <>
              <Button
                size="small"
                type="primary"
                icon={<CheckOutlined />}
                onClick={() => openReview(record, 'approve')}
              >
                通过
              </Button>
              <Button
                size="small"
                danger
                icon={<CloseOutlined />}
                onClick={() => openReview(record, 'reject')}
              >
                拒绝
              </Button>
            </>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Title level={3}>咨询审核</Title>
      <Text type="secondary">
        审核用户通过截图提交的清单→定额对应关系。通过后自动存入经验库权威层。
      </Text>

      {/* 筛选和统计 */}
      <Card style={{ marginTop: 16 }}>
        <Row gutter={16}>
          <Col>
            <Space>
              <Text>状态筛选：</Text>
              {['pending', 'approved', 'rejected', 'all'].map((s) => (
                <Button
                  key={s}
                  type={statusFilter === s ? 'primary' : 'default'}
                  size="small"
                  onClick={() => { setStatusFilter(s); setPage(1); }}
                >
                  {{ pending: '待审核', approved: '已通过', rejected: '已拒绝', all: '全部' }[s]}
                </Button>
              ))}
              <Button icon={<ReloadOutlined />} onClick={loadData} size="small">
                刷新
              </Button>
            </Space>
          </Col>
        </Row>
      </Card>

      {/* 列表 */}
      <Card style={{ marginTop: 16 }}>
        <Table
          columns={columns}
          dataSource={submissions}
          rowKey="id"
          loading={loading}
          pagination={{
            current: page,
            pageSize: 20,
            total,
            onChange: setPage,
            showTotal: (t) => `共 ${t} 条`,
          }}
          size="small"
          locale={{ emptyText: '暂无数据' }}
        />
      </Card>

      {/* 详情弹窗 */}
      <Modal
        title="咨询详情"
        open={detailVisible}
        onCancel={() => setDetailVisible(false)}
        footer={null}
        width={800}
      >
        {currentRecord && (
          <div>
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="省份">{currentRecord.province}</Descriptions.Item>
              <Descriptions.Item label="条目数">{currentRecord.item_count}</Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={statusMap[currentRecord.status]?.color}>
                  {statusMap[currentRecord.status]?.text || currentRecord.status}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="提交时间">
                {currentRecord.created_at ? new Date(currentRecord.created_at).toLocaleString('zh-CN') : '-'}
              </Descriptions.Item>
              {currentRecord.review_note && (
                <Descriptions.Item label="审核备注" span={2}>
                  {currentRecord.review_note}
                </Descriptions.Item>
              )}
            </Descriptions>

            {/* 提交的清单→定额列表 */}
            <div style={{ marginTop: 16 }}>
              <Text strong>清单→定额对应：</Text>
              <Table
                dataSource={currentRecord.submitted_items?.map((item, i) => ({ ...item, key: i }))}
                columns={[
                  { title: '清单名称', dataIndex: 'bill_name', key: 'bill_name' },
                  { title: '定额编号', dataIndex: 'quota_id', key: 'quota_id', width: 130 },
                  { title: '定额名称', dataIndex: 'quota_name', key: 'quota_name' },
                  { title: '单位', dataIndex: 'unit', key: 'unit', width: 60 },
                ]}
                pagination={false}
                size="small"
                style={{ marginTop: 8 }}
              />
            </div>
          </div>
        )}
      </Modal>

      {/* 审核弹窗 */}
      <Modal
        title={reviewAction === 'approve' ? '确认通过' : '确认拒绝'}
        open={reviewVisible}
        onCancel={() => setReviewVisible(false)}
        onOk={handleReview}
        confirmLoading={reviewing}
        okText={reviewAction === 'approve' ? '通过并写入经验库' : '拒绝'}
        okButtonProps={{ danger: reviewAction === 'reject' }}
      >
        <div style={{ marginBottom: 12 }}>
          <Text>
            {reviewAction === 'approve'
              ? `确认通过？共 ${currentRecord?.item_count || 0} 条记录将写入经验库权威层。`
              : '确认拒绝此提交？'}
          </Text>
        </div>
        <TextArea
          placeholder="审核备注（可选）"
          value={reviewNote}
          onChange={(e) => setReviewNote(e.target.value)}
          rows={3}
        />
      </Modal>
    </div>
  );
}
