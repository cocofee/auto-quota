/**
 * API client
 *
 * Auth uses HttpOnly cookies from the backend.
 * No token is persisted in frontend storage.
 */

import axios from 'axios';
import type { AxiosError, InternalAxiosRequestConfig } from 'axios';
import type { ApiError, TokenResponse } from '../types';
import { repairMojibakeData } from '../utils/text';

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
});

// Backward-compatible no-op helpers for existing imports.
export function getAccessToken(): string | null {
  return null;
}

export function getRefreshToken(): string | null {
  return null;
}

export function saveTokens(_tokens: TokenResponse) {}

export function clearTokens() {}

interface RetryableRequestConfig extends InternalAxiosRequestConfig {
  _retry?: boolean;
}

let isRefreshing = false;
let pendingRequests: Array<{
  resolve: () => void;
  reject: (error: unknown) => void;
}> = [];

const AUTH_PATHS = ['/auth/login', '/auth/register', '/auth/refresh', '/auth/me'];

api.interceptors.response.use(
  (response) => {
    const contentType = String(response.headers?.['content-type'] || '');
    if (contentType.includes('application/json')) {
      response.data = repairMojibakeData(response.data, true);
    }
    return response;
  },
  async (error: AxiosError<ApiError>) => {
    const originalRequest = error.config as RetryableRequestConfig | undefined;
    if (!originalRequest) {
      return Promise.reject(error);
    }

    const isAuthPath = AUTH_PATHS.some((p) => originalRequest.url?.includes(p));
    const shouldTryRefresh =
      error.response?.status === 401 && !isAuthPath && !originalRequest._retry;
    if (!shouldTryRefresh) {
      return Promise.reject(error);
    }

    originalRequest._retry = true;

    if (isRefreshing) {
      return new Promise((resolve, reject) => {
        pendingRequests.push({
          resolve: () => resolve(api(originalRequest)),
          reject,
        });
      });
    }

    isRefreshing = true;
    try {
      await api.post<TokenResponse>('/auth/refresh');
      pendingRequests.forEach((req) => req.resolve());
      pendingRequests = [];
      return api(originalRequest);
    } catch (refreshError) {
      pendingRequests.forEach((req) => req.reject(refreshError));
      pendingRequests = [];
      clearTokens();
      window.location.href = '/login';
      return Promise.reject(refreshError);
    } finally {
      isRefreshing = false;
    }
  },
);

export default api;
