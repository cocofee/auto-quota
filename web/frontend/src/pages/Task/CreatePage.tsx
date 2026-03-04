/**
 * 新建任务页
 *
 * 客户（普通用户）：上传Excel + 选省份 → 直接开始匹配（2步流程）
 * 管理员：额外显示 Sheet指定、限制条数、经验库开关（3步流程）
 * 匹配模式和大模型由后端配置统一控制，用户不需要选择。
 */

import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card, Form, Button, Select, Switch, Upload, InputNumber, App, Steps, Progress,
  Checkbox, Tag, Tooltip, Input, Radio,
} from 'antd';
import { InboxOutlined, RocketOutlined, FileExcelOutlined, DeleteOutlined, CheckCircleOutlined, SearchOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { read, utils as xlsxUtils } from 'xlsx';
import api from '../../services/api';
import { useAuthStore } from '../../stores/auth';
import { useProvinceStore } from '../../stores/province';
import type { TaskInfo } from '../../types';
import { getSiblingProvinces } from '../../utils/region';
import { getErrorMessage } from '../../utils/error';

const { Dragger } = Upload;

/** 非清单Sheet的关键词（这些Sheet通常不包含需要匹配的清单数据） */
const SKIP_KEYWORDS = [
  '汇总', '造价汇总', '规费', '税金', '措施', '人材机', '人工', '材料',
  '机械', '主材', '甲供', '暂估', '签证', '索赔', '封面', '目录',
  '说明', '编制说明', '取费', '费率', '调差', '价差',
];

/** 判断Sheet名称是否为非清单Sheet（汇总表、措施费等） */
function isSkipSheet(name: string): boolean {
  const n = name.replace(/\s+/g, '');
  return SKIP_KEYWORDS.some(kw => n.includes(kw));
}

/** 判断Sheet名称是否为推荐的清单Sheet（只推荐"分部分项清单"这类核心Sheet） */
function isRecommendSheet(name: string): boolean {
  const n = name.replace(/\s+/g, '');
  // 只推荐明确包含"分部分项"或"工程量清单"的Sheet
  const recommendKeywords = ['分部分项', '工程量清单'];
  return recommendKeywords.some(kw => n.includes(kw));
}

export default function TaskCreatePage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { user } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;

  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState(0); // 上传进度百分比
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [sheetNames, setSheetNames] = useState<string[]>([]); // 从上传的Excel中读取的工作表名列表
  const [selectedSheets, setSelectedSheets] = useState<string[]>([]); // 用户勾选的工作表
  const [sheetFilter, setSheetFilter] = useState<'all' | 'selected' | 'skipped'>('all'); // Sheet筛选模式
  const [sheetSearch, setSheetSearch] = useState(''); // Sheet搜索关键词

  // 根据筛选条件过滤Sheet列表
  const filteredSheets = useMemo(() => {
    return sheetNames.filter((name) => {
      // 搜索过滤
      if (sheetSearch && !name.includes(sheetSearch)) return false;
      // 分类过滤
      if (sheetFilter === 'selected') return selectedSheets.includes(name);
      if (sheetFilter === 'skipped') return isSkipSheet(name);
      return true;
    });
  }, [sheetNames, sheetSearch, sheetFilter, selectedSheets]);
  const [currentStep, setCurrentStep] = useState(0);
  const { provinces: allProvinces, loading: provincesLoading, fetchProvinces, getGroup, getSubgroup } = useProvinceStore(); // 全局缓存的定额库列表
  const [selectedRegion, setSelectedRegion] = useState<string | undefined>(undefined); // 用户选的省份（地区）
  const [selectedSubRegion, setSelectedSubRegion] = useState<string | undefined>(undefined); // 新疆地区选择
  const selectedProvince = Form.useWatch('province', form); // 监听选中的定额库

  // 计算同批兄弟库（同省份+同年份，自动挂载为辅助库）
  const siblingDbs = useMemo(() => {
    if (!selectedProvince) return [];
    return getSiblingProvinces(selectedProvince, allProvinces);
  }, [selectedProvince, allProvinces]);

  // 客户2步流程，管理员3步流程
  const steps = isAdmin
    ? [{ title: '上传文件' }, { title: '配置参数' }, { title: '开始匹配' }]
    : [{ title: '上传文件' }, { title: '开始匹配' }];

  // 按分组（来自后端文件夹结构）：{ "北京": ["北京市建设工程...(2024)", ...], "石油": [...] }
  const regionMap = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const name of allProvinces) {
      const region = getGroup(name);
      if (!map.has(region)) map.set(region, []);
      map.get(region)!.push(name);
    }
    return map;
  }, [allProvinces, getGroup]);

  // 省份（地区）下拉选项
  const regionOptions = useMemo(() => {
    return Array.from(regionMap.entries()).map(([region, items]) => ({
      label: `${region}（${items.length} 个定额库）`,
      value: region,
    }));
  }, [regionMap]);

  // 新疆子地区列表（仅当选了"新疆"时有值）
  const subRegionOptions = useMemo(() => {
    if (selectedRegion !== '新疆') return [];
    const items = regionMap.get('新疆') || [];
    const regions = new Set<string>();
    for (const name of items) {
      const sub = getSubgroup(name);
      if (sub) regions.add(sub);
    }
    return Array.from(regions).map((r) => ({ label: r, value: r }));
  }, [selectedRegion, regionMap, getSubgroup]);

  // 当前省份下的定额库下拉选项（新疆按子地区过滤）
  const dbOptions = useMemo(() => {
    if (!selectedRegion) return [];
    const items = regionMap.get(selectedRegion) || [];
    // 新疆：按选中的子地区过滤
    if (selectedRegion === '新疆' && selectedSubRegion) {
      return items
        .filter((name) => getSubgroup(name) === selectedSubRegion)
        .map((name) => ({ label: name, value: name }));
    }
    return items.map((name) => ({ label: name, value: name }));
  }, [selectedRegion, selectedSubRegion, regionMap, getSubgroup]);

  // 从全局 store 加载定额库列表（有缓存则跳过请求）
  useEffect(() => {
    fetchProvinces().then((list) => {
      if (list.length > 0 && !form.getFieldValue('province')) {
        const firstRegion = getGroup(list[0]);
        setSelectedRegion(firstRegion);
        const firstDb = list.find((p) => getGroup(p) === firstRegion);
        if (firstDb) {
          form.setFieldValue('province', firstDb);
        }
      }
    });
  }, [fetchProvinces, form]);

  // 切换省份时：自动选中该省份下的第一个定额库
  const onRegionChange = (region: string) => {
    setSelectedRegion(region);
    setSelectedSubRegion(undefined); // 重置子地区
    const items = regionMap.get(region) || [];
    if (region === '新疆') {
      // 新疆：不自动选定额库，等用户选子地区
      form.setFieldValue('province', undefined);
    } else if (items.length > 0) {
      form.setFieldValue('province', items[0]);
    } else {
      form.setFieldValue('province', undefined);
    }
  };

  // 切换新疆子地区时：自动选中该地区的第一个定额库
  const onSubRegionChange = (subRegion: string) => {
    setSelectedSubRegion(subRegion);
    const items = regionMap.get('新疆') || [];
    const filtered = items.filter((name) => getSubgroup(name) === subRegion);
    if (filtered.length > 0) {
      form.setFieldValue('province', filtered[0]);
    } else {
      form.setFieldValue('province', undefined);
    }
  };

  /** 提交任务 */
  const onSubmit = async () => {
    try {
      // 用 getFieldsValue(true) 获取所有字段值（包括被条件渲染隐藏的字段）
      // 注意：不能用 validateFields()，因为它只返回当前页面上可见的字段，
      // 省份字段在步骤0，到确认步骤时已不在页面上，会返回 undefined
      const values = form.getFieldsValue(true);

      // 手动验证关键字段（因为 validateFields 无法验证未渲染的字段）
      if (!values.province) {
        message.warning('请先选择定额库');
        setCurrentStep(0);
        return;
      }

      if (fileList.length === 0) {
        message.warning('请先上传清单文件');
        setCurrentStep(0);
        return;
      }

      setLoading(true);
      setUploadPercent(0);

      const formData = new FormData();
      const file = fileList[0].originFileObj as Blob;
      formData.append('file', file);
      formData.append('province', values.province);

      // 管理员设置的高级参数；客户用默认值
      formData.append('use_experience', String(isAdmin ? (values.use_experience ?? true) : true));

      if (isAdmin) {
        // 传选中的sheets（如果不是全选，才传，全选时后端默认处理全部）
        if (selectedSheets.length > 0 && selectedSheets.length < sheetNames.length) {
          formData.append('sheets', selectedSheets.join(','));
        }
        if (values.limit_count) {
          formData.append('limit_count', String(values.limit_count));
        }
      }

      // 根据文件大小动态计算超时（至少 60s，每 MB 加 10s）
      const fileSizeMB = file.size / 1024 / 1024;
      const timeout = Math.max(60000, Math.ceil(fileSizeMB * 10000) + 30000);

      const { data } = await api.post<TaskInfo>('/tasks', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout,
        onUploadProgress: (e) => {
          if (e.total) {
            setUploadPercent(Math.round((e.loaded / e.total) * 100));
          }
        },
      });

      message.success(`任务"${data.name}"创建成功，开始匹配！`);
      navigate('/tasks');
    } catch (err: unknown) {
      message.error(getErrorMessage(err, '创建任务失败，请重试'));
    } finally {
      setLoading(false);
    }
  };

  /** 客户模式：步骤0上传 → 步骤1确认提交 */
  /** 管理员模式：步骤0上传 → 步骤1参数 → 步骤2确认提交 */
  const isConfirmStep = isAdmin ? currentStep === 2 : currentStep === 1;
  const isParamStep = isAdmin && currentStep === 1;

  return (
    <div style={{ display: 'flex', gap: 16, justifyContent: 'center', alignItems: 'flex-start' }}>
      {/* 左侧：Sheet选择面板（上传文件后显示） */}
      {sheetNames.length > 0 && (
        <Card
          title={
            <span>
              工作表
              <span style={{ color: '#888', fontWeight: 'normal', marginLeft: 8, fontSize: 13 }}>
                已选 {selectedSheets.length}/{sheetNames.length}
              </span>
            </span>
          }
          style={{ width: 520, flexShrink: 0 }}
          styles={{ body: { padding: 0 } }}
        >
          {/* 搜索框 */}
          <div style={{ padding: '12px 16px 8px' }}>
            <Input
              placeholder="搜索工作表名称"
              prefix={<SearchOutlined style={{ color: '#bbb' }} />}
              allowClear
              value={sheetSearch}
              onChange={(e) => setSheetSearch(e.target.value)}
            />
          </div>

          {/* 筛选按钮 */}
          <div style={{ padding: '0 16px 8px' }}>
            <Radio.Group
              size="small"
              value={sheetFilter}
              onChange={(e) => setSheetFilter(e.target.value)}
              optionType="button"
              buttonStyle="solid"
            >
              <Radio.Button value="all">全部 {sheetNames.length}</Radio.Button>
              <Radio.Button value="selected">已选 {selectedSheets.length}</Radio.Button>
              <Radio.Button value="skipped">已跳过 {sheetNames.filter(n => isSkipSheet(n)).length}</Radio.Button>
            </Radio.Group>
          </div>

          {/* 全选/取消全选 */}
          <div style={{
            padding: '4px 16px 8px',
            borderTop: '1px solid #f0f0f0',
            borderBottom: '1px solid #f0f0f0',
            marginBottom: 4,
          }}>
            <Checkbox
              checked={selectedSheets.length === sheetNames.length}
              indeterminate={selectedSheets.length > 0 && selectedSheets.length < sheetNames.length}
              onChange={(e) => {
                setSelectedSheets(e.target.checked ? [...sheetNames] : []);
              }}
            >
              全选
            </Checkbox>
          </div>

          {/* Sheet列表 */}
          <div style={{ maxHeight: 400, overflowY: 'auto' }}>
            {filteredSheets.length > 0 ? filteredSheets.map((name) => {
              const skip = isSkipSheet(name);
              const recommend = isRecommendSheet(name);
              return (
                <div
                  key={name}
                  style={{
                    padding: '6px 16px',
                    display: 'flex',
                    alignItems: 'center',
                    gap: 8,
                    opacity: skip && !selectedSheets.includes(name) ? 0.5 : 1,
                    background: selectedSheets.includes(name) ? '#f6ffed' : 'transparent',
                  }}
                >
                  <Checkbox
                    checked={selectedSheets.includes(name)}
                    onChange={(e) => {
                      setSelectedSheets(prev =>
                        e.target.checked
                          ? [...prev, name]
                          : prev.filter(s => s !== name)
                      );
                    }}
                  >
                    <span style={{ color: skip ? '#999' : undefined, fontSize: 15 }}>
                      {name}
                    </span>
                  </Checkbox>
                  {recommend && (
                    <Tooltip title="系统识别为清单Sheet，推荐处理">
                      <Tag color="green" style={{ marginLeft: 'auto', cursor: 'help', fontSize: 13 }}>
                        推荐
                      </Tag>
                    </Tooltip>
                  )}
                  {skip && !selectedSheets.includes(name) && (
                    <Tooltip title="汇总/措施/规费等非清单Sheet，已自动跳过">
                      <Tag color="default" style={{ marginLeft: 'auto', cursor: 'help', fontSize: 13 }}>
                        跳过
                      </Tag>
                    </Tooltip>
                  )}
                </div>
              );
            }) : (
              <div style={{ padding: '16px', textAlign: 'center', color: '#999' }}>
                无匹配结果
              </div>
            )}
          </div>
        </Card>
      )}

      {/* 右侧：原来的步骤卡片 */}
      <Card title="新建匹配任务" style={{ maxWidth: 720, flex: 1 }}>
      {/* 提示信息移到上传区域的hint中 */}
      <Steps
        current={currentStep}
        size="small"
        style={{ marginBottom: 32 }}
        items={steps}
      />

      <Form
        form={form}
        layout="vertical"
        initialValues={{
          use_experience: true,
        }}
      >
        {/* 步骤1：上传文件 + 选省份 */}
        {currentStep === 0 && (
          <>
            <Form.Item
              label="上传清单文件"
              required
              help="支持 .xlsx / .xls 格式的工程量清单"
            >
              {/* 已选文件：显示文件信息卡片 */}
              {fileList.length > 0 ? (
                <div style={{
                  border: '1px solid #91caff',
                  borderRadius: 8,
                  padding: '16px 20px',
                  background: '#e6f4ff',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <FileExcelOutlined style={{ fontSize: 32, color: '#52c41a' }} />
                    <div>
                      <div style={{ fontWeight: 500, fontSize: 15 }}>{fileList[0].name}</div>
                      <div style={{ color: '#888', fontSize: 13 }}>
                        {((fileList[0].size || 0) / 1024).toFixed(0)} KB
                        {sheetNames.length > 0 && `  ·  ${sheetNames.length} 个工作表`}
                      </div>
                    </div>
                  </div>
                  <Button
                    type="text"
                    danger
                    icon={<DeleteOutlined />}
                    onClick={() => { setFileList([]); setSheetNames([]); setSelectedSheets([]); }}
                  >
                    移除
                  </Button>
                </div>
              ) : (
                /* 未选文件：显示拖拽上传区域 */
                <Dragger
                  fileList={[]}
                  maxCount={1}
                  accept=".xlsx,.xls"
                  showUploadList={false}
                  beforeUpload={(file) => {
                    const isExcel = file.name.endsWith('.xlsx') || file.name.endsWith('.xls');
                    if (!isExcel) {
                      message.error('只支持 Excel 文件（.xlsx / .xls）');
                      return Upload.LIST_IGNORE;
                    }
                    const isLt30M = file.size / 1024 / 1024 < 30;
                    if (!isLt30M) {
                      message.error('文件不能超过 30MB');
                      return Upload.LIST_IGNORE;
                    }
                    setFileList([{ uid: file.uid || Date.now().toString(), name: file.name, size: file.size, originFileObj: file } as UploadFile]);

                    // 读取Excel中的工作表（Sheet）名列表 + 校验必备列
                    const reader = new FileReader();
                    reader.onload = (e) => {
                      try {
                        // sheetRows:5 只读前5行（性能优化，不加载全部数据）
                        const wb = read(e.target?.result, { type: 'array', sheetRows: 5 });
                        setSheetNames(wb.SheetNames || []);

                        // 智能选中：非清单Sheet（汇总、措施费等）默认不勾选
                        const autoSelected = (wb.SheetNames || []).filter(
                          (name: string) => !isSkipSheet(name)
                        );
                        setSelectedSheets(autoSelected);

                        // 校验必备列：从所有Sheet的前几行中查找列名
                        const requiredCols: { name: string; aliases: string[] }[] = [
                          { name: '项目编码', aliases: ['项目编码', '清单编码', '编码', '编号'] },
                          { name: '项目名称', aliases: ['项目名称', '清单名称', '名称'] },
                          { name: '项目特征', aliases: ['项目特征', '项目特征描述', '特征描述', '特征', '工程内容'] },
                          { name: '单位', aliases: ['计量单位', '单位'] },
                        ];

                        // 从所有Sheet中收集表头（通常在前3行内）
                        const allHeaders = new Set<string>();
                        for (const sheetName of wb.SheetNames) {
                          const ws = wb.Sheets[sheetName];
                          if (!ws) continue;
                          const rows: unknown[][] = xlsxUtils.sheet_to_json(ws, { header: 1 });
                          for (const row of rows) {
                            if (!Array.isArray(row)) continue;
                            for (const cell of row) {
                              if (cell != null && String(cell).trim()) {
                                allHeaders.add(String(cell).trim());
                              }
                            }
                          }
                        }

                        // 检查每个必备列是否在表头中找到
                        const missing = requiredCols.filter(
                          col => !col.aliases.some(alias => allHeaders.has(alias))
                        );

                        if (missing.length > 0) {
                          const names = missing.map(c => `「${c.name}」`).join('、');
                          message.warning({
                            content: `提醒：未在前几行中找到 ${names} 列。如果这些列在更下方，可忽略此提醒。`,
                            duration: 6,
                          });
                        }
                      } catch {
                        setSheetNames([]);
                      }
                    };
                    reader.readAsArrayBuffer(file);

                    return false;
                  }}
                >
                  <p className="ant-upload-drag-icon">
                    <InboxOutlined />
                  </p>
                  <p className="ant-upload-text">拖拽文件到此处，或点击选择</p>
                  <p className="ant-upload-hint">支持 .xlsx / .xls，最大 30MB，安装工程匹配效果最佳</p>
                </Dragger>
              )}
            </Form.Item>

            <Form.Item
              label="选择省份"
              required
            >
              <Select
                options={regionOptions}
                loading={provincesLoading}
                placeholder="先选择省份"
                value={selectedRegion}
                onChange={onRegionChange}
                showSearch
                filterOption={(input, option) =>
                  (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                }
              />
            </Form.Item>

            {/* 新疆子地区选择（仅新疆显示） */}
            {selectedRegion === '新疆' && subRegionOptions.length > 0 && (
              <Form.Item label="选择地区" required>
                <Select
                  options={subRegionOptions}
                  placeholder="选择新疆地区"
                  value={selectedSubRegion}
                  onChange={onSubRegionChange}
                  showSearch
                  filterOption={(input, option) =>
                    (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                  }
                />
              </Form.Item>
            )}

            <Form.Item
              name="province"
              label="选择定额库"
              rules={[{ required: true, message: '请选择定额库' }]}
            >
              <Select
                options={dbOptions}
                loading={provincesLoading}
                placeholder={
                  selectedRegion === '新疆' && !selectedSubRegion
                    ? '请先选择地区'
                    : selectedRegion
                      ? '选择该省份的定额库'
                      : '请先选择省份'
                }
                disabled={!selectedRegion || (selectedRegion === '新疆' && !selectedSubRegion)}
                showSearch
                filterOption={(input, option) =>
                  (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                }
              />
            </Form.Item>

            {/* 同批兄弟库提示：告知用户自动挂载了哪些辅助库 */}
            {siblingDbs.length > 0 && (
              <div style={{ marginTop: -16, marginBottom: 16, fontSize: 13 }}>
                <div style={{ color: '#52c41a', marginBottom: 4 }}>
                  同时使用同批辅助库（共 {siblingDbs.length} 个）：
                </div>
                <ul style={{ margin: 0, paddingLeft: 20, color: '#666' }}>
                  {siblingDbs.map((name) => (
                    <li key={name}>{name}</li>
                  ))}
                </ul>
              </div>
            )}

            <Button
              type="primary"
              block
              disabled={fileList.length === 0}
              onClick={() => setCurrentStep(1)}
            >
              {isAdmin ? '下一步：配置参数' : '下一步：确认'}
            </Button>
          </>
        )}

        {/* 步骤2（仅管理员）：参数配置 */}
        {isParamStep && (
          <>
            <Form.Item name="limit_count" label="限制条数（调试用）">
              <InputNumber
                min={1}
                max={10000}
                placeholder="不限"
                style={{ width: '100%' }}
              />
            </Form.Item>

            <Form.Item name="use_experience" label="使用经验库" valuePropName="checked">
              <Switch checkedChildren="开" unCheckedChildren="关" />
            </Form.Item>

            <div style={{ display: 'flex', gap: 12 }}>
              <Button block onClick={() => setCurrentStep(0)}>
                上一步
              </Button>
              <Button type="primary" block onClick={() => setCurrentStep(2)}>
                下一步：确认
              </Button>
            </div>
          </>
        )}

        {/* 确认并开始匹配 */}
        {isConfirmStep && (
          <>
            <Card type="inner" title="任务配置确认" style={{ marginBottom: 24 }}>
              <p><strong>文件：</strong>{fileList[0]?.name || '-'}</p>
              <p><strong>定额库：</strong>{form.getFieldValue('province')}</p>
              {siblingDbs.length > 0 && (
                <div>
                  <strong>辅助库：</strong>同批 {siblingDbs.length} 个库自动挂载
                  <ul style={{ margin: '4px 0 0', paddingLeft: 20, color: '#666', fontSize: 13 }}>
                    {siblingDbs.map((name) => (
                      <li key={name}>{name}</li>
                    ))}
                  </ul>
                </div>
              )}
              {isAdmin && (
                <>
                  {sheetNames.length > 0 && (
                    <p><strong>工作表：</strong>
                      {selectedSheets.length === sheetNames.length
                        ? `全部 ${sheetNames.length} 个`
                        : `已选 ${selectedSheets.length}/${sheetNames.length} 个`
                      }
                    </p>
                  )}
                  {form.getFieldValue('limit_count') && (
                    <p><strong>限制条数：</strong>{form.getFieldValue('limit_count')}</p>
                  )}
                  <p><strong>经验库：</strong>{form.getFieldValue('use_experience') ? '使用' : '不使用'}</p>
                </>
              )}
            </Card>

            {/* 上传进度条（上传中显示） */}
            {loading && uploadPercent < 100 && (
              <Progress
                percent={uploadPercent}
                status="active"
                format={(p) => `上传中 ${p}%`}
                style={{ marginBottom: 12 }}
              />
            )}
            {loading && uploadPercent >= 100 && (
              <Progress
                percent={100}
                status="active"
                format={() => '正在创建任务...'}
                style={{ marginBottom: 12 }}
              />
            )}

            <div style={{ display: 'flex', gap: 12 }}>
              <Button block onClick={() => setCurrentStep(isAdmin ? 1 : 0)} disabled={loading}>
                上一步
              </Button>
              <Button
                type="primary"
                block
                icon={<RocketOutlined />}
                loading={loading}
                onClick={onSubmit}
              >
                开始匹配
              </Button>
            </div>
          </>
        )}
      </Form>
    </Card>
    </div>
  );
}
