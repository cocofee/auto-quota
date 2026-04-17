import api from '../services/api.ts';
import { getErrorMessage } from './error.ts';

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

function isExportFinalUnsupported(err: unknown): boolean {
  const status = (err as AxiosBlobLikeError)?.response?.status;
  return status === 404 || status === 405 || status === 501;
}

export async function getDownloadErrorMessage(err: unknown, fallback = '涓嬭浇澶辫触'): Promise<string> {
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
  try {
    const response = await api.get(`/tasks/${taskId}/export-final?materials=true`, { responseType: 'blob' });
    triggerBrowserDownload(new Blob([response.data]), filename);
    return;
  } catch (err) {
    if (!isExportFinalUnsupported(err)) {
      const message = await getDownloadErrorMessage(err, '涓嬭浇澶辫触');
      throw new Error(message);
    }
  }

  try {
    const response = await api.get(`/tasks/${taskId}/export?materials=true`, { responseType: 'blob' });
    triggerBrowserDownload(new Blob([response.data]), filename);
    return;
  } catch (err) {
    const message = await getDownloadErrorMessage(err, '涓嬭浇澶辫触');
    throw new Error(message);
  }
}
