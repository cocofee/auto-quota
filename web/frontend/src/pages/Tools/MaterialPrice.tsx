/**
 * 智能填主材页面
 *
 * 上传套完定额的Excel → 识别主材行 → 选地区自动查价 → 手填补充 → 下载结果
 * 用户手填的价格会贡献到价格库候选层（众包收集）。
 */

import { useState, useEffect, useCallback } from 'react';
import {
  Card, Upload, Button, Table, Select, Space, App, Statistic, Row, Col,
  InputNumber, Tag, Tooltip, Switch, Cascader,
} from 'antd';
import {
  InboxOutlined, SearchOutlined, DownloadOutlined, GoldOutlined,
  CheckCircleOutlined, EditOutlined, QuestionCircleOutlined,
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
  lookup_price: number | null;
  lookup_source: string | null;
  user_price?: number | null;       // 用户手填价格
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

export default function MaterialPrice() {
  const { message } = App.useApp();

  // 文件上传
  const [file, setFile] = useState<UploadFile[]>([]);
  const [parseLoading, setParseLoading] = useState(false);

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
  const [lookupDone, setLookupDone] = useState(false);

  // 贡献开关
  const [contributeEnabled, setContributeEnabled] = useState(true);

  // 加载省份列表
  useEffect(() => {
    api.get('/tools/material-price/provinces').then(res => {
      setProvinces(res.data.provinces || []);
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
    // 同时加载省级期次
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
      // 城市没有期次数据就保留省级期次
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
    setLookupDone(false);
    try {
      const res = await api.post('/tools/material-price/parse', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      const mats: MaterialRow[] = (res.data.materials || []).map((m: MaterialRow) => ({
        ...m,
        user_price: null,
      }));
      setMaterials(mats);
      message.success(`识别出 ${mats.length} 条主材`);
    } catch (err) {
      message.error(getErrorMessage(err, '解析失败'));
    } finally {
      setParseLoading(false);
    }
  };

  // 批量查价
  const handleLookup = async () => {
    if (!materials.length) {
      message.warning('请先上传并解析Excel');
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
      // 合并查价结果到主材列表
      setMaterials(prev =>
        prev.map((m, i) => ({
          ...m,
          lookup_price: results[i]?.lookup_price ?? null,
          lookup_source: results[i]?.lookup_source ?? null,
        }))
      );
      setLookupDone(true);
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
    // 收集用户手填的价格，提交到候选层
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

    // 导出Excel（前端生成）
    try {
      const { utils: xlsxUtils, writeFile: xlsxWriteFile } = await import('xlsx');
      const rows = materials.map(m => {
        // 最终价格：优先用户手填 > 系统查价 > 已有价格
        const finalPrice = m.user_price ?? m.lookup_price ?? m.existing_price;
        return {
          '工作表': m.sheet,
          '行号': m.row,
          '编码': m.code,
          '名称': m.name,
          '规格': m.spec,
          '单位': m.unit,
          '数量': m.qty,
          '单价': finalPrice,
          '价格来源': m.user_price != null ? '手填'
            : m.lookup_price != null ? (m.lookup_source || '系统查价')
            : m.existing_price != null ? '原有'
            : '',
        };
      });

      const ws = xlsxUtils.json_to_sheet(rows);
      ws['!cols'] = [
        { wch: 12 }, { wch: 6 }, { wch: 15 }, { wch: 30 },
        { wch: 15 }, { wch: 6 }, { wch: 10 }, { wch: 12 }, { wch: 15 },
      ];
      const wb = xlsxUtils.book_new();
      xlsxUtils.book_append_sheet(wb, ws, '主材价格');
      const origName = file[0]?.name?.replace(/\.[^.]+$/, '') || '主材';
      xlsxWriteFile(wb, `${origName}_已填价.xlsx`);
      message.success('导出成功');
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
              上传套好定额的Excel → 选地区自动查价 → 手动补充 → 导出结果
            </span>
          </div>
        </div>
      </Card>

      {/* 上传 + 地区选择 */}
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col span={10}>
          <Card title="上传Excel" size="small">
            <Dragger
              fileList={file}
              maxCount={1}
              accept=".xlsx,.xls"
              beforeUpload={() => false}
              onChange={({ fileList }) => {
                setFile(fileList.slice(-1));
                setMaterials([]);
                setLookupDone(false);
              }}
              style={{ padding: '12px 0' }}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">上传套好定额的Excel</p>
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
              <Button
                type="primary"
                icon={<DownloadOutlined />}
                onClick={handleExport}
              >
                导出Excel
              </Button>
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
