/**
 * 管理员 — 知识库管理
 *
 * 三个 Tab 页：
 * 1. 方法论卡片 — 查看、搜索、触发生成
 * 2. 通用知识库 — 查看、搜索、删除
 * 3. 定额规则库 — 查看、搜索、导入
 */

import { useEffect, useState, useCallback } from 'react';
import {
  Card, Table, Tag, Button, Space, App, Tabs, Statistic, Row, Col,
  Input, Popconfirm, Select, Modal, Upload, Tooltip,
} from 'antd';
import {
  SearchOutlined, ReloadOutlined, DeleteOutlined,
  UploadOutlined, ThunderboltOutlined, BulbOutlined,
  BookOutlined, FileTextOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { getErrorMessage } from '../../utils/error';

// ============================================================
// 类型定义
// ============================================================

/** 方法论卡片 */
interface MethodCard {
  id: number;
  category: string;
  specialty: string;
  pattern_keys: string[];
  keywords: string[];
  method_text: string;
  common_errors: string;
  sample_count: number;
  confirm_rate: number;
  source_province: string;
  universal_method: string;
  version: number;
}

/** 通用知识库条目 */
interface KnowledgeRecord {
  id: number;
  bill_pattern: string;
  bill_keywords: string[];
  quota_patterns: string[];
  associated_patterns: string[];
  param_hints: Record<string, string>;
  layer: string;
  specialty: string;
  confidence: number;
  confirm_count: number;
  province_list: string[];
  source_province: string;
}

/** 定额规则条目 */
interface RuleRecord {
  id: number;
  province: string;
  specialty: string;
  chapter: string;
  content: string;
  keywords: string;
  source_file: string;
}

// ============================================================
// 方法论卡片 Tab
// ============================================================

function MethodCardsTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [cards, setCards] = useState<MethodCard[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<MethodCard[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [generateLoading, setGenerateLoading] = useState(false);

  // 加载统计
  const loadStats = useCallback(async () => {
    try {
      const { data } = await api.get('/admin/knowledge/method-cards/stats');
      setStats(data);
    } catch {
      // 静默（可能还没初始化）
    }
  }, []);

  // 加载卡片列表
  const loadCards = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const { data } = await api.get<{ items: MethodCard[]; total: number }>(
        '/admin/knowledge/method-cards',
        { params: { page: p, size: 20 } },
      );
      setCards(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载方法卡片失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { loadStats(); }, [loadStats]);
  useEffect(() => { loadCards(page); }, [page, loadCards]);

  // 搜索
  const onSearch = async () => {
    if (!searchQuery.trim()) {
      message.warning('请输入清单名称');
      return;
    }
    setSearchLoading(true);
    try {
      const { data } = await api.get<{ items: MethodCard[] }>(
        '/admin/knowledge/method-cards/search',
        { params: { q: searchQuery.trim(), limit: 10 } },
      );
      setSearchResults(data.items);
    } catch {
      message.error('搜索失败');
    } finally {
      setSearchLoading(false);
    }
  };

  // 触发生成
  const handleGenerate = async (dryRun: boolean) => {
    setGenerateLoading(true);
    try {
      const { data } = await api.post('/admin/knowledge/method-cards/generate', {
        dry_run: dryRun,
        incremental: true,
      }, { timeout: 300000 });
      Modal.success({
        title: dryRun ? '分析结果（预览）' : '生成完成',
        content: (
          <div>
            <p>可生成/已生成：<strong>{data.generated ?? 0}</strong> 张</p>
            <p>已更新：<strong>{data.updated ?? 0}</strong> 张</p>
            <p>已跳过：<strong>{data.skipped ?? 0}</strong> 张</p>
            {(data.failed ?? 0) > 0 && <p style={{ color: 'red' }}>失败：{data.failed} 张</p>}
          </div>
        ),
      });
      if (!dryRun) {
        loadStats();
        loadCards(page);
      }
    } catch (err: unknown) {
      message.error(getErrorMessage(err, '生成失败'));
    } finally {
      setGenerateLoading(false);
    }
  };

  // 卡片表格列
  const columns = [
    { title: '#', dataIndex: 'id', key: 'id', width: 50 },
    { title: '类别', dataIndex: 'category', key: 'category', width: 120 },
    {
      title: '专业',
      dataIndex: 'specialty',
      key: 'specialty',
      width: 70,
      render: (v: string) => <Tag>{v || '-'}</Tag>,
    },
    {
      title: '关键词',
      dataIndex: 'keywords',
      key: 'keywords',
      width: 200,
      render: (v: string[]) => (
        <Space size={2} wrap>
          {(v || []).slice(0, 5).map((k, i) => <Tag key={i} color="blue" style={{ fontSize: 11 }}>{k}</Tag>)}
          {(v || []).length > 5 && <Tag>+{v.length - 5}</Tag>}
        </Space>
      ),
    },
    {
      title: '样本数',
      dataIndex: 'sample_count',
      key: 'sample_count',
      width: 70,
      sorter: (a: MethodCard, b: MethodCard) => a.sample_count - b.sample_count,
    },
    {
      title: '确认率',
      dataIndex: 'confirm_rate',
      key: 'confirm_rate',
      width: 70,
      render: (v: number) => v != null ? `${(v * 100).toFixed(0)}%` : '-',
    },
    {
      title: '来源省份',
      dataIndex: 'source_province',
      key: 'source_province',
      width: 100,
      ellipsis: true,
    },
    {
      title: '版本',
      dataIndex: 'version',
      key: 'version',
      width: 50,
      render: (v: number) => `v${v}`,
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 统计卡片 */}
      <Row gutter={16}>
        <Col span={8}>
          <Card><Statistic title="卡片总数" value={(stats as Record<string, number>)?.total_cards ?? 0} prefix={<BulbOutlined />} /></Card>
        </Col>
        <Col span={8}>
          <Card><Statistic title="覆盖专业数" value={((stats as Record<string, string[]>)?.specialties ?? []).length} /></Card>
        </Col>
        <Col span={8}>
          <Card><Statistic title="平均样本数" value={(stats as Record<string, number>)?.avg_sample_count ?? 0} precision={1} /></Card>
        </Col>
      </Row>

      {/* 搜索栏 */}
      <Card size="small">
        <Space>
          <Input.Search
            placeholder="输入清单名称搜索相关方法卡片"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onSearch={onSearch}
            enterButton={<><SearchOutlined /> 搜索</>}
            loading={searchLoading}
            style={{ width: 400 }}
          />
          <Button
            icon={<ThunderboltOutlined />}
            onClick={() => handleGenerate(true)}
            loading={generateLoading}
          >
            分析可生成
          </Button>
          <Popconfirm
            title="确认调用大模型生成方法卡片？"
            description="增量模式：只生成新模式的卡片，已有的跳过"
            onConfirm={() => handleGenerate(false)}
          >
            <Button type="primary" icon={<ThunderboltOutlined />} loading={generateLoading}>
              增量生成
            </Button>
          </Popconfirm>
        </Space>
      </Card>

      {/* 搜索结果（有搜索内容时显示） */}
      {searchResults.length > 0 && (
        <Card title="搜索结果" size="small">
          {searchResults.map((card) => (
            <Card key={card.id} size="small" style={{ marginBottom: 8 }}>
              <div style={{ fontWeight: 'bold', marginBottom: 4 }}>
                {card.category} <Tag>{card.specialty}</Tag>
              </div>
              <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: '#333', marginBottom: 4 }}>
                {card.method_text}
              </div>
              {card.universal_method && (
                <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: '#666', borderTop: '1px dashed #eee', paddingTop: 4 }}>
                  <strong>通用方法论：</strong>{card.universal_method}
                </div>
              )}
              {card.common_errors && (
                <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: '#ff4d4f', marginTop: 4 }}>
                  <strong>常见错误：</strong>{card.common_errors}
                </div>
              )}
            </Card>
          ))}
        </Card>
      )}

      {/* 卡片列表 */}
      <Card
        title="全部卡片"
        size="small"
        extra={<Button icon={<ReloadOutlined />} onClick={() => loadCards(page)}>刷新</Button>}
      >
        <Table
          rowKey="id"
          dataSource={cards}
          columns={columns}
          loading={loading}
          size="small"
          expandable={{
            expandedRowRender: (record: MethodCard) => (
              <div style={{ padding: '8px 0' }}>
                <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, marginBottom: 8 }}>
                  <strong>方法正文：</strong>{record.method_text || '-'}
                </div>
                {record.universal_method && (
                  <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: '#666', marginBottom: 8 }}>
                    <strong>通用方法论：</strong>{record.universal_method}
                  </div>
                )}
                {record.common_errors && (
                  <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: '#ff4d4f' }}>
                    <strong>常见错误：</strong>{record.common_errors}
                  </div>
                )}
              </div>
            ),
          }}
          pagination={{
            current: page,
            total,
            showTotal: (t) => `共 ${t} 张卡片`,
            onChange: (p) => setPage(p),
          }}
        />
      </Card>
    </Space>
  );
}

