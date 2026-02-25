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

/** 单条匹配结果 */
export interface MatchResult {
  id: string;
  index: number;
  bill_name: string;
  bill_description: string;
  bill_unit: string;
  bill_quantity: number | null;
  specialty: string;
  quotas: QuotaItem[] | null;
  confidence: number;
  match_source: string;
  explanation: string;
  candidates_count: number;
  review_status: ReviewStatus;
  corrected_quotas: QuotaItem[] | null;
  review_note: string;
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
  };
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
