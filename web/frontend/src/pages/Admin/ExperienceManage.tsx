/**
 * 管理员 — 经验库数据看板
 *
 * 纯统计视图，不展示具体清单记录。
 * 看板内容：大数字概览、省份分布、专业分布、来源分布、批量晋升操作。
 */

import { useEffect, useState, useCallback, useMemo } from 'react';
import {
  Card, Table, Tag, Button, Space, App, Statistic, Row, Col,
  Select, Modal, Tooltip,
} from 'antd';
import {
  ReloadOutlined, DatabaseOutlined,
  EnvironmentOutlined, ThunderboltOutlined,
  SafetyOutlined, InboxOutlined, DeleteOutlined,
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

interface ProvinceItem {
  province: string;
  count: number;
}

// ============================================================
// 组件
// ============================================================

export default function ExperienceManage() {
  const { message } = App.useApp();
  const [stats, setStats] = useState<ExperienceStats | null>(null);
  const [loading, setLoading] = useState(false);

  // 批量晋升
  const [batchLoading, setBatchLoading] = useState(false);

  // 省份选择（用于批量晋升的范围）
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
    setLoading(true);
    try {
      const { data } = await api.get<ExperienceStats>('/admin/experience/stats');
      setStats(data);
      const byProvince = data.by_province || {};
      const provinceList: ProvinceItem[] = Object.entries(byProvince)
        .map(([name, count]) => ({ province: name, count: count as number }))
        .sort((a, b) => b.count - a.count);
      setProvinces(provinceList);
    } catch {
      message.error('加载经验库统计失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { loadStats(); }, [loadStats]);

  const onRegionChange = (value: string | undefined) => {
    setSelectedRegion(value);
    if (value) {
      const items = regionMap.get(value) || [];
      setSelectedProvince(items.length > 0 ? items[0].province : undefined);
    } else {
      setSelectedProvince(undefined);
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
          loadStats();
        },
      });
    } catch { message.error('批量晋升失败'); setBatchLoading(false); }
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
            '/admin/experience/by-province',
            { params: { province } },
          );
          message.success(`已删除「${province}」${data.deleted} 条记录`);
          loadStats();
        } catch { message.error('删除失败'); }
      },
    });
  };

  // ============================================================
  // 分布数据处理
  // ============================================================

  // 按省份分布表格数据
  const provinceTableData = useMemo(() => {
    return provinces.map((p, idx) => ({ key: idx, province: p.province, count: p.count }));
  }, [provinces]);

  // 按专业分布表格数据
  const specialtyTableData = useMemo(() => {
    const bySpecialty = stats?.by_specialty || {};
    return Object.entries(bySpecialty)
      .map(([code, count], idx) => ({ key: idx, specialty: code, label: specialtyLabel(code), count: count as number }))
      .sort((a, b) => b.count - a.count);
  }, [stats]);

  // 按来源分布
  const sourceData = useMemo(() => {
    const bySource = stats?.by_source || {};
    return Object.entries(bySource)
      .map(([source, count]) => ({ source, label: sourceToLabel(source), count: count as number }))
      .sort((a, b) => b.count - a.count);
  }, [stats]);

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 第一行：大数字概览 */}
      <Row gutter={[16, 16]}>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="总记录" value={stats?.total || 0} prefix={<DatabaseOutlined />} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="权威层" value={stats?.authority || 0}
              prefix={<SafetyOutlined />} valueStyle={{ color: COLORS.greenSolid }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="候选层" value={stats?.candidate || 0}
              prefix={<InboxOutlined />} valueStyle={{ color: COLORS.yellowSolid }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card loading={loading}>
            <Statistic title="平均置信度" value={stats?.avg_confidence || 0} suffix="%" />
          </Card>
        </Col>
      </Row>

      {/* 第二行：省份分布 + 专业分布 */}
      <Row gutter={[16, 16]}>
        <Col xs={24} md={12}>
          <Card title="按省份分布" size="small" loading={loading}
            extra={<Tag>{provinces.length} 个省份</Tag>}>
            <Table
              dataSource={provinceTableData}
              size="small"
              pagination={false}
              scroll={{ y: 300 }}
              columns={[
                { title: '省份', dataIndex: 'province', key: 'province', ellipsis: true,
                  render: (v: string) => <Tooltip title={v}><span>{v}</span></Tooltip>,
                },
                {
                  title: '条数', dataIndex: 'count', key: 'count', width: 80, align: 'right',
                  sorter: (a, b) => a.count - b.count, defaultSortOrder: 'descend',
                  render: (v: number) => <strong>{v.toLocaleString()}</strong>,
                },
                {
                  title: '', key: 'action', width: 40, align: 'center',
                  render: (_: unknown, row: { province: string; count: number }) => (
                    <Button type="text" danger size="small" icon={<DeleteOutlined />}
                      onClick={() => handleDeleteProvince(row.province, row.count)} />
                  ),
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card title="按专业分布" size="small" loading={loading}
            extra={<Tag>{specialtyTableData.length} 个专业</Tag>}>
            <Table
              dataSource={specialtyTableData}
              size="small"
              pagination={false}
              scroll={{ y: 300 }}
              columns={[
                { title: '专业', dataIndex: 'label', key: 'label', ellipsis: true },
                {
                  title: '条数', dataIndex: 'count', key: 'count', width: 80, align: 'right',
                  sorter: (a, b) => a.count - b.count, defaultSortOrder: 'descend',
                  render: (v: number) => <strong>{v.toLocaleString()}</strong>,
                },
              ]}
            />
          </Card>
        </Col>
      </Row>

      {/* 第三行：来源分布 */}
      <Card title="按来源分布" size="small" loading={loading}>
        <Row gutter={[12, 12]}>
          {sourceData.map((item) => (
            <Col key={item.source} xs={12} sm={8} md={6} lg={4}>
              <Card size="small" style={{ textAlign: 'center' }}>
                <Statistic title={item.label} value={item.count} />
              </Card>
            </Col>
          ))}
        </Row>
      </Card>

      {/* 第四行：批量晋升操作 */}
      <Card title="批量操作" size="small">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <EnvironmentOutlined style={{ fontSize: 16 }} />
          <span>晋升范围：</span>
          <Select allowClear placeholder="全部地区" value={selectedRegion} onChange={onRegionChange}
            style={{ width: 200 }} options={regionOptions} showSearch
            filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
          />
          <Select allowClear placeholder={selectedRegion ? '该地区全部省份' : '请先选择地区'}
            value={selectedProvince} onChange={(v) => setSelectedProvince(v)} disabled={!selectedRegion}
            style={{ width: 300 }} options={provinceDbOptions} showSearch
            filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
          />
          <Button type="primary" icon={<ThunderboltOutlined />} onClick={handleBatchPromote}
            loading={batchLoading}>
            智能批量晋升
          </Button>
          <Button icon={<ReloadOutlined />} onClick={loadStats}>刷新数据</Button>
        </div>
      </Card>
    </Space>
  );
}
