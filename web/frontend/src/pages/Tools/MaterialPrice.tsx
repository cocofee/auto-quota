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

// ============================================================
// 数据类型
// ============================================================

// 后端返回的行数据（all_rows 中的每一项）
interface RawRow {
  type: 'section' | 'bill' | 'quota' | 'material';
  row: number;
  sheet: string;
  code?: string;
  name: string;
  name_col?: number | null;
  spec_col?: number | null;
  desc?: string;       // 清单行的项目特征描述
  spec?: string;
  unit?: string;
  qty?: number | null;
  existing_price?: number | null;
  price_col?: number | null;
  lookup_price?: number | null;
  lookup_source?: string | null;
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
  lookup_price: number | null;
  lookup_source: string | null;
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
        edited_name: r.name,
        edited_spec: r.spec || '',
        lookup_price: r.lookup_price ?? null,
        lookup_source: r.lookup_source ?? null,
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
        edited_name: r.name,
        edited_spec: r.spec || '',
        lookup_price: r.lookup_price ?? null, lookup_source: r.lookup_source ?? null, user_price: null,
      });
    }
  }
  return rows;
}

/** 从 lookup_source 提取价格类型标签 */
function priceSourceTag(source: string | null): React.ReactNode {
  if (!source) return null;
  if (source.includes('信息价')) return <Tag color="blue" style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>信息价</Tag>;
  if (source.includes('市场价')) return <Tag color="orange" style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>市场价</Tag>;
  if (source.includes('用户')) return <Tag color="green" style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>用户</Tag>;
  return <Tag style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>{source.slice(0, 4)}</Tag>;
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

  // 价格类型：all=不限, info=信息价, market=市场价
  const [priceType, setPriceType] = useState<string>('all');

  // 查价状态
  const [lookupLoading, setLookupLoading] = useState(false);

  // 贡献开关
  const [contributeEnabled, setContributeEnabled] = useState(true);

  // 广材网查价（管理员专用）
  const [gldjcModalOpen, setGldjcModalOpen] = useState(false);
  const [gldjcCookie, setGldjcCookie] = useState(() => localStorage.getItem('gldjc_cookie') || '');
  const [gldjcLoading, setGldjcLoading] = useState(false);
  const [gldjcProgress, setGldjcProgress] = useState('');

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
            return { ...row, lookup_price: r.lookup_price ?? null, lookup_source: r.lookup_source ?? null };
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

  const handleMaterialName = useCallback((rowKey: string, name: string) => {
    setDisplayRows(prev =>
      prev.map(r =>
        r._rowType === 'material' && r._rowKey === rowKey ? { ...r, edited_name: name } : r
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
          if (finalPrice == null && !nameChanged && !specChanged) return null;
          return {
            row: m._raw.row,
            sheet: m._raw.sheet,
            name_col: m._raw.name_col,
            final_name: finalName,
            spec_col: m._raw.spec_col,
            final_spec: finalSpec,
            price_col: m._raw.price_col,
            final_price: finalPrice,
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
        message.success(`导出成功，已写入 ${exportMaterials.length} 个主材价格`);
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
    // 单次上限30条（和后端一致）
    const batch = unfound.slice(0, 30);
    // 保存cookie到localStorage
    localStorage.setItem('gldjc_cookie', gldjcCookie);
    setGldjcModalOpen(false);
    setGldjcLoading(true);
    setGldjcProgress(`正在查询 ${batch.length} 条，每条5~8秒...`);
    try {
      const res = await api.post('/tools/material-price/gldjc-lookup', {
        materials: batch.map(m => ({
          name: m.edited_name.trim() || m._raw.name,
          spec: m.edited_spec.trim(),
          unit: m._raw.unit || '',
          _rowKey: m._rowKey,  // 传回rowKey方便前端定位
        })),
        cookie: gldjcCookie,
      }, { timeout: 600000 });  // 10分钟超时（大批量查价）
      const results: Array<{ _rowKey?: string; gldjc_price?: number | null; gldjc_source?: string }> = res.data.results || [];
      // 用rowKey更新对应行的价格
      const priceMap = new Map<string, { price: number; source: string }>();
      for (const r of results) {
        if (r._rowKey && r.gldjc_price != null) {
          priceMap.set(r._rowKey, { price: r.gldjc_price, source: r.gldjc_source || '广材网市场价' });
        }
      }
      setDisplayRows(prev =>
        prev.map(row => {
          if (row._rowType === 'material' && priceMap.has(row._rowKey)) {
            const { price, source } = priceMap.get(row._rowKey)!;
            return { ...row, lookup_price: price, lookup_source: source };
          }
          return row;
        })
      );
      const found = res.data.found || 0;
      message.success(`广材网查价完成：${found}/${unfound.length}条查到价格`);
    } catch (err) {
      message.error(getErrorMessage(err, '广材网查价失败'));
    } finally {
      setGldjcLoading(false);
      setGldjcProgress('');
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
          <div style={{ paddingLeft: 16, display: 'flex', alignItems: 'flex-start', gap: 6 }}>
            <span style={{ color: '#d97706', marginRight: 4 }}>◆</span>
            <Input
              size="small"
              value={row.edited_name}
              onChange={(e) => handleMaterialName(row._rowKey, e.target.value)}
              placeholder="主材名称"
            />
            <Input
              size="small"
              value={row.edited_spec}
              onChange={(e) => handleMaterialSpec(row._rowKey, e.target.value)}
              placeholder="规格型号"
            />
          </div>
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
        if (row._rowType !== 'bill') return null;
        const desc = row._raw.desc;
        if (!desc) return <span style={{ color: '#ccc' }}>-</span>;
        // 原样显示项目特征，和套定额预览页样式一致
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

          {/* 价格类型 */}
          <Radio.Group
            value={priceType}
            onChange={e => setPriceType(e.target.value)}
            optionType="button" buttonStyle="solid" size="middle"
          >
            <Radio.Button value="all">不限</Radio.Button>
            <Radio.Button value="info">信息价</Radio.Button>
            <Radio.Button value="market">市场价</Radio.Button>
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
            将对 <b>{Math.min(emptyCount, 30)}</b> 条未查到价格的材料实时搜索广材网。
            {emptyCount > 30 && <span style={{ color: '#d97706' }}>（单次上限30条，共{emptyCount}条待查，可分批操作）</span>}
          </p>
          <p style={{ marginBottom: 4, fontSize: 12, color: '#999' }}>
            每条间隔5~8秒（随机模拟人工），预计 <b>{Math.ceil(Math.min(emptyCount, 30) * 6.5 / 60)}</b> 分钟。
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
        </div>
      </Modal>
    </div>
  );
}
