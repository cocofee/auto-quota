import { useEffect, useState, useCallback, useMemo } from 'react';
import {
  App,
  Button,
  Card,
  Col,
  Input,
  Modal,
  Pagination,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Tooltip,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EnvironmentOutlined,
  InboxOutlined,
  ReloadOutlined,
  SafetyOutlined,
  SearchOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { extractRegion } from '../../utils/region';
import {
  COLORS,
  GREEN_THRESHOLD,
  YELLOW_THRESHOLD,
  getBillRowBgColor,
  sourceToLabel,
  specialtyLabel,
} from '../../utils/experience';

interface ExperienceStats {
  total: number;
  authority: number;
  candidate: number;
  by_source?: Record<string, number>;
  by_province?: Record<string, number>;
  by_specialty?: Record<string, number>;
  avg_confidence?: number;
  vector_count?: number;
  historical_total?: number;
  historical_sources?: Record<string, { label: string; count: number; table?: string; db_path?: string }>;
}

interface ExperienceMaterial {
  quota_code?: string;
  name?: string;
  code?: string;
  unit?: string;
  price?: number | string;
  spec?: string;
  desc?: string;
  [key: string]: unknown;
}

interface ExperienceRecord {
  id: number;
  bill_text: string;
  bill_name?: string;
  province: string;
  specialty?: string;
  quota_ids: string[] | string;
  quota_names?: string[] | string;
  materials?: ExperienceMaterial[] | string;
  confidence?: number;
  source?: string;
  layer_type?: string;
  created_at?: string;
  updated_at?: string;
  confirm_count?: number;
  bill_code?: string;
  bill_unit?: string;
  notes?: string;
}

interface ProvinceItem {
  province: string;
  count: number;
}

interface BillDisplayRow {
  _rowType: 'bill';
  _rowKey: string;
  _record: ExperienceRecord;
  _quotaCount: number;
  _materialCount: number;
}

interface QuotaDisplayRow {
  _rowType: 'quota';
  _rowKey: string;
  _record: ExperienceRecord;
  _quotaId: string;
  _quotaName: string;
  _quotaSpecialty: string;
  _materialCount: number;
}

interface MaterialDisplayRow {
  _rowType: 'material';
  _rowKey: string;
  _record: ExperienceRecord;
  _material: ExperienceMaterial;
  _quotaId: string;
  _quotaSpecialty: string;
}

type DisplayRow = BillDisplayRow | QuotaDisplayRow | MaterialDisplayRow;

const SPECIALTY_FILTERS = ['安装', '建筑装饰', '市政', '园林绿化', '电力', '光伏'] as const;


function parseStringArray(raw: string[] | string | undefined): string[] {
  if (Array.isArray(raw)) return raw.filter(Boolean).map(String);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(Boolean).map(String) : [];
  } catch {
    return raw ? [String(raw)] : [];
  }
}

function parseMaterials(raw: ExperienceMaterial[] | string | undefined): ExperienceMaterial[] {
  if (Array.isArray(raw)) return raw.filter(Boolean);
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(Boolean) : [];
  } catch {
    return [];
  }
}

function inferSpecialtyFromProvinceLabel(name?: string | null): string {
  const text = String(name || '');
  if (!text) return '';
  if (/光伏|发电|升压站/i.test(text)) return '光伏';
  if (/电力|输电|变电|配电/i.test(text)) return '电力';
  if (/园林|绿化/.test(text)) return '园林绿化';
  if (/市政/.test(text)) return '市政';
  if (/装饰|装修/.test(text)) return '建筑装饰';
  if (/安装/.test(text)) return '安装';
  return '';
}

function normalizeCode(text?: string | null): string {
  return String(text || '').trim().replace(/\s+/g, '').toUpperCase();
}

function inferSpecialtyCodeFromQuotaId(quotaId?: string | null): string {
  if (!quotaId) return '';
  const alphaMatch = String(quotaId).trim().match(/^([A-Za-z]+\d{0,2})-/);
  if (alphaMatch) return alphaMatch[1].toUpperCase();
  const numericMatch = String(quotaId).trim().match(/^(\d{1,2})-/);
  if (!numericMatch) return '';
  const num = Number(numericMatch[1]);
  if (Number.isNaN(num)) return '';
  return num >= 1 && num <= 12 ? `C${num}` : String(num);
}

