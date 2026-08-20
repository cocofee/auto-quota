from tools.goal_17x_p17b_precision_family_retention_dev_oof_shadow import _candidate_spec, _passes_family_retention_filter


def _row(family="concrete", **overrides):
    row = {
        "query_family": family,
        "oss_recall_exact_name": False,
        "oss_recall_source_family_count": 1,
        "oss_recall_support_count": 2,
        "oss_recall_overlap": 2,
        "oss_recall_quota_name_overlap": 0,
        "oss_recall_quota_specific_overlap": 0,
    }
    row.update(overrides)
    return row


def test_p17h_adds_only_compatible_rebar_rescue():
    spec = _candidate_spec("P17_H")

    assert _passes_family_retention_filter(
        spec,
        _row(
            "rebar",
            oss_recall_source_family_count=2,
            oss_recall_support_count=3,
            oss_recall_overlap=2,
            oss_recall_quota_name_overlap=1,
            oss_recall_quota_specific_overlap=1,
        ),
    )
    assert not _passes_family_retention_filter(
        spec,
        _row("rebar", oss_recall_source_family_count=2, oss_recall_support_count=3, oss_recall_overlap=2),
    )
    assert not _passes_family_retention_filter(
        spec,
        _row(
            "pump",
            oss_recall_source_family_count=2,
            oss_recall_support_count=3,
            oss_recall_overlap=3,
            oss_recall_quota_name_overlap=1,
            oss_recall_quota_specific_overlap=1,
        ),
    )


def test_p17i_adds_only_stricter_compatible_pump_rescue():
    spec = _candidate_spec("P17_I")

    assert _passes_family_retention_filter(
        spec,
        _row(
            "pump",
            oss_recall_source_family_count=2,
            oss_recall_support_count=4,
            oss_recall_overlap=3,
            oss_recall_quota_name_overlap=1,
            oss_recall_quota_specific_overlap=1,
        ),
    )
    assert not _passes_family_retention_filter(
        spec,
        _row(
            "pump",
            oss_recall_source_family_count=2,
            oss_recall_support_count=3,
            oss_recall_overlap=3,
            oss_recall_quota_name_overlap=1,
            oss_recall_quota_specific_overlap=1,
        ),
    )


def test_p17j_combines_rebar_and_pump_branch_guards():
    spec = _candidate_spec("P17_J")

    assert _passes_family_retention_filter(
        spec,
        _row(
            "rebar",
            oss_recall_source_family_count=2,
            oss_recall_support_count=3,
            oss_recall_overlap=2,
            oss_recall_quota_name_overlap=1,
            oss_recall_quota_specific_overlap=1,
        ),
    )
    assert _passes_family_retention_filter(
        spec,
        _row(
            "pump",
            oss_recall_source_family_count=2,
            oss_recall_support_count=4,
            oss_recall_overlap=3,
            oss_recall_quota_name_overlap=1,
            oss_recall_quota_specific_overlap=1,
        ),
    )


def test_p17k_requires_p17f_first_slot_then_strong_second_slot():
    spec = _candidate_spec("P17_K")

    assert _passes_family_retention_filter(spec, _row(oss_recall_exact_name=True), accepted_count=0)
    assert not _passes_family_retention_filter(
        spec,
        _row(
            "rebar",
            oss_recall_source_family_count=2,
            oss_recall_support_count=3,
            oss_recall_overlap=2,
            oss_recall_quota_name_overlap=1,
            oss_recall_quota_specific_overlap=1,
        ),
        accepted_count=1,
    )
    assert _passes_family_retention_filter(
        spec,
        _row(
            "rebar",
            oss_recall_source_family_count=2,
            oss_recall_support_count=5,
            oss_recall_overlap=3,
            oss_recall_quota_name_overlap=1,
            oss_recall_quota_specific_overlap=2,
        ),
        accepted_count=1,
    )


def test_precision_family_retention_blocks_non_core_families():
    spec = _candidate_spec("P17_J")

    assert not _passes_family_retention_filter(spec, _row("pipe", oss_recall_exact_name=True))
    assert not _passes_family_retention_filter(spec, _row("support", oss_recall_exact_name=True))
