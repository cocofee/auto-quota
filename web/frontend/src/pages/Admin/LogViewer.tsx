/**
 * 系统日志查看页面（管理员专属）
 *
 * 改进：
 * 1. 结构化Table展示（时间、级别徽章、来源、消息）
 * 2. 自动刷新（5秒轮询）+ 跟踪最新
 * 3. 文件列表按月折叠，大文件颜色标记
 * 4. 级别筛选用按钮组，带数量
 * 5. 搜索结果高亮命中词
 * 6. 时间范围快捷筛选
 * 7. 相同错误合并显示
 * 8. 顶部错误统计看板
 * 9. 点击展开完整堆栈
 * 10. 顶部Tab快速切换Web/Celery
 */

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import {
  Card, Input, Select, Button, Space, Tag, Empty, Spin,
  Table, Modal, Switch, Badge, Segmented,
} from 'antd';
import {
  ReloadOutlined, FileTextOutlined, SearchOutlined, FullscreenOutlined,
  RightOutlined, DownOutlined, VerticalAlignBottomOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

// ============================================================
// 类型定义
// ============================================================

interface LogFile {
  filename: string;
  size: number;
  size_display: string;
  modified_at: number;
}

interface LogContent {
  filename: string;
  total_lines: number;
  returned_lines: number;
  content: string;
}

// 解析后的日志条目（多行堆栈合并为一条）
interface LogEntry {
  key: number;
  time: string;
  level: string;
  source: string;
  message: string;
  detail: string;   // 堆栈/续行内容
  raw: string;
}

// 合并后的日志条目（相同错误归组）
interface MergedEntry extends LogEntry {
  count: number;
  firstTime: string;
  lastTime: string;
}

// ============================================================
// 日志解析
// ============================================================

/** 解析日志文本 → 结构化条目
 *  - 匹配loguru格式：2026-03-18 08:18:54.702 | ERROR | module:line - message
 *  - 不匹配的行（堆栈/traceback）附加到上一条的detail
 */
function parseLogContent(content: string): LogEntry[] {
  if (!content) return [];
  const rawLines = content.split('\n');
  const entries: LogEntry[] = [];
  // loguru格式正则
  const pattern = /^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}[.\d]*)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*-\s*(.*)$/;

  for (const line of rawLines) {
    if (!line.trim()) continue;
    const match = line.match(pattern);
    if (match) {
      entries.push({
        key: entries.length,
        time: match[1].trim(),
        level: match[2].trim().toUpperCase(),
        source: match[3].trim(),
        message: match[4].trim(),
        detail: '',
        raw: line,
      });
    } else if (entries.length > 0) {
      // 续行（堆栈跟踪等），附加到上一条
      const prev = entries[entries.length - 1];
      prev.detail += (prev.detail ? '\n' : '') + line;
    } else {
      // 文件开头的非标准行
      entries.push({
        key: entries.length,
        time: '',
        level: '',
        source: '',
        message: line,
        detail: '',
        raw: line,
      });
    }
  }
  return entries;
}

/** 合并相同错误（按级别+消息归一化分组） */
function mergeEntries(entries: LogEntry[]): MergedEntry[] {
  const groups = new Map<string, MergedEntry>();
  const order: string[] = []; // 保持首次出现顺序

  for (const entry of entries) {
    // 归一化：去掉IP地址、端口号、具体数字，只保留消息骨架
    const normalized = entry.message
      .replace(/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/g, '*')
      .replace(/:\d{2,5}/g, ':*')
      .replace(/\d{4}-\d{2}-\d{2}/g, '*');
    const groupKey = `${entry.level}|${normalized}`;

    if (groups.has(groupKey)) {
      const g = groups.get(groupKey)!;
      g.count++;
      g.lastTime = entry.time || g.lastTime;
      // 保留最新的detail（堆栈信息）
      if (entry.detail && !g.detail) g.detail = entry.detail;
    } else {
      const merged: MergedEntry = {
        ...entry,
        count: 1,
        firstTime: entry.time,
        lastTime: entry.time,
      };
      groups.set(groupKey, merged);
      order.push(groupKey);
    }
  }

  return order.map(k => groups.get(k)!);
}