function inferDisplayCategory(
  record: ExperienceRecord,
  quotaId?: string,
  quotaName?: string,
): string {
  const textParts = [
    record.bill_name || '',
    record.bill_text || '',
    quotaId || '',
    quotaName || '',
    ...parseStringArray(record.quota_names),
    ...parseStringArray(record.quota_ids),
  ];
  const text = textParts.join(' ').toLowerCase();

  if (/光伏|升压站|发电/.test(text)) return '光伏';
  if (/电力|变电|输电|配电装置|电力电缆|变压器|母线|开关站|开闭所|间隔/.test(text)) return '电力';

  const specialtyCode = inferSpecialtyCodeFromQuotaId(quotaId) || record.specialty || '';
  if (specialtyCode.startsWith('C')) return '安装';
  if (specialtyCode === 'A' || specialtyCode === 'B') return '建筑装饰';
  if (specialtyCode === 'D') return '市政';
  if (specialtyCode === 'E') return '园林绿化';

  return '安装';
}

function materialBelongsToQuota(material: ExperienceMaterial, quotaId: string): boolean {
  const materialQuota = normalizeCode(String(material.quota_code || ''));
  const normalizedQuotaId = normalizeCode(quotaId);
  return Boolean(materialQuota && normalizedQuotaId && materialQuota === normalizedQuotaId);
}

function buildDisplayRows(records: ExperienceRecord[]): DisplayRow[] {
  const rows: DisplayRow[] = [];

  records.forEach((record) => {
    const quotaIds = parseStringArray(record.quota_ids);
    const quotaNames = parseStringArray(record.quota_names);
    const materials = parseMaterials(record.materials);

    rows.push({
      _rowType: 'bill',
      _rowKey: `bill-${record.id}`,
      _record: record,
      _quotaCount: quotaIds.length,
      _materialCount: materials.length,
    });

    const matchedMaterialIndexes = new Set<number>();

    quotaIds.forEach((quotaId, quotaIndex) => {
      const quotaMaterials = materials.filter((material) => materialBelongsToQuota(material, quotaId));
      const quotaSpecialty = inferSpecialtyCodeFromQuotaId(quotaId) || record.specialty || '';

      rows.push({
        _rowType: 'quota',
        _rowKey: `quota-${record.id}-${quotaIndex}`,
        _record: record,
        _quotaId: quotaId,
        _quotaName: quotaNames[quotaIndex] || '',
        _quotaSpecialty: quotaSpecialty,
        _materialCount: quotaMaterials.length,
      });

      materials.forEach((material, materialIndex) => {
        if (!materialBelongsToQuota(material, quotaId)) return;
        matchedMaterialIndexes.add(materialIndex);
        rows.push({
          _rowType: 'material',
          _rowKey: `material-${record.id}-${quotaIndex}-${materialIndex}`,
          _record: record,
          _material: material,
          _quotaId: quotaId,
          _quotaSpecialty: quotaSpecialty,
        });
      });
    });

    materials.forEach((material, materialIndex) => {
      if (matchedMaterialIndexes.has(materialIndex)) return;
      const quotaSpecialty = inferSpecialtyCodeFromQuotaId(String(material.quota_code || '')) || record.specialty || '';
      rows.push({
        _rowType: 'material',
        _rowKey: `material-${record.id}-unbound-${materialIndex}`,
        _record: record,
        _material: material,
        _quotaId: String(material.quota_code || ''),
        _quotaSpecialty: quotaSpecialty,
      });
    });
  });

  return rows;
}

function confidenceColor(confidence?: number): string {
  if (confidence == null) return '#999';
  if (confidence >= GREEN_THRESHOLD) return COLORS.greenSolid;
  if (confidence >= YELLOW_THRESHOLD) return COLORS.yellowSolid;
  return COLORS.redSolid;
}

