"""
Celery 匹配任务

后台执行耗时的定额匹配：
1. 更新任务状态为"运行中"
2. 调用现有的 main.run() 函数执行匹配
3. 保存结果到 PostgreSQL
4. 更新任务状态为"已完成"或"已失败"

Celery worker 启动命令:
    cd web/backend
    celery -A app.celery_app worker --loglevel=info --pool=solo
"""

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.celery_app import celery_app
from app.database import get_sync_session
from app.models.task import Task
from app.services.match_service import save_results_to_db, get_task_output_dir


def _make_progress_callback(session, task):
    """创建进度回调函数：每次被调用时更新数据库中的任务进度

    限制更新频率（至少间隔2秒），避免频繁写数据库。
    """
    last_update = [0]  # 用列表包装，让闭包可以修改

    def callback(progress, current_idx, message):
        now = time.time()
        # 至少间隔2秒更新一次（进度>=95%时不限频，确保关键节点立即更新）
        if now - last_update[0] < 2 and progress < 95:
            return
        last_update[0] = now
        try:
            task.progress = progress
            task.progress_message = message
            session.commit()
        except Exception as e:
            # 数据库写入失败不影响匹配主流程
            logger.debug(f"进度更新写入DB失败（不影响匹配）: {e}")
            try:
                session.rollback()
            except Exception:
                pass

    return callback


@celery_app.task(bind=True, name="execute_match")
def execute_match(self, task_id: str, file_path: str, params: dict):
    """后台执行定额匹配任务

    参数:
        task_id: 任务ID（UUID字符串）
        file_path: 上传的Excel文件路径
        params: 匹配参数字典:
            - mode: "search" 或 "agent"
            - province: 省份定额库名称
            - sheet: 指定Sheet名称（可选）
            - limit: 限制处理条数（可选）
            - agent_llm: Agent模式大模型（可选）
            - no_experience: 是否禁用经验库
    """
    task_uuid = uuid.UUID(task_id)
    session = get_sync_session()

    try:
        # ---- 第1步：更新状态为"运行中" ----
        task = session.get(Task, task_uuid)
        if not task:
            logger.error(f"任务 {task_id} 不存在")
            return

        task.status = "running"
        task.progress = 10
        task.progress_message = "正在初始化匹配引擎..."
        task.started_at = datetime.now(timezone.utc)
        session.commit()

        # ---- 文件存在性检查：避免文件丢失导致任务卡死 ----
        if not Path(file_path).exists():
            error_msg = f"上传文件不存在: {file_path}（可能已被清理或磁盘故障）"
            logger.error(f"任务 {task_id}: {error_msg}")
            task.status = "failed"
            task.error_message = error_msg
            task.completed_at = datetime.now(timezone.utc)
            session.commit()
            return

        # ---- 第2步：准备输出路径 ----
        output_dir = get_task_output_dir(task_uuid)
        json_output = str(output_dir / "results.json")
        excel_output = str(output_dir / "output.xlsx")

        # ---- 第3步：调用现有的 run() 函数 ----
        # main.py 已通过 celery_app.py 的 sys.path 设置可被导入
        logger.info(
            f"任务 {task_id}: 开始匹配 "
            f"(mode={params.get('mode')}, province={params.get('province')})"
        )

        import main as auto_quota_main  # 延迟导入，避免循环依赖

        # 创建进度回调（匹配过程中实时更新数据库进度）
        progress_cb = _make_progress_callback(session, task)

        result = auto_quota_main.run(
            input_file=file_path,
            mode=params.get("mode", "search"),
            output=excel_output,
            province=params.get("province"),
            sheet=params.get("sheet"),
            limit=params.get("limit"),
            agent_llm=params.get("agent_llm"),
            json_output=json_output,
            no_experience=params.get("no_experience", False),
            interactive=False,  # Web调用不需要交互式提示
            progress_callback=progress_cb,
        )

        # ---- 第4步：保存匹配结果到数据库 ----
        results_list = result.get("results", [])
        save_results_to_db(session, task_uuid, results_list)

        # ---- 第5步：更新任务状态为"已完成" ----
        task.status = "completed"
        task.progress = 100
        task.progress_message = "匹配完成"
        task.stats = result.get("stats", {})
        task.output_path = excel_output if Path(excel_output).exists() else None
        task.json_output_path = json_output if Path(json_output).exists() else None
        task.completed_at = datetime.now(timezone.utc)
        session.commit()

        logger.info(f"任务 {task_id}: 匹配完成，共 {len(results_list)} 条结果")

    except Exception as e:
        # ---- 失败处理 ----
        logger.error(f"任务 {task_id} 执行失败: {e}")
        try:
            session.rollback()
            task = session.get(Task, task_uuid)
            if task:
                task.status = "failed"
                task.error_message = str(e)[:1000]  # 截断过长的错误信息
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as db_err:
            logger.error(f"任务 {task_id} 更新失败状态时出错: {db_err}")
        raise  # 让 Celery 也记录这个异常

    finally:
        session.close()
