/**
 * 管理员 — 经验库管理
 *
 * 功能：
 * 1. 省份选择器（按省份分类查看经验记录）
 * 2. 统计卡片（权威层/候选层数量，跟随省份筛选变化）
 * 3. Tab 切换：全部 / 权威层 / 候选层 / 搜索
 * 4. 表格展示记录 + 晋升/降级/删除操作
 */

import { useEffect, useState, useCallback } from 'react';
import {
  Card, Table, Tag, Button, Space, App, Tabs, Statistic, Row, Col,
  Input, Popconfirm, Select,
} from 'antd';
import {
  ArrowUpOutlined, ArrowDownOutlined, DeleteOutlined,
  SearchOutlined, ReloadOutlined, DatabaseOutlined,
  EnvironmentOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

interface ExperienceRecord {
  id: number;
  bill_text: string;
  bill_name?: string;
  quota_ids: string;
  quota_names?: string;
  province?: string;
  source?: string;
  layer_type?: string;
  confidence?: number;
  confirm_count?: number;
  created_at?: string;
  updated_at?: string;
}

interface ExperienceStats {
  total: number;
  authority: number;
  candidate: number;
  by_source?: Record<string, number>;
  by_province?: Record<string, number>;
  avg_confidence?: number;
}

interface ProvinceItem {
  province: string;
  count: number;
}

export default function ExperienceManage() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<ExperienceStats | null>(null);
  const [records, setRecords] = useState<ExperienceRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [activeTab, setActiveTab] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<ExperienceRecord[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);

  // 省份筛选
  const [provinces, setProvinces] = useState<ProvinceItem[]>([]);
  const [selectedProvince, setSelectedProvince] = useState<string | undefined>(undefined);

  // 加载统计（同时从 by_province 提取省份列表，避免重复请求）
  const loadStats = useCallback(async () => {
    try {
      const { data } = await api.get<ExperienceStats>('/admin/experience/stats');
      setStats(data);
      // 从统计数据中提取省份列表（替代原来单独的 /provinces 接口）
      const byProvince = data.by_province || {};
      const provinceList: ProvinceItem[] = Object.entries(byProvince)
        .map(([name, count]) => ({ province: name, count: count as number }))
        .sort((a, b) => b.count - a.count);
      setProvinces(provinceList);
    } catch {
      // 静默失败（经验库可能还没初始化）
    }
  }, []);

  // 加载记录（带省份过滤）
  const loadRecords = useCallback(async (layer: string, p: number) => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { layer, page: p, size: 20 };
      if (selectedProvince) {
        params.province = selectedProvince;
      }
      const { data } = await api.get<{ items: ExperienceRecord[]; total: number }>(
        '/admin/experience/records',
        { params },
      );
      setRecords(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载经验记录失败');
    } finally {
      setLoading(false);
    }
  }, [message, selectedProvince]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  useEffect(() => {
    if (activeTab !== 'search') {
      loadRecords(activeTab, page);
    }
  }, [activeTab, page, loadRecords]);

  // 切换省份时重置分页
  const onProvinceChange = (value: string | undefined) => {
    setSelectedProvince(value);
    setPage(1);
  };

  // 搜索（带省份过滤）
  const onSearch = async () => {
    if (!searchQuery.trim()) {
      message.warning('请输入搜索关键词');
      return;
    }
    setSearchLoading(true);
    try {
      const params: Record<string, unknown> = { q: searchQuery.trim() };
      if (selectedProvince) {
        params.province = selectedProvince;
      }
      const { data } = await api.get<{ items: ExperienceRecord[] }>(
        '/admin/experience/search',
        { params },
      );
      setSearchResults(data.items);
    } catch {
      message.error('搜索失败');
    } finally {
      setSearchLoading(false);
    }
  };

  // 晋升
  const promote = async (id: number) => {
    try {
      await api.post(`/admin/experience/${id}/promote`);
      message.success('晋升成功');
      loadRecords(activeTab, page);
      loadStats();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || '晋升失败');
    }
  };

  // 降级
  const demote = async (id: number) => {
    try {
      await api.post(`/admin/experience/${id}/demote`);
      message.success('降级成功');
      loadRecords(activeTab, page);
      loadStats();
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      message.error(detail || '降级失败');
    }
  };

  // 删除
  const deleteRecord = async (id: number) => {
    try {
      await api.delete(`/admin/experience/${id}`);
      message.success('删除成功');
      loadRecords(activeTab, page);
      loadStats();
    } catch {
      message.error('删除失败');
    }
  };

  // 计算当前省份的统计（如果选了省份，从 by_province 里取）
  const currentTotal = selectedProvince
    ? (stats?.by_province?.[selectedProvince] || 0)
    : (stats?.total || 0);

  // 表格列
  const columns = [
    {
      title: 'ID',
      dataIndex: 'id',
      key: 'id',
      width: 60,
    },
    {
      title: '清单文本',
      dataIndex: 'bill_text',
      key: 'bill_text',
      ellipsis: true,
    },
    {
      title: '定额编号',
      dataIndex: 'quota_ids',
      key: 'quota_ids',
      width: 150,
      ellipsis: true,
      render: (v: string) => v ? <Tag color="blue">{v}</Tag> : '-',
    },
    // 选了省份就不显示省份列（节省空间）
    ...(!selectedProvince ? [{
      title: '省份',
      dataIndex: 'province',
      key: 'province',
      width: 120,
      ellipsis: true,
    }] : []),
    {
      title: '层级',
      dataIndex: 'layer_type',
      key: 'layer_type',
      width: 80,
      render: (v: string) => (
        <Tag color={v === 'authority' ? 'green' : 'orange'}>
          {v === 'authority' ? '权威' : '候选'}
        </Tag>
      ),
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 100,
      render: (v: string) => <Tag>{v || '-'}</Tag>,
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 80,
      render: (v: number) => v != null ? `${v}` : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 200,
      render: (_: unknown, record: ExperienceRecord) => (
        <Space size="small">
          {record.layer_type === 'candidate' && (
            <Button size="small" type="primary" icon={<ArrowUpOutlined />} onClick={() => promote(record.id)}>
              晋升
            </Button>
          )}
          {record.layer_type === 'authority' && (
            <Button size="small" icon={<ArrowDownOutlined />} onClick={() => demote(record.id)}>
              降级
            </Button>
          )}
          <Popconfirm title="确定删除？" onConfirm={() => deleteRecord(record.id)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  // 表格组件（复用，避免重复代码）
  const renderTable = () => (
    <Table
      rowKey="id"
      dataSource={records}
      columns={columns}
      loading={loading}
      size="small"
      pagination={{
        current: page,
        total,
        showTotal: (t) => `共 ${t} 条`,
        onChange: (p) => setPage(p),
      }}
    />
  );

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 省份选择器 + 统计卡片 */}
      <Card>
        <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
          <EnvironmentOutlined style={{ fontSize: 16 }} />
          <span style={{ fontWeight: 500 }}>选择省份：</span>
          <Select
            allowClear
            placeholder="全部省份"
            value={selectedProvince}
            onChange={onProvinceChange}
            style={{ width: 300 }}
            options={provinces.map((p) => ({
              value: p.province,
              label: `${p.province}（${p.count} 条）`,
            }))}
          />
          {selectedProvince && (
            <Tag color="blue">
              当前：{selectedProvince}（{currentTotal} 条）
            </Tag>
          )}
        </div>
        <Row gutter={16}>
          <Col span={8}>
            <Card>
              <Statistic
                title={selectedProvince ? `${selectedProvince} - 总记录` : '总记录'}
                value={currentTotal}
                prefix={<DatabaseOutlined />}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card>
              <Statistic title="权威层" value={stats?.authority || 0} valueStyle={{ color: '#52c41a' }} />
            </Card>
          </Col>
          <Col span={8}>
            <Card>
              <Statistic title="候选层" value={stats?.candidate || 0} valueStyle={{ color: '#faad14' }} />
            </Card>
          </Col>
        </Row>
      </Card>

      {/* Tab 切换 + 表格 */}
      <Card>
        <Tabs
          activeKey={activeTab}
          onChange={(key) => { setActiveTab(key); setPage(1); }}
          tabBarExtraContent={
            activeTab !== 'search' && (
              <Button icon={<ReloadOutlined />} onClick={() => loadRecords(activeTab, page)}>
                刷新
              </Button>
            )
          }
          items={[
            { key: 'all', label: '全部', children: renderTable() },
            { key: 'authority', label: '权威层', children: renderTable() },
            { key: 'candidate', label: '候选层', children: renderTable() },
            {
              key: 'search',
              label: '搜索',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Input.Search
                    placeholder="输入清单文本搜索经验记录"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onSearch={onSearch}
                    enterButton={<><SearchOutlined /> 搜索</>}
                    loading={searchLoading}
                    size="large"
                  />
                  <Table
                    rowKey="id"
                    dataSource={searchResults}
                    columns={columns}
                    loading={searchLoading}
                    size="small"
                    pagination={false}
                    locale={{ emptyText: '输入关键词搜索经验记录' }}
                  />
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
