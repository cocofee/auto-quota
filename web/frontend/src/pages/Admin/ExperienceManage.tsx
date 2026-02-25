/**
 * 管理员 — 经验库管理
 *
 * 功能：
 * 1. 省份选择器（按省份分类查看经验记录）
 * 2. 统计卡片（权威层/候选层数量，跟随省份筛选变化）
 * 3. Tab 切换：全部 / 权威层 / 候选层 / 搜索
 * 4. 表格展示记录 + 晋升/降级/删除操作
 */

import { useEffect, useState, useCallback, useMemo } from 'react';
import {
  Card, Table, Tag, Button, Space, App, Tabs, Statistic, Row, Col,
  Input, Popconfirm, Select, Modal, Tooltip,
} from 'antd';
import {
  ArrowUpOutlined, ArrowDownOutlined, DeleteOutlined,
  SearchOutlined, ReloadOutlined, DatabaseOutlined,
  EnvironmentOutlined, ThunderboltOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { extractRegion } from '../../utils/region';
import { getErrorMessage } from '../../utils/error';

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

  // 批量晋升
  const [batchLoading, setBatchLoading] = useState(false);

  // 省份筛选（两级联动：地区 → 省份定额库）
  const [provinces, setProvinces] = useState<ProvinceItem[]>([]);
  const [selectedRegion, setSelectedRegion] = useState<string | undefined>(undefined);
  const [selectedProvince, setSelectedProvince] = useState<string | undefined>(undefined);

  // 按地区分组省份列表
  const regionMap = useMemo(() => {
    const map = new Map<string, ProvinceItem[]>();
    for (const p of provinces) {
      const region = extractRegion(p.province);
      if (!map.has(region)) map.set(region, []);
      map.get(region)!.push(p);
    }
    return map;
  }, [provinces]);

  // 地区下拉选项
  const regionOptions = useMemo(() => {
    return Array.from(regionMap.entries()).map(([region, items]) => ({
      label: `${region}（${items.length} 个）`,
      value: region,
    }));
  }, [regionMap]);

  // 当前地区下的省份定额库选项
  const provinceDbOptions = useMemo(() => {
    if (!selectedRegion) return [];
    const items = regionMap.get(selectedRegion) || [];
    return items.map((p) => ({
      label: `${p.province}（${p.count} 条）`,
      value: p.province,
    }));
  }, [selectedRegion, regionMap]);

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

  // 切换地区时：自动选中该地区第一个省份，重置分页
  const onRegionChange = (value: string | undefined) => {
    setSelectedRegion(value);
    if (value) {
      const items = regionMap.get(value) || [];
      setSelectedProvince(items.length > 0 ? items[0].province : undefined);
    } else {
      // 清空地区 → 清空省份（显示全部）
      setSelectedProvince(undefined);
    }
    setPage(1);
  };

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
      message.error(getErrorMessage(err, '晋升失败'));
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
      message.error(getErrorMessage(err, '降级失败'));
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

  // 智能批量晋升
  const handleBatchPromote = async () => {
    setBatchLoading(true);
    try {
      // 先预览（dry_run=true）
      const { data: preview } = await api.post<{
        total: number; promoted: number; skipped: number;
        errors: string[]; dry_run: boolean;
      }>('/admin/experience/batch-promote', {
        province: selectedProvince || null,
        dry_run: true,
      }, { timeout: 120000 });

      setBatchLoading(false);

      if (preview.total === 0) {
        message.info('没有可晋升的候选层记录');
        return;
      }

      // 弹窗确认
      const scopeText = selectedProvince ? `「${selectedProvince}」` : '全部省份';
      Modal.confirm({
        title: '智能批量晋升预览',
        width: 500,
        content: (
          <div>
            <p>范围：{scopeText}</p>
            <p>候选层记录：<strong>{preview.total}</strong> 条</p>
            <p style={{ color: '#52c41a' }}>
              校验通过（可晋升）：<strong>{preview.promoted}</strong> 条
            </p>
            {preview.skipped > 0 && (
              <p style={{ color: '#faad14' }}>
                校验不通过（跳过）：<strong>{preview.skipped}</strong> 条
              </p>
            )}
            {preview.errors.length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
                <div>不通过示例：</div>
                {preview.errors.map((e, i) => (
                  <div key={i}>• {e}</div>
                ))}
              </div>
            )}
            <p style={{ marginTop: 12 }}>确定要晋升 {preview.promoted} 条记录到权威层吗？</p>
          </div>
        ),
        okText: `确认晋升 ${preview.promoted} 条`,
        cancelText: '取消',
        onOk: async () => {
          // 实际执行（dry_run=false）
          const { data: result } = await api.post<{
            total: number; promoted: number; skipped: number;
          }>('/admin/experience/batch-promote', {
            province: selectedProvince || null,
            dry_run: false,
          }, { timeout: 300000 });

          message.success(`批量晋升完成：${result.promoted} 条已晋升到权威层`);
          loadRecords(activeTab, page);
          loadStats();
        },
      });
    } catch {
      message.error('批量晋升失败');
      setBatchLoading(false);
    }
  };

  // 计算当前省份的统计（如果选了省份，从 by_province 里取）
  const currentTotal = selectedProvince
    ? (stats?.by_province?.[selectedProvince] || 0)
    : (stats?.total || 0);

  // 表格列
  const columns = [
    {
      title: '#',
      dataIndex: 'id',
      key: 'id',
      width: 50,
      render: (v: number) => <span style={{ color: '#999' }}>{v}</span>,
    },
    {
      title: '清单文本',
      dataIndex: 'bill_text',
      key: 'bill_text',
      width: 300,
      render: (v: string) => {
        if (!v) return '-';
        // 在"名称:" "型号:" "规格:" 等关键词前换行，让内容结构清晰
        const formatted = v.replace(/ +(名称|型号|规格|单位|数量|安装方式|材质|材料|功率|电压|容量|尺寸|含|做法|特征)[:：]/g, '\n$1:');
        return (
          <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, lineHeight: 1.6 }}>
            {formatted}
          </div>
        );
      },
    },
    {
      title: '定额编号',
      dataIndex: 'quota_ids',
      key: 'quota_ids',
      width: 120,
      render: (v: string | string[]) => {
        const ids = Array.isArray(v) ? v : (typeof v === 'string' ? (() => { try { return JSON.parse(v); } catch { return [v]; } })() : []);
        return ids.length > 0 ? (
          <Space size={2} wrap>{ids.map((id: string, i: number) => <Tag key={i} color="blue" style={{ fontSize: 11 }}>{id}</Tag>)}</Space>
        ) : '-';
      },
    },
    {
      title: '定额名称',
      dataIndex: 'quota_names',
      key: 'quota_names',
      width: 200,
      ellipsis: { showTitle: false },
      render: (v: string | string[]) => {
        const names = Array.isArray(v) ? v : (typeof v === 'string' ? (() => { try { return JSON.parse(v); } catch { return v ? [v] : []; } })() : []);
        if (names.length === 0) return '-';
        const text = names.join('；');
        return (
          <Tooltip title={text} placement="topLeft">
            <span style={{ fontSize: 12 }}>{text}</span>
          </Tooltip>
        );
      },
    },
    // 选了省份就不显示省份列（节省空间）
    ...(!selectedProvince ? [{
      title: '省份',
      dataIndex: 'province',
      key: 'province',
      width: 100,
      ellipsis: { showTitle: false } as const,
      render: (v: string) => (
        <Tooltip title={v}>
          <span style={{ fontSize: 12 }}>{v}</span>
        </Tooltip>
      ),
    }] : []),
    {
      title: '层级',
      dataIndex: 'layer_type',
      key: 'layer_type',
      width: 70,
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
      width: 80,
      render: (v: string) => <Tag>{v || '-'}</Tag>,
    },
    {
      title: '置信',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 60,
      render: (v: number) => v != null ? `${v}` : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
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
        <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <EnvironmentOutlined style={{ fontSize: 16 }} />
          <span style={{ fontWeight: 500 }}>选择地区：</span>
          <Select
            allowClear
            placeholder="全部地区"
            value={selectedRegion}
            onChange={onRegionChange}
            style={{ width: 200 }}
            options={regionOptions}
            showSearch
            filterOption={(input, option) =>
              (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
            }
          />
          <span style={{ fontWeight: 500 }}>选择省份：</span>
          <Select
            allowClear
            placeholder={selectedRegion ? '该地区全部省份' : '请先选择地区'}
            value={selectedProvince}
            onChange={onProvinceChange}
            disabled={!selectedRegion}
            style={{ width: 300 }}
            options={provinceDbOptions}
            showSearch
            filterOption={(input, option) =>
              (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
            }
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
              <Statistic
                title={selectedProvince ? '权威层（全部省份）' : '权威层'}
                value={stats?.authority || 0}
                valueStyle={{ color: '#52c41a' }}
              />
            </Card>
          </Col>
          <Col span={8}>
            <Card>
              <Statistic
                title={selectedProvince ? '候选层（全部省份）' : '候选层'}
                value={stats?.candidate || 0}
                valueStyle={{ color: '#faad14' }}
              />
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
              <Space>
                {activeTab === 'candidate' && (
                  <Button
                    type="primary"
                    icon={<ThunderboltOutlined />}
                    onClick={handleBatchPromote}
                    loading={batchLoading}
                  >
                    智能批量晋升
                  </Button>
                )}
                <Button icon={<ReloadOutlined />} onClick={() => loadRecords(activeTab, page)}>
                  刷新
                </Button>
              </Space>
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