function getExperienceBillRowBgColor(confidence?: number, hasQuotas = true): string {
  if (!hasQuotas) return '#F5F5F5';
  return getBillRowBgColor(confidence || 0);
}

function formatMaterialMeta(material: ExperienceMaterial): string {
  const parts = [
    material.spec ? `规格: ${String(material.spec)}` : '',
    material.desc ? `说明: ${String(material.desc)}` : '',
    material.price != null && material.price !== '' ? `参考价: ${String(material.price)}` : '',
  ].filter(Boolean);
  return parts.join(' | ');
}

function formatMaterialName(material: ExperienceMaterial): string {
  const rawName = String(material.name || '').trim();
  if (!rawName) return '';

  let text = rawName
    .replace(/[\r\n]+/g, ' ')
    .replace(/^[◆●■\-\s]+/, '')
    .replace(/\s+/g, ' ')
    .trim();

  text = text
    .replace(/[（(][^()（）]{0,80}(?:见平面图|详见|投标|中标|综合考虑|不予调整|仅供参考|参考)[^()（）]{0,80}[)）]/g, ' ')
    .replace(/[（(][\u4e00-\u9fff、，,/\s]{7,}[)）]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();

  const detailIndex = text.search(
    /\s(?:功率|风量|风压|噪声|扬程|流量|全压|声压|电压|规格[:：]|型号[:：]|材质[:：]|名称[:：]|参数[:：]|说明[:：]|敷设方式[:：])/,
  );
  if (detailIndex > 0) {
    text = text.slice(0, detailIndex).trim();
  }

  text = text.replace(/[，,;；:：\-\/]+$/, '').trim();
  return text || rawName;
}

export default function ExperienceManage() {
  const { message } = App.useApp();

  const [stats, setStats] = useState<ExperienceStats | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);

  const [records, setRecords] = useState<ExperienceRecord[]>([]);
  const [recordsLoading, setRecordsLoading] = useState(false);
  const [totalRecords, setTotalRecords] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const [filterProvinceName, setFilterProvinceName] = useState<string | undefined>(undefined);
  const [filterSpecialtyName, setFilterSpecialtyName] = useState<string>('all');
  const [filterLayer, setFilterLayer] = useState<string>('all');
  const [searchKeyword, setSearchKeyword] = useState('');
  const [searchMode, setSearchMode] = useState(false);
  const [provinces, setProvinces] = useState<ProvinceItem[]>([]);

  const [batchLoading, setBatchLoading] = useState(false);
  const [selectedRegion, setSelectedRegion] = useState<string | undefined>(undefined);
  const [batchProvince, setBatchProvince] = useState<string | undefined>(undefined);

  const regionMap = useMemo(() => {
    const map = new Map<string, ProvinceItem[]>();
    for (const p of provinces) {
      const region = extractRegion(p.province);
      if (!map.has(region)) map.set(region, []);
      map.get(region)?.push(p);
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
    return (regionMap.get(selectedRegion) || []).map((item) => ({
      label: `${item.province}（${item.count}条）`,
      value: item.province,
    }));
  }, [regionMap, selectedRegion]);

  const provinceOptions = useMemo(() => {
    const counter = new Map<string, number>();
    provinces.forEach((item) => {
      const provinceName = extractRegion(item.province);
      if (!provinceName) return;
      counter.set(provinceName, (counter.get(provinceName) || 0) + item.count);
    });
    return Array.from(counter.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([provinceName, count]) => ({
        label: `${provinceName}（${count.toLocaleString()}条）`,
        value: provinceName,
      }));
  }, [provinces]);

  const specialtyOptions = useMemo(() => {
    const counter = new Map<string, number>();
    provinces.forEach((item) => {
      const provinceName = extractRegion(item.province);
      if (filterProvinceName && provinceName !== filterProvinceName) return;
      const specialtyName = inferSpecialtyFromProvinceLabel(item.province);
      if (!specialtyName) return;
      counter.set(specialtyName, (counter.get(specialtyName) || 0) + item.count);
    });
    return [
      { label: '全部专业', value: 'all' },
      ...SPECIALTY_FILTERS.map((specialtyName) => ({
        label: `${specialtyName}（${(counter.get(specialtyName) || 0).toLocaleString()}条）`,
        value: specialtyName,
      })),
    ];
  }, [filterProvinceName, provinces]);

  const specialtyData = useMemo(() => {
    const bySpecialty = stats?.by_specialty || {};
    return Object.entries(bySpecialty)
      .map(([code, count]) => ({ code, label: specialtyLabel(code), count: count as number }))
      .sort((a, b) => b.count - a.count);
  }, [stats]);

  const displayRows = useMemo(() => buildDisplayRows(records), [records]);

  const displaySummary = useMemo(() => {
    let billCount = 0;
    let quotaCount = 0;
    let materialCount = 0;
    displayRows.forEach((row) => {
      if (row._rowType === 'bill') billCount += 1;
      else if (row._rowType === 'quota') quotaCount += 1;
      else materialCount += 1;
    });
    return { billCount, quotaCount, materialCount };
  }, [displayRows]);

  const historicalSourceCards = useMemo(() => {
    const sources = stats?.historical_sources || {};
    return [
      {
        key: 'bill_items',
        title: sources.bill_items?.label || '历史清单项',
        value: sources.bill_items?.count || 0,
        color: '#1677ff',
        prefix: <DatabaseOutlined />,
      },
      {
        key: 'price_facts',
        title: sources.price_facts?.label || '主材价格事实',
        value: sources.price_facts?.count || 0,
        color: '#d97706',
        prefix: <ThunderboltOutlined />,
      },
      {
        key: 'bill_descriptions',
        title: sources.bill_descriptions?.label || '清单描述样本',
        value: sources.bill_descriptions?.count || 0,
        color: '#7c3aed',
        prefix: <DatabaseOutlined />,
      },
      {
        key: 'material_master',
        title: sources.material_master?.label || '材料主档',
        value: sources.material_master?.count || 0,
        color: '#0f766e',
        prefix: <DatabaseOutlined />,
      },
    ];
  }, [stats]);

  const loadStats = useCallback(async () => {
    setStatsLoading(true);
    try {
      const { data } = await api.get<ExperienceStats>('/admin/experience/stats');
      setStats(data);
      const byProvince = data.by_province || {};
      const items = Object.entries(byProvince)
        .map(([province, count]) => ({ province, count: count as number }))
        .sort((a, b) => b.count - a.count);
      setProvinces(items);
    } catch {
      message.error('加载经验库统计失败');
    } finally {
      setStatsLoading(false);
    }
  }, [message]);

  const loadRecords = useCallback(async () => {
    setRecordsLoading(true);
    setSearchMode(false);
    try {
      const { data } = await api.get<{
        items: ExperienceRecord[];
        total: number;
        page: number;
        size: number;
      }>('/admin/experience/records', {
        params: {
          layer: filterLayer,
          province_name: filterProvinceName || undefined,
          specialty_name: filterSpecialtyName !== 'all' ? filterSpecialtyName : undefined,
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
  }, [filterLayer, filterProvinceName, filterSpecialtyName, message, page, pageSize]);

  const handleSearch = useCallback(async () => {
    const q = searchKeyword.trim();
    if (!q) {
      setSearchMode(false);
      loadRecords();
      return;
    }

    setRecordsLoading(true);
    setSearchMode(true);
    try {
      const { data } = await api.get<{ items: ExperienceRecord[]; total: number }>(
        '/admin/experience/search',
        {
          params: {
            q,
            province_name: filterProvinceName || undefined,
            specialty_name: filterSpecialtyName !== 'all' ? filterSpecialtyName : undefined,
            limit: 200,
          },
        },
      );
      setRecords(data.items || []);
      setTotalRecords(data.total || 0);
    } catch {
      message.error('搜索失败');
    } finally {
      setRecordsLoading(false);
    }
  }, [filterProvinceName, filterSpecialtyName, loadRecords, message, searchKeyword]);

  useEffect(() => { loadStats(); }, [loadStats]);

  useEffect(() => {
    if (!searchMode) loadRecords();
  }, [loadRecords, searchMode]);

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
      content: `确定要删除“${billText.slice(0, 30)}”这条经验记录吗？`,
      okText: '删除',
      cancelText: '取消',
      okButtonProps: { danger: true },
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

  const handleBatchPromote = async () => {
    setBatchLoading(true);
    try {
      const { data: preview } = await api.post<{
        total: number;
        promoted: number;
        skipped: number;
        errors: string[];
        dry_run: boolean;
      }>('/admin/experience/batch-promote', {
        province: batchProvince || null,
        dry_run: true,
      }, { timeout: 120000 });

      setBatchLoading(false);
      if (preview.total === 0) {
        message.info('没有可晋升的候选层记录');
        return;
      }

      const scopeText = batchProvince ? `“${batchProvince}”` : '全部省份';
      Modal.confirm({
        title: '智能批量晋升预览',
        width: 520,
        content: (
          <div>
            <p>范围：{scopeText}</p>
            <p>候选层记录：<strong>{preview.total}</strong> 条</p>
            <p style={{ color: COLORS.greenSolid }}>
              校验通过：<strong>{preview.promoted}</strong> 条
            </p>
            {preview.skipped > 0 && (
              <p style={{ color: COLORS.yellowSolid }}>
                跳过：<strong>{preview.skipped}</strong> 条
              </p>
            )}
            {preview.errors.length > 0 && (
              <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
                <div>跳过示例：</div>
                {preview.errors.map((item, index) => <div key={index}>- {item}</div>)}
              </div>
            )}
          </div>
        ),
        okText: `确认晋升 ${preview.promoted} 条`,
        cancelText: '取消',
        onOk: async () => {
          const { data } = await api.post<{ promoted: number }>(
            '/admin/experience/batch-promote',
            { province: batchProvince || null, dry_run: false },
            { timeout: 300000 },
          );
          message.success(`批量晋升完成，${data.promoted} 条已晋升`);
          loadRecords();
          loadStats();
        },
      });
    } catch {
      message.error('批量晋升失败');
      setBatchLoading(false);
    }
  };

  const columns: ColumnsType<DisplayRow> = [
    {
      title: '类型',
      key: 'type',
      width: 120,
      render: (_value, row) => {
        if (row._rowType === 'bill') {
          const isAuthority = row._record.layer_type === 'authority';
          return (
            <Space size={4} wrap>
              <Tag color={isAuthority ? 'green' : 'orange'}>{isAuthority ? '权威' : '候选'}</Tag>
              <Tag color="cyan">清单</Tag>
            </Space>
          );
        }
        if (row._rowType === 'quota') {
          return <Tag color="geekblue">定额</Tag>;
        }
        return <Tag color="gold">主材</Tag>;
      },
    },
    {
      title: '项目编码',
      key: 'code',
      width: 170,
      render: (_value, row) => {
        if (row._rowType === 'bill') {
          return <span style={{ fontSize: 12, color: '#555' }}>{row._record.bill_code || '-'}</span>;
        }
        if (row._rowType === 'quota') {
          return <Tag color="blue" style={{ margin: 0 }}>{row._quotaId}</Tag>;
        }
        return (
          <Space size={4} wrap>
            {row._material.code ? (
              <Tag color="gold" style={{ margin: 0 }}>{String(row._material.code)}</Tag>
            ) : (
              <span style={{ color: '#999' }}>-</span>
            )}
            {!row._quotaId && row._material.quota_code && (
              <Tag style={{ margin: 0 }}>挂定额 {String(row._material.quota_code)}</Tag>
            )}
          </Space>
        );
      },
    },
    {
      title: '项目名称',
      key: 'name',
      width: 520,
      render: (_value, row) => {
        if (row._rowType === 'bill') {
          return (
            <div>
              <div style={{ fontWeight: 600, whiteSpace: 'normal', wordBreak: 'break-all', lineHeight: 1.6 }}>
                {row._record.bill_name || '未命名清单'}
              </div>
              <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
                定额 {row._quotaCount} 项
                {row._materialCount > 0 ? ` · 主材 ${row._materialCount} 项` : ''}
              </div>
            </div>
          );
        }

        if (row._rowType === 'quota') {
          return (
            <div style={{ paddingLeft: 8 }}>
              <div style={{ color: '#444', whiteSpace: 'normal', wordBreak: 'break-all', lineHeight: 1.6 }}>
                {row._quotaName || row._quotaId}
              </div>
              {row._materialCount > 0 && (
                <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
                  主材 {row._materialCount} 项
                </div>
              )}
            </div>
          );
        }

        return (
          <div style={{ paddingLeft: 16 }}>
            <span style={{ color: '#d97706', marginRight: 6 }}>●</span>
            <span style={{ whiteSpace: 'normal', wordBreak: 'break-all', lineHeight: 1.6 }}>
              {formatMaterialName(row._material) || '未命名主材'}
            </span>
          </div>
        );
      },
    },
    {
      title: '项目特征描述',
      key: 'desc',
      width: 420,
      render: (_value, row) => {
        if (row._rowType === 'bill') {
          return (
            <div style={{ fontSize: 12, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
              {row._record.bill_text || '-'}
            </div>
          );
        }

        if (row._rowType === 'quota') {
          if (!row._quotaSpecialty) return null;
          return (
            <div style={{ fontSize: 12, color: '#666' }}>
              {`专业：${inferDisplayCategory(row._record, row._quotaId, row._quotaName)}`}
            </div>
          );
        }

        const meta = formatMaterialMeta(row._material);
        return (
          <div style={{ fontSize: 12, color: '#666', lineHeight: 1.6 }}>
            {meta || '无补充说明'}
          </div>
        );
      },
    },
    {
      title: '单位',
      key: 'unit',
      width: 80,
      align: 'center',
      render: (_value, row) => {
        if (row._rowType === 'bill') return row._record.bill_unit || '-';
        if (row._rowType === 'quota') return '-';
        return row._material.unit ? String(row._material.unit) : '-';
      },
    },
    {
      title: '置信度 / 来源',
      key: 'confidence-source',
      width: 180,
      render: (_value, row) => {
        if (row._rowType !== 'bill') {
          return <span style={{ fontSize: 12, color: '#999' }}>跟随清单</span>;
        }

        return (
          <div>
            <div style={{ fontWeight: 600, color: confidenceColor(row._record.confidence) }}>
              {row._record.confidence == null ? '-' : `${row._record.confidence}%`}
            </div>
            <div style={{ marginTop: 4, fontSize: 12, color: '#888' }}>
              {row._record.source ? sourceToLabel(row._record.source) : '-'}
            </div>
          </div>
        );
      },
    },
    {
      title: '操作',
      key: 'action',
      width: 120,
      render: (_value, row) => {
        if (row._rowType !== 'bill') return null;

        const isAuthority = row._record.layer_type === 'authority';
        return (
          <Space size={2}>
            {isAuthority ? (
              <Tooltip title="降级到候选层">
                <Button
                  type="text"
                  size="small"
                  icon={<ArrowDownOutlined />}
                  style={{ color: '#d48806' }}
                  onClick={() => handleDemote(row._record.id)}
                />
              </Tooltip>
            ) : (
              <Tooltip title="晋升到权威层">
                <Button
                  type="text"
                  size="small"
                  icon={<ArrowUpOutlined />}
                  style={{ color: '#16a34a' }}
                  onClick={() => handlePromote(row._record.id)}
                />
              </Tooltip>
            )}
            <Tooltip title="删除">
              <Button
                type="text"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={() => handleDelete(row._record.id, row._record.bill_text || row._record.bill_name || '')}
              />
            </Tooltip>
          </Space>
        );
      },
    },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {false && (
      <Card size="small">
        <div style={{ display: 'grid', gap: 6, color: '#475569', fontSize: 13, lineHeight: 1.7 }}>
          <div style={{ fontWeight: 600, color: '#0f172a' }}>这里是人工维护的正式经验库。</div>
          <div>你在这里看到的是 ExperienceDB 中已经导入、确认或人工整理过的经验记录，不等同于系统自动学习出的全部知识。</div>
          <div>系统新产出的候选知识，请到“候选知识晋升”页面确认后，再决定是否写入正式层。</div>
        </div>
      </Card>
      )}

      <style>{`
        .exp-preview-table .ant-table {
          border-radius: 8px;
          overflow: hidden;
          border: 1px solid #e8e8e8;
        }
        .exp-preview-table .ant-table-thead > tr > th {
          background: #fafafa !important;
          font-weight: 600 !important;
          font-size: 13px;
          border-bottom: 2px solid #d9d9d9 !important;
        }
        .exp-preview-table .ant-table-tbody > tr > td {
          background: inherit !important;
          border-bottom: 1px solid #f0f0f0;
          padding: 8px 10px !important;
        }
        .exp-preview-table .ant-table-tbody > tr.bill-row:hover > td {
          filter: brightness(0.97);
        }
        .exp-preview-table .ant-table-tbody > tr.quota-row > td:nth-child(1),
        .exp-preview-table .ant-table-tbody > tr.quota-row > td:nth-child(2) {
          border-left: 3px solid #91caff;
        }
        .exp-preview-table .ant-table-tbody > tr.material-row > td:nth-child(1),
        .exp-preview-table .ant-table-tbody > tr.material-row > td:nth-child(2) {
          border-left: 3px solid #fbbf24;
        }
      `}</style>

      <Row gutter={[12, 12]}>
        <Col xs={12} sm={6}>
          <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
            <Statistic
              title="历史资料条目"
              value={stats?.historical_total || 0}
              prefix={<ThunderboltOutlined />}
              valueStyle={{ fontSize: 28, color: '#1677ff' }}
            />
          </Card>
        </Col>
        {historicalSourceCards.map((item) => (
          <Col xs={12} sm={6} key={item.key}>
            <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
              <Statistic
                title={item.title}
                value={item.value}
                prefix={item.prefix}
                valueStyle={{ fontSize: 24, color: item.color }}
              />
            </Card>
          </Col>
        ))}
      </Row>

      <Row gutter={[12, 12]}>
        <Col xs={12} sm={6}>
          <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
            <Statistic
              title="经验记录总数"
              value={stats?.total || 0}
              prefix={<DatabaseOutlined />}
              valueStyle={{ fontSize: 28 }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
            <Statistic
              title="正式经验"
              value={stats?.authority || 0}
              prefix={<SafetyOutlined />}
              valueStyle={{ fontSize: 28, color: COLORS.greenSolid }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
            <Statistic
              title="待确认经验"
              value={stats?.candidate || 0}
              prefix={<InboxOutlined />}
              valueStyle={{ fontSize: 28, color: COLORS.yellowSolid }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={statsLoading} styles={{ body: { padding: '16px 20px' } }}>
            <Statistic
              title="省份 / 专业"
              value={`${provinces.length}省 / ${specialtyData.length}专业`}
              prefix={<EnvironmentOutlined />}
              valueStyle={{ fontSize: 20 }}
            />
          </Card>
        </Col>
      </Row>

      <Card
        styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' } }}
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
            <Segmented
              value={filterLayer}
              onChange={(value) => {
                setFilterLayer(value as string);
                setPage(1);
              }}
              options={[
                { value: 'all', label: `全部 ${stats?.total?.toLocaleString() || 0}` },
                {
                  value: 'authority',
                  label: <span style={{ color: COLORS.greenSolid }}>正式经验 {stats?.authority?.toLocaleString() || 0}</span>,
                },
                {
                  value: 'candidate',
                  label: <span style={{ color: COLORS.yellowSolid }}>待确认 {stats?.candidate?.toLocaleString() || 0}</span>,
                },
              ]}
              size="small"
            />

            <div style={{ width: 1, height: 16, background: '#e8e8e8' }} />

            <Select
              allowClear
              placeholder="全部省份"
              value={filterProvinceName}
              onChange={(value) => {
                setFilterProvinceName(value);
                setFilterSpecialtyName('all');
                setPage(1);
              }}
              style={{ width: 160 }}
              options={provinceOptions}
              showSearch
              optionFilterProp="label"
              size="small"
            />

            <Select
              value={filterSpecialtyName}
              onChange={(value) => {
                setFilterSpecialtyName(value);
                setPage(1);
              }}
              style={{ width: 170 }}
              options={specialtyOptions}
              size="small"
            />

            <Input
              placeholder="搜索清单名称 / 经验文本"
              prefix={<SearchOutlined />}
              value={searchKeyword}
              onChange={(event) => setSearchKeyword(event.target.value)}
              onPressEnter={handleSearch}
              style={{ width: 220 }}
              size="small"
              allowClear
            />
            <Button type="primary" size="small" onClick={handleSearch}>搜索</Button>

            {searchMode && (
              <Tag color="blue">搜索结果 {totalRecords} 条清单</Tag>
            )}
          </div>
        }
        extra={
          <Space size={4}>
            <Button
              size="small"
              icon={<ReloadOutlined />}
              onClick={() => {
                loadStats();
                if (searchMode) handleSearch();
                else loadRecords();
              }}
            >
              刷新
            </Button>
          </Space>
        }
      >
        <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', background: '#fafafa' }}>
          <Space size={[8, 8]} wrap>
            <Tag color="cyan" style={{ margin: 0 }}>清单 {displaySummary.billCount}</Tag>
            <Tag color="geekblue" style={{ margin: 0 }}>定额 {displaySummary.quotaCount}</Tag>
            <Tag color="gold" style={{ margin: 0 }}>主材 {displaySummary.materialCount}</Tag>
          </Space>
        </div>

        <Table
          className="exp-preview-table"
          rowKey="_rowKey"
          dataSource={displayRows}
          columns={columns}
          size="small"
          loading={recordsLoading}
          pagination={false}
          scroll={{ x: 1540 }}
          onRow={(row) => {
            if (row._rowType === 'bill') {
              return {
                className: 'bill-row',
                style: {
                  backgroundColor: getExperienceBillRowBgColor(
                    row._record.confidence,
                    row._quotaCount > 0,
                  ),
                  fontWeight: 500,
                },
              };
            }
            if (row._rowType === 'quota') {
              return {
                className: 'quota-row',
                style: { backgroundColor: '#fafafa', fontSize: 13 },
              };
            }
            return {
              className: 'material-row',
              style: { backgroundColor: '#fffbeb', fontSize: 13 },
            };
          }}
          locale={{ emptyText: '暂无经验记录（可能正式经验库尚未接入）' }}
        />

        {!searchMode && totalRecords > 0 && (
          <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '12px 16px' }}>
            <Pagination
              current={page}
              pageSize={pageSize}
              total={totalRecords}
              showSizeChanger
              pageSizeOptions={['20', '50', '100']}
              showTotal={(total) => `共 ${total.toLocaleString()} 条清单记录`}
              onChange={(nextPage, nextPageSize) => {
                setPage(nextPage);
                setPageSize(nextPageSize);
              }}
            />
          </div>
        )}
      </Card>

      <Card title="批量晋升到正式经验" size="small" styles={{ body: { padding: '12px 20px' } }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <EnvironmentOutlined style={{ fontSize: 16 }} />
          <span>晋升范围：</span>
          <Select
            allowClear
            placeholder="全部地区"
            value={selectedRegion}
            onChange={(value) => {
              setSelectedRegion(value);
              if (value) {
                const items = regionMap.get(value) || [];
                setBatchProvince(items.length > 0 ? items[0].province : undefined);
              } else {
                setBatchProvince(undefined);
              }
            }}
            style={{ width: 180 }}
            options={regionOptions}
            showSearch
            size="small"
            filterOption={(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())}
          />
          <Select
            allowClear
            placeholder={selectedRegion ? '该地区全部省份' : '请先选地区'}
            value={batchProvince}
            onChange={setBatchProvince}
            disabled={!selectedRegion}
            style={{ width: 260 }}
            options={batchProvinceOptions}
            showSearch
            size="small"
            filterOption={(input, option) => String(option?.label || '').toLowerCase().includes(input.toLowerCase())}
          />
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            onClick={handleBatchPromote}
            loading={batchLoading}
            size="small"
          >
            智能批量晋升
          </Button>
        </div>
      </Card>
    </div>
  );
}
