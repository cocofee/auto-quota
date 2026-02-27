"""
Celery 跑分任务

从网页触发 benchmark 跑分，后台异步执行。
复用 tools/run_benchmark.py 中的核心函数，通过 Celery 的 update_state 报告进度。
"""

import sys
from pathlib import Path

from loguru import logger

from app.celery_app import celery_app
from app.config import PROJECT_ROOT

# 把 tools/ 目录加入搜索路径，以便导入 run_benchmark 模块
_tools_dir = str(PROJECT_ROOT / "tools")
if _tools_dir not in sys.path:
    sys.path.insert(0, _tools_dir)


@celery_app.task(bind=True, name="execute_benchmark")
def execute_benchmark(self, mode: str = "search", note: str = ""):
    """后台执行 benchmark 跑分

    参数:
        mode: 匹配模式（"search" 免费快速 / "agent" 需API Key）
        note: 跑分备注（说明本次改了什么）

    通过 self.update_state() 向前端推送进度:
        state="PROGRESS", meta={"current": 当前第几个, "total": 总数, "dataset": 正在跑的数据集名}
        state="SUCCESS", meta={"message": "完成", "datasets_run": 跑了几个}
    """
    import run_benchmark as bm  # 延迟导入，避免 worker 启动时加载全部匹配引擎

    try:
        # 加载数据集配置
        config = bm.load_config()
        datasets = config["datasets"]
        total = len(datasets)

        logger.info(f"Benchmark 开始: mode={mode}, 共 {total} 个数据集")

        # 报告初始状态
        self.update_state(state="PROGRESS", meta={
            "current": 0,
            "total": total,
            "dataset": "初始化中...",
        })

        # 逐个跑数据集
        all_metrics = {}
        for idx, (name, ds_config) in enumerate(datasets.items()):
            # 报告当前进度
            self.update_state(state="PROGRESS", meta={
                "current": idx,
                "total": total,
                "dataset": name,
            })

            metrics = bm.run_single_dataset(name, ds_config, mode)
            all_metrics[name] = metrics

        # 检查是否有可用结果
        non_skipped = [m for m in all_metrics.values() if m is not None]
        if not non_skipped:
            logger.warning("Benchmark: 所有数据集都被跳过（文件不存在）")
            return {
                "success": False,
                "message": "所有数据集文件都不存在，无法跑分",
                "datasets_run": 0,
            }

        # 检查是否全部失败
        failed = [n for n, m in all_metrics.items()
                  if m is not None and m.get("_failed")]
        if non_skipped and all(m.get("_failed") for m in non_skipped):
            logger.error(f"Benchmark: 所有数据集均执行失败: {failed}")
            return {
                "success": False,
                "message": f"所有数据集均执行失败: {', '.join(failed)}",
                "datasets_run": 0,
            }

        # 有失败的数据集时不保存基线（但保存历史记录）
        if failed:
            logger.warning(f"Benchmark: 部分数据集失败，不保存基线: {failed}")
            # 仍然追加历史记录（让前端能看到这次跑分）
            bm._append_history({
                "version": "L2-a_baseline",
                "date": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "mode": mode,
                "datasets": {
                    n: m for n, m in all_metrics.items()
                    if m is not None and not m.get("_failed")
                },
            }, note)
        else:
            # 全部成功，保存基线 + 历史
            bm.save_baseline(all_metrics, mode, note=note)

        datasets_run = len([m for m in all_metrics.values()
                           if m is not None and not m.get("_failed")])
        logger.info(f"Benchmark 完成: {datasets_run}/{total} 个数据集成功")

        return {
            "success": True,
            "message": f"跑分完成，{datasets_run} 个数据集成功",
            "datasets_run": datasets_run,
            "failed": failed,
        }

    except Exception as e:
        logger.error(f"Benchmark 执行异常: {e}")
        raise  # 让 Celery 标记任务为 FAILURE
