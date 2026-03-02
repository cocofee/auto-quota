/**
 * 管理员 — 经验库管理（广联达风格）
 *
 * 功能：
 * 1. 省份选择器（两级联动：地区 → 省份定额库）
 * 2. 统计卡片（权威层/候选层数量，跟随省份变化）
 * 3. Tab 切换：全部 / 权威层 / 候选层 / 搜索
 * 4. 表格：清单行可展开，上方显示项目特征描述，下方显示定额子行
 * 5. 筛选：按置信度、来源前端过滤
 * 6. 预览Modal：点"详情"弹窗查看完整信息
 * 7. 操作：晋升/降级/删除/智能批量晋升
 */

import { useEffect, useState, useCallback, useMemo } from 'react';
import {
  Card, Table, Tag, Button, Space, App, Tabs, Statistic, Row, Col,
  Input, Popconfirm, Select, Modal, Tooltip, Descriptions,
} from 'antd';
import {
  ArrowUpOutlined, ArrowDownOutlined, DeleteOutlined,
  SearchOutlined, ReloadOutlined, DatabaseOutlined,
  EnvironmentOutlined, ThunderboltOutlined, EyeOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { extractRegion } from '../../utils/region';
import { getErrorMessage } from '../../utils/error';
import {
  GREEN_THRESHOLD, YELLOW_THRESHOLD,
  getBillRowBgColor, confidenceToTagColor, confidenceToLabel,
  sourceToLabel, parseBillFeatures,
} from '../../utils/experience';

// ============================================================
// 类型定义
// ============================================================

interface ExperienceRecord {
  id: number;
  bill_text: string;
  bill_name?: string;
  bill_code?: string;
  bill_unit?: string;
  quota_ids: string | string[];
  quota_names?: string | string[];
  province?: string;
  source?: string;
  layer_type?: string;
  confidence?: number;
  created_at?: string;
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

// ============================================================
// 工具函数
// ============================================================

/** 把 quota_ids/quota_names 统一解析成 string[] */
function parseJsonArray(v: string | string[] | undefined | null): string[] {
  if (!v) return [];
  if (Array.isArray(v)) return v;
  if (typeof v === 'string') {
    try { return JSON.parse(v); } catch { return v ? [v] : []; }
  }
  return [];
}

// ============================================================
// 组件
// ============================================================

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

  // 前端筛选
  const [filterConfidence, setFilterConfidence] = useState<string | undefined>(undefined);
  const [filterSource, setFilterSource] = useState<string | undefined>(undefined);

  // 预览Modal
  const [previewRecord, setPreviewRecord] = useState<ExperienceRecord | null>(null);

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

  // 加载统计（同时从 by_province 提取省份列表）
  const loadStats = useCallback(async () => {
    try {
      const { data } = await api.get<ExperienceStats>('/admin/experience/stats');
      setStats(data);
      const byProvince = data.by_province || {};
      const provinceList: ProvinceItem[] = Object.entries(byProvince)
        .map(([name, count]) => ({ province: name, count: count as number }))
        .sort((a, b) => b.count - a.count);
      setProvinces(provinceList);
    } catch {
      // 静默失败
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

  // 切回浏览器标签页时自动刷新
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        loadStats();
        if (activeTab !== 'search') {
          loadRecords(activeTab, page);
        }
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [loadStats, loadRecords, activeTab, page]);

  // 切换地区时自动选中第一个省份，重置分页
  const onRegionChange = (value: string | undefined) => {
    setSelectedRegion(value);
    if (value) {
      const items = regionMap.get(value) || [];
      setSelectedProvince(items.length > 0 ? items[0].province : undefined);
    } else {
      setSelectedProvince(undefined);
    }
    setPage(1);
  };

  // 切换省份时重置分页
  const onProvinceChange = (value: string | undefined) => {
    setSelectedProvince(value);
    setPage(1);
  };

  // 搜索
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

  // ============================================================
  // 前端筛选：在已加载的数据上做过滤
  // ============================================================

  const applyFilters = useCallback((data: ExperienceRecord[]) => {
    let filtered = data;

    // 置信度筛选
    if (filterConfidence === 'high') {
      filtered = filtered.filter(r => (r.confidence ?? 0) >= GREEN_THRESHOLD);
    } else if (filterConfidence === 'mid') {
      filtered = filtered.filter(r => {
        const c = r.confidence ?? 0;
        return c >= YELLOW_THRESHOLD && c < GREEN_THRESHOLD;
      });
    } else if (filterConfidence === 'low') {
      filtered = filtered.filter(r => (r.confidence ?? 0) < YELLOW_THRESHOLD);
    }

    // 来源筛选
    if (filterSource) {
      filtered = filtered.filter(r => r.source === filterSource);
    }

    return filtered;
  }, [filterConfidence, filterSource]);

  // 过滤后的数据（给当前Tab用）
  const filteredRecords = useMemo(() => applyFilters(records), [records, applyFilters]);
  const filteredSearchResults = useMemo(() => applyFilters(searchResults), [searchResults, applyFilters]);

  // 当前省份统计
  const currentTotal = selectedProvince
    ? (stats?.by_province?.[selectedProvince] || 0)
    : (stats?.total || 0);

  // ============================================================
  // 展开行渲染（上：项目特征描述，下：定额子行）
  // ============================================================

  const renderExpandedRow = (record: ExperienceRecord) => {
    const features = parseBillFeatures(record.bill_text);
    const quotaIds = parseJsonArray(record.quota_ids);
    const quotaNames = parseJsonArray(record.quota_names);

    return (
      <div style={{ padding: '8px 16px', background: '#FAFAFA' }}>
        {/* 项目特征描述 */}
        {features.length > 0 && (
          <div style={{ marginBottom: quotaIds.length > 0 ? 12 : 0 }}>
            <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>项目特征描述</div>
            <div style={{ fontSize: 12, lineHeight: 1.8, paddingLeft: 8 }}>
              {features.map((line, idx) => (
                <div key={idx} style={{ color: '#333' }}>{line}</div>
              ))}
            </div>
          </div>
        )}

        {/* 定额子行 */}
        {quotaIds.length > 0 && (
          <div>
            <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>定额子目</div>
            {quotaIds.map((qid, idx) => (
              <div
                key={idx}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '4px 8px',
                  borderLeft: '3px solid #91caff',
                  marginBottom: 2,
                  background: '#F0F5FF',
                  borderRadius: '0 4px 4px 0',
                }}
              >
                <Tag color="blue" style={{ fontSize: 11, margin: 0 }}>{qid}</Tag>
                <span style={{ fontSize: 12, color: '#555' }}>
                  {quotaNames[idx] || ''}
                </span>
              </div>
            ))}
          </div>
        )}

        {features.length === 0 && quotaIds.length === 0 && (
          <span style={{ color: '#ccc', fontSize: 12 }}>无详细信息</span>
        )}
      </div>
    );
  };

  // ============================================================
  // 预览Modal
  // ============================================================

  const renderPreviewModal = () => {
    if (!previewRecord) return null;
    const r = previewRecord;
    const quotaIds = parseJsonArray(r.quota_ids);
    const quotaNames = parseJsonArray(r.quota_names);
    const features = parseBillFeatures(r.bill_text);

    return (
      <Modal
        title="经验记录详情"
        open={!!previewRecord}
        onCancel={() => setPreviewRecord(null)}
        footer={null}
        width={700}
      >
        <Descriptions column={2} bordered size="small">
          <Descriptions.Item label="ID">{r.id}</Descriptions.Item>
          <Descriptions.Item label="层级">
            <Tag color={r.layer_type === 'authority' ? 'green' : 'orange'}>
              {r.layer_type === 'authority' ? '权威层' : '候选层'}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="清单编码">{r.bill_code || '-'}</Descriptions.Item>
          <Descriptions.Item label="清单名称">{r.bill_name || '-'}</Descriptions.Item>
          <Descriptions.Item label="单位">{r.bill_unit || '-'}</Descriptions.Item>
          <Descriptions.Item label="置信度">
            <Tag color={confidenceToTagColor(r.confidence ?? 0)}>
              {confidenceToLabel(r.confidence ?? 0)}
            </Tag>
          </Descriptions.Item>
          <Descriptions.Item label="省份">{r.province || '-'}</Descriptions.Item>
          <Descriptions.Item label="来源">{sourceToLabel(r.source || '')}</Descriptions.Item>
          <Descriptions.Item label="创建时间" span={2}>{r.created_at || '-'}</Descriptions.Item>
        </Descriptions>

        {/* 项目特征描述 */}
        {features.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>项目特征描述</div>
            <div style={{
              background: '#FAFAFA', padding: '8px 12px', borderRadius: 6,
              fontSize: 13, lineHeight: 1.8,
            }}>
              {features.map((line, idx) => (
                <div key={idx}>{line}</div>
              ))}
            </div>
          </div>
        )}

        {/* 定额列表 */}
        {quotaIds.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>定额子目（{quotaIds.length} 条）</div>
            {quotaIds.map((qid, idx) => (
              <div
                key={idx}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '6px 12px',
                  borderLeft: '3px solid #91caff',
                  marginBottom: 4,
                  background: '#F0F5FF',
                  borderRadius: '0 4px 4px 0',
                }}
              >
                <Tag color="blue" style={{ margin: 0 }}>{qid}</Tag>
                <span style={{ color: '#555' }}>{quotaNames[idx] || ''}</span>
              </div>
            ))}
          </div>
        )}

        {/* 原始清单文本（折叠显示） */}
        <div style={{ marginTop: 16 }}>
          <div style={{ fontWeight: 500, marginBottom: 8 }}>原始清单文本</div>
          <div style={{
            background: '#F5F5F5', padding: '8px 12px', borderRadius: 6,
            fontSize: 12, lineHeight: 1.8, whiteSpace: 'pre-wrap',
            maxHeight: 200, overflow: 'auto', color: '#666',
          }}>
            {r.bill_text || '-'}
          </div>
        </div>
      </Modal>
    );
  };

  // ============================================================
  // 表格列定义
  // ============================================================

  const columns = [
    {
      title: '#',
      dataIndex: 'id',
      key: 'id',
      width: 55,
      render: (v: number) => <span style={{ color: '#999', fontSize: 12 }}>{v}</span>,
    },
    {
      title: '清单编码',
      dataIndex: 'bill_code',
      key: 'bill_code',
      width: 110,
      render: (v: string) => (
        <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{v || '-'}</span>
      ),
    },
    {
      title: '清单名称',
      dataIndex: 'bill_name',
      key: 'bill_name',
      width: 180,
      ellipsis: { showTitle: false },
      render: (v: string, record: ExperienceRecord) => {
        const name = v || record.bill_text?.split(/[\r\n]/)[0]?.slice(0, 40) || '-';
        return (
          <Tooltip title={name} placement="topLeft">
            <span style={{ fontSize: 12, fontWeight: 500 }}>{name}</span>
          </Tooltip>
        );
      },
    },
    {
      title: '单位',
      dataIndex: 'bill_unit',
      key: 'bill_unit',
      width: 50,
      align: 'center' as const,
      render: (v: string) => <span style={{ fontSize: 12 }}>{v || '-'}</span>,
    },
    {
      title: '定额',
      key: 'quotas',
      width: 100,
      render: (_: unknown, record: ExperienceRecord) => {
        const ids = parseJsonArray(record.quota_ids);
        if (ids.length === 0) return <span style={{ color: '#ccc' }}>-</span>;
        return (
          <Space size={2} wrap>
            {ids.slice(0, 2).map((id, i) => (
              <Tag key={i} color="blue" style={{ fontSize: 11 }}>{id}</Tag>
            ))}
            {ids.length > 2 && (
              <Tag style={{ fontSize: 11 }}>+{ids.length - 2}</Tag>
            )}
          </Space>
        );
      },
    },
    // 选了省份就隐藏省份列
    ...(!selectedProvince ? [{
      title: '省份',
      dataIndex: 'province',
      key: 'province',
      width: 90,
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
      width: 60,
      render: (v: string) => (
        <Tag color={v === 'authority' ? 'green' : 'orange'} style={{ fontSize: 11 }}>
          {v === 'authority' ? '权威' : '候选'}
        </Tag>
      ),
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 80,
      render: (v: number) => {
        if (v == null) return '-';
        return (
          <Tag color={confidenceToTagColor(v)} style={{ fontSize: 11 }}>
            {confidenceToLabel(v)}
          </Tag>
        );
      },
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 85,
      render: (v: string) => (
        <span style={{ fontSize: 11, color: '#666' }}>{sourceToLabel(v || '')}</span>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 160,
      render: (_: unknown, record: ExperienceRecord) => (
        <Space size="small">
          <Tooltip title="查看详情">
            <Button
              size="small"
              icon={<EyeOutlined />}
              onClick={() => setPreviewRecord(record)}
            />
          </Tooltip>
          {record.layer_type === 'candidate' && (
            <Button size="small" type="primary" icon={<ArrowUpOutlined />}
              onClick={() => promote(record.id)}>
              晋升
            </Button>
          )}
          {record.layer_type === 'authority' && (
            <Button size="small" icon={<ArrowDownOutlined />}
              onClick={() => demote(record.id)}>
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

  // ============================================================
  // 筛选栏
  // ============================================================

  const renderFilters = () => (
    <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
      <span style={{ fontSize: 13, color: '#666' }}>筛选：</span>
      <Select
        allowClear
        placeholder="置信度"
        value={filterConfidence}
        onChange={setFilterConfidence}
        style={{ width: 130 }}
        options={[
          { label: '高 (≥85%)', value: 'high' },
          { label: '中 (60-84%)', value: 'mid' },
          { label: '低 (<60%)', value: 'low' },
        ]}
      />
      <Select
        allowClear
        placeholder="来源"
        value={filterSource}
        onChange={setFilterSource}
        style={{ width: 140 }}
        options={[
          { label: '自动匹配', value: 'auto_match' },
          { label: 'Jarvis纠正', value: 'jarvis_correction' },
          { label: '项目导入', value: 'project_import' },
          { label: '项目导入(待审)', value: 'project_import_suspect' },
          { label: '用户确认', value: 'user_confirmed' },
          { label: '用户修正', value: 'user_correction' },
        ]}
      />
      {(filterConfidence || filterSource) && (
        <Button size="small" onClick={() => { setFilterConfidence(undefined); setFilterSource(undefined); }}>
          清除筛选
        </Button>
      )}
    </div>
  );

  // ============================================================
  // 表格组件（复用，带展开行和行着色）
  // ============================================================

  const renderTable = (dataSource: ExperienceRecord[], tableLoading: boolean, showPagination = true) => (
    <>
      {renderFilters()}
      <Table
        rowKey="id"
        dataSource={dataSource}
        columns={columns}
        loading={tableLoading}
        size="small"
        expandable={{
          expandedRowRender: renderExpandedRow,
          rowExpandable: () => true,
        }}
        onRow={(record) => ({
          style: {
            background: record.confidence != null
              ? getBillRowBgColor(record.confidence)
              : undefined,
          },
        })}
        pagination={showPagination ? {
          current: page,
          total,
          showTotal: (t) => `共 ${t} 条${dataSource.length < t ? `（显示 ${dataSource.length} 条）` : ''}`,
          onChange: (p) => setPage(p),
        } : false}
      />
    </>
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
            { key: 'all', label: '全部', children: renderTable(filteredRecords, loading) },
            { key: 'authority', label: '权威层', children: renderTable(filteredRecords, loading) },
            { key: 'candidate', label: '候选层', children: renderTable(filteredRecords, loading) },
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
                  {renderTable(filteredSearchResults, searchLoading, false)}
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* 预览Modal */}
      {renderPreviewModal()}
    </Space>
  );
}
