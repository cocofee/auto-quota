"""
Celery 批量处理任务

从网页触发文件扫描和批量匹配，后台异步执行。
复用 tools/batch_scanner.py 和 tools/batch_runner.py 的核心函数。
"""

import sys
from pathlib import Path

from loguru import logger

from app.celery_app import celery_app
from app.config import PROJECT_ROOT

# 把项目根目录加入搜索路径
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@celery_app.task(bind=True, name="execute_scan")
def execute_scan(self, directory: str = "F:/jarvis",
                 specialty: str = None, rescan: bool = False):
    """后台执行文件扫描

    参数:
        directory: 扫描目录
        specialty: 只扫某专业（可选）
        rescan: 是否重新分类已扫描的文件
    """
    try:
        from tools.batch_scanner import scan_directory, init_db

        # 初始化数据库
        init_db()

        self.update_state(state="PROGRESS", meta={
            "stage": "scanning",
            "message": f"正在扫描 {directory}...",
        })

        # 执行扫描
        stats = scan_directory(
            base_dir=directory,
            specialty_filter=specialty,
            rescan=rescan,
        )

        logger.info(f"扫描完成: {stats}")
        return {
            "success": True,
            "message": "扫描完成",
            "stats": stats,
        }

    except Exception as e:
        logger.error(f"扫描执行异常: {e}")
        raise


@celery_app.task(bind=True, name="execute_batch_run")
def execute_batch_run(self, format_filter: str = None, province: str = None,
                      specialty: str = None, limit: int = None):
    """后台执行批量匹配

    参数:
        format_filter: 只跑某格式
        province: 只跑某省
        specialty: 只跑某专业
        limit: 最多跑几个文件
    """
    try:
        from tools.batch_runner import run_batch

        self.update_state(state="PROGRESS", meta={
            "stage": "matching",
            "message": "正在启动批量匹配...",
            "current": 0,
            "total": 0,
        })

        # 执行批量匹配（传入进度回调）
        def progress_callback(current, total, file_name):
            self.update_state(state="PROGRESS", meta={
                "stage": "matching",
                "message": f"正在匹配: {file_name}",
                "current": current,
                "total": total,
            })

        stats = run_batch(
            format_filter=format_filter,
            province_filter=province,
            specialty_filter=specialty,
            limit=limit,
            progress_callback=progress_callback,
        )

        logger.info(f"批量匹配完成: {stats}")
        return {
            "success": True,
            "message": "批量匹配完成",
            "stats": stats,
        }

    except Exception as e:
        logger.error(f"批量匹配执行异常: {e}")
        raise
