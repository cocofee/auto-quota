/**
 * 管理员 — 经验库管理
 *
 * 改版：Table展示所有记录（和套定额预览页风格一致）
 * - 顶部统计卡片
 * - 筛选栏：省份、专业、层级、搜索
 * - 记录表格：清单名称 | 清单文本 | 定额编号 | 省份 | 专业 | 层级 | 置信度 | 来源 | 操作
 * - 支持分页、晋升/降级/删除
 */

import { useEffect, useState, useCallback, useMemo } from 'react';
import {
  Card, Table, Tag, Button, Space, App, Statistic, Row, Col,
  Select, Modal, Input, Tooltip, Segmented,
} from 'antd';
import {
  ReloadOutlined, DatabaseOutlined, SearchOutlined,
  EnvironmentOutlined, ThunderboltOutlined,
  SafetyOutlined, InboxOutlined, DeleteOutlined,
  ArrowUpOutlined, ArrowDownOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { extractRegion } from '../../utils/region';
import { COLORS, sourceToLabel, specialtyLabel } from '../../utils/experience';

// ============================================================
// 类型定义
// ============================================================

interface ExperienceStats {
  total: number;
  authority: number;
  candidate: number;
  by_source?: Record<string, number>;
  by_province?: Record<string, number>;
  by_specialty?: Record<string, number>;
  avg_confidence?: number;
  vector_count?: number;
}

interface ExperienceRecord {
  id: number;
  bill_text: string;
  bill_name?: string;
  province: string;
  quota_ids: string | string[];
  confidence?: number;
  source?: string;
  layer_type?: string;  // authority / candidate
  specialty?: string;
  created_at?: string;
  updated_at?: string;
  confirm_count?: number;
}

interface ProvinceItem {
  province: string;
  count: number;
}

// ============================================================
// 组件
// ============================================================

export default function ExperienceManage() {
  const { message } = App.useApp();

  // 统计数据
  const [stats, setStats] = useState<ExperienceStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);

  // 记录列表
  const [records, setRecords] = useState<ExperienceRecord[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [totalRecords, setTotalRecords] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  // 筛选条件
  const [filterProvince, setFilterProvince] = useState<string | undefined>(undefined);
  const [filterLayer, setFilterLayer] = useState<string>('all');
  const [searchKeyword, setSearchKeyword] = useState('');
  const [searchMode, setSearchMode] = useState(false); // 是否在搜索模式

  // 省份列表（从stats提取）
  const [provinces, setProvinces] = useState<ProvinceItem[]>([]);

  // 批量晋升
  const [batchLoading, setBatchLoading] = useState(false);
  const [selectedRegion, setSelectedRegion] = useState<string | undefined>(undefined);
  const [batchProvince, setBatchProvince] = useState<string | undefined>(undefined);

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

  const regionOptions = useMemo(() =>
    Array.from(regionMap.entries()).map(([region, items]) => ({
      label: `${region}（${items.length}个）`,
      value: region,
    })),
  [regionMap]);

  const batchProvinceOptions = useMemo(() => {
    if (!selectedRegion) return [];
    return (regionMap.get(selectedRegion) || []).map(p => ({
      label: `${p.province}（${p.count}条）`,
      value: p.province,
    }));
  }, [selectedRegion, regionMap]);

  // 省份下拉选项（带计数）
  const provinceOptions = useMemo(() =>
    provinces.map(p => ({
      label: `${p.province}（${p.count.toLocaleString()}条）`,
      value: p.province,
    })),
  [provinces]);

  // 专业统计（从stats提取，用于展示）
  const specialtyData = useMemo(() => {
    const bySpecialty = stats?.by_specialty || {};
    return Object.entries(bySpecialty)
      .map(([code, count]) => ({ code, label: specialtyLabel(code), count: count as number }))
      .sort((a, b) => b.count - a.count);
  }, [stats]);

  // ============================================================
  // 数据加载
  // ============================================================

  // 加载统计
  const loadStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const { data } = await api.get<ExperienceStats>('/admin/experience/stats');
      setStats(data);
      const byProvince = data.by_province || {};
      const list: ProvinceItem[] = Object.entries(byProvince)
        .map(([name, count]) => ({ province: name, count: count as number }))
        .sort((a, b) => b.count - a.count);
      setProvinces(list);
    } catch {
      message.error('加载经验库统计失败');
    } finally {
      setStatsLoading(false);
    }
  }, [message]);

  // 加载记录列表（分页）
  const loadRecords = useCallback(async () => {
    setRecordsLoading(true);
    setSearchMode(false);
    try {
      const { data } = await api.get<{
        items: ExperienceRecord[]; total: number; page: number; size: number;
      }>('/admin/experience/records', {
        params: {
          layer: filterLayer,
          province: filterProvince || undefined,
          page,
          size: pageSize,
        },
      });
      setRecords(data.items || []);
      setTotalRecords(data.total || 0);
    } catch {
      message.error('加载经验记录失败');
    } finally {
      setRecordsLoading(false);
    }
  }, [filterLayer, filterProvince, page, pageSize, message]);

  // 搜索
  const handleSearch = useCallback(async () => {
    const kw = searchKeyword.trim();
    if (!kw) {
      setSearchMode(false);
      loadRecords();
      return;
    }
    setRecordsLoading(true);
    setSearchMode(true);
    try {
      const { data } = await api.get<{ items: ExperienceRecord[]; total: number }>(
        '/admin/experience/search',
        { params: { q: kw, province: filterProvince || undefined, limit: 200 } },
      );
      setRecords(data.items || []);
      setTotalRecords(data.total || 0);
    } catch {
      message.error('搜索失败');
    } finally {
      setRecordsLoading(false);
    }
  }, [searchKeyword, filterProvince, loadRecords, message]);

  useEffect(() => { loadStats(); }, [loadStats]);
  useEffect(() => {
    if (!searchMode) loadRecords();
  }, [loadRecords]); // eslint-disable-line react-hooks/exhaustive-deps

  // ============================================================
  // 操作：晋升/降级/删除
  // ============================================================

  const handlePromote = async (id: number) => {
    try {
      await api.post(`/admin/experience/${id}/promote`);
      message.success('已晋升到权威层');
      loadRecords();
      loadStats();
    } catch {
      message.error('晋升失败');
    }
  };

  const handleDemote = async (id: number) => {
    try {
      await api.post(`/admin/experience/${id}/demote`);
      message.success('已降级到候选层');
      loadRecords();
      loadStats();
    } catch {
      message.error('降级失败');
    }
  };

  const handleDelete = (id: number, billText: string) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除「${billText.slice(0, 30)}」这条经验记录吗？`,
      okText: '删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        try {
          await api.delete(`/admin/experience/${id}`);
          message.success('删除成功');
          loadRecords();
          loadStats();
        } catch {
          message.error('删除失败');
        }
      },
    });
  };

  // 按省份删除
  const handleDeleteProvince = (province: string, count: number) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除「${province}」的全部 ${count} 条经验记录吗？此操作不可恢复。`,
      okText: '确认删除', okButtonProps: { danger: true }, cancelText: '取消',
      onOk: async () => {
        try {
          const { data } = await api.delete<{ deleted: number }>(
            '/admin/experience/by-province', { params: { province } },
          );
          message.success(`已删除「${province}」${data.deleted} 条记录`);
          loadRecords();
          loadStats();
        } catch {
          message.error('删除失败');
        }
      },
    });
  };

  // 智能批量晋升
  const handleBatchPromote = async () => {
    setBatchLoading(true);
    try {
      const { data: preview } = await api.post<{
        total: number; promoted: number; skipped: number;
        errors: string[]; dry_run: boolean;
      }>('/admin/experience/batch-promote', {
        province: batchProvince || null, dry_run: true,
      }, { timeout: 120000 });
      setBatchLoading(false);
      if (preview.total === 0) { message.info('没有可晋升的候选层记录'); return; }
      const scopeText = batchProvince ? `「${batchProvince}」` : '全部省份';
      Modal.confirm({
        title: '智能批量晋升预览', width: 500,
        content: (
          <div>
            <p>范围：{scopeText}</p>
            <p>候选层记录：<strong>{preview.total}</strong> 条</p>
            <p style={{ color: COLORS.greenSolid }}>校验通过（可晋升）：<strong>{preview.promoted}</strong> 条</p>
            {preview.skipped > 0 && (
              <p style={{ color: COLORS.yellowSolid }}>校验不通过（跳过）：<strong>{preview.skipped}</strong> 条</p>
            )}
            {preview.errors.length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
                <div>不通过示例：</div>
                {preview.errors.map((e, i) => <div key={i}>• {e}</div>)}
              </div>
            )}
          </div>
        ),
        okText: `确认晋升 ${preview.promoted} 条`, cancelText: '取消',
        onOk: async () => {
          const { data: result } = await api.post<{ promoted: number }>(
            '/admin/experience/batch-promote',
            { province: batchProvince || null, dry_run: false },
            { timeout: 300000 },
          );
          message.success(`批量晋升完成：${result.promoted} 条已晋升`);
          loadRecords();
          loadStats();
        },
      });
    } catch {
      message.error('批量晋升失败');
      setBatchLoading(false);
    }
  };

  // ============================================================
  // 解析定额编号
  // ============================================================

  const parseQuotaIds = (raw: string | string[]): string[] => {
    if (Array.isArray(raw)) return raw;
    if (!raw) return [];
    try {
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return raw ? [raw] : [];
    }
  };

  // ============================================================
  // 表格列定义
  // ============================================================

  const columns = [
    {
      title: '层级',
      key: 'layer',
      width: 80,
      render: (_: unknown, r: ExperienceRecord) => {
        const isAuth = r.layer_type === 'authority';
        return <Tag color={isAuth ? 'green' : 'orange'}>{isAuth ? '权威' : '候选'}</Tag>;
      },
    },
    {
      title: '省份',
      dataIndex: 'province',
      key: 'province',
      width: 100,
      ellipsis: true,
    },
    {
      title: '清单名称',
      key: 'bill_name',
      width: 180,
      render: (_: unknown, r: ExperienceRecord) => (
        <span style={{ fontWeight: 500 }}>{r.bill_name || '—'}</span>
      ),
    },
    {
      title: '清单文本',
      dataIndex: 'bill_text',
      key: 'bill_text',
      width: 260,
      render: (text: string) => (
        <div style={{ fontSize: 12, lineHeight: '1.6', whiteSpace: 'pre-wrap' }}>
          {text || '—'}
        </div>
      ),
    },
    {
      title: '定额编号',
      key: 'quota_ids',
      width: 180,
      render: (_: unknown, r: ExperienceRecord) => {
        const ids = parseQuotaIds(r.quota_ids);
        if (ids.length === 0) return <span style={{ color: '#ccc' }}>—</span>;
        return (
          <Space size={2} wrap>
            {ids.map((id, i) => (
              <Tag key={i} color="blue" style={{ margin: 0 }}>{id}</Tag>
            ))}
          </Space>
        );
      },
    },
    {
      title: '专业',
      dataIndex: 'specialty',
      key: 'specialty',
      width: 90,
      render: (v: string) => v ? <span style={{ fontSize: 12 }}>{specialtyLabel(v)}</span> : '—',
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 80,
      align: 'center' as const,
      sorter: (a: ExperienceRecord, b: ExperienceRecord) =>
        (a.confidence || 0) - (b.confidence || 0),
      render: (v: number | undefined) => {
        if (v == null) return '—';
        let color = COLORS.redSolid;
        if (v >= 85) color = COLORS.greenSolid;
        else if (v >= 60) color = COLORS.yellowSolid;
        return <span style={{ fontWeight: 600, color }}>{v}%</span>;
      },
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 100,
      ellipsis: true,
      render: (v: string) => (
        <span style={{ fontSize: 12, color: '#888' }}>{v ? sourceToLabel(v) : '—'}</span>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_: unknown, r: ExperienceRecord) => {
        const isAuth = r.layer_type === 'authority';
        return (
          <Space size={2}>
            {isAuth ? (
              <Tooltip title="降级到候选层">
                <Button type="text" size="small" icon={<ArrowDownOutlined />}
                  style={{ color: '#d48806' }}
                  onClick={() => handleDemote(r.id)} />
              </Tooltip>
            ) : (
              <Tooltip title="晋升到权威层">
                <Button type="text" size="small" icon={<ArrowUpOutlined />}
                  style={{ color: '#16a34a' }}
                  onClick={() => handlePromote(r.id)} />
              </Tooltip>
            )}
            <Tooltip title="删除">
              <Button type="text" size="small" danger icon={<DeleteOutlined />}
                onClick={() => handleDelete(r.id, r.bill_text || r.bill_name || '')} />
            </Tooltip>
          </Space>
        );
      },
    },
  ];

  // 行样式：权威层白色，候选层浅黄
  const rowClassName = (r: ExperienceRecord) =>
    r.layer_type === 'authority' ? 'exp-row-auth' : 'exp-row-cand';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <style>{`
        .exp-table .ant-table {
          border-radius: 8px;
          overflow: hidden;
          border: 1px solid #e8e8e8;
        }
        .exp-table .ant-table-thead > tr > th {
          background: #fafafa !important;
          font-weight: 600 !important;
          font-size: 13px;
          border-bottom: 2px solid #d9d9d9 !important;
        }
        .exp-table .ant-table-tbody > tr > td {
          border-bottom: 1px solid #f0f0f0;
          padding: 6px 8px !important;
        }
        .exp-table .ant-table-tbody > tr.exp-row-auth > td {
          background: #fff !important;
        }
        .exp-table .ant-table-tbody > tr.exp-row-cand > td {
          background: #fffbe6 !important;
        }
        .exp-table .ant-table-tbody > tr:hover > td {
          filter: brightness(0.97);
        }
      `}</style>

      {/* 第一行：统计概览 */}
      <Row gutter={[12, 12]}>
        <Col xs={12} sm={6}>
          <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
            <Statistic title="总记录" value={stats?.total || 0} prefix={<DatabaseOutlined />}
              valueStyle={{ fontSize: 28 }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
            <Statistic title="权威层" value={stats?.authority || 0}
              prefix={<SafetyOutlined />} valueStyle={{ fontSize: 28, color: COLORS.greenSolid }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
            <Statistic title="候选层" value={stats?.candidate || 0}
              prefix={<InboxOutlined />} valueStyle={{ fontSize: 28, color: COLORS.yellowSolid }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
            <Statistic title="省份/专业" value={`${provinces.length}省 ${specialtyData.length}专业`}
              prefix={<EnvironmentOutlined />} valueStyle={{ fontSize: 20 }} />
          </Card>
        </Col>
      </Row>

      {/* 第二行：筛选栏 + 记录表格 */}
      <Card
        styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            {/* 层级筛选 */}
            <Segmented
              value={filterLayer}
              onChange={v => { setFilterLayer(v as string); setPage(1); }}
              options={[
                { value: 'all', label: `全部 ${stats?.total?.toLocaleString() || 0}` },
                { value: 'authority', label: <span style={{ color: COLORS.greenSolid }}>权威 {stats?.authority?.toLocaleString() || 0}</span> },
                { value: 'candidate', label: <span style={{ color: COLORS.yellowSolid }}>候选 {stats?.candidate?.toLocaleString() || 0}</span> },
              ]}
              size="small"
            />

            <div style={{ width: 1, height: 16, background: '#e8e8e8' }} />

            {/* 省份筛选 */}
            <Select
              allowClear placeholder="全部省份"
              value={filterProvince}
              onChange={v => { setFilterProvince(v); setPage(1); }}
              style={{ width: 180 }}
              options={provinceOptions}
              showSearch optionFilterProp="label"
              size="small"
            />

            {/* 搜索 */}
            <Input
              placeholder="搜索清单名称/文本"
              prefix={<SearchOutlined />}
              value={searchKeyword}
              onChange={e => setSearchKeyword(e.target.value)}
              onPressEnter={handleSearch}
              style={{ width: 200 }}
              size="small"
              allowClear
              onClear={() => { setSearchKeyword(''); setSearchMode(false); }}
            />
            <Button size="small" type="primary" onClick={handleSearch}>搜索</Button>

            {searchMode && (
              <Tag color="blue">搜索结果：{totalRecords}条</Tag>
            )}
          </div>
        }
        extra={
          <Space size={4}>
            {/* 按省份删除（筛选了省份后可用） */}
            {filterProvince && (
              <Button size="small" danger icon={<DeleteOutlined />}
                onClick={() => {
                  const p = provinces.find(p => p.province === filterProvince);
                  handleDeleteProvince(filterProvince, p?.count || 0);
                }}>
                删除该省份
              </Button>
            )}
            <Button size="small" icon={<ReloadOutlined />}
              onClick={() => { loadStats(); loadRecords(); }}>
              刷新
            </Button>
          </Space>
        }
      >
        <Table
          className="exp-table"
          rowKey="id"
          dataSource={records}
          columns={columns}
          size="small"
          loading={recordsLoading}
          rowClassName={rowClassName}
          pagination={searchMode ? {
            pageSize: 200, showTotal: (t) => `共 ${t} 条`,
          } : {
            current: page,
            pageSize,
            total: totalRecords,
            showSizeChanger: true,
            pageSizeOptions: ['20', '50', '100'],
            showTotal: (t) => `共 ${t.toLocaleString()} 条`,
            onChange: (p, s) => { setPage(p); setPageSize(s); },
          }}
          scroll={{ x: 1200 }}
          locale={{ emptyText: '暂无经验记录（可能经验库未连接）' }}
        />
      </Card>

      {/* 第三行：批量操作 */}
      <Card title="批量操作" size="small" styles={{ body: { padding: '12px 20px' } }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <EnvironmentOutlined style={{ fontSize: 16 }} />
          <span>晋升范围：</span>
          <Select allowClear placeholder="全部地区" value={selectedRegion}
            onChange={v => {
              setSelectedRegion(v);
              if (v) {
                const items = regionMap.get(v) || [];
                setBatchProvince(items.length > 0 ? items[0].province : undefined);
              } else {
                setBatchProvince(undefined);
              }
            }}
            style={{ width: 180 }} options={regionOptions} showSearch size="small"
            filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
          />
          <Select allowClear placeholder={selectedRegion ? '该地区全部省份' : '请先选地区'}
            value={batchProvince} onChange={setBatchProvince} disabled={!selectedRegion}
            style={{ width: 260 }} options={batchProvinceOptions} showSearch size="small"
            filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
          />
          <Button type="primary" icon={<ThunderboltOutlined />} onClick={handleBatchPromote}
            loading={batchLoading} size="small">
            智能批量晋升
          </Button>
        </div>
      </Card>
    </div>
  );
}
