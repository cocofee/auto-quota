# -*- coding: utf-8 -*-
"""
准确率趋势查看工具

用法:
  python tools/accuracy_trend.py          # 显示最近20次运行趋势
  python tools/accuracy_trend.py --last 50  # 显示最近50次
"""

import argparse

from src.accuracy_tracker import AccuracyTracker


def main():
    parser = argparse.ArgumentParser(description="查看系统准确率趋势")
    parser.add_argument("--last", type=int, default=20, help="显示最近N次运行记录")
    args = parser.parse_args()

    tracker = AccuracyTracker()
    tracker.show_trend(last_n=args.last)


if __name__ == "__main__":
    main()
