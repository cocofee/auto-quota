/**
 * 管理员 — 反馈审核
 *
 * 显示所有用户上传的反馈列表，包含学习统计。
 * 管理员可以查看每条反馈的详细信息。
 */

import { useEffect, useState, useCallback } from 'react';
import { Card, Table, Statistic, Row, Col, Tag, App, Modal, Descriptions } from 'antd';
import { MessageOutlined, BookOutlined } from '@ant-design/icons';
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
        <Col span={12}>
          <Card>
            <Statistic
              title="总反馈数"
              value={totalFeedbacks}
              prefix={<MessageOutlined />}
            />
          </Card>
        </Col>
        <Col span={12}>
          <Card>
            <Statistic
              title="总学习条数"
              value={totalLearned}
              prefix={<BookOutlined />}
              valueStyle={{ color: '#3f8600' }}
            />
          </Card>
        </Col>
      </Row>

      {/* 反馈列表 */}
      <Card title="反馈列表">
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
    </>
  );
}
