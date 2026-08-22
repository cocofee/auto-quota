import csv
import json
import sqlite3

import pytest

from eval.accuracy_baseline.contracts import DatasetKind
from eval.accuracy_baseline.datasets import load_dataset
from eval.accuracy_baseline.promotion import (
    PromotionValidationError,
    build_promoted_dataset,
    write_promoted_dataset,
)
from eval.accuracy_baseline.review_sampling import write_review_queue

BOOK_NAME = "上海市安装工程预算定额(2026)"


def _queue_row(sample_id, *, project_id, bill_name):
    query_fingerprint = bill_name.casefold()
    return {
        "sample_id": sample_id,
        "review_status": "pending",
        "dataset_role": "independent_gold_candidate",
        "source": "bill_library.db",
        "source_family": "bill_library",
        "province": "上海",
        "specialty": "安装",
        "project_id": project_id,
        "source_file_name": f"{project_id}.xlsx",
        "source_record_id": sample_id,
        "sheet_name": "清单",
        "section": "安装工程",
        "bill_code": f"03{sample_id}",
        "bill_name": bill_name,
        "bill_text": bill_name,
        "description": "",
        "unit": "m",
        "quality_tier": "name_unit",
        "query_fingerprint": query_fingerprint,
        "province_query_fingerprint": f"上海|{query_fingerprint}",
        "sample_rank_in_province": int(sample_id),
        "candidate_quota_books": [{"name": BOOK_NAME, "quota_rows": 10}],
        "oracle_quota_ids": [],
        "oracle_quota_names": [],
        "oracle_semantics": "",
        "reviewer": "",
        "reviewed_at": "",
        "review_notes": "",
    }


def _write_queue(tmp_path, rows):
    return write_review_queue(
        rows=rows,
        manifest={
            "version": "accuracy_review_queue.v3",
            "selected_rows": len(rows),
        },
        output_dir=tmp_path / "queue",
    )