// ============================================================
// 通用知识库 Tab
// ============================================================

function UniversalKBTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [records, setRecords] = useState<KnowledgeRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [layerFilter, setLayerFilter] = useState('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Record<string, unknown>[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);

  // 加载统计
  const loadStats = useCallback(async () => {
    try {
      const { data } = await api.get('/admin/knowledge/universal-kb/stats');
      setStats(data);
    } catch {
      // 静默
    }
  }, []);

  // 加载记录
  const loadRecords = useCallback(async (p: number, layer: string) => {
    setLoading(true);
    try {
      const { data } = await api.get<{ items: KnowledgeRecord[]; total: number }>(
        '/admin/knowledge/universal-kb/records',
        { params: { page: p, size: 20, layer } },
      );
      setRecords(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载通用知识库失败');
    } finally {
      setLoading(false);
    }
  }, [message]);

  useEffect(() => { loadStats(); }, [loadStats]);
  useEffect(() => { loadRecords(page, layerFilter); }, [page, layerFilter, loadRecords]);

  // 搜索
  const onSearch = async () => {
    if (!searchQuery.trim()) {
      message.warning('请输入清单描述');
      return;
    }
    setSearchLoading(true);
    try {
      const { data } = await api.get<{ items: Record<string, unknown>[] }>(
        '/admin/knowledge/universal-kb/search',
        { params: { q: searchQuery.trim() } },
      );
      setSearchResults(data.items);
    } catch {
      message.error('搜索失败');
    } finally {
      setSearchLoading(false);
    }
  };

  // 删除
  const deleteRecord = async (id: number) => {
    try {
      await api.delete(`/admin/knowledge/universal-kb/${id}`);
      message.success('删除成功');
      loadRecords(page, layerFilter);
      loadStats();
    } catch {
      message.error('删除失败');
    }
  };

  const columns = [
    { title: '#', dataIndex: 'id', key: 'id', width: 50 },
    {
      title: '清单模式',
      dataIndex: 'bill_pattern',
      key: 'bill_pattern',
      width: 250,
      ellipsis: { showTitle: false },
      render: (v: string) => (
        <Tooltip title={v} placement="topLeft">
          <span style={{ fontSize: 12 }}>{v}</span>
        </Tooltip>
      ),
    },
    {
      title: '定额模式',
      dataIndex: 'quota_patterns',
      key: 'quota_patterns',
      width: 250,
      render: (v: string[]) => (
        <Space size={2} wrap>
          {(v || []).slice(0, 3).map((p, i) => <Tag key={i} color="green" style={{ fontSize: 11 }}>{p}</Tag>)}
          {(v || []).length > 3 && <Tag>+{v.length - 3}</Tag>}
        </Space>
      ),
    },
    {
      title: '层级',
      dataIndex: 'layer',
      key: 'layer',
      width: 70,
      render: (v: string) => (
        <Tag color={v === 'authority' ? 'green' : 'orange'}>
          {v === 'authority' ? '权威' : '候选'}
        </Tag>
      ),
    },
    {
      title: '置信',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 60,
    },
    {
      title: '确认次数',
      dataIndex: 'confirm_count',
      key: 'confirm_count',
      width: 70,
    },
    {
      title: '专业',
      dataIndex: 'specialty',
      key: 'specialty',
      width: 60,
      render: (v: string) => v ? <Tag>{v}</Tag> : '-',
    },
    {
      title: '操作',
      key: 'actions',
      width: 70,
      render: (_: unknown, record: KnowledgeRecord) => (
        <Popconfirm title="确定删除？" onConfirm={() => deleteRecord(record.id)}>
          <Button size="small" danger icon={<DeleteOutlined />} />
        </Popconfirm>
      ),
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Row gutter={16}>
        <Col span={6}>
          <Card><Statistic title="总记录" value={(stats as Record<string, number>)?.total ?? 0} prefix={<BookOutlined />} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="权威层" value={(stats as Record<string, number>)?.authority ?? 0} valueStyle={{ color: '#52c41a' }} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="候选层" value={(stats as Record<string, number>)?.candidate ?? 0} valueStyle={{ color: '#faad14' }} /></Card>
        </Col>
        <Col span={6}>
          <Card><Statistic title="涉及省份" value={(stats as Record<string, number>)?.province_count ?? 0} /></Card>
        </Col>
      </Row>

      {/* 搜索栏 */}
      <Card size="small">
        <Input.Search
          placeholder="输入清单描述搜索知识提示（如：镀锌钢管管道安装 DN25）"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onSearch={onSearch}
          enterButton={<><SearchOutlined /> 搜索</>}
          loading={searchLoading}
          style={{ width: 500 }}
        />
      </Card>

      {/* 搜索结果 */}
      {searchResults.length > 0 && (
        <Card title="搜索结果" size="small">
          {searchResults.map((item, i) => (
            <Card key={i} size="small" style={{ marginBottom: 8 }}>
              <div><strong>清单模式：</strong>{String(item.bill_pattern || '')}</div>
              <div>
                <strong>定额模式：</strong>
                <Space size={2} wrap>
                  {((item.quota_patterns as string[]) || []).map((p, j) => <Tag key={j} color="green">{p}</Tag>)}
                </Space>
              </div>
              {Array.isArray(item.associated_patterns) && item.associated_patterns.length > 0 && (
                <div>
                  <strong>关联定额：</strong>
                  {(item.associated_patterns as string[]).join('、')}
                </div>
              )}
              <div style={{ color: '#999', fontSize: 12 }}>
                相似度：{((item.similarity as number) * 100).toFixed(0)}%，
                置信度：{String(item.confidence ?? '-')}，
                层级：{item.layer === 'authority' ? '权威' : '候选'}
              </div>
            </Card>
          ))}
        </Card>
      )}

      {/* 记录列表 */}
      <Card
        size="small"
        title="知识库记录"
        extra={
          <Space>
            <Select
              value={layerFilter}
              onChange={(v) => { setLayerFilter(v); setPage(1); }}
              style={{ width: 120 }}
              options={[
                { label: '全部', value: 'all' },
                { label: '权威层', value: 'authority' },
                { label: '候选层', value: 'candidate' },
              ]}
            />
            <Button icon={<ReloadOutlined />} onClick={() => loadRecords(page, layerFilter)}>刷新</Button>
          </Space>
        }
      >
        <Table
          rowKey="id"
          dataSource={records}
          columns={columns}
          loading={loading}
          size="small"
          pagination={{
            current: page,
            total,
            showTotal: (t) => `共 ${t} 条`,
            onChange: (p) => setPage(p),
          }}
        />
      </Card>
    </Space>
  );
}

// ============================================================
// 定额规则库 Tab
// ============================================================

function RulesTab() {
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [stats, setStats] = useState<Record<string, unknown> | null>(null);
  const [records, setRecords] = useState<RuleRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [provinceFilter, setProvinceFilter] = useState<string | undefined>(undefined);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<Record<string, unknown>[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [importLoading, setImportLoading] = useState(false);

  // 省份列表（从stats中提取）
  const [provinces, setProvinces] = useState<string[]>([]);

  // 加载统计
  const loadStats = useCallback(async () => {
    try {
      const { data } = await api.get('/admin/knowledge/rules/stats');
      setStats(data);
      // 提取省份列表
      const byProvince = (data.by_province || {}) as Record<string, number>;
      setProvinces(Object.keys(byProvince).sort());
    } catch {
      // 静默
    }
  }, []);

  // 加载记录
  const loadRecords = useCallback(async (p: number) => {
    setLoading(true);
    try {
      const params: Record<string, unknown> = { page: p, size: 20 };
      if (provinceFilter) params.province = provinceFilter;
      const { data } = await api.get<{ items: RuleRecord[]; total: number }>(
        '/admin/knowledge/rules/records',
        { params },
      );
      setRecords(data.items);
      setTotal(data.total);
    } catch {
      message.error('加载规则列表失败');
    } finally {
      setLoading(false);
    }
  }, [message, provinceFilter]);

  useEffect(() => { loadStats(); }, [loadStats]);
  useEffect(() => { loadRecords(page); }, [page, loadRecords]);

  // 搜索
  const onSearch = async () => {
    if (!searchQuery.trim()) {
      message.warning('请输入搜索关键词');
      return;
    }
    setSearchLoading(true);
    try {
      const { data } = await api.get<{ items: Record<string, unknown>[] }>(
        '/admin/knowledge/rules/search',
        { params: { q: searchQuery.trim(), province: provinceFilter || undefined } },
      );
      setSearchResults(data.items);
    } catch {
      message.error('搜索失败');
    } finally {
      setSearchLoading(false);
    }
  };

  // 导入规则文件
  const [importProvince, setImportProvince] = useState('');
  const handleImport = async (file: File) => {
    if (!importProvince) {
      message.warning('请先选择导入的省份');
      return;
    }
    setImportLoading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('province', importProvince);
      const { data } = await api.post('/admin/knowledge/rules/import', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout: 120000,
      });
      const s = data.stats || {};
      Modal.success({
        title: '导入成功',
        content: `共解析 ${s.total || 0} 段，新增 ${s.added || 0} 段，跳过（已存在）${s.skipped || 0} 段`,
      });
      loadStats();
      loadRecords(page);
    } catch (err: unknown) {
      message.error(getErrorMessage(err, '导入失败'));
    } finally {
      setImportLoading(false);
    }
  };

  const columns = [
    { title: '#', dataIndex: 'id', key: 'id', width: 50 },
    {
      title: '省份',
      dataIndex: 'province',
      key: 'province',
      width: 120,
      ellipsis: true,
    },
    {
      title: '专业',
      dataIndex: 'specialty',
      key: 'specialty',
      width: 70,
      render: (v: string) => v ? <Tag>{v}</Tag> : '-',
    },
    {
      title: '章节',
      dataIndex: 'chapter',
      key: 'chapter',
      width: 120,
      ellipsis: true,
    },
    {
      title: '内容',
      dataIndex: 'content',
      key: 'content',
      ellipsis: { showTitle: false },
      render: (v: string) => (
        <Tooltip title={v} placement="topLeft">
          <span style={{ fontSize: 12 }}>{v}</span>
        </Tooltip>
      ),
    },
    {
      title: '关键词',
      dataIndex: 'keywords',
      key: 'keywords',
      width: 150,
      render: (v: string) => {
        if (!v) return '-';
        const kws = v.split(' ').filter(Boolean).slice(0, 4);
        return (
          <Space size={2} wrap>
            {kws.map((k, i) => <Tag key={i} style={{ fontSize: 11 }}>{k}</Tag>)}
          </Space>
        );
      },
    },
  ];

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <Row gutter={16}>
        <Col span={8}>
          <Card><Statistic title="规则总数" value={(stats as Record<string, number>)?.total ?? 0} prefix={<FileTextOutlined />} /></Card>
        </Col>
        <Col span={8}>
          <Card><Statistic title="涉及省份" value={provinces.length} /></Card>
        </Col>
        <Col span={8}>
          <Card>
            <Statistic
              title="涉及专业"
              value={Object.keys((stats as Record<string, Record<string, number>>)?.by_specialty ?? {}).length}
            />
          </Card>
        </Col>
      </Row>

      {/* 搜索 + 导入 */}
      <Card size="small">
        <Space wrap>
          <Select
            allowClear
            placeholder="筛选省份"
            value={provinceFilter}
            onChange={(v) => { setProvinceFilter(v); setPage(1); }}
            style={{ width: 200 }}
            showSearch
            options={provinces.map((p) => ({ label: p, value: p }))}
          />
          <Input.Search
            placeholder="搜索规则内容（如：管道DN25）"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onSearch={onSearch}
            enterButton={<><SearchOutlined /> 搜索</>}
            loading={searchLoading}
            style={{ width: 350 }}
          />
          <Select
            placeholder="选择导入省份"
            value={importProvince || undefined}
            onChange={(v) => setImportProvince(v)}
            style={{ width: 200 }}
            showSearch
            options={provinces.map((p) => ({ label: p, value: p }))}
          />
          <Upload
            accept=".txt"
            showUploadList={false}
            beforeUpload={(file) => {
              handleImport(file as unknown as File);
              return false;
            }}
          >
            <Button icon={<UploadOutlined />} loading={importLoading} disabled={!importProvince}>
              导入规则文件
            </Button>
          </Upload>
        </Space>
      </Card>

      {/* 搜索结果 */}
      {searchResults.length > 0 && (
        <Card title="搜索结果" size="small">
          {searchResults.map((item, i) => (
            <Card key={i} size="small" style={{ marginBottom: 8 }}>
              <div style={{ marginBottom: 4 }}>
                <Tag color="blue">{String(item.province || '')}</Tag>
                {!!item.specialty && <Tag>{String(item.specialty)}</Tag>}
                {!!item.chapter && <Tag>{String(item.chapter)}</Tag>}
                {item.similarity != null && (
                  <span style={{ color: '#999', fontSize: 12 }}>
                    相似度：{((item.similarity as number) * 100).toFixed(0)}%
                  </span>
                )}
              </div>
              <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, color: '#333' }}>
                {String(item.content || '')}
              </div>
            </Card>
          ))}
        </Card>
      )}

      {/* 规则列表 */}
      <Card
        size="small"
        title="规则列表"
        extra={<Button icon={<ReloadOutlined />} onClick={() => loadRecords(page)}>刷新</Button>}
      >
        <Table
          rowKey="id"
          dataSource={records}
          columns={columns}
          loading={loading}
          size="small"
          expandable={{
            expandedRowRender: (record: RuleRecord) => (
              <div style={{ whiteSpace: 'pre-wrap', fontSize: 12, padding: '8px 0' }}>
                {record.content}
              </div>
            ),
          }}
          pagination={{
            current: page,
            total,
            showTotal: (t) => `共 ${t} 条规则`,
            onChange: (p) => setPage(p),
          }}
        />
      </Card>
    </Space>
  );
}

// ============================================================
// 主页面：Tabs 组合三个知识库
// ============================================================

export default function KnowledgeManage() {
  return (
    <Card title="知识库管理">
      <Tabs
        defaultActiveKey="method-cards"
        items={[
          {
            key: 'method-cards',
            label: (
              <span><BulbOutlined /> 方法论卡片</span>
            ),
            children: <MethodCardsTab />,
          },
          {
            key: 'universal-kb',
            label: (
              <span><BookOutlined /> 通用知识库</span>
            ),
            children: <UniversalKBTab />,
          },
          {
            key: 'rules',
            label: (
              <span><FileTextOutlined /> 定额规则库</span>
            ),
            children: <RulesTab />,
          },
        ]}
      />
    </Card>
  );
}
