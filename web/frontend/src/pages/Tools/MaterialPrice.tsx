/**
 * 智能填主材页面
 *
 * 两种输入方式：
 * 1. 上传Excel（广联达材料表等）
 * 2. 从"我的任务"拉取（套完定额的结果，已含主材）
 *
 * → 选地区自动查价 → 手填补充 → 导出结果
 * 用户手填的价格会贡献到价格库候选层（众包收集）。
 *
 * 预览界面参考套定额结果页：分部标题→清单行→定额行→主材行（层级展示）
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import {
  Card, Upload, Button, Table, Select, Space, App, Statistic, Row, Col,
  InputNumber, Tag, Tooltip, Switch, Segmented,
} from 'antd';
import {
  InboxOutlined, SearchOutlined, DownloadOutlined, GoldOutlined,
  QuestionCircleOutlined, UploadOutlined, UnorderedListOutlined,
  RightOutlined, DownOutlined,
} from '@ant-design/icons';
import type { UploadFile } from 'antd';
import api from '../../services/api';
import { getErrorMessage } from '../../utils/error';

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
  desc?: string;       // 清单行的项目特征描述
  spec?: string;
  unit?: string;
  qty?: number | null;
  existing_price?: number | null;
  price_col?: number | null;
  lookup_price?: number | null;
  lookup_source?: string | null;
}

// 前端展示用的扁平行（类似Results页的DisplayRow）
interface SectionDisplayRow {
  _rowType: 'section';
  _rowKey: string;
  _title: string;
}

interface BillDisplayRow {
  _rowType: 'bill';
  _rowKey: string;
  _raw: RawRow;
  _sectionKey: string;  // 所属分部标题的key（折叠用）
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
  // 查价结果（会被 lookup 覆盖）
  lookup_price: number | null;
  lookup_source: string | null;
  // 用户手填价格
  user_price: number | null;
}

type DisplayRow = SectionDisplayRow | BillDisplayRow | QuotaDisplayRow | MaterialDisplayRow;

// 省份/城市数据
interface AreaItem {
  name: string;
  count: number;
}

// 期次数据
interface PeriodItem {
  start: string;
  end: string;
  count: number;
  label: string;
}

// 任务数据
interface TaskItem {
  id: string;
  original_filename: string;
  province: string;
  status: string;
  created_at: string;
  total_items: number;
}

// ============================================================
// 工具函数
// ============================================================

/** 将后端 all_rows 转换为前端扁平展示行 */
function buildDisplayRows(allRows: RawRow[], isMixed: boolean): DisplayRow[] {
  const rows: DisplayRow[] = [];

  if (!isMixed) {
    // 纯材料表：直接平铺
    for (let i = 0; i < allRows.length; i++) {
      const r = allRows[i];
      rows.push({
        _rowType: 'material',
        _rowKey: `${r.sheet}-${r.row}`,
        _raw: r,
        _sectionKey: '',
        lookup_price: r.lookup_price ?? null,
        lookup_source: r.lookup_source ?? null,
        user_price: null,
      });
    }
    return rows;
  }

  // 混合表（分部分项格式）：按 section→bill→quota→material 层级展示
  let currentSectionKey = '';

  for (let i = 0; i < allRows.length; i++) {
    const r = allRows[i];
    const key = `${r.sheet}-${r.row}`;

    if (r.type === 'section') {
      currentSectionKey = key;
      rows.push({
        _rowType: 'section',
        _rowKey: key,
        _title: r.name,
      });
    } else if (r.type === 'bill') {
      rows.push({
        _rowType: 'bill',
        _rowKey: key,
        _raw: r,
        _sectionKey: currentSectionKey,
      });
    } else if (r.type === 'quota') {
      rows.push({
        _rowType: 'quota',
        _rowKey: key,
        _raw: r,
        _sectionKey: currentSectionKey,
      });
    } else if (r.type === 'material') {
      rows.push({
        _rowType: 'material',
        _rowKey: key,
        _raw: r,
        _sectionKey: currentSectionKey,
        lookup_price: r.lookup_price ?? null,
        lookup_source: r.lookup_source ?? null,
        user_price: null,
      });
    }
  }

  return rows;
}

