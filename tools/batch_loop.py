"""
全专业批量匹配循环脚本
每批跑10个标准清单，间隔30秒，自动轮换专业（优先跑量大的）
按 Ctrl+C 随时停止，已跑的不会丢
"""
import subprocess
import sqlite3
import sys
import time
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "output" / "batch" / "batch.db"
BATCH_SIZE = 10   # 每批跑几个
COOLDOWN = 30     # 每批间隔秒数


def get_pending_stats():
    """查询各专业待处理数量"""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT specialty, COUNT(*) FROM file_registry "
        "WHERE format='standard_bill' AND status='scanned' "
        "GROUP BY specialty ORDER BY COUNT(*) DESC"
    ).fetchall()
    conn.close()
    return rows


def get_matched_stats():
    """查询已完成统计"""
    conn = sqlite3.connect(str(DB_PATH))
    matched = conn.execute(
        "SELECT COUNT(*) FROM file_registry WHERE format='standard_bill' AND status='matched'"
    ).fetchone()[0]
    error = conn.execute(
        "SELECT COUNT(*) FROM file_registry WHERE format='standard_bill' AND status='error'"
    ).fetchone()[0]
    conn.close()
    return matched, error


def main():
    print("=" * 50)
    print("  全专业批量匹配（每批%d个，间隔%d秒）" % (BATCH_SIZE, COOLDOWN))
    print("  按 Ctrl+C 随时停止，已跑的不会丢")
    print("=" * 50)
    print()

    # 显示初始状态
    pending = get_pending_stats()
    if not pending:
        print("没有待处理的文件，退出")
        return

    total = sum(r[1] for r in pending)
    print("待处理标准清单：")
    for spec, cnt in pending:
        print(f"  {spec}: {cnt}个")
    print(f"  合计: {total}个")
    print()

    project_dir = Path(__file__).parent.parent

    # 让用户选专业
    pending = get_pending_stats()
    print("请选择要跑的专业（输入序号）：")
    for i, (spec, cnt) in enumerate(pending, 1):
        print(f"  {i}. {spec}（{cnt}个）")
    print()
    choice = input("序号: ").strip()
    try:
        idx = int(choice) - 1
        specialty = pending[idx][0]
    except (ValueError, IndexError):
        print("输入有误，退出")
        return

    spec_count = pending[idx][1]
    print()
    limit_input = input(f"跑几个？（直接回车=全部{spec_count}个）: ").strip()
    max_files = int(limit_input) if limit_input.isdigit() else spec_count

    print()
    print("=" * 50)
    print(f"  开始跑【{specialty}】（目标{max_files}个，共{spec_count}个待处理）")
    print("=" * 50)
    print()

    # 循环跑这个专业
    batch_num = 0
    done_count = 0
    while done_count < max_files:
        conn = sqlite3.connect(str(DB_PATH))
        left = conn.execute(
            "SELECT COUNT(*) FROM file_registry "
            "WHERE format='standard_bill' AND status='scanned' AND specialty=?",
            (specialty,)
        ).fetchone()[0]
        conn.close()

        if left == 0:
            break

        # 这批实际跑的数量
        this_batch = min(BATCH_SIZE, max_files - done_count)
        batch_num += 1
        t = time.strftime("%H:%M:%S")
        print(f"[{t}] 第{batch_num}批（{this_batch}个）| 已跑{done_count}/{max_files} | 剩余{left}个")

        cmd = [
            sys.executable, str(project_dir / "tools" / "batch_runner.py"),
            "--specialty", specialty,
            "--format", "standard_bill",
            "--limit", str(this_batch)
        ]
        try:
            env = {**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            result = subprocess.run(
                cmd, cwd=str(project_dir),
                capture_output=True, timeout=600,
                env=env
            )
            try:
                stdout = result.stdout.decode("utf-8")
            except UnicodeDecodeError:
                stdout = result.stdout.decode("gbk", errors="replace")
            try:
                stderr = result.stderr.decode("utf-8")
            except UnicodeDecodeError:
                stderr = result.stderr.decode("gbk", errors="replace")
            for line in stdout.split("\n"):
                if any(kw in line for kw in ["成功", "失败", "清单总条数", "待处理", "条 |", "跳过"]):
                    print(f"  {line.strip()}")
            if result.returncode != 0 and stderr:
                err_lines = stderr.strip().split("\n")[-3:]
                for line in err_lines:
                    print(f"  [错误] {line.strip()}")
        except subprocess.TimeoutExpired:
            print("  [超时] 本批超过10分钟，跳过")
        except KeyboardInterrupt:
            print("\n用户中断，退出")
            return

        done_count += this_batch

        # 还没到目标就休息
        if done_count < max_files:
            t = time.strftime("%H:%M:%S")
            print(f"[{t}] 休息{COOLDOWN}秒...")
            try:
                time.sleep(COOLDOWN)
            except KeyboardInterrupt:
                print("\n用户中断，退出")
                return

    # 跑完统计
    matched, error = get_matched_stats()
    print()
    print("=" * 50)
    print(f"  【{specialty}】全部跑完！")
    print(f"  可以去 Claude Code 用算法Agent分析：")
    print(f"    /algo-agent 分析{specialty}的匹配结果")
    print("=" * 50)
    print()
    print(f"全局统计：已匹配{matched} | 出错{error}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户中断，退出")
    input("\n按回车关闭...")
