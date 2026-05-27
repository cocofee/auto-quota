from tools.goal_17x_false_candidate_precision_guard_dev_oof_shadow import _candidate_spec, _passes_p17_filter


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


def test_p17a_accepts_exact_name_or_strong_multifield_only():
    spec = _candidate_spec("P17_A")

    assert _passes_p17_filter(spec, _row(oss_recall_exact_name=True))
    assert _passes_p17_filter(
        spec,
        _row(
            oss_recall_source_family_count=2,
            oss_recall_support_count=4,
            oss_recall_overlap=3,
            oss_recall_quota_name_overlap=1,
        ),
    )
    assert not _passes_p17_filter(spec, _row(oss_recall_source_family_count=2, oss_recall_support_count=3))


def test_p17_blocks_pipe_and_support():
    spec = _candidate_spec("P17_A")

    assert not _passes_p17_filter(spec, _row("pipe", oss_recall_exact_name=True))
    assert not _passes_p17_filter(spec, _row("support", oss_recall_exact_name=True))


def test_p17c_uses_family_specific_guards():
    spec = _candidate_spec("P17_C")

    assert _passes_p17_filter(
        spec,
        _row("concrete", oss_recall_source_family_count=2, oss_recall_support_count=4, oss_recall_overlap=3),
    )
    assert not _passes_p17_filter(spec, _row("concrete", oss_recall_exact_name=True))
    assert _passes_p17_filter(spec, _row("pump", oss_recall_support_count=3, oss_recall_overlap=2))
    assert _passes_p17_filter(spec, _row("rebar", oss_recall_exact_name=True))


def test_p17d_requires_very_strong_observable_challenger():
    spec = _candidate_spec("P17_D")

    assert _passes_p17_filter(spec, _row(oss_recall_exact_name=True))
    assert _passes_p17_filter(
        spec,
        _row(oss_recall_source_family_count=2, oss_recall_support_count=6, oss_recall_overlap=4),
    )
    assert not _passes_p17_filter(
        spec,
        _row(oss_recall_source_family_count=2, oss_recall_support_count=4, oss_recall_overlap=3),
    )
