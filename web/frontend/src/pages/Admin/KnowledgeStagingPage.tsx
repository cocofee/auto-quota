import { useEffect, useRef, useState } from 'react';
import {
  App,
  Button,
  Card,
  Col,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  CheckOutlined,
  EyeOutlined,
  LinkOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  StopOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

interface StagingHealth {
  ok: boolean;
  schema_version: string;
  missing_tables: string[];
  missing_views: string[];
}

interface CountMap {
  [key: string]: number;
}

interface RejectionReasonStat {
  reason: string;
  count: number;
}

interface StagingStats {
  audit_total: number;
  promotion_total: number;
  promotion_status_counts: CountMap;
  promotion_target_counts: CountMap;
  promotion_candidate_counts: CountMap;
  audit_review_counts: CountMap;
  audit_match_source_counts: CountMap;
  audit_error_type_counts: CountMap;
  promotion_reviewed_total: number;
  promotion_approved_total: number;
  promotion_rejected_total: number;
  promotion_approval_rate: number;
  promotion_rejection_rate: number;
  promotion_execution_rate: number;
  top_rejection_reasons: RejectionReasonStat[];
}

interface HealthSummary {
  duplicate_candidate_groups: number;
  stale_pending_promotions: number;
  rolled_back_promotions: number;
  source_conflict_groups: number;
  inactive_formal_total: number;
  inactive_rules: number;
  inactive_method_cards: number;
  experience_candidate_count: number;
  experience_disputed_count: number;
  stale_pending_days: number;
}

interface StalePendingPromotion {
  id: number;
  source_table: string;
  source_record_id: string;
  candidate_type: string;
  target_layer: string;
  candidate_title: string;
  status: string;
  review_status: string;
  created_at: number;
  age_days: number;
}

interface RollbackRecord {
  id: number;
  source_table: string;
  source_record_id: string;
  candidate_type: string;
  target_layer: string;
  candidate_title: string;
  reviewed_at?: number;
  reviewer?: string;
  review_comment?: string;
  promoted_target_ref?: string;
}

interface FormalLayerHealth {
  inactive_rules: number;
  inactive_method_cards: number;
  inactive_formal_total: number;
  experience_candidate_count: number;
  experience_disputed_count: number;
}

interface StagingHealthReport {
  summary: HealthSummary;
  stale_pending_promotions: StalePendingPromotion[];
  recent_rollbacks: RollbackRecord[];
  formal_layer_health: FormalLayerHealth;
}

interface KnowledgeImpactSummary {
  tracked_runs: number;
  tracked_results: number;
  last_7d_runs: number;
  last_7d_results: number;
  last_7d_hits: number;
  last_7d_direct: number;
}

interface KnowledgeImpactLayerMetric {
  layer: string;
  hit_count: number;
  direct_count: number;
  assist_count: number;
  reviewed_count: number;
  confirmed_count: number;
  corrected_count: number;
  pending_count: number;
  hit_rate: number;
  review_coverage_rate: number;
  confirmed_rate: number;
  corrected_rate: number;
}

interface KnowledgeImpactObjectMetric {
  layer: string;
  object_ref: string;
  hit_count: number;
  direct_count: number;
  assist_count: number;
  reviewed_count: number;
  confirmed_count: number;
  corrected_count: number;
  pending_count: number;
  review_coverage_rate: number;
  confirmed_rate: number;
  corrected_rate: number;
}

interface KnowledgeImpactReport {
  summary: KnowledgeImpactSummary;
  layer_metrics: KnowledgeImpactLayerMetric[];
  top_objects: KnowledgeImpactObjectMetric[];
}

interface KnowledgeObjectDetailPromotion {
  id: number;
  source_table: string;
  source_record_id: string;
  target_layer: string;
  candidate_type: string;
  candidate_title: string;
  status: string;
  review_status: string;
  reviewer?: string;
  review_comment?: string;
  promoted_target_ref?: string;
  promotion_trace?: string;
}

interface KnowledgeObjectDetail {
  object_ref: string;
  promoted_target_ref?: string;
  formal_detail?: Record<string, unknown> | null;
  promotion_sources: KnowledgeObjectDetailPromotion[];
}

interface PromotionItem {
  id: number;
  source_table: string;
  source_record_id: string;
  candidate_type: string;
  target_layer: string;
  candidate_title: string;
  candidate_summary: string;
  candidate_payload: Record<string, unknown>;
  status: string;
  review_status: string;
  reviewer: string;
  review_comment?: string;
  rejection_reason?: string;
  priority: number;
  promoted_target_id?: string;
  promoted_target_ref?: string;
  promotion_trace?: string;
}

interface AuditErrorItem {
  id: number;
  source_type: string;
  evidence_ref: string;
  source_table: string;
  source_record_id: string;
  task_id: string;
  result_id: string;
  province: string;
  specialty: string;
  bill_name: string;
  bill_desc: string;
  predicted_quota_code: string;
  predicted_quota_name: string;
  corrected_quota_code: string;
  corrected_quota_name: string;
  match_source: string;
  error_type: string;
  error_level: string;
  root_cause: string;
  root_cause_tags: string[];
  fix_suggestion: string;
  decision_basis: string;
  review_status: string;
  reviewer: string;
  review_comment?: string;
  can_promote_rule?: number;
  can_promote_method?: number;
}

type PromotionStatusView = 'pending' | 'rejected' | 'promoted' | 'rolled_back' | 'all';

const STATUS_COLORS: Record<string, string> = {
  draft: 'default',
  reviewing: 'processing',
  approved: 'blue',
  rejected: 'red',
  promoted: 'green',
  rolled_back: 'orange',
  unreviewed: 'default',
};

const STATUS_LABELS: Record<string, string> = {
  draft: '待整理',
  reviewing: '审核中',
  approved: '已通过',
  rejected: '已驳回',
  promoted: '已晋升',
  rolled_back: '已回退',
  unreviewed: '未审核',
};

const ERROR_LEVEL_COLORS: Record<string, string> = {
  low: 'default',
  medium: 'orange',
  high: 'red',
  critical: 'red',
};

const ERROR_LEVEL_LABELS: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
  critical: '严重',
};

const TARGET_LAYER_LABELS: Record<string, string> = {
  RuleKnowledge: '规则知识',
  MethodCards: '方法卡',
  ExperienceDB: '正式经验库',
  UniversalKB: '通用知识',
};

const CANDIDATE_TYPE_LABELS: Record<string, string> = {
  rule: '规则',
  method: '方法',
  experience: '经验',
  universal: '通用知识',
};

const SOURCE_TABLE_LABELS: Record<string, string> = {
  audit_errors: '错因记录',
  match_results: '匹配结果',
  openclaw_manual_cards: 'OpenClaw 知识卡片',
};

