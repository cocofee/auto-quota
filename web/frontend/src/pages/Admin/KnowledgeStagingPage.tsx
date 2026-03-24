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

interface RecentActivityPoint {
  date: string;
  audit_created: number;
  promotion_created: number;
  promotion_reviewed: number;
  promotion_promoted: number;
}

interface BreakdownItem {
  bucket: string;
  draft: number;
  reviewing: number;
  approved: number;
  rejected: number;
  promoted: number;
  rolled_back: number;
  total: number;
  reviewed_total: number;
  approved_total: number;
  rejected_total: number;
  approval_rate: number;
  rejection_rate: number;
  execution_rate: number;
}

interface RejectionBreakdownItem {
  bucket: string;
  rejected_total: number;
  top_reasons: RejectionReasonStat[];
}

interface StagingStats {
  audit_total: number;
  promotion_total: number;
  promotion_status_counts: CountMap;
  promotion_target_counts: CountMap;
  promotion_candidate_counts: CountMap;
  promotion_target_metrics: BreakdownItem[];
  promotion_candidate_metrics: BreakdownItem[];
  rejection_reason_by_target: RejectionBreakdownItem[];
  rejection_reason_by_candidate: RejectionBreakdownItem[];
  audit_review_counts: CountMap;
  audit_match_source_counts: CountMap;
  audit_error_type_counts: CountMap;
  promotion_reviewed_total: number;
  promotion_approved_total: number;
  promotion_rejected_total: number;
  promotion_approval_rate: number;
  promotion_rejection_rate: number;
  promotion_execution_rate: number;
  recent_activity: RecentActivityPoint[];
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

interface DuplicateCandidateGroup {
  target_layer: string;
  candidate_type: string;
  candidate_title: string;
  duplicate_count: number;
  source_count: number;
  oldest_created_at: number;
  latest_created_at: number;
  sample_ids: string;
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

interface SourceConflictGroup {
  source_table: string;
  source_record_id: string;
  candidate_count: number;
  target_layer_count: number;
  target_layers: string;
  candidate_types: string;
  latest_updated_at?: number;
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
  duplicate_candidate_groups: DuplicateCandidateGroup[];
  stale_pending_promotions: StalePendingPromotion[];
  recent_rollbacks: RollbackRecord[];
  source_conflict_groups: SourceConflictGroup[];
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
  run_count: number;
  total_results: number;
  hit_count: number;
  direct_count: number;
  assist_count: number;
  high_conf_count: number;
  low_risk_count: number;
  green_count: number;
  hint_count: number;
  reviewed_count: number;
  confirmed_count: number;
  corrected_count: number;
  pending_count: number;
  hit_rate: number;
  direct_rate: number;
  high_conf_rate: number;
  low_risk_rate: number;
  review_coverage_rate: number;
  confirmed_rate: number;
  corrected_rate: number;
}

interface KnowledgeImpactRecentItem {
  date: string;
  total_results: number;
  runs: number;
  experience_hits: number;
  experience_direct: number;
  rule_hits: number;
  rule_direct: number;
  method_hits: number;
  method_assist: number;
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
  recent_activity: KnowledgeImpactRecentItem[];
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
  reviewed_at?: number;
  promoted_at?: number;
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
  RuleKnowledge: '规则知识库',
  MethodCards: '方法卡片库',
  ExperienceDB: '经验库',
  UniversalKB: '通用知识库',
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
  { label: '规则知识库', value: 'RuleKnowledge' },
  { label: '方法卡片库', value: 'MethodCards' },
  { label: '经验库', value: 'ExperienceDB' },
];

const PROMOTION_TYPE_OPTIONS = [
  { label: '全部候选类型', value: 'all' },
  { label: '规则', value: 'rule' },
  { label: '方法', value: 'method' },
  { label: '经验', value: 'experience' },
];

const SOURCE_TABLE_OPTIONS = [
  { label: '全部来源', value: 'all' },
  { label: '错因记录', value: 'audit_errors' },
  { label: '匹配结果', value: 'match_results' },
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

function formatDateTime(ts?: number) {
  if (!ts) return '-';
  return new Date(ts * 1000).toLocaleString();
}

function sumRecentActivity(points: RecentActivityPoint[], key: keyof RecentActivityPoint) {
  return points.reduce((sum, item) => sum + (typeof item[key] === 'number' ? Number(item[key]) : 0), 0);
}

function getAuditErrorIdFromSource(sourceTable?: string, sourceRecordId?: string) {
  if (sourceTable !== 'audit_errors') return null;
  const auditErrorId = Number(sourceRecordId);
  return Number.isFinite(auditErrorId) ? auditErrorId : null;
}

function getLabel(value: string | undefined, mapping: Record<string, string>) {
  const normalized = String(value || '').trim();
  return mapping[normalized] || normalized || '-';
}

function renderStatusTag(status?: string) {
  const normalized = String(status || '').trim();
  return (
    <Tag color={STATUS_COLORS[normalized] || 'default'} style={{ marginRight: 0 }}>
      {getLabel(normalized, STATUS_LABELS)}
    </Tag>
  );
}

function renderCountTags(data: CountMap | undefined, emptyText = '暂无数据', mapping?: Record<string, string>) {
  if (!data || Object.keys(data).length === 0) {
    return <Typography.Text type="secondary">{emptyText}</Typography.Text>;
  }
  return (
    <Space wrap>
      {Object.entries(data).map(([key, value]) => (
        <Tag key={key}>{`${mapping ? getLabel(key, mapping) : key}: ${value}`}</Tag>
      ))}
    </Space>
  );
}

function formatPercent(value?: number) {
  return `${Number(value || 0).toFixed(1)}%`;
}

function buildPromotionEmptyDescription(view: PromotionStatusView) {
  if (view === 'rejected') return '当前筛选条件下没有已驳回候选';
  if (view === 'promoted') return '当前筛选条件下没有已晋升候选';
  if (view === 'rolled_back') return '当前筛选条件下没有已回退候选';
  if (view === 'all') return '当前筛选条件下没有候选记录';
  return '当前没有待处理的晋升候选';
}

function buildAuditEmptyDescription() {
  return '当前筛选条件下没有错因记录';
}

function renderSecondaryPlaceholder(loading: boolean, errorMessage: string, loadingText: string) {
  if (loading) {
    return <Typography.Text type="secondary">{loadingText}</Typography.Text>;
  }
  if (errorMessage) {
    return <Typography.Text type="secondary">{errorMessage}</Typography.Text>;
  }
  return <Typography.Text type="secondary">暂无数据</Typography.Text>;
}

export default function KnowledgeStagingPage() {
  const { message } = App.useApp();
  const requestRef = useRef(0);

  const [primaryLoading, setPrimaryLoading] = useState(false);
  const [secondaryLoading, setSecondaryLoading] = useState(false);
  const [auditLoading, setAuditLoading] = useState(false);

  const [health, setHealth] = useState<StagingHealth | null>(null);
  const [stats, setStats] = useState<StagingStats | null>(null);
  const [healthReport, setHealthReport] = useState<StagingHealthReport | null>(null);
  const [knowledgeImpact, setKnowledgeImpact] = useState<KnowledgeImpactReport | null>(null);
  const [secondaryError, setSecondaryError] = useState('');

  const [items, setItems] = useState<PromotionItem[]>([]);
  const [auditErrors, setAuditErrors] = useState<AuditErrorItem[]>([]);
  const [selectedAuditError, setSelectedAuditError] = useState<AuditErrorItem | null>(null);
  const [auditDrawerOpen, setAuditDrawerOpen] = useState(false);
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
    const query = params.toString();
    const url = query ? `/tasks/${taskId}/results?${query}` : `/tasks/${taskId}/results`;
    window.open(url, '_blank', 'noopener,noreferrer');
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
      const { data: healthReportData } = await api.get<StagingHealthReport>('/admin/knowledge-staging/health-report');
      if (requestId !== requestRef.current) return;
      setHealthReport(healthReportData);

      const { data: knowledgeImpactData } = await api.get<KnowledgeImpactReport>(
        '/admin/knowledge-staging/knowledge-impact',
        { params: { days: 7 } },
      );
      if (requestId !== requestRef.current) return;
      setKnowledgeImpact(knowledgeImpactData);
    } catch {
      if (requestId === requestRef.current) {
        setSecondaryError('深度报表暂时未加载成功，不影响首屏处理。');
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
      if (requestId !== requestRef.current) return;
      void loadSecondaryData(requestId);
    } catch {
      if (requestId === requestRef.current) {
        message.error('加载晋升工作台首屏数据失败');
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
      setAuditErrors((current) =>
        current.some((item) => item.id === data.id)
          ? current.map((item) => (item.id === data.id ? data : item))
          : [data, ...current],
      );
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
    } catch {
      message.error('执行晋升失败');
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

  const recentActivity = stats?.recent_activity || [];
  const recentPromotionCreatedTotal = sumRecentActivity(recentActivity, 'promotion_created');
  const recentPromotionReviewedTotal = sumRecentActivity(recentActivity, 'promotion_reviewed');
  const pendingCount = (stats?.promotion_status_counts.draft || 0) + (stats?.promotion_status_counts.reviewing || 0);
  const approvedCount = stats?.promotion_status_counts.approved || 0;
  const promotedCount = stats?.promotion_status_counts.promoted || 0;
  const rolledBackCount = stats?.promotion_status_counts.rolled_back || 0;
  const rejectedCount = stats?.promotion_status_counts.rejected || 0;

  const promotionColumns: ColumnsType<PromotionItem> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 72 },
    {
      title: '候选内容',
      dataIndex: 'candidate_title',
      key: 'candidate_title',
      render: (value: string, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{value || '-'}</div>
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
      title: '当前状态',
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
      key: 'review_result',
      width: 280,
      render: (_value, record) => {
        const promotedResult = record.promoted_target_ref || record.promoted_target_id || '';
        return (
          <Space direction="vertical" size={4}>
            <Typography.Text style={{ fontSize: 12 }}>驳回原因：{record.rejection_reason || '-'}</Typography.Text>
            <Typography.Text style={{ fontSize: 12 }}>审核备注：{record.review_comment || '-'}</Typography.Text>
            <Typography.Text style={{ fontSize: 12 }}>晋升结果：{promotedResult || '-'}</Typography.Text>
          </Space>
        );
      },
    },
    {
      title: '来源',
      key: 'source',
      width: 220,
      render: (_value, record) => {
        const linkedAuditId = getAuditErrorIdFromSource(record.source_table, record.source_record_id);
        return (
          <Space direction="vertical" size={4}>
            <div style={{ fontSize: 12 }}>
              <div>{getLabel(record.source_table, SOURCE_TABLE_LABELS)}</div>
              <div style={{ color: '#64748b' }}>{record.source_record_id || '-'}</div>
            </div>
            {linkedAuditId !== null ? (
              <Button
                size="small"
                type="link"
                style={{ padding: 0, height: 'auto' }}
                icon={<EyeOutlined />}
                onClick={() => openAuditErrorDetail(linkedAuditId)}
              >
                查看错因
              </Button>
            ) : null}
          </Space>
        );
      },
    },
    {
      title: '操作',
      key: 'actions',
      width: 340,
      render: (_value, record) => (
        <Space wrap>
          <Button
            size="small"
            icon={<EyeOutlined />}
            onClick={() => {
              const linkedAuditId = getAuditErrorIdFromSource(record.source_table, record.source_record_id);
              if (linkedAuditId !== null) {
                void openAuditErrorDetail(linkedAuditId);
              } else {
                message.warning('这条候选没有关联错因记录');
              }
            }}
          >
            看依据
          </Button>
          <Button
            size="small"
            icon={<CheckOutlined />}
            disabled={record.status !== 'draft' && record.status !== 'reviewing'}
            onClick={() => reviewPromotion(record, 'approved')}
          >
            通过
          </Button>
          <Button
            size="small"
            icon={<StopOutlined />}
            disabled={record.status !== 'draft' && record.status !== 'reviewing'}
            onClick={() => reviewPromotion(record, 'rejected')}
          >
            驳回
          </Button>
          <Button
            size="small"
            type="primary"
            icon={<PlayCircleOutlined />}
            disabled={record.status !== 'approved' || !EXECUTABLE_TARGET_LAYERS.has(record.target_layer)}
            onClick={() => executePromotion(record)}
          >
            晋升入库
          </Button>
          <Button
            size="small"
            disabled={!(record.status === 'promoted' && ROLLBACK_TARGET_LAYERS.has(record.target_layer))}
            onClick={() => rollbackPromotion(record)}
          >
            回退
          </Button>
        </Space>
      ),
    },
  ];

  const auditErrorColumns: ColumnsType<AuditErrorItem> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 72 },
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
            原匹配：{record.predicted_quota_name || '-'}
            {record.predicted_quota_code ? `（${record.predicted_quota_code}）` : ''}
          </div>
          <div style={{ fontSize: 12 }}>
            修正后：{record.corrected_quota_name || '-'}
            {record.corrected_quota_code ? `（${record.corrected_quota_code}）` : ''}
          </div>
        </div>
      ),
    },
    {
      title: '错因信息',
      key: 'error',
      width: 260,
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
      width: 220,
      render: (_value, record) => (
        <Space wrap>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openAuditErrorDetail(record.id)}>
            详情
          </Button>
          <Button
            size="small"
            icon={<LinkOutlined />}
            onClick={() =>
              openTaskResultPage(record.task_id, record.result_id, {
                sourceLabel: '错因审核',
                errorType: getLabel(record.error_type, ERROR_TYPE_LABELS),
              })
            }
          >
            任务结果
          </Button>
        </Space>
      ),
    },
  ];

  const duplicateCandidateColumns: ColumnsType<DuplicateCandidateGroup> = [
    {
      title: '候选内容',
      key: 'candidate',
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{record.candidate_title || '-'}</div>
          <Space wrap size={4} style={{ marginTop: 4 }}>
            <Tag>{getLabel(record.candidate_type, CANDIDATE_TYPE_LABELS)}</Tag>
            <Tag>{getLabel(record.target_layer, TARGET_LAYER_LABELS)}</Tag>
          </Space>
        </div>
      ),
    },
    { title: '重复数', dataIndex: 'duplicate_count', key: 'duplicate_count', width: 100 },
    { title: '来源数', dataIndex: 'source_count', key: 'source_count', width: 90 },
    { title: '最近时间', dataIndex: 'latest_created_at', key: 'latest_created_at', width: 180, render: (value: number) => formatDateTime(value) },
  ];

  const stalePendingColumns: ColumnsType<StalePendingPromotion> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 72 },
    {
      title: '候选内容',
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
    {
      title: '来源',
      key: 'source',
      width: 220,
      render: (_value, record) => (
        <div style={{ fontSize: 12 }}>
          <div>{getLabel(record.source_table, SOURCE_TABLE_LABELS)}</div>
          <div style={{ color: '#64748b' }}>{record.source_record_id || '-'}</div>
        </div>
      ),
    },
  ];

  const rollbackColumns: ColumnsType<RollbackRecord> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 72 },
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

  const sourceConflictColumns: ColumnsType<SourceConflictGroup> = [
    {
      title: '来源',
      key: 'source',
      render: (_value, record) => (
        <div>
          <div style={{ fontWeight: 600 }}>{getLabel(record.source_table, SOURCE_TABLE_LABELS)}</div>
          <div style={{ color: '#64748b', fontSize: 12 }}>{record.source_record_id || '-'}</div>
        </div>
      ),
    },
    {
      title: '目标层',
      dataIndex: 'target_layers',
      key: 'target_layers',
      render: (value: string) => (
        <Space wrap>
          {String(value || '')
            .split(',')
            .filter(Boolean)
            .map((item) => (
              <Tag key={`${value}-${item}`}>{getLabel(item, TARGET_LAYER_LABELS)}</Tag>
            ))}
        </Space>
      ),
    },
    {
      title: '候选类型',
      dataIndex: 'candidate_types',
      key: 'candidate_types',
      render: (value: string) => (
        <Space wrap>
          {String(value || '')
            .split(',')
            .filter(Boolean)
            .map((item) => (
              <Tag key={`${value}-${item}`}>{getLabel(item, CANDIDATE_TYPE_LABELS)}</Tag>
            ))}
        </Space>
      ),
    },
    { title: '数量', dataIndex: 'candidate_count', key: 'candidate_count', width: 90 },
  ];

  const knowledgeImpactLayerColumns: ColumnsType<KnowledgeImpactLayerMetric> = [
    { title: '知识层', dataIndex: 'layer', key: 'layer', width: 140, render: (value: string) => getLabel(value, TARGET_LAYER_LABELS) },
    { title: '命中数', dataIndex: 'hit_count', key: 'hit_count', width: 90 },
    { title: '直接命中', dataIndex: 'direct_count', key: 'direct_count', width: 90 },
    { title: '辅助命中', dataIndex: 'assist_count', key: 'assist_count', width: 90 },
    { title: '已审核', dataIndex: 'reviewed_count', key: 'reviewed_count', width: 90 },
    { title: '已确认', dataIndex: 'confirmed_count', key: 'confirmed_count', width: 90 },
    { title: '已修正', dataIndex: 'corrected_count', key: 'corrected_count', width: 90 },
    { title: '待审核', dataIndex: 'pending_count', key: 'pending_count', width: 90 },
    { title: '高置信', dataIndex: 'high_conf_count', key: 'high_conf_count', width: 90 },
    { title: '低风险', dataIndex: 'low_risk_count', key: 'low_risk_count', width: 90 },
    { title: '规则提示', dataIndex: 'hint_count', key: 'hint_count', width: 90 },
    { title: '命中率', dataIndex: 'hit_rate', key: 'hit_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '审核率', dataIndex: 'review_coverage_rate', key: 'review_coverage_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '确认率', dataIndex: 'confirmed_rate', key: 'confirmed_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '修正率', dataIndex: 'corrected_rate', key: 'corrected_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '直命率', dataIndex: 'direct_rate', key: 'direct_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '高置信率', dataIndex: 'high_conf_rate', key: 'high_conf_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '低风险率', dataIndex: 'low_risk_rate', key: 'low_risk_rate', width: 90, render: (value: number) => formatPercent(value) },
  ];

  const knowledgeImpactRecentColumns: ColumnsType<KnowledgeImpactRecentItem> = [
    { title: '日期', dataIndex: 'date', key: 'date', width: 120 },
    { title: '任务数', dataIndex: 'runs', key: 'runs', width: 90 },
    { title: '结果数', dataIndex: 'total_results', key: 'total_results', width: 90 },
    { title: '经验命中', dataIndex: 'experience_hits', key: 'experience_hits', width: 100 },
    { title: '经验直命', dataIndex: 'experience_direct', key: 'experience_direct', width: 100 },
    { title: '规则命中', dataIndex: 'rule_hits', key: 'rule_hits', width: 100 },
    { title: '规则直命', dataIndex: 'rule_direct', key: 'rule_direct', width: 100 },
    { title: '方法命中', dataIndex: 'method_hits', key: 'method_hits', width: 100 },
    { title: '方法辅助', dataIndex: 'method_assist', key: 'method_assist', width: 100 },
  ];

  const knowledgeImpactObjectColumns: ColumnsType<KnowledgeImpactObjectMetric> = [
    { title: '知识层', dataIndex: 'layer', key: 'layer', width: 140, render: (value: string) => getLabel(value, TARGET_LAYER_LABELS) },
    { title: '对象引用', dataIndex: 'object_ref', key: 'object_ref', width: 220, render: (value: string) => value || '-' },
    { title: '命中数', dataIndex: 'hit_count', key: 'hit_count', width: 80 },
    { title: '直接命中', dataIndex: 'direct_count', key: 'direct_count', width: 90 },
    { title: '辅助命中', dataIndex: 'assist_count', key: 'assist_count', width: 90 },
    { title: '已审核', dataIndex: 'reviewed_count', key: 'reviewed_count', width: 90 },
    { title: '已确认', dataIndex: 'confirmed_count', key: 'confirmed_count', width: 90 },
    { title: '已修正', dataIndex: 'corrected_count', key: 'corrected_count', width: 90 },
    { title: '待审核', dataIndex: 'pending_count', key: 'pending_count', width: 90 },
    { title: '审核率', dataIndex: 'review_coverage_rate', key: 'review_coverage_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '确认率', dataIndex: 'confirmed_rate', key: 'confirmed_rate', width: 90, render: (value: number) => formatPercent(value) },
    { title: '修正率', dataIndex: 'corrected_rate', key: 'corrected_rate', width: 90, render: (value: number) => formatPercent(value) },
    {
      title: '操作',
      key: 'actions',
      width: 100,
      render: (_value, record) => (
        <Button size="small" icon={<EyeOutlined />} onClick={() => openKnowledgeObjectDetail(record.object_ref)}>
          打开
        </Button>
      ),
    },
  ];

  return (
    <>
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        <Card
          title="知识晋升工作台"
          extra={(
            <Space>
              <Typography.Text type="secondary">
                {secondaryLoading ? '首屏已到位，正在补充深度报表' : '首屏优先加载'}
              </Typography.Text>
              <Button icon={<ReloadOutlined />} onClick={loadData} loading={primaryLoading || secondaryLoading}>
                刷新工作台
              </Button>
            </Space>
          )}
        >
          {health ? (
            <Descriptions size="small" column={3}>
              <Descriptions.Item label="staging 状态">
                <Tag color={health.ok ? 'green' : 'red'}>{health.ok ? '正常' : '异常'}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="Schema 版本">v{health.schema_version || '-'}</Descriptions.Item>
              <Descriptions.Item label="首屏候选数">{items.length}</Descriptions.Item>
            </Descriptions>
          ) : (
            <Typography.Text type="secondary">正在加载首屏状态...</Typography.Text>
          )}
        </Card>

        <Card title="今日先看这些">
          {stats ? (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Row gutter={[16, 16]}>
                <Col xs={12} md={6}><Card size="small"><Statistic title="待处理候选" value={pendingCount} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="已通过待晋升" value={approvedCount} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="已晋升" value={promotedCount} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="已回退" value={rolledBackCount} /></Card></Col>
              </Row>
              <Row gutter={[16, 16]}>
                <Col xs={12} md={6}><Card size="small"><Statistic title="错因记录" value={stats.audit_total} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="已驳回候选" value={rejectedCount} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="7天新增候选" value={recentPromotionCreatedTotal} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="7天已审核" value={recentPromotionReviewedTotal} /></Card></Col>
              </Row>
              <Space wrap>
                <Tag color="blue">通过率 {formatPercent(stats.promotion_approval_rate)}</Tag>
                <Tag color="red">驳回率 {formatPercent(stats.promotion_rejection_rate)}</Tag>
                <Tag color="green">执行率 {formatPercent(stats.promotion_execution_rate)}</Tag>
              </Space>
              <div>
                <Typography.Text strong>目标层分布</Typography.Text>
                <div style={{ marginTop: 8 }}>
                  {renderCountTags(stats.promotion_target_counts, '暂无候选', TARGET_LAYER_LABELS)}
                </div>
              </div>
              <div>
                <Typography.Text strong>候选类型分布</Typography.Text>
                <div style={{ marginTop: 8 }}>
                  {renderCountTags(stats.promotion_candidate_counts, '暂无候选', CANDIDATE_TYPE_LABELS)}
                </div>
              </div>
              <div>
                <Typography.Text strong>错因来源分布</Typography.Text>
                <div style={{ marginTop: 8 }}>
                  {renderCountTags(stats.audit_match_source_counts, '暂无错因', MATCH_SOURCE_LABELS)}
                </div>
              </div>
              <div>
                <Typography.Text strong>主要驳回原因</Typography.Text>
                <div style={{ marginTop: 8 }}>
                  {stats.top_rejection_reasons.length > 0 ? (
                    <Space wrap>
                      {stats.top_rejection_reasons.map((item) => (
                        <Tag key={item.reason}>{`${item.reason}: ${item.count}`}</Tag>
                      ))}
                    </Space>
                  ) : (
                    <Typography.Text type="secondary">目前还没有驳回原因统计</Typography.Text>
                  )}
                </div>
              </div>
            </Space>
          ) : (
            <Typography.Text type="secondary">正在加载首屏指标...</Typography.Text>
          )}
        </Card>

        <Card title="晋升队列">
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space wrap>
              <Tag color="blue">先看依据，再决定通过或驳回</Tag>
              <Tag color="green">通过后再执行晋升入库</Tag>
              <Tag color="orange">如发现污染，可直接回退</Tag>
            </Space>
            <Row gutter={[12, 12]}>
              <Col xs={24} md={10}>
                <Segmented
                  options={PROMOTION_STATUS_VIEW_OPTIONS}
                  value={promotionStatusView}
                  onChange={(value) => setPromotionStatusView(value as PromotionStatusView)}
                  block
                />
              </Col>
              <Col xs={24} md={14}>
                <Space wrap>
                  <Select style={{ minWidth: 180 }} value={promotionTargetLayer} options={PROMOTION_TARGET_OPTIONS} onChange={setPromotionTargetLayer} />
                  <Select style={{ minWidth: 180 }} value={promotionCandidateType} options={PROMOTION_TYPE_OPTIONS} onChange={setPromotionCandidateType} />
                  <Select style={{ minWidth: 180 }} value={promotionSourceTable} options={SOURCE_TABLE_OPTIONS} onChange={setPromotionSourceTable} />
                </Space>
              </Col>
            </Row>
            <Row gutter={[12, 12]}>
              <Col xs={12} md={6}><Card size="small"><Statistic title="待处理" value={pendingCount} /></Card></Col>
              <Col xs={12} md={6}><Card size="small"><Statistic title="已通过待晋升" value={approvedCount} /></Card></Col>
              <Col xs={12} md={6}><Card size="small"><Statistic title="已晋升" value={promotedCount} /></Card></Col>
              <Col xs={12} md={6}><Card size="small"><Statistic title="已回退" value={rolledBackCount} /></Card></Col>
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
                expandable={{
                  expandedRowRender: (record) => (
                    <div style={{ display: 'grid', gap: 12 }}>
                      <div>
                        <Typography.Text strong>候选载荷</Typography.Text>
                        <pre style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>
                          {JSON.stringify(record.candidate_payload || {}, null, 2)}
                        </pre>
                      </div>
                      {record.promotion_trace ? (
                        <div>
                          <Typography.Text strong>晋升轨迹</Typography.Text>
                          <pre style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>{record.promotion_trace}</pre>
                        </div>
                      ) : null}
                      {record.source_table === 'audit_errors' && record.source_record_id ? (
                        <Space wrap>
                          <Button size="small" icon={<EyeOutlined />} onClick={() => openAuditErrorDetail(Number(record.source_record_id))}>
                            打开错因
                          </Button>
                          {(() => {
                            const linkedAudit = auditErrors.find((item) => item.id === Number(record.source_record_id));
                            if (!linkedAudit?.task_id) return null;
                            return (
                              <Button
                                size="small"
                                icon={<LinkOutlined />}
                                onClick={() =>
                                  openTaskResultPage(linkedAudit.task_id, linkedAudit.result_id, {
                                    sourceLabel: '晋升候选审核',
                                    candidateTitle: record.candidate_title,
                                    candidateType: getLabel(record.candidate_type, CANDIDATE_TYPE_LABELS),
                                    errorType: getLabel(linkedAudit.error_type, ERROR_TYPE_LABELS),
                                  })
                                }
                              >
                                打开任务结果
                              </Button>
                            );
                          })()}
                        </Space>
                      ) : null}
                    </div>
                  ),
                }}
              />
            )}
          </Space>
        </Card>

        <Card title="最近错因记录">
          <Space wrap style={{ marginBottom: 16 }}>
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
        </Card>

        <Card title="健康体检">
          {healthReport ? (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Row gutter={[16, 16]}>
                <Col xs={12} md={6}><Card size="small"><Statistic title="重复候选组" value={healthReport.summary.duplicate_candidate_groups} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title={`滞留候选（${healthReport.summary.stale_pending_days}天+）`} value={healthReport.summary.stale_pending_promotions} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="已回退" value={healthReport.summary.rolled_back_promotions} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="正式层失活" value={healthReport.summary.inactive_formal_total} /></Card></Col>
              </Row>
              <Row gutter={[16, 16]}>
                <Col xs={12} md={6}><Card size="small"><Statistic title="来源冲突组" value={healthReport.summary.source_conflict_groups} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="失活规则" value={healthReport.summary.inactive_rules} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="失活方法卡" value={healthReport.summary.inactive_method_cards} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="争议经验" value={healthReport.summary.experience_disputed_count} /></Card></Col>
              </Row>
              <div>
                <Typography.Text strong>正式层健康概览</Typography.Text>
                <div style={{ marginTop: 8 }}>
                  <Space wrap>
                    <Tag>{`失活规则: ${healthReport.formal_layer_health.inactive_rules}`}</Tag>
                    <Tag>{`失活方法卡: ${healthReport.formal_layer_health.inactive_method_cards}`}</Tag>
                    <Tag>{`经验候选: ${healthReport.formal_layer_health.experience_candidate_count}`}</Tag>
                    <Tag>{`经验争议: ${healthReport.formal_layer_health.experience_disputed_count}`}</Tag>
                  </Space>
                </div>
              </div>
              <div>
                <Typography.Text strong>重复候选组</Typography.Text>
                <Table rowKey={(record) => `${record.target_layer}-${record.candidate_type}-${record.candidate_title}`} size="small" style={{ marginTop: 8 }} dataSource={healthReport.duplicate_candidate_groups} columns={duplicateCandidateColumns} pagination={false} />
              </div>
              <div>
                <Typography.Text strong>滞留待处理候选</Typography.Text>
                <Table rowKey="id" size="small" style={{ marginTop: 8 }} dataSource={healthReport.stale_pending_promotions} columns={stalePendingColumns} pagination={false} />
              </div>
              <div>
                <Typography.Text strong>最近回退记录</Typography.Text>
                <Table rowKey="id" size="small" style={{ marginTop: 8 }} dataSource={healthReport.recent_rollbacks} columns={rollbackColumns} pagination={false} />
              </div>
              <div>
                <Typography.Text strong>来源冲突</Typography.Text>
                <Table rowKey={(record) => `${record.source_table}-${record.source_record_id}`} size="small" style={{ marginTop: 8 }} dataSource={healthReport.source_conflict_groups} columns={sourceConflictColumns} pagination={false} />
              </div>
            </Space>
          ) : (
            renderSecondaryPlaceholder(secondaryLoading, secondaryError, '正在补充健康体检报表...')
          )}
        </Card>

        <Card title="知识影响分析">
          {knowledgeImpact ? (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              <Row gutter={[16, 16]}>
                <Col xs={12} md={6}><Card size="small"><Statistic title="纳入跟踪任务" value={knowledgeImpact.summary.tracked_runs} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="纳入跟踪结果" value={knowledgeImpact.summary.tracked_results} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="7天命中" value={knowledgeImpact.summary.last_7d_hits} /></Card></Col>
                <Col xs={12} md={6}><Card size="small"><Statistic title="7天直接命中" value={knowledgeImpact.summary.last_7d_direct} /></Card></Col>
              </Row>
              <Row gutter={[16, 16]}>
                <Col xs={12}><Card size="small"><Statistic title="7天任务数" value={knowledgeImpact.summary.last_7d_runs} /></Card></Col>
                <Col xs={12}><Card size="small"><Statistic title="7天结果数" value={knowledgeImpact.summary.last_7d_results} /></Card></Col>
              </Row>
              <div>
                <Typography.Text strong>按知识层命中统计</Typography.Text>
                <Table rowKey="layer" size="small" style={{ marginTop: 8 }} dataSource={knowledgeImpact.layer_metrics} columns={knowledgeImpactLayerColumns} pagination={false} scroll={{ x: 1500 }} />
              </div>
              <div>
                <Typography.Text strong>近 7 天知识使用情况</Typography.Text>
                <Table rowKey="date" size="small" style={{ marginTop: 8 }} dataSource={knowledgeImpact.recent_activity} columns={knowledgeImpactRecentColumns} pagination={false} scroll={{ x: 900 }} />
              </div>
              <div>
                <Typography.Text strong>命中最多的对象</Typography.Text>
                <Table rowKey={(record) => `${record.layer}-${record.object_ref}`} size="small" style={{ marginTop: 8 }} dataSource={knowledgeImpact.top_objects || []} columns={knowledgeImpactObjectColumns} pagination={false} scroll={{ x: 1300 }} />
              </div>
            </Space>
          ) : (
            renderSecondaryPlaceholder(secondaryLoading, secondaryError, '正在补充知识影响报表...')
          )}
        </Card>
      </Space>

      <Drawer
        title={selectedAuditError ? `错因 #${selectedAuditError.id}` : '错因详情'}
        width={760}
        open={auditDrawerOpen}
        extra={selectedAuditError ? (
          <Button
            size="small"
            icon={<LinkOutlined />}
            onClick={() =>
              openTaskResultPage(selectedAuditError.task_id, selectedAuditError.result_id, {
                sourceLabel: '错因审核',
                errorType: getLabel(selectedAuditError.error_type, ERROR_TYPE_LABELS),
              })
            }
          >
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
              <pre style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>
                {JSON.stringify(knowledgeObjectDetail?.formal_detail || {}, null, 2)}
              </pre>
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
                  { title: 'ID', dataIndex: 'id', key: 'id', width: 72 },
                  { title: '候选标题', dataIndex: 'candidate_title', key: 'candidate_title' },
                  {
                    title: '目标层',
                    dataIndex: 'target_layer',
                    key: 'target_layer',
                    width: 150,
                    render: (value: string) => getLabel(value, TARGET_LAYER_LABELS),
                  },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    key: 'status',
                    width: 100,
                    render: (value: string) => renderStatusTag(value),
                  },
                  {
                    title: '来源',
                    key: 'source',
                    width: 160,
                    render: (_value, record) => (
                      <div>
                        <div>{getLabel(record.source_table, SOURCE_TABLE_LABELS)}</div>
                        <div style={{ color: '#64748b', fontSize: 12 }}>{record.source_record_id || '-'}</div>
                      </div>
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
                    width: 220,
                    render: (_value, record) => {
                      const linkedAuditId = getAuditErrorIdFromSource(record.source_table, record.source_record_id);
                      if (linkedAuditId === null) return '-';
                      return (
                        <Space wrap>
                          <Button size="small" icon={<EyeOutlined />} onClick={() => openAuditErrorDetail(linkedAuditId)}>
                            打开错因
                          </Button>
                          <Button size="small" icon={<LinkOutlined />} onClick={() => openTaskResultFromPromotionSource(record)}>
                            打开任务结果
                          </Button>
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
