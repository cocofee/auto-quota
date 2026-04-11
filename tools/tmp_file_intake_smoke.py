from pathlib import Path
import json
import sys

PROJECT_ROOT = Path(r"C:\Users\Administrator\Documents\trae_projects\auto-quota")
BACKEND_ROOT = PROJECT_ROOT / "web" / "backend"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.append(str(BACKEND_ROOT))

from src.file_intake_db import FileIntakeDB
from app.api import file_intake as file_intake_api

root = Path(r"F:\jarvis")
candidates = list(root.rglob("*.xlsx"))
target = None
for path in candidates:
    text = str(path)
    if "吴忠" in text and "变配电室" in text and "低压电缆敷设工程" in text:
        target = path
        break
if target is None:
    for path in candidates:
        text = str(path)
        if "造价" in text and ("工程量" in text or "清单" in text):
            target = path
            break
if target is None and candidates:
    target = candidates[0]
if target is None or not target.exists():
    raise SystemExit("no usable xlsx sample found under F:\\jarvis")

db = FileIntakeDB()
record = db.create_file(
    filename=target.name,
    stored_path=str(target),
    mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    file_ext=target.suffix.lower(),
    file_size=target.stat().st_size,
    source_hint="jarvis_real_sample",
    project_name="真实样本验收",
    actor="openclaw-smoke",
    created_by="openclaw-smoke",
)
file_id = record["file_id"]

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
    db.update_route(file_id, route_result={"targets": route_targets, "smoke": True})

final_record = db.get_file(file_id)
print(json.dumps({
    "sample_path": str(target),
    "file_id": final_record["file_id"],
    "filename": final_record["filename"],
    "status": final_record.get("status"),
    "file_type": final_record.get("file_type"),
    "current_stage": final_record.get("current_stage"),
    "next_action": final_record.get("next_action"),
    "failure_type": final_record.get("failure_type"),
    "failure_stage": final_record.get("failure_stage"),
    "needs_manual_review": final_record.get("needs_manual_review"),
    "manual_review_reason": final_record.get("manual_review_reason"),
    "classify_result": final_record.get("classify_result"),
    "parse_summary": final_record.get("parse_summary"),
    "route_result": final_record.get("route_result"),
    "receipt_summary": final_record.get("receipt_summary"),
}, ensure_ascii=False, indent=2))
