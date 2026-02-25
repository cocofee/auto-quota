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
} from 'antd';
import { InboxOutlined, RocketOutlined } from '@ant-design/icons';
import type { UploadFile } from 'antd';
import { read, utils } from 'xlsx';
import api from '../../services/api';
import { useAuthStore } from '../../stores/auth';
import { useProvinceStore } from '../../stores/province';
import type { TaskInfo } from '../../types';
import { extractRegion } from '../../utils/region';
import { getErrorMessage } from '../../utils/error';

const { Dragger } = Upload;

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
  const [currentStep, setCurrentStep] = useState(0);
  const { provinces: allProvinces, loading: provincesLoading, fetchProvinces } = useProvinceStore(); // 全局缓存的定额库列表
  const [selectedRegion, setSelectedRegion] = useState<string | undefined>(undefined); // 用户选的省份（地区）

  // 客户2步流程，管理员3步流程
  const steps = isAdmin
    ? [{ title: '上传文件' }, { title: '配置参数' }, { title: '开始匹配' }]
    : [{ title: '上传文件' }, { title: '开始匹配' }];

  // 按省份（地区）分组：{ "北京": ["北京市建设工程...(2024)", ...], "广东": [...] }
  const regionMap = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const name of allProvinces) {
      const region = extractRegion(name);
      if (!map.has(region)) map.set(region, []);
      map.get(region)!.push(name);
    }
    return map;
  }, [allProvinces]);

  // 省份（地区）下拉选项
  const regionOptions = useMemo(() => {
    return Array.from(regionMap.entries()).map(([region, items]) => ({
      label: `${region}（${items.length} 个定额库）`,
      value: region,
    }));
  }, [regionMap]);

  // 当前省份下的定额库下拉选项
  const dbOptions = useMemo(() => {
    if (!selectedRegion) return [];
    const items = regionMap.get(selectedRegion) || [];
    return items.map((name) => ({ label: name, value: name }));
  }, [selectedRegion, regionMap]);

  // 从全局 store 加载定额库列表（有缓存则跳过请求）
  useEffect(() => {
    fetchProvinces().then((list) => {
      if (list.length > 0 && !form.getFieldValue('province')) {
        const firstRegion = extractRegion(list[0]);
        setSelectedRegion(firstRegion);
        const firstDb = list.find((p) => extractRegion(p) === firstRegion);
        if (firstDb) {
          form.setFieldValue('province', firstDb);
        }
      }
    });
  }, [fetchProvinces, form]);

  // 切换省份时：自动选中该省份下的第一个定额库
  const onRegionChange = (region: string) => {
    setSelectedRegion(region);
    const items = regionMap.get(region) || [];
    if (items.length > 0) {
      form.setFieldValue('province', items[0]);
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
        if (values.sheet) {
          formData.append('sheet', values.sheet);
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
    <Card title="新建匹配任务" style={{ maxWidth: 720, margin: '0 auto' }}>
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
              <Dragger
                fileList={fileList}
                maxCount={1}
                accept=".xlsx,.xls"
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
                  setFileList([{ ...file, originFileObj: file } as UploadFile]);

                  // 读取Excel中的工作表（Sheet）名列表
                  const reader = new FileReader();
                  reader.onload = (e) => {
                    try {
                      const wb = read(e.target?.result, { type: 'array', bookSheets: true });
                      setSheetNames(wb.SheetNames || []);
                    } catch {
                      setSheetNames([]);
                    }
                  };
                  reader.readAsArrayBuffer(file);

                  return false;
                }}
                onRemove={() => { setFileList([]); setSheetNames([]); }}
              >
                <p className="ant-upload-drag-icon">
                  <InboxOutlined />
                </p>
                <p className="ant-upload-text">拖拽文件到此处，或点击选择</p>
                <p className="ant-upload-hint">支持 .xlsx / .xls，最大 30MB</p>
              </Dragger>
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

            <Form.Item
              name="province"
              label="选择定额库"
              rules={[{ required: true, message: '请选择定额库' }]}
            >
              <Select
                options={dbOptions}
                loading={provincesLoading}
                placeholder={selectedRegion ? '选择该省份的定额库' : '请先选择省份'}
                disabled={!selectedRegion}
                showSearch
                filterOption={(input, option) =>
                  (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                }
              />
            </Form.Item>

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

        {/* 步骤2（仅管理员）：高级参数配置 */}
        {isParamStep && (
          <>
            <Form.Item name="sheet" label="指定Sheet（可选）">
              <Select
                placeholder="默认处理全部Sheet"
                allowClear
                options={sheetNames.map((name) => ({ label: name, value: name }))}
                notFoundContent={fileList.length === 0 ? '请先上传文件' : '未读取到Sheet'}
              />
            </Form.Item>

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
              {isAdmin && (
                <>
                  {form.getFieldValue('sheet') && (
                    <p><strong>Sheet：</strong>{form.getFieldValue('sheet')}</p>
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
  );
}
