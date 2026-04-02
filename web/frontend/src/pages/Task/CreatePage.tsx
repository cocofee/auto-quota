/**
 * 新建任务页
 *
 * 布局：顶部紧凑表单 + 下方 Sheet列表(左) / 预览表格(右) 并排
 * 智能识别清单Sheet：按内容（而非Sheet名）判断是否为工程量清单
 */

import { useState, useEffect, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Card, Form, Button, Select, Switch, Upload, InputNumber, App, Progress,
  Checkbox, Tag, Input, Radio,
} from 'antd';
import { InboxOutlined, RocketOutlined, FileExcelOutlined, DeleteOutlined, SearchOutlined, ThunderboltOutlined, SettingOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { read, utils as xlsxUtils } from 'xlsx';
import api from '../../services/api';
import { useAuthStore } from '../../stores/auth';
import { useProvinceStore } from '../../stores/province';
import type { TaskInfo } from '../../types';
import { getSiblingProvinces } from '../../utils/region';
import { getErrorMessage } from '../../utils/error';

const { Dragger } = Upload;

/** 非清单Sheet的关键词（用于Sheet名匹配） */
const SKIP_KEYWORDS = [
  '汇总', '造价汇总', '规费', '税金', '措施', '人材机', '人工', '材料',
  '机械', '主材', '甲供', '暂估', '暂列金额', '签证', '索赔', '封面', '目录',
  '说明', '编制说明', '取费', '费率', '调差', '价差',
  '投标报价', '报价汇总', '待审核',
];

/** 非清单内容关键词（用于Sheet内容匹配） */
const SKIP_CONTENT_KEYWORDS = [
  '汇总表', '费用汇总', '报价表', '措施项目', '总价措施',
  '规费', '税金', '其他项目清单', '其他项目费',
  '人材机汇总', '主材汇总', '甲供材料', '暂估价', '暂列金额',
  '签证', '索赔', '编制说明', '取费表', '费率表',
  '招标控制价', '投标总价',
];

/** 清单内容关键词（只有"分部分项"才是真正要匹配的清单） */
const BILL_CONTENT_KEYWORDS = ['分部分项'];

/** 推荐关键词 */
const RECOMMEND_KEYWORDS = ['分部分项', '工程量清单'];

/** 判断Sheet名称是否为非清单Sheet */
function isSkipByName(name: string): boolean {
  const n = name.replace(/\s+/g, '');
  if (RECOMMEND_KEYWORDS.some(kw => n.includes(kw))) return false;
  return SKIP_KEYWORDS.some(kw => n.includes(kw));
}

/** 判断Sheet名称是否为推荐的清单Sheet */
function isRecommendSheet(name: string): boolean {
  const n = name.replace(/\s+/g, '');
  return RECOMMEND_KEYWORDS.some(kw => n.includes(kw));
}

/** 从Sheet的前几行内容中提取摘要标题（第一个非空有意义的文本） */
function extractSheetTitle(rows: unknown[][]): string {
  for (const row of rows.slice(0, 8)) {
    if (!Array.isArray(row)) continue;
    for (const cell of row) {
      const text = cell != null ? String(cell).trim() : '';
      if (text.length >= 3 && text.length <= 50) return text;
    }
  }
  return '';
}

/** 按内容判断Sheet是否为清单（必须含"分部分项"） */
function isBillByContent(rows: unknown[][]): boolean {
  const allText = rows.slice(0, 15).flat().map(c => c != null ? String(c).trim() : '').join(' ');
  return BILL_CONTENT_KEYWORDS.some(kw => allText.includes(kw));
}

/** 按内容判断Sheet是否为非清单 */
function isSkipByContent(rows: unknown[][]): boolean {
  const allText = rows.slice(0, 10).flat().map(c => c != null ? String(c).trim() : '').join(' ');
  return SKIP_CONTENT_KEYWORDS.some(kw => allText.includes(kw));
}

/** 估算Sheet中的清单数据行数（跳过表头和空行） */
function estimateBillRowCount(rows: unknown[][]): number {
  // 找到"项目编码"所在行作为表头，之后的非空行就是数据行
  let headerRow = -1;
  for (let i = 0; i < Math.min(rows.length, 15); i++) {
    const row = rows[i];
    if (!Array.isArray(row)) continue;
    const text = row.map(c => c != null ? String(c).trim() : '').join(' ');
    if (text.includes('项目编码') || text.includes('清单编码')) {
      headerRow = i;
      break;
    }
  }
  if (headerRow < 0) return 0;
  // 统计表头之后有内容的行数
  let count = 0;
  for (let i = headerRow + 1; i < rows.length; i++) {
    const row = rows[i];
    if (!Array.isArray(row)) continue;
    // 有至少2个非空单元格的行算数据行
    const nonEmpty = row.filter(c => c != null && String(c).trim() !== '').length;
    if (nonEmpty >= 2) count++;
  }
  return count;
}

/** 检测是否为噪音行（小计/合计/注释/分页标记等，不是真正的清单数据） */
const NOISE_KEYWORDS = [
  '本页小计', '本页合计', '小计', '合计', '合价',
  '注：', '注:', '备注：', '备注:',
  '第 ', '第　', '共 页', '共　页',
  '为计取规费', '定额人工费',
];
function isNoiseRow(row: unknown[]): boolean {
  if (!Array.isArray(row)) return false;
  const text = row.map(c => c != null ? String(c).trim() : '').join(' ').trim();
  if (!text) return false;
  return NOISE_KEYWORDS.some(kw => text.includes(kw));
}

/** Excel风格列名：A..Z, AA..AZ, BA..BZ... */
function colName(i: number): string {
  let name = '';
  let n = i;
  while (n >= 0) {
    name = String.fromCharCode(65 + (n % 26)) + name;
    n = Math.floor(n / 26) - 1;
  }
  return name;
}

/** Sheet信息（名称+摘要+是否清单） */
interface SheetInfo {
  name: string;       // Sheet名称
  title: string;      // 内容摘要标题
  isBill: boolean;    // 是否清单Sheet
  isSkip: boolean;    // 是否应跳过
  isRecommend: boolean;
  rowCount: number;   // 估算的清单数据行数
}

/** 从文件名中提取省份标签，如 "[广东]暖通工程.xlsx" → "广东"
 *  支持格式：[省份]、【省份】、(省份)、（省份）
 */
function extractProvinceFromFilename(filename: string): string | null {
  // 匹配方括号/中括号/圆括号中的2-4个字的省份名
  const patterns = [
    /[[\[【]([^[\]【】]{2,4}?)[]\]】]/,  // [广东]、【北京】
    /[（(]([^（）()]{2,4}?)[）)]/,         // (上海)、（广西）
  ];
  for (const re of patterns) {
    const m = filename.match(re);
    if (m) {
      const name = m[1];
      // 排除非省份的常见标签（如"安装"、"给排水"等专业名）
      if (/安装|市政|土建|电气|给排水|暖通|消防|园林|装饰/.test(name)) continue;
      return name;
    }
  }
  return null;
}

/** 定额类型推荐：根据文件名和Sheet名推荐安装/市政/建筑/园林等 */
const QUOTA_TYPE_RULES: [RegExp, string][] = [
  [/给排水|暖通|电气|消防|通风|空调|智能化|弱电|强电|管道/, '安装'],
  [/道路|排水管网|路缘石|桥梁|隧道|交通|路灯|管网/, '市政'],
  [/绿化|栽植|种植|园林|景观/, '园林'],
  [/土建|装饰|混凝土|砌体|钢筋|模板|砌筑|抹灰|防水/, '房屋建筑'],
  [/光伏|升压站|发电/, '光伏发电'],
];

function recommendQuotaType(filename: string, sheetNames: string[]): string | null {
  const text = filename + ' ' + sheetNames.join(' ');
  for (const [re, type] of QUOTA_TYPE_RULES) {
    if (re.test(text)) return type;
  }
  return null;
}

export default function TaskCreatePage() {
  const navigate = useNavigate();
  const { message } = App.useApp();
  const { user } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;

  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [uploadPercent, setUploadPercent] = useState(0);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [sheetInfos, setSheetInfos] = useState<SheetInfo[]>([]);  // Sheet信息列表
  const [sheetNames, setSheetNames] = useState<string[]>([]);
  const [selectedSheets, setSelectedSheets] = useState<string[]>([]);
  const [sheetSearch, setSheetSearch] = useState('');
  const [sheetData, setSheetData] = useState<Record<string, unknown[][]>>({});
  const [previewSheet, setPreviewSheet] = useState('');
  const [showAdvanced, setShowAdvanced] = useState(false); // 管理员高级设置展开

  const { provinces: allProvinces, loading: provincesLoading, fetchProvinces, getGroup, getSubgroup } = useProvinceStore();
  const [selectedRegion, setSelectedRegion] = useState<string | undefined>(undefined);
  const [selectedSubRegion, setSelectedSubRegion] = useState<string | undefined>(undefined);
  const selectedProvince = Form.useWatch('province', form);
  const selectedMode = Form.useWatch('mode', form);

  // 计算同批兄弟库
  const siblingDbs = useMemo(() => {
    if (!selectedProvince) return [];
    return getSiblingProvinces(selectedProvince, allProvinces);
  }, [selectedProvince, allProvinces]);

  // 按分组
  const regionMap = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const name of allProvinces) {
      const region = getGroup(name);
      if (!map.has(region)) map.set(region, []);
      map.get(region)!.push(name);
    }
    return map;
  }, [allProvinces, getGroup]);

  const regionOptions = useMemo(() => {
    return Array.from(regionMap.entries()).map(([region, items]) => ({
      label: `${region}（${items.length} 个定额库）`,
      value: region,
    }));
  }, [regionMap]);

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

  const dbOptions = useMemo(() => {
    if (!selectedRegion) return [];
    const items = regionMap.get(selectedRegion) || [];
    if (selectedRegion === '新疆' && selectedSubRegion) {
      return items
        .filter((name) => getSubgroup(name) === selectedSubRegion)
        .map((name) => ({ label: name, value: name }));
    }
    return items.map((name) => ({ label: name, value: name }));
  }, [selectedRegion, selectedSubRegion, regionMap, getSubgroup]);

  useEffect(() => {
    fetchProvinces().then((list) => {
      if (list.length > 0 && !form.getFieldValue('province')) {
        const firstRegion = getGroup(list[0]);
        setSelectedRegion(firstRegion);
        const firstDb = list.find((p) => getGroup(p) === firstRegion);
        if (firstDb) form.setFieldValue('province', firstDb);
      }
    });
  }, [fetchProvinces, form]);

  const onRegionChange = (region: string) => {
    setSelectedRegion(region);
    setSelectedSubRegion(undefined);
    const items = regionMap.get(region) || [];
    if (region === '新疆') {
      form.setFieldValue('province', undefined);
    } else if (items.length > 0) {
      form.setFieldValue('province', items[0]);
    } else {
      form.setFieldValue('province', undefined);
    }
  };

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

  /** 省份自动识别：根据文件名中的省份标签自动选中对应的区域和定额库 */
  const autoSelectProvince = useCallback((provinceName: string, sheetNames: string[]) => {
    // 在 regionMap 中找到匹配的区域
    for (const [region, items] of regionMap.entries()) {
      if (region.includes(provinceName) || provinceName.includes(region)) {
        setSelectedRegion(region);
        // 根据定额类型推荐进一步筛选（如"安装"/"市政"）
        const recType = recommendQuotaType('', sheetNames);
        if (recType) {
          const matched = items.find(name => name.includes(recType));
          if (matched) {
            form.setFieldValue('province', matched);
            message.success(`已自动识别：${region} · ${recType}`);
            return;
          }
        }
        // 没有类型推荐，选第一个
        if (items.length > 0) {
          form.setFieldValue('province', items[0]);
          message.success(`已自动识别省份：${region}`);
        }
        return;
      }
    }
  }, [regionMap, form, message]);

  /** 定额类型推荐：没有省份标签时，根据文件名/Sheet名推荐定额类型 */
  const autoRecommendType = useCallback((filename: string, sheetNames: string[]) => {
    const recType = recommendQuotaType(filename, sheetNames);
    if (recType && selectedRegion) {
      const items = regionMap.get(selectedRegion) || [];
      const matched = items.find(name => name.includes(recType));
      if (matched && matched !== form.getFieldValue('province')) {
        form.setFieldValue('province', matched);
        message.info(`根据文件内容推荐：${recType}定额`);
      }
    }
  }, [selectedRegion, regionMap, form, message]);

  // 根据搜索过滤Sheet
  const filteredSheets = useMemo(() => {
    return sheetInfos.filter((info) => {
      if (sheetSearch) {
        const q = sheetSearch.toLowerCase();
        return info.name.toLowerCase().includes(q) || info.title.toLowerCase().includes(q);
      }
      return true;
    });
  }, [sheetInfos, sheetSearch]);

  // 预览表格数据——跳过开头的空白行，过滤中间的纯空行
  const rawPreviewRows = previewSheet ? (sheetData[previewSheet] || []) : [];
  // 带原始行号的预览数据（过滤纯空行，保留原始Excel行号）
  const previewIndexedRows = useMemo(() => {
    const result: { row: unknown[]; excelRow: number }[] = [];
    for (let i = 0; i < rawPreviewRows.length; i++) {
      const row = rawPreviewRows[i];
      if (!Array.isArray(row)) continue;
      const nonEmpty = row.filter(c => c != null && String(c).trim() !== '').length;
      if (nonEmpty === 0) continue; // 跳过纯空行
      result.push({ row, excelRow: i + 1 });
    }
    return result;
  }, [rawPreviewRows]);
  const previewMaxCols = useMemo(() => {
    return previewIndexedRows.reduce((max, { row }) => Math.max(max, row.length), 0);
  }, [previewIndexedRows]);

  /** 处理文件上传 */
  const handleFileUpload = (file: File) => {
    setFileList([{ uid: Date.now().toString(), name: file.name, size: file.size, originFileObj: file } as UploadFile]);

    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const wb = read(e.target?.result, { type: 'array', sheetRows: 30 });
        setSheetNames(wb.SheetNames || []);

        // 读取每个Sheet的前30行
        const dataMap: Record<string, unknown[][]> = {};
        const infos: SheetInfo[] = [];

        for (const sn of wb.SheetNames) {
          const ws = wb.Sheets[sn];
          if (!ws) continue;
          const rows: unknown[][] = xlsxUtils.sheet_to_json(ws, { header: 1, defval: '' });
          dataMap[sn] = rows;

          const title = extractSheetTitle(rows);
          const billByContent = isBillByContent(rows);
          const skipByContent = isSkipByContent(rows);
          const skipByName = isSkipByName(sn);
          const recommend = isRecommendSheet(sn);
          const rowCount = estimateBillRowCount(rows);

          // 名称明确是非清单（汇总/措施/规费等）→ 直接跳过，不管内容
          // 除非名称本身包含"分部分项"（如"分部分项工程清单与计价表"）
          const shouldSkip = skipByName
            ? !recommend  // 名称说跳过，但名称含"分部分项"就不跳
            : (skipByContent && !billByContent);  // 名称不说跳过，按内容判断
          const isBill = !shouldSkip && (billByContent || recommend);

          infos.push({
            name: sn,
            title,
            isBill,
            isSkip: shouldSkip,
            isRecommend: isBill,
            rowCount,
          });
        }

        setSheetData(dataMap);
        setSheetInfos(infos);

        // 智能选中：只选清单Sheet（不是"非跳过"，而是"确认是清单"）
        const autoSelected = infos.filter(i => i.isBill).map(i => i.name);
        setSelectedSheets(autoSelected);

        // 默认预览第一个清单Sheet
        const firstBill = infos.find(i => i.isBill) || infos.find(i => !i.isSkip) || infos[0];
        setPreviewSheet(firstBill?.name || '');

        // === 省份自动识别（文档5.1）===
        const detectedProvince = extractProvinceFromFilename(file.name);
        if (detectedProvince) {
          autoSelectProvince(detectedProvince, wb.SheetNames);
        } else {
          // 没检测到省份，尝试推荐定额类型（文档5.2）
          autoRecommendType(file.name, wb.SheetNames);
        }

      } catch {
        setSheetNames([]);
        setSheetData({});
        setSheetInfos([]);
        setPreviewSheet('');
      }
    };
    reader.readAsArrayBuffer(file);
  };

  /** 提交任务 */
  const onSubmit = async () => {
    try {
      const values = form.getFieldsValue(true);
      if (!values.province) {
        message.warning('请先选择定额库');
        return;
      }
      if (fileList.length === 0) {
        message.warning('请先上传清单文件');
        return;
      }
      if (selectedSheets.length === 0) {
        message.warning('请至少选择一个工作表');
        return;
      }

      setLoading(true);
      setUploadPercent(0);

      const formData = new FormData();
      const file = fileList[0].originFileObj as Blob;
      formData.append('file', file);
      formData.append('province', values.province);
      formData.append('mode', values.mode || 'agent');

      // 始终显式传递选中的 sheets，避免“全选”时被后端回退到自动筛选。
      if (selectedSheets.length > 0) {
        formData.append('sheet', JSON.stringify(selectedSheets));
      }

      formData.append('use_experience', String(isAdmin ? (values.use_experience ?? true) : true));
      if (isAdmin && values.limit_count) {
        formData.append('limit_count', String(values.limit_count));
      }

      const fileSizeMB = file.size / 1024 / 1024;
      const timeout = Math.max(60000, Math.ceil(fileSizeMB * 10000) + 30000);

      const { data } = await api.post<TaskInfo>('/tasks', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
        timeout,
        onUploadProgress: (e) => {
          if (e.total) setUploadPercent(Math.round((e.loaded / e.total) * 100));
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

  // 统计
  const billCount = sheetInfos.filter(i => i.isBill).length;
  const skipCount = sheetInfos.filter(i => i.isSkip).length;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: 'calc(100vh - 80px)', padding: '0 16px' }}>

      {/* ========== 顶部：紧凑表单 ========== */}
      <Card styles={{ body: { padding: '12px 20px' } }}>
        {/* 主行：文件 + 省份 + 定额库 + 模式 + 按钮 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          {/* 文件上传 */}
          {fileList.length > 0 ? (
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8, height: 32,
              border: '1px solid #91caff', borderRadius: 6, padding: '0 12px', background: '#e6f4ff',
            }}>
              <FileExcelOutlined style={{ fontSize: 16, color: '#52c41a' }} />
              <span style={{ fontSize: 13, fontWeight: 500, maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {fileList[0].name}
              </span>
              <span style={{ fontSize: 11, color: '#888' }}>
                {((fileList[0].size || 0) / 1024).toFixed(0)} KB · {sheetNames.length} 表
              </span>
              <DeleteOutlined
                style={{ fontSize: 12, color: '#ff4d4f', cursor: 'pointer', marginLeft: 2 }}
                onClick={() => { setFileList([]); setSheetNames([]); setSelectedSheets([]); setSheetData({}); setSheetInfos([]); setPreviewSheet(''); }}
              />
            </div>
          ) : (
            <Upload
              maxCount={1} accept=".xlsx,.xls" showUploadList={false}
              beforeUpload={(file) => {
                const isExcel = file.name.endsWith('.xlsx') || file.name.endsWith('.xls');
                if (!isExcel) { message.error('只支持 Excel 文件'); return Upload.LIST_IGNORE; }
                if (file.size / 1024 / 1024 >= 30) { message.error('文件不能超过 30MB'); return Upload.LIST_IGNORE; }
                handleFileUpload(file);
                return false;
              }}
            >
              <Button icon={<InboxOutlined />} size="middle">上传清单</Button>
            </Upload>
          )}

          {/* 分隔线 */}
          <div style={{ width: 1, height: 20, background: '#e8e8e8' }} />

          {/* 省份+定额库 */}
          <Form form={form} layout="inline" initialValues={{ use_experience: true, mode: 'agent' }}
            style={{ display: 'contents' }}
          >
            <Form.Item style={{ marginBottom: 0 }}>
              <Select
                options={regionOptions} loading={provincesLoading}
                placeholder="选择省份" value={selectedRegion} onChange={onRegionChange}
                showSearch style={{ width: 150 }}
                filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
              />
            </Form.Item>

            {selectedRegion === '新疆' && subRegionOptions.length > 0 && (
              <Form.Item style={{ marginBottom: 0 }}>
                <Select
                  options={subRegionOptions} placeholder="选择地区"
                  value={selectedSubRegion} onChange={onSubRegionChange}
                  showSearch style={{ width: 130 }}
                  filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
                />
              </Form.Item>
            )}

            <Form.Item name="province" style={{ marginBottom: 0 }}>
              <Select
                options={dbOptions} loading={provincesLoading}
                placeholder={selectedRegion ? '选择定额库' : '请先选省份'}
                disabled={!selectedRegion || (selectedRegion === '新疆' && !selectedSubRegion)}
                showSearch style={{ width: 320 }}
                filterOption={(input, option) => (option?.label ?? '').toLowerCase().includes(input.toLowerCase())}
              />
            </Form.Item>

            {/* 分隔线 */}
            <div style={{ width: 1, height: 20, background: '#e8e8e8' }} />

            {/* 匹配模式 */}
            <Form.Item name="mode" style={{ marginBottom: 0 }}>
              <Radio.Group optionType="button" buttonStyle="solid" size="middle">
                <Radio.Button value="search"><ThunderboltOutlined /> 快速</Radio.Button>
                <Radio.Button value="agent"><RocketOutlined /> 精准</Radio.Button>
              </Radio.Group>
            </Form.Item>
          </Form>

          {/* 管理员设置齿轮 */}
          {isAdmin && (
            <SettingOutlined
              style={{ fontSize: 14, color: showAdvanced ? '#1890ff' : '#bbb', cursor: 'pointer' }}
              onClick={() => setShowAdvanced(!showAdvanced)}
            />
          )}

          {/* 开始按钮 */}
          <Button type="primary" icon={<RocketOutlined />} onClick={onSubmit}
            loading={loading} disabled={fileList.length === 0 || !form.getFieldValue('province')}
            size="middle"
          >
            开始匹配
          </Button>

          {/* 右侧推到最右：模式提示 + 已选工作表 */}
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10, fontSize: 12 }}>
            <span style={{
              padding: '2px 8px', borderRadius: 3,
              background: selectedMode === 'search' ? '#fff7e6' : '#f6ffed',
              color: selectedMode === 'search' ? '#d46b08' : '#389e0d',
              whiteSpace: 'nowrap',
            }}>
              {selectedMode === 'search' ? '⚡ 不调大模型' : '🚀 大模型逐条分析'}
            </span>
            {selectedSheets.length > 0 && sheetNames.length > 0 && (
              <span style={{ color: '#999', whiteSpace: 'nowrap' }}>
                已选 {selectedSheets.length}/{sheetNames.length} 个工作表
              </span>
            )}
          </div>
        </div>

        {/* 管理员高级设置（折叠） */}
        {isAdmin && showAdvanced && (
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid #f0f0f0', display: 'flex', gap: 16, alignItems: 'center' }}>
            <Form form={form} layout="inline">
              <Form.Item name="limit_count" label="限制条数" style={{ marginBottom: 0 }}>
                <InputNumber min={1} max={10000} placeholder="不限" style={{ width: 100 }} />
              </Form.Item>
              <Form.Item name="use_experience" label="经验库" valuePropName="checked" style={{ marginBottom: 0 }}>
                <Switch checkedChildren="开" unCheckedChildren="关" />
              </Form.Item>
            </Form>
            {siblingDbs.length > 0 && (
              <span style={{ fontSize: 12, color: '#52c41a' }}>
                辅助库: {siblingDbs.length} 个自动挂载
              </span>
            )}
          </div>
        )}

        {/* 上传进度 */}
        {loading && (
          <Progress
            percent={uploadPercent} status="active"
            format={(p) => uploadPercent >= 100 ? '正在创建任务...' : `上传中 ${p}%`}
            style={{ marginTop: 8 }}
          />
        )}
      </Card>

      {/* ========== 未上传文件时：显示拖拽上传区 ========== */}
      {fileList.length === 0 && (
        <Card style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Dragger
            fileList={[]} maxCount={1} accept=".xlsx,.xls" showUploadList={false}
            style={{ padding: '40px 80px' }}
            beforeUpload={(file) => {
              const isExcel = file.name.endsWith('.xlsx') || file.name.endsWith('.xls');
              if (!isExcel) { message.error('只支持 Excel 文件'); return Upload.LIST_IGNORE; }
              if (file.size / 1024 / 1024 >= 30) { message.error('文件不能超过 30MB'); return Upload.LIST_IGNORE; }
              handleFileUpload(file);
              return false;
            }}
          >
            <p className="ant-upload-drag-icon"><InboxOutlined /></p>
            <p className="ant-upload-text">拖拽清单文件到此处，或点击选择</p>
            <p className="ant-upload-hint">支持 .xlsx / .xls，最大 30MB</p>
          </Dragger>
        </Card>
      )}

      {/* ========== 上传文件后：Sheet列表(左) + 预览表格(右) 并排 ========== */}
      {sheetInfos.length > 0 && (
        <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>

          {/* 左侧：Sheet列表 */}
          <Card
            title={<span style={{ fontSize: 14 }}>工作表</span>}
            extra={<span style={{ fontSize: 12, color: '#888' }}>清单 {billCount} / 跳过 {skipCount}</span>}
            style={{ width: 280, flexShrink: 0, display: 'flex', flexDirection: 'column' }}
            styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 } }}
          >
            {/* 搜索 */}
            <div style={{ padding: '8px 10px 4px' }}>
              <Input
                placeholder="搜索" prefix={<SearchOutlined style={{ color: '#bbb' }} />}
                allowClear size="small" value={sheetSearch}
                onChange={(e) => setSheetSearch(e.target.value)}
              />
            </div>

            {/* 全选 */}
            <div style={{ padding: '4px 10px 6px', borderBottom: '1px solid #f0f0f0' }}>
              <Checkbox
                checked={selectedSheets.length === sheetNames.length}
                indeterminate={selectedSheets.length > 0 && selectedSheets.length < sheetNames.length}
                onChange={(e) => setSelectedSheets(e.target.checked ? [...sheetNames] : [])}
              >
                <span style={{ fontSize: 12 }}>全选</span>
              </Checkbox>
            </div>

            {/* Sheet列表 */}
            <div style={{ flex: 1, overflowY: 'auto' }}>
              {filteredSheets.map((info) => {
                const isSelected = selectedSheets.includes(info.name);
                const isPreviewing = previewSheet === info.name;
                return (
                  <div
                    key={info.name}
                    onClick={() => setPreviewSheet(info.name)}
                    style={{
                      padding: '4px 10px',
                      display: 'flex', alignItems: 'center', gap: 6,
                      cursor: 'pointer',
                      opacity: info.isSkip && !isSelected ? 0.5 : 1,
                      background: isPreviewing ? '#e6f7ff' : isSelected ? '#f6ffed' : 'transparent',
                      borderLeft: isPreviewing ? '3px solid #1890ff' : '3px solid transparent',
                    }}
                  >
                    <span onClick={(e) => e.stopPropagation()}>
                      <Checkbox
                        checked={isSelected}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedSheets(prev => [...prev, info.name]);
                            setPreviewSheet(info.name);
                          } else {
                            setSelectedSheets(prev => prev.filter(s => s !== info.name));
                          }
                        }}
                      />
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 13, color: info.isSkip && !isSelected ? '#999' : '#333',
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {info.name}
                      </div>
                      {/* 内容摘要（Sheet名是数字或很短时特别有用） */}
                      {info.title && info.title !== info.name && (
                        <div style={{
                          fontSize: 11, color: '#999', marginTop: 1,
                          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                        }}>
                          {info.title}
                        </div>
                      )}
                    </div>
                    {info.isRecommend && !info.isSkip && (
                      <Tag color="green" style={{ fontSize: 11, lineHeight: '18px', padding: '0 4px', margin: 0 }}>
                        {info.rowCount > 0 ? `${info.rowCount}条` : '清单'}
                      </Tag>
                    )}
                    {info.isSkip && !isSelected && (
                      <Tag color="default" style={{ fontSize: 11, lineHeight: '18px', padding: '0 4px', margin: 0 }}>跳过</Tag>
                    )}
                  </div>
                );
              })}
            </div>
          </Card>

          {/* 右侧：预览表格 */}
          <Card
            title={
              previewSheet ? (
                <span style={{ fontSize: 14 }}>
                  {previewSheet}
                  {sheetInfos.find(i => i.name === previewSheet)?.title && (
                    <span style={{ color: '#888', fontWeight: 'normal', marginLeft: 8, fontSize: 12 }}>
                      {sheetInfos.find(i => i.name === previewSheet)?.title}
                    </span>
                  )}
                </span>
              ) : '选择工作表预览'
            }
            extra={previewIndexedRows.length > 0 && (
              <span style={{ fontSize: 12, color: '#888' }}>{previewIndexedRows.length} 行 / {previewMaxCols} 列</span>
            )}
            style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}
            styles={{ body: { padding: 0, flex: 1, overflow: 'auto' } }}
          >
            {previewSheet && previewIndexedRows.length > 0 ? (
              <table style={{ borderCollapse: 'collapse', fontSize: 13, whiteSpace: 'nowrap', width: '100%' }}>
                <thead>
                  <tr style={{ background: '#fafafa', position: 'sticky', top: 0, zIndex: 1 }}>
                    <th style={{
                      padding: '6px 10px', borderBottom: '2px solid #e8e8e8', borderRight: '1px solid #f0f0f0',
                      color: '#999', fontWeight: 500, position: 'sticky', left: 0, background: '#fafafa', zIndex: 2,
                      minWidth: 40,
                    }}>#</th>
                    {Array.from({ length: previewMaxCols }, (_, i) => (
                      <th key={i} style={{
                        padding: '6px 10px', borderBottom: '2px solid #e8e8e8', borderRight: '1px solid #f0f0f0',
                        color: '#999', fontWeight: 500, minWidth: 80,
                      }}>
                        {colName(i)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {previewIndexedRows.map(({ row, excelRow }, ri) => {
                    const noise = isNoiseRow(row);
                    const bgNormal = ri % 2 === 0 ? '#fff' : '#fafafa';
                    const bg = noise ? '#f5f5f5' : bgNormal;
                    return (
                    <tr key={ri} style={{ background: bg, opacity: noise ? 0.5 : 1 }}>
                      <td style={{
                        padding: '5px 10px', borderBottom: '1px solid #f0f0f0', borderRight: '1px solid #f0f0f0',
                        color: '#bbb', textAlign: 'center', position: 'sticky', left: 0,
                        background: bg, zIndex: 1, fontWeight: 500,
                      }}>{excelRow}</td>
                      {Array.from({ length: previewMaxCols }, (_, ci) => (
                        <td key={ci} style={{
                          padding: '5px 10px', borderBottom: '1px solid #f0f0f0', borderRight: '1px solid #f0f0f0',
                          maxWidth: 600, overflow: 'hidden', textOverflow: 'ellipsis',
                          color: noise ? '#bbb' : undefined,
                        }}
                          title={row[ci] != null ? String(row[ci]) : ''}
                        >
                          {row[ci] != null ? String(row[ci]) : ''}
                        </td>
                      ))}
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#999' }}>
                {previewSheet ? '此工作表为空' : '点击左侧工作表预览内容'}
              </div>
            )}
          </Card>
        </div>
      )}
    </div>
  );
}
