/**
 * 智能填主材页面
 *
 * 布局参考新建任务页：顶部紧凑操作栏 + 下方全宽层级预览表
 *
 * 两种输入方式：
 * 1. 上传Excel（广联达材料表等）
 * 2. 从"我的任务"拉取（套完定额的结果，已含主材）
 *
 * → 选地区+价格类型 → 自动查价 → 手填补充 → 导出结果
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Card, Upload, Button, Table, Select, Space, App,
  InputNumber, Tag, Tooltip, Switch, Segmented, Radio, Input, Modal,
} from 'antd';
import {
  InboxOutlined, SearchOutlined, DownloadOutlined,
  QuestionCircleOutlined, UploadOutlined, UnorderedListOutlined,
  RightOutlined, DownOutlined, FileExcelOutlined, DeleteOutlined,
  GlobalOutlined,
} from '@ant-design/icons';
import type { UploadFile } from 'antd';
import api from '../../services/api';
import { getErrorMessage } from '../../utils/error';
import { useAuthStore } from '../../stores/auth';

const { Dragger } = Upload;
const GLDJC_BATCH_SIZE = 30;

// ============================================================
// 数据类型
// ============================================================

// 后端返回的行数据（all_rows 中的每一项）
interface RawRow {
  type: 'section' | 'bill' | 'quota' | 'material';
  row: number;
  sheet: string;
  header_row?: number;
  code?: string;
  name: string;
  name_col?: number | null;
  spec_col?: number | null;
  suggested_name?: string;
  desc?: string;       // 清单行的项目特征描述
  spec?: string;
  suggested_spec?: string;
  normalized_name?: string;
  normalized_spec?: string;
  critical_spec_text?: string;
  normalized_query_text?: string;
  object_type?: string;
  family?: string;
  normalization_confidence?: string;
  connection_hint?: string;
  material_hint?: string;
  desc_type_hint?: string;
  unit?: string;
  qty?: number | null;
  existing_price?: number | null;
  price_col?: number | null;
  lookup_price?: number | null;
  lookup_source?: string | null;
  lookup_url?: string | null;
  lookup_label?: string | null;
}

// 前端展示行
interface SectionDisplayRow {
  _rowType: 'section';
  _rowKey: string;
  _title: string;
}

interface BillDisplayRow {
  _rowType: 'bill';
  _rowKey: string;
  _raw: RawRow;
  _sectionKey: string;
}

interface QuotaDisplayRow {
  _rowType: 'quota';
  _rowKey: string;
  _raw: RawRow;
  _sectionKey: string;
}

interface MaterialDisplayRow {
  _rowType: 'material';
  _rowKey: string;
  _raw: RawRow;
  _sectionKey: string;
  edited_name: string;
  edited_spec: string;
  normalized_name: string;
  normalized_spec: string;
  critical_spec_text: string;
  object_type: string;
  family: string;
  normalization_confidence: string;
  lookup_price: number | null;
  lookup_source: string | null;
  lookup_url: string | null;
  lookup_label: string | null;
  user_price: number | null;
}

type DisplayRow = SectionDisplayRow | BillDisplayRow | QuotaDisplayRow | MaterialDisplayRow;

interface AreaItem { name: string; count: number; }
interface PeriodItem { start: string; end: string; count: number; label: string; }
interface TaskItem { id: string; original_filename: string; province: string; status: string; created_at: string; total_items: number; }

// ============================================================
// 工具函数
// ============================================================

/** 将后端 all_rows 转换为前端扁平展示行 */
function buildDisplayRows(allRows: RawRow[], isMixed: boolean): DisplayRow[] {
  const rows: DisplayRow[] = [];

  if (!isMixed) {
    for (const r of allRows) {
      rows.push({
        _rowType: 'material',
        _rowKey: `${r.sheet}-${r.row}`,
        _raw: r,
        _sectionKey: '',
        edited_name: r.normalized_name || r.suggested_name || r.name,
        edited_spec: r.normalized_spec ?? r.suggested_spec ?? (r.spec || ''),
        normalized_name: r.normalized_name || r.suggested_name || r.name,
        normalized_spec: r.normalized_spec ?? r.suggested_spec ?? (r.spec || ''),
        critical_spec_text: r.critical_spec_text || '',
        object_type: r.object_type || '',
        family: r.family || '',
        normalization_confidence: r.normalization_confidence || '',
        lookup_price: r.lookup_price ?? null,
        lookup_source: r.lookup_source ?? null,
        lookup_url: r.lookup_url ?? null,
        lookup_label: r.lookup_label ?? null,
        user_price: null,
      });
    }
    return rows;
  }

  let currentSectionKey = '';
  for (const r of allRows) {
    const key = `${r.sheet}-${r.row}`;
    if (r.type === 'section') {
      currentSectionKey = key;
      rows.push({ _rowType: 'section', _rowKey: key, _title: r.name });
    } else if (r.type === 'bill') {
      rows.push({ _rowType: 'bill', _rowKey: key, _raw: r, _sectionKey: currentSectionKey });
    } else if (r.type === 'quota') {
      rows.push({ _rowType: 'quota', _rowKey: key, _raw: r, _sectionKey: currentSectionKey });
    } else if (r.type === 'material') {
      rows.push({
        _rowType: 'material', _rowKey: key, _raw: r, _sectionKey: currentSectionKey,
        edited_name: r.normalized_name || r.suggested_name || r.name,
        edited_spec: r.normalized_spec ?? r.suggested_spec ?? (r.spec || ''),
        normalized_name: r.normalized_name || r.suggested_name || r.name,
        normalized_spec: r.normalized_spec ?? r.suggested_spec ?? (r.spec || ''),
        critical_spec_text: r.critical_spec_text || '',
        object_type: r.object_type || '',
        family: r.family || '',
        normalization_confidence: r.normalization_confidence || '',
        lookup_price: r.lookup_price ?? null,
        lookup_source: r.lookup_source ?? null,
        lookup_url: r.lookup_url ?? null,
        lookup_label: r.lookup_label ?? null,
        user_price: null,
      });
    }
  }
  return rows;
}

