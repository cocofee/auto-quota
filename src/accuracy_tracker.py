# -*- coding: utf-8 -*-
"""
准确率追踪 — 记录每次运行的关键指标，追踪系统是否在进化

核心指标：
- 经验库直通率（越高=系统越"聪明"，但如果错误率同步升则有问题）
- 高置信度比例（越高越好）
- 审核纠正率（越低越好，说明匹配准确）
- 用户手动修正率（最关键：下降=真的在变好）

用法：
  from src.accuracy_tracker import AccuracyTracker
  tracker = AccuracyTracker()
  tracker.record_run(stats, input_file, mode, province)  # 每次匹配后调用
  tracker.show_trend()                                    # 查看趋势
"""

import sqlite3
import time
from pathlib import Path

from loguru import logger

import config
from db.sqlite import connect_init as _db_connect_init


# 数据库路径：和经验库放一起
_DB_PATH = config.COMMON_DB_DIR / "run_history.db"


def _get_conn() -> sqlite3.Connection:
    """获取数据库连接，不存在则自动创建表。"""
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _db_connect_init(_DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,           -- 运行日期 (YYYY-MM-DD)
            run_time TEXT NOT NULL,           -- 运行时间 (HH:MM:SS)
            input_file TEXT,                  -- 输入文件名
            mode TEXT,                        -- 匹配模式 (search/agent)
            province TEXT,                    -- 省份

            total INTEGER,                    -- 总清单项数
            matched INTEGER,                  -- 已匹配数
            high_conf INTEGER,                -- 高置信度(≥85)数
            mid_conf INTEGER,                 -- 中置信度(60-85)数
            low_conf INTEGER,                 -- 低置信度(<60)数
            exp_hits INTEGER,                 -- 经验库命中数
            review_rejected INTEGER DEFAULT 0, -- 审核规则拦截的经验库直通数

            elapsed REAL,                     -- 耗时（秒）
            created_at REAL                   -- 记录创建时间戳
        )
    """)
    # 审核记录表：记录Jarvis审核纠正的统计
    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_date TEXT NOT NULL,
            run_time TEXT NOT NULL,
            input_file TEXT,
            province TEXT,

            total INTEGER,                    -- 总匹配条数
            auto_corrections INTEGER,         -- 自动纠正条数
            manual_items INTEGER,             -- 需人工处理条数
            measure_items INTEGER,            -- 措施项（不套定额）条数
            correct_count INTEGER,            -- 审核通过（无错）条数

            created_at REAL
        )
    """)
    conn.commit()
    return conn


