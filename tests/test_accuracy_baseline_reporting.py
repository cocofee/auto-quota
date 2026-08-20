import csv
import json

from eval.accuracy_baseline.reporting import write_reports


def test_write_reports_is_deterministic_and_writes_all_artifacts(tmp_path):
    payload = {
        "summary": {"valid_cases": 1, "recall_at": {"25": 1.0}},
        "cases": [{"case_id": "b"}, {"case_id": "a"}],
        "stage_attribution": [
            {
                "provider": "production",
                "stage": "ltr",
                "good_flip": 1,
                "bad_flip": 0,
                "net_gain": 1,
            }
        ],
        "slice_metrics": [
            {
                "provider": "production",
                "slice": "province=demo",
                "count": 1,
                "correct": 1,
                "top1": None,
            }
        ],
        "provider_comparison": [
            {"case_id": "a", "production_recalled": False, "goal_shadow_recalled": True}
        ],
    }

    paths = write_reports(tmp_path, payload)

    assert set(paths) == {
        "summary_json",
        "cases_jsonl",
        "stage_attribution_csv",
        "slice_metrics_csv",
        "provider_comparison_csv",
    }
    case_lines = [
        json.loads(line)
        for line in paths["cases_jsonl"].read_text(encoding="utf-8").splitlines()
    ]
    assert [row["case_id"] for row in case_lines] == ["a", "b"]
    with paths["stage_attribution_csv"].open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["stage"] == "ltr"
