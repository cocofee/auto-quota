from eval.accuracy_baseline.contracts import DatasetKind, EvalCase
from eval.accuracy_baseline.coverage import summarize_dataset_coverage


def _case(
    case_id: str,
    *,
    province: str,
    source_family: str,
    project_id: str,
    split: str,
    bill_name: str,
    specialty: str = "C10",
) -> EvalCase:
    return EvalCase(
        case_id=case_id,
        dataset_kind=DatasetKind.PRIMARY,
        province=province,
        bill_name=bill_name,
        bill_text="",
        unit="m",
        specialty=specialty,
        oracle_quota_ids=("Q-1",),
        source_family=source_family,
        project_id=project_id,
        split=split,
    )


def _complete_contract(**overrides):
    contract = {
        "contract_version": "coverage.v1",
        "approval_reference": "approved-baseline-contract",
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
    contract.update(overrides)
    return contract


def test_primary_dataset_without_coverage_contract_is_only_a_slice():
    report = summarize_dataset_coverage(
        [
            _case(
                "1",
                province="安徽",
                source_family="human",
                project_id="project-a",
                split="heldout",
                bill_name="给水管",
            )
        ],
        DatasetKind.PRIMARY,
    )

    assert report["scope"] == "slice"
    assert report["system_baseline_eligible"] is False
    assert report["reasons"] == ["system_coverage_contract_missing"]
    assert report["observed"]["province_count"] == 1
    assert report["observed"]["missing_specialty_count"] == 0


def test_coverage_contract_rejects_cross_split_source_and_project_leakage():
    cases = [
        _case(
            "1",
            province="安徽",
            source_family="oss/a",
            project_id="project-a",
            split="dev",
            bill_name="给水管",
        ),
        _case(
            "2",
            province="浙江",
            source_family="oss/a",
            project_id="project-a",
            split="heldout",
            bill_name="给水管",
        ),
    ]

    report = summarize_dataset_coverage(
        cases,
        DatasetKind.PRIMARY,
        _complete_contract(),
    )

    assert report["system_baseline_eligible"] is False
    assert report["observed"]["cross_split_query_overlap_count"] == 1
    assert report["observed"]["cross_split_province_query_overlap_count"] == 0
    assert report["observed"]["cross_split_source_family_overlap_count"] == 1
    assert report["observed"]["cross_split_project_overlap_count"] == 1


def test_coverage_reports_province_scoped_query_overlap_separately():
    cases = [
        _case(
            "1",
            province="AH",
            source_family="human/a",
            project_id="project-a",
            split="dev",
            bill_name="Valve",
        ),
        _case(
            "2",
            province="ah",
            source_family="human/b",
            project_id="project-b",
            split="heldout",
            bill_name=" valve ",
        ),
    ]

    report = summarize_dataset_coverage(cases, DatasetKind.PRIMARY)

    assert report["observed"]["cross_split_query_overlap_count"] == 1
    assert report["observed"]["cross_split_province_query_overlap_count"] == 1


def test_coverage_contract_can_mark_an_isolated_multisource_dataset_eligible():
    cases = [
        _case(
            "1",
            province="安徽",
            source_family="human/a",
            project_id="project-a",
            split="heldout",
            bill_name="给水管",
        ),
        _case(
            "2",
            province="浙江",
            source_family="oss/b",
            project_id="project-b",
            split="hard",
            bill_name="电力电缆",
            specialty="C4",
        ),
    ]

    report = summarize_dataset_coverage(
        cases,
        DatasetKind.PRIMARY,
        _complete_contract(),
    )

    assert report["scope"] == "system_baseline"
    assert report["system_baseline_eligible"] is True
    assert report["gate_status"] == "passed"


def test_coverage_contract_requires_explicit_business_approval():
    contract = _complete_contract()
    contract["approval_reference"] = ""

    report = summarize_dataset_coverage([], DatasetKind.PRIMARY, contract)

    assert report["gate_status"] == "invalid_contract"
    assert "coverage_contract_invalid_requirement:approval_reference" in report["reasons"]


def test_contract_can_allow_province_and_source_family_overlap_across_splits():
    cases = [
        _case(
            "1",
            province="安徽",
            source_family="human/a",
            project_id="project-a",
            split="dev",
            bill_name="给水管",
        ),
        _case(
            "2",
            province="安徽",
            source_family="human/a",
            project_id="project-b",
            split="heldout",
            bill_name="电力电缆",
            specialty="C4",
        ),
    ]

    report = summarize_dataset_coverage(
        cases,
        DatasetKind.PRIMARY,
        _complete_contract(
            min_provinces=2,
            min_source_families=2,
            max_cross_split_province_overlap=1,
            max_cross_split_source_family_overlap=1,
        ),
    )

    assert report["coverage_contract_complete"] is True
    assert "coverage_contract_invalid_requirement:max_cross_split_province_overlap" not in report["reasons"]


def test_partial_contract_cannot_mark_a_slice_as_system_baseline():
    report = summarize_dataset_coverage(
        [
            _case(
                "1",
                province="安徽",
                source_family="human",
                project_id="project-a",
                split="heldout",
                bill_name="给水管",
            )
        ],
        DatasetKind.PRIMARY,
        {"min_cases": 1},
    )

    assert report["scope"] == "slice"
    assert report["system_baseline_eligible"] is False
    assert report["coverage_contract_complete"] is False
    assert report["gate_status"] == "invalid_contract"
    assert "coverage_contract_missing_requirement:min_provinces" in report["reasons"]


def test_contract_cannot_define_single_province_as_system_coverage():
    report = summarize_dataset_coverage(
        [
            _case(
                "1",
                province="安徽",
                source_family="human/a",
                project_id="project-a",
                split="heldout",
                bill_name="给水管",
            ),
            _case(
                "2",
                province="安徽",
                source_family="human/b",
                project_id="project-b",
                split="hard",
                bill_name="电力电缆",
            ),
        ],
        DatasetKind.PRIMARY,
        _complete_contract(
            min_provinces=1,
            max_dominant_province_share=1.0,
        ),
    )

    assert report["system_baseline_eligible"] is False
    assert report["gate_status"] == "invalid_contract"
    assert "coverage_contract_invalid_requirement:min_provinces" in report["reasons"]
    assert (
        "coverage_contract_invalid_requirement:max_dominant_province_share"
        in report["reasons"]
    )


def test_contract_rejects_non_finite_share_and_nonzero_leakage_allowance():
    report = summarize_dataset_coverage(
        [],
        DatasetKind.PRIMARY,
        _complete_contract(
            max_dominant_project_share=float("nan"),
            max_cross_split_project_overlap=1,
        ),
    )

    assert report["system_baseline_eligible"] is False
    assert report["gate_status"] == "invalid_contract"
    assert (
        "coverage_contract_invalid_requirement:max_dominant_project_share"
        in report["reasons"]
    )
    assert (
        "coverage_contract_invalid_requirement:max_cross_split_project_overlap"
        in report["reasons"]
    )