/** 从时间字符串提取小时数（用于时间范围筛选） */
function getHoursAgo(timeStr: string): number | null {
  if (!timeStr) return null;
  try {
    const t = new Date(timeStr.replace(' ', 'T'));
    if (isNaN(t.getTime())) return null;
    return (Date.now() - t.getTime()) / 3600000;
  } catch {
    return null;
  }
}

// ============================================================
// 文件列表分组
// ============================================================

interface FileGroup {
  month: string;       // 如 "2026-03"
  label: string;       // 如 "2026年3月"
  files: LogFile[];
  isCurrent: boolean;  // 是否当月
}

/** 按月分组文件列表 */
function groupFilesByMonth(files: LogFile[]): FileGroup[] {
  const groups = new Map<string, LogFile[]>();

  for (const f of files) {
    // 从文件名提取日期：web_2026-03-18.log → 2026-03
    const dateMatch = f.filename.match(/(\d{4}-\d{2})/);
    const month = dateMatch ? dateMatch[1] : '其他';
    if (!groups.has(month)) groups.set(month, []);
    groups.get(month)!.push(f);
  }

  const now = new Date();
  const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;

  // 按月份倒序排列
  const sorted = Array.from(groups.entries()).sort((a, b) => b[0].localeCompare(a[0]));

  return sorted.map(([month, monthFiles]) => {
    let label: string;
    if (month === '其他') {
      label = '其他';
    } else {
      const [y, m] = month.split('-');
      label = `${y}年${parseInt(m)}月`;
    }
    return {
      month,
      label,
      files: monthFiles,
      isCurrent: month === currentMonth,
    };
  });
}

/** 文件大小颜色标记 */
function fileSizeColor(size: number): string {
  if (size > 1024 * 1024) return '#d4380d';   // >1MB 红橙色
  if (size > 512 * 1024) return '#d48806';     // >500KB 黄色
  return '#8c8c8c';                             // 正常灰色
}

// ============================================================
// 搜索高亮
// ============================================================

/** 在文本中高亮关键词 */
function highlightText(text: string, keyword: string): React.ReactNode {
  if (!keyword || !text) return text;
  const escaped = keyword.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const parts = text.split(new RegExp(`(${escaped})`, 'gi'));
  if (parts.length <= 1) return text;
  return (
    <>
      {parts.map((part, i) =>
        part.toLowerCase() === keyword.toLowerCase()
          ? <mark key={i} style={{ background: '#ffe58f', padding: '0 1px', borderRadius: 2 }}>{part}</mark>
          : part
      )}
    </>
  );
}

// ============================================================
// 页面组件
// ============================================================

