from src.accuracy_tracker import AccuracyTracker


class _FailingConn:
    def __init__(self):
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("db write failed")

    def commit(self):
        pass

    def close(self):
        self.closed = True


def test_record_run_closes_connection_on_error(monkeypatch):
    conn = _FailingConn()
    monkeypatch.setattr("src.accuracy_tracker._get_conn", lambda: conn)

    AccuracyTracker().record_run({"total": 1}, input_file="x.xlsx", mode="agent", province="test")

    assert conn.closed is True


def test_record_review_closes_connection_on_error(monkeypatch):
    conn = _FailingConn()
    monkeypatch.setattr("src.accuracy_tracker._get_conn", lambda: conn)

    AccuracyTracker().record_review(
        input_file="x.xlsx",
        province="test",
        total=1,
        auto_corrections=0,
        manual_items=0,
        measure_items=0,
        correct_count=0,
    )

    assert conn.closed is True
