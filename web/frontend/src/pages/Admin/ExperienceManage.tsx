/**
 * 管理员 — 经验库管理（广联达风格平铺表格）
 *
 * 表格样式参照广联达：清单行（项）和定额行（定）交替平铺在同一张表中，
 * 不需要点击展开，项目特征描述直接显示在清单行的专用列。
 *
 * 功能：
 * 1. 省份选择器（两级联动：地区 → 省份定额库）
 * 2. 统计卡片（权威层/候选层数量）
 * 3. Tab 切换：全部 / 权威层 / 候选层 / 搜索
 * 4. 筛选：按置信度、来源前端过滤
 * 5. 预览Modal：点"详情"弹窗查看完整信息
 * 6. 操作：晋升/降级/删除/智能批量晋升
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
  COLORS, GREEN_THRESHOLD, YELLOW_THRESHOLD,
  getBillRowBgColor, confidenceToTagColor, confidenceToLabel,
  sourceToLabel, parseBillFeatures, specialtyLabel,
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
  specialty?: string;
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

// 广联达风格：清单行和定额行混合平铺
interface BillDisplayRow {
  _rowType: 'bill';
  _rowKey: string;
  _record: ExperienceRecord;   // 原始记录（操作时需要）
  _seq: number;                // 序号
  _features: string[];         // 解析后的项目特征
  _quotaCount: number;         // 定额数量
}

interface QuotaDisplayRow {
  _rowType: 'quota';
  _rowKey: string;
  _record: ExperienceRecord;   // 所属清单的原始记录
  _quotaId: string;            // 定额编号
  _quotaName: string;          // 定额名称
}

// 分组标题行（如 "C4 电气"、"C10 给排水"）
interface SectionDisplayRow {
  _rowType: 'section';
  _rowKey: string;
  _sectionLabel: string;   // 显示文字，如 "C4 电气"
  _sectionCount: number;   // 该分组下的清单条数
}

type DisplayRow = SectionDisplayRow | BillDisplayRow | QuotaDisplayRow;

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

/**
 * 将经验记录列表转成广联达风格的平铺行（分组标题 + 清单行 + 定额行交替）
 *
 * 分组逻辑：按 specialty 字段分组，如 C4电气、C10给排水。
 * 排序：专业册号自然排序（C1→C12→A→D→E→未分类垫底）。
 */
