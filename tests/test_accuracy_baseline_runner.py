import json
import sys

from eval.accuracy_baseline.contracts import (
    CandidateSnapshot,
    LifecycleStage,
    ProviderResult,
    ProviderStatus,
    StageSnapshot,
)
from eval.accuracy_baseline.runner import run_accuracy_baseline


class FakeProvider:
    def __init__(self, name: str, quota_id: str):
        self.name = name
        self.quota_id = quota_id

    def run(self, cases):
        results = []
        for case in cases:
            candidate = CandidateSnapshot(
                quota_id=self.quota_id,
                name=self.quota_id,
                unit=case.unit,
                province=case.province,
                provider=self.name,
                source="fake",
                stage=LifecycleStage.RETRIEVED,
                rank=1,
            )
            results.append(
                ProviderResult(
                    case_id=case.case_id,
                    provider_name=self.name,
                    status=ProviderStatus.OK,
                    final_quota_ids=(self.quota_id,),
                    lifecycle=(
                        StageSnapshot(
                            stage=LifecycleStage.RETRIEVED,
                            emitted=True,
                            candidates=(candidate,),
                            top1_id=self.quota_id,
                        ),
                    ),
                )
            )
        return results


class UnionFakeProvider(FakeProvider):
    def run(self, cases):
        results = super().run(cases)
        return [
            ProviderResult(
                case_id=result.case_id,
                provider_name=self.name,
                status=result.status,
                final_quota_ids=result.final_quota_ids,
                lifecycle=result.lifecycle,
                runtime_metadata={
                    "production_retrieved_ids": ["Q-1"],
                    "goal_retrieved_ids": ["Q-2"],
                    "raw_union_ids": ["Q-1", "Q-2"],
                    "goal_unique_ids": ["Q-2"],
                    "materialized_goal_ids": ["Q-2"],
                    "missing_local_goal_ids": [],
                },
            )
            for result in results
        ]


def _write_case(path, *, baseline_correct=None):
    row = {
        "sample_id": "1",
        "province": "demo",
        "bill_name": "Valve",
        "bill_text": "DN50",
        "unit": "set",
        "specialty": "C10",
        "oracle_quota_ids": ["Q-2"],
        "source": "user_correction",
        "source_family": "human",
        "project_name": "project-a",
    }
    if baseline_correct is not None:
        row["baseline_correct"] = baseline_correct
    path.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")


def test_run_accuracy_baseline_writes_isolated_dataset_metrics(tmp_path):
    dataset = tmp_path / "primary.jsonl"
    _write_case(dataset)

    result = run_accuracy_baseline(
        datasets={"primary": dataset},
        output_dir=tmp_path / "reports",
        providers=[FakeProvider("production", "Q-1"), FakeProvider("goal_shadow", "Q-2")],
        min_slice_size=20,
    )

    production = result["summary"]["datasets"]["primary"]["providers"]["production"]
    goal = result["summary"]["datasets"]["primary"]["providers"]["goal_shadow"]
    comparison = result["summary"]["datasets"]["primary"]["provider_comparison"]
    assert production["recall_at"]["25"] == 0.0
    assert goal["recall_at"]["25"] == 1.0
    assert comparison["union_recall"] == 1.0
    assert (tmp_path / "reports" / "summary.json").exists()


def test_runner_keeps_oss_and_historical_metrics_out_of_primary_accuracy(tmp_path):
    oss = tmp_path / "oss.jsonl"
    stress = tmp_path / "stress.jsonl"
    _write_case(oss)
    _write_case(stress, baseline_correct=True)

    result = run_accuracy_baseline(
        datasets={"oss_diagnostic": oss, "historical_stress": stress},
        output_dir=tmp_path / "reports",
        providers=[FakeProvider("production", "Q-1"), FakeProvider("goal_shadow", "Q-2")],
        min_slice_size=20,
    )

    oss_metrics = result["summary"]["datasets"]["oss_diagnostic"]["providers"]["production"]
    stress_metrics = result["summary"]["datasets"]["historical_stress"]["providers"]["production"]
    assert "final_top1" not in oss_metrics
    assert stress_metrics["repair_rate"] == 0.0
    assert stress_metrics["new_regression_count"] == 1


def test_runner_records_reconstructed_province_assets(tmp_path, monkeypatch):
    import config

    dataset = tmp_path / "primary.jsonl"
    provinces_root = tmp_path / "reconstructed_assets" / "provinces"
    province_dir = provinces_root / "demo"
    province_dir.mkdir(parents=True)
    (province_dir / "asset_manifest.json").write_text(
        json.dumps(
            {
                "asset_mode": "reconstructed_from_national_index",
                "gate_passed": True,
                "province": "demo",
            }
        ),
        encoding="utf-8",
    )
    _write_case(dataset)
    monkeypatch.setattr(config, "PROVINCES_DB_DIR", provinces_root)

    result = run_accuracy_baseline(
        datasets={"primary": dataset},
        output_dir=tmp_path / "reports",
        providers=[FakeProvider("goal_shadow", "Q-2")],
    )

    artifacts = result["summary"]["runtime"]["configured_artifacts"]
    assert artifacts["provinces_db_dir"] == str(provinces_root.resolve())
    assert artifacts["province_assets"] == [
        {
            "asset_mode": "reconstructed_from_national_index",
            "gate_passed": True,
            "manifest_path": str((province_dir / "asset_manifest.json").resolve()),
            "province": "demo",
        }
    ]