// ============================================================
// 页面组件
// ============================================================

export default function MaterialPrice() {
  const { message } = App.useApp();

  // 输入模式："upload" 或 "task"
  const [inputMode, setInputMode] = useState<'upload' | 'task'>('upload');

  // 文件上传
  const [file, setFile] = useState<UploadFile[]>([]);
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

  // 查价状态
  const [lookupLoading, setLookupLoading] = useState(false);

  // 贡献开关
  const [contributeEnabled, setContributeEnabled] = useState(true);

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

  // 省份变化 → 加载城市
  useEffect(() => {
    if (!selectedProvince) {
      setCities([]);
      setPeriods([]);
      return;
    }
    setCities([]);
    setPeriods([]);
    setSelectedCity('');
    setSelectedPeriod('');
    api.get('/tools/material-price/cities', { params: { province: selectedProvince } })
      .then(res => setCities(res.data.cities || []))
      .catch(() => {});
    api.get('/tools/material-price/periods', { params: { province: selectedProvince } })
      .then(res => setPeriods(res.data.periods || []))
      .catch(() => {});
  }, [selectedProvince]);

  // 城市变化 → 加载期次
  useEffect(() => {
    if (!selectedProvince || !selectedCity) return;
    api.get('/tools/material-price/periods', {
      params: { province: selectedProvince, city: selectedCity },
    }).then(res => {
      const p = res.data.periods || [];
      if (p.length > 0) setPeriods(p);
    }).catch(() => {});
  }, [selectedCity, selectedProvince]);

  // 从 displayRows 中提取所有主材行
  const materialRows = useMemo(() =>
    displayRows.filter((r): r is MaterialDisplayRow => r._rowType === 'material'),
    [displayRows],
  );

  // 上传解析Excel
  const handleParse = async () => {
    if (!file.length) {
      message.warning('请先上传Excel文件');
      return;
    }
    const formData = new FormData();
    formData.append('file', file[0].originFileObj as File);

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

  // 从任务拉取主材
  const handleTaskPull = async () => {
    if (!selectedTaskId) {
      message.warning('请先选择一个任务');
      return;
    }
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
    if (!materialRows.length) {
      message.warning('请先获取主材数据');
      return;
    }
    if (!selectedProvince) {
      message.warning('请先选择省份');
      return;
    }

    setLookupLoading(true);
    try {
      const res = await api.post('/tools/material-price/lookup', {
        materials: materialRows.map(m => ({
          name: m._raw.name,
          spec: m._raw.spec || '',
          unit: m._raw.unit || '',
        })),
        province: selectedProvince,
        city: selectedCity,
        period_end: selectedPeriod,
      });
      const results = res.data.results || [];
      // 把查价结果更新到 displayRows 中的主材行
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
        r._rowType === 'material' && r._rowKey === rowKey
          ? { ...r, user_price: price }
          : r
      ),
    );
  }, []);

  // 提交用户贡献 + 导出
  const handleExport = async () => {
    // 先贡献用户手填的价格
    if (contributeEnabled) {
      const userItems = materialRows
        .filter(m => m.user_price != null && m.user_price > 0)
        .map(m => ({
          name: m._raw.name,
          spec: m._raw.spec || '',
          unit: m._raw.unit || '',
          price: m.user_price,
          province: selectedProvince,
          city: selectedCity,
        }));

      if (userItems.length > 0) {
        try {
          await api.post('/tools/material-price/contribute', { items: userItems });
          message.success(`已贡献 ${userItems.length} 条价格数据`);
        } catch {
          // 贡献失败不影响导出
        }
      }
    }

    // 写回原Excel
    if (fileKey) {
      await _exportWriteBack();
    } else {
      message.error('文件丢失，请重新上传或拉取');
    }
  };

  // 把价格写回原Excel的主材行单价列
  const _exportWriteBack = async () => {
    const exportMaterials = materialRows
      .map(m => {
        const finalPrice = m.user_price ?? m.lookup_price ?? null;
        if (finalPrice == null || m._raw.price_col == null) return null;
        return {
          row: m._raw.row,
          sheet: m._raw.sheet,
          price_col: m._raw.price_col,
          final_price: finalPrice,
        };
      })
      .filter(Boolean);

    try {
      const res = await api.post('/tools/material-price/export', {
        file_key: fileKey,
        materials: exportMaterials,
      }, {
        responseType: 'blob',
      });

      // 下载文件
      const blob = new Blob([res.data], {
        type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      });
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
  };

  // 折叠/展开分部
  const toggleSection = useCallback((sectionKey: string) => {
    setCollapsedSections(prev => {
      const next = new Set(prev);
      if (next.has(sectionKey)) next.delete(sectionKey);
      else next.add(sectionKey);
      return next;
    });
  }, []);

  // 根据折叠状态过滤可见行
  const visibleRows = useMemo(() => {
    if (!isMixed) return displayRows;  // 纯材料表不折叠
    return displayRows.filter(row => {
      if (row._rowType === 'section') return true;
      // 非标题行：所属分部未折叠时才显示
      const secKey = (row as BillDisplayRow | QuotaDisplayRow | MaterialDisplayRow)._sectionKey;
      return !secKey || !collapsedSections.has(secKey);
    });
  }, [displayRows, collapsedSections, isMixed]);

  // 统计
  const totalMaterials = materialRows.length;
  const foundCount = materialRows.filter(m => m.lookup_price != null).length;
  const userFilledCount = materialRows.filter(m => m.user_price != null).length;
  const emptyCount = totalMaterials - foundCount - userFilledCount;

  // 每个分部的主材条数（折叠时显示）
  const sectionMatCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const row of displayRows) {
      if (row._rowType === 'material' && row._sectionKey) {
        counts[row._sectionKey] = (counts[row._sectionKey] || 0) + 1;
      }
    }
    return counts;
  }, [displayRows]);

  // ============================================================
  // 表格列定义
  // ============================================================

  const columns = [
    // 第一列：状态/标题
    {
      title: '状态',
      key: 'status',
      width: 80,
      onCell: (row: DisplayRow) => {
        if (row._rowType === 'section') {
          return { colSpan: 7, style: { textAlign: 'left' as const, paddingLeft: 12 } };
        }
        return {};
      },
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') {
          const isCollapsed = collapsedSections.has(row._rowKey);
          const matCount = sectionMatCounts[row._rowKey] || 0;
          return (
            <span style={{
              fontWeight: 'bold',
              fontSize: 13,
              color: '#1565C0',
              userSelect: 'none',
            }}>
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
    // 编码列
    {
      title: '编码',
      key: 'code',
      width: 120,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') {
          return <span style={{ fontSize: 12 }}>{row._raw.code || ''}</span>;
        }
        if (row._rowType === 'quota') {
          return <Tag color="blue" style={{ margin: 0 }}>{row._raw.code || ''}</Tag>;
        }
        // 主材行
        const code = row._raw.code || '';
        return code === '主' ? <Tag color="gold">主</Tag> : <span style={{ fontSize: 12, color: '#999' }}>{code}</span>;
      },
    },
    // 名称列
    {
      title: '名称',
      key: 'name',
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') {
          return <span style={{ fontWeight: 500 }}>{row._raw.name}</span>;
        }
        if (row._rowType === 'quota') {
          return (
            <span style={{ fontSize: 13, color: '#555', paddingLeft: 8 }}>
              {row._raw.name}
            </span>
          );
        }
        // 主材行：加缩进+金色标记
        return (
          <span style={{ paddingLeft: 16 }}>
            <span style={{ color: '#d97706', marginRight: 4 }}>◆</span>
            {row._raw.name}
            {row._raw.spec && (
              <span style={{ color: '#94a3b8', marginLeft: 6, fontSize: 12 }}>
                {row._raw.spec}
              </span>
            )}
          </span>
        );
      },
    },
    // 单位
    {
      title: '单位',
      key: 'unit',
      width: 60,
      align: 'center' as const,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        return (row as BillDisplayRow | QuotaDisplayRow | MaterialDisplayRow)._raw?.unit || '';
      },
    },
    // 数量
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
    // 系统查价（只在主材行显示）
    {
      title: '系统查价',
      key: 'lookup_price',
      width: 110,
      align: 'right' as const,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'material') return null;
        const v = row.lookup_price;
        if (v != null) {
          return (
            <Tooltip title={row.lookup_source || ''}>
              <span style={{ color: '#2563eb', fontWeight: 500 }}>
                {v.toFixed(2)}
              </span>
            </Tooltip>
          );
        }
        return <span style={{ color: '#ccc' }}>—</span>;
      },
    },
    // 手填价格（只在主材行显示）
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
            size="small"
            min={0}
            step={0.01}
            placeholder="手填"
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
    <div style={{ maxWidth: 1200, margin: '0 auto' }}>
      {/* 页面标题 */}
      <Card style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <GoldOutlined style={{ fontSize: 24, color: '#d97706' }} />
          <div>
            <h2 style={{ margin: 0 }}>智能填主材</h2>
            <span style={{ color: '#64748b' }}>
              上传材料表或从已有任务拉取 → 选地区自动查价 → 手动补充 → 导出结果
            </span>
          </div>
        </div>
      </Card>

      {/* 数据来源 + 地区选择 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={10}>
          <Card
            size="small"
            title={
              <Segmented
                value={inputMode}
                onChange={v => {
                  setInputMode(v as 'upload' | 'task');
                  setDisplayRows([]);
                }}
                options={[
                  { value: 'upload', label: '上传文件', icon: <UploadOutlined /> },
                  { value: 'task', label: '从任务拉取', icon: <UnorderedListOutlined /> },
                ]}
                size="small"
              />
            }
          >
            {inputMode === 'upload' ? (
              <>
                <Dragger
                  fileList={file}
                  maxCount={1}
                  accept=".xlsx,.xls"
                  beforeUpload={() => false}
                  onChange={({ fileList }) => {
                    setFile(fileList.slice(-1));
                    setDisplayRows([]);
                  }}
                  style={{ padding: '12px 0' }}
                >
                  <p className="ant-upload-drag-icon"><InboxOutlined /></p>
                  <p className="ant-upload-text">上传材料表或套完定额的Excel</p>
                </Dragger>
                <Button
                  block
                  type="primary"
                  style={{ marginTop: 12 }}
                  loading={parseLoading}
                  disabled={!file.length}
                  onClick={handleParse}
                >
                  识别主材
                </Button>
              </>
            ) : (
              <>
                <div style={{ marginBottom: 8, color: '#475569', fontSize: 13 }}>
                  选择已完成的套定额任务
                </div>
                <Select
                  style={{ width: '100%' }}
                  placeholder="选择任务"
                  value={selectedTaskId || undefined}
                  onChange={v => setSelectedTaskId(v)}
                  showSearch
                  optionFilterProp="label"
                  options={tasks.map(t => ({
                    value: t.id,
                    label: `${t.original_filename}（${t.province || ''}，${t.total_items || 0}条）`,
                  }))}
                />
                <Button
                  block
                  type="primary"
                  style={{ marginTop: 12 }}
                  loading={taskLoading}
                  disabled={!selectedTaskId}
                  onClick={handleTaskPull}
                >
                  拉取主材
                </Button>
              </>
            )}
          </Card>
        </Col>
        <Col span={14}>
          <Card title="选择地区" size="small">
            <Space direction="vertical" style={{ width: '100%' }} size="middle">
              <div>
                <div style={{ marginBottom: 4, color: '#475569', fontSize: 13 }}>省份</div>
                <Select
                  style={{ width: '100%' }}
                  placeholder="选择省份"
                  value={selectedProvince || undefined}
                  onChange={v => setSelectedProvince(v)}
                  showSearch
                  optionFilterProp="label"
                  options={provinces.map(p => ({
                    value: p.name,
                    label: `${p.name}（${p.count}条）`,
                  }))}
                />
              </div>
              <div>
                <div style={{ marginBottom: 4, color: '#475569', fontSize: 13 }}>
                  城市 <span style={{ color: '#94a3b8' }}>（可选）</span>
                </div>
                <Select
                  style={{ width: '100%' }}
                  placeholder="选择城市（不选则查全省）"
                  value={selectedCity || undefined}
                  onChange={v => setSelectedCity(v || '')}
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  disabled={!selectedProvince}
                  options={cities.map(c => ({
                    value: c.name,
                    label: `${c.name}（${c.count}条）`,
                  }))}
                />
              </div>
              <div>
                <div style={{ marginBottom: 4, color: '#475569', fontSize: 13 }}>
                  期次 <span style={{ color: '#94a3b8' }}>（不选则用最新价格）</span>
                </div>
                <Select
                  style={{ width: '100%' }}
                  placeholder="选择期次"
                  value={selectedPeriod || undefined}
                  onChange={v => setSelectedPeriod(v || '')}
                  allowClear
                  disabled={!selectedProvince}
                  options={periods.map(p => ({
                    value: p.end,
                    label: `${p.label}（${p.count}条）`,
                  }))}
                />
              </div>
              <Button
                type="primary"
                icon={<SearchOutlined />}
                block
                loading={lookupLoading}
                disabled={!materialRows.length || !selectedProvince}
                onClick={handleLookup}
              >
                开始查价
              </Button>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* 统计 */}
      {totalMaterials > 0 && (
        <Card style={{ marginBottom: 16 }}>
          <Row gutter={24}>
            <Col flex="1">
              <Statistic title="主材总数" value={totalMaterials} suffix="条" />
            </Col>
            <Col flex="1">
              <Statistic
                title="系统查到"
                value={foundCount}
                valueStyle={{ color: '#2563eb' }}
              />
            </Col>
            <Col flex="1">
              <Statistic
                title="用户手填"
                value={userFilledCount}
                valueStyle={{ color: '#16a34a' }}
              />
            </Col>
            <Col flex="1">
              <Statistic
                title="待填写"
                value={emptyCount > 0 ? emptyCount : 0}
                valueStyle={{ color: emptyCount > 0 ? '#dc2626' : '#16a34a' }}
              />
            </Col>
          </Row>
        </Card>
      )}

      {/* 层级预览表格 */}
      {displayRows.length > 0 && (
        <Card
          title={`主材列表（${totalMaterials}条主材，共${displayRows.length}行）`}
          style={{ marginBottom: 16 }}
          extra={
            <Space>
              <Tooltip title="开启后，你手填的价格会贡献到系统价格库，帮助其他用户">
                <Switch
                  checked={contributeEnabled}
                  onChange={setContributeEnabled}
                  checkedChildren="贡献价格"
                  unCheckedChildren="不贡献"
                />
              </Tooltip>
              <Tooltip title="把价格写回原Excel的主材行单价列">
                <Button
                  type="primary"
                  icon={<DownloadOutlined />}
                  onClick={handleExport}
                >
                  导出Excel
                </Button>
              </Tooltip>
            </Space>
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
            scroll={{ y: 600 }}
            onRow={(row: DisplayRow) => {
              if (row._rowType === 'section') {
                return {
                  className: 'section-row',
                  style: {
                    backgroundColor: '#BBDEFB',
                    fontWeight: 'bold',
                    cursor: 'pointer',
                  },
                  onClick: () => toggleSection(row._rowKey),
                };
              }
              if (row._rowType === 'bill') {
                return {
                  className: 'bill-row',
                  style: { backgroundColor: '#F5F5F5', fontWeight: 500 },
                };
              }
              if (row._rowType === 'quota') {
                return {
                  className: 'quota-row',
                  style: { backgroundColor: '#FAFAFA', fontSize: 13 },
                };
              }
              // 主材行
              return {
                className: 'mat-row',
                style: { backgroundColor: '#FFFBEB', fontSize: 13 },
              };
            }}
            locale={{ emptyText: '暂无数据' }}
          />
        </Card>
      )}
    </div>
  );
}
