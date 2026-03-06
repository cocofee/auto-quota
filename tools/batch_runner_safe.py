"""
智能批量匹配调度器 - 根据系统资源自动调节跑批节奏

规则：
- 内存使用 > 85%：暂停，等资源释放
- 内存使用 < 75%：可以启动下一个省
- 一次只跑一个省，跑完检查资源再决定是否继续
- 按待匹配文件数从少到多排序
- 支持 --batch N 参数，每跑N个省自动停（默认5）

用法：
  python tools/batch_runner_safe.py              # 每批跑5个省
  python tools/batch_runner_safe.py --batch 3    # 每批跑3个省
  python tools/batch_runner_safe.py --batch 0    # 不限制，跑完全部

前置依赖：pip install psutil（需要提前安装）
"""

import subprocess
import sys
import time
import sqlite3
import os
import argparse

import psutil  # 需要提前安装：pip install psutil

# 阈值设置
MEM_PAUSE_THRESHOLD = 85   # 内存超过85%就暂停（31.8GB的85%≈27GB，留4.8GB防卡死）
MEM_RESUME_THRESHOLD = 75  # 内存降到75%以下才继续（≈23.8GB）
CHECK_INTERVAL = 30        # 暂停时每30秒检查一次

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "output", "batch", "batch.db")


def get_memory_percent():
    """获取当前内存使用百分比"""
    return psutil.virtual_memory().percent


def get_pending_provinces():
    """获取待匹配的省份列表，按文件数从少到多排序"""
    db = os.path.abspath(DB_PATH)
    if not os.path.exists(db):
        print(f"数据库不存在: {db}")
        return []

    conn = sqlite3.connect(db)
    rows = conn.execute("""
        SELECT province, COUNT(*) as cnt
        FROM file_registry
        WHERE province IS NOT NULL AND status = 'scanned'
        GROUP BY province
        ORDER BY cnt ASC
    """).fetchall()
    conn.close()
    return [(r[0], r[1]) for r in rows]


def wait_for_resources():
    """等待系统资源恢复到安全水平"""
    mem = get_memory_percent()
    if mem < MEM_PAUSE_THRESHOLD:
        return True

    print(f"\n  内存 {mem:.0f}% 超过阈值 {MEM_PAUSE_THRESHOLD}%，暂停等待...")
    while True:
        time.sleep(CHECK_INTERVAL)
        mem = get_memory_percent()
        if mem < MEM_RESUME_THRESHOLD:
            print(f"  内存恢复到 {mem:.0f}%，继续跑")
            return True
        else:
            print(f"  内存 {mem:.0f}%，继续等待...", end="\r")


def run_province(province, file_count):
    """跑一个省的批量匹配"""
    print(f"\n{'='*50}")
    print(f"开始: {province} ({file_count}个文件)")
    mem = get_memory_percent()
    print(f"当前内存: {mem:.0f}%")
    print(f"{'='*50}")

    cmd = [
        sys.executable, "tools/batch_runner.py",
        "--province", province
    ]

    project_dir = os.path.join(os.path.dirname(__file__), "..")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    result = subprocess.run(
        cmd,
        cwd=project_dir,
        env=env,
        timeout=7200,  # 单省最长2小时
        capture_output=False  # 直接输出到控制台
    )

    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="智能批量匹配调度器")
    parser.add_argument("--batch", type=int, default=5,
                        help="每批跑几个省就停（默认5，0=不限制跑完全部）")
    args = parser.parse_args()

    batch_size = args.batch

    print("=== 智能批量匹配调度器 ===")
    print(f"暂停阈值: 内存 > {MEM_PAUSE_THRESHOLD}%")
    print(f"恢复阈值: 内存 < {MEM_RESUME_THRESHOLD}%")
    if batch_size > 0:
        print(f"每批跑: {batch_size} 个省")
    else:
        print(f"模式: 全部跑完")
    print()

    # 获取待跑省份
    provinces = get_pending_provinces()
    if not provinces:
        print("没有待匹配的省份了！")
        return

    # 如果有batch限制，只取前N个
    if batch_size > 0:
        run_list = provinces[:batch_size]
    else:
        run_list = provinces

    total_files = sum(c for _, c in run_list)
    remaining_provinces = len(provinces) - len(run_list)

    print(f"本批跑: {len(run_list)}个省份, 共{total_files}个文件")
    if remaining_provinces > 0:
        print(f"排队中: 还有{remaining_provinces}个省份等下一批")
    print("本批队列:")
    for p, c in run_list:
        print(f"  {p}: {c}个文件")
    print()

    # 逐省跑
    completed = 0
    failed = 0
    for province, file_count in run_list:
        # 跑之前检查资源
        wait_for_resources()

        try:
            success = run_province(province, file_count)
            if success:
                completed += 1
            else:
                failed += 1
                print(f"  {province} 匹配出错，跳过继续")
        except subprocess.TimeoutExpired:
            failed += 1
            print(f"  {province} 超时(2小时)，跳过继续")
        except KeyboardInterrupt:
            print(f"\n\n用户中断，已完成 {completed} 个省份")
            break

        # 跑完一个省，打印进度
        done_in_batch = completed + failed
        left_in_batch = len(run_list) - done_in_batch
        mem = get_memory_percent()
        print(f"\n本批进度: {done_in_batch}/{len(run_list)} (成功{completed} 失败{failed} 剩余{left_in_batch}) | 内存{mem:.0f}%")

    print(f"\n=== 本批完成 ===")
    print(f"成功: {completed}, 失败: {failed}")
    if remaining_provinces > 0:
        print(f"还有 {remaining_provinces} 个省份等下一批，再跑一次即可继续")
        print(f"命令: python tools/batch_runner_safe.py --batch {batch_size}")


if __name__ == "__main__":
    main()
