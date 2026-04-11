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


EXPERIMENT_ID = "night-exp-001"
EXPERIMENT_GOAL = "验证 file-intake 入口识别在真实样本上的最小闭环是否稳定"
EXPERIMENT_CHANGE = "不改正式学习链路，只跑固定真实样本，输出固定指标与保留/回退判定"
SAMPLE_MATCHERS = [
    ("吴忠", "变配电室", "低压电缆敷设工程"),
    ("造价", "工程量"),
    ("造价", "清单"),
]


def _pick_sample(root: Path) -> Path:
    candidates = list(root.rglob("*.xlsx"))
    for matcher in SAMPLE_MATCHERS:
        for path in candidates:
            text = str(path)
            if all(token in text for token in matcher):
                return path
    if candidates:
        return candidates[0]
    raise FileNotFoundError("no usable xlsx sample found under F:\\jarvis")


def _run_file_intake(sample_path: Path) -> dict:
    db = FileIntakeDB()
    record = db.create_file(
        filename=sample_path.name,
        stored_path=str(sample_path),
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        file_ext=sample_path.suffix.lower(),
        file_size=sample_path.stat().st_size,
        source_hint="night_experiment_real_sample",
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

    elapsed_ms = round((time.time() - started_at) * 1000)
    final_record = db.get_file(file_id)
    return {
        "sample_path": str(sample_path),
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


def _judge(result: dict) -> dict:
    confidence = float((result.get("classify_result") or {}).get("confidence") or 0.0)
    status = result.get("status") or ""
    route_targets = (result.get("route_result") or {}).get("targets") or []
    elapsed_ms = int(result.get("elapsed_ms") or 0)

    metrics = {
        "elapsed_ms": elapsed_ms,
        "classify_confidence": confidence,
        "waiting_human": status == "waiting_human",
        "routed": status == "routed",
        "route_target_count": len(route_targets),
        "bill_items": int((result.get("parse_summary") or {}).get("bill_items") or 0),
    }

    keep = (
        status == "routed"
        and confidence >= 0.55
        and len(route_targets) >= 1
        and elapsed_ms <= 10 * 60 * 1000
    )
    if keep:
        decision = "保留"
        reason = "真实样本完成 classify -> parse -> route，且耗时与置信度都在可接受范围内"
    elif status == "waiting_human":
        decision = "失败"
        reason = "真实样本仍被挡在 waiting_human，入口识别还不够稳"
    else:
        decision = "回退"
        reason = "结果未达到最小闭环目标，应回退本轮思路或继续隔离修正"

    return {
        "metrics": metrics,
        "decision": decision,
        "reason": reason,
        "next_step": "继续做单变量入口识别实验，优先减少真实造价样本误拦截" if decision != "保留" else "可继续扩到固定样本集和批量夜间运行",
    }


def main() -> None:
    sample_root = Path(r"F:\jarvis")
    sample_path = _pick_sample(sample_root)
    result = _run_file_intake(sample_path)
    judgment = _judge(result)

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = OUTPUT_ROOT / f"{EXPERIMENT_ID}_{timestamp}.json"
    payload = {
        "experiment_id": EXPERIMENT_ID,
        "experiment_goal": EXPERIMENT_GOAL,
        "change": EXPERIMENT_CHANGE,
        "sample_set": "F:\\jarvis 单真实样本",
        "result": result,
        "judgment": judgment,
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(out_path), **payload}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