def test_runner_adds_union_shadow_diagnostics_to_provider_summary(tmp_path):
    dataset = tmp_path / "primary.jsonl"
    _write_case(dataset)

    result = run_accuracy_baseline(
        datasets={"primary": dataset},
        output_dir=tmp_path / "reports",
        providers=[UnionFakeProvider("production_goal_union_shadow", "Q-2")],
    )

    metrics = result["summary"]["datasets"]["primary"]["providers"][
        "production_goal_union_shadow"
    ]
    assert metrics["union_shadow_diagnostics"]["raw_union_recalled_count"] == 1
    assert metrics["union_shadow_diagnostics"]["rankable_recalled_count"] == 1


def test_accuracy_baseline_cli_injects_provinces_dir_for_process(
    tmp_path,
    monkeypatch,
):
    import config
    import tools.run_accuracy_baseline as cli

    dataset = tmp_path / "primary.jsonl"
    provinces_root = tmp_path / "reconstructed_assets" / "provinces"
    provinces_root.mkdir(parents=True)
    _write_case(dataset)
    captured = {}

    def fake_run_accuracy_baseline(**kwargs):
        captured["provinces_db_dir"] = config.PROVINCES_DB_DIR
        return {"summary": {}}

    monkeypatch.setattr(cli, "run_accuracy_baseline", fake_run_accuracy_baseline)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_accuracy_baseline.py",
            "--primary",
            str(dataset),
            "--providers",
            "goal_shadow",
            "--provinces-db-dir",
            str(provinces_root),
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )

    assert cli.main() == 0
    assert captured["provinces_db_dir"] == provinces_root.resolve()


def test_accuracy_baseline_cli_constructs_union_shadow_provider(tmp_path, monkeypatch):
    import tools.run_accuracy_baseline as cli

    dataset = tmp_path / "primary.jsonl"
    _write_case(dataset)
    captured = {}

    class FakeUnionProvider:
        name = "production_goal_union_shadow"

        def __init__(self, *, goal_top_k, candidate_budget_policy):
            captured["goal_top_k"] = goal_top_k
            captured["candidate_budget_policy"] = candidate_budget_policy

    def fake_run_accuracy_baseline(**kwargs):
        captured["providers"] = kwargs["providers"]
        return {"summary": {}}

    monkeypatch.setattr(cli, "GoalUnionShadowProvider", FakeUnionProvider, raising=False)
    monkeypatch.setattr(cli, "run_accuracy_baseline", fake_run_accuracy_baseline)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_accuracy_baseline.py",
            "--primary",
            str(dataset),
            "--providers",
            "production_goal_union_shadow",
            "--goal-top-k",
            "64",
            "--union-budget-policy",
            "production_40_goal_10",
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )

    assert cli.main() == 0
    assert captured["goal_top_k"] == 64
    assert captured["candidate_budget_policy"] == "production_40_goal_10"
    assert [provider.name for provider in captured["providers"]] == [
        "production_goal_union_shadow"
    ]


def test_accuracy_baseline_cli_returns_nonzero_when_requested_coverage_gate_fails(
    tmp_path,
    monkeypatch,
):
    import tools.run_accuracy_baseline as cli

    dataset = tmp_path / "primary.jsonl"
    contract = tmp_path / "coverage.json"
    _write_case(dataset)
    contract.write_text("{}", encoding="utf-8")

    def fake_run_accuracy_baseline(**kwargs):
        return {
            "summary": {
                "datasets": {
                    "primary": {"system_baseline_eligible": False},
                }
            }
        }

    monkeypatch.setattr(cli, "run_accuracy_baseline", fake_run_accuracy_baseline)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_accuracy_baseline.py",
            "--primary",
            str(dataset),
            "--coverage-contract",
            str(contract),
            "--providers",
            "goal_shadow",
            "--output-dir",
            str(tmp_path / "reports"),
        ],
    )

    assert cli.main() == 2


def test_runner_blocks_system_eligibility_when_dataset_rows_are_rejected(tmp_path):
    dataset = tmp_path / "primary.jsonl"
    rows = [
        {
            "sample_id": "1",
            "province": "demo-a",
            "bill_name": "Composite",
            "oracle_quota_ids": ["Q-1", "Q-2"],
            "source_family": "human/a",
            "project_name": "project-a",
            "specialty": "C10",
            "split": "heldout",
        },
        {
            "sample_id": "2",
            "province": "demo-b",
            "bill_name": "Single",
            "oracle_quota_ids": ["Q-2"],
            "source_family": "human/b",
            "project_name": "project-b",
            "specialty": "C4",
            "split": "hard",
        },
    ]
    dataset.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    contract = {
        "contract_version": "coverage.v1",
        "approval_reference": "approved",
        "target_surface": "search_core",
        "approved_for_system_baseline": True,
        "min_cases": 2,
        "min_provinces": 2,
        "min_source_families": 2,
        "min_projects": 2,
        "min_specialties": 2,
        "min_splits": 2,
        "max_dominant_province_share": 0.5,
        "max_dominant_source_family_share": 0.5,
        "max_dominant_project_share": 0.5,
        "max_dominant_specialty_share": 0.5,
        "max_cross_split_query_overlap": 0,
        "max_cross_split_source_family_overlap": 0,
        "max_cross_split_project_overlap": 0,
        "max_cross_split_province_overlap": 0,
        "require_nonempty_source_family": True,
        "require_nonempty_project": True,
        "require_nonempty_specialty": True,
        "require_nonempty_split": True,
    }

    result = run_accuracy_baseline(
        datasets={"primary": dataset},
        output_dir=tmp_path / "reports",
        providers=[FakeProvider("search_core", "Q-2")],
        coverage_requirements=contract,
    )

    primary = result["summary"]["datasets"]["primary"]
    assert primary["system_baseline_eligible"] is False
    assert primary["rejection_counts"] == {"ambiguous_oracle_semantics": 1}
    assert "dataset_rejection:ambiguous_oracle_semantics=1" in primary["coverage"]["reasons"]
