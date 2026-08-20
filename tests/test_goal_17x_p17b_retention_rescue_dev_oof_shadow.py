from tools.goal_17x_p17b_retention_rescue_dev_oof_shadow import _candidate_spec, _passes_rescue_filter


def _row(family="concrete", **overrides):
    row = {
        "query_family": family,
        "oss_recall_exact_name": False,
        "oss_recall_source_family_count": 1,
        "oss_recall_support_count": 2,
        "oss_recall_overlap": 2,
        "oss_recall_quota_name_overlap": 0,
    }
    row.update(overrides)
    return row


def test_p17e_keeps_p17b_trunk_and_adds_rebar_support_rescue():
    spec = _candidate_spec("P17_E")

    assert _passes_rescue_filter(spec, _row(oss_recall_exact_name=True))
    assert _passes_rescue_filter(spec, _row("rebar", oss_recall_support_count=3, oss_recall_overlap=2))
    assert not _passes_rescue_filter(spec, _row("pump", oss_recall_support_count=3, oss_recall_overlap=2))


def test_p17f_adds_only_exact_pump_rebar_rescue():
    spec = _candidate_spec("P17_F")

    assert _passes_rescue_filter(spec, _row("pump", oss_recall_exact_name=True))
    assert _passes_rescue_filter(spec, _row("rebar", oss_recall_exact_name=True))
    assert not _passes_rescue_filter(spec, _row("rebar", oss_recall_support_count=3, oss_recall_overlap=2))


def test_p17g_first_candidate_uses_p17b_trunk_then_very_strong_second_candidate():
    spec = _candidate_spec("P17_G")

    assert _passes_rescue_filter(spec, _row(oss_recall_exact_name=True), accepted_count=0)
    assert not _passes_rescue_filter(
        spec,
        _row(oss_recall_source_family_count=2, oss_recall_support_count=4, oss_recall_overlap=3),
        accepted_count=1,
    )
    assert _passes_rescue_filter(
        spec,
        _row(oss_recall_source_family_count=2, oss_recall_support_count=6, oss_recall_overlap=4),
        accepted_count=1,
    )


def test_retention_rescue_blocks_non_core_families():
    spec = _candidate_spec("P17_E")

    assert not _passes_rescue_filter(spec, _row("pipe", oss_recall_exact_name=True))
    assert not _passes_rescue_filter(spec, _row("support", oss_recall_exact_name=True))