/** 从 lookup_source 提取价格类型标签 */
function formatMaterialInput(name: string, spec: string): string {
  return [name.trim(), spec.trim()].filter(Boolean).join(' ');
}

function splitMaterialInput(value: string): { name: string; spec: string } {
  const text = value.trim();
  if (!text) return { name: '', spec: '' };

  const patterns = [
    /^(.*?)[\s]*(DN\d+[A-Za-z0-9\-./]*)$/i,
    /^(.*?)[\s]*(De\d+[A-Za-z0-9\-./]*(?:\s+[A-Za-z0-9\-./]+)*)$/i,
    /^(.*?)[\s]*(\d+(?:\.\d+)?(?:mm|mm2|㎡|m2))$/i,
    /^(.*?)[\s]*(\d+(?:\*\d+){1,3})$/i,
  ];

  for (const pattern of patterns) {
    const matched = text.match(pattern);
    if (matched) {
      return { name: matched[1].trim(), spec: matched[2].trim() };
    }
  }

  return { name: text, spec: '' };
}

function priceSourceTag(source: string | null): React.ReactNode {
  if (!source) return null;
  if (source.includes('信息价')) return <Tag color="blue" style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>信息价</Tag>;
  if (source.includes('市场价')) return <Tag color="orange" style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>市场价</Tag>;
  if (source.includes('用户')) return <Tag color="green" style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>用户</Tag>;
  return <Tag style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>{source.slice(0, 4)}</Tag>;
}

function formatLookupSummary(row: MaterialDisplayRow): string {
  if (row.lookup_label?.trim()) return row.lookup_label.trim();
  if (row.lookup_price == null && !row.lookup_source) return '';

  const materialText = [row.edited_name.trim(), row.edited_spec.trim()].filter(Boolean).join(' ');
  const parts = [
    row.lookup_source?.trim() || '',
    materialText,
    row._raw.unit?.trim() || '',
    row.lookup_price != null ? row.lookup_price.toFixed(2) : '',
  ].filter(Boolean);

  return parts.join(' | ');
}

function buildExportLookupLabel(row: MaterialDisplayRow, finalPrice: number | null): string | null {
  const isManualOverride = row.user_price != null;
  if (isManualOverride) return null;
  if (finalPrice == null) return row.lookup_label?.trim() || null;

  const parts = [
    row.lookup_source?.trim() || '',
    [row.edited_name.trim(), row.edited_spec.trim()].filter(Boolean).join(' '),
    row._raw.unit?.trim() || '',
    finalPrice.toFixed(2),
  ].filter(Boolean);

  const text = parts.join(' | ').trim();
  return text || null;
}

function formatNormalizationSummary(row: MaterialDisplayRow): string {
  const parts = [
    [row.normalized_name.trim(), row.normalized_spec.trim()].filter(Boolean).join(' '),
    row.critical_spec_text.trim(),
    row.object_type.trim(),
    row.normalization_confidence.trim(),
  ].filter(Boolean);
  return parts.join(' | ');
}

// ============================================================
// 页面组件
// ============================================================

