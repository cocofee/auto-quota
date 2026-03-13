"""
阶段二自动循环的轻量状态管理器。

提供 3 个护栏能力：
1. `current_priority_queue` 持久化（热启动）
2. 轮次结果记录（keep/discard / delta）
3. 边际收益递减检测（最近5轮/10轮平均涨幅）

用法示例：
  python tools/autoresearch_manager.py show
  python tools/autoresearch_manager.py queue --active "P1: 同义词缺口" --carry "P2: 电气搜索词偏差"
  python tools/autoresearch_manager.py round --direction "P1: 同义词缺口" --delta -0.2 --result discard --carry "P3: 福建园林排序偏差"
  python tools/autoresearch_manager.py marginal
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = PROJECT_ROOT / "output" / "temp" / "autoresearch_state.json"


def _default_state() -> dict:
    return {
        "updated_at": "",
        "current_priority_queue": {
            "active": [],
            "carry_over": [],
        },
        "recent_rounds": [],
    }


def load_state() -> dict:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _default_state()
        state = _default_state()
        state.update(data)
        cpq = state.get("current_priority_queue") or {}
        state["current_priority_queue"] = {
            "active": list(cpq.get("active", [])),
            "carry_over": list(cpq.get("carry_over", [])),
        }
        state["recent_rounds"] = list(state.get("recent_rounds", []))
        return state
    except (json.JSONDecodeError, OSError):
        return _default_state()


def save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = dict(state)
    state["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _dedupe_keep_order(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def update_queue(active: list[str] | None = None,
                 carry_over: list[str] | None = None,
                 clear_active: bool = False,
                 clear_carry: bool = False) -> dict:
    state = load_state()
    queue = state["current_priority_queue"]
    if clear_active:
        queue["active"] = []
    if clear_carry:
        queue["carry_over"] = []
    if active:
        queue["active"] = _dedupe_keep_order(list(active))
    if carry_over:
        queue["carry_over"] = _dedupe_keep_order(queue["carry_over"] + list(carry_over))
    save_state(state)
    return state


def record_round(direction: str, delta: float, result: str,
                 note: str = "", carry_over: list[str] | None = None) -> dict:
    state = load_state()
    rounds = state["recent_rounds"]
    round_no = (rounds[-1]["round"] + 1) if rounds else 1
    rounds.append({
        "round": round_no,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "direction": direction,
        "delta": float(delta),
        "result": result,
        "note": note,
    })
    if len(rounds) > 50:
        state["recent_rounds"] = rounds[-50:]

    queue = state["current_priority_queue"]
    queue["active"] = [direction] if direction else queue.get("active", [])
    if carry_over:
        queue["carry_over"] = _dedupe_keep_order(queue.get("carry_over", []) + list(carry_over))
    save_state(state)
    return state


def marginal_analysis(state: dict | None = None) -> dict:
    state = state or load_state()
    rounds = state.get("recent_rounds", [])
    deltas = [float(item.get("delta", 0.0)) for item in rounds]

    avg5 = round(sum(deltas[-5:]) / len(deltas[-5:]), 4) if deltas[-5:] else None
    avg10 = round(sum(deltas[-10:]) / len(deltas[-10:]), 4) if deltas[-10:] else None
    rounds_count = len(deltas)

    recommendation = "continue"
    reason = "样本不足，继续当前方向"
    if rounds_count >= 10 and avg10 is not None and avg10 < 0.05:
        recommendation = "saturated"
        reason = "最近10轮平均涨幅 < 0.05%，当前方向已饱和"
    elif rounds_count >= 5 and avg5 is not None and avg5 < 0.1:
        recommendation = "switch_direction"
        reason = "最近5轮平均涨幅 < 0.1%，建议切换优先级方向"

    return {
        "rounds": len(deltas),
        "avg5": avg5,
        "avg10": avg10,
        "recommendation": recommendation,
        "reason": reason,
    }


def _cmd_show(_args):
    state = load_state()
    print(json.dumps(state, ensure_ascii=False, indent=2))


def _cmd_queue(args):
    state = update_queue(
        active=args.active,
        carry_over=args.carry,
        clear_active=args.clear_active,
        clear_carry=args.clear_carry,
    )
    print(json.dumps(state["current_priority_queue"], ensure_ascii=False, indent=2))


def _cmd_round(args):
    state = record_round(
        direction=args.direction,
        delta=args.delta,
        result=args.result,
        note=args.note,
        carry_over=args.carry,
    )
    print(json.dumps(state["recent_rounds"][-1], ensure_ascii=False, indent=2))
    print(json.dumps(marginal_analysis(state), ensure_ascii=False, indent=2))


def _cmd_marginal(_args):
    print(json.dumps(marginal_analysis(), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="阶段二 autoresearch 状态管理器")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="显示当前 autoresearch 状态")

    p_queue = sub.add_parser("queue", help="更新 current_priority_queue")
    p_queue.add_argument("--active", action="append", help="当前激活方向，可重复传入")
    p_queue.add_argument("--carry", action="append", help="未处理候选方向，可重复传入")
    p_queue.add_argument("--clear-active", action="store_true", help="清空 active")
    p_queue.add_argument("--clear-carry", action="store_true", help="清空 carry_over")

    p_round = sub.add_parser("round", help="记录一轮 keep/discard 结果")
    p_round.add_argument("--direction", required=True, help="本轮方向")
    p_round.add_argument("--delta", type=float, required=True, help="相对上一轮命中率变化（百分点）")
    p_round.add_argument("--result", choices=["keep", "discard"], required=True,
                         help="本轮结果")
    p_round.add_argument("--note", default="", help="本轮备注")
    p_round.add_argument("--carry", action="append", help="下轮优先处理方向")

    sub.add_parser("marginal", help="评估边际收益是否递减")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "show":
        _cmd_show(args)
    elif args.command == "queue":
        _cmd_queue(args)
    elif args.command == "round":
        _cmd_round(args)
    elif args.command == "marginal":
        _cmd_marginal(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