const MATCH_SOURCE_LABELS: Record<string, string> = {
  search: '搜索',
  rule: '规则',
  agent: '智能体',
  experience: '经验',
  hybrid: '混合',
};

const ERROR_TYPE_LABELS: Record<string, string> = {
  wrong_rank: '排序错误',
  wrong_rule: '规则错误',
  polluted_experience: '经验污染',
  review_corrected: '审核修正',
};

const EXECUTABLE_TARGET_LAYERS = new Set(['RuleKnowledge', 'MethodCards', 'ExperienceDB']);
const ROLLBACK_TARGET_LAYERS = new Set(['RuleKnowledge', 'MethodCards', 'ExperienceDB']);
const DEFAULT_PROMOTION_STATUSES = 'draft,reviewing,approved';
const PROMOTED_STATUSES = 'promoted';
const ALL_PROMOTION_STATUSES = 'draft,reviewing,approved,rejected,promoted,rolled_back';

const PROMOTION_STATUS_VIEW_OPTIONS = [
  { label: '待处理', value: 'pending' },
  { label: '已驳回', value: 'rejected' },
  { label: '已晋升', value: 'promoted' },
  { label: '已回退', value: 'rolled_back' },
  { label: '全部', value: 'all' },
];

const PROMOTION_TARGET_OPTIONS = [
  { label: '全部目标层', value: 'all' },
  { label: '规则知识', value: 'RuleKnowledge' },
  { label: '方法卡', value: 'MethodCards' },
  { label: '正式经验库', value: 'ExperienceDB' },
];

const PROMOTION_TYPE_OPTIONS = [
  { label: '全部候选类型', value: 'all' },
  { label: '规则', value: 'rule' },
  { label: '方法', value: 'method' },
  { label: '经验', value: 'experience' },
  { label: '通用知识', value: 'universal' },
];

const SOURCE_TABLE_OPTIONS = [
  { label: '全部来源', value: 'all' },
  { label: '错因记录', value: 'audit_errors' },
  { label: '匹配结果', value: 'match_results' },
  { label: 'OpenClaw 知识卡片', value: 'openclaw_manual_cards' },
];

const AUDIT_MATCH_SOURCE_OPTIONS = [
  { label: '全部匹配来源', value: 'all' },
  { label: '搜索', value: 'search' },
  { label: '规则', value: 'rule' },
  { label: '智能体', value: 'agent' },
  { label: '经验', value: 'experience' },
];

const AUDIT_ERROR_TYPE_OPTIONS = [
  { label: '全部错因类型', value: 'all' },
  { label: '排序错误', value: 'wrong_rank' },
  { label: '规则错误', value: 'wrong_rule' },
  { label: '经验污染', value: 'polluted_experience' },
  { label: '审核修正', value: 'review_corrected' },
];

function statusViewToStatuses(view: PromotionStatusView): string {
  if (view === 'rejected') return 'rejected';
  if (view === 'promoted') return PROMOTED_STATUSES;
  if (view === 'rolled_back') return 'rolled_back';
  if (view === 'all') return ALL_PROMOTION_STATUSES;
  return DEFAULT_PROMOTION_STATUSES;
}

function getLabel(value: string | undefined, mapping: Record<string, string>) {
  const normalized = String(value || '').trim();
  return mapping[normalized] || normalized || '-';
}

function formatDateTime(ts?: number) {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString();
}

function formatPercent(value?: number) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function getAuditErrorIdFromSource(sourceTable?: string, sourceRecordId?: string) {
  if (sourceTable !== 'audit_errors') return null;
  const auditErrorId = Number(sourceRecordId);
  return Number.isFinite(auditErrorId) ? auditErrorId : null;
}

function renderStatusTag(status?: string) {
  const normalized = String(status || '').trim();
  return (
    <Tag color={STATUS_COLORS[normalized] || 'default'} style={{ marginRight: 0 }}>
      {getLabel(normalized, STATUS_LABELS)}
    </Tag>
  );
}

function buildPromotionEmptyDescription(view: PromotionStatusView) {
  if (view === 'rejected') return '今天暂时没有已驳回候选需要回看';
  if (view === 'promoted') return '今天暂时没有新晋升记录需要确认';
  if (view === 'rolled_back') return '目前没有回退或污染信号';
  if (view === 'all') return '当前筛选条件下没有候选记录';
  return '今天暂时没有待你确认的候选知识';
}

function buildAuditEmptyDescription() {
  return '当前没有发现需要你处理的错因';
}

function getPayloadValue(payload: Record<string, unknown> | undefined, keys: string[]) {
  if (!payload) return undefined;
  for (const key of keys) {
    const value = payload[key];
    if (value !== undefined && value !== null && value !== '') {
      return value;
    }
  }
  return undefined;
}