export default function LogViewer() {
  // 文件列表
  const [files, setFiles] = useState<LogFile[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [selectedFile, setSelectedFile] = useState('');

  // 日志内容
  const [logContent, setLogContent] = useState<LogContent | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);

  // 搜索参数
  const [keyword, setKeyword] = useState('');
  const [lineCount, setLineCount] = useState(200);
  const [levelFilter, setLevelFilter] = useState<string>('');

  // 功能开关
  const [autoRefresh, setAutoRefresh] = useState(false);
  const [mergeMode, setMergeMode] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [timeRange, setTimeRange] = useState<string>('');  // '30m' | '1h' | 'today' | ''

  // 文件列表折叠状态（按月份key）
  const [collapsedMonths, setCollapsedMonths] = useState<Set<string>>(new Set());

  // 文件类型Tab：all / web / celery
  const [fileTab, setFileTab] = useState<string>('all');

  // 自动刷新定时器
  const refreshTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  // Table容器引用（用于滚到底部）
  const tableRef = useRef<HTMLDivElement>(null);

  // ============================================================
  // 数据加载
  // ============================================================

  const loadFiles = useCallback(() => {
    setLoadingFiles(true);
    api.get('/admin/logs/files')
      .then((res) => {
        const items: LogFile[] = res.data.items || [];
        setFiles(items);
        setSelectedFile((prev) => {
          if (!prev && items.length > 0) return items[0].filename;
          return prev;
        });
      })
      .catch(() => {})
      .finally(() => setLoadingFiles(false));
  }, []);

  useEffect(() => { loadFiles(); }, [loadFiles]);

  const loadContent = useCallback(() => {
    if (!selectedFile) return;
    setLoadingContent(true);
    api.get('/admin/logs/read', {
      params: { filename: selectedFile, lines: lineCount, keyword: keyword || undefined },
    })
      .then((res) => setLogContent(res.data))
      .catch(() => setLogContent(null))
      .finally(() => setLoadingContent(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFile, lineCount]);

  useEffect(() => { loadContent(); }, [loadContent]);

  // 自动刷新（5秒轮询）
  useEffect(() => {
    if (autoRefresh && selectedFile) {
      refreshTimer.current = setInterval(loadContent, 5000);
    }
    return () => {
      if (refreshTimer.current) clearInterval(refreshTimer.current);
    };
  }, [autoRefresh, selectedFile, loadContent]);

  // ============================================================
  // 日志解析 + 过滤
  // ============================================================

  // 全部解析后的条目
  const allEntries = useMemo(() => {
    if (!logContent) return [];
    return parseLogContent(logContent.content);
  }, [logContent]);

  // 各级别数量统计
  const levelCounts = useMemo(() => {
    const counts: Record<string, number> = { ERROR: 0, WARNING: 0, INFO: 0, DEBUG: 0 };
    for (const e of allEntries) {
      if (e.level in counts) counts[e.level]++;
    }
    return counts;
  }, [allEntries]);

  // 最近一条错误
  const latestError = useMemo(() => {
    for (let i = allEntries.length - 1; i >= 0; i--) {
      if (allEntries[i].level === 'ERROR') return allEntries[i];
    }
    return null;
  }, [allEntries]);

  // 应用过滤：级别 + 时间范围
  const filteredEntries = useMemo(() => {
    let result = allEntries;

    // 级别过滤
    if (levelFilter) {
      result = result.filter(e => e.level === levelFilter || !e.level);
    }

    // 时间范围过滤
    if (timeRange) {
      let maxHours = Infinity;
      if (timeRange === '30m') maxHours = 0.5;
      else if (timeRange === '1h') maxHours = 1;
      else if (timeRange === 'today') {
        // 今天：从0点到现在
        const now = new Date();
        maxHours = now.getHours() + now.getMinutes() / 60;
      }

      if (maxHours < Infinity) {
        result = result.filter(e => {
          const h = getHoursAgo(e.time);
          return h === null || h <= maxHours;
        });
      }
    }

    return result;
  }, [allEntries, levelFilter, timeRange]);

  // 最终展示数据（可能合并）
  const displayEntries = useMemo(() => {
    if (mergeMode) return mergeEntries(filteredEntries);
    return filteredEntries.map(e => ({ ...e, count: 1, firstTime: e.time, lastTime: e.time }));
  }, [filteredEntries, mergeMode]);

  // ============================================================
  // 文件列表处理
  // ============================================================

  // 按Tab过滤文件
  const tabFilteredFiles = useMemo(() => {
    if (fileTab === 'web') return files.filter(f => f.filename.startsWith('web_'));
    if (fileTab === 'celery') return files.filter(f => f.filename.startsWith('celery_'));
    return files;
  }, [files, fileTab]);

  // 按月分组
  const fileGroups = useMemo(() => groupFilesByMonth(tabFilteredFiles), [tabFilteredFiles]);

  // 默认折叠非当月
  useEffect(() => {
    const nonCurrent = fileGroups.filter(g => !g.isCurrent).map(g => g.month);
    setCollapsedMonths(new Set(nonCurrent));
  }, [fileGroups.length]); // eslint-disable-line react-hooks/exhaustive-deps

  const toggleMonth = useCallback((month: string) => {
    setCollapsedMonths(prev => {
      const next = new Set(prev);
      if (next.has(month)) next.delete(month); else next.add(month);
      return next;
    });
  }, []);

  // 滚到底部
  const scrollToBottom = useCallback(() => {
    if (tableRef.current) {
      const tbody = tableRef.current.querySelector('.ant-table-body');
      if (tbody) tbody.scrollTop = tbody.scrollHeight;
    }
  }, []);

  // 文件类型标签
  const fileTypeTag = (filename: string) => {
    if (filename.startsWith('web_')) return <Tag color="blue" style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>Web</Tag>;
    if (filename.startsWith('celery_')) return <Tag color="green" style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>Celery</Tag>;
    return <Tag style={{ fontSize: 11, margin: 0, lineHeight: '16px', padding: '0 4px' }}>其他</Tag>;
  };

  // ============================================================
  // 表格列定义
  // ============================================================

  const logColumns = [
    {
      title: '时间',
      dataIndex: 'time',
      key: 'time',
      width: 170,
      render: (text: string) => (
        <span style={{ fontFamily: 'Consolas, monospace', fontSize: 12, color: '#666', whiteSpace: 'nowrap' }}>
          {text || '—'}
        </span>
      ),
    },
    {
      title: '级别',
      dataIndex: 'level',
      key: 'level',
      width: 80,
      align: 'center' as const,
      render: (_: unknown, row: MergedEntry) => {
        if (!row.level) return null;
        const colorMap: Record<string, string> = {
          ERROR: 'red', WARNING: 'orange', INFO: 'blue', DEBUG: 'default',
        };
        return <Tag color={colorMap[row.level] || 'default'} style={{ margin: 0 }}>{row.level}</Tag>;
      },
    },
    {
      title: '来源',
      dataIndex: 'source',
      key: 'source',
      width: 220,
      ellipsis: true,
      render: (text: string) => (
        <span style={{ fontFamily: 'Consolas, monospace', fontSize: 12, color: '#888' }}>
          {keyword ? highlightText(text, keyword) : (text || '—')}
        </span>
      ),
    },
    {
      title: '消息',
      dataIndex: 'message',
      key: 'message',
      render: (_: unknown, row: MergedEntry) => {
        const isError = row.level === 'ERROR';
        const isWarning = row.level === 'WARNING';
        const color = isError ? '#cf1322' : isWarning ? '#d48806' : '#333';
        return (
          <div>
            <span style={{
              fontFamily: 'Consolas, monospace',
              fontSize: 12,
              color,
              fontWeight: isError ? 600 : 'normal',
              wordBreak: 'break-all',
            }}>
              {keyword ? highlightText(row.message, keyword) : row.message}
            </span>
            {/* 合并模式下显示出现次数 */}
            {row.count > 1 && (
              <Tag color="volcano" style={{ marginLeft: 8, fontSize: 11 }}>
                ×{row.count}次
                <span style={{ marginLeft: 4, color: '#888', fontSize: 10 }}>
                  {row.firstTime?.slice(11, 19)} ~ {row.lastTime?.slice(11, 19)}
                </span>
              </Tag>
            )}
          </div>
        );
      },
    },
  ];

  // 行样式
  const rowClassName = (row: MergedEntry) => {
    if (row.level === 'ERROR') return 'log-row-error';
    if (row.level === 'WARNING') return 'log-row-warning';
    return 'log-row-normal';
  };

  // 可展开行配置（有堆栈detail时可展开）
  const expandable = {
    expandedRowRender: (row: MergedEntry) => (
      <pre style={{
        fontFamily: 'Consolas, monospace',
        fontSize: 11,
        lineHeight: 1.5,
        margin: 0,
        padding: '8px 12px',
        background: '#2d2d2d',
        color: '#f48771',
        borderRadius: 4,
        maxHeight: 400,
        overflow: 'auto',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-all',
      }}>
        {row.detail}
      </pre>
    ),
    rowExpandable: (row: MergedEntry) => !!row.detail,
  };

  // ============================================================
  // 计算最近错误的时间提示
  // ============================================================
  const latestErrorTimeAgo = useMemo(() => {
    if (!latestError?.time) return '';
    const h = getHoursAgo(latestError.time);
    if (h === null) return '';
    if (h < 1 / 60) return '刚刚';
    if (h < 1) return `${Math.round(h * 60)}分钟前`;
    if (h < 24) return `${Math.round(h)}小时前`;
    return `${Math.round(h / 24)}天前`;
  }, [latestError]);

  // ============================================================
  // 渲染
  // ============================================================

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: 'calc(100vh - 80px)', gap: 8, padding: '0 16px' }}>
      <style>{`
        .log-table .ant-table {
          border-radius: 8px;
          overflow: hidden;
          border: 1px solid #e8e8e8;
        }
        .log-table .ant-table-thead > tr > th {
          background: #fafafa !important;
          font-weight: 600 !important;
          font-size: 13px;
          border-bottom: 2px solid #d9d9d9 !important;
        }
        .log-table .ant-table-tbody > tr > td {
          border-bottom: 1px solid #f0f0f0;
          padding: 4px 8px !important;
        }
        .log-table .ant-table-tbody > tr.log-row-error > td {
          background: #fff1f0 !important;
        }
        .log-table .ant-table-tbody > tr.log-row-error:hover > td {
          background: #ffccc7 !important;
        }
        .log-table .ant-table-tbody > tr.log-row-warning > td {
          background: #fffbe6 !important;
        }
        .log-table .ant-table-tbody > tr.log-row-warning:hover > td {
          background: #fff1b8 !important;
        }
        .log-table .ant-table-tbody > tr.log-row-normal:hover > td {
          background: #f5f5f5 !important;
        }
        .log-file-group .group-header {
          cursor: pointer;
          user-select: none;
          padding: 6px 12px;
          font-weight: 600;
          font-size: 12px;
          color: #1677ff;
          background: #f0f5ff;
          border-bottom: 1px solid #e8e8e8;
          display: flex;
          align-items: center;
          justify-content: space-between;
        }
        .log-file-group .group-header:hover {
          background: #e6f4ff;
        }
        .log-file-item {
          cursor: pointer;
          padding: 6px 12px 6px 20px;
          border-bottom: 1px solid #f5f5f5;
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 12px;
        }
        .log-file-item:hover {
          background: #fafafa;
        }
        .log-file-item.active {
          background: #e6f4ff;
        }
      `}</style>

      {/* ========== 顶部统计看板 ========== */}
      <Card styles={{ body: { padding: '8px 20px' } }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, fontSize: 14 }}>系统日志</span>

          {/* 级别统计 */}
          {allEntries.length > 0 && (
            <>
              <div style={{ width: 1, height: 16, background: '#e8e8e8' }} />
              <Space size={12}>
                <Badge count={levelCounts.ERROR} showZero overflowCount={999}
                  style={{ backgroundColor: levelCounts.ERROR > 0 ? '#ff4d4f' : '#d9d9d9' }}>
                  <span style={{ padding: '0 4px', fontSize: 12 }}>ERROR</span>
                </Badge>
                <Badge count={levelCounts.WARNING} showZero overflowCount={999}
                  style={{ backgroundColor: levelCounts.WARNING > 0 ? '#faad14' : '#d9d9d9' }}>
                  <span style={{ padding: '0 4px', fontSize: 12 }}>WARN</span>
                </Badge>
                <Badge count={levelCounts.INFO} showZero overflowCount={999}
                  style={{ backgroundColor: '#1677ff' }}>
                  <span style={{ padding: '0 4px', fontSize: 12 }}>INFO</span>
                </Badge>
              </Space>
            </>
          )}

          {/* 最近错误提示 */}
          {latestError && (
            <>
              <div style={{ width: 1, height: 16, background: '#e8e8e8' }} />
              <span style={{ fontSize: 12, color: '#ff4d4f' }}>
                最近错误：{latestError.message.slice(0, 50)}{latestError.message.length > 50 ? '...' : ''}
                <span style={{ color: '#999', marginLeft: 6 }}>({latestErrorTimeAgo})</span>
              </span>
            </>
          )}

          {/* 右侧自动刷新 */}
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
            {autoRefresh && <span style={{ fontSize: 11, color: '#52c41a', animation: 'pulse 2s infinite' }}>● 自动刷新中</span>}
            <Switch
              checked={autoRefresh}
              onChange={setAutoRefresh}
              checkedChildren="自动刷新"
              unCheckedChildren="手动"
              size="small"
            />
          </div>
        </div>
      </Card>

      {/* ========== 主体：左文件列表 + 右日志表格 ========== */}
      <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>

        {/* 左侧：文件列表（按月折叠） */}
        <Card
          style={{ width: 240, flexShrink: 0, display: 'flex', flexDirection: 'column' }}
          styles={{ body: { padding: 0, flex: 1, overflow: 'auto' } }}
          title={
            <Segmented
              value={fileTab}
              onChange={v => { setFileTab(v as string); }}
              options={[
                { value: 'all', label: '全部' },
                { value: 'web', label: 'Web' },
                { value: 'celery', label: 'Celery' },
              ]}
              size="small"
              block
            />
          }
          extra={
            <Button icon={<ReloadOutlined />} size="small" type="text"
              onClick={loadFiles} loading={loadingFiles} />
          }
        >
          {fileGroups.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无日志" style={{ padding: 24 }} />
          ) : (
            <div className="log-file-group">
              {fileGroups.map(group => (
                <div key={group.month}>
                  {/* 月份标题 */}
                  <div className="group-header" onClick={() => toggleMonth(group.month)}>
                    <span>
                      {collapsedMonths.has(group.month)
                        ? <RightOutlined style={{ fontSize: 10, marginRight: 4 }} />
                        : <DownOutlined style={{ fontSize: 10, marginRight: 4 }} />}
                      {group.label}
                    </span>
                    <span style={{ fontSize: 11, fontWeight: 'normal', color: '#999' }}>
                      {group.files.length}个
                    </span>
                  </div>
                  {/* 文件列表 */}
                  {!collapsedMonths.has(group.month) && group.files.map(f => (
                    <div
                      key={f.filename}
                      className={`log-file-item ${f.filename === selectedFile ? 'active' : ''}`}
                      onClick={() => setSelectedFile(f.filename)}
                    >
                      <FileTextOutlined style={{ fontSize: 12, color: '#bbb' }} />
                      {fileTypeTag(f.filename)}
                      <span style={{ flex: 1 }}>
                        {f.filename.replace(/^(web_|celery_|auto_quota_)/, '').replace('.log', '')}
                      </span>
                      <span style={{ fontSize: 11, color: fileSizeColor(f.size) }}>
                        {f.size_display}
                      </span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* 右侧：日志表格 */}
        <Card
          style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}
          styles={{ body: { padding: 0, flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' } }}
          title={
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              {/* 级别筛选按钮组 */}
              <Segmented
                value={levelFilter}
                onChange={v => setLevelFilter(v as string)}
                options={[
                  { value: '', label: `全部 ${allEntries.length}` },
                  { value: 'ERROR', label: <span style={{ color: levelFilter === 'ERROR' ? '#fff' : '#ff4d4f' }}>ERROR {levelCounts.ERROR}</span> },
                  { value: 'WARNING', label: <span style={{ color: levelFilter === 'WARNING' ? '#fff' : '#d48806' }}>WARN {levelCounts.WARNING}</span> },
                  { value: 'INFO', label: <span style={{ color: levelFilter === 'INFO' ? '#fff' : '#1677ff' }}>INFO {levelCounts.INFO}</span> },
                ]}
                size="small"
              />

              <div style={{ width: 1, height: 16, background: '#e8e8e8' }} />

              {/* 时间范围快捷筛选 */}
              <Segmented
                value={timeRange}
                onChange={v => setTimeRange(v as string)}
                options={[
                  { value: '', label: '不限' },
                  { value: '30m', label: '30分钟' },
                  { value: '1h', label: '1小时' },
                  { value: 'today', label: '今天' },
                ]}
                size="small"
              />

              <div style={{ width: 1, height: 16, background: '#e8e8e8' }} />

              {/* 合并开关 */}
              <Switch
                checked={mergeMode}
                onChange={setMergeMode}
                checkedChildren="合并相同"
                unCheckedChildren="逐行"
                size="small"
              />
            </div>
          }
          extra={
            <Space size={4}>
              <Input
                placeholder="搜索"
                prefix={<SearchOutlined />}
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onPressEnter={loadContent}
                style={{ width: 140 }}
                size="small"
                allowClear
              />
              <Select
                value={lineCount}
                onChange={setLineCount}
                size="small"
                style={{ width: 80 }}
                options={[
                  { label: '100行', value: 100 },
                  { label: '200行', value: 200 },
                  { label: '500行', value: 500 },
                  { label: '1000行', value: 1000 },
                  { label: '全部', value: 5000 },
                ]}
              />
              <Button icon={<VerticalAlignBottomOutlined />} size="small" type="text"
                onClick={scrollToBottom} title="滚到底部" />
              <Button icon={<FullscreenOutlined />} size="small" type="text"
                onClick={() => setFullscreen(true)} disabled={!logContent} />
              <Button icon={<ReloadOutlined />} size="small"
                onClick={loadContent} loading={loadingContent}>
                刷新
              </Button>
            </Space>
          }
        >
          {loadingContent ? (
            <div style={{ textAlign: 'center', padding: 40 }}>
              <Spin tip="加载中..." />
            </div>
          ) : !logContent ? (
            <Empty description="选择左侧的日志文件查看内容" style={{ padding: 40 }} />
          ) : logContent.content === '' ? (
            <Empty description={keyword ? `未找到包含"${keyword}"的日志行` : '日志文件为空'} style={{ padding: 40 }} />
          ) : (
            <div ref={tableRef} style={{ flex: 1, overflow: 'auto' }}>
              <Table
                className="log-table"
                rowKey="key"
                dataSource={displayEntries}
                columns={logColumns}
                size="small"
                pagination={{ pageSize: 200, showSizeChanger: true, pageSizeOptions: ['100', '200', '500'] }}
                scroll={{ x: 800 }}
                rowClassName={rowClassName}
                expandable={expandable}
                locale={{ emptyText: '暂无匹配的日志' }}
              />
            </div>
          )}
        </Card>
      </div>

      {/* 全屏预览弹窗（暗色终端风格） */}
      <Modal
        title={selectedFile || '日志预览'}
        open={fullscreen}
        onCancel={() => setFullscreen(false)}
        footer={null}
        width="95vw"
        styles={{ body: { height: '80vh', overflow: 'auto', padding: 0 } }}
      >
        <div
          style={{
            fontFamily: 'Consolas, "Courier New", monospace',
            fontSize: 12,
            lineHeight: 1.6,
            height: '100%',
            overflow: 'auto',
            background: '#1e1e1e',
            color: '#d4d4d4',
            padding: 16,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {displayEntries.map((row) => {
            const isError = row.level === 'ERROR';
            const isWarning = row.level === 'WARNING';
            let color = '#d4d4d4';
            if (isError) color = '#f48771';
            if (isWarning) color = '#cca700';
            return (
              <div key={row.key}>
                <div style={{ color, fontWeight: isError ? 'bold' : 'normal' }}>
                  <span style={{ color: '#858585', marginRight: 12, userSelect: 'none' }}>
                    {String(row.key + 1).padStart(4)}
                  </span>
                  {row.raw}
                  {row.count > 1 && (
                    <span style={{ color: '#e06c75', marginLeft: 8 }}>
                      [×{row.count}次 {row.firstTime?.slice(11, 19)}~{row.lastTime?.slice(11, 19)}]
                    </span>
                  )}
                </div>
                {row.detail && (
                  <div style={{ color: '#e06c75', paddingLeft: 52 }}>{row.detail}</div>
                )}
              </div>
            );
          })}
        </div>
      </Modal>
    </div>
  );
}
