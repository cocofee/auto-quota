/**
 * 管理员 — 定额库管理
 *
 * 单表格视图：展示所有定额库，支持按地区筛选、导入文件夹
 */

import { useEffect, useState, useCallback, useMemo, useRef } from 'react';
import {
  Card, Table, Tag, Button, Space, App, Collapse,
  Empty, Modal, Input, Select,
} from 'antd';
import {
  DatabaseOutlined, UploadOutlined, ReloadOutlined,
  PlusOutlined, FolderOpenOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

// 定额库信息
interface ProvinceInfo {
  name: string;
  total_quotas: number;
  chapter_count: number;
  version: string;
  group?: string; // 分组名（来自后端文件夹结构）
}

export default function QuotaManage() {
  const { message } = App.useApp();
  const [provinces, setProvinces] = useState<ProvinceInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [regionFilter, setRegionFilter] = useState<string | undefined>(undefined);

  // 导入对话框
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importRegion, setImportRegion] = useState<string>('');
  const [isNewRegion, setIsNewRegion] = useState(false);
  const [newRegionName, setNewRegionName] = useState('');
  const [folderName, setFolderName] = useState('');
  const [folderFiles, setFolderFiles] = useState<File[]>([]);
  const [folderProgress, setFolderProgress] = useState('');
  const folderInputRef = useRef<HTMLInputElement>(null);

  // 获取分组名（优先用后端返回的group字段，兜底取前2字）
  const getGroup = useCallback((p: ProvinceInfo) => p.group || p.name.slice(0, 2), []);

  // 地区列表（从后端文件夹分组获取）
  const regionOptions = useMemo(() => {
    const map = new Map<string, number>();
    for (const p of provinces) {
      const region = getGroup(p);
      map.set(region, (map.get(region) || 0) + 1);
    }
    return Array.from(map.entries())
      .sort((a, b) => b[1] - a[1])
      .map(([region, count]) => ({
        label: `${region}（${count}）`,
        value: region,
      }));
  }, [provinces, getGroup]);

  // 导入弹窗地区选项
  const importRegionOptions = useMemo(() => {
    return regionOptions.map((o) => ({ label: o.label, value: o.value }));
  }, [regionOptions]);

  // 按地区筛选后的列表
  const filteredProvinces = useMemo(() => {
    if (!regionFilter) return provinces;
    return provinces.filter((p) => getGroup(p) === regionFilter);
  }, [provinces, regionFilter, getGroup]);

  // 统计
  const totalQuotas = filteredProvinces.reduce((sum, p) => sum + p.total_quotas, 0);

  // 加载省份列表
  const loadProvinces = useCallback(async () => {
    setLoading(true);
    try {
      const { data } = await api.get<{ items: ProvinceInfo[] }>('/admin/quotas/provinces');
      setProvinces(data.items);
    } catch {
      // 静默失败
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProvinces();
  }, [loadProvinces]);

  // 文件夹选择
  const handleFolderSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    const xlsxFiles = files.filter((f) => f.name.endsWith('.xlsx'));
    if (xlsxFiles.length === 0) {
      message.warning('所选文件夹中没有 .xlsx 文件');
      return;
    }
    const firstPath = xlsxFiles[0].webkitRelativePath;
    const topFolder = firstPath.split('/')[0];
    setFolderName(topFolder);
    setFolderFiles(xlsxFiles);
  };

  // 上传文件夹
  const handleFolderUpload = async () => {
    if (!folderName || folderFiles.length === 0) return;
    setUploading(true);
    let successCount = 0;
    let importedQuotas = 0;
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
        importedQuotas += data.imported_count || 0;
      } catch {
        failCount++;
      }
    }

    if (successCount > 0) {
      message.success(
        `导入完成：${successCount} 个文件，共 ${importedQuotas} 条定额` +
        (failCount > 0 ? `（${failCount} 个文件失败）` : ''),
      );
    } else {
      message.error('导入失败');
    }

    setImportRegion('');
    setIsNewRegion(false);
    setNewRegionName('');
    setFolderFiles([]);
    setFolderName('');
    setFolderProgress('');
    setImportModalOpen(false);
    setUploading(false);
    loadProvinces();
  };

  // 按地区分组
  const regionGroups = useMemo(() => {
    const map = new Map<string, ProvinceInfo[]>();
    for (const p of filteredProvinces) {
      const region = getGroup(p);
      if (!map.has(region)) map.set(region, []);
      map.get(region)!.push(p);
    }
    return Array.from(map.entries())
      .map(([region, items]) => {
        items.sort((a, b) => b.total_quotas - a.total_quotas);
        const total = items.reduce((s, i) => s + i.total_quotas, 0);
        return { region, items, total };
      })
      .sort((a, b) => b.total - a.total);
  }, [filteredProvinces, getGroup]);

  return (
    <Card
      title={
        <Space>
          <DatabaseOutlined />
          <span>定额库管理</span>
          {filteredProvinces.length > 0 && (
            <span style={{ fontSize: 13, fontWeight: 'normal', color: '#999' }}>
              {filteredProvinces.length} 个定额库，{totalQuotas.toLocaleString()} 条定额
            </span>
          )}
        </Space>
      }
      extra={
        <Space>
          <Select
            allowClear
            placeholder="全部地区"
            value={regionFilter}
            onChange={setRegionFilter}
            style={{ width: 160 }}
            options={regionOptions}
          />
          <Button icon={<ReloadOutlined />} onClick={loadProvinces}>
            刷新
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setImportModalOpen(true)}>
            导入定额
          </Button>
        </Space>
      }
    >
      {provinces.length === 0 && !loading ? (
        <Empty description="暂无定额库" image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <Button type="primary" onClick={() => setImportModalOpen(true)}>
            导入第一个定额库
          </Button>
        </Empty>
      ) : loading ? (
        <div style={{ textAlign: 'center', padding: 40, color: '#999' }}>加载中...</div>
      ) : (
        <Collapse
          bordered={false}
          defaultActiveKey={regionGroups.length > 0 ? [regionGroups[0].region] : []}
          style={{ background: 'transparent' }}
          items={regionGroups.map((g) => ({
            key: g.region,
            label: (
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <span style={{ fontWeight: 600, fontSize: 15 }}>{g.region}</span>
                <Tag color="blue">{g.items.length} 个定额库</Tag>
                <span style={{ color: '#999', fontSize: 13 }}>{g.total.toLocaleString()} 条定额</span>
              </div>
            ),
            children: (
              <Table
                rowKey="name"
                dataSource={g.items}
                size="small"
                pagination={false}
                tableLayout="fixed"
                columns={[
                  {
                    title: '#',
                    key: 'idx',
                    width: '6%',
                    align: 'center' as const,
                    render: (_: unknown, __: ProvinceInfo, i: number) => (
                      <span style={{ color: '#999' }}>{i + 1}</span>
                    ),
                  },
                  {
                    title: '定额库名称',
                    dataIndex: 'name',
                    key: 'name',
                    width: '54%',
                    ellipsis: true,
                  },
                  {
                    title: '定额条数',
                    dataIndex: 'total_quotas',
                    key: 'total_quotas',
                    width: '20%',
                    align: 'right' as const,
                    render: (v: number) => <b>{v.toLocaleString()}</b>,
                  },
                  {
                    title: '章节数',
                    dataIndex: 'chapter_count',
                    key: 'chapter_count',
                    width: '20%',
                    align: 'center' as const,
                    render: (v: number) => v || '-',
                  },
                ]}
              />
            ),
          }))}
        />
      )}

      {/* 隐藏的文件夹选择器 */}
      <input
        type="file"
        ref={folderInputRef}
        style={{ display: 'none' }}
        onChange={handleFolderSelect}
        multiple
      />

      {/* 导入对话框 */}
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

          {/* 第二步：选择文件夹 */}
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
    </Card>
  );
}
