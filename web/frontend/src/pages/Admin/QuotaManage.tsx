/**
 * 管理员 — 定额库管理
 *
 * 功能：
 * 1. 左侧：按省份（地区）折叠分组展示定额库列表
 * 2. 右侧：当前定额库统计 + 章节列表 + 导入Excel + 导入历史
 */

import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import {
  Card, Table, Tag, Button, Space, App, Row, Col, Statistic,
  Upload, Empty, Descriptions, Modal, Input, Collapse, Badge, Select,
} from 'antd';
import {
  DatabaseOutlined, BookOutlined, UploadOutlined,
  ReloadOutlined, HistoryOutlined,
  PlusOutlined, RightOutlined, FolderOpenOutlined,
} from '@ant-design/icons';
import type { UploadProps } from 'antd';
import api from '../../services/api';
import { extractRegion } from '../../utils/region';
import { getErrorMessage } from '../../utils/error';

// 定额库信息
interface ProvinceInfo {
  name: string;
  total_quotas: number;
  chapter_count: number;
  version: string;
}

// 章节信息
interface ChapterInfo {
  chapter: string;
  count: number;
}

// 导入历史
interface ImportRecord {
  file_path: string;
  file_name: string;
  file_size: number;
  specialty: string;
  quota_count: number;
  imported_at: number;
  status: string;
}

// 按地区分组后的结构
interface RegionGroup {
  region: string;          // 地区名（北京、广东、宁夏等）
  items: ProvinceInfo[];   // 该地区下的所有定额库
  totalQuotas: number;     // 该地区总定额数
}