function toDisplayText(value: unknown) {
  if (value === undefined || value === null || value === '') return '-';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function toDisplayList(value: unknown) {
  if (Array.isArray(value)) {
    return value
      .map((item) => String(item ?? '').trim())
      .filter(Boolean);
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) return [];
    return trimmed
      .split(/[\n,，;；]+/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  if (value === undefined || value === null) return [];
  return [String(value)];
}

export default function KnowledgeStagingPage() {
  const { message } = App.useApp();
  const requestRef = useRef(0);

  const [primaryLoading, setPrimaryLoading] = useState(false);
  const [secondaryLoading, setSecondaryLoading] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);
  const [showHealthDetails, setShowHealthDetails] = useState(false);
  const [showKnowledgeImpact, setShowKnowledgeImpact] = useState(false);

  const [health, setHealth] = useState<StagingHealth | null>(null);
  const [stats, setStats] = useState<StagingStats | null>(null);
  const [healthReport, setHealthReport] = useState<StagingHealthReport | null>(null);
  const [knowledgeImpact, setKnowledgeImpact] = useState<KnowledgeImpactReport | null>(null);
  const [secondaryError, setSecondaryError] = useState('');

  const [items, setItems] = useState<PromotionItem[]>([]);
  const [auditErrors, setAuditErrors] = useState<AuditErrorItem[]>([]);
  const [selectedAuditError, setSelectedAuditError] = useState<AuditErrorItem | null>(null);
  const [auditDrawerOpen, setAuditDrawerOpen] = useState(false);
  const [selectedPromotion, setSelectedPromotion] = useState<PromotionItem | null>(null);
  const [promotionDrawerOpen, setPromotionDrawerOpen] = useState(false);
  const [knowledgeObjectDetail, setKnowledgeObjectDetail] = useState<KnowledgeObjectDetail | null>(null);
  const [knowledgeObjectDrawerOpen, setKnowledgeObjectDrawerOpen] = useState(false);
  const [knowledgeObjectLoading, setKnowledgeObjectLoading] = useState(false);

  const [promotionStatusView, setPromotionStatusView] = useState<PromotionStatusView>('pending');
  const [promotionTargetLayer, setPromotionTargetLayer] = useState<string>('all');
  const [promotionCandidateType, setPromotionCandidateType] = useState<string>('all');
  const [promotionSourceTable, setPromotionSourceTable] = useState<string>('all');
  const [auditMatchSource, setAuditMatchSource] = useState<string>('all');
  const [auditErrorType, setAuditErrorType] = useState<string>('all');
  const [auditSourceTable, setAuditSourceTable] = useState<string>('all');

  const openTaskResultPage = (
    taskId?: string,
    resultId?: string,
    context?: { sourceLabel?: string; candidateTitle?: string; candidateType?: string; errorType?: string },
  ) => {
    if (!taskId) {
      message.warning('这条记录没有关联任务上下文');
      return;
    }
    const params = new URLSearchParams();
    if (resultId) params.set('result_id', resultId);
    params.set('source', 'knowledge-staging');
    params.set('return_to', '/admin?tab=staging');
    if (context?.sourceLabel) params.set('source_label', context.sourceLabel);
    if (context?.candidateTitle) params.set('candidate_title', context.candidateTitle);
    if (context?.candidateType) params.set('candidate_type', context.candidateType);
    if (context?.errorType) params.set('error_type', context.errorType);
    window.open(`/tasks/${taskId}/results?${params.toString()}`, '_blank', 'noopener,noreferrer');
  };

  const loadPrimaryData = async (requestId: number) => {
    const { data: healthData } = await api.get<StagingHealth>('/admin/knowledge-staging/health');
    if (requestId !== requestRef.current) return;
    setHealth(healthData);

    const { data: promotionData } = await api.get<{ items: PromotionItem[]; total: number }>(
      '/admin/knowledge-staging/promotions',
      {
        params: {
          limit: 100,
          statuses: statusViewToStatuses(promotionStatusView),
          candidate_types: promotionCandidateType === 'all' ? '' : promotionCandidateType,
          target_layers: promotionTargetLayer === 'all' ? '' : promotionTargetLayer,
          source_table: promotionSourceTable === 'all' ? '' : promotionSourceTable,
        },
      },
    );
    if (requestId !== requestRef.current) return;
    setItems(promotionData.items || []);

    const { data: auditData } = await api.get<{ items: AuditErrorItem[]; total: number }>(
      '/admin/knowledge-staging/audit-errors',
      {
        params: {
          limit: 100,
          match_sources: auditMatchSource === 'all' ? '' : auditMatchSource,
          error_types: auditErrorType === 'all' ? '' : auditErrorType,
          source_table: auditSourceTable === 'all' ? '' : auditSourceTable,
        },
      },
    );
    if (requestId !== requestRef.current) return;
    setAuditErrors(auditData.items || []);

    const { data: statsData } = await api.get<StagingStats>('/admin/knowledge-staging/stats');
    if (requestId !== requestRef.current) return;
    setStats(statsData);
  };

  const loadSecondaryData = async (requestId: number) => {
    setSecondaryLoading(true);
    setSecondaryError('');
    try {
      const [{ data: healthReportData }, { data: knowledgeImpactData }] = await Promise.all([
        api.get<StagingHealthReport>('/admin/knowledge-staging/health-report'),
        api.get<KnowledgeImpactReport>('/admin/knowledge-staging/knowledge-impact', { params: { days: 7 } }),
      ]);
      if (requestId !== requestRef.current) return;
      setHealthReport(healthReportData);
      setKnowledgeImpact(knowledgeImpactData);
    } catch {
      if (requestId === requestRef.current) {
        setSecondaryError('深度分析暂时没有加载完成，不影响你先处理候选。');
      }
    } finally {
      if (requestId === requestRef.current) {
        setSecondaryLoading(false);
      }
    }
  };

  const loadData = async () => {
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    setPrimaryLoading(true);
    try {
      await loadPrimaryData(requestId);
      if (requestId === requestRef.current) {
        void loadSecondaryData(requestId);
      }
    } catch {
      if (requestId === requestRef.current) {
        message.error('加载知识晋升工作台失败');
      }
    } finally {
      if (requestId === requestRef.current) {
        setPrimaryLoading(false);
      }
    }
  };

  useEffect(() => {
    void loadData();
  }, [
    promotionStatusView,
    promotionTargetLayer,
    promotionCandidateType,
    promotionSourceTable,
    auditMatchSource,
    auditErrorType,
    auditSourceTable,
  ]);

  const openAuditErrorDetail = async (auditErrorId: number) => {
    setAuditLoading(true);
    setAuditDrawerOpen(true);
    try {
      const cached = auditErrors.find((item) => item.id === auditErrorId);
      if (cached?.root_cause) setSelectedAuditError(cached);
      const { data } = await api.get<AuditErrorItem>(`/admin/knowledge-staging/audit-errors/${auditErrorId}`);
      setSelectedAuditError(data);
      setAuditErrors((current) =>
        current.some((item) => item.id === data.id)
          ? current.map((item) => (item.id === data.id ? data : item))
          : [data, ...current],
      );
    } catch {
      message.error('加载错因详情失败');
      setAuditDrawerOpen(false);
    } finally {
      setAuditLoading(false);
    }
  };

  const fetchAuditErrorDetail = async (auditErrorId: number) => {
    const cached = auditErrors.find((item) => item.id === auditErrorId);
    if (cached?.root_cause) return cached;
    try {
      const { data } = await api.get<AuditErrorItem>(`/admin/knowledge-staging/audit-errors/${auditErrorId}`);
      return data;
    } catch {
      message.error('反查错因详情失败');
      return null;
    }
  };

  const openKnowledgeObjectDetail = async (objectRef: string) => {
    setKnowledgeObjectLoading(true);
    setKnowledgeObjectDrawerOpen(true);
    try {
      const { data } = await api.get<KnowledgeObjectDetail>(
        '/admin/knowledge-staging/knowledge-impact/object-detail',
        { params: { object_ref: objectRef } },
      );
      setKnowledgeObjectDetail(data);
    } catch {
      message.error('加载知识对象详情失败');
      setKnowledgeObjectDrawerOpen(false);
    } finally {
      setKnowledgeObjectLoading(false);
    }
  };

  const openTaskResultFromPromotionSource = async (record: KnowledgeObjectDetailPromotion) => {
    const auditErrorId = getAuditErrorIdFromSource(record.source_table, record.source_record_id);
    if (!auditErrorId) {
      message.warning('这条晋升来源没有关联错因记录');
      return;
    }
    const auditError = await fetchAuditErrorDetail(auditErrorId);
    if (!auditError?.task_id) {
      message.warning('关联错因没有任务上下文');
      return;
    }
    openTaskResultPage(auditError.task_id, auditError.result_id, {
      sourceLabel: '知识对象晋升来源',
      candidateTitle: record.candidate_title,
      candidateType: getLabel(record.candidate_type, CANDIDATE_TYPE_LABELS),
      errorType: getLabel(auditError.error_type, ERROR_TYPE_LABELS),
    });
  };

  const reviewPromotion = async (record: PromotionItem, reviewStatus: 'approved' | 'rejected') => {
    try {
      await api.put(`/admin/knowledge-staging/promotions/${record.id}/review`, {
        review_status: reviewStatus,
        review_comment: reviewStatus === 'approved' ? '管理员审核通过' : '管理员驳回候选',
        rejection_reason: reviewStatus === 'rejected' ? '管理员驳回候选' : '',
      });
      message.success(reviewStatus === 'approved' ? '候选已通过审核' : '候选已驳回');
      await loadData();
    } catch {
      message.error('更新候选审核状态失败');
    }
  };

  const executePromotion = async (record: PromotionItem) => {
    try {
      await api.post(`/admin/knowledge-staging/promotions/${record.id}/execute`, {
        expected_target_layer: record.target_layer,
      });
      message.success('已执行晋升');
      await loadData();
    } catch (error: any) {
      const detail = error?.response?.data?.detail;
      message.error(detail ? `执行晋升失败：${detail}` : '执行晋升失败');
    }
  };

  const rollbackPromotion = async (record: PromotionItem) => {
    try {
      await api.post(`/admin/knowledge-staging/promotions/${record.id}/rollback`, {
        reason: '管理员从晋升工作台回退',
      });
      message.success('已完成回退');
      await loadData();
    } catch {
      message.error('执行回退失败');
    }
  };

  const openPromotionDetail = (record: PromotionItem) => {
    setSelectedPromotion(record);
    setPromotionDrawerOpen(true);
  };

  const pendingCount = (stats?.promotion_status_counts.draft || 0) + (stats?.promotion_status_counts.reviewing || 0);
  const approvedCount = stats?.promotion_status_counts.approved || 0;
  const rolledBackCount = stats?.promotion_status_counts.rolled_back || 0;
  const rejectedCount = stats?.promotion_status_counts.rejected || 0;
  const shouldPrioritizeQueue = pendingCount > 0;
  const selectedPromotionPayload = selectedPromotion?.candidate_payload || {};
  const selectedPromotionOriginalProblem = getPayloadValue(selectedPromotionPayload, ['original_problem', 'originalProblem', 'question', 'problem', 'raw_problem']);
  const selectedPromotionConclusion = getPayloadValue(selectedPromotionPayload, ['final_conclusion', 'finalConclusion', 'conclusion', 'answer', 'decision']);
  const selectedPromotionJudgmentBasis = getPayloadValue(selectedPromotionPayload, ['judgment_basis', 'judgmentBasis', 'basis', 'rationale', 'decision_basis']);
  const selectedPromotionExclusionReasons = toDisplayList(
    getPayloadValue(selectedPromotionPayload, ['exclusion_reasons', 'exclusionReasons', 'excluded_reasons', 'rejected_reasons', 'exclude_reasons']),
  );
  const selectedPromotionKnowledgePoints = toDisplayList(
    getPayloadValue(selectedPromotionPayload, ['core_knowledge_points', 'coreKnowledgePoints', 'knowledge_points', 'key_points', 'points']),
  );
  const selectedPromotionSuggestedType = getPayloadValue(selectedPromotionPayload, ['suggested_promotion_type', 'suggestedPromotionType', 'promotion_type', 'suggested_type']);
  const selectedPromotionTags = toDisplayList(getPayloadValue(selectedPromotionPayload, ['tags', 'labels']));
  const selectedPromotionSource = getPayloadValue(selectedPromotionPayload, ['source', 'source_name', 'source_label']);
  const selectedPromotionCardId = getPayloadValue(selectedPromotionPayload, ['card_id', 'cardId', 'id']);

  const promotionColumns: ColumnsType<PromotionItem> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 70 },
    {
      title: '候选内容',
      key: 'candidate',
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{record.candidate_title || '-'}</div>
          <Space wrap size={4} style={{ marginTop: 4 }}>
            <Tag>{getLabel(record.candidate_type, CANDIDATE_TYPE_LABELS)}</Tag>
            <Tag>{getLabel(record.target_layer, TARGET_LAYER_LABELS)}</Tag>
            <Tag>优先级 {record.priority}</Tag>
          </Space>
          {record.candidate_summary ? (
            <Typography.Text type="secondary" style={{ display: 'block', marginTop: 6, fontSize: 12 }}>
              {record.candidate_summary}
            </Typography.Text>
          ) : null}
        </div>
      ),
    },
    {
      title: '状态',
      key: 'status',
      width: 150,
      render: (_value, record) => (
        <Space direction="vertical" size={4}>
          {renderStatusTag(record.status)}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            审核：{getLabel(record.review_status, STATUS_LABELS)}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '审核结果',
      key: 'result',
      width: 260,
      render: (_value, record) => (
        <Space direction="vertical" size={4}>
          <Typography.Text style={{ fontSize: 12 }}>驳回原因：{record.rejection_reason || '-'}</Typography.Text>
          <Typography.Text style={{ fontSize: 12 }}>审核备注：{record.review_comment || '-'}</Typography.Text>
          <Typography.Text style={{ fontSize: 12 }}>晋升结果：{record.promoted_target_ref || record.promoted_target_id || '-'}</Typography.Text>
        </Space>
      ),
    },
    {
      title: '来源',
      key: 'source',
      width: 180,
      render: (_value, record) => (
        <div style={{ fontSize: 12 }}>
          <div>{getLabel(record.source_table, SOURCE_TABLE_LABELS)}</div>
          <div style={{ color: '#64748b' }}>{record.source_record_id || '-'}</div>
        </div>
      ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 340,
      render: (_value, record) => {
        const linkedAuditId = getAuditErrorIdFromSource(record.source_table, record.source_record_id);
        return (
          <Space wrap>
            <Button size="small" icon={<EyeOutlined />} onClick={() => openPromotionDetail(record)}>
              详情
            </Button>
            <Button size="small" icon={<EyeOutlined />} onClick={() => linkedAuditId ? void openAuditErrorDetail(linkedAuditId) : message.warning('这条候选没有关联错因记录')}>
              看依据
            </Button>
            <Button size="small" icon={<CheckOutlined />} disabled={record.status !== 'draft' && record.status !== 'reviewing'} onClick={() => reviewPromotion(record, 'approved')}>
              通过
            </Button>
            <Button size="small" icon={<StopOutlined />} disabled={record.status !== 'draft' && record.status !== 'reviewing'} onClick={() => reviewPromotion(record, 'rejected')}>
              驳回
            </Button>
            <Button size="small" type="primary" icon={<PlayCircleOutlined />} disabled={record.status !== 'approved' || !EXECUTABLE_TARGET_LAYERS.has(record.target_layer)} onClick={() => executePromotion(record)}>
              晋升入库
            </Button>
            <Button size="small" disabled={!(record.status === 'promoted' && ROLLBACK_TARGET_LAYERS.has(record.target_layer))} onClick={() => rollbackPromotion(record)}>
              回退
            </Button>
          </Space>
        );
      },
    },
  ];

  const auditErrorColumns: ColumnsType<AuditErrorItem> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 70 },
    {
      title: '清单与修正',
      key: 'bill',
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{record.bill_name || '-'}</div>
          {record.bill_desc ? (
            <Typography.Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
              {record.bill_desc}
            </Typography.Text>
          ) : null}
          <div style={{ marginTop: 6, fontSize: 12 }}>
            原匹配：{record.predicted_quota_name || '-'}{record.predicted_quota_code ? `（${record.predicted_quota_code}）` : ''}
          </div>
          <div style={{ fontSize: 12 }}>
            修正后：{record.corrected_quota_name || '-'}{record.corrected_quota_code ? `（${record.corrected_quota_code}）` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '错因信息',
      key: 'error',
      width: 240,
      render: (_value, record) => (
        <Space direction="vertical" size={4}>
          <Space wrap>
            <Tag color={ERROR_LEVEL_COLORS[record.error_level] || 'default'}>
              风险：{getLabel(record.error_level, ERROR_LEVEL_LABELS)}
            </Tag>
            <Tag>{getLabel(record.error_type, ERROR_TYPE_LABELS)}</Tag>
            <Tag>{getLabel(record.match_source, MATCH_SOURCE_LABELS)}</Tag>
          </Space>
          <Space wrap size={4}>
            {record.can_promote_rule ? <Tag color="blue">可产出规则</Tag> : null}
            {record.can_promote_method ? <Tag color="cyan">可产出方法</Tag> : null}
          </Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.root_cause || '-'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '审核状态',
      key: 'status',
      width: 150,
      render: (_value, record) => (
        <Space direction="vertical" size={4}>
          {renderStatusTag(record.review_status)}
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {record.reviewer || '-'}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: '审核备注',
      dataIndex: 'review_comment',
      key: 'review_comment',
      width: 220,
      render: (value?: string) => <div style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{value || '-'}</div>,
    },
    {
      title: '操作',
      key: 'actions',
      width: 210,
      render: (_value, record) => (
        <Space wrap>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openAuditErrorDetail(record.id)}>
            详情
          </Button>
          <Button size="small" icon={<LinkOutlined />} onClick={() => openTaskResultPage(record.task_id, record.result_id, { sourceLabel: '错因审核', errorType: getLabel(record.error_type, ERROR_TYPE_LABELS) })}>
            任务结果
          </Button>
        </Space>
      ),
    },
  ];

  const stalePendingColumns: ColumnsType<StalePendingPromotion> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 70 },
    {
      title: '候选',
      key: 'candidate',
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{record.candidate_title || '-'}</div>
          <Space wrap size={4} style={{ marginTop: 4 }}>
            <Tag>{getLabel(record.candidate_type, CANDIDATE_TYPE_LABELS)}</Tag>
            <Tag>{getLabel(record.target_layer, TARGET_LAYER_LABELS)}</Tag>
            {renderStatusTag(record.status)}
          </Space>
        </div>
      ),
    },
    { title: '滞留天数', dataIndex: 'age_days', key: 'age_days', width: 100, render: (value: number) => value.toFixed(1) },
  ];

  const rollbackColumns: ColumnsType<RollbackRecord> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 70 },
    {
      title: '回退对象',
      key: 'target',
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{record.candidate_title || '-'}</div>
          <Space wrap size={4} style={{ marginTop: 4 }}>
            <Tag>{getLabel(record.target_layer, TARGET_LAYER_LABELS)}</Tag>
            <Tag>{getLabel(record.candidate_type, CANDIDATE_TYPE_LABELS)}</Tag>
          </Space>
        </div>
      ),
    },
    { title: '正式层引用', dataIndex: 'promoted_target_ref', key: 'promoted_target_ref', width: 180, render: (value?: string) => value || '-' },
    { title: '回退时间', dataIndex: 'reviewed_at', key: 'reviewed_at', width: 180, render: (value?: number) => formatDateTime(value) },
  ];

  const knowledgeImpactLayerColumns: ColumnsType<KnowledgeImpactLayerMetric> = [
    { title: '知识层', dataIndex: 'layer', key: 'layer', width: 140, render: (value: string) => getLabel(value, TARGET_LAYER_LABELS) },
    { title: '命中数', dataIndex: 'hit_count', key: 'hit_count', width: 90 },
    { title: '直接命中', dataIndex: 'direct_count', key: 'direct_count', width: 90 },
    { title: '辅助命中', dataIndex: 'assist_count', key: 'assist_count', width: 90 },
    { title: '审核率', dataIndex: 'review_coverage_rate', key: 'review_coverage_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '确认率', dataIndex: 'confirmed_rate', key: 'confirmed_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '修正率', dataIndex: 'corrected_rate', key: 'corrected_rate', width: 90, render: (value: number) => formatPercent(value) },
  ];

  const knowledgeImpactObjectColumns: ColumnsType<KnowledgeImpactObjectMetric> = [
    { title: '知识层', dataIndex: 'layer', key: 'layer', width: 140, render: (value: string) => getLabel(value, TARGET_LAYER_LABELS) },
    { title: '对象引用', dataIndex: 'object_ref', key: 'object_ref', width: 220 },
    { title: '命中数', dataIndex: 'hit_count', key: 'hit_count', width: 90 },
    { title: '直接命中', dataIndex: 'direct_count', key: 'direct_count', width: 90 },
    { title: '审核率', dataIndex: 'review_coverage_rate', key: 'review_coverage_rate', width: 90, render: (value: number) => formatPercent(value) },
    {
      title: '操作',
      key: 'actions',
      width: 90,
      render: (_value, record) => (
        <Button size="small" icon={<EyeOutlined />} onClick={() => openKnowledgeObjectDetail(record.object_ref)}>
          打开
        </Button>
      ),
    },
  ];

  const queueCard = (
    <Card title={shouldPrioritizeQueue ? '今天先处理这些候选' : '候选晋升队列'}>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Space wrap>
          <Tag color="blue">先看依据，再决定通过或驳回</Tag>
          <Tag color="green">审核通过后，再执行正式入库</Tag>
          <Tag color="orange">如发现污染，可直接回退</Tag>
          {shouldPrioritizeQueue ? <Tag color="red">今天有 {pendingCount} 条候选待你确认</Tag> : null}
        </Space>
        <Row gutter={[12, 12]}>
          <Col xs={24} md={10}>
            <Segmented options={PROMOTION_STATUS_VIEW_OPTIONS} value={promotionStatusView} onChange={(value) => setPromotionStatusView(value as PromotionStatusView)} block />
          </Col>
          <Col xs={24} md={14}>
            <Space wrap>
              <Select style={{ minWidth: 180 }} value={promotionTargetLayer} options={PROMOTION_TARGET_OPTIONS} onChange={setPromotionTargetLayer} />
              <Select style={{ minWidth: 180 }} value={promotionCandidateType} options={PROMOTION_TYPE_OPTIONS} onChange={setPromotionCandidateType} />
              <Select style={{ minWidth: 180 }} value={promotionSourceTable} options={SOURCE_TABLE_OPTIONS} onChange={setPromotionSourceTable} />
            </Space>
          </Col>
        </Row>
        {items.length === 0 && !primaryLoading ? (
          <Empty description={buildPromotionEmptyDescription(promotionStatusView)} />
        ) : (
          <Table
            rowKey="id"
            dataSource={items}
            columns={promotionColumns}
            loading={primaryLoading}
            pagination={{ pageSize: 20, hideOnSinglePage: true }}
          />
        )}
      </Space>
    </Card>
  );

  const summaryCard = (
    <Card title="今日待办">
      {stats ? (
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Row gutter={[16, 16]}>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic title="待处理候选" value={pendingCount} />
                <Typography.Text type="secondary">先审批</Typography.Text>
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic title="已通过待晋升" value={approvedCount} />
                <Typography.Text type="secondary">可执行入库</Typography.Text>
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic title="已回退" value={rolledBackCount} />
                <Typography.Text type="secondary">复查污染</Typography.Text>
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card size="small">
                <Statistic title="已驳回候选" value={rejectedCount} />
                <Typography.Text type="secondary">看是否误杀</Typography.Text>
              </Card>
            </Col>
          </Row>
          <Typography.Text type="secondary">
            近 7 天新增、驳回原因和命中统计已经降到下层分析里，需要复盘时再展开看。
          </Typography.Text>
        </Space>
      ) : (
        <Typography.Text type="secondary">正在加载今日待办...</Typography.Text>
      )}
    </Card>
  );

  return (
    <>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Card
          title="候选知识晋升工作台"
          extra={(
            <Space>
              <Typography.Text type="secondary">
                {secondaryLoading ? '首屏已到位，正在补充深度分析' : '首屏优先加载'}
              </Typography.Text>
              <Button icon={<ReloadOutlined />} onClick={loadData} loading={primaryLoading || secondaryLoading}>
                刷新工作台
              </Button>
            </Space>
          )}
        >
          {health ? (
            <Space direction="vertical" size="small" style={{ width: '100%' }}>
              <Descriptions size="small" column={3}>
                <Descriptions.Item label="候选区状态">
                  <Tag color={health.ok ? 'green' : 'red'}>{health.ok ? '正常' : '异常'}</Tag>
                </Descriptions.Item>
                <Descriptions.Item label="Schema 版本">v{health.schema_version || '-'}</Descriptions.Item>
                <Descriptions.Item label="当前候选数">{items.length}</Descriptions.Item>
              </Descriptions>
              <Typography.Text type="secondary">
                这里不是正式知识库，而是待确认候选区。OpenClaw 或其他业务入口先把候选写进 staging，你在这里确认、驳回，确认通过后再晋升到规则知识、方法卡或正式经验库。
              </Typography.Text>
            </Space>
          ) : (
            <Typography.Text type="secondary">正在加载首屏状态...</Typography.Text>
          )}
        </Card>

        {shouldPrioritizeQueue ? queueCard : summaryCard}
        {shouldPrioritizeQueue ? summaryCard : queueCard}

        <Card title="候选送审依据">
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Typography.Text type="secondary">审批前先看这里，确认这些候选为什么会被送进来。</Typography.Text>
            <Space wrap>
              <Select style={{ minWidth: 180 }} value={auditMatchSource} options={AUDIT_MATCH_SOURCE_OPTIONS} onChange={setAuditMatchSource} />
              <Select style={{ minWidth: 200 }} value={auditErrorType} options={AUDIT_ERROR_TYPE_OPTIONS} onChange={setAuditErrorType} />
              <Select style={{ minWidth: 180 }} value={auditSourceTable} options={SOURCE_TABLE_OPTIONS} onChange={setAuditSourceTable} />
            </Space>
            {auditErrors.length === 0 && !primaryLoading ? (
              <Empty description={buildAuditEmptyDescription()} />
            ) : (
              <Table
                rowKey="id"
                dataSource={auditErrors}
                columns={auditErrorColumns}
                loading={primaryLoading}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
              />
            )}
          </Space>
        </Card>

        <Card
          title="有没有脏数据 / 异常"
          extra={(
            <Button type="link" onClick={() => setShowHealthDetails((value) => !value)}>
              {showHealthDetails ? '收起详细体检' : '展开详细体检'}
            </Button>
          )}
        >
          {healthReport ? (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Row gutter={[16, 16]}>
                <Col xs={12} md={6}><Card size="small"><Statistic title="滞留候选" value={healthReport.summary.stale_pending_promotions} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="已回退" value={healthReport.summary.rolled_back_promotions} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="来源冲突组" value={healthReport.summary.source_conflict_groups} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="正式层失活" value={healthReport.summary.inactive_formal_total} /></Card></Col>
              </Row>
              <Typography.Text type="secondary">
                {healthReport.summary.rolled_back_promotions > 0 || healthReport.summary.source_conflict_groups > 0
                  ? '目前有回退或冲突信号，建议先处理队列，再回来复查这里。'
                  : '目前没有明显污染信号，这里按需查看即可。'}
              </Typography.Text>
              {showHealthDetails ? (
                <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                  <div>
                    <Typography.Text strong>正式层健康概览</Typography.Text>
                    <div style={{ marginTop: 8 }}>
                      <Space wrap>
                        <Tag>{`失活规则: ${healthReport.formal_layer_health.inactive_rules}`}</Tag>
                        <Tag>{`失活方法卡: ${healthReport.formal_layer_health.inactive_method_cards}`}</Tag>
                        <Tag>{`待确认经验: ${healthReport.formal_layer_health.experience_candidate_count}`}</Tag>
                        <Tag>{`争议经验: ${healthReport.formal_layer_health.experience_disputed_count}`}</Tag>
                      </Space>
                    </div>
                  </div>
                  <div>
                    <Typography.Text strong>滞留待处理候选</Typography.Text>
                    <Table rowKey="id" size="small" style={{ marginTop: 8 }} dataSource={healthReport.stale_pending_promotions} columns={stalePendingColumns} pagination={false} />
                  </div>
                  <div>
                    <Typography.Text strong>最近回退记录</Typography.Text>
                    <Table rowKey="id" size="small" style={{ marginTop: 8 }} dataSource={healthReport.recent_rollbacks} columns={rollbackColumns} pagination={false} />
                  </div>
                </Space>
              ) : null}
            </Space>
          ) : (
            <Typography.Text type="secondary">
              {secondaryLoading ? '正在补充异常分析...' : secondaryError || '目前没有需要你优先关注的异常信号。'}
            </Typography.Text>
          )}
        </Card>

        <Card
          title="最近有没有真正帮上忙"
          extra={(
            <Button type="link" onClick={() => setShowKnowledgeImpact((value) => !value)}>
              {showKnowledgeImpact ? '收起详细分析' : '展开详细分析'}
            </Button>
          )}
        >
          {knowledgeImpact ? (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Row gutter={[16, 16]}>
                <Col xs={12} md={6}><Card size="small"><Statistic title="7天命中" value={knowledgeImpact.summary.last_7d_hits} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="7天直接命中" value={knowledgeImpact.summary.last_7d_direct} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="7天任务数" value={knowledgeImpact.summary.last_7d_runs} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="7天结果数" value={knowledgeImpact.summary.last_7d_results} /></Card></Col>
              </Row>
              <Typography.Text type="secondary">这块更像分析层，不是第一操作层。需要复盘时再展开看。</Typography.Text>
              {showKnowledgeImpact ? (
                <Space direction="vertical" size="middle" style={{ width: '100%' }}>
                  <div>
                    <Typography.Text strong>正式知识层命中统计</Typography.Text>
                    <Table rowKey="layer" size="small" style={{ marginTop: 8 }} dataSource={knowledgeImpact.layer_metrics} columns={knowledgeImpactLayerColumns} pagination={false} scroll={{ x: 900 }} />
                  </div>
                  <div>
                    <Typography.Text strong>命中最多的对象</Typography.Text>
                    <Table rowKey={(record) => `${record.layer}-${record.object_ref}`} size="small" style={{ marginTop: 8 }} dataSource={knowledgeImpact.top_objects || []} columns={knowledgeImpactObjectColumns} pagination={false} scroll={{ x: 900 }} />
                  </div>
                </Space>
              ) : null}
            </Space>
          ) : (
            <Typography.Text type="secondary">
              {secondaryLoading ? '正在补充使用效果分析...' : secondaryError || '目前还没有足够的使用效果数据。'}
            </Typography.Text>
          )}
        </Card>
      </Space>

      <Drawer
        title={selectedPromotion ? `候选详情 #${selectedPromotion.id}` : '候选详情'}
        width={860}
        open={promotionDrawerOpen}
        onClose={() => {
          setPromotionDrawerOpen(false);
          setSelectedPromotion(null);
        }}
      >
        {!selectedPromotion ? (
          <Empty description="暂无可查看的候选详情" />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="候选标题" span={2}>
                {selectedPromotion.candidate_title || '-'}
              </Descriptions.Item>
              <Descriptions.Item label="候选类型">
                <Tag>{getLabel(selectedPromotion.candidate_type, CANDIDATE_TYPE_LABELS)}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="目标层">
                <Tag>{getLabel(selectedPromotion.target_layer, TARGET_LAYER_LABELS)}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="当前状态">
                {renderStatusTag(selectedPromotion.status)}
              </Descriptions.Item>
              <Descriptions.Item label="审核状态">
                {renderStatusTag(selectedPromotion.review_status)}
              </Descriptions.Item>
              <Descriptions.Item label="来源">
                {getLabel(selectedPromotion.source_table, SOURCE_TABLE_LABELS)}
              </Descriptions.Item>
              <Descriptions.Item label="card_id">
                {toDisplayText(selectedPromotionCardId !== undefined ? selectedPromotionCardId : selectedPromotion.source_record_id)}
              </Descriptions.Item>
              <Descriptions.Item label="source">
                {toDisplayText(selectedPromotionSource !== undefined ? selectedPromotionSource : selectedPromotion.source_table)}
              </Descriptions.Item>
              <Descriptions.Item label="evidence_ref">
                {toDisplayText(getPayloadValue(selectedPromotionPayload, ['evidence_ref']) ?? '-')}
              </Descriptions.Item>
              <Descriptions.Item label="审核备注" span={2}>
                {selectedPromotion.review_comment || '-'}
              </Descriptions.Item>
            </Descriptions>

            <Card size="small" title="原始问题">
              <div style={{ whiteSpace: 'pre-wrap' }}>
                {toDisplayText(selectedPromotionOriginalProblem !== undefined ? selectedPromotionOriginalProblem : selectedPromotion.candidate_summary)}
              </div>
            </Card>

            <Card size="small" title="定案结论">
              <div style={{ whiteSpace: 'pre-wrap' }}>
                {toDisplayText(selectedPromotionConclusion !== undefined ? selectedPromotionConclusion : selectedPromotion.candidate_summary)}
              </div>
            </Card>

            <Card size="small" title="判断依据">
              <div style={{ whiteSpace: 'pre-wrap' }}>
                {toDisplayText(selectedPromotionJudgmentBasis)}
              </div>
            </Card>

            <Card size="small" title="排除理由">
              {selectedPromotionExclusionReasons.length > 0 ? (
                <Space direction="vertical" size={8} style={{ width: '100%' }}>
                  {selectedPromotionExclusionReasons.map((item, index) => (
                    <div key={`${item}-${index}`} style={{ whiteSpace: 'pre-wrap' }}>
                      {index + 1}. {item}
                    </div>
                  ))}
                </Space>
              ) : (
                <Typography.Text type="secondary">暂无排除理由</Typography.Text>
              )}
            </Card>

            <Card size="small" title="核心知识点">
              {selectedPromotionKnowledgePoints.length > 0 ? (
                <Space wrap>
                  {selectedPromotionKnowledgePoints.map((item) => (
                    <Tag key={item} color="blue">
                      {item}
                    </Tag>
                  ))}
                </Space>
              ) : (
                <Typography.Text type="secondary">暂无核心知识点</Typography.Text>
              )}
            </Card>

            <Card size="small" title="建议晋升类型">
              <Space wrap>
                <Tag color="purple">
                  {toDisplayText(selectedPromotionSuggestedType !== undefined ? selectedPromotionSuggestedType : getLabel(selectedPromotion.candidate_type, CANDIDATE_TYPE_LABELS))}
                </Tag>
                <Tag color="geekblue">{getLabel(selectedPromotion.target_layer, TARGET_LAYER_LABELS)}</Tag>
              </Space>
            </Card>

            <Card size="small" title="标签">
              {selectedPromotionTags.length > 0 ? (
                <Space wrap>
                  {selectedPromotionTags.map((tag) => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                </Space>
              ) : (
                <Typography.Text type="secondary">暂无标签</Typography.Text>
              )}
            </Card>

            <Card size="small" title="查看原始 JSON">
              <details>
                <summary style={{ cursor: 'pointer', color: '#2563eb' }}>展开原始 candidate_payload</summary>
                <pre style={{ marginTop: 12, whiteSpace: 'pre-wrap' }}>
                  {JSON.stringify(selectedPromotion.candidate_payload || {}, null, 2)}
                </pre>
              </details>
            </Card>
          </Space>
        )}
      </Drawer>

      <Drawer
        title={selectedAuditError ? `错因 #${selectedAuditError.id}` : '错因详情'}
        width={760}
        open={auditDrawerOpen}
        extra={selectedAuditError ? (
          <Button size="small" icon={<LinkOutlined />} onClick={() => openTaskResultPage(selectedAuditError.task_id, selectedAuditError.result_id, { sourceLabel: '错因审核', errorType: getLabel(selectedAuditError.error_type, ERROR_TYPE_LABELS) })}>
            打开任务结果
          </Button>
        ) : null}
        onClose={() => {
          setAuditDrawerOpen(false);
          setSelectedAuditError(null);
        }}
      >
        {!selectedAuditError && !auditLoading ? (
          <Empty description="暂无可查看的错因详情" />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="清单名称" span={2}>{selectedAuditError?.bill_name || '-'}</Descriptions.Item>
              <Descriptions.Item label="省份">{selectedAuditError?.province || '-'}</Descriptions.Item>
              <Descriptions.Item label="专业">{selectedAuditError?.specialty || '-'}</Descriptions.Item>
              <Descriptions.Item label="来源类型">{selectedAuditError?.source_type || '-'}</Descriptions.Item>
              <Descriptions.Item label="匹配来源">{getLabel(selectedAuditError?.match_source, MATCH_SOURCE_LABELS)}</Descriptions.Item>
              <Descriptions.Item label="任务 ID">{selectedAuditError?.task_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="结果 ID">{selectedAuditError?.result_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="原匹配" span={2}>{selectedAuditError?.predicted_quota_name || '-'}{selectedAuditError?.predicted_quota_code ? `（${selectedAuditError.predicted_quota_code}）` : ''}</Descriptions.Item>
              <Descriptions.Item label="修正后" span={2}>{selectedAuditError?.corrected_quota_name || '-'}{selectedAuditError?.corrected_quota_code ? `（${selectedAuditError.corrected_quota_code}）` : ''}</Descriptions.Item>
              <Descriptions.Item label="错因类型"><Tag>{getLabel(selectedAuditError?.error_type, ERROR_TYPE_LABELS)}</Tag></Descriptions.Item>
              <Descriptions.Item label="风险等级"><Tag color={ERROR_LEVEL_COLORS[selectedAuditError?.error_level || ''] || 'default'}>{getLabel(selectedAuditError?.error_level, ERROR_LEVEL_LABELS)}</Tag></Descriptions.Item>
              <Descriptions.Item label="审核状态">{renderStatusTag(selectedAuditError?.review_status)}</Descriptions.Item>
              <Descriptions.Item label="审核人">{selectedAuditError?.reviewer || '-'}</Descriptions.Item>
              <Descriptions.Item label="证据引用" span={2}>{selectedAuditError?.evidence_ref || '-'}</Descriptions.Item>
            </Descriptions>
            <div><Typography.Text strong>清单描述</Typography.Text><div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{selectedAuditError?.bill_desc || '-'}</div></div>
            <Divider style={{ margin: '8px 0' }} />
            <div><Typography.Text strong>根因说明</Typography.Text><div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{selectedAuditError?.root_cause || '-'}</div><Space wrap style={{ marginTop: 8 }}>{(selectedAuditError?.root_cause_tags || []).map((tag) => <Tag key={tag}>{tag}</Tag>)}</Space></div>
            <div><Typography.Text strong>修正建议</Typography.Text><div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{selectedAuditError?.fix_suggestion || '-'}</div></div>
            <div><Typography.Text strong>决策依据</Typography.Text><div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{selectedAuditError?.decision_basis || '-'}</div></div>
            <div><Typography.Text strong>审核备注</Typography.Text><div style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{selectedAuditError?.review_comment || '-'}</div></div>
          </Space>
        )}
      </Drawer>

      <Drawer
        title={knowledgeObjectDetail ? `知识对象 ${knowledgeObjectDetail.object_ref}` : '知识对象详情'}
        width={860}
        open={knowledgeObjectDrawerOpen}
        onClose={() => {
          setKnowledgeObjectDrawerOpen(false);
          setKnowledgeObjectDetail(null);
        }}
      >
        {!knowledgeObjectDetail && !knowledgeObjectLoading ? (
          <Empty description="暂无可查看的知识对象详情" />
        ) : (
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Descriptions column={2} bordered size="small">
              <Descriptions.Item label="对象引用">{knowledgeObjectDetail?.object_ref || '-'}</Descriptions.Item>
              <Descriptions.Item label="正式层引用">{knowledgeObjectDetail?.promoted_target_ref || '-'}</Descriptions.Item>
            </Descriptions>
            <div>
              <Typography.Text strong>正式层详情</Typography.Text>
              <pre style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{JSON.stringify(knowledgeObjectDetail?.formal_detail || {}, null, 2)}</pre>
            </div>
            <div>
              <Typography.Text strong>晋升来源</Typography.Text>
              <Table
                rowKey="id"
                size="small"
                style={{ marginTop: 8 }}
                dataSource={knowledgeObjectDetail?.promotion_sources || []}
                pagination={false}
                columns={[
                  { title: 'ID', dataIndex: 'id', key: 'id', width: 70 },
                  { title: '候选标题', dataIndex: 'candidate_title', key: 'candidate_title' },
                  { title: '目标层', dataIndex: 'target_layer', key: 'target_layer', width: 150, render: (value: string) => getLabel(value, TARGET_LAYER_LABELS) },
                  { title: '状态', dataIndex: 'status', key: 'status', width: 100, render: (value: string) => renderStatusTag(value) },
                  { title: '审核备注', dataIndex: 'review_comment', key: 'review_comment', width: 220, render: (value?: string) => <div style={{ fontSize: 12, whiteSpace: 'pre-wrap' }}>{value || '-'}</div> },
                  {
                    title: '操作',
                    key: 'actions',
                    width: 220,
                    render: (_value, record) => {
                      const linkedAuditId = getAuditErrorIdFromSource(record.source_table, record.source_record_id);
                      if (linkedAuditId === null) return '-';
                      return (
                        <Space wrap>
                          <Button size="small" icon={<EyeOutlined />} onClick={() => openAuditErrorDetail(linkedAuditId)}>打开错因</Button>
                          <Button size="small" icon={<LinkOutlined />} onClick={() => openTaskResultFromPromotionSource(record)}>打开任务结果</Button>
                        </Space>
                      );
                    },
                  },
                ]}
              />
            </div>
          </Space>
        )}
      </Drawer>
    </>
  );
}
