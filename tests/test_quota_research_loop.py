from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools import autoresearch_manager as arm
from tools import quota_research_loop as qrl


def test_resolve_template_from_direction_keywords():
    template = qrl.resolve_template("P0: 全国安装 配管对象模板")

    assert template is not None
    assert template["name"] == "conduit"
    assert "src/query_builder.py" in template["editable_files"]


def test_build_experiment_plan_uses_active_direction(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        state_path = Path(temp_dir) / "autoresearch_state.json"
        monkeypatch.setattr(arm, "STATE_PATH", state_path)
        arm.update_queue(active=["P0: 全国安装 配电箱/配电柜对象模板"])

        record = qrl.build_experiment_plan(idea="distribution box template")

        assert record["template"] == "distribution_box"
        assert "配电箱" in record["fast_command"]
        assert record["direction"] == "P0: 全国安装 配电箱/配电柜对象模板"


def test_start_experiment_persists_loop_state_and_jsonl(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        loop_state_path = Path(temp_dir) / "quota_research_loop_state.json"
        log_path = Path(temp_dir) / "quota_research_experiments.jsonl"
        monkeypatch.setattr(qrl, "STATE_PATH", loop_state_path)
        monkeypatch.setattr(qrl, "EXPERIMENT_LOG_PATH", log_path)
        monkeypatch.setattr(qrl, "git_current_commit", lambda: "abc123")
        monkeypatch.setattr(qrl, "git_current_branch", lambda: "main")
        monkeypatch.setattr(qrl, "git_is_dirty", lambda: True)

        record = qrl.start_experiment(
            direction="P0: 全国安装 配管对象模板",
            idea="conduit material alias cleanup",
        )

        saved = json.loads(loop_state_path.read_text(encoding="utf-8"))
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        logged = json.loads(lines[-1])

        assert saved["current_template"] == "conduit"
        assert saved["current_direction"] == "P0: 全国安装 配管对象模板"
        assert saved["recent_experiments"][-1]["experiment_id"] == record["experiment_id"]
        assert logged["idea"] == "conduit material alias cleanup"
        assert logged["template"] == "conduit"
        assert record["base_commit"] == "abc123"
        assert record["base_branch"] == "main"
        assert record["base_dirty"] is True


def test_start_experiment_require_clean_git_rejects_dirty_workspace(monkeypatch):
    monkeypatch.setattr(qrl, "git_is_dirty", lambda: True)

    try:
        qrl.start_experiment(
            direction="P0: 全国安装 配管对象模板",
            idea="should fail",
            require_clean_git=True,
        )
    except RuntimeError as exc:
        assert "git 工作区不干净" in str(exc)
    else:
        raise AssertionError("expected RuntimeError for dirty git workspace")


def test_execute_experiment_bootstraps_template_metrics(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        loop_state_path = Path(temp_dir) / "quota_research_loop_state.json"
        log_path = Path(temp_dir) / "quota_research_experiments.jsonl"
        runs_dir = Path(temp_dir) / "runs"
        monkeypatch.setattr(qrl, "STATE_PATH", loop_state_path)
        monkeypatch.setattr(qrl, "EXPERIMENT_LOG_PATH", log_path)
        monkeypatch.setattr(qrl, "RUNS_DIR", runs_dir)

        def fake_run(args, timeout_sec, summary_path, log_file):
            return {
                "status": "ok",
                "summary": {"json_overall": {"hit_rate": 32.0}},
                "log_path": str(log_file),
                "summary_path": str(summary_path),
            }

        monkeypatch.setattr(qrl, "_run_benchmark", fake_run)

        result = qrl.execute_experiment(
            direction="P0: 全国安装 配管对象模板",
            idea="bootstrap conduit",
        )

        saved = json.loads(loop_state_path.read_text(encoding="utf-8"))
        assert result["status"] == "bootstrap"
        assert saved["template_metrics"]["conduit"]["best_fast_hit_rate"] == 32.0
        assert saved["template_metrics"]["conduit"]["best_full_hit_rate"] == 32.0


def test_execute_experiment_discards_when_fast_screen_regresses(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        loop_state_path = Path(temp_dir) / "quota_research_loop_state.json"
        log_path = Path(temp_dir) / "quota_research_experiments.jsonl"
        runs_dir = Path(temp_dir) / "runs"
        monkeypatch.setattr(qrl, "STATE_PATH", loop_state_path)
        monkeypatch.setattr(qrl, "EXPERIMENT_LOG_PATH", log_path)
        monkeypatch.setattr(qrl, "RUNS_DIR", runs_dir)

        qrl.save_loop_state({
            "updated_at": "",
            "best_commit": "",
            "current_direction": "",
            "current_template": "",
            "recent_experiments": [],
            "template_metrics": {"conduit": {"best_fast_hit_rate": 32.0, "best_full_hit_rate": 41.4}},
        })

        calls = []

        def fake_run(args, timeout_sec, summary_path, log_file):
            calls.append(list(args))
            return {
                "status": "ok",
                "summary": {"json_overall": {"hit_rate": 30.5}},
                "log_path": str(log_file),
                "summary_path": str(summary_path),
            }

        monkeypatch.setattr(qrl, "_run_benchmark", fake_run)

        result = qrl.execute_experiment(
            direction="P0: 全国安装 配管对象模板",
            idea="regress conduit",
        )

        assert result["status"] == "discard"
        assert result["delta"] == -1.5
        assert len(calls) == 1


def test_execute_experiment_resumes_existing_experiment_by_id(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        loop_state_path = Path(temp_dir) / "quota_research_loop_state.json"
        log_path = Path(temp_dir) / "quota_research_experiments.jsonl"
        runs_dir = Path(temp_dir) / "runs"
        monkeypatch.setattr(qrl, "STATE_PATH", loop_state_path)
        monkeypatch.setattr(qrl, "EXPERIMENT_LOG_PATH", log_path)
        monkeypatch.setattr(qrl, "RUNS_DIR", runs_dir)
        monkeypatch.setattr(qrl, "git_current_commit", lambda: "trial123")
        monkeypatch.setattr(qrl, "git_is_dirty", lambda: False)

        qrl.save_loop_state({
            "updated_at": "",
            "best_commit": "",
            "current_direction": "P0: 全国安装 配管对象模板",
            "current_template": "conduit",
            "recent_experiments": [{
                "experiment_id": "exp-1",
                "direction": "P0: 全国安装 配管对象模板",
                "template": "conduit",
                "time": "2026-03-13 09:00:00",
                "status": "planned",
                "idea": "resume conduit",
                "base_commit": "base123",
                "base_branch": "main",
                "base_dirty": False,
            }],
            "template_metrics": {"conduit": {"best_fast_hit_rate": 32.0}},
        })

        def fake_run(args, timeout_sec, summary_path, log_file):
            return {
                "status": "ok",
                "summary": {"json_overall": {"hit_rate": 32.0}},
                "log_path": str(log_file),
                "summary_path": str(summary_path),
            }

        monkeypatch.setattr(qrl, "_run_benchmark", fake_run)

        result = qrl.execute_experiment(experiment_id="exp-1", run_full=False)
        saved = json.loads(loop_state_path.read_text(encoding="utf-8"))

        assert result["experiment_id"] == "exp-1"
        assert result["idea"] == "resume conduit"
        assert saved["recent_experiments"][-1]["experiment_id"] == "exp-1"
        assert saved["recent_experiments"][-1]["status"] == "keep"


def test_execute_experiment_resets_git_on_discard_when_base_is_clean(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        loop_state_path = Path(temp_dir) / "quota_research_loop_state.json"
        log_path = Path(temp_dir) / "quota_research_experiments.jsonl"
        runs_dir = Path(temp_dir) / "runs"
        monkeypatch.setattr(qrl, "STATE_PATH", loop_state_path)
        monkeypatch.setattr(qrl, "EXPERIMENT_LOG_PATH", log_path)
        monkeypatch.setattr(qrl, "RUNS_DIR", runs_dir)
        monkeypatch.setattr(qrl, "git_current_commit", lambda: "trial123")
        monkeypatch.setattr(qrl, "git_is_dirty", lambda: True)

        qrl.save_loop_state({
            "updated_at": "",
            "best_commit": "",
            "current_direction": "P0: 全国安装 配管对象模板",
            "current_template": "conduit",
            "recent_experiments": [{
                "experiment_id": "exp-2",
                "direction": "P0: 全国安装 配管对象模板",
                "template": "conduit",
                "time": "2026-03-13 09:00:00",
                "status": "planned",
                "idea": "discard conduit",
                "base_commit": "base123",
                "base_branch": "main",
                "base_dirty": False,
            }],
            "template_metrics": {"conduit": {"best_fast_hit_rate": 32.0, "best_full_hit_rate": 41.4}},
        })

        def fake_run(args, timeout_sec, summary_path, log_file):
            return {
                "status": "ok",
                "summary": {"json_overall": {"hit_rate": 30.0}},
                "log_path": str(log_file),
                "summary_path": str(summary_path),
            }

        resets = []
        monkeypatch.setattr(qrl, "_run_benchmark", fake_run)
        monkeypatch.setattr(qrl, "git_reset_hard", lambda commit: resets.append(commit))

        result = qrl.execute_experiment(
            experiment_id="exp-2",
            run_full=False,
            git_reset_on_discard=True,
        )

        assert result["status"] == "discard"
        assert resets == ["base123"]
        assert result["git_action"] == "reset --hard base123"


def test_execute_experiment_commits_keep_result_and_updates_best_commit(monkeypatch):
    with tempfile.TemporaryDirectory() as temp_dir:
        loop_state_path = Path(temp_dir) / "quota_research_loop_state.json"
        log_path = Path(temp_dir) / "quota_research_experiments.jsonl"
        runs_dir = Path(temp_dir) / "runs"
        monkeypatch.setattr(qrl, "STATE_PATH", loop_state_path)
        monkeypatch.setattr(qrl, "EXPERIMENT_LOG_PATH", log_path)
        monkeypatch.setattr(qrl, "RUNS_DIR", runs_dir)
        monkeypatch.setattr(qrl, "git_current_commit", lambda: "precommit")
        monkeypatch.setattr(qrl, "git_current_branch", lambda: "main")

        dirty_states = iter([True, True, True, False])
        monkeypatch.setattr(qrl, "git_is_dirty", lambda: next(dirty_states))

        def fake_run(args, timeout_sec, summary_path, log_file):
            return {
                "status": "ok",
                "summary": {"json_overall": {"hit_rate": 32.0}},
                "log_path": str(log_file),
                "summary_path": str(summary_path),
            }

        commits = []
        monkeypatch.setattr(qrl, "_run_benchmark", fake_run)
        monkeypatch.setattr(qrl, "git_commit_files", lambda files, message: commits.append((list(files), message)) or "newcommit123")

        result = qrl.execute_experiment(
            direction="P0: 全国安装 配管对象模板",
            idea="commit conduit",
            run_full=False,
            git_commit_on_keep=True,
            commit_message="autoresearch keep",
        )
        saved = json.loads(loop_state_path.read_text(encoding="utf-8"))

        assert result["status"] in {"bootstrap", "keep"}
        assert result["git_action"] == "commit newcommit123"
        assert result["trial_commit"] == "newcommit123"
        assert result["trial_dirty"] is False
        assert commits == [(["src/query_builder.py"], "autoresearch keep")]
        assert saved["best_commit"] == "newcommit123"
        assert saved["recent_experiments"][-1]["trial_commit"] == "newcommit123"
