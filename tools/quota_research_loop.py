"""Quota research loop V2.

把当前阶段二的研究节奏抽象成模板化 loop runner：
- 读 active direction
- 解析成模板
- 生成 fast/full 评测计划
- 记录 loop 状态与实验日志

V2 在 V1 的计划层之上，增加：
- fast/full benchmark 执行
- 结果判定（bootstrap / keep / discard / crash）
- loop 状态中的模板基线沉淀
- 可选写回 autoresearch_manager round 记录
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools import autoresearch_manager as arm


STATE_PATH = PROJECT_ROOT / "output" / "temp" / "quota_research_loop_state.json"
EXPERIMENT_LOG_PATH = PROJECT_ROOT / "output" / "temp" / "quota_research_experiments.jsonl"
RUNS_DIR = PROJECT_ROOT / "output" / "temp" / "loop_runs"


TEMPLATES = [
    {
        "name": "distribution_box",
        "direction_keywords": ["配电箱", "配电柜"],
        "editable_files": ["src/query_builder.py"],
        "fast_benchmark_args": [
            "--json-only", "--install-only",
            "--item-keyword", "配电箱",
            "--item-keyword", "配电柜",
            "--max-items-per-province", "20",
        ],
        "full_benchmark_args": ["--json-only", "--install-only"],
        "notes": "对象模板优先，先打具体箱名/型号，再回落安装方式+半周长。",
    },
    {
        "name": "conduit",
        "direction_keywords": ["配管", "JDG", "KBG", "SC", "PVC"],
        "editable_files": ["src/query_builder.py"],
        "fast_benchmark_args": [
            "--json-only", "--install-only",
            "--item-keyword", "配管",
            "--item-keyword", "JDG",
            "--item-keyword", "KBG",
            "--item-keyword", "SC",
            "--item-keyword", "PVC",
            "--max-items-per-province", "30",
        ],
        "full_benchmark_args": ["--json-only", "--install-only"],
        "notes": "配管对象模板，重点看材质代号、敷设方式和部位命名。",
    },
    {
        "name": "cable_split",
        "direction_keywords": ["电缆", "终端头", "电缆头"],
        "editable_files": ["src/query_builder.py", "src/match_pipeline.py"],
        "fast_benchmark_args": [
            "--json-only", "--install-only",
            "--item-keyword", "电缆",
            "--item-keyword", "终端头",
            "--item-keyword", "电缆头",
            "--max-items-per-province", "30",
        ],
        "full_benchmark_args": ["--json-only", "--install-only"],
        "notes": "把敷设类和终端头类切开，减少错路由。",
    },
    {
        "name": "lamp_install",
        "direction_keywords": ["灯具", "灯", "吸顶", "壁灯", "标志灯"],
        "editable_files": ["src/query_builder.py"],
        "fast_benchmark_args": [
            "--json-only", "--install-only",
            "--item-keyword", "灯",
            "--item-keyword", "灯具",
            "--max-items-per-province", "30",
        ],
        "full_benchmark_args": ["--json-only", "--install-only"],
        "notes": "安装方式+灯具类型模板。",
    },
    {
        "name": "valve_family",
        "direction_keywords": ["阀门", "法兰", "管件", "软接头"],
        "editable_files": ["src/query_builder.py", "src/match_pipeline.py"],
        "fast_benchmark_args": [
            "--json-only", "--install-only",
            "--item-keyword", "阀门",
            "--item-keyword", "法兰",
            "--item-keyword", "管件",
            "--item-keyword", "软接头",
            "--max-items-per-province", "30",
        ],
        "full_benchmark_args": ["--json-only", "--install-only"],
        "notes": "高风险方向，必须分省/分册保守推进。",
    },
]


def _default_state() -> dict:
    return {
        "updated_at": "",
        "best_commit": "",
        "current_direction": "",
        "current_template": "",
        "recent_experiments": [],
        "template_metrics": {},
    }


def load_loop_state() -> dict:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default_state()
    state = _default_state()
    if isinstance(data, dict):
        state.update(data)
    state["recent_experiments"] = list(state.get("recent_experiments", []))
    state["template_metrics"] = dict(state.get("template_metrics", {}))
    return state


def save_loop_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    STATE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_experiment_log(record: dict) -> None:
    EXPERIMENT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EXPERIMENT_LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def _git(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=check,
    )


def git_current_commit() -> str:
    return (_git(["rev-parse", "HEAD"]).stdout or "").strip()


def git_current_branch() -> str:
    return (_git(["rev-parse", "--abbrev-ref", "HEAD"]).stdout or "").strip()


def git_status_short() -> str:
    return (_git(["status", "--short"]).stdout or "").strip()


def git_is_dirty() -> bool:
    return bool(git_status_short())


def git_reset_hard(commit: str) -> None:
    _git(["reset", "--hard", commit])


def git_commit_files(files: list[str], message: str) -> str:
    normalized = [path for path in files if path]
    if normalized:
        _git(["add", "--", *normalized])
    else:
        _git(["add", "-A"])
    _git(["commit", "-m", message])
    return git_current_commit()


def _replace_recent_experiment(state: dict, experiment_id: str, updates: dict) -> None:
    recent = state.get("recent_experiments", [])
    for item in recent:
        if item.get("experiment_id") == experiment_id:
            item.update(updates)
            break


def _find_recent_experiment(state: dict, experiment_id: str) -> dict | None:
    for item in reversed(state.get("recent_experiments", [])):
        if item.get("experiment_id") == experiment_id:
            return item
    return None


def _run_benchmark(args: list[str], timeout_sec: int,
                   summary_path: Path, log_path: Path) -> dict:
    command = [sys.executable, str(PROJECT_ROOT / "tools" / "run_benchmark.py")] + list(args)
    command += ["--summary-json-out", str(summary_path)]
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
        combined = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(combined, encoding="utf-8")
        if result.returncode != 0:
            return {
                "status": "crash",
                "error": f"benchmark exited with code {result.returncode}",
                "returncode": result.returncode,
                "log_path": str(log_path),
            }
        if not summary_path.exists():
            return {
                "status": "crash",
                "error": "summary json not generated",
                "returncode": result.returncode,
                "log_path": str(log_path),
            }
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return {
            "status": "ok",
            "summary": summary,
            "returncode": result.returncode,
            "log_path": str(log_path),
            "summary_path": str(summary_path),
        }
    except subprocess.TimeoutExpired:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(f"TIMEOUT after {timeout_sec}s", encoding="utf-8")
        return {
            "status": "crash",
            "error": f"timeout after {timeout_sec}s",
            "returncode": None,
            "log_path": str(log_path),
        }


def _get_overall_hit_rate(summary: dict) -> float:
    overall = (summary or {}).get("json_overall", {})
    return float(overall.get("hit_rate") or 0.0)


def _judge_experiment(template_metrics: dict,
                      fast_hit_rate: float,
                      full_hit_rate: float | None) -> tuple[str, float, str]:
    best_fast = template_metrics.get("best_fast_hit_rate")
    best_full = template_metrics.get("best_full_hit_rate")

    if best_fast is None:
        return "bootstrap", 0.0, "首次记录该模板基线"

    fast_delta = round(fast_hit_rate - float(best_fast), 1)
    if fast_delta < 0:
        return "discard", fast_delta, "快筛下降，直接丢弃"

    if full_hit_rate is None:
        return "keep", fast_delta, "快筛不下降，保留快筛结果"

    if best_full is None:
        return "keep", fast_delta, "首次记录该模板全量基线"

    full_delta = round(full_hit_rate - float(best_full), 1)
    if full_delta < 0:
        return "discard", full_delta, "全量下降，丢弃本轮"

    return "keep", full_delta, "全量不下降，保留本轮"


def _update_template_metrics(state: dict, template_name: str,
                             fast_hit_rate: float,
                             full_hit_rate: float | None) -> None:
    metrics = dict(state.get("template_metrics", {}).get(template_name, {}))
    best_fast = metrics.get("best_fast_hit_rate")
    if best_fast is None or fast_hit_rate >= float(best_fast):
        metrics["best_fast_hit_rate"] = round(fast_hit_rate, 1)
    if full_hit_rate is not None:
        best_full = metrics.get("best_full_hit_rate")
        if best_full is None or full_hit_rate >= float(best_full):
            metrics["best_full_hit_rate"] = round(full_hit_rate, 1)
    state.setdefault("template_metrics", {})[template_name] = metrics


def execute_experiment(direction: str = "",
                       idea: str = "",
                       template_name: str = "",
                       experiment_id: str = "",
                       fast_timeout_sec: int = 180,
                       full_timeout_sec: int = 900,
                       run_full: bool = True,
                       record_round: bool = False,
                       require_clean_git: bool = False,
                       git_reset_on_discard: bool = False,
                       git_commit_on_keep: bool = False,
                       commit_message: str = "") -> dict:
    record = _load_or_start_experiment(
        experiment_id=experiment_id,
        direction=direction,
        idea=idea,
        template_name=template_name,
        require_clean_git=require_clean_git,
    )
    experiment_id = record["experiment_id"]
    run_dir = RUNS_DIR / experiment_id
    fast_summary_path = run_dir / "fast_summary.json"
    fast_log_path = run_dir / "fast.log"
    fast_args = next(item for item in TEMPLATES if item["name"] == record["template"])["fast_benchmark_args"]
    fast_run = _run_benchmark(fast_args, fast_timeout_sec, fast_summary_path, fast_log_path)

    state = load_loop_state()
    if fast_run["status"] != "ok":
        result = {
            **record,
            "status": "crash",
            "error": fast_run.get("error", "fast benchmark failed"),
            "fast_log_path": fast_run.get("log_path", ""),
            "trial_commit": git_current_commit(),
            "trial_dirty": git_is_dirty(),
        }
        if git_reset_on_discard and record.get("base_commit") and not record.get("base_dirty"):
            git_reset_hard(record["base_commit"])
            result["git_action"] = f"reset --hard {record['base_commit']}"
        _replace_recent_experiment(state, experiment_id, {"status": "crash"})
        save_loop_state(state)
        append_experiment_log(result)
        return result

    fast_summary = fast_run["summary"]
    fast_hit_rate = _get_overall_hit_rate(fast_summary)

    template_metrics = state.get("template_metrics", {}).get(record["template"], {})
    best_fast = template_metrics.get("best_fast_hit_rate")
    if best_fast is not None and fast_hit_rate < float(best_fast):
        delta = round(fast_hit_rate - float(best_fast), 1)
        reason = "快筛下降，直接丢弃"
        _replace_recent_experiment(state, experiment_id, {
            "status": "discard",
            "fast_hit_rate": round(fast_hit_rate, 1),
            "delta": delta,
        })
        save_loop_state(state)
        result = {
            **record,
            "status": "discard",
            "delta": delta,
            "reason": reason,
            "fast_hit_rate": round(fast_hit_rate, 1),
            "full_hit_rate": None,
            "fast_log_path": fast_run.get("log_path", ""),
            "fast_summary_path": fast_run.get("summary_path", ""),
            "full_log_path": "",
            "full_summary_path": "",
            "trial_commit": git_current_commit(),
            "trial_dirty": git_is_dirty(),
        }
        if record_round:
            arm.record_round(
                direction=record["direction"],
                delta=delta,
                result="discard",
                note=f"{record['template']} | {reason}",
            )
        if git_reset_on_discard and record.get("base_commit") and not record.get("base_dirty"):
            git_reset_hard(record["base_commit"])
            result["git_action"] = f"reset --hard {record['base_commit']}"
        append_experiment_log(result)
        return result

    full_summary = None
    full_hit_rate = None
    full_run = None
    if run_full:
        full_summary_path = run_dir / "full_summary.json"
        full_log_path = run_dir / "full.log"
        full_args = next(item for item in TEMPLATES if item["name"] == record["template"])["full_benchmark_args"]
        full_run = _run_benchmark(full_args, full_timeout_sec, full_summary_path, full_log_path)
        if full_run["status"] == "ok":
            full_summary = full_run["summary"]
            full_hit_rate = _get_overall_hit_rate(full_summary)
        else:
            result = {
                **record,
                "status": "crash",
                "error": full_run.get("error", "full benchmark failed"),
                "fast_hit_rate": fast_hit_rate,
                "fast_log_path": fast_run.get("log_path", ""),
                "full_log_path": full_run.get("log_path", ""),
                "trial_commit": git_current_commit(),
                "trial_dirty": git_is_dirty(),
            }
            if git_reset_on_discard and record.get("base_commit") and not record.get("base_dirty"):
                git_reset_hard(record["base_commit"])
                result["git_action"] = f"reset --hard {record['base_commit']}"
            _replace_recent_experiment(state, experiment_id, {"status": "crash"})
            save_loop_state(state)
            append_experiment_log(result)
            return result

    status, delta, reason = _judge_experiment(template_metrics, fast_hit_rate, full_hit_rate)

    if status in {"bootstrap", "keep"}:
        _update_template_metrics(state, record["template"], fast_hit_rate, full_hit_rate)

    if record_round and status in {"keep", "discard"}:
        arm.record_round(
            direction=record["direction"],
            delta=delta,
            result=status,
            note=f"{record['template']} | {reason}",
        )

    _replace_recent_experiment(state, experiment_id, {
        "status": status,
        "fast_hit_rate": round(fast_hit_rate, 1),
        "full_hit_rate": round(full_hit_rate, 1) if full_hit_rate is not None else None,
        "delta": delta,
    })
    save_loop_state(state)

    result = {
        **record,
        "status": status,
        "delta": delta,
        "reason": reason,
        "fast_hit_rate": round(fast_hit_rate, 1),
        "full_hit_rate": round(full_hit_rate, 1) if full_hit_rate is not None else None,
        "fast_log_path": fast_run.get("log_path", ""),
        "fast_summary_path": fast_run.get("summary_path", ""),
        "full_log_path": full_run.get("log_path", "") if full_run else "",
        "full_summary_path": full_run.get("summary_path", "") if full_run else "",
        "trial_commit": git_current_commit(),
        "trial_dirty": git_is_dirty(),
        "git_action": "",
    }

    if status in {"bootstrap", "keep"} and git_commit_on_keep and git_is_dirty():
        auto_message = commit_message.strip() or f"autoresearch: {record['template']} - {record['idea'] or record['direction']}"
        new_commit = git_commit_files(record["editable_files"], auto_message)
        result["git_action"] = f"commit {new_commit}"
        result["trial_commit"] = new_commit
        result["trial_dirty"] = git_is_dirty()
        state["best_commit"] = new_commit
        _replace_recent_experiment(state, experiment_id, {
            "git_action": result["git_action"],
            "trial_commit": result["trial_commit"],
            "trial_dirty": result["trial_dirty"],
        })
        save_loop_state(state)
    append_experiment_log(result)
    return result


def list_templates() -> list[dict]:
    return [dict(template) for template in TEMPLATES]


def resolve_template(direction: str) -> dict | None:
    text = (direction or "").strip()
    if not text:
        return None

    for template in TEMPLATES:
        if any(keyword in text for keyword in template["direction_keywords"]):
            return dict(template)
    return None


def get_active_direction() -> str:
    state = arm.load_state()
    active = state.get("current_priority_queue", {}).get("active", [])
    return active[0] if active else ""


def build_command(args: list[str]) -> str:
    return "python tools/run_benchmark.py " + " ".join(args)


def build_experiment_plan(direction: str = "",
                          idea: str = "",
                          template_name: str = "") -> dict:
    resolved_direction = direction or get_active_direction()

    template = None
    if template_name:
        template = next((dict(item) for item in TEMPLATES if item["name"] == template_name), None)
    if template is None:
        template = resolve_template(resolved_direction)
    if template is None:
        raise ValueError(f"未找到可匹配模板: {resolved_direction or template_name}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    experiment_id = f"{timestamp}-{template['name']}"
    return {
        "experiment_id": experiment_id,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "direction": resolved_direction,
        "idea": idea,
        "template": template["name"],
        "editable_files": template["editable_files"],
        "fast_command": build_command(template["fast_benchmark_args"]),
        "full_command": build_command(template["full_benchmark_args"]),
        "notes": template["notes"],
        "status": "planned",
    }


def start_experiment(direction: str = "",
                     idea: str = "",
                     template_name: str = "",
                     require_clean_git: bool = False) -> dict:
    record = build_experiment_plan(direction=direction, idea=idea, template_name=template_name)
    if require_clean_git and git_is_dirty():
        raise RuntimeError("git 工作区不干净，拒绝开始实验")

    record["base_commit"] = git_current_commit()
    record["base_branch"] = git_current_branch()
    record["base_dirty"] = git_is_dirty()

    state = load_loop_state()
    state["current_direction"] = record["direction"]
    state["current_template"] = record["template"]
    recent = state.get("recent_experiments", [])
    recent.append({
        "experiment_id": record["experiment_id"],
        "direction": record["direction"],
        "template": record["template"],
        "time": record["time"],
        "status": record["status"],
        "idea": record["idea"],
        "base_commit": record["base_commit"],
        "base_branch": record["base_branch"],
        "base_dirty": record["base_dirty"],
    })
    state["recent_experiments"] = recent[-20:]
    save_loop_state(state)
    append_experiment_log(record)
    return record


def _load_or_start_experiment(experiment_id: str = "",
                              direction: str = "",
                              idea: str = "",
                              template_name: str = "",
                              require_clean_git: bool = False) -> dict:
    if experiment_id:
        state = load_loop_state()
        found = _find_recent_experiment(state, experiment_id)
        if not found:
            raise ValueError(f"未找到实验: {experiment_id}")
        template = next(item for item in TEMPLATES if item["name"] == found["template"])
        return {
            "experiment_id": found["experiment_id"],
            "time": found["time"],
            "direction": found["direction"],
            "idea": found.get("idea", idea),
            "template": found["template"],
            "editable_files": template["editable_files"],
            "fast_command": build_command(template["fast_benchmark_args"]),
            "full_command": build_command(template["full_benchmark_args"]),
            "notes": template["notes"],
            "status": found.get("status", "planned"),
            "base_commit": found.get("base_commit", ""),
            "base_branch": found.get("base_branch", ""),
            "base_dirty": found.get("base_dirty", False),
        }

    return start_experiment(
        direction=direction,
        idea=idea,
        template_name=template_name,
        require_clean_git=require_clean_git,
    )


def _cmd_show(_args) -> None:
    payload = {
        "manager_state": arm.load_state(),
        "loop_state": load_loop_state(),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_templates(_args) -> None:
    print(json.dumps(list_templates(), ensure_ascii=False, indent=2))


def _cmd_plan(args) -> None:
    record = build_experiment_plan(direction=args.direction, idea=args.idea, template_name=args.template)
    print(json.dumps(record, ensure_ascii=False, indent=2))


def _cmd_start(args) -> None:
    record = start_experiment(
        direction=args.direction,
        idea=args.idea,
        template_name=args.template,
        require_clean_git=args.require_clean_git,
    )
    print(json.dumps(record, ensure_ascii=False, indent=2))


def _cmd_execute(args) -> None:
    result = execute_experiment(
        experiment_id=args.experiment_id,
        direction=args.direction,
        idea=args.idea,
        template_name=args.template,
        fast_timeout_sec=args.fast_timeout,
        full_timeout_sec=args.full_timeout,
        run_full=not args.fast_only,
        record_round=args.record_round,
        require_clean_git=args.require_clean_git,
        git_reset_on_discard=args.git_reset_on_discard,
        git_commit_on_keep=args.git_commit_on_keep,
        commit_message=args.commit_message,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Quota research loop V1")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("show", help="查看 manager + loop 状态")
    sub.add_parser("templates", help="列出方向模板")

    p_plan = sub.add_parser("plan", help="生成一轮实验计划，不落盘")
    p_plan.add_argument("--direction", default="", help="显式指定方向；默认取 autoresearch active")
    p_plan.add_argument("--idea", default="", help="本轮想法摘要")
    p_plan.add_argument("--template", default="", help="显式指定模板名")

    p_start = sub.add_parser("start", help="开始一轮实验计划并写入 loop 状态")
    p_start.add_argument("--direction", default="", help="显式指定方向；默认取 autoresearch active")
    p_start.add_argument("--idea", default="", help="本轮想法摘要")
    p_start.add_argument("--template", default="", help="显式指定模板名")
    p_start.add_argument("--require-clean-git", action="store_true", help="要求开始实验前 git 工作区干净")

    p_execute = sub.add_parser("execute", help="执行一轮 fast/full benchmark 并判定")
    p_execute.add_argument("--experiment-id", default="", help="基于已有 start 记录继续执行")
    p_execute.add_argument("--direction", default="", help="显式指定方向；默认取 autoresearch active")
    p_execute.add_argument("--idea", default="", help="本轮想法摘要")
    p_execute.add_argument("--template", default="", help="显式指定模板名")
    p_execute.add_argument("--fast-only", action="store_true", help="只跑快筛，不跑全量")
    p_execute.add_argument("--record-round", action="store_true", help="把 keep/discard 写回 autoresearch_manager")
    p_execute.add_argument("--fast-timeout", type=int, default=180, help="快筛超时秒数")
    p_execute.add_argument("--full-timeout", type=int, default=900, help="全量超时秒数")
    p_execute.add_argument("--require-clean-git", action="store_true", help="要求开始/执行前 git 工作区干净")
    p_execute.add_argument("--git-reset-on-discard", action="store_true", help="discard/crash 时回滚到 start 记录的 base_commit")
    p_execute.add_argument("--git-commit-on-keep", action="store_true", help="keep/bootstrap 时仅提交模板白名单文件")
    p_execute.add_argument("--commit-message", default="", help="自动提交时的 commit message")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "show":
        _cmd_show(args)
    elif args.command == "templates":
        _cmd_templates(args)
    elif args.command == "plan":
        _cmd_plan(args)
    elif args.command == "start":
        _cmd_start(args)
    elif args.command == "execute":
        _cmd_execute(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
