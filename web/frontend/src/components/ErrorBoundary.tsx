/**
 * 全局错误边界
 *
 * 捕获子组件的 React 渲染错误，防止整个应用白屏。
 * 显示友好的错误提示，并提供"重试"按钮。
 */

import { Component } from 'react';
import type { ReactNode, ErrorInfo } from 'react';
import { Button, Result } from 'antd';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export default class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // 打印到控制台，方便调试
    console.error('[ErrorBoundary] 页面渲染出错:', error, errorInfo);
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null });
  };

  render() {
    if (this.state.hasError) {
      return (
        <Result
          status="error"
          title="页面出错了"
          subTitle={this.state.error?.message || '未知错误，请刷新页面重试'}
          extra={[
            <Button key="retry" type="primary" onClick={this.handleRetry}>
              重试
            </Button>,
            <Button key="refresh" onClick={() => window.location.reload()}>
              刷新页面
            </Button>,
          ]}
        />
      );
    }

    return this.props.children;
  }
}
