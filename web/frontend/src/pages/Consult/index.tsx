/**
 * 定额咨询页面 — 和贾维斯多轮对话
 *
 * 用户输入问题（可贴图）→ 贾维斯回答 → 多轮交流 → 确认提取 → 提交审核。
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  Card, Button, Input, Select, Space, Tag, Upload, App,
  Typography, Divider, Table, Popconfirm, Empty, Spin,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  SendOutlined, DeleteOutlined, PlusOutlined,
  ReloadOutlined, PictureOutlined, CheckCircleOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

const { Title, Text } = Typography;
const { TextArea } = Input;

// 对话消息
interface ChatMsg {
  role: 'user' | 'assistant';
  content: string;
  image_base64?: string;
  image_type?: string;
  image_preview?: string;  // 本地预览用的 data URL
}

// 解析出的对应关系
interface ParsedItem {
  bill_name: string;
  quota_id: string;
  quota_name: string;
  unit: string;
}

// 提交记录
interface SubmissionRecord {
  id: string;
  province: string;
  item_count: number;
  status: 'pending' | 'approved' | 'rejected';
  review_note: string | null;
  created_at: string;
  reviewed_at: string | null;
}

export default function ConsultPage() {
  const { message } = App.useApp();

  // 省份
  const [provinces, setProvinces] = useState<string[]>([]);
  const [selectedProvince, setSelectedProvince] = useState<string>('');

  // 对话
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [inputText, setInputText] = useState('');
  const [sending, setSending] = useState(false);
  const [pendingImage, setPendingImage] = useState<{
    base64: string; type: string; preview: string; path: string;
  } | null>(null);
  // 收集对话中上传的图片路径（提交时传给后端，管理员审核可溯源）
  const [uploadedImagePaths, setUploadedImagePaths] = useState<string[]>([]);
  const chatEndRef = useRef<HTMLDivElement>(null);

  // 提取结果
  const [extracting, setExtracting] = useState(false);
  const [extractedItems, setExtractedItems] = useState<ParsedItem[]>([]);

  // 提交
  const [submitting, setSubmitting] = useState(false);

  // 历史
  const [submissions, setSubmissions] = useState<SubmissionRecord[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  // 加载省份
  useEffect(() => {
    api.get('/provinces').then((res) => {
      setProvinces(res.data.provinces || []);
      if (res.data.provinces?.length > 0) {
        setSelectedProvince(res.data.provinces[0]);
      }
    }).catch(() => message.error('获取省份列表失败'));
  }, [message]);

  // 加载历史
  const loadHistory = useCallback(() => {
    setLoadingHistory(true);
    api.get('/consult/submissions', { params: { page: 1, size: 50 } })
      .then((res) => setSubmissions(res.data.items || []))
      .catch(() => {})
      .finally(() => setLoadingHistory(false));
  }, []);

  useEffect(() => { loadHistory(); }, [loadHistory]);

  // 自动滚动到底部
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, sending]);

  // 发送消息
  const handleSend = async () => {
    const text = inputText.trim();
    if (!text && !pendingImage) return;

    // 构造用户消息
    const userMsg: ChatMsg = {
      role: 'user',
      content: text || '请看这张图片',
      image_base64: pendingImage?.base64,
      image_type: pendingImage?.type,
      image_preview: pendingImage?.preview,
    };

    const newMessages = [...messages, userMsg];
    setMessages(newMessages);
    setInputText('');
    // 记录图片路径（submit 时传给后端）
    if (pendingImage?.path) {
      setUploadedImagePaths((prev) => [...prev, pendingImage.path]);
    }
    setPendingImage(null);
    setSending(true);

    try {
      // 发送完整对话历史（不含前端专用字段）
      const apiMessages = newMessages.map((m) => ({
        role: m.role,
        content: m.content,
        image_base64: m.image_base64 || '',
        image_type: m.image_type || '',
      }));

      const res = await api.post('/consult/chat', { messages: apiMessages }, {
        timeout: 120000,
      });

      const assistantMsg: ChatMsg = {
        role: 'assistant',
        content: res.data.reply,
      };
      setMessages([...newMessages, assistantMsg]);
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      message.error(detail || '发送失败');
      // 移除刚发的用户消息（发送失败）
      setMessages(messages);
    } finally {
      setSending(false);
    }
  };

  // 上传图片
  const handleImageUpload = async (file: File) => {
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await api.post('/consult/upload-image', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 30000,
      });

      // 本地预览
      const reader = new FileReader();
      reader.onload = (e) => {
        setPendingImage({
          base64: res.data.image_base64,
          type: res.data.image_type,
          preview: e.target?.result as string,
          path: res.data.image_path,
        });
      };
      reader.readAsDataURL(file);

      message.success('图片已准备好，输入文字后一起发送');
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      message.error(detail || '图片上传失败');
    }
    return false;
  };

  // 提取结果
  const handleExtract = async () => {
    if (messages.length < 2) {
      message.warning('请先和贾维斯对话几轮再提取结果');
      return;
    }

    setExtracting(true);
    try {
      const apiMessages = messages.map((m) => ({
        role: m.role,
        content: m.content,
        image_base64: m.image_base64 || '',
        image_type: m.image_type || '',
      }));

      const res = await api.post('/consult/extract', { messages: apiMessages }, {
        timeout: 120000,
      });
      setExtractedItems(res.data.items || []);
      if (res.data.items?.length > 0) {
        message.success(`提取出 ${res.data.items.length} 条定额对应关系`);
      } else {
        message.warning('未能从对话中提取出定额信息');
      }
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      message.error(detail || '提取失败');
    } finally {
      setExtracting(false);
    }
  };

  // 编辑提取的结果
  const handleItemChange = (index: number, field: keyof ParsedItem, value: string) => {
    const newItems = [...extractedItems];
    newItems[index] = { ...newItems[index], [field]: value };
    setExtractedItems(newItems);
  };

  const handleDeleteItem = (index: number) => {
    setExtractedItems(extractedItems.filter((_, i) => i !== index));
  };

  const handleAddItem = () => {
    setExtractedItems([...extractedItems, { bill_name: '', quota_id: '', quota_name: '', unit: '' }]);
  };

  // 提交审核
  const handleSubmit = async () => {
    const validItems = extractedItems.filter(
      (item) => item.bill_name.trim() && item.quota_id.trim()
    );
    if (validItems.length === 0) {
      message.warning('没有有效记录（清单名称和定额编号不能为空）');
      return;
    }

    setSubmitting(true);
    try {
      await api.post('/consult/submit', {
        items: validItems,
        province: selectedProvince,
        image_path: uploadedImagePaths.length > 0 ? uploadedImagePaths[uploadedImagePaths.length - 1] : '',
      });
      message.success('提交成功，等待管理员审核');
      // 清空
      setMessages([]);
      setExtractedItems([]);
      setUploadedImagePaths([]);
      loadHistory();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      message.error(detail || '提交失败');
    } finally {
      setSubmitting(false);
    }
  };

  // 清空对话
  const handleClear = () => {
    setMessages([]);
    setExtractedItems([]);
    setPendingImage(null);
    setUploadedImagePaths([]);
  };

  // 提取结果表格列
  const itemColumns: ColumnsType<ParsedItem & { _index: number }> = [
    {
      title: '清单名称', dataIndex: 'bill_name', key: 'bill_name',
      render: (text, record) => (
        <Input value={text} size="small" placeholder="清单名称"
          onChange={(e) => handleItemChange(record._index, 'bill_name', e.target.value)} />
      ),
    },
    {
      title: '定额编号', dataIndex: 'quota_id', key: 'quota_id', width: 140,
      render: (text, record) => (
        <Input value={text} size="small" placeholder="C10-6-30"
          onChange={(e) => handleItemChange(record._index, 'quota_id', e.target.value)} />
      ),
    },
    {
      title: '定额名称', dataIndex: 'quota_name', key: 'quota_name',
      render: (text, record) => (
        <Input value={text} size="small" placeholder="定额名称"
          onChange={(e) => handleItemChange(record._index, 'quota_name', e.target.value)} />
      ),
    },
    {
      title: '单位', dataIndex: 'unit', key: 'unit', width: 80,
      render: (text, record) => (
        <Input value={text} size="small" placeholder="m/个"
          onChange={(e) => handleItemChange(record._index, 'unit', e.target.value)} />
      ),
    },
    {
      title: '操作', key: 'action', width: 60,
      render: (_, record) => (
        <Button type="text" danger size="small" icon={<DeleteOutlined />}
          onClick={() => handleDeleteItem(record._index)} />
      ),
    },
  ];

  // 状态标签
  const statusMap: Record<string, { color: string; text: string }> = {
    pending: { color: 'orange', text: '待审核' },
    approved: { color: 'green', text: '已通过' },
    rejected: { color: 'red', text: '已拒绝' },
  };

  // 历史表格
  const historyColumns: ColumnsType<SubmissionRecord> = [
    { title: '省份', dataIndex: 'province', key: 'province', width: 200, ellipsis: true },
    { title: '条目数', dataIndex: 'item_count', key: 'item_count', width: 80 },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 100,
      render: (s: string) => <Tag color={statusMap[s]?.color}>{statusMap[s]?.text || s}</Tag>,
    },
    {
      title: '审核备注', dataIndex: 'review_note', key: 'review_note', ellipsis: true,
      render: (n: string | null) => n || '-',
    },
    {
      title: '提交时间', dataIndex: 'created_at', key: 'created_at', width: 180,
      render: (t: string) => t ? new Date(t).toLocaleString('zh-CN') : '-',
    },
  ];

  return (
    <div>
      <Title level={3}>定额咨询</Title>
      <Text type="secondary">
        和贾维斯对话，确认定额后提交管理员审核。
      </Text>

      {/* 省份选择 */}
      <Card style={{ marginTop: 16 }}>
        <Space>
          <Text>省份：</Text>
          <Select
            value={selectedProvince}
            onChange={setSelectedProvince}
            style={{ width: 360 }}
            placeholder="选择省份"
            options={provinces.map((p) => ({ label: p, value: p }))}
          />
          <Button onClick={handleClear} disabled={messages.length === 0}>
            清空对话
          </Button>
        </Space>
      </Card>

      {/* 对话区域 */}
      <Card style={{ marginTop: 16 }}>
        <div style={{
          minHeight: 300, maxHeight: 500, overflowY: 'auto',
          padding: '8px 0', marginBottom: 16,
        }}>
          {messages.length === 0 && !sending && (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="输入清单描述，问贾维斯应该套什么定额"
            />
          )}

          {messages.map((msg, i) => (
            <div key={`${msg.role}-${i}`} style={{
              display: 'flex',
              justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
              marginBottom: 12,
            }}>
              <div style={{
                maxWidth: '80%',
                padding: '8px 12px',
                borderRadius: 8,
                background: msg.role === 'user' ? '#1677ff' : '#f5f5f5',
                color: msg.role === 'user' ? '#fff' : '#000',
              }}>
                {/* 图片预览 */}
                {msg.image_preview && (
                  <div style={{ marginBottom: 8 }}>
                    <img src={msg.image_preview} alt="截图" style={{
                      maxWidth: 300, maxHeight: 200, borderRadius: 4,
                    }} />
                  </div>
                )}
                <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
                  {msg.content}
                </div>
              </div>
            </div>
          ))}

          {sending && (
            <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 12 }}>
              <div style={{ padding: '8px 12px', borderRadius: 8, background: '#f5f5f5' }}>
                <Spin size="small" /> <Text type="secondary">贾维斯思考中...</Text>
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        {/* 待发送的图片预览 */}
        {pendingImage && (
          <div style={{ marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
            <img src={pendingImage.preview} alt="待发送" style={{
              height: 60, borderRadius: 4, border: '1px solid #d9d9d9',
            }} />
            <Button size="small" danger onClick={() => setPendingImage(null)}>移除</Button>
          </div>
        )}

        {/* 输入区域 */}
        <Space.Compact style={{ width: '100%' }}>
          <Upload
            accept=".png,.jpg,.jpeg,.webp,.bmp"
            showUploadList={false}
            beforeUpload={handleImageUpload}
            disabled={sending}
          >
            <Button icon={<PictureOutlined />} disabled={sending} />
          </Upload>
          <TextArea
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder="输入清单描述，如：给水管道DN25 PPR 该套什么定额？"
            autoSize={{ minRows: 1, maxRows: 3 }}
            onPressEnter={(e) => {
              if (!e.shiftKey) { e.preventDefault(); handleSend(); }
            }}
            disabled={sending}
            style={{ flex: 1 }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            onClick={handleSend}
            loading={sending}
            disabled={!inputText.trim() && !pendingImage}
          >
            发送
          </Button>
        </Space.Compact>

        {/* 提取按钮 */}
        {messages.length >= 2 && (
          <div style={{ marginTop: 12 }}>
            <Button
              icon={<CheckCircleOutlined />}
              onClick={handleExtract}
              loading={extracting}
            >
              提取对话中的定额结果
            </Button>
          </div>
        )}
      </Card>

      {/* 提取结果编辑区 */}
      {extractedItems.length > 0 && (
        <Card title="提取结果（可编辑）" style={{ marginTop: 16 }}>
          <Table
            columns={itemColumns}
            dataSource={extractedItems.map((item, i) => ({ ...item, _index: i, key: i }))}
            pagination={false}
            size="small"
          />
          <Space style={{ marginTop: 12 }}>
            <Button icon={<PlusOutlined />} onClick={handleAddItem}>新增一行</Button>
            <Popconfirm
              title="确认提交审核？"
              description={`将提交 ${extractedItems.filter(i => i.bill_name.trim() && i.quota_id.trim()).length} 条有效记录`}
              onConfirm={handleSubmit}
            >
              <Button type="primary" icon={<SendOutlined />} loading={submitting}>
                提交审核
              </Button>
            </Popconfirm>
          </Space>
        </Card>
      )}

      <Divider />

      {/* 历史记录 */}
      <Card
        title="我的提交记录"
        extra={
          <Button icon={<ReloadOutlined />} onClick={loadHistory} loading={loadingHistory} size="small">
            刷新
          </Button>
        }
      >
        <Table
          columns={historyColumns}
          dataSource={submissions}
          rowKey="id"
          pagination={{ pageSize: 10 }}
          size="small"
          locale={{ emptyText: '暂无提交记录' }}
        />
      </Card>
    </div>
  );
}