export default function MaterialPrice() {
  const { message } = App.useApp();
  const { user } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;

  // 输入模式
  const [inputMode, setInputMode] = useState<'upload' | 'task'>('upload');

  // 文件上传
  const [file, setFile] = useState<UploadFile | null>(null);
  const [parseLoading, setParseLoading] = useState(false);
  const [fileKey, setFileKey] = useState<string>('');

  // 任务拉取
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>('');
  const [taskLoading, setTaskLoading] = useState(false);

  // 展示行数据
  const [displayRows, setDisplayRows] = useState<DisplayRow[]>([]);
  const [isMixed, setIsMixed] = useState(false);

  // 地区选择
  const [provinces, setProvinces] = useState<AreaItem[]>([]);
  const [cities, setCities] = useState<AreaItem[]>([]);
  const [periods, setPeriods] = useState<PeriodItem[]>([]);
  const [selectedProvince, setSelectedProvince] = useState<string>('');
  const [selectedCity, setSelectedCity] = useState<string>('');
  const [selectedPeriod, setSelectedPeriod] = useState<string>('');

  // 普通查价固定走信息价。
  const [priceType] = useState<string>('info');

  // 查价状态
  const [lookupLoading, setLookupLoading] = useState(false);

  // 贡献开关
  const [contributeEnabled, setContributeEnabled] = useState(true);

  // 广材网查价（管理员专用）
  const [gldjcModalOpen, setGldjcModalOpen] = useState(false);
  const [gldjcCookie, setGldjcCookie] = useState(() => localStorage.getItem('gldjc_cookie') || '');
  const [gldjcLoading, setGldjcLoading] = useState(false);
  const [gldjcProgress, setGldjcProgress] = useState('');
  const [gldjcVerifyLoading, setGldjcVerifyLoading] = useState(false);
  const [gldjcVerifyResult, setGldjcVerifyResult] = useState<null | {
    ok: boolean;
    status: string;
    message: string;
    keyword?: string;
    scope?: string;
    url?: string;
  }>(null);

  // 分部折叠状态
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set());

  // 加载省份列表
  useEffect(() => {
    api.get('/tools/material-price/provinces').then(res => {
      setProvinces(res.data.provinces || []);
    }).catch(() => {});
  }, []);

  // 加载已完成的任务列表
  useEffect(() => {
    api.get('/tasks', { params: { status: 'completed' } }).then(res => {
      const items = (res.data.items || res.data || [])
        .filter((t: TaskItem) => t.status === 'completed')
        .slice(0, 50);
      setTasks(items);
    }).catch(() => {});
  }, []);

  // 省份变化 → 加载城市+期次
  useEffect(() => {
    if (!selectedProvince) { setCities([]); setPeriods([]); return; }
    setCities([]); setPeriods([]); setSelectedCity(''); setSelectedPeriod('');
    api.get('/tools/material-price/cities', { params: { province: selectedProvince } })
      .then(res => setCities(res.data.cities || [])).catch(() => {});
    api.get('/tools/material-price/periods', { params: { province: selectedProvince } })
      .then(res => setPeriods(res.data.periods || [])).catch(() => {});
  }, [selectedProvince]);

  // 城市变化 → 刷新期次
  useEffect(() => {
    if (!selectedProvince || !selectedCity) return;
    api.get('/tools/material-price/periods', {
      params: { province: selectedProvince, city: selectedCity },
    }).then(res => {
      const p = res.data.periods || [];
      if (p.length > 0) setPeriods(p);
    }).catch(() => {});
  }, [selectedCity, selectedProvince]);

  // 主材行
  const materialRows = useMemo(() =>
    displayRows.filter((r): r is MaterialDisplayRow => r._rowType === 'material'),
    [displayRows],
  );

  // 上传解析Excel
  const handleParse = async () => {
    if (!file) { message.warning('请先上传Excel文件'); return; }
    const formData = new FormData();
    formData.append('file', file.originFileObj as File);
    setParseLoading(true);
    try {
      const res = await api.post('/tools/material-price/parse', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const allRows: RawRow[] = res.data.all_rows || [];
      const mixed: boolean = res.data.is_mixed ?? false;
      const rows = buildDisplayRows(allRows, mixed);
      setDisplayRows(rows);
      setIsMixed(mixed);
      setFileKey(res.data.file_key || '');
      const matCount = rows.filter(r => r._rowType === 'material').length;
      message.success(`识别出 ${matCount} 条主材`);
    } catch (err) {
      message.error(getErrorMessage(err, '解析失败'));
    } finally {
      setParseLoading(false);
    }
  };

  // 从任务拉取
  const handleTaskPull = async () => {
    if (!selectedTaskId) { message.warning('请先选择一个任务'); return; }
    setTaskLoading(true);
    try {
      const res = await api.get(`/tools/material-price/from-task/${selectedTaskId}`);
      const allRows: RawRow[] = res.data.all_rows || [];
      const mixed: boolean = res.data.is_mixed ?? false;
      const rows = buildDisplayRows(allRows, mixed);
      setDisplayRows(rows);
      setIsMixed(mixed);
      setFileKey(res.data.file_key || '');
      const matCount = rows.filter(r => r._rowType === 'material').length;
      message.success(`从任务中拉取到 ${matCount} 条主材`);
    } catch (err) {
      message.error(getErrorMessage(err, '拉取失败'));
    } finally {
      setTaskLoading(false);
    }
  };

  // 批量查价
  const handleLookup = async () => {
    if (!materialRows.length) { message.warning('请先获取主材数据'); return; }
    if (!selectedProvince) { message.warning('请先选择省份'); return; }
    setLookupLoading(true);
    try {
      const res = await api.post('/tools/material-price/lookup', {
        materials: materialRows.map(m => ({
          name: m.edited_name.trim() || m._raw.name,
          spec: m.edited_spec.trim(),
          unit: m._raw.unit || '',
          object_type: m.object_type || '',
          family: m.family || '',
          critical_spec_text: m.critical_spec_text || '',
        })),
        province: selectedProvince,
        city: selectedCity,
        period_end: selectedPeriod,
        price_type: priceType,
      });
      const results = res.data.results || [];
      setDisplayRows(prev => {
        let matIdx = 0;
        return prev.map(row => {
          if (row._rowType === 'material') {
            const r = results[matIdx] || {};
            matIdx++;
            return {
              ...row,
              lookup_price: r.lookup_price ?? null,
              lookup_source: r.lookup_source ?? null,
              lookup_url: r.lookup_url ?? null,
              lookup_label: r.lookup_label ?? null,
            };
          }
          return row;
        });
      });
      const stats = res.data.stats || {};
      message.success(`查价完成：${stats.found}条查到，${stats.not_found}条未查到`);
    } catch (err) {
      message.error(getErrorMessage(err, '查价失败'));
    } finally {
      setLookupLoading(false);
    }
  };

  // 用户手填价格
  const handleUserPrice = useCallback((rowKey: string, price: number | null) => {
    setDisplayRows(prev =>
      prev.map(r =>
        r._rowType === 'material' && r._rowKey === rowKey ? { ...r, user_price: price } : r
      ),
    );
  }, []);

  const handleMaterialSpec = useCallback((rowKey: string, spec: string) => {
    setDisplayRows(prev =>
      prev.map(r =>
        r._rowType === 'material' && r._rowKey === rowKey ? { ...r, edited_spec: spec } : r
      ),
    );
  }, []);

  const handleMaterialCombined = useCallback((rowKey: string, value: string) => {
    const { name, spec } = splitMaterialInput(value);
    setDisplayRows(prev =>
      prev.map(r =>
        r._rowType === 'material' && r._rowKey === rowKey
          ? { ...r, edited_name: name, edited_spec: spec }
          : r
      ),
    );
  }, []);

  // 导出
  const handleExport = async () => {
    if (contributeEnabled) {
      const userItems = materialRows
        .filter(m => m.user_price != null && m.user_price > 0)
        .map(m => ({
          name: m.edited_name.trim() || m._raw.name,
          spec: m.edited_spec.trim(),
          unit: m._raw.unit || '',
          price: m.user_price, province: selectedProvince, city: selectedCity,
        }));
      if (userItems.length > 0) {
        try {
          await api.post('/tools/material-price/contribute', { items: userItems });
          message.success(`已贡献 ${userItems.length} 条价格数据`);
        } catch { /* 贡献失败不影响导出 */ }
      }
    }
    if (fileKey) {
      const exportMaterials = materialRows
        .map(m => {
          const finalPrice = m.user_price ?? m.lookup_price ?? null;
          const finalName = m.edited_name.trim() || m._raw.name;
          const finalSpec = m.edited_spec.trim();
          const originalSpec = m._raw.spec || '';
          const nameChanged = finalName !== m._raw.name;
          const specChanged = finalSpec !== originalSpec;
          const isManualOverride = m.user_price != null;
          const lookupUrl = isManualOverride ? '' : (m.lookup_url?.trim() || '');
          if (finalPrice == null && !nameChanged && !specChanged && !lookupUrl) return null;
          return {
            row: m._raw.row,
            sheet: m._raw.sheet,
            header_row: m._raw.header_row,
            name_col: m._raw.name_col,
            final_name: finalName,
            spec_col: m._raw.spec_col,
            final_spec: finalSpec,
            price_col: m._raw.price_col,
            final_price: finalPrice,
            lookup_url: lookupUrl || null,
            lookup_label: buildExportLookupLabel(m, finalPrice),
            critical_spec_text: m.critical_spec_text || '',
          };
        }).filter(Boolean);
      try {
        const res = await api.post('/tools/material-price/export', {
          file_key: fileKey, materials: exportMaterials,
        }, { responseType: 'blob' });
        const blob = new Blob([res.data], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        const disposition = res.headers['content-disposition'] || '';
        const match = disposition.match(/filename\*?=(?:UTF-8''|")?([^";]+)/i);
        const filename = match ? decodeURIComponent(match[1]) : '已填价.xlsx';
        a.href = url;
        a.download = filename;
        a.click();
        window.URL.revokeObjectURL(url);
        message.success(`导出成功，已写回 ${exportMaterials.length} 条主材结果`);
      } catch (err) {
        message.error(getErrorMessage(err, '导出失败'));
      }
    } else {
      message.error('文件丢失，请重新上传或拉取');
    }
  };

  // 广材网实时查价（管理员专用）
  const handleGldjcLookup = async () => {
    // 筛选"未查到"的主材行
    const unfound = materialRows.filter(m => m.lookup_price == null && m.user_price == null);
    if (!unfound.length) {
      message.info('没有需要查广材网的材料（全部已有价格）');
      return;
    }
    if (!gldjcCookie.trim()) {
      message.warning('请先输入广材网Cookie');
      return;
    }
    // 单次请求上限30条，前端会自动拆批连续查询
    const batchSize = GLDJC_BATCH_SIZE;
    const batches: MaterialDisplayRow[][] = [];
    for (let i = 0; i < unfound.length; i += batchSize) {
      batches.push(unfound.slice(i, i + batchSize));
    }
    // 保存cookie到localStorage
    localStorage.setItem('gldjc_cookie', gldjcCookie);
    setGldjcModalOpen(false);
    setGldjcLoading(true);
    setGldjcProgress(`正在查询 1/${batches.length} 批，共${unfound.length} 条...`);
    try {
      let totalFound = 0;
      for (let batchIndex = 0; batchIndex < batches.length; batchIndex++) {
        const currentBatch = batches[batchIndex];
        setGldjcProgress(`正在查询 ${batchIndex + 1}/${batches.length} 批，当前 ${currentBatch.length} 条，共${unfound.length} 条...`);
        const batchRes = await api.post('/tools/material-price/gldjc-lookup', {
          materials: currentBatch.map(m => ({
            name: m.edited_name.trim() || m._raw.name,
            spec: m.edited_spec.trim(),
            unit: m._raw.unit || '',
            _rowKey: m._rowKey,
          })),
          cookie: gldjcCookie,
          province: selectedProvince,
          city: selectedCity,
          period_end: selectedPeriod,
        }, { timeout: 600000 });
        const batchResults: Array<{ _rowKey?: string; gldjc_price?: number | null; gldjc_source?: string; gldjc_url?: string | null; gldjc_label?: string | null }> = batchRes.data.results || [];
        const batchResultMap = new Map<string, { price: number | null; source: string | null; url: string | null; label: string | null }>();
        for (const item of batchResults) {
          if (item._rowKey) {
            batchResultMap.set(item._rowKey, {
              price: item.gldjc_price ?? null,
              source: item.gldjc_source || null,
              url: item.gldjc_url ?? null,
              label: item.gldjc_label ?? null,
            });
          }
        }
        setDisplayRows(prev =>
          prev.map(row => {
            if (row._rowType === 'material' && batchResultMap.has(row._rowKey)) {
              const { price, source, url, label } = batchResultMap.get(row._rowKey)!;
              return { ...row, lookup_price: price, lookup_source: source, lookup_url: url, lookup_label: label };
            }
            return row;
          })
        );
        totalFound += batchRes.data.found || 0;
      }
      message.success(`广材网查价完成：${totalFound}/${unfound.length}条查到价格`);
      return;
    } catch (err) {
      message.error(getErrorMessage(err, '广材网查价失败'));
    } finally {
      setGldjcLoading(false);
      setGldjcProgress('');
    }
  };

  const handleGldjcCookieVerify = async () => {
    if (!gldjcCookie.trim()) {
      message.warning('请先输入广材网Cookie');
      return;
    }
    localStorage.setItem('gldjc_cookie', gldjcCookie);
    setGldjcVerifyLoading(true);
    setGldjcVerifyResult(null);
    try {
      const res = await api.post('/tools/material-price/gldjc-cookie-verify', {
        cookie: gldjcCookie,
        province: selectedProvince,
        city: selectedCity,
      }, { timeout: 60000 });
      setGldjcVerifyResult(res.data || null);
      if (res.data?.status === 'valid') {
        message.success('Cookie验证通过');
      } else if (res.data?.status === 'invalid') {
        message.error(res.data?.message || 'Cookie已失效');
      } else {
        message.warning(res.data?.message || 'Cookie疑似受限');
      }
    } catch (err) {
      setGldjcVerifyResult({
        ok: false,
        status: 'error',
        message: getErrorMessage(err, 'Cookie验证失败'),
      });
      message.error(getErrorMessage(err, 'Cookie验证失败'));
    } finally {
      setGldjcVerifyLoading(false);
    }
  };

  // 折叠/展开
  const toggleSection = useCallback((sectionKey: string) => {
    setCollapsedSections(prev => {
      const next = new Set(prev);
      if (next.has(sectionKey)) next.delete(sectionKey);
      else next.add(sectionKey);
      return next;
    });
  }, []);

  const visibleRows = useMemo(() => {
    if (!isMixed) return displayRows;
    return displayRows.filter(row => {
      if (row._rowType === 'section') return true;
      const secKey = (row as BillDisplayRow | QuotaDisplayRow | MaterialDisplayRow)._sectionKey;
      return !secKey || !collapsedSections.has(secKey);
    });
  }, [displayRows, collapsedSections, isMixed]);

  // 统计
  const totalMaterials = materialRows.length;
  const foundCount = materialRows.filter(m => m.lookup_price != null).length;
  const userFilledCount = materialRows.filter(m => m.user_price != null).length;
  const emptyCount = totalMaterials - foundCount - userFilledCount;

  const sectionMatCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const row of displayRows) {
      if (row._rowType === 'material' && row._sectionKey) {
        counts[row._sectionKey] = (counts[row._sectionKey] || 0) + 1;
      }
    }
    return counts;
  }, [displayRows]);

  // 当前选中的任务名称
  const selectedTaskName = useMemo(() => {
    const t = tasks.find(t => t.id === selectedTaskId);
    return t ? t.original_filename : '';
  }, [tasks, selectedTaskId]);

  // 文件信息展示
  const hasData = displayRows.length > 0;
  const dataSourceName = inputMode === 'upload'
    ? (file?.name || '')
    : (selectedTaskName || '');

  // ============================================================
  // 表格列定义
  // ============================================================

  const columns = [
    {
      title: '状态',
      key: 'status',
      width: 80,
      onCell: (row: DisplayRow) => {
        if (row._rowType === 'section') {
          return { colSpan: 9, style: { textAlign: 'left' as const, paddingLeft: 12 } };
        }
        return {};
      },
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') {
          const isCollapsed = collapsedSections.has(row._rowKey);
          const matCount = sectionMatCounts[row._rowKey] || 0;
          return (
            <span style={{ fontWeight: 'bold', fontSize: 13, color: '#1565C0', userSelect: 'none' }}>
              {isCollapsed
                ? <RightOutlined style={{ fontSize: 10, marginRight: 6 }} />
                : <DownOutlined style={{ fontSize: 10, marginRight: 6 }} />}
              {row._title}
              {matCount > 0 && (
                <span style={{ fontSize: 11, fontWeight: 'normal', opacity: 0.55, marginLeft: 8 }}>
                  {matCount}条主材
                </span>
              )}
            </span>
          );
        }
        if (row._rowType === 'material') {
          if (row.user_price != null) return <Tag color="green">手填</Tag>;
          if (row.lookup_price != null) return <Tag color="blue">已查到</Tag>;
          if (row._raw.existing_price != null) return <Tag color="default">原有</Tag>;
          return <Tag color="red">待填</Tag>;
        }
        if (row._rowType === 'bill') return <Tag color="cyan">清单</Tag>;
        if (row._rowType === 'quota') return <Tag color="geekblue">定额</Tag>;
        return null;
      },
    },
    {
      title: '编码',
      key: 'code',
      width: 120,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') return <span style={{ fontSize: 12 }}>{row._raw.code || ''}</span>;
        if (row._rowType === 'quota') return <Tag color="blue" style={{ margin: 0 }}>{row._raw.code || ''}</Tag>;
        const code = row._raw.code || '';
        return code === '主' ? <Tag color="gold">主</Tag> : <span style={{ fontSize: 12, color: '#999' }}>{code}</span>;
      },
    },
    {
      title: '名称',
      key: 'name',
      width: 200,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') return <span style={{ fontWeight: 500 }}>{row._raw.name}</span>;
        if (row._rowType === 'quota') {
          return <span style={{ fontSize: 13, color: '#555', paddingLeft: 8 }}>{row._raw.name}</span>;
        }
        return (
          <div style={{ paddingLeft: 16, display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'nowrap' }}>
            <span style={{ color: '#d97706', marginRight: 4, flex: '0 0 auto' }}>◆</span>
            <Input
              size="small"
              value={formatMaterialInput(row.edited_name, row.edited_spec)}
              onChange={(e) => handleMaterialCombined(row._rowKey, e.target.value)}
              placeholder="主材名称"
              style={{ flex: '1 1 360px', minWidth: 240 }}
            />
            <Input
              size="small"
              value={row.edited_spec}
              onChange={(e) => handleMaterialSpec(row._rowKey, e.target.value)}
              placeholder="规格型号"
              style={{ display: 'none' }}
            />
          </div>
        );
      },
    },
    {
      title: '标准化结果',
      key: 'normalized_summary',
      width: 260,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'material') return null;
        const summary = formatNormalizationSummary(row);
        if (!summary) return <span style={{ color: '#ccc' }}>—</span>;

        return (
          <Tooltip title={summary}>
            <div style={{ minWidth: 0 }}>
              <div
                style={{
                  fontSize: 12,
                  color: '#334155',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {[row.normalized_name, row.normalized_spec].filter(Boolean).join(' ')}
              </div>
              {row.critical_spec_text ? (
                <div
                  style={{
                    fontSize: 11,
                    color: '#64748b',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                >
                  关键规格: {row.critical_spec_text}
                </div>
              ) : null}
            </div>
          </Tooltip>
        );
      },
    },
    // 项目特征（只在清单行显示）
    {
      title: '项目特征',
      key: 'desc',
      width: 320,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        const desc = row._raw?.desc;
        if (!desc) return <span style={{ color: '#ccc' }}>-</span>;
        // 原样显示项目特征，混合表里让清单/定额/主材行都能看到所属清单特征
        return (
          <div style={{ fontSize: 12, lineHeight: '1.6', whiteSpace: 'pre-wrap' }}>
            {desc}
          </div>
        );
      },
    },
    {
      title: '单位',
      key: 'unit',
      width: 55,
      align: 'center' as const,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        return (row as BillDisplayRow | QuotaDisplayRow | MaterialDisplayRow)._raw?.unit || '';
      },
    },
    {
      title: '数量',
      key: 'qty',
      width: 80,
      align: 'right' as const,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        const qty = (row as BillDisplayRow | QuotaDisplayRow | MaterialDisplayRow)._raw?.qty;
        return qty != null ? qty : '—';
      },
    },
    // 系统查价（带信息价/市场价标识）
    {
      title: '系统查价',
      key: 'lookup_price',
      width: 140,
      align: 'right' as const,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'material') return null;
        const v = row.lookup_price;
        if (v != null) {
          return (
            <Space size={4}>
              {priceSourceTag(row.lookup_source)}
              <span style={{ color: '#2563eb', fontWeight: 500 }}>{v.toFixed(2)}</span>
            </Space>
          );
        }
        return <span style={{ color: '#ccc' }}>—</span>;
      },
    },
    {
      title: '核对信息',
      key: 'lookup_detail',
      width: 320,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'material') return null;
        const summary = formatLookupSummary(row);
        if (!row.lookup_url && !summary) return <span style={{ color: '#ccc' }}>—</span>;

        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            {row.lookup_url ? (
              <Tooltip title={summary || row.lookup_url}>
                <a href={row.lookup_url} target="_blank" rel="noreferrer" style={{ flex: '0 0 auto' }}>
                  查看
                </a>
              </Tooltip>
            ) : null}
            {summary ? (
              <Tooltip title={summary}>
                <span
                  style={{
                    color: '#666',
                    fontSize: 12,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    minWidth: 0,
                  }}
                >
                  {summary}
                </span>
              </Tooltip>
            ) : null}
          </div>
        );
      },
    },
    // 手填价格
    {
      title: (
        <span>
          手填价格 <Tooltip title="查不到的可以手填，会自动贡献到价格库">
            <QuestionCircleOutlined style={{ color: '#94a3b8' }} />
          </Tooltip>
        </span>
      ),
      key: 'user_price',
      width: 130,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'material') return null;
        return (
          <InputNumber
            size="small" min={0} step={0.01} placeholder="手填"
            value={row.user_price}
            onChange={(val) => handleUserPrice(row._rowKey, val)}
            style={{ width: '100%' }}
          />
        );
      },
    },
  ];

  // ============================================================
  // 渲染
  // ============================================================

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: 'calc(100vh - 80px)', padding: '0 16px' }}>

      {/* ========== 顶部：紧凑操作栏 ========== */}
      <Card styles={{ body: { padding: '12px 20px' } }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>

          {/* 数据来源切换 */}
          <Segmented
            value={inputMode}
            onChange={v => {
              setInputMode(v as 'upload' | 'task');
              setDisplayRows([]);
              setFile(null);
              setSelectedTaskId('');
            }}
            options={[
              { value: 'upload', label: '上传文件', icon: <UploadOutlined /> },
              { value: 'task', label: '从任务拉取', icon: <UnorderedListOutlined /> },
            ]}
            size="small"
          />

          {/* 文件/任务选择 */}
          {inputMode === 'upload' ? (
            <>
              {file ? (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8, height: 32,
                  border: '1px solid #91caff', borderRadius: 6, padding: '0 12px', background: '#e6f4ff',
                }}>
                  <FileExcelOutlined style={{ fontSize: 16, color: '#52c41a' }} />
                  <span style={{ fontSize: 13, fontWeight: 500, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {file.name}
                  </span>
                  <span style={{ fontSize: 11, color: '#888' }}>
                    {((file.size || 0) / 1024).toFixed(0)} KB
                  </span>
                  <DeleteOutlined
                    style={{ fontSize: 12, color: '#ff4d4f', cursor: 'pointer' }}
                    onClick={() => { setFile(null); setDisplayRows([]); }}
                  />
                </div>
              ) : (
                <Upload
                  maxCount={1} accept=".xlsx,.xls" showUploadList={false}
                  beforeUpload={(f) => {
                    if (!f.name.endsWith('.xlsx') && !f.name.endsWith('.xls')) {
                      message.error('只支持Excel文件');
                      return Upload.LIST_IGNORE;
                    }
                    setFile({ uid: Date.now().toString(), name: f.name, size: f.size, originFileObj: f } as UploadFile);
                    setDisplayRows([]);
                    return false;
                  }}
                >
                  <Button icon={<InboxOutlined />} size="middle">选择文件</Button>
                </Upload>
              )}
              <Button
                type="primary" size="middle"
                loading={parseLoading} disabled={!file}
                onClick={handleParse}
              >
                识别主材
              </Button>
            </>
          ) : (
            <>
              <Select
                style={{ width: 360 }}
                placeholder="选择已完成的套定额任务"
                value={selectedTaskId || undefined}
                onChange={v => setSelectedTaskId(v)}
                showSearch optionFilterProp="label" size="middle"
                options={tasks.map(t => ({
                  value: t.id,
                  label: `${t.original_filename}（${t.province || ''}，${t.total_items || 0}条）`,
                }))}
              />
              <Button
                type="primary" size="middle"
                loading={taskLoading} disabled={!selectedTaskId}
                onClick={handleTaskPull}
              >
                拉取主材
              </Button>
            </>
          )}

          {/* 分隔线 */}
          <div style={{ width: 1, height: 20, background: '#e8e8e8' }} />

          {/* 地区选择 */}
          <Select
            style={{ width: 120 }}
            placeholder="省份" size="middle"
            value={selectedProvince || undefined}
            onChange={v => setSelectedProvince(v)}
            showSearch optionFilterProp="label"
            options={provinces.map(p => ({ value: p.name, label: p.name }))}
          />
          <Select
            style={{ width: 100 }}
            placeholder="城市" size="middle" allowClear
            value={selectedCity || undefined}
            onChange={v => setSelectedCity(v || '')}
            disabled={!selectedProvince}
            showSearch optionFilterProp="label"
            options={cities.map(c => ({ value: c.name, label: c.name }))}
          />
          <Select
            style={{ width: 140 }}
            placeholder="期次" size="middle" allowClear
            value={selectedPeriod || undefined}
            onChange={v => setSelectedPeriod(v || '')}
            disabled={!selectedProvince}
            options={periods.map(p => ({ value: p.end, label: p.label }))}
          />

          {/* 普通查价固定为信息价 */}
          <Radio.Group
            value={priceType}
            optionType="button" buttonStyle="solid" size="middle"
          >
            <Radio.Button value="info">信息价</Radio.Button>
          </Radio.Group>

          {/* 查价按钮 */}
          <Button
            type="primary" icon={<SearchOutlined />} size="middle"
            loading={lookupLoading}
            disabled={!materialRows.length || !selectedProvince}
            onClick={handleLookup}
          >
            开始查价
          </Button>

          {/* 广材网查价按钮（管理员专用，查价后才显示） */}
          {isAdmin && hasData && emptyCount > 0 && (
            <Tooltip title={`对 ${emptyCount} 条未查到的材料实时搜索广材网`}>
              <Button
                icon={<GlobalOutlined />} size="middle"
                loading={gldjcLoading}
                onClick={() => setGldjcModalOpen(true)}
                style={{ color: '#d97706', borderColor: '#d97706' }}
              >
                {gldjcLoading ? gldjcProgress : `查广材网(${emptyCount})`}
              </Button>
            </Tooltip>
          )}

          {/* 右侧统计 + 操作 */}
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
            {hasData && (
              <span style={{ fontSize: 12, color: '#666' }}>
                主材 <b>{totalMaterials}</b>条
                {foundCount > 0 && <span style={{ color: '#2563eb' }}> · 查到 {foundCount}</span>}
                {userFilledCount > 0 && <span style={{ color: '#16a34a' }}> · 手填 {userFilledCount}</span>}
                {emptyCount > 0 && <span style={{ color: '#dc2626' }}> · 待填 {emptyCount}</span>}
              </span>
            )}
            {hasData && (
              <>
                <Tooltip title="开启后，你手填的价格会贡献到系统价格库">
                  <Switch
                    checked={contributeEnabled}
                    onChange={setContributeEnabled}
                    checkedChildren="贡献" unCheckedChildren="不贡献"
                    size="small"
                  />
                </Tooltip>
                <Button type="primary" icon={<DownloadOutlined />} size="middle" onClick={handleExport}>
                  导出Excel
                </Button>
              </>
            )}
          </div>
        </div>
      </Card>

      {/* ========== 未加载数据时：拖拽上传区 ========== */}
      {!hasData && inputMode === 'upload' && !file && (
        <Card style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Dragger
            fileList={[]} maxCount={1} accept=".xlsx,.xls" showUploadList={false}
            style={{ padding: '40px 80px' }}
            beforeUpload={(f) => {
              if (!f.name.endsWith('.xlsx') && !f.name.endsWith('.xls')) {
                message.error('只支持Excel文件');
                return Upload.LIST_IGNORE;
              }
              setFile({ uid: Date.now().toString(), name: f.name, size: f.size, originFileObj: f } as UploadFile);
              return false;
            }}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">拖拽材料表或套完定额的Excel到此处</p>
            <p className="ant-upload-hint">支持 .xlsx / .xls</p>
          </Dragger>
        </Card>
      )}

      {/* ========== 数据表格 ========== */}
      {hasData && (
        <Card
          style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}
          styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'auto' } }}
          title={
            <span style={{ fontSize: 14 }}>
              {dataSourceName && (
                <span style={{ marginRight: 8 }}>
                  <FileExcelOutlined style={{ color: '#52c41a', marginRight: 4 }} />
                  {dataSourceName}
                </span>
              )}
              <span style={{ color: '#888', fontWeight: 'normal' }}>
                {totalMaterials}条主材 · 共{displayRows.length}行
              </span>
            </span>
          }
        >
          {/* 视觉样式 */}
          <style>{`
            .material-table .ant-table-tbody > tr > td {
              background: inherit !important;
              transition: filter 0.15s ease;
              border-bottom: 1px solid #f0f0f0;
            }
            .material-table .ant-table {
              border-radius: 8px;
              overflow: hidden;
              border: 1px solid #e8e8e8;
            }
            .material-table .ant-table-thead > tr > th {
              background: #fafafa !important;
              font-weight: 600 !important;
              font-size: 13px;
              border-bottom: 2px solid #d9d9d9 !important;
            }
            .material-table .ant-table-tbody > tr.section-row:hover > td {
              filter: brightness(0.92);
            }
            .material-table .ant-table-tbody > tr.bill-row:hover > td {
              filter: brightness(0.96);
            }
            .material-table .ant-table-tbody > tr.quota-row > td:nth-child(1),
            .material-table .ant-table-tbody > tr.quota-row > td:nth-child(2) {
              border-left: 3px solid #91caff;
            }
            .material-table .ant-table-tbody > tr.mat-row > td:nth-child(1),
            .material-table .ant-table-tbody > tr.mat-row > td:nth-child(2) {
              border-left: 3px solid #fbbf24;
            }
            .material-table .ant-table-tbody > tr.mat-row:hover > td {
              filter: brightness(0.97);
            }
          `}</style>
          <Table
            className="material-table"
            rowKey="_rowKey"
            dataSource={visibleRows}
            columns={columns}
            size="small"
            pagination={{ pageSize: 100, showSizeChanger: true, pageSizeOptions: ['50', '100', '200'] }}
            scroll={{ x: 1300 }}
            onRow={(row: DisplayRow) => {
              if (row._rowType === 'section') {
                return {
                  className: 'section-row',
                  style: { backgroundColor: '#BBDEFB', fontWeight: 'bold', cursor: 'pointer' },
                  onClick: () => toggleSection(row._rowKey),
                };
              }
              if (row._rowType === 'bill') {
                return { className: 'bill-row', style: { backgroundColor: '#F5F5F5', fontWeight: 500 } };
              }
              if (row._rowType === 'quota') {
                return { className: 'quota-row', style: { backgroundColor: '#FAFAFA', fontSize: 13 } };
              }
              return { className: 'mat-row', style: { backgroundColor: '#FFFBEB', fontSize: 13 } };
            }}
            locale={{ emptyText: '暂无数据' }}
          />
        </Card>
      )}

      {/* 广材网Cookie输入弹窗（管理员专用） */}
      <Modal
        title="广材网实时查价"
        open={gldjcModalOpen}
        onCancel={() => setGldjcModalOpen(false)}
        onOk={handleGldjcLookup}
        okText="开始查价"
        cancelText="取消"
        width={520}
      >
        <div style={{ marginBottom: 12 }}>
          <p style={{ marginBottom: 8, color: '#666' }}>
            将对 <b>{emptyCount}</b> 条未查到价格的材料实时搜索广材网。
            {emptyCount > GLDJC_BATCH_SIZE && (
              <span style={{ color: '#d97706' }}>
                （系统会自动分成{Math.ceil(emptyCount / GLDJC_BATCH_SIZE)}批连续查询，每批最多{GLDJC_BATCH_SIZE}条，无需手工分批）
              </span>
            )}
          </p>
          <p style={{ marginBottom: 4, fontSize: 12, color: '#999' }}>
            每条间隔5~8秒（随机模拟人工，避免过快触发限制），预计 <b>{Math.ceil(emptyCount * 6.5 / 60)}</b> 分钟。
            查到的价格自动缓存，下次不再重复查。
          </p>
          <p style={{ marginBottom: 12, fontSize: 12, color: '#999' }}>
            Cookie获取：登录 gldjc.com → F12开发者工具 → Network → 复制请求头中的Cookie
          </p>
          <Input.TextArea
            rows={3}
            placeholder="粘贴广材网Cookie（如：token=bearer xxx; ...）"
            value={gldjcCookie}
            onChange={e => setGldjcCookie(e.target.value)}
          />
          <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <Button loading={gldjcVerifyLoading} onClick={handleGldjcCookieVerify}>
              验证 Cookie
            </Button>
            {gldjcVerifyResult && (
              <span style={{ fontSize: 12, color: gldjcVerifyResult.status === 'valid' ? '#15803d' : gldjcVerifyResult.status === 'invalid' ? '#dc2626' : '#d97706' }}>
                {gldjcVerifyResult.status === 'valid' ? '有效' : gldjcVerifyResult.status === 'invalid' ? '失效' : '受限'}：
                {gldjcVerifyResult.message}
                {gldjcVerifyResult.scope ? ` | ${gldjcVerifyResult.scope}` : ''}
                {gldjcVerifyResult.keyword ? ` | 测试材料: ${gldjcVerifyResult.keyword}` : ''}
                {gldjcVerifyResult.url ? (
                  <>
                    {' | '}
                    <a href={gldjcVerifyResult.url} target="_blank" rel="noreferrer">查看链接</a>
                  </>
                ) : null}
              </span>
            )}
          </div>
        </div>
      </Modal>
    </div>
  );
}
