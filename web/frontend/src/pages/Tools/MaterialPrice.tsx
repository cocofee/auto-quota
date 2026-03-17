/**
 * 智能填主材页面
 *
 * 两种输入方式：
 * 1. 上传Excel（广联达材料表等）
 * 2. 从"我的任务"拉取（套完定额的结果，已含主材）
 *
 * → 选地区自动查价 → 手填补充 → 导出结果
 * 用户手填的价格会贡献到价格库候选层（众包收集）。
 */

import { useState, useEffect, useCallback } from 'react';
import {
  Card, Upload, Button, Table, Select, Space, App, Statistic, Row, Col,
  InputNumber, Tag, Tooltip, Switch, Segmented,
} from 'antd';
import {
  InboxOutlined, SearchOutlined, DownloadOutlined, GoldOutlined,
  QuestionCircleOutlined, UploadOutlined, UnorderedListOutlined,
} from '@ant-design/icons';
import type { UploadFile } from 'antd';
import api from '../../services/api';
import { getErrorMessage } from '../../utils/error';

const { Dragger } = Upload;

// 主材行数据类型
interface MaterialRow {
  row: number;
  sheet: string;
  code: string;
  name: string;
  spec: string;
  unit: string;
  qty: number | null;
  existing_price: number | null;
  price_col: number | null;     // 价格列位置（export写回用）
  lookup_price: number | null;
  lookup_source: string | null;
  user_price?: number | null;
}

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