def _write_national_index(path, rows):
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE national_quotas (
            province TEXT NOT NULL,
            quota_id TEXT NOT NULL,
            name TEXT NOT NULL,
            PRIMARY KEY (province, quota_id)
        )
        """
    )
    connection.executemany(
        "INSERT INTO national_quotas VALUES (?, ?, ?)",
        [(BOOK_NAME, quota_id, quota_name) for quota_id, quota_name in rows],
    )
    connection.commit()
    connection.close()


def _write_reviewer_registry(path):
    path.write_text(
        json.dumps(
            {
                "version": "accuracy_reviewer_registry.v1",
                "approval_reference": "cost-team-approval-2026-08",
                "reviewers": [
                    {"reviewer_id": "reviewer-a", "active": True},
                    {"reviewer_id": "reviewer-b", "active": True},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_review_copy(
    source_path,
    output_path,
    *,
    reviewer,
    decisions,
    reviewed_at="2026-08-22T09:00:00+08:00",
    mutate=None,
):
    with source_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        rows = list(reader)
    for row in rows:
        sample_id = row["sample_id"]
        decision = decisions[sample_id]
        row["review_status"] = decision["status"]
        row["reviewer"] = reviewer
        row["reviewed_at"] = reviewed_at
        row["review_notes"] = decision.get("notes", "")
        if decision["status"] == "accepted":
            row["oracle_quota_ids"] = json.dumps(
                decision["quota_ids"],
                ensure_ascii=False,
            )
            row["oracle_quota_names"] = json.dumps(
                decision["quota_names"],
                ensure_ascii=False,
            )
            row["oracle_semantics"] = decision.get("semantics", "any")
        if mutate is not None:
            mutate(row)
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _build_inputs(tmp_path, rows, decisions_a, decisions_b=None):
    queue_outputs = _write_queue(tmp_path, rows)
    quota_rows = []
    for decision in decisions_a.values():
        quota_rows.extend(zip(decision.get("quota_ids", []), decision.get("quota_names", [])))
    national_index = tmp_path / "national.sqlite"
    _write_national_index(national_index, list(dict.fromkeys(quota_rows)))
    registry = tmp_path / "reviewers.json"
    _write_reviewer_registry(registry)
    review_a = tmp_path / "review-a.csv"
    review_b = tmp_path / "review-b.csv"
    _write_review_copy(
        queue_outputs["csv"],
        review_a,
        reviewer="reviewer-a",
        decisions=decisions_a,
    )
    _write_review_copy(
        queue_outputs["csv"],
        review_b,
        reviewer="reviewer-b",
        decisions=decisions_b or decisions_a,
        reviewed_at="2026-08-22T10:00:00+08:00",
    )
    return queue_outputs, national_index, registry, review_a, review_b


def _promote(queue_outputs, national_index, registry, review_a, review_b):
    return build_promoted_dataset(
        review_queue_path=queue_outputs["jsonl"],
        review_queue_manifest_path=queue_outputs["manifest"],
        review_a_path=review_a,
        review_b_path=review_b,
        national_index_path=national_index,
        reviewer_registry_path=registry,
    )


def test_promotion_uses_exported_csv_and_preserves_agreed_rejections(tmp_path):
    rows = [
        _queue_row("1", project_id="project-a", bill_name="给水管"),
        _queue_row("2", project_id="project-b", bill_name="给水管"),
        _queue_row("3", project_id="project-b", bill_name="阀门"),
        _queue_row("4", project_id="project-c", bill_name="电力电缆"),
        _queue_row("5", project_id="project-d", bill_name="待排除项"),
    ]
    decisions = {
        sample_id: {
            "status": "accepted",
            "quota_ids": [f"Q-{sample_id}"],
            "quota_names": [f"定额 {sample_id}"],
        }
        for sample_id in ("1", "2", "3", "4")
    }
    decisions["5"] = {"status": "rejected", "notes": "无可用规范定额"}
    inputs = _build_inputs(tmp_path, rows, decisions)

    promoted, manifest = _promote(*inputs)
    outputs = write_promoted_dataset(
        rows=promoted,
        manifest=manifest,
        output_dir=tmp_path / "promoted",
    )
    loaded = load_dataset(outputs["dataset"], DatasetKind.PRIMARY)
    rejected = [
        json.loads(line)
        for line in outputs["agreed_rejections"].read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    assert len(promoted) == 4
    assert loaded.rejection_counts == {}
    assert manifest["reviewed_rows"] == 5
    assert manifest["agreed_rejected_rows"] == 1
    assert rejected[0]["sample_id"] == "5"
    assert manifest["split_assignment"]["component_count"] == 2
    assert len(manifest["split_assignment"]["counts"]) == 2
    assert manifest["isolation"]["passed"] is True
    assert manifest["scope"] == "slice"
    assert manifest["system_baseline_eligible"] is False
    assert manifest["oracle_authority"]["checked_oracles"] == 4
    assert all(row["source_family"] == "bill_library" for row in promoted)
    assert all(
        row["label_source_family"] == "dual_independent_human_review"
        for row in promoted
    )


def test_promotion_rejects_queue_context_and_hash_tampering(tmp_path):
    rows = [_queue_row("1", project_id="project-a", bill_name="给水管")]
    decisions = {
        "1": {
            "status": "accepted",
            "quota_ids": ["Q-1"],
            "quota_names": ["给水管安装"],
        }
    }
    queue_outputs, national_index, registry, review_a, review_b = _build_inputs(
        tmp_path,
        rows,
        decisions,
    )
    _write_review_copy(
        queue_outputs["csv"],
        review_a,
        reviewer="reviewer-a",
        decisions=decisions,
        mutate=lambda row: row.update(
            bill_text="被篡改文本",
            queue_content_sha256="wrong-hash",
        ),
    )

    with pytest.raises(PromotionValidationError) as exc_info:
        _promote(queue_outputs, national_index, registry, review_a, review_b)

    assert "review_a:1:context_conflict:bill_text" in exc_info.value.errors
    assert "review_a:1:queue_content_sha256_mismatch" in exc_info.value.errors


def test_promotion_rejects_unknown_oracle_and_wrong_canonical_name(tmp_path):
    rows = [
        _queue_row("1", project_id="project-a", bill_name="给水管"),
        _queue_row("2", project_id="project-b", bill_name="阀门"),
    ]
    authoritative = {
        "1": {
            "status": "accepted",
            "quota_ids": ["Q-1"],
            "quota_names": ["给水管安装"],
        },
        "2": {
            "status": "accepted",
            "quota_ids": ["Q-2"],
            "quota_names": ["阀门安装"],
        },
    }
    queue_outputs, national_index, registry, review_a, review_b = _build_inputs(
        tmp_path,
        rows,
        authoritative,
    )
    invalid = {
        "1": {
            "status": "accepted",
            "quota_ids": ["NOT-A-REAL-QUOTA"],
            "quota_names": ["不存在定额"],
        },
        "2": {
            "status": "accepted",
            "quota_ids": ["Q-2"],
            "quota_names": ["错误名称"],
        },
    }
    _write_review_copy(
        queue_outputs["csv"],
        review_a,
        reviewer="reviewer-a",
        decisions=invalid,
    )
    _write_review_copy(
        queue_outputs["csv"],
        review_b,
        reviewer="reviewer-b",
        decisions=invalid,
    )

    with pytest.raises(PromotionValidationError) as exc_info:
        _promote(queue_outputs, national_index, registry, review_a, review_b)

    assert (
        "1:NOT-A-REAL-QUOTA:oracle_not_in_candidate_books"
        in exc_info.value.errors
    )
    assert "2:Q-2:oracle_name_mismatch" in exc_info.value.errors


def test_promotion_rejects_same_or_unapproved_reviewer(tmp_path):
    rows = [_queue_row("1", project_id="project-a", bill_name="给水管")]
    decisions = {
        "1": {
            "status": "accepted",
            "quota_ids": ["Q-1"],
            "quota_names": ["给水管安装"],
        }
    }
    queue_outputs, national_index, registry, review_a, review_b = _build_inputs(
        tmp_path,
        rows,
        decisions,
    )
    _write_review_copy(
        queue_outputs["csv"],
        review_b,
        reviewer="reviewer-a",
        decisions=decisions,
    )

    with pytest.raises(PromotionValidationError) as exc_info:
        _promote(queue_outputs, national_index, registry, review_a, review_b)

    assert "1:reviewers_must_be_distinct" in exc_info.value.errors

    _write_review_copy(
        queue_outputs["csv"],
        review_b,
        reviewer="unapproved-reviewer",
        decisions=decisions,
    )
    with pytest.raises(PromotionValidationError) as exc_info:
        _promote(queue_outputs, national_index, registry, review_a, review_b)
    assert "review_b:1:reviewer_not_approved" in exc_info.value.errors


def test_promotion_rejects_missing_duplicate_conflict_and_naive_time(tmp_path):
    rows = [
        _queue_row("1", project_id="project-a", bill_name="给水管"),
        _queue_row("2", project_id="project-b", bill_name="阀门"),
    ]
    decisions = {
        "1": {
            "status": "accepted",
            "quota_ids": ["Q-1"],
            "quota_names": ["给水管安装"],
        },
        "2": {"status": "rejected"},
    }
    queue_outputs, national_index, registry, review_a, review_b = _build_inputs(
        tmp_path,
        rows,
        decisions,
    )
    _write_review_copy(
        queue_outputs["csv"],
        review_a,
        reviewer="reviewer-a",
        decisions=decisions,
        reviewed_at="2026-08-22T09:00:00",
    )
    with review_a.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        review_a_rows = list(reader)
    review_a_rows.append(dict(review_a_rows[0]))
    with review_a.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(review_a_rows)

    conflicting = dict(decisions)
    conflicting["2"] = {
        "status": "accepted",
        "quota_ids": ["Q-2"],
        "quota_names": ["阀门安装"],
    }
    _write_review_copy(
        queue_outputs["csv"],
        review_b,
        reviewer="reviewer-b",
        decisions=conflicting,
    )

    with pytest.raises(PromotionValidationError) as exc_info:
        _promote(queue_outputs, national_index, registry, review_a, review_b)

    assert "review_a:1:duplicate_sample_id" in exc_info.value.errors
    assert "review_a:1:reviewed_at_timezone_required" in exc_info.value.errors
    assert "2:review_status_conflict" in exc_info.value.errors


def test_promotion_rejects_missing_and_unknown_samples(tmp_path):
    rows = [
        _queue_row("1", project_id="project-a", bill_name="给水管"),
        _queue_row("2", project_id="project-b", bill_name="阀门"),
    ]
    decisions = {
        "1": {
            "status": "accepted",
            "quota_ids": ["Q-1"],
            "quota_names": ["给水管安装"],
        },
        "2": {"status": "rejected"},
    }
    queue_outputs, national_index, registry, review_a, review_b = _build_inputs(
        tmp_path,
        rows,
        decisions,
    )
    with review_a.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        review_rows = list(reader)
    unknown = dict(review_rows[0])
    unknown["sample_id"] = "unknown"
    with review_a.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows([review_rows[0], unknown])

    with pytest.raises(PromotionValidationError) as exc_info:
        _promote(queue_outputs, national_index, registry, review_a, review_b)

    assert "review_a:2:missing_sample" in exc_info.value.errors
    assert "review_a:unknown:unknown_sample" in exc_info.value.errors
