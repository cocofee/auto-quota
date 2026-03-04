/**
 * 管理员 — 系统设置
 *
 * 显示系统配置信息和运行状态：
 * 1. 服务健康状态
 * 2. 注册邀请码
 * 3. 匹配模型配置
 * 4. 验证模型配置
 * 5. 系统参数
 */

import { useEffect, useState } from 'react';
import {
  Card, Space, App, Tag, Descriptions, Row, Col, Statistic, Badge,
  Input, Button, Select, Form, Alert,
} from 'antd';
import {
  CheckCircleOutlined, CloseCircleOutlined,
  DatabaseOutlined, CloudOutlined, SaveOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { getErrorMessage } from '../../utils/error';

// 支持的模型列表
const MODEL_OPTIONS = [
  { value: 'qwen', label: '通义千问（推荐）', defaultModel: 'qwen-plus', defaultUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { value: 'deepseek', label: 'DeepSeek', defaultModel: 'deepseek-chat', defaultUrl: 'https://api.deepseek.com' },
  { value: 'kimi', label: 'Kimi', defaultModel: 'kimi-k2.5', defaultUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1' },
  { value: 'claude', label: 'Claude', defaultModel: 'claude-sonnet-4-20250514', defaultUrl: '' },
  { value: 'openai', label: 'OpenAI', defaultModel: 'gpt-4o', defaultUrl: 'https://api.openai.com/v1' },
];

// 验证模型额外支持"跟匹配模型走"选项
const VERIFY_MODEL_OPTIONS = [
  { value: '', label: '跟匹配模型走（不单独配置）', defaultModel: '', defaultUrl: '' },
  ...MODEL_OPTIONS,
];

interface LlmConfig {
  llm_type: string;
  api_key_masked: string;
  has_api_key: boolean;
  base_url: string;
  model: string;
}

export default function SettingsPage() {
  const { message } = App.useApp();
  const [provinces, setProvinces] = useState<string[]>([]);
  const [healthOk, setHealthOk] = useState(false);
  const [loading, setLoading] = useState(false);
  const [inviteCode, setInviteCode] = useState('');
  const [inviteCodeInput, setInviteCodeInput] = useState('');
  const [inviteSaving, setInviteSaving] = useState(false);

  // 匹配模型配置
  const [llmConfig, setLlmConfig] = useState<LlmConfig | null>(null);
  const [llmForm] = Form.useForm();
  const [llmSaving, setLlmSaving] = useState(false);

  // 验证模型配置
  const [verifyConfig, setVerifyConfig] = useState<LlmConfig | null>(null);
  const [verifyForm] = Form.useForm();
  const [verifySaving, setVerifySaving] = useState(false);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [provRes, healthRes, inviteRes, llmRes, verifyRes] = await Promise.allSettled([
        api.get<{ provinces: string[] }>('/provinces'),
        api.get('/health'),
        api.get<{ invite_code: string }>('/admin/invite-code'),
        api.get<LlmConfig>('/admin/llm-config'),
        api.get<LlmConfig>('/admin/verify-config'),
      ]);

      if (provRes.status === 'fulfilled') {
        setProvinces(provRes.value.data.provinces);
      }
      if (healthRes.status === 'fulfilled') {
        setHealthOk(true);
      }
      if (inviteRes.status === 'fulfilled') {
        setInviteCode(inviteRes.value.data.invite_code);
        setInviteCodeInput(inviteRes.value.data.invite_code);
      }
      if (llmRes.status === 'fulfilled') {
        const cfg = llmRes.value.data;
        setLlmConfig(cfg);
        llmForm.setFieldsValue({
          llm_type: cfg.llm_type,
          base_url: cfg.base_url,
          model: cfg.model,
          api_key: '',
        });
      }
      if (verifyRes.status === 'fulfilled') {
        const cfg = verifyRes.value.data;
        setVerifyConfig(cfg);
        verifyForm.setFieldsValue({
          llm_type: cfg.llm_type || '',
          base_url: cfg.base_url,
          model: cfg.model,
          api_key: '',
        });
      }
    } catch {
      message.error('加载配置失败');
    } finally {
      setLoading(false);
    }
  };

  // 修改邀请码
  const saveInviteCode = async () => {
    if (!inviteCodeInput.trim() || inviteCodeInput.length < 4) {
      message.warning('邀请码至少4位');
      return;
    }
    setInviteSaving(true);
    try {
      await api.put('/admin/invite-code', { invite_code: inviteCodeInput.trim() });
      setInviteCode(inviteCodeInput.trim());
      message.success('邀请码已更新');
    } catch {
      message.error('修改失败');
    } finally {
      setInviteSaving(false);
    }
  };

  // 切换模型类型时自动填入默认值（通用函数）
  const onTypeChange = (type: string, form: ReturnType<typeof Form.useForm>[0], options: typeof MODEL_OPTIONS) => {
    const opt = options.find((o) => o.value === type);
    if (opt) {
      form.setFieldsValue({
        model: opt.defaultModel,
        base_url: opt.defaultUrl,
      });
    }
  };

  // 保存匹配模型配置
  const saveLlmConfig = async () => {
    setLlmSaving(true);
    try {
      const values = llmForm.getFieldsValue();
      await api.put('/admin/llm-config', {
        llm_type: values.llm_type,
        api_key: values.api_key || '',
        base_url: values.base_url || '',
        model: values.model || '',
      });
      message.success('匹配模型配置已保存，下次任务生效');
      const { data } = await api.get<LlmConfig>('/admin/llm-config');
      setLlmConfig(data);
      llmForm.setFieldValue('api_key', '');
    } catch (err) {
      message.error(getErrorMessage(err, '保存失败'));
    } finally {
      setLlmSaving(false);
    }
  };

  // 保存验证模型配置
  const saveVerifyConfig = async () => {
    setVerifySaving(true);
    try {
      const values = verifyForm.getFieldsValue();
      await api.put('/admin/verify-config', {
        llm_type: values.llm_type || '',
        api_key: values.api_key || '',
        base_url: values.base_url || '',
        model: values.model || '',
      });
      message.success('验证模型配置已保存，下次任务生效');
      const { data } = await api.get<LlmConfig>('/admin/verify-config');
      setVerifyConfig(data);
      verifyForm.setFieldValue('api_key', '');
    } catch (err) {
      message.error(getErrorMessage(err, '保存失败'));
    } finally {
      setVerifySaving(false);
    }
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 服务状态 */}
      <Card title="服务状态" loading={loading}>
        <Row gutter={16}>
          <Col span={6}>
            <Card>
              <Statistic
                title="API服务"
                value={healthOk ? '运行中' : '异常'}
                prefix={healthOk ? <CheckCircleOutlined /> : <CloseCircleOutlined />}
                valueStyle={{ color: healthOk ? '#52c41a' : '#ff4d4f' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="数据库"
                value={healthOk ? '已连接' : '异常'}
                prefix={<DatabaseOutlined />}
                valueStyle={{ color: healthOk ? '#52c41a' : '#ff4d4f' }}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="定额库数量"
                value={provinces.length}
                prefix={<DatabaseOutlined />}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card>
              <Statistic
                title="任务队列"
                value={healthOk ? '就绪' : '异常'}
                prefix={<CloudOutlined />}
                valueStyle={{ color: healthOk ? '#52c41a' : '#ff4d4f' }}
              />
            </Card>
          </Col>
        </Row>
      </Card>

      {/* 注册邀请码 */}
      <Card title="注册邀请码" loading={loading}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <div style={{ color: '#666', fontSize: 13 }}>
            新用户注册时必须填写正确的邀请码，防止未授权人员注册白嫖额度。修改后立即生效。
          </div>
          <Space>
            <Input
              value={inviteCodeInput}
              onChange={(e) => setInviteCodeInput(e.target.value)}
              style={{ width: 280 }}
              placeholder="输入新邀请码"
              maxLength={50}
            />
            <Button
              type="primary"
              loading={inviteSaving}
              disabled={inviteCodeInput === inviteCode}
              onClick={saveInviteCode}
            >
              保存
            </Button>
          </Space>
          <div style={{ fontSize: 13 }}>
            当前邀请码：<Tag color="blue">{inviteCode}</Tag>
            <span style={{ color: '#999', marginLeft: 8 }}>（告诉需要注册的人）</span>
          </div>
        </Space>
      </Card>

      {/* 匹配模型配置 */}
      <Card title="匹配模型（Agent）" loading={loading}>
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="负责清单→定额的智能匹配。修改后无需重启，下次新建任务自动使用新配置。"
        />

        {llmConfig && (
          <div style={{ marginBottom: 16, padding: '8px 12px', background: '#fafafa', borderRadius: 6 }}>
            当前模型：<Tag color="blue">{llmConfig.llm_type}</Tag>
            <Tag color="green">{llmConfig.model}</Tag>
            {llmConfig.has_api_key
              ? <Tag color="success">API Key 已配置（{llmConfig.api_key_masked}）</Tag>
              : <Tag color="error">API Key 未配置</Tag>
            }
          </div>
        )}

        <Form form={llmForm} layout="vertical" style={{ maxWidth: 500 }}>
          <Form.Item name="llm_type" label="选择模型" rules={[{ required: true, message: '请选择模型' }]}>
            <Select options={MODEL_OPTIONS} onChange={(v) => onTypeChange(v, llmForm, MODEL_OPTIONS)} />
          </Form.Item>
          <Form.Item name="api_key" label="API Key"
            help={llmConfig?.has_api_key ? '已配置，留空则保持不变；填入新值则覆盖' : '请填入API Key'}>
            <Input.Password placeholder={llmConfig?.has_api_key ? '留空保持不变' : '填入API Key'} autoComplete="off" />
          </Form.Item>
          <Form.Item name="base_url" label="API 地址" help="一般不需要改，用默认值即可">
            <Input placeholder="留空用默认地址" />
          </Form.Item>
          <Form.Item name="model" label="模型名称" help="一般不需要改，用默认值即可">
            <Input placeholder="留空用默认模型" />
          </Form.Item>
          <Form.Item>
            <Button type="primary" icon={<SaveOutlined />} loading={llmSaving} onClick={saveLlmConfig}>
              保存配置
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {/* 验证模型配置 */}
      <Card title="验证模型（审核纠偏）" loading={loading}>
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="负责检查匹配结果是否正确，发现错误自动纠正。可以和匹配用同一个模型，也可以单独配一个。"
        />

        {verifyConfig && (
          <div style={{ marginBottom: 16, padding: '8px 12px', background: '#fafafa', borderRadius: 6 }}>
            {verifyConfig.llm_type
              ? <>
                  当前模型：<Tag color="orange">{verifyConfig.llm_type}</Tag>
                  <Tag color="green">{verifyConfig.model}</Tag>
                  {verifyConfig.has_api_key
                    ? <Tag color="success">API Key 已配置（{verifyConfig.api_key_masked}）</Tag>
                    : <Tag color="error">API Key 未配置</Tag>
                  }
                </>
              : <span style={{ color: '#999' }}>跟匹配模型走（未单独配置）</span>
            }
          </div>
        )}

        <Form form={verifyForm} layout="vertical" style={{ maxWidth: 500 }}>
          <Form.Item name="llm_type" label="选择模型">
            <Select
              options={VERIFY_MODEL_OPTIONS}
              onChange={(v) => onTypeChange(v, verifyForm, VERIFY_MODEL_OPTIONS)}
            />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prev, cur) => prev.llm_type !== cur.llm_type}
          >
            {({ getFieldValue }) => {
              const vType = getFieldValue('llm_type');
              if (!vType) return null; // "跟匹配模型走"时不显示后续字段
              return (
                <>
                  <Form.Item name="api_key" label="API Key"
                    help={verifyConfig?.has_api_key ? '已配置，留空则保持不变' : '请填入API Key'}>
                    <Input.Password placeholder={verifyConfig?.has_api_key ? '留空保持不变' : '填入API Key'} autoComplete="off" />
                  </Form.Item>
                  <Form.Item name="base_url" label="API 地址" help="一般不需要改">
                    <Input placeholder="留空用默认地址" />
                  </Form.Item>
                  <Form.Item name="model" label="模型名称" help="一般不需要改">
                    <Input placeholder="留空用默认模型" />
                  </Form.Item>
                </>
              );
            }}
          </Form.Item>
          <Form.Item>
            <Button type="primary" icon={<SaveOutlined />} loading={verifySaving} onClick={saveVerifyConfig}>
              保存配置
            </Button>
          </Form.Item>
        </Form>
      </Card>

      {/* 系统参数 */}
      <Card title="匹配参数">
        <Descriptions bordered column={2} size="small">
          <Descriptions.Item label="高置信度阈值">
            <Badge status="success" text="≥ 85 分" />
          </Descriptions.Item>
          <Descriptions.Item label="中置信度阈值">
            <Badge status="warning" text="70 - 84 分" />
          </Descriptions.Item>
          <Descriptions.Item label="低置信度阈值">
            <Badge status="error" text="< 70 分" />
          </Descriptions.Item>
          <Descriptions.Item label="经验库直通阈值">
            <Badge status="processing" text="权威层精确匹配" />
          </Descriptions.Item>
          <Descriptions.Item label="默认匹配模式">
            <Tag color="blue">Agent模式（agent）</Tag>
          </Descriptions.Item>
          <Descriptions.Item label="默认经验库">
            <Tag color="green">启用</Tag>
          </Descriptions.Item>
        </Descriptions>
      </Card>
    </Space>
  );
}
