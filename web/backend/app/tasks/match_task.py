"""
Celery worker task for quota matching.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.celery_app import celery_app
from app.database import get_sync_session
from app.models.task import Task
from app.services.match_service import get_task_output_dir, save_results_to_db


class TaskCancelled(BaseException):
    """Raised to stop execution immediately after a user cancellation."""


def _assert_task_not_cancelled(session, task: Task) -> None:
    session.refresh(task, attribute_names=["status"])
    if task.status == "cancelled":
        raise TaskCancelled(f"任务 {task.id} 已被用户取消")


def _mark_task_cancelled(session, task_uuid: uuid.UUID, message: str) -> None:
    task = session.get(Task, task_uuid)
    if not task:
        return
    task.status = "cancelled"
    task.progress_message = "用户取消"
    task.error_message = message[:1000]
    task.completed_at = datetime.now(timezone.utc)
    session.commit()


def _make_progress_callback(session, task: Task, output_dir: Path):
    """Create a throttled progress callback for the local matcher."""
    last_update = [0.0]
    live_path = output_dir / "results_live.jsonl" if output_dir else None

    def callback(progress, current_idx, message, result=None):
        _assert_task_not_cancelled(session, task)
        now = time.time()

        if result and live_path:
            try:
                import json as _json

                quotas = result.get("quotas") or []
                bill = result.get("bill_item") or {}
                line = _json.dumps(
                    {
                        "idx": current_idx,
                        "quota_id": quotas[0]["quota_id"] if quotas else "",
                        "quota_name": quotas[0]["name"] if quotas else "",
                        "confidence": result.get("confidence", 0),
                        "match_source": result.get("match_source", ""),
                        "bill_name": bill.get("name", ""),
                    },
                    ensure_ascii=False,
                )
                with open(live_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass

        if now - last_update[0] < 2 and progress < 95:
            return
        last_update[0] = now

        try:
            task.progress = progress
            task.progress_current = current_idx
            task.progress_message = message
            session.commit()
        except Exception as exc:
            logger.debug(f"进度更新写入 DB 失败: {exc}")
            try:
                session.rollback()
            except Exception:
                pass

    return callback


@celery_app.task(bind=True, name="execute_match")
def execute_match(self, task_id: str, file_path: str, params: dict):
    """Execute a match task in the worker."""
    task_uuid = uuid.UUID(task_id)
    session = get_sync_session()

    try:
        task = session.get(Task, task_uuid)
        if not task:
            logger.error(f"任务 {task_id} 不存在")
            return

        _assert_task_not_cancelled(session, task)
        task.status = "running"
        task.progress = 10
        task.progress_message = "正在初始化匹配引擎..."
        task.started_at = datetime.now(timezone.utc)
        session.commit()

        if not Path(file_path).exists():
            error_msg = f"上传文件不存在: {file_path}"
            logger.error(f"任务 {task_id}: {error_msg}")
            task.status = "failed"
            task.error_message = error_msg
            task.completed_at = datetime.now(timezone.utc)
            session.commit()
            return

        output_dir = get_task_output_dir(task_uuid)
        json_output = str(output_dir / "results.json")
        excel_output = str(output_dir / "output.xlsx")

        from app.config import MATCH_BACKEND

        if MATCH_BACKEND == "remote":
            result = _execute_remote_match(
                session,
                task,
                task_id,
                file_path,
                params,
                output_dir,
                json_output,
                excel_output,
            )
        else:
            result = _execute_local_match(
                session,
                task,
                task_id,
                file_path,
                params,
                output_dir,
                json_output,
                excel_output,
            )

        _assert_task_not_cancelled(session, task)
        results_list = result.get("results", [])
        actual_count = len(results_list)

        if actual_count > 0:
            _assert_task_not_cancelled(session, task)
            try:
                from app.services.quota_service import deduct_quota_sync

                new_balance = deduct_quota_sync(
                    session=session,
                    user_id=task.user_id,
                    count=actual_count,
                    task_id=str(task_uuid),
                    task_name=task.name or "未命名任务",
                )
            except Exception as quota_err:
                logger.error(f"任务 {task_id}: 额度扣减异常: {quota_err}")
                task.status = "failed"
                task.progress = 100
                task.progress_message = "额度扣减异常"
                task.error_message = "额度扣减出错，匹配结果未保存。请联系管理员处理。"
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
                return

            if new_balance < 0:
                logger.warning(f"任务 {task_id}: 额度不足，无法扣减 {actual_count} 条")
                task.status = "failed"
                task.progress = 100
                task.progress_message = "额度不足"
                task.error_message = (
                    f"额度不足：本次匹配 {actual_count} 条，但当前余额不足，结果未保存。"
                )
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
                return

        _assert_task_not_cancelled(session, task)
        save_results_to_db(session, task_uuid, results_list)
        _assert_task_not_cancelled(session, task)

        task.status = "completed"
        task.progress = 100
        task.progress_message = "匹配完成"
        task.stats = result.get("stats", {})
        task.output_path = excel_output if Path(excel_output).exists() else None
        task.json_output_path = json_output if Path(json_output).exists() else None
        task.completed_at = datetime.now(timezone.utc)
        session.commit()

        logger.info(f"任务 {task_id}: 匹配完成，共 {len(results_list)} 条结果")

    except TaskCancelled as exc:
        logger.info(f"任务 {task_id} 已取消: {exc}")
        try:
            session.rollback()
        except Exception:
            pass
        try:
            _mark_task_cancelled(session, task_uuid, str(exc))
        except Exception as db_err:
            logger.error(f"任务 {task_id} 更新取消状态失败: {db_err}")
    except Exception as exc:
        logger.error(f"任务 {task_id} 执行失败: {exc}")
        try:
            session.rollback()
            task = session.get(Task, task_uuid)
            if task:
                task.status = "failed"
                task.error_message = str(exc)[:1000]
                task.completed_at = datetime.now(timezone.utc)
                session.commit()
        except Exception as db_err:
            logger.error(f"任务 {task_id} 更新失败状态时出错: {db_err}")
        raise
    finally:
        session.close()


def _execute_local_match(session, task, task_id, file_path, params, output_dir, json_output, excel_output):
    """Run the local matcher in-process."""
    logger.info(
        f"任务 {task_id}: 本地匹配模式 (mode={params.get('mode')}, province={params.get('province')})"
    )

    import config as quota_config
    import main as auto_quota_main
    from src.excel_compat import ensure_openpyxl_input

    def _clean_ascii(val: str) -> str:
        if not val or not isinstance(val, str):
            return val or ""
        for ch in ["\ufeff", "\u200b", "\u200c", "\u200d", "\u200e", "\u200f", "\ufffe", "\u00a0"]:
            val = val.replace(ch, "")
        return val.strip().encode("ascii", errors="ignore").decode("ascii")

    try:
        from app.services.llm_config_service import get_llm_config_sync, get_verify_config_sync

        llm_cfg = get_llm_config_sync(session)
        llm_type = _clean_ascii(llm_cfg["llm_type"])
        api_key = _clean_ascii(llm_cfg["api_key"])
        base_url = _clean_ascii(llm_cfg["base_url"])
        model_name = _clean_ascii(llm_cfg["model"])

        if api_key:
            setattr(quota_config, f"{llm_type.upper()}_API_KEY", api_key)
            if base_url:
                setattr(quota_config, f"{llm_type.upper()}_BASE_URL", base_url)
            if model_name:
                setattr(quota_config, f"{llm_type.upper()}_MODEL", model_name)
            quota_config.AGENT_LLM = llm_type

        v_cfg = get_verify_config_sync(session)
        v_type = _clean_ascii(v_cfg["llm_type"])
        v_key = _clean_ascii(v_cfg["api_key"])
        v_url = _clean_ascii(v_cfg["base_url"])
        v_model = _clean_ascii(v_cfg["model"])

        if v_type and v_key:
            setattr(quota_config, f"{v_type.upper()}_API_KEY", v_key)
            if v_url:
                setattr(quota_config, f"{v_type.upper()}_BASE_URL", v_url)
            if v_model:
                setattr(quota_config, f"{v_type.upper()}_MODEL", v_model)
            quota_config.VERIFY_LLM = v_type
            if v_model:
                quota_config.VERIFY_MODEL = v_model
        elif not v_type:
            quota_config.VERIFY_LLM = ""
            quota_config.VERIFY_MODEL = ""

        verify_label = (
            f"{quota_config.VERIFY_LLM}/{quota_config.VERIFY_MODEL}"
            if getattr(quota_config, "VERIFY_LLM", "")
            else "同匹配模型"
        )
        logger.info(
            f"任务 {task_id}: 从数据库加载模型配置 -> 匹配:{llm_type}/{model_name}，验证:{verify_label}"
        )
    except Exception as exc:
        logger.warning(f"任务 {task_id}: 读取模型配置失败，回退环境变量: {exc}")

    province = params.get("province", "")
    aux_provinces = quota_config.get_sibling_provinces(province)
    if aux_provinces:
        logger.info(f"任务 {task_id}: 自动挂载同批辅助库 {aux_provinces}")

    progress_cb = _make_progress_callback(session, task, output_dir)
    original_input = Path(file_path)
    processing_input, normalize_result = ensure_openpyxl_input(
        original_input,
        Path(output_dir) / "input_importable.xlsx",
    )
    if normalize_result:
        logger.info(
            f"任务 {task_id}: 已自动转换为可导入 .xlsx "
            f"(method={normalize_result.method}, preserved={normalize_result.preserved_formatting})"
        )

    _assert_task_not_cancelled(session, task)
    result = auto_quota_main.run(
        input_file=str(processing_input),
        mode=params.get("mode", "search"),
        output=excel_output,
        province=params.get("province"),
        aux_provinces=aux_provinces or None,
        sheet=params.get("sheet"),
        limit=params.get("limit"),
        agent_llm=params.get("agent_llm"),
        json_output=json_output,
        no_experience=params.get("no_experience", False),
        interactive=False,
        progress_callback=progress_cb,
        original_file=str(original_input),
        task_id=str(task_id),
    )
    _assert_task_not_cancelled(session, task)
    return result


def _execute_remote_match(session, task, task_id, file_path, params, output_dir, json_output, excel_output):
    """Forward the job to a remote match service and mirror progress locally."""
    import json as _json

    from app.config import LOCAL_MATCH_API_KEY, LOCAL_MATCH_URL
    from app.services.remote_match import RemoteMatchClient

    logger.info(
        f"任务 {task_id}: 远程匹配模式 -> {LOCAL_MATCH_URL} "
        f"(mode={params.get('mode')}, province={params.get('province')})"
    )

    if not LOCAL_MATCH_URL:
        raise RuntimeError("远程匹配模式需要配置 LOCAL_MATCH_URL")

    client = RemoteMatchClient(base_url=LOCAL_MATCH_URL, api_key=LOCAL_MATCH_API_KEY)
    _assert_task_not_cancelled(session, task)

    health = client.check_health()
    if not health:
        raise RuntimeError("无法连接本地匹配服务，请检查 LOCAL_MATCH_URL 和服务状态")
    logger.info(
        f"任务 {task_id}: 本地匹配服务在线 "
        f"(version={health.get('version')}, active_tasks={health.get('active_tasks')})"
    )

    match_id = client.submit_match(file_path, params)
    logger.info(f"任务 {task_id}: 已提交到远程匹配服务，远程 ID={match_id}")

    while True:
        time.sleep(3)
        _assert_task_not_cancelled(session, task)
        progress = client.poll_progress(match_id)
        _assert_task_not_cancelled(session, task)

        try:
            task.progress = progress.get("progress", 0)
            task.progress_current = progress.get("current_idx", 0)
            task.progress_message = progress.get("message", "")
            session.commit()
        except Exception as exc:
            logger.debug(f"远程进度写入 DB 失败: {exc}")
            try:
                session.rollback()
            except Exception:
                pass

        status = progress.get("status", "running")
        if status == "completed":
            logger.info(f"任务 {task_id}: 远程匹配完成")
            break
        if status == "failed":
            error_msg = progress.get("error", "未知错误")
            raise RuntimeError(f"远程匹配失败: {error_msg}")

    _assert_task_not_cancelled(session, task)
    result = client.get_results(match_id)
    _assert_task_not_cancelled(session, task)
    logger.info(f"任务 {task_id}: 已获取远程匹配结果，共 {len(result.get('results', []))} 条")

    output_dir.mkdir(parents=True, exist_ok=True)
    if json_output:
        Path(json_output).parent.mkdir(parents=True, exist_ok=True)
        with open(json_output, "w", encoding="utf-8") as f:
            _json.dump(result, f, ensure_ascii=False, indent=2)

    client.download_excel(match_id, excel_output)
    _assert_task_not_cancelled(session, task)
    return result
