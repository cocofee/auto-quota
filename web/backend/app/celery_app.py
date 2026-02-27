"""
Celery 应用配置

Celery 是一个异步任务队列：把耗时的匹配任务放到后台执行，
不阻塞Web请求。用户提交任务后立即返回，后台慢慢跑。

启动 Celery Worker（处理后台任务的进程）:
    cd web/backend
    celery -A app.celery_app worker --loglevel=info --pool=solo
    (Windows上必须用 --pool=solo，因为Windows不支持fork)
"""

import sys
from pathlib import Path

from celery import Celery
from loguru import logger
from app.config import REDIS_URL, PROJECT_ROOT

# 把项目根目录加入 Python 路径
# Celery worker 需要 import main（现有匹配入口）和 config（定额配置）等
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# 日志持久化：Celery worker 的日志写入文件（和 Web 后端共享 /app/logs/ 目录）
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger.add(
    str(LOG_DIR / "celery_{time:YYYY-MM-DD}.log"),
    rotation="00:00",     # 每天零点新建一个日志文件
    retention="30 days",  # 保留30天
    encoding="utf-8",
    level="INFO",
)

# 创建 Celery 应用实例
# broker: 消息中间件（Redis），任务发送到这里排队
# backend: 结果存储（也用Redis），任务执行结果存在这里
celery_app = Celery(
    "auto_quota",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

# Celery 配置
celery_app.conf.update(
    # 任务序列化方式（JSON更安全，pickle有远程代码执行风险）
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # 时区
    timezone="Asia/Shanghai",

    # 任务结果过期时间（24小时后自动清除）
    result_expires=86400,

    # 任务超时（匹配任务可能很耗时，硬限制30分钟，软限制28分钟提前通知）
    task_time_limit=1800,
    task_soft_time_limit=1680,

    # 存储完整的异常信息（便于调试失败的任务）
    result_extended=True,

    # 每个Worker同时执行的任务数（匹配任务比较重，不要并发太多）
    worker_concurrency=2,

    # 自动发现 app/tasks/ 目录下的任务
    include=["app.tasks.match_task", "app.tasks.benchmark_task"],
)