class AccuracyTracker:
    """准确率追踪器：记录运行指标、查看历史趋势。"""

    def record_run(self, stats: dict, input_file: str = "",
                   mode: str = "", province: str = ""):
        """
        记录一次运行的统计指标。

        参数:
            stats: _build_run_stats() 返回的统计字典
            input_file: 输入文件路径
            mode: 匹配模式
            province: 省份
        """
        now = time.localtime()
        run_date = time.strftime("%Y-%m-%d", now)
        run_time = time.strftime("%H:%M:%S", now)
        file_name = Path(input_file).name if input_file else ""

        conn = None
        try:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO run_history
                (run_date, run_time, input_file, mode, province,
                 total, matched, high_conf, mid_conf, low_conf,
                 exp_hits, review_rejected, elapsed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_date, run_time, file_name, mode, province,
                stats.get("total", 0),
                stats.get("matched", 0),
                stats.get("high_conf", 0),
                stats.get("mid_conf", 0),
                stats.get("low_conf", 0),
                stats.get("exp_hits", 0),
                stats.get("review_rejected", 0),
                stats.get("elapsed", 0),
                time.time(),
            ))
            conn.commit()
            logger.debug(f"运行记录已保存: {run_date} {run_time}")
        except Exception as e:
            logger.warning(f"保存运行记录失败（不影响主流程）: {e}")
        finally:
            if conn is not None:
                conn.close()

    def record_review(self, input_file: str = "", province: str = "",
                      total: int = 0, auto_corrections: int = 0,
                      manual_items: int = 0, measure_items: int = 0,
                      correct_count: int = 0):
        """
        记录一次Jarvis审核的统计指标。

        参数:
            input_file: 输入文件路径
            province: 省份
            total: 总匹配条数
            auto_corrections: 自动纠正条数
            manual_items: 需人工处理条数
            measure_items: 措施项条数
            correct_count: 审核通过（无错）条数
        """
        now = time.localtime()
        run_date = time.strftime("%Y-%m-%d", now)
        run_time = time.strftime("%H:%M:%S", now)
        file_name = Path(input_file).name if input_file else ""

        conn = None
        try:
            conn = _get_conn()
            conn.execute("""
                INSERT INTO review_history
                (run_date, run_time, input_file, province,
                 total, auto_corrections, manual_items,
                 measure_items, correct_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_date, run_time, file_name, province,
                total, auto_corrections, manual_items,
                measure_items, correct_count, time.time(),
            ))
            conn.commit()
            logger.debug(f"审核记录已保存: {run_date} {run_time}")
        except Exception as e:
            logger.warning(f"保存审核记录失败（不影响主流程）: {e}")
        finally:
            if conn is not None:
                conn.close()

    def show_trend(self, last_n: int = 20):
        """
        显示最近 N 次运行的趋势。

        输出格式：每行一条运行记录，显示关键比率。
        """
        conn = None
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT run_date, run_time, input_file, mode,
                       total, matched, high_conf, mid_conf, low_conf,
                       exp_hits, review_rejected, elapsed
                FROM run_history
                ORDER BY id DESC
                LIMIT ?
            """, (last_n,)).fetchall()
        except Exception as e:
            print(f"读取运行记录失败: {e}")
            return
        finally:
            if conn is not None:
                conn.close()

        if not rows:
            print("暂无匹配运行记录。")
            # 仍然显示审核记录（可能先跑了审核）
            self._show_review_history(last_n)
            return

        # 按时间正序显示
        rows = list(reversed(rows))

        print("=" * 90)
        print("系统准确率趋势报告")
        print("=" * 90)
        print(f"{'日期':<12} {'文件':<20} {'模式':<7} "
              f"{'总数':>4} {'匹配率':>6} {'绿率':>5} {'经验命中':>8} "
              f"{'审核拦截':>8} {'耗时':>6}")
        print("-" * 90)

        for row in rows:
            (date, time_str, file_name, mode,
             total, matched, high, mid, low,
             exp_hits, review_rej, elapsed) = row

            total = total or 1  # 避免除零
            match_rate = f"{matched * 100 // total}%"
            green_rate = f"{high * 100 // total}%"
            exp_rate = f"{exp_hits}({exp_hits * 100 // total}%)"
            rej_count = f"{review_rej}" if review_rej else "-"
            elapsed_str = f"{elapsed:.0f}s"
            short_file = (file_name[:18] + "..") if len(file_name) > 20 else file_name

            print(f"{date:<12} {short_file:<20} {mode:<7} "
                  f"{total:>4} {match_rate:>6} {green_rate:>5} {exp_rate:>8} "
                  f"{rej_count:>8} {elapsed_str:>6}")

        print("=" * 90)

        # 趋势判断：比较最近5次和之前5次
        if len(rows) >= 10:
            recent = rows[-5:]
            earlier = rows[-10:-5]

            def avg_green_rate(records):
                totals = sum(r[4] or 1 for r in records)
                greens = sum(r[6] for r in records)
                return greens / max(totals, 1)

            def avg_exp_rate(records):
                totals = sum(r[4] or 1 for r in records)
                exps = sum(r[9] for r in records)
                return exps / max(totals, 1)

            recent_green = avg_green_rate(recent)
            earlier_green = avg_green_rate(earlier)
            recent_exp = avg_exp_rate(recent)
            earlier_exp = avg_exp_rate(earlier)

            print("\n趋势分析（最近5次 vs 之前5次）:")
            delta_green = recent_green - earlier_green
            delta_exp = recent_exp - earlier_exp
            arrow_green = "↑" if delta_green > 0.02 else ("↓" if delta_green < -0.02 else "→")
            arrow_exp = "↑" if delta_exp > 0.02 else ("↓" if delta_exp < -0.02 else "→")
            print(f"  高置信度率: {earlier_green:.0%} → {recent_green:.0%} {arrow_green}")
            print(f"  经验命中率: {earlier_exp:.0%} → {recent_exp:.0%} {arrow_exp}")

            if delta_green > 0.02 and delta_exp > 0.02:
                print("  结论: 系统在正向进化，经验积累有效")
            elif delta_exp > 0.05 and delta_green < -0.02:
                print("  警告: 经验命中率升高但准确率下降，可能有错误数据污染经验库！")
            elif delta_green < -0.05:
                print("  警告: 准确率明显下降，建议运行经验库体检")

        # 显示审核纠正记录
        self._show_review_history(last_n)

    def _show_review_history(self, last_n: int = 10):
        """显示最近的Jarvis审核纠正记录。"""
        conn = None
        try:
            conn = _get_conn()
            rows = conn.execute("""
                SELECT run_date, run_time, input_file, province,
                       total, auto_corrections, manual_items,
                       measure_items, correct_count
                FROM review_history
                ORDER BY id DESC
                LIMIT ?
            """, (last_n,)).fetchall()
        except Exception:
            return
        finally:
            if conn is not None:
                conn.close()

        if not rows:
            return

        rows = list(reversed(rows))

        print("\n" + "=" * 80)
        print("Jarvis审核纠正记录")
        print("=" * 80)
        print(f"{'日期':<12} {'文件':<20} "
              f"{'总数':>4} {'自动纠正':>8} {'需人工':>6} {'无错':>4} {'纠正率':>6}")
        print("-" * 80)

        for row in rows:
            (date, time_str, file_name, province,
             total, auto_corr, manual, measure, correct) = row

            total = total or 1
            corr_rate = f"{auto_corr * 100 // total}%"
            short_file = (file_name[:18] + "..") if len(file_name) > 20 else file_name

            print(f"{date:<12} {short_file:<20} "
                  f"{total:>4} {auto_corr:>8} {manual:>6} {correct:>4} {corr_rate:>6}")

        print("=" * 80)
        print("  纠正率越低越好（说明匹配越准确）")


# ============================================================
# 命令行入口（供 bat 脚本直接调用）
# ============================================================

if __name__ == "__main__":
    # 重新导入（sys.path 修改后才能找到 config）
    import config as _cfg
    _DB_PATH = _cfg.COMMON_DB_DIR / "run_history.db"
    tracker = AccuracyTracker()
    tracker.show_trend()
