/**
 * 匹配结果页 — Excel 广联达风格
 *
 * 清单行和定额行交替展示，和导出的 Excel 效果一致：
 * - 清单行：序号 + 项目编码 + 项目名称 + 项目特征 + 单位 + 数量 + 推荐度 + 匹配说明
 * - 定额行：序号空 + 定额编号 + 定额名称 + 空 + 单位 + 空
 *
 * 管理员：清单行可确认/纠正，定额行可删除；支持批量确认
 * 普通用户：只读视图 + 下载Excel
 */

import { useEffect, useState, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Card, Table, Tag, Button, Space, Typography, App, Tooltip, Pagination,
} from 'antd';
import {
  ArrowLeftOutlined,
  DownloadOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  DeleteOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { useAuthStore } from '../../stores/auth';
import type {
  MatchResult, ResultListResponse, TaskInfo, ReviewStatus, QuotaItem,
} from '../../types';

// ============================================================
// 置信度工具函数
// ============================================================

const GREEN_THRESHOLD = 85;
const YELLOW_THRESHOLD = 60;

/** 推荐度单元格背景色（只给推荐度列用，不是整行） */
function getConfidenceBgColor(confidence: number, hasQuotas: boolean): string {
  if (!hasQuotas) return 'transparent';
  if (confidence >= GREEN_THRESHOLD) return '#C8E6C9';  // 绿色（比行背景深一些）
  if (confidence >= YELLOW_THRESHOLD) return '#FFE082';
  return '#EF9A9A';
}

/** 清单行背景色（按置信度，整行着淡色） */
function getBillRowBgColor(confidence: number, hasQuotas: boolean): string {
  if (!hasQuotas) return '#F5F5F5';
  if (confidence >= GREEN_THRESHOLD) return '#E8F5E9';
  if (confidence >= YELLOW_THRESHOLD) return '#FFF8E1';
  return '#FFEBEE';
}

function getConfidenceTextColor(confidence: number): string {
  if (confidence >= GREEN_THRESHOLD) return '#2e7d32';
  if (confidence >= YELLOW_THRESHOLD) return '#e65100';
  return '#c62828';
}

function confidenceToStars(confidence: number, hasQuotas: boolean): string {
  if (!hasQuotas) return '—';
  if (confidence >= GREEN_THRESHOLD) return `★★★推荐(${confidence}%)`;
  if (confidence >= YELLOW_THRESHOLD) return `★★参考(${confidence}%)`;
  return `★待审(${confidence}%)`;
}

const REVIEW_MAP: Record<ReviewStatus, { color: string; text: string }> = {
  pending: { color: 'default', text: '待审核' },
  confirmed: { color: 'success', text: '已确认' },
  corrected: { color: 'processing', text: '已纠正' },
};

// ============================================================
// 展示行类型（分部标题行 + 清单行 + 定额行混合扁平数组）
// ============================================================

interface SectionDisplayRow {
  _rowType: 'section';
  _rowKey: string;
  _title: string;              // 分部标题文字（如"【给排水】给水工程"）
}

interface BillDisplayRow {
  _rowType: 'bill';
  _rowKey: string;
  _result: MatchResult;        // 原始数据引用（操作时需要）
  _quotaCount: number;
}

interface QuotaDisplayRow {
  _rowType: 'quota';
  _rowKey: string;
  _parentResult: MatchResult;  // 所属清单的原始数据
  _quotaIndex: number;         // 在定额列表中的索引
  _quota: QuotaItem;           // 定额数据
}

type DisplayRow = SectionDisplayRow | BillDisplayRow | QuotaDisplayRow;

/** 将 MatchResult[] 展平为 DisplayRow[]（分部标题行+清单行+定额子行） */
function flattenResults(results: MatchResult[]): DisplayRow[] {
  const rows: DisplayRow[] = [];
  let currentSheet = '';
  let currentSection = '';

  for (const r of results) {
    // 检查是否需要插入分部标题行（sheet或section变化时）
    const sheet = r.sheet_name || '';
    const section = r.section || '';
    if (sheet && (sheet !== currentSheet || section !== currentSection)) {
      // 组合标题：有section就显示"【Sheet名】分部名"，没有就只显示Sheet名
      const title = section ? `【${sheet}】${section}` : sheet;
      rows.push({
        _rowType: 'section',
        _rowKey: `section_${rows.length}`,
        _title: title,
      });
      currentSheet = sheet;
      currentSection = section;
    } else if (!sheet && section && section !== currentSection) {
      // 没有sheet但有section变化
      rows.push({
        _rowType: 'section',
        _rowKey: `section_${rows.length}`,
        _title: section,
      });
      currentSection = section;
    }

    const quotas = r.corrected_quotas || r.quotas || [];
    // 清单行
    rows.push({
      _rowType: 'bill',
      _rowKey: r.id,
      _result: r,
      _quotaCount: quotas.length,
    });
    // 定额子行
    quotas.forEach((q, idx) => {
      rows.push({
        _rowType: 'quota',
        _rowKey: `${r.id}_q${idx}`,
        _parentResult: r,
        _quotaIndex: idx,
        _quota: q,
      });
    });
  }
  return rows;
}

// ============================================================
// 页面组件
// ============================================================

export default function ResultsPage() {
  const { taskId } = useParams<{ taskId: string }>();
  const navigate = useNavigate();
  const { message, modal } = App.useApp();
  const { user } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;

  const [loading, setLoading] = useState(false);
  const [task, setTask] = useState<TaskInfo | null>(null);
  const [results, setResults] = useState<MatchResult[]>([]);
  const [summary, setSummary] = useState({
    total: 0, high_confidence: 0, mid_confidence: 0, low_confidence: 0, no_match: 0,
  });
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([]);
  const [confirmLoading, setConfirmLoading] = useState(false);

  // 分页状态（以清单项为单位）
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);

  const loadData = useCallback(async () => {
    if (!taskId) return;
    setLoading(true);
    try {
      const [taskRes, resultsRes] = await Promise.all([
        api.get<TaskInfo>(`/tasks/${taskId}`),
        api.get<ResultListResponse>(`/tasks/${taskId}/results`),
      ]);
      setTask(taskRes.data);
      setResults(resultsRes.data.items);
      setSummary(resultsRes.data.summary);
    } catch {
      message.error('加载匹配结果失败');
    } finally {
      setLoading(false);
    }
  }, [taskId, message]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // 分页 + 展平
  const pagedResults = useMemo(() => {
    const start = (page - 1) * pageSize;
    return results.slice(start, start + pageSize);
  }, [results, page, pageSize]);

  const displayRows = useMemo(() => flattenResults(pagedResults), [pagedResults]);

  // ============================================================
  // 管理员操作
  // ============================================================

  /** 确认单条清单结果 */
  const confirmSingle = async (resultId: string) => {
    try {
      await api.post(`/tasks/${taskId}/results/confirm`, { result_ids: [resultId] });
      message.success('确认成功');
      loadData();
    } catch {
      message.error('确认失败');
    }
  };

  /** 批量确认选中的结果 */
  const confirmSelected = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择要确认的结果');
      return;
    }
    setConfirmLoading(true);
    try {
      const { data } = await api.post(`/tasks/${taskId}/results/confirm`, {
        result_ids: selectedRowKeys,
      });
      message.success(`成功确认 ${data.confirmed} 条结果`);
      setSelectedRowKeys([]);
      loadData();
    } catch {
      message.error('确认失败');
    } finally {
      setConfirmLoading(false);
    }
  };

  /** 一键确认所有高置信度 */
  const confirmAllHigh = async () => {
    const highConfIds = results
      .filter((r) => r.confidence >= GREEN_THRESHOLD && r.review_status === 'pending')
      .map((r) => r.id);
    if (highConfIds.length === 0) {
      message.info('没有待确认的高置信度结果');
      return;
    }
    setConfirmLoading(true);
    try {
      const { data } = await api.post(`/tasks/${taskId}/results/confirm`, {
        result_ids: highConfIds,
      });
      message.success(`一键确认 ${data.confirmed} 条高置信度结果`);
      setSelectedRowKeys([]);
      loadData();
    } catch {
      message.error('确认失败');
    } finally {
      setConfirmLoading(false);
    }
  };

  /** 删除单条定额（通过纠正 API 实现） */
  const removeQuota = (row: QuotaDisplayRow) => {
    const result = row._parentResult;
    const quotas = result.corrected_quotas || result.quotas || [];
    if (quotas.length <= 1) {
      message.warning('至少保留一条定额，不能全部删除');
      return;
    }
    modal.confirm({
      title: '确认删除',
      content: `确定要从该清单项中删除定额 ${row._quota.quota_id} 吗？`,
      okText: '删除',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        const newQuotas = quotas.filter((_, idx) => idx !== row._quotaIndex);
        try {
          await api.put(`/tasks/${taskId}/results/${result.id}`, {
            corrected_quotas: newQuotas,
            review_note: `删除定额 ${row._quota.quota_id}`,
          });
          message.success(`已删除定额 ${row._quota.quota_id}`);
          loadData();
        } catch {
          message.error('删除失败');
        }
      },
    });
  };

  /** 下载Excel */
  const downloadExcel = async () => {
    try {
      const response = await api.get(`/tasks/${taskId}/export`, { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([response.data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', `${task?.name || 'result'}_定额匹配结果.xlsx`);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      message.error('下载失败');
    }
  };

  // ============================================================
  // 列定义 — Excel 广联达风格
  // ============================================================

  const columns = [
    // 序号列：清单行显示数字，定额行空，分部标题行跨全列显示标题
    {
      title: '序号',
      key: 'serial',
      width: 42,
      align: 'center' as const,
      onCell: (row: DisplayRow) => {
        if (row._rowType === 'section') {
          return { colSpan: 20 };  // 跨所有列（数字大于实际列数即可）
        }
        return {};
      },
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') {
          return (
            <span style={{ fontWeight: 'bold', fontSize: 13, color: '#333' }}>
              {row._title}
            </span>
          );
        }
        if (row._rowType === 'bill') return <b>{row._result.index + 1}</b>;
        return null;
      },
    },
    // 项目编码 / 定额编号
    {
      title: '项目编码',
      key: 'code',
      width: 130,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') {
          const code = row._result.bill_code;
          return code ? (
            <span style={{ fontSize: 12 }}>{code}</span>
          ) : (
            <span style={{ color: '#ccc' }}>-</span>
          );
        }
        // 定额行：蓝色Tag显示定额编号
        return <Tag color="blue" style={{ margin: 0 }}>{row._quota.quota_id}</Tag>;
      },
    },
    // 项目名称 / 定额名称
    {
      title: '项目名称',
      key: 'name',
      ellipsis: true,
      width: 180,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') {
          return <span style={{ fontWeight: 500 }}>{row._result.bill_name}</span>;
        }
        return (
          <Tooltip title={row._quota.name}>
            <span style={{ fontSize: 13, color: '#555', paddingLeft: 8 }}>
              {row._quota.name}
            </span>
          </Tooltip>
        );
      },
    },
    // 项目特征（只在清单行显示，按编号拆行展示）
    {
      title: '项目特征',
      key: 'description',
      width: 260,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'bill') return null;
        const desc = row._result.bill_description;
        if (!desc) return <span style={{ color: '#ccc' }}>-</span>;

        // 按换行或编号前缀拆分成多行
        let lines = desc.split(/[\r\n]+/).map((s: string) => s.trim()).filter(Boolean);
        // 如果原文没换行但有多个编号（如"1.名称:xx 2.规格:yy"），按编号拆
        if (lines.length <= 1 && /\d+[.、．]/.test(desc)) {
          lines = desc.split(/(?=\d+[.、．])/).map((s: string) => s.trim()).filter(Boolean);
        }

        // 过滤废话行（详见图纸、其他：详见、空值字段等）
        const filtered = lines.filter((line: string) => {
          const clean = line.replace(/^\d+[.、．]\s*/, '');
          if (!clean.trim()) return false;
          if (/详见图纸|详见设计|按图施工|按规范/.test(clean)) return false;
          if (/^其他[：:]\s*(详见|见|按|\/|无|—|-)\s*/.test(clean)) return false;
          return true;
        });

        if (filtered.length === 0) return <span style={{ color: '#ccc' }}>-</span>;

        return (
          <div style={{ fontSize: 12, lineHeight: '1.6', whiteSpace: 'pre-wrap' }}>
            {filtered.map((line: string, idx: number) => (
              <div key={idx}>{line}</div>
            ))}
          </div>
        );
      },
    },
    // 单位
    {
      title: '单位',
      key: 'unit',
      width: 55,
      align: 'center' as const,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') return row._result.bill_unit || '-';
        return row._quota.unit || '';
      },
    },
    // 工程量
    {
      title: '工程量',
      key: 'quantity',
      width: 80,
      align: 'right' as const,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'bill') return null;
        return row._result.bill_quantity != null ? row._result.bill_quantity : '-';
      },
    },
    // 推荐度（只在清单行显示，单元格着色）
    {
      title: '推荐度',
      key: 'stars',
      width: 140,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'bill') return null;
        const r = row._result;
        const quotas = r.corrected_quotas || r.quotas || [];
        const hasQuotas = quotas.length > 0;
        const stars = confidenceToStars(r.confidence, hasQuotas);
        const textColor = hasQuotas ? getConfidenceTextColor(r.confidence) : '#999';
        const bgColor = getConfidenceBgColor(r.confidence, hasQuotas);
        return (
          <span style={{
            color: textColor,
            fontWeight: 'bold',
            fontSize: 13,
            whiteSpace: 'nowrap',
            backgroundColor: bgColor,
            padding: '2px 6px',
            borderRadius: 3,
          }}>
            {stars}
          </span>
        );
      },
    },
    // 匹配说明（只在清单行显示，自动换行）
    {
      title: '匹配说明',
      key: 'explanation',
      width: 220,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'bill') return null;
        const text = row._result.explanation;
        return text ? (
          <div style={{ fontSize: 12, color: '#666', whiteSpace: 'pre-wrap', lineHeight: '1.5' }}>
            {text}
          </div>
        ) : <span style={{ color: '#ccc' }}>-</span>;
      },
    },
    // 管理员审核操作列
    ...(isAdmin ? [{
      title: '审核',
      key: 'review',
      width: 120,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') {
          const status = row._result.review_status;
          const info = REVIEW_MAP[status] || { color: 'default', text: status };
          return (
            <Space size={2}>
              <Tag color={info.color} style={{ margin: 0 }}>{info.text}</Tag>
              {status === 'pending' && (
                <Button
                  type="link"
                  size="small"
                  icon={<CheckOutlined />}
                  onClick={(e) => { e.stopPropagation(); confirmSingle(row._result.id); }}
                  style={{ padding: 0 }}
                />
              )}
            </Space>
          );
        }
        // 定额行：删除按钮
        if (row._rowType === 'quota') {
          return (
            <Tooltip title="删除此条定额">
              <Button
                type="link"
                size="small"
                danger
                icon={<DeleteOutlined />}
                onClick={(e) => { e.stopPropagation(); removeQuota(row); }}
                style={{ padding: 0 }}
              />
            </Tooltip>
          );
        }
        return null;
      },
    }] : []),
  ];

  // ============================================================
  // 统计摘要
  // ============================================================

  const renderSummary = () => {
    const { total, high_confidence, mid_confidence, low_confidence, no_match } = summary;
    return (
      <Space size="middle" wrap style={{ fontSize: 13 }}>
        <span>共 <b>{total}</b> 条</span>
        <span style={{ color: '#2e7d32' }}>★★★高 <b>{high_confidence}</b></span>
        <span style={{ color: '#e65100' }}>★★中 <b>{mid_confidence}</b></span>
        <span style={{ color: '#c62828' }}>★低 <b>{low_confidence}</b></span>
        {no_match > 0 && <span style={{ color: '#999' }}>未匹配 <b>{no_match}</b></span>}
        {total > 0 && (
          <span style={{ color: '#999' }}>
            准确率 <b>{Math.round((high_confidence ?? 0) / total * 100)}%</b>
          </span>
        )}
      </Space>
    );
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {/* 顶部操作栏 */}
      <Card size="small">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/tasks')}>
              返回
            </Button>
            <Typography.Title level={5} style={{ margin: 0 }}>
              {task?.name || '匹配结果'}
            </Typography.Title>
            {task && <Tag>{task.province}</Tag>}
            {isAdmin && task && (
              <Tag color={task.mode === 'agent' ? 'purple' : 'blue'}>
                {task.mode === 'agent' ? 'Agent' : '搜索'}
              </Tag>
            )}
          </Space>
          <Space>
            {isAdmin && (
              <>
                <Button
                  icon={<CheckOutlined />}
                  onClick={confirmAllHigh}
                  loading={confirmLoading}
                  size="small"
                >
                  一键确认高置信度
                </Button>
                {selectedRowKeys.length > 0 && (
                  <Button
                    type="primary"
                    icon={<CheckCircleOutlined />}
                    onClick={confirmSelected}
                    loading={confirmLoading}
                    size="small"
                  >
                    确认选中({selectedRowKeys.length})
                  </Button>
                )}
              </>
            )}
            <Button type="primary" icon={<DownloadOutlined />} onClick={downloadExcel} size="small">
              下载Excel
            </Button>
          </Space>
        </div>
      </Card>

      {/* 结果表格（Excel 广联达风格） */}
      <Card size="small" title={renderSummary()}>
        <Table
          rowKey="_rowKey"
          dataSource={displayRows}
          columns={columns}
          loading={loading}
          size="small"
          pagination={false}  // 手动分页
          // 行勾选：只在清单行显示（管理员）
          rowSelection={isAdmin ? {
            selectedRowKeys,
            onChange: (keys) => setSelectedRowKeys(keys as string[]),
            getCheckboxProps: (row: DisplayRow) => ({
              disabled: row._rowType !== 'bill',
              style: row._rowType !== 'bill' ? { display: 'none' } : {},
            }),
            // eslint-disable-next-line @typescript-eslint/no-unused-vars
            renderCell: (_1: unknown, record: DisplayRow, _2: unknown, originNode: React.ReactNode) => {
              if (record._rowType !== 'bill') return null;
              return originNode;
            },
          } : undefined}
          // 行样式区分：分部标题行深灰粗体，清单行按置信度着色，定额行浅灰
          onRow={(row: DisplayRow) => {
            if (row._rowType === 'section') {
              return {
                style: {
                  backgroundColor: '#E0E0E0',
                  fontWeight: 'bold' as const,
                },
              };
            }
            if (row._rowType === 'bill') {
              const r = row._result;
              const quotas = r.corrected_quotas || r.quotas || [];
              return {
                style: {
                  backgroundColor: getBillRowBgColor(r.confidence, quotas.length > 0),
                  fontWeight: 500,
                },
              };
            }
            return {
              style: {
                backgroundColor: '#FAFAFA',
                fontSize: 13,
              },
            };
          }}
          locale={{ emptyText: '暂无匹配结果' }}
          scroll={{ x: 1200 }}
        />

        {/* 手动分页（以清单项数量计） */}
        {results.length > 0 && (
          <div style={{ textAlign: 'right', marginTop: 12 }}>
            <Pagination
              current={page}
              pageSize={pageSize}
              total={results.length}
              showSizeChanger
              showTotal={(total) => `共 ${total} 条清单`}
              pageSizeOptions={['20', '50', '100']}
              onChange={(p, ps) => { setPage(p); setPageSize(ps); }}
            />
          </div>
        )}
      </Card>
    </Space>
  );
}
