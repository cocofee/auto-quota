/**
 * 版本更新日志
 *
 * 每次发版往数组最前面加一条即可。
 * APP_VERSION 保持和 lzc-manifest.yml 中的 version 一致。
 *
 * 分类规则：
 * - type: 'user'  → 所有用户可见（定额匹配、经验库、准确率等用户关心的改动）
 * - type: 'admin' → 仅管理员可见（部署、重构、CI/CD、内部优化等技术改动）
 */

export const APP_VERSION = '0.1.55';

/** 更新类型：user=用户可见, admin=仅管理员可见 */
export type ChangeType = 'user' | 'admin';

/** 单条更新 */
export interface ChangeItem {
  type: ChangeType;
  text: string;
}

export interface ChangelogEntry {
  version: string;
  date: string;       // YYYY-MM-DD
  changes: ChangeItem[];
}

export const CHANGELOG: ChangelogEntry[] = [
  {
    version: '0.1.55',
    date: '2026-03-01',
    changes: [
      { type: 'admin', text: 'v0.1.55 更新' },
    ],
  },
  {
    version: '0.1.54',
    date: '2026-03-01',
    changes: [
      { type: 'user', text: '经验库验证增强，参数验证relaxed模式（容错更好）' },
      { type: 'admin', text: '自进化 — 从13222张经验卡片挖掘90+同义词' },
      { type: 'admin', text: '清理旧版lpk安装包（已加入.gitignore）' },
    ],
  },
  {
    version: '0.1.29',
    date: '2026-02-28',
    changes: [
      { type: 'user', text: '圆形风管/阀门周长参数修复（φ直径自动转周长）' },
      { type: 'admin', text: '管理页面切回浏览器自动刷新数据' },
      { type: 'admin', text: 'ChromaDB索引格式不兼容时自动重建' },
      { type: 'admin', text: '经验库删除同步清理向量索引' },
    ],
  },
  {
    version: '0.1.26',
    date: '2026-02-27',
    changes: [
      { type: 'admin', text: 'CI/CD自动部署配置' },
      { type: 'admin', text: 'manifest改回使用ACR镜像' },
    ],
  },
  {
    version: '0.1.25',
    date: '2026-02-26',
    changes: [
      { type: 'admin', text: 'Web端大模型在线配置管理' },
      { type: 'admin', text: 'Token自动刷新机制' },
    ],
  },
  {
    version: '0.1.24',
    date: '2026-02-25',
    changes: [
      { type: 'user', text: 'Jarvis类别不匹配时自动清空错配定额（减少误匹配）' },
      { type: 'admin', text: 'output_writer列自适应' },
    ],
  },
];
