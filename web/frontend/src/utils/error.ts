/**
 * API 错误信息提取工具
 *
 * 统一处理 axios 错误，提取用户友好的提示信息：
 * - 4xx 错误：返回后端 detail（用户操作错误，如"密码错误"）
 * - 5xx 错误：返回通用提示（不暴露数据库/SQL 等敏感信息）
 * - 网络错误：返回网络提示
 * - 超时错误：返回超时提示
 */

interface AxiosLikeError {
  response?: {
    status?: number;
    data?: { detail?: string };
  };
  code?: string;
  message?: string;
}

export function getErrorMessage(err: unknown, fallback = '操作失败'): string {
  const axiosErr = err as AxiosLikeError;

  // 网络错误（无 response）
  if (!axiosErr?.response) {
    if (axiosErr?.code === 'ECONNABORTED' || axiosErr?.message?.includes('timeout')) {
      return '请求超时，请稍后重试';
    }
    if (axiosErr?.code === 'ERR_NETWORK') {
      return '网络连接失败，请检查网络';
    }
    return fallback;
  }

  const status = axiosErr.response.status || 0;
  const detail = axiosErr.response.data?.detail;

  // 5xx 服务器错误：不暴露后端详情
  if (status >= 500) {
    return '服务器错误，请稍后重试';
  }

  // 413 文件太大
  if (status === 413) {
    return '文件太大，请减小文件后重试';
  }

  // 4xx 客户端错误：使用后端返回的 detail（这些是故意给用户看的）
  if (detail) {
    return detail;
  }

  return fallback;
}
