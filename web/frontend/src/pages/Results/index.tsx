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
import { useParams, useNavigate, useSearchParams } from 'react-router-dom';
import {
  Alert, Card, Table, Tag, Button, Space, Typography, App, Tooltip, Pagination,
} from 'antd';
import {
  ArrowLeftOutlined,
  DownloadOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  DeleteOutlined,
  RightOutlined,
  DownOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons';
import api from '../../services/api';
import { useAuthStore } from '../../stores/auth';
import {
  COLORS, GREEN_THRESHOLD,
  resolveLightStatus,
  confidenceToStars,
} from '../../utils/experience';
import { getErrorMessage } from '../../utils/error';
import type {
  MatchResult, ResultListResponse, TaskInfo, ReviewStatus, QuotaItem,
} from '../../types';

function getResultLightStatus(result: MatchResult): 'green' | 'yellow' | 'red' {
  if (result.review_status === 'corrected' && (result.corrected_quotas?.length || 0) > 0) {
    return 'green';
  }
  return resolveLightStatus(result);
}

function getResultConfidenceTextColor(result: MatchResult): string {
  const colorMap = {
    green: COLORS.greenText,
    yellow: COLORS.yellowText,
    red: COLORS.redText,
  };
  return colorMap[getResultLightStatus(result)];
}

function getResultConfidenceText(result: MatchResult, hasQuotas: boolean): string {
  if (!hasQuotas) return confidenceToStars(result, false);
  if (result.review_status === 'corrected') return '已纠正';
  return confidenceToStars(result, true);
}

// 包一层兼容 hasQuotas 参数
function getBillRowBgColor(result: MatchResult, hasQuotas: boolean): string {
  if (!hasQuotas) return '#F5F5F5';
  const colorMap = {
    green: COLORS.greenBg,
    yellow: COLORS.yellowBg,
    red: COLORS.redBg,
  };
  return colorMap[getResultLightStatus(result)];
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
  _title: string;              // 分部标题文字（如"给水工程"）
  _sectionLevel: 'specialty' | 'section';  // 专业级（如"消防电工"）or 分部级（如"预留预埋"）
  _parentSpecialtyKey?: string;  // 分部级section的所属专业key（折叠时用）
}

interface BillDisplayRow {
  _rowType: 'bill';
  _rowKey: string;
  _result: MatchResult;        // 原始数据引用（操作时需要）
  _quotaCount: number;
  _parentSpecialtyKey: string;  // 所属专业的key（折叠用）
  _parentSectionKey: string;    // 所属分部的key（折叠用）
}

interface QuotaDisplayRow {
  _rowType: 'quota';
  _rowKey: string;
  _parentResult: MatchResult;  // 所属清单的原始数据
  _quotaIndex: number;         // 在定额列表中的索引
  _quota: QuotaItem;           // 定额数据
  _parentSpecialtyKey: string;  // 所属专业的key（折叠用）
  _parentSectionKey: string;    // 所属分部的key（折叠用）
}

type DisplayRow = SectionDisplayRow | BillDisplayRow | QuotaDisplayRow;

/** 清理Sheet名称：从冗长的Excel Sheet名中提取有意义的部分
 *  例如 "表-08+分部分项工程和单价措施项目清单与计价表【消防电工】" → "消防电工"
 */
function cleanSheetName(sheet: string): string {
  if (!sheet) return sheet;
  // 提取最后一对【】中的内容（通常是专业名称，如"消防电工"、"给排水"）
  const brackets = sheet.match(/【([^【】]+)】/g);
  if (brackets && brackets.length > 0) {
    const last = brackets[brackets.length - 1];
    return last.replace(/[【】]/g, '');
  }
  return sheet;
}

/** 判断文本是否像定额编号（如"C10-2-123"、"SC20"、"C4-13-6 换"）
 *  定额编号不应作为分部标题显示，需要过滤掉
 */
function isQuotaCode(text: string): boolean {
  if (!text || !text.trim()) return false;
  const trimmed = text.trim();
  // 定额编号格式：1-4个字母 + 数字(可含连字符) + 可选空格+"换/增/减"后缀
  return /^[A-Za-z]{1,4}\d[\d-]*(\s*(换|增|减))?$/.test(trimmed);
}

/** 将 MatchResult[] 展平为 DisplayRow[]（分部标题行+清单行+定额子行）
 *  - 过滤掉像定额编号的分部名（如"C10-2-123 换"、"SC20"）
 *  - 记录每行所属的专业/分部key（用于折叠功能）
 */
function flattenResults(results: MatchResult[]): DisplayRow[] {
  const rows: DisplayRow[] = [];
  let currentSheet = '';
  let currentSection = '';
  let currentSpecialtyKey = '';   // 当前专业section的_rowKey
  let currentSectionKey = '';     // 当前分部section的_rowKey

  for (const r of results) {
    const sheet = r.sheet_name || '';
    const section = r.section || '';

    // 专业变化（Sheet变化）时，插入专业标题行
    if (sheet && sheet !== currentSheet) {
      const specialty = cleanSheetName(sheet);
      const key = `sheet_${rows.length}`;
      rows.push({
        _rowType: 'section',
        _rowKey: key,
        _title: `━━ ${specialty} ━━`,
        _sectionLevel: 'specialty',
      });
      currentSheet = sheet;
      currentSection = '';       // 专业变了，分部也要重新显示
      currentSpecialtyKey = key;
      currentSectionKey = '';    // 清空当前分部
    }

    // 分部变化时，插入分部标题行
    // 跳过看起来像定额编号的分部名（如"C10-2-123 换"、"SC20"）
    if (section && section !== currentSection) {
      if (!isQuotaCode(section)) {
        const key = `section_${rows.length}`;
        rows.push({
          _rowType: 'section',
          _rowKey: key,
          _title: section,
          _sectionLevel: 'section',
          _parentSpecialtyKey: currentSpecialtyKey,
        });
        currentSectionKey = key;
      }
      currentSection = section;
    }

    const quotas = r.corrected_quotas || r.quotas || [];
    // 清单行
    rows.push({
      _rowType: 'bill',
      _rowKey: r.id,
      _result: r,
      _quotaCount: quotas.length,
      _parentSpecialtyKey: currentSpecialtyKey,
      _parentSectionKey: currentSectionKey,
    });
    // 定额子行
    quotas.forEach((q, idx) => {
      rows.push({
        _rowType: 'quota',
        _rowKey: `${r.id}_q${idx}`,
        _parentResult: r,
        _quotaIndex: idx,
        _quota: q,
        _parentSpecialtyKey: currentSpecialtyKey,
        _parentSectionKey: currentSectionKey,
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
  const [searchParams] = useSearchParams();
  const { message, modal } = App.useApp();
  const { user } = useAuthStore();
  const isAdmin = user?.is_admin ?? false;
  const targetResultId = searchParams.get('result_id') || '';
  const stagingContext = useMemo(() => {
    if (searchParams.get('source') !== 'knowledge-staging') return null;
    return {
      sourceLabel: searchParams.get('source_label') || '知识晋升候选',
      candidateTitle: searchParams.get('candidate_title') || '',
      candidateType: searchParams.get('candidate_type') || '',
      errorType: searchParams.get('error_type') || '',
      returnTo: searchParams.get('return_to') || '/admin?tab=staging',
    };
  }, [searchParams]);

  const [loading, setLoading] = useState(false);
  const [task, setTask] = useState<TaskInfo | null>(null);
  const [results, setResults] = useState<MatchResult[]>([]);
  const [summary, setSummary] = useState({
    total: 0, high_confidence: 0, mid_confidence: 0, low_confidence: 0, no_match: 0,
    confirmed: 0, corrected: 0, pending: 0,
  });
  const [selectedRowKeys, setSelectedRowKeys] = useState<string[]>([]);
  const [confirmLoading, setConfirmLoading] = useState(false);

  // 红灯行展开候选定额的状态（key = result.id）
  const [expandedAlts, setExpandedAlts] = useState<Set<string>>(new Set());

  // 置信度筛选（all=全部, green=高置信度, yellow=中置信度, red=低置信度）
  const [confFilter, setConfFilter] = useState<'all' | 'need_review' | 'green' | 'yellow' | 'red'>(
    isAdmin ? 'need_review' : 'all',
  );

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
    } catch (err: unknown) {
      message.error(getErrorMessage(err, '加载匹配结果失败'));
    } finally {
      setLoading(false);
    }
  }, [taskId, message]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    if (targetResultId) return;
    setConfFilter(isAdmin ? 'need_review' : 'all');
  }, [taskId, isAdmin, targetResultId]);

  useEffect(() => {
    if (!targetResultId || results.length === 0) return;
    if (confFilter !== 'all') {
      setConfFilter('all');
      return;
    }
    const targetIndex = results.findIndex((item) => item.id === targetResultId);
    if (targetIndex < 0) return;
    const targetPage = Math.floor(targetIndex / pageSize) + 1;
    if (page !== targetPage) {
      setPage(targetPage);
    }
  }, [targetResultId, results, confFilter, pageSize, page]);

  // 置信度筛选 → 分页 → 展平
  const filteredResults = useMemo(() => {
    let next = results;
    if (confFilter !== 'all') {
      next = next.filter((r) => {
        const light = getResultLightStatus(r);
        if (confFilter === 'need_review') return r.review_status === 'pending' && (light === 'yellow' || light === 'red');
        if (confFilter === 'green') return light === 'green';
        if (confFilter === 'yellow') return light === 'yellow';
        return light === 'red';
      });
    }
    return next;
  }, [results, confFilter]);
  const reviewFocusCount = useMemo(
    () => results.filter((r) => {
      const light = getResultLightStatus(r);
      return r.review_status === 'pending' && (light === 'yellow' || light === 'red');
    }).length,
    [results],
  );
  const pagedResults = useMemo(() => {
    const start = (page - 1) * pageSize;
    return filteredResults.slice(start, start + pageSize);
  }, [filteredResults, page, pageSize]);

  const displayRows = useMemo(() => flattenResults(pagedResults), [pagedResults]);

  // 分部折叠/展开状态（key = 分部section行的_rowKey，只管分部级别）
  const [collapsedSections, setCollapsedSections] = useState<Set<string>>(new Set());

  /** 点击单个分部小节：折叠/展开该小节的清单 */
  const toggleSection = useCallback((sectionKey: string) => {
    setCollapsedSections(prev => {
      const next = new Set(prev);
      if (next.has(sectionKey)) {
        next.delete(sectionKey);
      } else {
        next.add(sectionKey);
      }
      return next;
    });
  }, []);

  /** 点击专业标题：批量折叠/展开它下面所有分部小节 */
  const toggleSpecialtySections = useCallback((specialtyKey: string) => {
    // 找出该专业下所有分部小节的key
    const childKeys = displayRows
      .filter(r => r._rowType === 'section' && r._sectionLevel === 'section'
                   && r._parentSpecialtyKey === specialtyKey)
      .map(r => r._rowKey);
    if (childKeys.length === 0) return;

    setCollapsedSections(prev => {
      const next = new Set(prev);
      // 如果所有子节都已折叠 → 全部展开；否则 → 全部折叠
      const allCollapsed = childKeys.every(k => next.has(k));
      if (allCollapsed) {
        childKeys.forEach(k => next.delete(k));
      } else {
        childKeys.forEach(k => next.add(k));
      }
      return next;
    });
  }, [displayRows]);

  /** 一键折叠所有分部（只看专业+分部名称，清单全部收起） */
  const collapseAll = useCallback(() => {
    const allSectionKeys = displayRows
      .filter(r => r._rowType === 'section' && r._sectionLevel === 'section')
      .map(r => r._rowKey);
    setCollapsedSections(new Set(allSectionKeys));
  }, [displayRows]);

  /** 一键展开所有分部 */
  const expandAll = useCallback(() => {
    setCollapsedSections(new Set());
  }, []);

  // 根据折叠状态过滤可见行
  // 专业标题和分部标题始终可见，只有清单/定额行会被折叠隐藏
  const visibleRows = useMemo(() => {
    const result: DisplayRow[] = [];
    for (const row of displayRows) {
      if (row._rowType === 'section') {
        // 标题行（专业和分部）始终显示
        result.push(row);
      } else {
        // 清单/定额行：所属分部未折叠时才显示
        const secCollapsed = row._parentSectionKey && collapsedSections.has(row._parentSectionKey);
        if (!secCollapsed) {
          result.push(row);
        }
      }
    }
    return result;
  }, [displayRows, collapsedSections]);

  useEffect(() => {
    if (!targetResultId || visibleRows.length === 0) return;
    const targetVisible = visibleRows.some(
      (row) => row._rowType === 'bill' && row._result.id === targetResultId,
    );
    if (!targetVisible) return;
    const timer = window.setTimeout(() => {
      const el = document.getElementById(`result-row-${targetResultId}`);
      el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }, 120);
    return () => window.clearTimeout(timer);
  }, [targetResultId, visibleRows]);

  // 每个分部小节包含的清单条数（显示在标题后面）
  const sectionBillCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const row of displayRows) {
      if (row._rowType === 'bill' && row._parentSectionKey) {
        counts[row._parentSectionKey] = (counts[row._parentSectionKey] || 0) + 1;
      }
    }
    return counts;
  }, [displayRows]);

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
      .filter((r) => resolveLightStatus(r) === 'green' && r.review_status === 'pending')
      .map((r) => r.id);
    if (highConfIds.length === 0) {
      message.info('没有待确认的高置信度结果');
      return;
    }
    // 二次确认弹窗（批量操作，防止误触）
    modal.confirm({
      title: '一键确认高置信度',
      content: `将批量确认 ${highConfIds.length} 条高置信度（≥${GREEN_THRESHOLD}%）结果，确定继续？`,
      okText: `确认 ${highConfIds.length} 条`,
      cancelText: '取消',
      onOk: async () => {
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
      },
    });
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
      const response = await api.get(`/tasks/${taskId}/export-final?materials=true`, { responseType: 'blob' });
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
          // 跨所有列 + 强制左对齐（序号列默认居中，标题行需要覆盖）
          return { colSpan: 20, style: { textAlign: 'left' as const, paddingLeft: 12 } };
        }
        return {};
      },
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') {
          const isSpecialty = row._sectionLevel === 'specialty';
          // 专业标题：看子节是否全部折叠；分部标题：看自己是否折叠
          let isCollapsed: boolean;
          if (isSpecialty) {
            const childKeys = displayRows
              .filter(r => r._rowType === 'section' && r._sectionLevel === 'section'
                           && r._parentSpecialtyKey === row._rowKey)
              .map(r => r._rowKey);
            isCollapsed = childKeys.length > 0 && childKeys.every(k => collapsedSections.has(k));
          } else {
            isCollapsed = collapsedSections.has(row._rowKey);
          }
          const count = !isSpecialty ? sectionBillCounts[row._rowKey] || 0 : 0;
          return (
            <span style={{
              fontWeight: 'bold',
              fontSize: isSpecialty ? 14 : 13,
              color: isSpecialty ? '#fff' : '#1565C0',
              userSelect: 'none',
            }}>
              {isCollapsed
                ? <RightOutlined style={{ fontSize: 10, marginRight: 6 }} />
                : <DownOutlined style={{ fontSize: 10, marginRight: 6 }} />}
              {row._title}
              {!isSpecialty && count > 0 && (
                <span style={{ fontSize: 11, fontWeight: 'normal', opacity: 0.55, marginLeft: 8 }}>
                  {count}条
                </span>
              )}
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
    // 项目名称 / 定额名称（定额行允许换行显示完整名称）
    {
      title: '项目名称',
      key: 'name',
      width: 200,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') {
          return <span style={{ fontWeight: 500 }}>{row._result.bill_name}</span>;
        }
        // 定额行：完整显示名称，允许换行
        return (
          <span style={{ fontSize: 13, color: '#555', paddingLeft: 8, whiteSpace: 'normal', wordBreak: 'break-all' }}>
            {row._quota.name}
          </span>
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
            <div style={{ fontSize: 12, lineHeight: '1.6' }}>
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
        const stars = getResultConfidenceText(r, hasQuotas);
        const textColor = hasQuotas ? getResultConfidenceTextColor(r) : '#999';
        // 星级标签用更醒目的颜色：绿底/黄底/红底
        const starBgMap: Record<string, string> = {
          green: '#b7eb8f',   // 亮绿
          yellow: '#ffe58f',  // 亮黄
          red: '#ffa39e',     // 亮红
        };
        const level = !hasQuotas ? 'none' : getResultLightStatus(r);
        const bgColor = level === 'none' ? 'transparent' : starBgMap[level];
        return (
          <span style={{
            color: textColor,
            fontWeight: 600,
            fontSize: 12,
            whiteSpace: 'nowrap',
            backgroundColor: bgColor,
            padding: '3px 10px',
            borderRadius: 12,
            display: 'inline-block',
          }}>
            {stars}
          </span>
        );
      },
    },
    // 匹配来源标签（只在清单行显示）
    {
      title: '来源',
      key: 'match_source',
      width: 70,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'bill') return null;
        const source = row._result.match_source;
        if (!source) return null;
        // 来源标签映射（原始字段名 → 中文胶囊）
        const sourceMap: Record<string, { color: string; text: string }> = {
          experience: { color: 'gold', text: '经验库' },
          experience_candidate: { color: 'orange', text: '候选' },
          experience_similar_confirmed: { color: 'orange', text: '⚠️经验库' },
          experience_similar: { color: 'orange', text: '⚠️经验库' },
          search: { color: 'default', text: '搜索' },
          rule: { color: 'blue', text: '规则' },
          llm: { color: 'purple', text: 'AI' },
          llm_corrected: { color: 'volcano', text: 'AI纠正' },
          manual: { color: 'cyan', text: '人工' },
        };
        const info = sourceMap[source] || { color: 'default', text: source };
        return <Tag color={info.color} style={{ margin: 0, fontSize: 11 }}>{info.text}</Tag>;
      },
    },
    // 匹配说明（只在清单行显示，自动换行；AI纠正/存疑结果高亮）
    // 低置信度行可展开Top3候选定额
    {
      title: '匹配说明',
      key: 'explanation',
      width: 220,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType !== 'bill') return null;
        const r = row._result;
        const text = r.explanation;
        const matchSource = r.match_source;

        // 匹配说明内容
        let explanationNode: React.ReactNode;
        if (matchSource === 'llm_corrected' || (text && text.startsWith('[AI纠正]'))) {
          const detail = text ? text.replace(/^\[AI纠正\]\s*/, '') : '';
          explanationNode = (
            <div style={{ fontSize: 12, lineHeight: '1.5' }}>
              <Tag color="orange" style={{ marginBottom: 4 }}>AI纠正</Tag>
              <div style={{ color: '#666', whiteSpace: 'pre-wrap' }}>{detail}</div>
            </div>
          );
        } else if (text && text.startsWith('[AI存疑]')) {
          const detail = text.replace(/^\[AI存疑\]\s*/, '');
          explanationNode = (
            <div style={{ fontSize: 12, lineHeight: '1.5' }}>
              <Tag color="red" style={{ marginBottom: 4 }}>AI存疑</Tag>
              <div style={{ color: '#666', whiteSpace: 'pre-wrap' }}>{detail}</div>
            </div>
          );
        } else {
          explanationNode = text ? (
            <Tooltip title={text} placement="topLeft" overlayStyle={{ maxWidth: 400 }}>
              <div style={{
                fontSize: 12, color: '#666', whiteSpace: 'pre-wrap', lineHeight: '1.5',
                maxHeight: 60, overflow: 'hidden',
              }}>
                {text}
              </div>
            </Tooltip>
          ) : <span style={{ color: '#ccc' }}>-</span>;
        }

        // 低置信度行：展开Top3候选
        const alts = r.alternatives as { quota_id: string; name: string; unit: string; confidence: number; reason?: string }[] | null;
        const isLow = r.review_status === 'pending' && getResultLightStatus(r) === 'red';
        const isExpanded = expandedAlts.has(r.id);
        const hasAlts = alts && alts.length > 0;

        return (
          <div>
            {explanationNode}
            {/* 低置信度且有候选时，显示展开按钮 */}
            {isAdmin && isLow && hasAlts && (
              <div style={{ marginTop: 4 }}>
                <Button
                  type="link"
                  size="small"
                  style={{ padding: 0, fontSize: 12, color: COLORS.redSolid }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setExpandedAlts(prev => {
                      const next = new Set(prev);
                      if (next.has(r.id)) next.delete(r.id); else next.add(r.id);
                      return next;
                    });
                  }}
                >
                  {isExpanded ? '收起候选' : `查看${alts!.length}个候选 ▼`}
                </Button>
                {isExpanded && (
                  <div style={{
                    marginTop: 6, padding: 8, background: '#fef2f2',
                    borderRadius: 6, border: '1px solid #fecaca',
                  }}>
                    {alts!.map((alt, idx) => (
                      <div key={idx} style={{
                        display: 'flex', alignItems: 'center', gap: 8,
                        padding: '4px 0',
                        borderBottom: idx < alts!.length - 1 ? '1px dashed #fecaca' : 'none',
                      }}>
                        <Tag color="blue" style={{ margin: 0, flexShrink: 0 }}>{alt.quota_id}</Tag>
                        <span style={{ flex: 1, fontSize: 12 }}>{alt.name}</span>
                        <span style={{ fontSize: 11, color: '#999', flexShrink: 0 }}>{alt.unit}</span>
                        <Button
                          type="primary"
                          size="small"
                          style={{ fontSize: 11, height: 22 }}
                          onClick={(e) => {
                            e.stopPropagation();
                            // 纠正为该候选定额
                            (async () => {
                              try {
                                await api.put(`/tasks/${taskId}/results/${r.id}`, {
                                  corrected_quotas: [{
                                    quota_id: alt.quota_id,
                                    name: alt.name,
                                    unit: alt.unit,
                                    source: 'manual_correction',
                                  }],
                                  review_note: `从候选中选择: ${alt.quota_id}`,
                                });
                                message.success(`已纠正为 ${alt.quota_id}`);
                                setExpandedAlts(prev => { const n = new Set(prev); n.delete(r.id); return n; });
                                loadData();
                              } catch {
                                message.error('纠正失败');
                              }
                            })();
                          }}
                        >
                          选用
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      },
    },
    // 管理员审核操作列
    ...(isAdmin ? [{
      title: '审核',
      key: 'review',
      width: 240,
      onCell: (row: DisplayRow) => row._rowType === 'section' ? { colSpan: 0 } : {},
      render: (_: unknown, row: DisplayRow) => {
        if (row._rowType === 'section') return null;
        if (row._rowType === 'bill') {
          const status = row._result.review_status;
          const info = REVIEW_MAP[status] || { color: 'default', text: status };
          return (
            <Space size={2} direction="vertical">
              <Space size={2} wrap>
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
    const { total, confirmed = 0, corrected = 0, no_match } = summary;
    const pill = (bg: string, color: string): React.CSSProperties => ({
      padding: '2px 12px',
      borderRadius: 12,
      backgroundColor: bg,
      color,
      fontSize: 13,
      fontWeight: 500,
      display: 'inline-block',
    });
    // 确认率 = (已确认 + 已纠正) / 总数
    const reviewed = confirmed + corrected;
    const confirmRate = total > 0
      ? Math.round((reviewed / total) * 100)
      : 0;
    return (
      <Space size="small" wrap>
        <span style={pill('#f0f0f0', '#333')}>共 <b>{total}</b> 条</span>
        {no_match > 0 && <span style={pill('#f5f5f5', '#999')}>未匹配 <b>{no_match}</b></span>}
        {total > 0 && (
          <span style={pill('#E3F2FD', '#1565C0')}>
            已审核 <b>{reviewed}</b>/{total}（{confirmRate}%）
          </span>
        )}
        {reviewed > 0 && (
          <span style={pill('#E8F5E9', '#2E7D32')}>
            已审核 <b>{reviewed}</b>/{total}
            {corrected > 0 && <span>（纠正{corrected}条）</span>}
          </span>
        )}
      </Space>
    );
  };

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      {stagingContext && (
        <Alert
          type="info"
          showIcon
          message="当前结果来自知识晋升审核链路"
          description={(
            <Space direction="vertical" size={8} style={{ width: '100%' }}>
              <Space wrap>
                <Typography.Text>来源：{stagingContext.sourceLabel}</Typography.Text>
                {stagingContext.candidateTitle ? (
                  <Typography.Text>候选标题：{stagingContext.candidateTitle}</Typography.Text>
                ) : null}
                {stagingContext.candidateType ? (
                  <Typography.Text>候选类型：{stagingContext.candidateType}</Typography.Text>
                ) : null}
                {stagingContext.errorType ? (
                  <Typography.Text>来源错因：{stagingContext.errorType}</Typography.Text>
                ) : null}
              </Space>
              <Space wrap>
                <Button size="small" onClick={() => navigate(stagingContext.returnTo)}>
                  回到 staging 候选页
                </Button>
              </Space>
            </Space>
          )}
        />
      )}

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
                {/* 按颜色全选 */}
                <Button
                  size="small"
                  style={{ color: COLORS.greenSolid, borderColor: COLORS.greenSolid }}
                  onClick={() => {
                    const ids = results.filter(r => getResultLightStatus(r) === 'green' && r.review_status === 'pending').map(r => r.id);
                    setSelectedRowKeys(ids);
                  }}
                >
                  选绿灯({summary.high_confidence})
                </Button>
                <Button
                  size="small"
                  style={{ color: COLORS.yellowSolid, borderColor: COLORS.yellowSolid }}
                  onClick={() => {
                    const ids = results.filter(r => getResultLightStatus(r) === 'yellow' && r.review_status === 'pending').map(r => r.id);
                    setSelectedRowKeys(ids);
                  }}
                >
                  选黄灯({summary.mid_confidence})
                </Button>
                <Button
                  size="small"
                  style={{ color: COLORS.redSolid, borderColor: COLORS.redSolid }}
                  onClick={() => {
                    const ids = results.filter(r => getResultLightStatus(r) === 'red' && r.review_status === 'pending').map(r => r.id);
                    setSelectedRowKeys(ids);
                  }}
                >
                  选红灯({summary.low_confidence})
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
        {task?.status === 'failed' && task.error_message && (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            message="任务执行失败"
            description={task.error_message}
          />
        )}
        {/* 置信度快捷筛选 */}
        <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
          {([
            ...(isAdmin ? [{ key: 'need_review', label: '先看要核对的项', color: '#d97706', count: reviewFocusCount }] : []),
            { key: 'all', label: '全部', color: undefined, count: results.length },
            { key: 'green', label: '高置信度', color: COLORS.greenSolid, count: summary.high_confidence },
            { key: 'yellow', label: '中置信度', color: COLORS.yellowSolid, count: summary.mid_confidence },
            { key: 'red', label: '低置信度', color: COLORS.redSolid, count: summary.low_confidence },
          ] as const).map(({ key, label, color, count }) => (
            <Button
              key={key}
              size="small"
              type={confFilter === key ? 'primary' : 'default'}
              style={{
                borderColor: confFilter === key ? undefined : color,
                color: confFilter === key ? undefined : color,
              }}
              onClick={() => { setConfFilter(key as 'all' | 'need_review' | 'green' | 'yellow' | 'red'); setPage(1); }}
            >
              {label} {count > 0 && `(${count})`}
            </Button>
          ))}
        </div>
      </Card>

      {/* 结果表格（Excel 广联达风格） */}
      <Card
        size="small"
        title={renderSummary()}
        extra={
          <Space size="small">
            <Button size="small" icon={<MenuFoldOutlined />} onClick={collapseAll}>
              全部折叠
            </Button>
            <Button size="small" icon={<MenuUnfoldOutlined />} onClick={expandAll}>
              全部展开
            </Button>
          </Space>
        }
      >
        {/* 表格视觉增强 */}
        <style>{`
          /* td 继承 tr 背景色（Ant Design 默认白色会覆盖） */
          .result-table .ant-table-tbody > tr > td {
            background: inherit !important;
            transition: filter 0.15s ease;
            border-bottom: 1px solid #f0f0f0;
          }
          /* 表格圆角 + 外边框 */
          .result-table .ant-table {
            border-radius: 8px;
            overflow: hidden;
            border: 1px solid #e8e8e8;
          }
          /* 表头加粗 + 底部双线 */
          .result-table .ant-table-thead > tr > th {
            background: #fafafa !important;
            font-weight: 600 !important;
            font-size: 13px;
            border-bottom: 2px solid #d9d9d9 !important;
          }
          /* 清单行悬停变暗一点 */
          .result-table .ant-table-tbody > tr.bill-row:hover > td {
            filter: brightness(0.96);
          }
          /* 分部标题行悬停 */
          .result-table .ant-table-tbody > tr.section-row:hover > td {
            filter: brightness(0.92);
          }
          /* 定额行左侧蓝色竖线 — 视觉上关联到上方清单行 */
          .result-table .ant-table-tbody > tr.quota-row > td:nth-child(1),
          .result-table .ant-table-tbody > tr.quota-row > td:nth-child(2) {
            border-left: 3px solid #91caff;
          }
          /* 定额行悬停 */
          .result-table .ant-table-tbody > tr.quota-row:hover > td {
            filter: brightness(0.97);
          }
          /* 专业标题行取消底部边框（和下面的分部标题视觉连贯） */
          .result-table .ant-table-tbody > tr.specialty-row > td {
            border-bottom: none;
          }
        `}</style>
        <Table
          className="result-table"
          rowKey="_rowKey"
          dataSource={visibleRows}
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
              // 专业标题：深蓝底白字；分部标题：浅蓝底深色字
              const isSpecialty = row._sectionLevel === 'specialty';
              return {
                className: isSpecialty ? 'section-row specialty-row' : 'section-row',
                style: {
                  backgroundColor: isSpecialty ? '#1565C0' : '#BBDEFB',
                  fontWeight: 'bold' as const,
                  cursor: 'pointer',
                },
                onClick: () => isSpecialty
                  ? toggleSpecialtySections(row._rowKey)   // 专业标题：批量切子节
                  : toggleSection(row._rowKey),             // 分部标题：切自己
              };
            }
            if (row._rowType === 'bill') {
              const r = row._result;
              const quotas = r.corrected_quotas || r.quotas || [];
              return {
                className: r.id === targetResultId ? 'bill-row target-result-row' : 'bill-row',
                id: `result-row-${r.id}`,
                style: {
                  backgroundColor: getBillRowBgColor(r, quotas.length > 0),
                  fontWeight: 500,
                  outline: r.id === targetResultId ? '2px solid #1677ff' : undefined,
                  outlineOffset: r.id === targetResultId ? '-2px' : undefined,
                },
              };
            }
            return {
              className: 'quota-row',
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
        {filteredResults.length > 0 && (
          <div style={{ textAlign: 'right', marginTop: 12 }}>
            <Pagination
              current={page}
              pageSize={pageSize}
              total={filteredResults.length}
              showSizeChanger
              showTotal={(total) => `共 ${total} 条清单${confFilter !== 'all' ? '（已筛选）' : ''}`}
              pageSizeOptions={['20', '50', '100']}
              onChange={(p, ps) => { setPage(p); setPageSize(ps); setSelectedRowKeys([]); }}
            />
          </div>
        )}
      </Card>

      {/* 固定底部操作栏（文档04章 P2） */}
      {isAdmin && results.length > 0 && (
        <div style={{
          position: 'sticky',
          bottom: 0,
          zIndex: 10,
          background: '#fff',
          borderTop: '1px solid #e8e8e8',
          padding: '10px 20px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          boxShadow: '0 -2px 8px rgba(0,0,0,0.06)',
          borderRadius: '0 0 8px 8px',
        }}>
          <span style={{ fontSize: 13, color: '#666' }}>
            已选 <b>{selectedRowKeys.length}</b> 条
          </span>
          <Space>
            <Button icon={<DownloadOutlined />} onClick={downloadExcel} size="small">
              导出Excel
            </Button>
            {selectedRowKeys.length > 0 && (
              <Button
                type="primary"
                icon={<CheckCircleOutlined />}
                onClick={confirmSelected}
                loading={confirmLoading}
                size="small"
              >
                批量确认({selectedRowKeys.length})
              </Button>
            )}
            <Button
              type="primary"
              style={{ background: '#ea580c', borderColor: '#ea580c' }}
              size="small"
              onClick={() => navigate('/tools/material-price')}
            >
              继续填主材 →
            </Button>
          </Space>
        </div>
      )}
    </Space>
  );
}
