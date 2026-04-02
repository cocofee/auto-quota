/**
 * 全局类型定义
 *
 * 和后端 schemas/ 对应的 TypeScript 类型。
 * 前端所有组件、服务、状态管理都引用这里的类型。
 */

// ============================================================
// 认证相关
// ============================================================

/** 登录/注册请求 */
export interface LoginRequest {
  email: string;
  password: string;
}

export interface RegisterRequest {
  email: string;
  password: string;
  nickname?: string;
  invite_code: string;  // 邀请码（后端必填）
}

/** 登录成功返回的 Token */
export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

/** 用户信息 */
export interface UserInfo {
  id: string;
  email: string;
  nickname: string;
  is_active: boolean;
  is_admin: boolean;
  quota_balance: number;  // 额度余额（条）
  created_at: string;
}

// ============================================================
// 任务相关
// ============================================================

/** 匹配模式 */
export type MatchMode = 'search' | 'agent';

/** 任务状态 */
export type TaskStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

/** 任务信息（对应后端 TaskResponse） */
export interface TaskInfo {
  id: string;
  name: string;
  original_filename: string;
  mode: MatchMode;
  province: string;
  sheet: string | null;
  limit_count: number | null;
  use_experience: boolean;
  agent_llm: string | null;
  status: TaskStatus;
  progress: number;
  progress_current: number;  // 当前处理到第几条清单（从1开始）
  progress_message: string;
  error_message: string | null;
  stats: TaskStats | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  // 反馈上传相关
  feedback_path: string | null;
  feedback_uploaded_at: string | null;
  feedback_stats: { total: number; learned: number } | null;
  // 用户信息（管理员视图）
  username: string | null;
}

/** 任务统计（run() 返回的 stats） */
export interface TaskStats {
  total: number;
  matched: number;
  high_conf: number;
  mid_conf: number;
  low_conf: number;
  exp_hits: number;
  elapsed: number;
  [key: string]: unknown; // 允许额外字段
}

/** 任务列表分页响应 */
export interface TaskListResponse {
  items: TaskInfo[];
  total: number;
  total_bills: number;  // 所有任务的清单条数合计
  page: number;
  size: number;
}

// ============================================================
// 匹配结果相关
// ============================================================

/** 定额项 */
export interface QuotaItem {
  quota_id: string;
  name: string;
  unit: string;
  param_score: number | null;
  rerank_score: number | null;
  source: string;
}

/** 审核状态 */
export type ReviewStatus = 'pending' | 'confirmed' | 'corrected';
export type ReviewRisk = 'low' | 'medium' | 'high';
export type LightStatus = 'green' | 'yellow' | 'red';
export type OpenClawReviewStatus = 'pending' | 'reviewed' | 'applied' | 'rejected';
export type OpenClawReviewConfirmStatus = 'pending' | 'approved' | 'rejected';
export type OpenClawDecisionType =
  | 'agree'
  | 'override_within_candidates'
  | 'retry_search_then_select'
  | 'candidate_pool_insufficient'
  | 'abstain';
export type OpenClawErrorStage = 'retriever' | 'ranker' | 'arbiter' | 'final_validator' | 'unknown';
export type OpenClawErrorType =
  | 'wrong_family'
  | 'wrong_param'
  | 'wrong_book'
  | 'synonym_gap'
  | 'low_confidence_override'
  | 'missing_candidate'
  | 'unknown';

/** 单条匹配结果 */
export interface MatchResult {
  id: string;
  index: number;
  bill_code: string;
  bill_name: string;
  bill_description: string;
  bill_unit: string;
  bill_quantity: number | null;
  bill_unit_price: number | null;  // 综合单价
  bill_amount: number | null;      // 金额
  specialty: string;
  sheet_name: string;   // 所属Sheet页名（如"给排水"、"电气"）
  section: string;      // 所属分部工程名（如"给水工程"、"强电系统"）
  quotas: QuotaItem[] | null;
  alternatives: Record<string, unknown>[] | null;  // 备选定额（top-N）
  confidence: number;
  confidence_score: number;
  review_risk: ReviewRisk;
  light_status: LightStatus;
  match_source: string;
  explanation: string;
  candidates_count: number;
  is_measure_item: boolean;  // 是否措施项
  review_status: ReviewStatus;
  corrected_quotas: QuotaItem[] | null;
  review_note: string;
  openclaw_review_status: OpenClawReviewStatus;
  openclaw_suggested_quotas: QuotaItem[] | null;
  openclaw_review_note: string;
  openclaw_review_confidence: number | null;
  openclaw_review_actor: string;
  openclaw_review_time: string | null;
  openclaw_decision_type?: OpenClawDecisionType | null;
  openclaw_error_stage?: OpenClawErrorStage | null;
  openclaw_error_type?: OpenClawErrorType | null;
  openclaw_retry_query?: string;
  openclaw_reason_codes?: string[] | null;
  openclaw_review_payload?: Record<string, unknown> | null;
  openclaw_review_confirm_status: OpenClawReviewConfirmStatus;
  openclaw_review_confirmed_by: string;
  openclaw_review_confirm_time: string | null;
  human_feedback_payload?: Record<string, unknown> | null;
  created_at: string;
}

