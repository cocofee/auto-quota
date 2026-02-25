/**
 * 管理员 — 所有任务列表
 *
 * 复用 TaskListPage 组件，传入 adminView=true 展示管理员视角。
 * 避免和 TaskListPage 80% 的代码重复。
 */

import TaskListPage from '../Task/ListPage';

export default function TaskListAll() {
  return <TaskListPage adminView />;
}
