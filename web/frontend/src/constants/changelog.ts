/**
 * 版本更新日志
 *
 * 每次发版往数组最前面加一条即可。
 * APP_VERSION 保持和 lzc-manifest.yml 中的 version 一致。
 */

export const APP_VERSION = '0.1.44';

export interface ChangelogEntry {
  version: string;
  date: string;       // YYYY-MM-DD
  changes: string[];  // 每条一句话
}

export const CHANGELOG: ChangelogEntry[] = [
  {
    version: '0.1.44',
    date: '2026-03-01',
    changes: [
      'v0.1.44 更新',
    ],
  },
  {
    version: '0.1.43',
    date: '2026-03-01',
    changes: [
      'v0.1.43 更新',
    ],
  },
  {
    version: '0.1.42',
    date: '2026-03-01',
    changes: [
      'v0.1.42 更新',
    ],
  },
  {
    version: '0.1.41',
    date: '2026-03-01',
    changes: [
      'v0.1.41 更新',
    ],
  },
  {
    version: '0.1.40',
    date: '2026-03-01',
    changes: [
      'v0.1.40 更新',
    ],
  },
  {
    version: '0.1.39',
    date: '2026-03-01',
    changes: [
      'v0.1.39 更新',
    ],
  },
  {
    version: '0.1.38',
    date: '2026-03-01',
    changes: [
      'v0.1.38 更新',
    ],
  },
  {
    version: '0.1.37',
    date: '2026-03-01',
    changes: [
      'v0.1.37 更新',
    ],
  },
  {
    version: '0.1.36',
    date: '2026-03-01',
    changes: [
      'v0.1.36 更新',
    ],
  },
  {
    version: '0.1.35',
    date: '2026-03-01',
    changes: [
      'v0.1.35 更新',
    ],
  },
  {
    version: '0.1.34',
    date: '2026-03-01',
    changes: [
      'v0.1.34 更新',
    ],
  },
  {
    version: '0.1.33',
    date: '2026-03-01',
    changes: [
      'v0.1.33 更新',
    ],
  },
  {
    version: '0.1.32',
    date: '2026-03-01',
    changes: [
      'v0.1.32 更新',
    ],
  },
  {
    version: '0.1.31',
    date: '2026-03-01',
    changes: [
      'v0.1.31 更新',
    ],
  },
  {
    version: '0.1.30',
    date: '2026-02-28',
    changes: [
      'v0.1.30 更新',
    ],
  },
  {
    version: '0.1.31',
    date: '2026-02-28',
    changes: [
      'v0.1.31 更新',
    ],
  },
  {
    version: '0.1.30',
    date: '2026-02-28',
    changes: [
      'v0.1.30 更新',
    ],
  },
  {
    version: '0.1.30',
    date: '2026-02-28',
    changes: [
      'v0.1.30 更新',
    ],
  },
  {
    version: '0.1.30',
    date: '2026-02-28',
    changes: [
      'v0.1.30 更新',
    ],
  },
  {
    version: '0.1.29',
    date: '2026-02-28',
    changes: [
      '圆形风管/阀门周长参数修复（φ直径自动转周长）',
      '管理页面切回浏览器自动刷新数据',
      'ChromaDB索引格式不兼容时自动重建',
      '经验库删除同步清理向量索引',
    ],
  },
  {
    version: '0.1.26',
    date: '2026-02-27',
    changes: [
      'CI/CD自动部署配置',
      'manifest改回使用ACR镜像',
    ],
  },
  {
    version: '0.1.25',
    date: '2026-02-26',
    changes: [
      'Web端大模型在线配置管理',
      'Token自动刷新机制',
    ],
  },
  {
    version: '0.1.24',
    date: '2026-02-25',
    changes: [
      'Jarvis类别不匹配自动清空错配定额',
      'output_writer列自适应',
    ],
  },
];
