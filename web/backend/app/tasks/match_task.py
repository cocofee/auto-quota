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


def _make_progress_callback(session, task, output_dir):
    """创建进度回调函数：每次被调用时更新数据库中的任务进度

    限制更新频率（至少间隔2秒），避免频繁写数据库。
    同时将匹配结果实时写入 results_live.jsonl（每行一条结果）。
    """
    last_update = [0]  # 用列表包装，让闭包可以修改
    live_path = output_dir / "results_live.jsonl" if output_dir else None

    def callback(progress, current_idx, message, result=None):
        now = time.time()

        # 实时写入匹配结果（不受2秒限频，每条都写）
        if result and live_path:
            try:
                import json as _json
                # 提取轻量摘要（不写完整 candidates/trace）
                quotas = result.get("quotas") or []
                bill = result.get("bill_item") or {}
                line = _json.dumps({
                    "idx": current_idx,
                    "quota_id": quotas[0]["quota_id"] if quotas else "",
                    "quota_name": quotas[0]["name"] if quotas else "",
                    "confidence": result.get("confidence", 0),
                    "match_source": result.get("match_source", ""),
                    "bill_name": bill.get("name", ""),
                }, ensure_ascii=False)
                with open(live_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass  # 写入失败不影响匹配

        # 至少间隔2秒更新一次（进度>=95%时不限频，确保关键节点立即更新）
        if now - last_update[0] < 2 and progress < 95:
            return
        last_update[0] = now
        try:
            task.progress = progress
            task.progress_current = current_idx
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
        import config as quota_config

        # 从数据库读取大模型配置，注入到 config 模块
        # 这样 agent_matcher.py 读 config.QWEN_API_KEY 等变量时能拿到最新值
        try:
            from app.services.llm_config_service import get_llm_config_sync, get_verify_config_sync

            # ---- 匹配模型配置 ----
            llm_cfg = get_llm_config_sync(session)
            llm_type = llm_cfg["llm_type"]
            api_key = llm_cfg["api_key"]
            base_url = llm_cfg["base_url"]
            model_name = llm_cfg["model"]

            if api_key:
                key_attr = f"{llm_type.upper()}_API_KEY"
                url_attr = f"{llm_type.upper()}_BASE_URL"
                model_attr = f"{llm_type.upper()}_MODEL"
                setattr(quota_config, key_attr, api_key)
                if base_url:
                    setattr(quota_config, url_attr, base_url)
                if model_name:
                    setattr(quota_config, model_attr, model_name)
                quota_config.AGENT_LLM = llm_type

            # ---- 验证模型配置 ----
            v_cfg = get_verify_config_sync(session)
            v_type = v_cfg["llm_type"]
            v_key = v_cfg["api_key"]
            v_url = v_cfg["base_url"]
            v_model = v_cfg["model"]

            if v_type and v_key:
                # 验证模型单独配置了
                v_key_attr = f"{v_type.upper()}_API_KEY"
                v_url_attr = f"{v_type.upper()}_BASE_URL"
                v_model_attr = f"{v_type.upper()}_MODEL"
                setattr(quota_config, v_key_attr, v_key)
                if v_url:
                    setattr(quota_config, v_url_attr, v_url)
                quota_config.VERIFY_LLM = v_type
                if v_model:
                    quota_config.VERIFY_MODEL = v_model
            elif not v_type:
                # 验证模型未配置，跟匹配模型走
                quota_config.VERIFY_LLM = ""
                quota_config.VERIFY_MODEL = ""

            verify_label = f"{quota_config.VERIFY_LLM}/{quota_config.VERIFY_MODEL}" if quota_config.VERIFY_LLM else "同匹配"
            logger.info(f"任务 {task_id}: 大模型配置从数据库加载 → 匹配:{llm_type}/{model_name}，验证:{verify_label}")
        except Exception as e:
            logger.warning(f"任务 {task_id}: 从数据库读取大模型配置失败，使用环境变量: {e}")

        # 自动挂载同批辅助定额库（同省份+同年份的兄弟库）
        province = params.get("province", "")
        aux_provinces = quota_config.get_sibling_provinces(province)
        if aux_provinces:
            logger.info(f"任务 {task_id}: 自动挂载同批辅助库: {aux_provinces}")

        # 创建进度回调（匹配过程中实时更新数据库进度）
        progress_cb = _make_progress_callback(session, task, output_dir)

        result = auto_quota_main.run(
            input_file=file_path,
            mode=params.get("mode", "search"),
            output=excel_output,
            province=params.get("province"),
            aux_provinces=aux_provinces or None,  # 同批兄弟库自动挂载
            sheet=params.get("sheet"),
            limit=params.get("limit"),
            agent_llm=params.get("agent_llm"),
            json_output=json_output,
            no_experience=params.get("no_experience", False),
            interactive=False,  # Web调用不需要交互式提示
            progress_callback=progress_cb,
        )

        # ---- 第4步：扣减用户额度（必须在保存结果之前） ----
        # 先扣费再保存结果，防止余额不足时用户白拿匹配结果
        results_list = result.get("results", [])
        actual_count = len(results_list)
        quota_deducted = False  # 标记是否成功扣减
        if actual_count > 0:
            try:
                from app.services.quota_service import deduct_quota_sync
                new_balance = deduct_quota_sync(
                    session=session,
                    user_id=task.user_id,
                    count=actual_count,
                    task_id=str(task_uuid),
                    task_name=task.name or "未命名任务",
                )
                if new_balance < 0:
                    # 余额不足，标记任务为失败，不保存匹配结果
                    logger.warning(
                        f"任务 {task_id}: 额度不足，无法扣减 {actual_count} 条，"
                        f"匹配结果不予保存"
                    )
                    task.status = "failed"
                    task.progress = 100
                    task.progress_message = "额度不足"
                    task.error_message = (
                        f"额度不足：本次匹配 {actual_count} 条，"
                        f"但余额不够扣减。请购买额度后重新提交任务。"
                    )
                    task.completed_at = datetime.now(timezone.utc)
                    session.commit()
                    return
                quota_deducted = True
            except Exception as quota_err:
                # 额度扣减异常（数据库故障等），标记任务失败，不保存结果
                # 避免异常时用户白拿匹配结果
                logger.error(f"任务 {task_id}: 额度扣减异常: {quota_err}")
                task.status = "failed"
                task.progress = 100
                task.progress_message = "额度扣减异常"
                task.error_message = (
                    f"额度扣减出错，匹配结果未保存。请联系管理员处理。"
                )
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
                return

        # ---- 第4.5步：保存匹配结果到数据库 ----
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
