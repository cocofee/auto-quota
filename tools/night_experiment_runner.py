from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\Administrator\Documents\trae_projects\auto-quota")
TOOLS_DIR = PROJECT_ROOT / "tools"
OUTPUT_ROOT = PROJECT_ROOT / "output" / "night_experiments"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))

EXPERIMENT_ID = "night-exp-runner"


def _load_module(filename: str, module_name: str):
    path = TOOLS_DIR / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _pick_sample(root: Path) -> Path:
    for path in root.rglob("*.xlsx"):
        text = str(path)
        if any(token in text for token in ("工程量", "清单", "报价", "预算", "电缆", "配电")):
            return path
    raise FileNotFoundError("no usable xlsx sample found under F:\\jarvis")


def _run_night_exp_001(sample_path: Path) -> dict:
    mod = _load_module("night_experiment_file_intake.py", "night_exp_001")
    result = mod._run_file_intake(sample_path)
    judgment = mod._judge(result)
    return {
        "experiment_id": mod.EXPERIMENT_ID,
        "goal": mod.EXPERIMENT_GOAL,
        "sample_set": "F:\\jarvis 单真实样本",
        "result": result,
        "summary": judgment,
    }


def _run_night_exp_002() -> dict:
    mod = _load_module("night_experiment_file_intake_batch.py", "night_exp_002")
    sample_paths = mod._collect_samples(Path(r"F:\jarvis"))
    results = [mod._run_one(path) for path in sample_paths]
    summary = mod._summarize(results)
    return {
        "experiment_id": mod.EXPERIMENT_ID,
        "goal": mod.EXPERIMENT_GOAL,
        "sample_set": f"F:\\jarvis 固定 {len(sample_paths)} 个真实样本",
        "results": results,
        "summary": summary,
    }


def _run_night_exp_003(sample_path: Path) -> dict:
    mod = _load_module("jarvis_pipeline.py", "night_exp_003_pipeline")
    started_at = time.time()
    run = mod.pipeline(
        str(sample_path),
        province=None,
        aux_provinces=None,
        use_experience=False,
        store=False,
        quiet=True,
    )
    elapsed_ms = round((time.time() - started_at) * 1000)
    stats = run.get("stats") or {}
    summary_text = run.get("summary") or ""
    output_excel = run.get("output_excel") or ""
    log_file = run.get("log_file") or ""

    decision = "保留"
    reason = "JARVIS 主流程成功跑通并产出统计结果"
    if not output_excel:
        decision = "失败"
        reason = "JARVIS 主流程未产出结果文件"
    elif int(stats.get("total") or 0) <= 0:
        decision = "回退"
        reason = "主流程跑通但未形成有效统计，需先排查样本或入口映射"

    return {
        "experiment_id": "night-exp-003",
        "goal": "验证固定样本进入 JARVIS 主流程后的可运行性与基础统计",
        "sample_set": str(sample_path),
        "result": {
            "elapsed_ms": elapsed_ms,
            "output_excel": output_excel,
            "log_file": log_file,
            "stats": stats,
            "summary_text": summary_text,
        },
        "summary": {
            "decision": decision,
            "reason": reason,
            "next_step": "接自动审核链路，比较绿黄红和待人工结构",
        },
    }


def _judge_runner(stages: list[dict]) -> dict:
    failed = [stage for stage in stages if stage.get("summary", {}).get("decision") == "失败"]
    fallback = [stage for stage in stages if stage.get("summary", {}).get("decision") == "回退"]
    if failed:
        return {
            "decision": "失败",
            "reason": f"至少 1 个阶段失败：{', '.join(stage['experiment_id'] for stage in failed)}",
        }
    if fallback:
        return {
            "decision": "回退",
            "reason": f"至少 1 个阶段需要回退：{', '.join(stage['experiment_id'] for stage in fallback)}",
        }
    return {
        "decision": "保留",
        "reason": "入口实验与 JARVIS 主跑都已形成最小可运行闭环，可继续接二次审核",
    }


def _render_markdown(payload: dict) -> str:
    lines = [
        f"# {payload['experiment_id']} 总报告",
        "",
        f"- 总判定：{payload['summary']['decision']}",
        f"- 原因：{payload['summary']['reason']}",
        "",
        "## 分阶段结果",
        "",
    ]
    for stage in payload["stages"]:
        lines.extend([
            f"- `{stage['experiment_id']}` | 判定={stage['summary']['decision']} | 原因={stage['summary']['reason']}",
        ])
    return "\n".join(lines)


def main() -> None:
    sample_root = Path(r"F:\jarvis")
    sample_path = _pick_sample(sample_root)

    stages = [
        _run_night_exp_001(sample_path),
        _run_night_exp_002(),
        _run_night_exp_003(sample_path),
    ]
    summary = _judge_runner(stages)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = OUTPUT_ROOT / f"{EXPERIMENT_ID}_{timestamp}.json"
    md_path = OUTPUT_ROOT / f"{EXPERIMENT_ID}_{timestamp}.md"
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "stages": stages,
        "summary": summary,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    print(json.dumps({"json_report": str(json_path), "markdown_report": str(md_path), **payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