function toDisplayRows(records: ExperienceRecord[]): DisplayRow[] {
  // 按 specialty 分组
  const groups = new Map<string, ExperienceRecord[]>();
  for (const r of records) {
    const key = r.specialty || '';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(r);
  }

  // 排序分组：C开头按数字、其他字母、空串垫底
  const sortedKeys = Array.from(groups.keys()).sort((a, b) => {
    if (!a) return 1;  // 空串（未分类）排最后
    if (!b) return -1;
    // C开头按数字大小排
    const ca = a.match(/^C(\d+)/);
    const cb = b.match(/^C(\d+)/);
    if (ca && cb) return Number(ca[1]) - Number(cb[1]);
    if (ca) return -1;  // C开头排前面
    if (cb) return 1;
    return a.localeCompare(b);
  });

  const rows: DisplayRow[] = [];
  let seq = 0;

  for (const key of sortedKeys) {
    const groupRecords = groups.get(key)!;

    // 分组标题行
    rows.push({
      _rowType: 'section',
      _rowKey: `section-${key || 'none'}`,
      _sectionLabel: specialtyLabel(key),
      _sectionCount: groupRecords.length,
    });

    // 该分组下的清单+定额行
    for (const r of groupRecords) {
      seq++;
      const quotaIds = parseJsonArray(r.quota_ids);
      const quotaNames = parseJsonArray(r.quota_names);
      const features = parseBillFeatures(r.bill_text);

      rows.push({
        _rowType: 'bill',
        _rowKey: `bill-${r.id}`,
        _record: r,
        _seq: seq,
        _features: features,
        _quotaCount: quotaIds.length,
      });

      quotaIds.forEach((qid, qi) => {
        rows.push({
          _rowType: 'quota',
          _rowKey: `quota-${r.id}-${qi}`,
          _record: r,
          _quotaId: qid,
          _quotaName: quotaNames[qi] || '',
        });
      });
    }
  }

  return rows;
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

  // 省份筛选（两级联动）
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

  const regionOptions = useMemo(() => {
    return Array.from(regionMap.entries()).map(([region, items]) => ({
      label: `${region}（${items.length} 个）`,
      value: region,
    }));
  }, [regionMap]);

  const provinceDbOptions = useMemo(() => {
    if (!selectedRegion) return [];
    const items = regionMap.get(selectedRegion) || [];
    return items.map((p) => ({
      label: `${p.province}（${p.count} 条）`,
      value: p.province,
    }));
  }, [selectedRegion, regionMap]);

  // 加载统计
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

  // 加载记录
  const loadRecords = useCallback(async (layer: string, p: number) => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { layer, page: p, size: 20 };
      if (selectedProvince) params.province = selectedProvince;
      const { data } = await api.get<{ items: ExperienceRecord[]; total: number }>(
        '/admin/experience/records', { params },
      );
      setRecords(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载经验记录失败');
    } finally {
      setLoading(false);
    }
  }, [message, selectedProvince]);

  useEffect(() => { loadStats(); }, [loadStats]);

  useEffect(() => {
    if (activeTab !== 'search') loadRecords(activeTab, page);
  }, [activeTab, page, loadRecords]);

  // 切回浏览器标签页时自动刷新
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === 'visible') {
        loadStats();
        if (activeTab !== 'search') loadRecords(activeTab, page);
      }
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [loadStats, loadRecords, activeTab, page]);

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

  const onProvinceChange = (value: string | undefined) => {
    setSelectedProvince(value);
    setPage(1);
  };

  const onSearch = async () => {
    if (!searchQuery.trim()) { message.warning('请输入搜索关键词'); return; }
    setSearchLoading(true);
    try {
      const params: Record<string, unknown> = { q: searchQuery.trim() };
      if (selectedProvince) params.province = selectedProvince;
      const { data } = await api.get<{ items: ExperienceRecord[] }>(
        '/admin/experience/search', { params },
      );
      setSearchResults(data.items);
    } catch {
      message.error('搜索失败');
    } finally {
      setSearchLoading(false);
    }
  };

  const promote = async (id: number) => {
    try {
      await api.post(`/admin/experience/${id}/promote`);
      message.success('晋升成功');
      loadRecords(activeTab, page); loadStats();
    } catch (err: unknown) { message.error(getErrorMessage(err, '晋升失败')); }
  };

  const demote = async (id: number) => {
    try {
      await api.post(`/admin/experience/${id}/demote`);
      message.success('降级成功');
      loadRecords(activeTab, page); loadStats();
    } catch (err: unknown) { message.error(getErrorMessage(err, '降级失败')); }
  };

  const deleteRecord = async (id: number) => {
    try {
      await api.delete(`/admin/experience/${id}`);
      message.success('删除成功');
      loadRecords(activeTab, page); loadStats();
    } catch { message.error('删除失败'); }
  };

  // 智能批量晋升
  const handleBatchPromote = async () => {
    setBatchLoading(true);
    try {
      const { data: preview } = await api.post<{
        total: number; promoted: number; skipped: number;
        errors: string[]; dry_run: boolean;
      }>('/admin/experience/batch-promote', {
        province: selectedProvince || null, dry_run: true,
      }, { timeout: 120000 });
      setBatchLoading(false);
      if (preview.total === 0) { message.info('没有可晋升的候选层记录'); return; }
      const scopeText = selectedProvince ? `「${selectedProvince}」` : '全部省份';
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
            <p style={{ marginTop: 12 }}>确定要晋升 {preview.promoted} 条记录到权威层吗？</p>
          </div>
        ),
        okText: `确认晋升 ${preview.promoted} 条`, cancelText: '取消',
        onOk: async () => {
          const { data: result } = await api.post<{ total: number; promoted: number; skipped: number }>(
            '/admin/experience/batch-promote',
            { province: selectedProvince || null, dry_run: false },
            { timeout: 300000 },
          );
          message.success(`批量晋升完成：${result.promoted} 条已晋升到权威层`);
          loadRecords(activeTab, page); loadStats();
        },
      });
    } catch { message.error('批量晋升失败'); setBatchLoading(false); }
  };

  // ============================================================
  // 前端筛选
  // ============================================================

  const applyFilters = useCallback((data: ExperienceRecord[]) => {
    let filtered = data;
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
    if (filterSource) {
      filtered = filtered.filter(r => r.source === filterSource);
    }
    return filtered;
  }, [filterConfidence, filterSource]);

  const filteredRecords = useMemo(() => applyFilters(records), [records, applyFilters]);
  const filteredSearchResults = useMemo(() => applyFilters(searchResults), [searchResults, applyFilters]);

  const currentTotal = selectedProvince
    ? (stats?.by_province?.[selectedProvince] || 0)
    : (stats?.total || 0);

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
      <Modal title="经验记录详情" open={!!previewRecord} onCancel={() => setPreviewRecord(null)} footer={null} width={700}>
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
            <Tag color={confidenceToTagColor(r.confidence ?? 0)}>{confidenceToLabel(r.confidence ?? 0)}</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="省份">{r.province || '-'}</Descriptions.Item>
          <Descriptions.Item label="来源">{sourceToLabel(r.source || '')}</Descriptions.Item>
          <Descriptions.Item label="创建时间" span={2}>{r.created_at || '-'}</Descriptions.Item>
        </Descriptions>
        {features.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>项目特征描述</div>
            <div style={{ background: '#FAFAFA', padding: '8px 12px', borderRadius: 6, fontSize: 13, lineHeight: 1.8 }}>
              {features.map((line, idx) => <div key={idx}>{line}</div>)}
            </div>
          </div>
        )}
        {quotaIds.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <div style={{ fontWeight: 500, marginBottom: 8 }}>定额子目（{quotaIds.length} 条）</div>
            {quotaIds.map((qid, idx) => (
              <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderLeft: '3px solid #91caff', marginBottom: 4, background: '#F0F5FF', borderRadius: '0 4px 4px 0' }}>
                <Tag color="blue" style={{ margin: 0 }}>{qid}</Tag>
                <span style={{ color: '#555' }}>{quotaNames[idx] || ''}</span>
              </div>
            ))}
          </div>
        )}
        <div style={{ marginTop: 16 }}>
          <div style={{ fontWeight: 500, marginBottom: 8 }}>原始清单文本</div>
          <div style={{ background: '#F5F5F5', padding: '8px 12px', borderRadius: 6, fontSize: 12, lineHeight: 1.8, whiteSpace: 'pre-wrap', maxHeight: 200, overflow: 'auto', color: '#666' }}>
            {r.bill_text || '-'}
          </div>
        </div>
      </Modal>
    );
  };

  // ============================================================
  // 广联达风格平铺表格列定义
  // ============================================================

  const flatColumns = [
    {
      title: '#',
      key: 'seq',
      width: 45,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;  // 分组标题行由 name 列合并显示
        if (row._rowType === 'bill') return <span style={{ color: '#333', fontSize: 12 }}>{row._seq}</span>;
        return null;
      },
    },
    {
      title: '编码',
      key: 'code',
      width: 120,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') {
          return <span style={{ fontSize: 12, fontFamily: 'monospace' }}>{row._record.bill_code || '-'}</span>;
        }
        // 定额行：蓝色编号
        return <span style={{ fontSize: 12, fontFamily: 'monospace', color: '#1677ff' }}>{row._quotaId}</span>;
      },
    },
    {
      title: '类别',
      key: 'type',
      width: 40,
      align: 'center' as const,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') return <span style={{ fontSize: 11 }}>项</span>;
        return <span style={{ fontSize: 11, color: '#1677ff' }}>定</span>;
      },
    },
    {
      title: '名称',
      key: 'name',
      width: 200,
      ellipsis: { showTitle: false },
      render: (_: unknown, row: DisplayRow) => {
        // 分组标题行：显示专业名称 + 数量
        if (row._rowType === 'section') {
          return (
            <span style={{ fontSize: 13, fontWeight: 600, color: '#1d39c4' }}>
              {row._sectionLabel}
              <span style={{ fontWeight: 400, color: '#8c8c8c', marginLeft: 8, fontSize: 12 }}>
                ({row._sectionCount} 条)
              </span>
            </span>
          );
        }
        if (row._rowType === 'bill') {
          const name = row._record.bill_name || row._record.bill_text?.split(/[\r\n]/)[0]?.slice(0, 50) || '-';
          return (
            <Tooltip title={name} placement="topLeft">
              <span style={{ fontSize: 12, fontWeight: 500 }}>{name}</span>
            </Tooltip>
          );
        }
        // 定额行名称
        return (
          <Tooltip title={row._quotaName} placement="topLeft">
            <span style={{ fontSize: 12, color: '#555' }}>{row._quotaName || '-'}</span>
          </Tooltip>
        );
      },
    },
    {
      title: '项目特征',
      key: 'features',
      width: 240,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType !== 'bill') return null;
        const features = row._features;
        if (features.length === 0) return <span style={{ color: '#ccc', fontSize: 12 }}>-</span>;
        return (
          <div style={{ fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
            {features.map((line, idx) => <div key={idx}>{line}</div>)}
          </div>
        );
      },
    },
    {
      title: '单位',
      key: 'unit',
      width: 45,
      align: 'center' as const,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') return <span style={{ fontSize: 12 }}>{row._record.bill_unit || '-'}</span>;
        return null;
      },
    },
    // 选了省份就隐藏省份列
    ...(!selectedProvince ? [{
      title: '省份',
      key: 'province',
      width: 80,
      ellipsis: { showTitle: false } as const,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section' || row._rowType !== 'bill') return null;
        return (
          <Tooltip title={row._record.province}>
            <span style={{ fontSize: 11 }}>{row._record.province}</span>
          </Tooltip>
        );
      },
    }] : []),
    {
      title: '层级',
      key: 'layer',
      width: 55,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section' || row._rowType !== 'bill') return null;
        const v = row._record.layer_type;
        return (
          <Tag color={v === 'authority' ? 'green' : 'orange'} style={{ fontSize: 11 }}>
            {v === 'authority' ? '权威' : '候选'}
          </Tag>
        );
      },
    },
    {
      title: '置信度',
      key: 'confidence',
      width: 75,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section' || row._rowType !== 'bill') return null;
        const v = row._record.confidence;
        if (v == null) return '-';
        return <Tag color={confidenceToTagColor(v)} style={{ fontSize: 11 }}>{confidenceToLabel(v)}</Tag>;
      },
    },
    {
      title: '来源',
      key: 'source',
      width: 80,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section' || row._rowType !== 'bill') return null;
        return <span style={{ fontSize: 11, color: '#666' }}>{sourceToLabel(row._record.source || '')}</span>;
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section' || row._rowType !== 'bill') return null;
        const record = row._record;
        return (
          <Space size="small">
            <Tooltip title="查看详情">
              <Button size="small" icon={<EyeOutlined />} onClick={() => setPreviewRecord(record)} />
            </Tooltip>
            {record.layer_type === 'candidate' && (
              <Button size="small" type="primary" icon={<ArrowUpOutlined />} onClick={() => promote(record.id)}>晋升</Button>
            )}
            {record.layer_type === 'authority' && (
              <Button size="small" icon={<ArrowDownOutlined />} onClick={() => demote(record.id)}>降级</Button>
            )}
            <Popconfirm title="确定删除？" onConfirm={() => deleteRecord(record.id)}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  // ============================================================
  // 筛选栏
  // ============================================================

  const renderFilters = () => (
    <div style={{ marginBottom: 12, display: 'flex', gap: 12, alignItems: 'center' }}>
      <span style={{ fontSize: 13, color: '#666' }}>筛选：</span>
      <Select allowClear placeholder="置信度" value={filterConfidence} onChange={setFilterConfidence}
        style={{ width: 130 }} options={[
          { label: '高 (≥85%)', value: 'high' },
          { label: '中 (60-84%)', value: 'mid' },
          { label: '低 (<60%)', value: 'low' },
        ]}
      />
      <Select allowClear placeholder="来源" value={filterSource} onChange={setFilterSource}
        style={{ width: 140 }} options={[
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
  // 渲染平铺表格
  // ============================================================

  const renderTable = (dataSource: ExperienceRecord[], tableLoading: boolean, showPagination = true) => {
    // 转成平铺行（清单行+定额行交替）
    const displayRows = toDisplayRows(dataSource);

    return (
      <>
        {renderFilters()}
        <Table<DisplayRow>
          rowKey="_rowKey"
          dataSource={displayRows}
          columns={flatColumns}
          loading={tableLoading}
          size="small"
          // 广联达风格行样式：分组标题蓝底、清单行着淡色背景、定额行蓝色左边框
          onRow={(row) => ({
            style: row._rowType === 'section'
              ? {
                  background: '#E6F4FF',
                  borderLeft: '4px solid #1677ff',
                }
              : row._rowType === 'bill'
              ? {
                  background: row._record.confidence != null
                    ? getBillRowBgColor(row._record.confidence)
                    : '#FAFAFA',
                  fontWeight: 500,
                }
              : {
                  background: '#F8FBFF',
                  borderLeft: '3px solid #91caff',
                },
          })}
          pagination={showPagination ? {
            current: page,
            total,
            showTotal: (t) => `共 ${t} 条经验记录`,
            onChange: (p) => setPage(p),
          } : false}
        />
      </>
    );
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 省份选择器 + 统计卡片 */}
      <Card>
        <div style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <EnvironmentOutlined style={{ fontSize: 16 }} />
          <span style={{ fontWeight: 500 }}>选择地区：</span>
          <Select allowClear placeholder="全部地区" value={selectedRegion} onChange={onRegionChange}
            style={{ width: 200 }} options={regionOptions} showSearch
            filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
          />
          <span style={{ fontWeight: 500 }}>选择省份：</span>
          <Select allowClear placeholder={selectedRegion ? '该地区全部省份' : '请先选择地区'}
            value={selectedProvince} onChange={onProvinceChange} disabled={!selectedRegion}
            style={{ width: 300 }} options={provinceDbOptions} showSearch
            filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
          />
          {selectedProvince && (
            <Tag color="blue">当前：{selectedProvince}（{currentTotal} 条）</Tag>
          )}
        </div>
        <Row gutter={16}>
          <Col span={8}>
            <Card><Statistic title={selectedProvince ? `${selectedProvince} - 总记录` : '总记录'} value={currentTotal} prefix={<DatabaseOutlined />} /></Card>
          </Col>
          <Col span={8}>
            <Card><Statistic title={selectedProvince ? '权威层（全部省份）' : '权威层'} value={stats?.authority || 0} valueStyle={{ color: COLORS.greenSolid }} /></Card>
          </Col>
          <Col span={8}>
            <Card><Statistic title={selectedProvince ? '候选层（全部省份）' : '候选层'} value={stats?.candidate || 0} valueStyle={{ color: COLORS.yellowSolid }} /></Card>
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
                  <Button type="primary" icon={<ThunderboltOutlined />} onClick={handleBatchPromote} loading={batchLoading}>
                    智能批量晋升
                  </Button>
                )}
                <Button icon={<ReloadOutlined />} onClick={() => loadRecords(activeTab, page)}>刷新</Button>
              </Space>
            )
          }
          items={[
            { key: 'all', label: '全部', children: renderTable(filteredRecords, loading) },
            { key: 'authority', label: '权威层', children: renderTable(filteredRecords, loading) },
            { key: 'candidate', label: '候选层', children: renderTable(filteredRecords, loading) },
            {
              key: 'search', label: '搜索',
              children: (
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Input.Search placeholder="输入清单文本搜索经验记录" value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)} onSearch={onSearch}
                    enterButton={<><SearchOutlined /> 搜索</>} loading={searchLoading} size="large"
                  />
                  {renderTable(filteredSearchResults, searchLoading, false)}
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {renderPreviewModal()}
    </Space>
  );
}