export default function MaterialPrice() {
  const { message } = App.useApp();

  // 输入模式："upload" 或 "task"
  const [inputMode, setInputMode] = useState<'upload' | 'task'>('upload');

  // 文件上传
  const [file, setFile] = useState<UploadFile[]>([]);
  const [parseLoading, setParseLoading] = useState(false);
  const [fileKey, setFileKey] = useState<string>('');  // parse返回的文件标识，export时回传

  // 任务拉取
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string>('');
  const [taskLoading, setTaskLoading] = useState(false);
  const [taskSourceName, setTaskSourceName] = useState<string>('');

  // 主材数据
  const [materials, setMaterials] = useState<MaterialRow[]>([]);

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
      const mats: MaterialRow[] = (res.data.materials || []).map((m: MaterialRow) => ({
        ...m,
        user_price: null,
      }));
      setMaterials(mats);
      setFileKey(res.data.file_key || '');
      message.success(`识别出 ${mats.length} 条主材`);
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
      const mats: MaterialRow[] = (res.data.materials || []).map((m: MaterialRow) => ({
        ...m,
        user_price: null,
      }));
      setMaterials(mats);
      setTaskSourceName(res.data.task_name || '');
      setFileKey(res.data.file_key || '');
      message.success(`从任务中拉取到 ${mats.length} 条主材`);
    } catch (err) {
      message.error(getErrorMessage(err, '拉取失败'));
    } finally {
      setTaskLoading(false);
    }
  };

  // 批量查价
  const handleLookup = async () => {
    if (!materials.length) {
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
        materials: materials.map(m => ({
          name: m.name,
          spec: m.spec,
          unit: m.unit,
        })),
        province: selectedProvince,
        city: selectedCity,
        period_end: selectedPeriod,
      });
      const results = res.data.results || [];
      setMaterials(prev =>
        prev.map((m, i) => ({
          ...m,
          lookup_price: results[i]?.lookup_price ?? null,
          lookup_source: results[i]?.lookup_source ?? null,
        }))
      );
      const stats = res.data.stats || {};
      message.success(`查价完成：${stats.found}条查到，${stats.not_found}条未查到`);
    } catch (err) {
      message.error(getErrorMessage(err, '查价失败'));
    } finally {
      setLookupLoading(false);
    }
  };

  // 用户手填价格
  const handleUserPrice = useCallback((row: number, price: number | null) => {
    setMaterials(prev =>
      prev.map(m => m.row === row ? { ...m, user_price: price } : m)
    );
  }, []);

  // 提交用户贡献 + 导出
  const handleExport = async () => {
    // 先贡献用户手填的价格
    if (contributeEnabled) {
      const userItems = materials
        .filter(m => m.user_price != null && m.user_price > 0)
        .map(m => ({
          name: m.name,
          spec: m.spec,
          unit: m.unit,
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

    // 两种模式都写回原Excel
    if (fileKey) {
      await _exportWriteBack();
    } else {
      message.error('文件丢失，请重新上传或拉取');
    }
  };

  // 从任务拉取模式：把价格写回原Excel的主材行单价列
  const _exportWriteBack = async () => {
    const exportMaterials = materials
      .map(m => {
        const finalPrice = m.user_price ?? m.lookup_price ?? null;
        if (finalPrice == null || m.price_col == null) return null;
        return {
          row: m.row,
          sheet: m.sheet,
          price_col: m.price_col,
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

  // 统计
  const totalCount = materials.length;
  const foundCount = materials.filter(m => m.lookup_price != null).length;
  const userFilledCount = materials.filter(m => m.user_price != null).length;
  const emptyCount = totalCount - foundCount - userFilledCount;

  // 表格列定义
  const columns = [
    {
      title: '状态',
      key: 'status',
      width: 80,
      render: (_: unknown, record: MaterialRow) => {
        if (record.user_price != null) return <Tag color="green">手填</Tag>;
        if (record.lookup_price != null) return <Tag color="blue">已查到</Tag>;
        if (record.existing_price != null) return <Tag color="default">原有</Tag>;
        return <Tag color="red">待填</Tag>;
      },
    },
    {
      title: '名称',
      dataIndex: 'name',
      key: 'name',
      ellipsis: true,
    },
    {
      title: '规格',
      dataIndex: 'spec',
      key: 'spec',
      width: 120,
      ellipsis: true,
    },
    {
      title: '单位',
      dataIndex: 'unit',
      key: 'unit',
      width: 60,
    },
    {
      title: '数量',
      dataIndex: 'qty',
      key: 'qty',
      width: 80,
      align: 'right' as const,
      render: (v: number | null) => v != null ? v : '—',
    },
    {
      title: '系统查价',
      dataIndex: 'lookup_price',
      key: 'lookup_price',
      width: 110,
      align: 'right' as const,
      render: (v: number | null, record: MaterialRow) => {
        if (v != null) {
          return (
            <Tooltip title={record.lookup_source || ''}>
              <span style={{ color: '#2563eb', fontWeight: 500 }}>
                {v.toFixed(2)}
              </span>
            </Tooltip>
          );
        }
        return <span style={{ color: '#ccc' }}>—</span>;
      },
    },
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
      render: (_: unknown, record: MaterialRow) => (
        <InputNumber
          size="small"
          min={0}
          step={0.01}
          placeholder="手填"
          value={record.user_price}
          onChange={(val) => handleUserPrice(record.row, val)}
          style={{ width: '100%' }}
        />
      ),
    },
  ];

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
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
                  setMaterials([]);
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
                    setMaterials([]);
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
                disabled={!materials.length || !selectedProvince}
                onClick={handleLookup}
              >
                开始查价
              </Button>
            </Space>
          </Card>
        </Col>
      </Row>

      {/* 统计 */}
      {materials.length > 0 && (
        <Card style={{ marginBottom: 16 }}>
          <Row gutter={24}>
            <Col flex="1">
              <Statistic title="主材总数" value={totalCount} suffix="条" />
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

      {/* 主材表格 */}
      {materials.length > 0 && (
        <Card
          title={`主材列表（${materials.length}条）`}
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
          <Table
            dataSource={materials}
            columns={columns}
            rowKey={r => `${r.sheet}-${r.row}`}
            size="small"
            pagination={{ pageSize: 50 }}
            scroll={{ y: 500 }}
          />
        </Card>
      )}
    </div>
  );
}
