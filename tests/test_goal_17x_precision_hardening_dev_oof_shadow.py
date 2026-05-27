from tools.goal_17x_precision_hardening_dev_oof_shadow import (
    PrecisionHardenedRecallSource,
    _candidate_spec,
    _passes_candidate_filter,
)


def _row(family, **overrides):
    row = {
        "query_family": family,
        "oss_recall_exact_name": False,
        "oss_recall_source_family_count": 1,
        "oss_recall_quota_specific_overlap": 1,
        "oss_recall_quota_name_overlap": 0,
    }
    row.update(overrides)
    return row


def test_h17a_blocks_pipe_and_support():
    spec = _candidate_spec("H17_A")

    assert _passes_candidate_filter(spec, _row("concrete"))
    assert not _passes_candidate_filter(spec, _row("pipe"))
    assert not _passes_candidate_filter(spec, _row("support"))


def test_h17b_re_admits_only_strong_pipe_evidence():
    spec = _candidate_spec("H17_B")

    assert not _passes_candidate_filter(spec, _row("pipe"))
    assert _passes_candidate_filter(
        spec,
        _row(
            "pipe",
            oss_recall_source_family_count=2,
            oss_recall_quota_specific_overlap=2,
            oss_recall_quota_name_overlap=1,
        ),
    )
    assert _passes_candidate_filter(spec, _row("pipe", oss_recall_exact_name=True))


def test_h17d_rank1_veto_blocks_weak_challenger_only_when_baseline_rank1():
    spec = _candidate_spec("H17_D")
    weak = _row("support")

    assert not _passes_candidate_filter(spec, weak, {"_h17_baseline_rank": 1})
    assert _passes_candidate_filter(spec, weak, {"_h17_baseline_rank": 2})
    assert _passes_candidate_filter(spec, _row("support", oss_recall_exact_name=True), {"_h17_baseline_rank": 1})


def test_precision_source_preserves_topk_after_filtering():
    class FakeDelegate:
        def collect(self, **_kwargs):
            return [
                _row("pipe", quota_id="P-weak"),
                _row("pipe", quota_id="P-strong", oss_recall_exact_name=True),
                _row("concrete", quota_id="C-1"),
            ]

    source = PrecisionHardenedRecallSource(FakeDelegate(), _candidate_spec("H17_B"))
    rows = source.collect(province="p", query_text="q", query_family="pipe", top_k=1)

    assert [row["quota_id"] for row in rows] == ["P-strong"]
