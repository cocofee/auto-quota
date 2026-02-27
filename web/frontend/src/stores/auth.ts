/**
 * Auth state store (Zustand)
 */

import { create } from 'zustand';
import type { UserInfo } from '../types';
import api, { clearTokens } from '../services/api';

interface AuthState {
  user: UserInfo | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, nickname: string | undefined, invite_code: string) => Promise<void>;
  logout: () => Promise<void>;
  fetchUser: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  loading: true,

  login: async (email, password) => {
    await api.post('/auth/login', { email, password });
    const userRes = await api.get<UserInfo>('/auth/me');
    set({ user: userRes.data, loading: false });
  },

  register: async (email, password, nickname, invite_code) => {
    await api.post('/auth/register', { email, password, nickname, invite_code });
    await api.post('/auth/login', { email, password });
    const userRes = await api.get<UserInfo>('/auth/me');
    set({ user: userRes.data, loading: false });
  },

  logout: async () => {
    try {
      await api.post('/auth/logout');
    } catch {
      // Ignore backend revoke failures and clear local auth state.
    } finally {
      clearTokens();
      set({ user: null, loading: false });
    }
  },

  fetchUser: async () => {
    set({ loading: true });
    try {
      const { data } = await api.get<UserInfo>('/auth/me');
      set({ user: data, loading: false });
    } catch {
      clearTokens();
      set({ user: null, loading: false });
    }
  },
}));