export default function QuotaManage() {
  const { message } = App.useApp();
  const [provinces, setProvinces] = useState<ProvinceInfo[]>([]);
  const [selectedProvince, setSelectedProvince] = useState<string | null>(null);
  const [chapters, setChapters] = useState<ChapterInfo[]>([]);
  const [history, setHistory] = useState<ImportRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [uploading, setUploading] = useState(false);

  // 导入对话框（先选地区，再选文件夹上传）
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importRegion, setImportRegion] = useState<string>('');    // 选择的地区（已有或新建）
  const [isNewRegion, setIsNewRegion] = useState(false);           // 是否新建地区
  const [newRegionName, setNewRegionName] = useState('');           // 新地区名称
  const [folderName, setFolderName] = useState('');                // 从文件夹名自动提取的定额库名
  const [folderFiles, setFolderFiles] = useState<File[]>([]);      // 文件夹中的 .xlsx 文件
  const [folderProgress, setFolderProgress] = useState('');        // 导入进度提示
  const folderInputRef = useRef<HTMLInputElement>(null);

  // 按地区分组
  const regionGroups: RegionGroup[] = useMemo(() => {
    const map = new Map<string, ProvinceInfo[]>();
    for (const p of provinces) {
      const region = extractRegion(p.name);
      if (!map.has(region)) map.set(region, []);
      map.get(region)!.push(p);
    }
    // 转为数组，按定额总数降序排列
    return Array.from(map.entries())
      .map(([region, items]) => ({
        region,
        items: items.sort((a, b) => b.total_quotas - a.total_quotas),
        totalQuotas: items.reduce((sum, i) => sum + i.total_quotas, 0),
      }))
      .sort((a, b) => b.totalQuotas - a.totalQuotas);
  }, [provinces]);

  // 导入弹窗：地区下拉选项（从已有定额库中提取）
  const importRegionOptions = useMemo(() => {
    return regionGroups.map((g) => ({
      label: `${g.region}（${g.items.length} 个定额库）`,
      value: g.region,
    }));
  }, [regionGroups]);

  // 文件夹选择：提取文件夹名和 .xlsx 文件列表
  const handleFolderSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    const xlsxFiles = files.filter((f) => f.name.endsWith('.xlsx'));
    if (xlsxFiles.length === 0) {
      message.warning('所选文件夹中没有 .xlsx 文件');
      return;
    }
    // 从 webkitRelativePath 提取顶层文件夹名："FolderName/file.xlsx" → "FolderName"
    const firstPath = xlsxFiles[0].webkitRelativePath;
    const topFolder = firstPath.split('/')[0];
    setFolderName(topFolder);
    setFolderFiles(xlsxFiles);
  };

  // 上传文件夹中的所有 .xlsx 文件
  const handleFolderUpload = async () => {
    if (!folderName || folderFiles.length === 0) return;
    setUploading(true);
    let successCount = 0;
    let totalQuotas = 0;
    let failCount = 0;

    for (let i = 0; i < folderFiles.length; i++) {
      setFolderProgress(`正在导入第 ${i + 1}/${folderFiles.length} 个文件：${folderFiles[i].name}`);
      const formData = new FormData();
      formData.append('file', folderFiles[i]);
      formData.append('province', folderName);
      try {
        const { data } = await api.post('/admin/quotas/import', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 300000,
        });
        successCount++;
        totalQuotas += data.imported_count || 0;
      } catch {
        failCount++;
      }
    }

    if (successCount > 0) {
      message.success(
        `导入完成：${successCount} 个文件，共 ${totalQuotas} 条定额` +
        (failCount > 0 ? `（${failCount} 个文件失败）` : ''),
      );
    } else {
      message.error('导入失败');
    }

    // 重置弹窗状态
    setImportRegion('');
    setIsNewRegion(false);
    setNewRegionName('');
    setFolderFiles([]);
    setFolderName('');
    setFolderProgress('');
    setImportModalOpen(false);
    setUploading(false);
    loadProvinces();
    if (selectedProvince) loadProvinceDetail(selectedProvince);
  };

  // 用 ref 跟踪当前选中的省份（避免 loadProvinces 的 useCallback 依赖 selectedProvince）
  const selectedProvinceRef = useRef(selectedProvince);
  useEffect(() => {
    selectedProvinceRef.current = selectedProvince;
  }, [selectedProvince]);

  // 加载省份列表
  const loadProvinces = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get<{ items: ProvinceInfo[] }>('/admin/quotas/provinces');
      setProvinces(data.items);
      // 自动选中第一个有定额的（仅当前没有选中时）
      if (data.items.length > 0 && !selectedProvinceRef.current) {
        const withData = data.items.find((p) => p.total_quotas > 0);
        setSelectedProvince(withData ? withData.name : data.items[0].name);
      }
    } catch {
      // 静默失败
    } finally {
      setLoading(false);
    }
  }, []);

  // 加载指定定额库的章节列表和导入历史
  const loadProvinceDetail = useCallback(async (province: string) => {
    setDetailLoading(true);
    try {
      const [chaptersRes, historyRes] = await Promise.allSettled([
        api.get<{ items: ChapterInfo[] }>(`/admin/quotas/${encodeURIComponent(province)}/chapters`),
        api.get<{ items: ImportRecord[] }>(`/admin/quotas/${encodeURIComponent(province)}/import-history`),
      ]);
      if (chaptersRes.status === 'fulfilled') {
        setChapters(chaptersRes.value.data.items);
      }
      if (historyRes.status === 'fulfilled') {
        setHistory(historyRes.value.data.items);
      }
    } catch {
      message.error('加载详情失败');
    } finally {
      setDetailLoading(false);
    }
  }, [message]);

  useEffect(() => {
    loadProvinces();
  }, [loadProvinces]);

  useEffect(() => {
    if (selectedProvince) {
      loadProvinceDetail(selectedProvince);
    }
  }, [selectedProvince, loadProvinceDetail]);

  // 右侧"导入Excel到此定额库"按钮的上传配置（单文件导入到当前选中的定额库）
  const uploadProps: UploadProps = {
    name: 'file',
    accept: '.xlsx',
    showUploadList: false,
    customRequest: async (options) => {
      const { file, onSuccess, onError } = options;
      if (!selectedProvince) {
        message.error('请先在左侧选择一个定额库');
        return;
      }

      setUploading(true);
      const formData = new FormData();
      formData.append('file', file as Blob);
      formData.append('province', selectedProvince);

      try {
        const { data } = await api.post('/admin/quotas/import', formData, {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 300000,
        });
        message.success(`导入成功：${data.imported_count} 条定额`);
        onSuccess?.(data);
        loadProvinces();
        loadProvinceDetail(selectedProvince);
      } catch (err: unknown) {
        message.error(getErrorMessage(err, '导入失败'));
        onError?.(new Error(getErrorMessage(err, '导入失败')));
      } finally {
        setUploading(false);
      }
    },
  };

  // 当前选中的定额库信息
  const currentProvince = provinces.find((p) => p.name === selectedProvince);
  const totalQuotas = currentProvince?.total_quotas || 0;

  // 章节表格列
  const chapterColumns = [
    {
      title: '序号',
      key: 'index',
      width: 60,
      render: (_: unknown, __: unknown, index: number) => index + 1,
    },
    {
      title: '章节名称',
      dataIndex: 'chapter',
      key: 'chapter',
    },
    {
      title: '定额条数',
      dataIndex: 'count',
      key: 'count',
      width: 120,
      render: (v: number) => <Tag color="green">{v} 条</Tag>,
    },
  ];

  // 格式化时间戳
  const formatTime = (ts: number) => {
    if (!ts) return '-';
    return new Date(ts * 1000).toLocaleString('zh-CN');
  };

  // 格式化文件大小
  const formatSize = (bytes: number) => {
    if (!bytes) return '-';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  };

  // 找到当前选中项所在的地区，用于默认展开
  const selectedRegion = selectedProvince ? extractRegion(selectedProvince) : undefined;

  return (
    <Row gutter={16} style={{ height: '100%' }}>
      {/* 左侧：按地区折叠分组 */}
      <Col span={7}>
        <Card
          title={<><DatabaseOutlined /> 定额库</>}
          loading={loading}
          extra={
            <Button type="primary" size="small" icon={<PlusOutlined />} onClick={() => setImportModalOpen(true)}>
              导入
            </Button>
          }
          styles={{ body: { padding: '0 0 12px 0' } }}
        >
          {provinces.length === 0 ? (
            <div style={{ padding: 24 }}>
              <Empty description="暂无定额库" image={Empty.PRESENTED_IMAGE_SIMPLE}>
                <Button type="primary" onClick={() => setImportModalOpen(true)}>
                  导入第一个定额库
                </Button>
              </Empty>
            </div>
          ) : (
            <Collapse
              bordered={false}
              defaultActiveKey={selectedRegion ? [selectedRegion] : regionGroups.length > 0 ? [regionGroups[0].region] : []}
              expandIcon={({ isActive }) => <RightOutlined rotate={isActive ? 90 : 0} style={{ fontSize: 11 }} />}
              style={{ background: 'transparent' }}
              items={regionGroups.map((group) => ({
                key: group.region,
                label: (
                  <span style={{ fontWeight: 600 }}>
                    {group.region}
                    <Badge
                      count={group.items.length}
                      style={{ marginLeft: 8, backgroundColor: '#1677ff' }}
                      size="small"
                    />
                    <span style={{ fontWeight: 400, color: '#999', fontSize: 12, marginLeft: 8 }}>
                      {group.totalQuotas.toLocaleString()} 条
                    </span>
                  </span>
                ),
                children: (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {group.items.map((item) => (
                      <div
                        key={item.name}
                        onClick={() => setSelectedProvince(item.name)}
                        style={{
                          padding: '6px 10px',
                          borderRadius: 6,
                          cursor: 'pointer',
                          background: selectedProvince === item.name ? '#e6f4ff' : undefined,
                          border: selectedProvince === item.name ? '1px solid #91caff' : '1px solid transparent',
                          transition: 'all 0.15s',
                        }}
                      >
                        <div style={{
                          fontSize: 13,
                          fontWeight: selectedProvince === item.name ? 600 : 400,
                          color: selectedProvince === item.name ? '#1677ff' : undefined,
                          lineHeight: 1.4,
                        }}>
                          {item.name}
                        </div>
                        <div style={{ fontSize: 12, color: '#999' }}>
                          {item.total_quotas.toLocaleString()} 条定额 · {item.chapter_count} 章节
                        </div>
                      </div>
                    ))}
                  </div>
                ),
              }))}
            />
          )}
        </Card>
      </Col>

      {/* 右侧：定额库详情 */}
      <Col span={17}>
        {selectedProvince ? (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            {/* 统计卡片 */}
            <Row gutter={16}>
              <Col span={8}>
                <Card>
                  <Statistic title="总定额数" value={totalQuotas} prefix={<DatabaseOutlined />} />
                </Card>
              </Col>
              <Col span={8}>
                <Card>
                  <Statistic title="章节数" value={chapters.length} prefix={<BookOutlined />} />
                </Card>
              </Col>
              <Col span={8}>
                <Card>
                  <Statistic title="导入文件数" value={history.length} prefix={<HistoryOutlined />} />
                </Card>
              </Col>
            </Row>

            {/* 章节列表 */}
            <Card
              title={<><BookOutlined /> 章节列表 — {selectedProvince}</>}
              loading={detailLoading}
              extra={
                <Space>
                  <Upload {...uploadProps}>
                    <Button icon={<UploadOutlined />} loading={uploading}>
                      导入Excel到此定额库
                    </Button>
                  </Upload>
                  <Button icon={<ReloadOutlined />} onClick={() => loadProvinceDetail(selectedProvince)}>
                    刷新
                  </Button>
                </Space>
              }
            >
              <Table
                rowKey="chapter"
                dataSource={chapters}
                columns={chapterColumns}
                size="small"
                pagination={chapters.length > 20 ? { pageSize: 20, showTotal: (t) => `共 ${t} 个章节` } : false}
              />
            </Card>

            {/* 导入历史 */}
            {history.length > 0 && (
              <Card title={<><HistoryOutlined /> 导入历史</>}>
                <Descriptions bordered column={1} size="small">
                  {history.map((h, i) => (
                    <Descriptions.Item key={i} label={h.file_name || h.file_path.split(/[/\\]/).pop()}>
                      <Space>
                        <Tag color={h.status === 'success' ? 'green' : 'red'}>
                          {h.status === 'success' ? '成功' : '失败'}
                        </Tag>
                        <span>{h.quota_count} 条</span>
                        <span style={{ color: '#999' }}>{formatSize(h.file_size)}</span>
                        <span style={{ color: '#999' }}>{formatTime(h.imported_at)}</span>
                      </Space>
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </Card>
            )}
          </Space>
        ) : (
          <Card style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <Empty description="请从左侧选择一个定额库查看详情" />
          </Card>
        )}
      </Col>

      {/* 隐藏的文件夹选择器 */}
      <input
        type="file"
        ref={folderInputRef}
        style={{ display: 'none' }}
        onChange={handleFolderSelect}
        multiple
      />

      {/* 导入对话框（先选地区，再选文件夹） */}
      <Modal
        title="导入定额"
        open={importModalOpen}
        onCancel={() => {
          setImportModalOpen(false);
          setImportRegion('');
          setIsNewRegion(false);
          setNewRegionName('');
          setFolderFiles([]);
          setFolderName('');
          setFolderProgress('');
        }}
        footer={null}
      >
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          {/* 第一步：选择地区 */}
          <div>
            <div style={{ marginBottom: 8, fontWeight: 500 }}>1. 选择地区</div>
            {!isNewRegion ? (
              <Space direction="vertical" style={{ width: '100%' }} size="small">
                <Select
                  placeholder="选择已有地区"
                  value={importRegion || undefined}
                  onChange={(v) => setImportRegion(v)}
                  style={{ width: '100%' }}
                  options={importRegionOptions}
                />
                <Button
                  type="link"
                  icon={<PlusOutlined />}
                  onClick={() => { setIsNewRegion(true); setImportRegion(''); }}
                  style={{ padding: 0 }}
                >
                  新建地区
                </Button>
              </Space>
            ) : (
              <Space direction="vertical" style={{ width: '100%' }} size="small">
                <Input
                  placeholder="输入地区名称，如：山东"
                  value={newRegionName}
                  onChange={(e) => {
                    setNewRegionName(e.target.value);
                    setImportRegion(e.target.value);
                  }}
                />
                <Button
                  type="link"
                  onClick={() => { setIsNewRegion(false); setNewRegionName(''); setImportRegion(''); }}
                  style={{ padding: 0 }}
                >
                  返回选择已有地区
                </Button>
              </Space>
            )}
          </div>

          {/* 第二步：选择文件夹（地区选了之后才能操作） */}
          {importRegion && (
            <div>
              <div style={{ marginBottom: 4, fontWeight: 500 }}>2. 选择文件夹</div>
              <div style={{ color: '#999', fontSize: 13, marginBottom: 8 }}>
                文件夹名将作为定额库名称，里面所有 .xlsx 文件都会被导入到「{importRegion}」地区
              </div>
              <Button
                icon={<FolderOpenOutlined />}
                onClick={() => {
                  if (folderInputRef.current) {
                    folderInputRef.current.setAttribute('webkitdirectory', '');
                    folderInputRef.current.setAttribute('directory', '');
                    folderInputRef.current.value = '';
                    folderInputRef.current.click();
                  }
                }}
              >
                选择文件夹
              </Button>
            </div>
          )}

          {/* 选完文件夹后显示预览 */}
          {folderName && (
            <Card size="small" style={{ background: '#fafafa' }}>
              <p style={{ margin: '0 0 4px 0' }}>
                <strong>地区：</strong>{importRegion}
              </p>
              <p style={{ margin: '0 0 4px 0' }}>
                <strong>定额库名称：</strong>{folderName}
              </p>
              <p style={{ margin: '0 0 4px 0' }}>
                <strong>Excel 文件：</strong>{folderFiles.length} 个
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {folderFiles.map((f, i) => (
                  <Tag key={i}>{f.name}</Tag>
                ))}
              </div>
            </Card>
          )}

          {/* 导入进度 */}
          {folderProgress && (
            <div style={{ color: '#1677ff', fontSize: 13 }}>{folderProgress}</div>
          )}

          {/* 开始导入按钮 */}
          {folderName && (
            <Button
              type="primary"
              icon={<UploadOutlined />}
              onClick={handleFolderUpload}
              loading={uploading}
              block
            >
              开始导入 {folderFiles.length} 个文件到「{folderName}」
            </Button>
          )}

          <div style={{ color: '#999', fontSize: 13 }}>
            定额Excel格式：A列=编号，B列=名称，C列=单位，D列=工作类型。支持增量导入。
          </div>
        </Space>
      </Modal>
    </Row>
  );
}