/** 结果列表响应（含统计摘要） */
export interface ResultListResponse {
  items: MatchResult[];
  total: number;
  summary: {
    total: number;
    high_confidence: number;
    mid_confidence: number;
    low_confidence: number;
    no_match: number;
    confirmed: number;   // 已确认条数（审核后）
    corrected: number;   // 已纠正条数（审核后）
    pending: number;     // 待审核条数
  };
}

export type OpenClawReviewJobStatus = 'ready' | 'running' | 'completed' | 'failed';
export type OpenClawReviewJobScope = 'need_review' | 'all_pending' | 'yellow_red_pending';

export interface OpenClawReviewJob {
  id: string;
  source_task_id: string;
  status: OpenClawReviewJobStatus;
  scope: OpenClawReviewJobScope;
  requested_by: string;
  note: string;
  total_results: number;
  pending_results: number;
  reviewable_results: number;
  green_count: number;
  yellow_count: number;
  red_count: number;
  reviewed_pending_count: number;
  summary?: Record<string, unknown> | null;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface OpenClawAutoReviewResponse {
  result_id: string;
  source_task_id: string;
  review_job_id?: string | null;
  status: 'drafted' | 'skipped';
  decision_type?: OpenClawDecisionType | null;
  openclaw_review_status: OpenClawReviewStatus;
  reviewable: boolean;
  note: string;
}

export interface OpenClawBatchAutoReviewResponse {
  review_job_id?: string | null;
  source_task_id: string;
  scope: OpenClawReviewJobScope;
  total_candidates: number;
  drafted_count: number;
  skipped_count: number;
  failed_count: number;
  processed_result_ids: string[];
}

// ============================================================
// SSE 进度事件
// ============================================================

/** 进度推送数据 */
export interface ProgressData {
  status: TaskStatus;
  progress: number;
  message: string;
  stats: TaskStats | null;
  error: string | null;
}

// ============================================================
// 通用
// ============================================================

/** API 错误响应 */
export interface ApiError {
  detail: string;
}

// ============================================================
// 额度管理相关
// ============================================================

/** 额度余额信息 */
export interface QuotaBalance {
  balance: number;          // 剩余额度（条）
  total_used: number;       // 已使用总量
  total_purchased: number;  // 已购买总量
}

/** 额度包 */
export interface QuotaPackage {
  id: string;      // 如 pkg_500
  name: string;    // 如 "500条额度包"
  quota: number;   // 额度条数
  price: number;   // 价格（元）
}

/** 额度变动记录 */
export interface QuotaLogItem {
  id: number;
  change_type: string;     // register_gift/task_deduct/purchase/admin_adjust
  amount: number;          // 正=增加，负=扣减
  balance_after: number;   // 变动后余额
  ref_id: string | null;
  note: string;
  created_at: string;
}

/** 额度变动记录列表响应 */
export interface QuotaLogListResponse {
  items: QuotaLogItem[];
  total: number;
  page: number;
  size: number;
}

/** 创建订单响应 */
export interface CreateOrderResponse {
  order_id: string;
  out_trade_no: string;
  pay_url: string;
}

/** 订单信息 */
export interface OrderInfo {
  id: string;
  out_trade_no: string;
  package_name: string;
  package_quota: number;
  amount: number;
  pay_type: string;
  status: string;        // pending/paid/expired
  trade_no: string | null;
  created_at: string;
  paid_at: string | null;
}

/** 订单列表响应 */
export interface OrderListResponse {
  items: OrderInfo[];
  total: number;
  page: number;
  size: number;
  total_amount: number;
}
