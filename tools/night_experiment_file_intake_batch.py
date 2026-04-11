from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(r"C:\Users\Administrator\Documents\trae_projects\auto-quota")
BACKEND_ROOT = PROJECT_ROOT / "web" / "backend"
OUTPUT_ROOT = PROJECT_ROOT / "output" / "night_experiments"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from src.file_intake_db import FileIntakeDB
from app.api import file_intake as file_intake_api


EXPERIMENT_ID = "night-exp-002"
EXPERIMENT_GOAL = "验证 file-intake 入口识别在固定多样本集上的稳定性与误拦截情况"
EXPERIMENT_CHANGE = "固定抽取 5 个真实 Excel 样本，逐个跑 classify -> parse -> route，并输出批量汇总判定"
MAX_SAMPLES = 5
KEYWORDS = ["工程量", "清单", "预算", "报价", "审核", "安装工程", "土建工程", "电缆", "配电", "图纸"]


def _collect_samples(root: Path) -> list[Path]:
    picked: list[Path] = []
    for path in root.rglob("*.xlsx"):
        text = str(path)
        if not any(word in text for word in KEYWORDS):
            continue
        picked.append(path)
        if len(picked) >= MAX_SAMPLES:
            break
    if not picked:
        raise FileNotFoundError("no usable xlsx samples found under F:\\jarvis")
    return picked


def _run_one(sample_path: Path) -> dict:
    db = FileIntakeDB()
    record = db.create_file(
        filename=sample_path.name,
        stored_path=str(sample_path),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_ext=sample_path.suffix.lower(),
        file_size=sample_path.stat().st_size,
        source_hint="night_experiment_batch_sample",
        project_name="夜间自动实验",
        actor="openclaw-night-exp",
        created_by="openclaw-night-exp",
    )
    file_id = record["file_id"]
    started_at = time.time()

    file_type, classify_result = file_intake_api._classify_record(record)
    if file_type == "other" or float(classify_result.get("confidence") or 0.0) < 0.55:
        db.update_failure(
            file_id,
            error_message=f"classification confidence too low: {classify_result.get('confidence')}",
            failure_type="manual_review",
            failure_stage="classify-file",
            needs_manual_review=True,
            manual_review_reason="low_confidence_classification",
        )
    else:
        db.update_classify(file_id, file_type=file_type, classify_result=classify_result)
        parse_summary = file_intake_api._parse_record(db.get_file(file_id))
        db.update_parse(file_id, status="parsed", parse_summary=parse_summary)
        route_targets = file_intake_api._default_route_targets(file_type)
        db.update_route(file_id, route_result={"targets": route_targets, "night_experiment": True})

    final_record = db.get_file(file_id)
    elapsed_ms = round((time.time() - started_at) * 1000)
    return {
        "sample_path": str(sample_path),
        "filename": sample_path.name,
        "elapsed_ms": elapsed_ms,
        "file_id": final_record["file_id"],
        "status": final_record.get("status"),
        "file_type": final_record.get("file_type"),
        "current_stage": final_record.get("current_stage"),
        "next_action": final_record.get("next_action"),
        "failure_type": final_record.get("failure_type"),
        "failure_stage": final_record.get("failure_stage"),
        "needs_manual_review": final_record.get("needs_manual_review"),
        "manual_review_reason": final_record.get("manual_review_reason"),
        "classify_result": final_record.get("classify_result") or {},
        "parse_summary": final_record.get("parse_summary") or {},
        "route_result": final_record.get("route_result") or {},
        "receipt_summary": final_record.get("receipt_summary") or {},
    }


def _summarize(results: list[dict]) -> dict:
    total = len(results)
    routed = sum(1 for item in results if item.get("status") == "routed")
    waiting_human = sum(1 for item in results if item.get("status") == "waiting_human")
    avg_confidence = round(
        sum(float((item.get("classify_result") or {}).get("confidence") or 0.0) for item in results) / max(total, 1),
        3,
    )
    total_bill_items = sum(int((item.get("parse_summary") or {}).get("bill_items") or 0) for item in results)
    avg_elapsed_ms = round(sum(int(item.get("elapsed_ms") or 0) for item in results) / max(total, 1))

    if routed >= max(3, total - 1) and waiting_human <= 1:
        decision = "保留"
        reason = "多样本集大部分已稳定通过入口识别与后续路由，可进入下一阶段夜间批跑"
    elif waiting_human >= max(2, total // 2):
        decision = "失败"
        reason = "真实多样本仍有较高比例被拦在 waiting_human，入口识别策略还需继续收紧与补词"
    else:
        decision = "回退"
        reason = "结果不够稳定，暂不适合夜间批量扩跑，应先继续优化样本命中率"

    return {
        "metrics": {
            "sample_count": total,
            "routed_count": routed,
            "waiting_human_count": waiting_human,
            "avg_confidence": avg_confidence,
            "avg_elapsed_ms": avg_elapsed_ms,
            "total_bill_items": total_bill_items,
        },
        "decision": decision,
        "reason": reason,
        "next_step": "优先补入口特征词和边界文件分流规则，再继续夜间批跑" if decision != "保留" else "可以继续补夜间定时报表和更多固定样本",
    }


def _render_markdown(payload: dict) -> str:
    summary = payload["summary"]
    lines = [
        f"# {payload['experiment_id']} 实验报告",
        "",
        f"- 实验目标：{payload['experiment_goal']}",
        f"- 改动点：{payload['change']}",
        f"- 样本集：{payload['sample_set']}",
        f"- 判定：{summary['decision']}",
        f"- 原因：{summary['reason']}",
        "",
        "## 核心指标",
        "",
        f"- 样本数：{summary['metrics']['sample_count']}",
        f"- routed 数：{summary['metrics']['routed_count']}",
        f"- waiting_human 数：{summary['metrics']['waiting_human_count']}",
        f"- 平均置信度：{summary['metrics']['avg_confidence']}",
        f"- 平均耗时(ms)：{summary['metrics']['avg_elapsed_ms']}",
        f"- bill_items 总数：{summary['metrics']['total_bill_items']}",
        "",
        "## 样本结果",
        "",
    ]
    for item in payload["results"]:
        lines.extend([
            f"- `{item['filename']}` | status={item['status']} | file_type={item['file_type']} | confidence={(item.get('classify_result') or {}).get('confidence', 0)} | next_action={item['next_action']}",
        ])
    lines.extend([
        "",
        "## 下一步",
        "",
        f"- {summary['next_step']}",
    ])
    return "\n".join(lines)


def main() -> None:
    sample_root = Path(r"F:\jarvis")
    sample_paths = _collect_samples(sample_root)
    results = [_run_one(path) for path in sample_paths]
    summary = _summarize(results)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    json_path = OUTPUT_ROOT / f"{EXPERIMENT_ID}_{timestamp}.json"
    md_path = OUTPUT_ROOT / f"{EXPERIMENT_ID}_{timestamp}.md"

    payload = {
        "experiment_id": EXPERIMENT_ID,
        "experiment_goal": EXPERIMENT_GOAL,
        "change": EXPERIMENT_CHANGE,
        "sample_set": f"F:\\jarvis 固定 {len(sample_paths)} 个真实样本",
        "results": results,
        "summary": summary,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    print(json.dumps({"json_report": str(json_path), "markdown_report": str(md_path), **payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
