/**
 * 系统日志查看页面（管理员专属）
 *
 * 左侧：日志文件列表
 * 右侧：日志内容显示（等宽字体，高亮 ERROR/WARNING）
 * 支持关键词搜索和行数控制
 */

import { useState, useEffect, useCallback } from 'react';
import {
  Card, List, Input, Select, Button, Space, Typography, Tag, Empty, Spin, Modal,
} from 'antd';
import {
  ReloadOutlined, FileTextOutlined, SearchOutlined, FullscreenOutlined,
} from '@ant-design/icons';
import api from '../../services/api';

const { Title, Text } = Typography;

// 日志文件信息
interface LogFile {
  filename: string;
  size: number;
  size_display: string;
  modified_at: number;
}

// 日志内容响应
interface LogContent {
  filename: string;
  total_lines: number;
  returned_lines: number;
  content: string;
}

export default function LogViewer() {
  // 文件列表
  const [files, setFiles] = useState<LogFile[]>([]);
  const [loadingFiles, setLoadingFiles] = useState(false);

  // 当前选中的文件
  const [selectedFile, setSelectedFile] = useState('');

  // 日志内容
  const [logContent, setLogContent] = useState<LogContent | null>(null);
  const [loadingContent, setLoadingContent] = useState(false);

  // 搜索参数
  const [keyword, setKeyword] = useState('');
  const [lines, setLines] = useState(200);
  const [levelFilter, setLevelFilter] = useState<string>(''); // 日志级别筛选
  const [fullscreen, setFullscreen] = useState(false); // 全屏模式

  // 加载文件列表（只在首次加载和手动刷新时调用）
  const loadFiles = useCallback(() => {
    setLoadingFiles(true);
    api.get('/admin/logs/files')
      .then((res) => {
        const items: LogFile[] = res.data.items || [];
        setFiles(items);
        // 自动选中最新文件（仅首次加载时）
        setSelectedFile((prev) => {
          if (!prev && items.length > 0) return items[0].filename;
          return prev;
        });
      })
      .catch(() => {})
      .finally(() => setLoadingFiles(false));
  }, []);

  useEffect(() => {
    loadFiles();
  }, [loadFiles]);

  // 加载日志内容（keyword 不放依赖数组，只在回车/点击刷新时触发搜索）
  const loadContent = useCallback(() => {
    if (!selectedFile) return;
    setLoadingContent(true);
    api.get('/admin/logs/read', {
      params: { filename: selectedFile, lines, keyword: keyword || undefined },
    })
      .then((res) => {
        setLogContent(res.data);
      })
      .catch(() => {
        setLogContent(null);
      })
      .finally(() => setLoadingContent(false));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedFile, lines]);

  useEffect(() => {
    loadContent();
  }, [loadContent]);

  // 高亮日志行中的 ERROR/WARNING 等关键词
  const renderLogLine = (line: string, index: number) => {
    const isError = /\bERROR\b/i.test(line);
    const isWarning = /\bWARNING\b/i.test(line);

    let color = 'inherit';
    if (isError) color = '#ff4d4f';
    if (isWarning) color = '#faad14';

    return (
      <div
        key={index}
        style={{
          color,
          fontWeight: isError ? 'bold' : 'normal',
          padding: '1px 0',
          borderBottom: '1px solid #f5f5f5',
        }}
      >
        {line}
      </div>
    );
  };

  // 按级别过滤日志行
  const filteredLines = logContent
    ? logContent.content.split('\n').filter((line) => {
        if (!levelFilter) return true;
        if (levelFilter === 'ERROR') return /\bERROR\b/i.test(line);
        if (levelFilter === 'WARNING') return /\bWARNING\b/i.test(line);
        if (levelFilter === 'INFO') return /\bINFO\b/i.test(line);
        return true;
      })
    : [];

  // 文件列表中正在选中的项高亮
  const fileTypeTag = (filename: string) => {
    if (filename.startsWith('web_')) return <Tag color="blue">Web</Tag>;
    if (filename.startsWith('celery_')) return <Tag color="green">Celery</Tag>;
    return <Tag>其他</Tag>;
  };

  return (
    <div>
      <Title level={3}>系统日志</Title>
      <Text type="secondary">
        查看后端和 Celery 的运行日志，定位错误和异常。
      </Text>

      <div style={{ display: 'flex', gap: 16, marginTop: 16 }}>
        {/* 左侧：文件列表 */}
        <Card
          title="日志文件"
          style={{ width: 300, flexShrink: 0 }}
          extra={
            <Button
              icon={<ReloadOutlined />}
              size="small"
              onClick={loadFiles}
              loading={loadingFiles}
            />
          }
          styles={{ body: { padding: 0 } }}
        >
          {files.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无日志文件"
              style={{ padding: 24 }}
            />
          ) : (
            <List
              size="small"
              dataSource={files}
              renderItem={(file) => (
                <List.Item
                  onClick={() => setSelectedFile(file.filename)}
                  style={{
                    cursor: 'pointer',
                    padding: '8px 16px',
                    background: file.filename === selectedFile ? '#e6f4ff' : 'transparent',
                  }}
                >
                  <div style={{ width: '100%' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <FileTextOutlined />
                      {fileTypeTag(file.filename)}
                      <Text style={{ fontSize: 12 }}>
                        {file.filename.replace(/^(web_|celery_)/, '').replace('.log', '')}
                      </Text>
                    </div>
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {file.size_display}
                    </Text>
                  </div>
                </List.Item>
              )}
            />
          )}
        </Card>

        {/* 右侧：日志内容 */}
        <Card
          title={selectedFile ? `${selectedFile}` : '选择一个日志文件'}
          style={{ flex: 1, minWidth: 0 }}
          extra={
            <Space>
              <Select
                value={levelFilter}
                onChange={setLevelFilter}
                size="small"
                style={{ width: 100 }}
                options={[
                  { label: '全部级别', value: '' },
                  { label: 'ERROR', value: 'ERROR' },
                  { label: 'WARNING', value: 'WARNING' },
                  { label: 'INFO', value: 'INFO' },
                ]}
              />
              <Input
                placeholder="搜索关键词"
                prefix={<SearchOutlined />}
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
                onPressEnter={loadContent}
                style={{ width: 180 }}
                size="small"
                allowClear
              />
              <Select
                value={lines}
                onChange={setLines}
                size="small"
                style={{ width: 100 }}
                options={[
                  { label: '100 行', value: 100 },
                  { label: '200 行', value: 200 },
                  { label: '500 行', value: 500 },
                  { label: '1000 行', value: 1000 },
                  { label: '全部', value: 5000 },
                ]}
              />
              <Button
                icon={<FullscreenOutlined />}
                size="small"
                onClick={() => setFullscreen(true)}
                disabled={!logContent}
              >
                全屏
              </Button>
              <Button
                icon={<ReloadOutlined />}
                size="small"
                onClick={loadContent}
                loading={loadingContent}
              >
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
            <Empty description="选择左侧的日志文件查看内容" />
          ) : logContent.content === '' ? (
            <Empty description={keyword ? `未找到包含"${keyword}"的日志行` : '日志文件为空'} />
          ) : (
            <div>
              <div style={{ marginBottom: 8 }}>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  共 {logContent.total_lines} 行，显示最后 {logContent.returned_lines} 行
                  {keyword && `（搜索: "${keyword}"）`}
                  {levelFilter && `（级别: ${levelFilter}，${filteredLines.length} 条）`}
                </Text>
              </div>
              <div
                style={{
                  fontFamily: 'Consolas, "Courier New", monospace',
                  fontSize: 12,
                  lineHeight: 1.6,
                  maxHeight: 'calc(100vh - 320px)',
                  overflow: 'auto',
                  background: '#fafafa',
                  padding: 12,
                  borderRadius: 4,
                  border: '1px solid #f0f0f0',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-all',
                }}
              >
                {filteredLines.map(renderLogLine)}
              </div>
            </div>
          )}
        </Card>
      </div>

      {/* 全屏预览弹窗 */}
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
          {filteredLines.map((line, index) => {
            const isError = /\bERROR\b/i.test(line);
            const isWarning = /\bWARNING\b/i.test(line);
            let color = '#d4d4d4';
            if (isError) color = '#f48771';
            if (isWarning) color = '#cca700';
            return (
              <div key={index} style={{ color, fontWeight: isError ? 'bold' : 'normal' }}>
                <span style={{ color: '#858585', marginRight: 12, userSelect: 'none' }}>
                  {String(index + 1).padStart(4)}
                </span>
                {line}
              </div>
            );
          })}
        </div>
      </Modal>
    </div>
  );
}
