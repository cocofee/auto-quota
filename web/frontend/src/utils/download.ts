import api from '../services/api';
import { getErrorMessage } from './error';

interface AxiosBlobLikeError {
  response?: {
    status?: number;
    data?: unknown;
  };
  code?: string;
  message?: string;
}

function triggerBrowserDownload(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.setAttribute('download', filename);
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.URL.revokeObjectURL(url);
}

export async function getDownloadErrorMessage(err: unknown, fallback = '下载失败'): Promise<string> {
  const axiosErr = err as AxiosBlobLikeError;
  const data = axiosErr?.response?.data;

  if (data instanceof Blob) {
    try {
      const text = (await data.text()).trim();
      if (text) {
        try {
          const parsed = JSON.parse(text) as { detail?: unknown };
          if (typeof parsed.detail === 'string' && parsed.detail.trim()) {
            return parsed.detail.trim();
          }
        } catch {
          if ((axiosErr.response?.status || 0) < 500) {
            return text;
          }
        }
      }
    } catch {
      // Fall through to generic error parsing.
    }
  }

  return getErrorMessage(err, fallback);
}

export async function downloadTaskResultExcel(taskId: string, filename: string): Promise<void> {
  const endpoints = [
    `/tasks/${taskId}/export-final?materials=true`,
    `/tasks/${taskId}/export?materials=true`,
  ];

  let lastError: unknown = null;

  for (const endpoint of endpoints) {
    try {
      const response = await api.get(endpoint, { responseType: 'blob' });
      triggerBrowserDownload(new Blob([response.data]), filename);
      return;
    } catch (err) {
      lastError = err;
    }
  }

  const message = await getDownloadErrorMessage(lastError, '下载失败');
  throw new Error(message);
}
