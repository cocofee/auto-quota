from src.match_core import _should_audit_fastpath


class _Decision:
    def __init__(self, *, audit_recommended: bool):
        self.audit_recommended = audit_recommended


def test_should_audit_fastpath_forces_audit_when_recommended(monkeypatch):
    monkeypatch.setattr("config.AGENT_FASTPATH_AUDIT_RATE", 0.0)

    assert _should_audit_fastpath(_Decision(audit_recommended=True)) is True


def test_should_audit_fastpath_respects_zero_rate_when_not_recommended(monkeypatch):
    monkeypatch.setattr("config.AGENT_FASTPATH_AUDIT_RATE", 0.0)

    assert _should_audit_fastpath(_Decision(audit_recommended=False)) is False
