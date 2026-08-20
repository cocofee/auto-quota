import json

from eval.accuracy_baseline.contracts import DatasetKind
from eval.accuracy_baseline.datasets import load_dataset


def test_load_dataset_normalizes_fields_and_reports_rejections(tmp_path):
    path = tmp_path / "cases.jsonl"
    rows = [
        {
            "sample_id": "1",
            "province": "demo",
            "bill_name": "Valve",
            "bill_text": "DN50",
            "unit": "set",
            "specialty": "C10",
            "oracle_quota_ids": ["Q-1", "Q-1", "Q-2"],
            "source": "user_correction",
            "source_family": "human",
            "project_name": "project-a",
        },
        {"sample_id": "2", "province": "demo", "bill_name": "No oracle"},
        {"sample_id": "3", "oracle_quota_ids": ["Q-3"]},
    ]
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    loaded = load_dataset(path, DatasetKind.PRIMARY)

    assert len(loaded.cases) == 1
    assert loaded.cases[0].oracle_quota_ids == ("Q-1", "Q-2")
    assert loaded.rejection_counts == {"missing_oracle": 1, "missing_province": 1}
    assert loaded.total_rows == 3
    assert len(loaded.content_sha256) == 64


def test_oss_dataset_requires_source_family_and_project_provenance(tmp_path):
    path = tmp_path / "oss.jsonl"
    path.write_text(
        json.dumps(
            {
                "sample_id": "oss-1",
                "province": "demo",
                "bill_name": "Pipe",
                "oracle_quota_ids": ["Q-1"],
                "source_family": "",
                "project_name": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_dataset(path, DatasetKind.OSS_DIAGNOSTIC)

    assert loaded.cases == ()
    assert loaded.rejection_counts == {"missing_provenance": 1}


def test_load_dataset_normalizes_quota_variant_markers(tmp_path):
    path = tmp_path / "primary.jsonl"
    path.write_text(
        json.dumps(
            {
                "sample_id": "variant-1",
                "province": "demo",
                "bill_name": "Pipe",
                "oracle_quota_ids": ["借A10-1-223 换", "A10-1-223*2"],
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_dataset(path, DatasetKind.PRIMARY)

    assert loaded.cases[0].oracle_quota_ids == ("A10-1-223",)
