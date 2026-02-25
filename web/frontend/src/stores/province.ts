/**
 * 定额库省份列表全局缓存
 *
 * 多个页面（CreatePage、Consult、ExperienceManage）都需要省份列表，
 * 用全局 store 只请求一次，避免重复请求。
 */

import { create } from 'zustand';
import api from '../services/api';

interface ProvinceStore {
  /** 所有定额库名列表（如 ["北京市建设工程施工消耗量标准(2024)", ...] */
  provinces: string[];
  /** 是否正在加载 */
  loading: boolean;
  /** 是否已加载过（避免重复请求） */
  loaded: boolean;
  /** 加载省份列表（有缓存则跳过，force=true 强制刷新） */
  fetchProvinces: (force?: boolean) => Promise<string[]>;
}

export const useProvinceStore = create<ProvinceStore>((set, get) => ({
  provinces: [],
  loading: false,
  loaded: false,

  fetchProvinces: async (force = false) => {
    const state = get();
    // 已有缓存且不强制刷新，直接返回
    if (state.loaded && !force) {
      return state.provinces;
    }
    // 正在加载中，等待当前请求完成（避免并发重复请求）
    if (state.loading) {
      return new Promise<string[]>((resolve) => {
        const check = setInterval(() => {
          const s = get();
          if (!s.loading) {
            clearInterval(check);
            resolve(s.provinces);
          }
        }, 100);
      });
    }

    set({ loading: true });
    try {
      const { data } = await api.get<{ provinces: string[] }>('/provinces');
      const list = data.provinces || [];
      set({ provinces: list, loaded: true, loading: false });
      return list;
    } catch {
      set({ loading: false });
      return state.provinces; // 失败时返回旧数据
    }
  },
}));
