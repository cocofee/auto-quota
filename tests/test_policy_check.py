import json
import shutil
import subprocess
import uuid
from pathlib import Path

from tools.policy_check import run_policy_check


def _workspace() -> Path:
    root = Path.cwd() / ".codex_stage" / "policy_check_tests" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=False)
    return root


def _cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _init_git_workspace(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    src = root / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "example.py").write_text("def ok():\n    return True\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/example.py"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=v36@example.invalid",
            "-c",
            "user.name=V36 Test",
            "commit",
            "-m",
            "init",
        ],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _write_next_action(root: Path, owner_module: str) -> Path:
    path = root / "next_action.json"
    path.write_text(
        json.dumps(
            {
                "action": "fix_r1_recall",
                "repair_unit_id": "R1-01::issue::fix_r1_recall::src/search_features",
                "suggested_validation_scope": {"owner_module": owner_module},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_policy_check_fails_silent_exception_pass():
    root = _workspace()
    try:
        _init_git_workspace(root)
        (root / "src" / "example.py").write_text(
            "def bad():\n    try:\n        run()\n    except Exception: pass\n",
            encoding="utf-8",
        )

        result = run_policy_check(root)

        assert result["policy_check_status"] == "fail"
        assert result["failures"][0]["check"] == "silent_exception_pass"
    finally:
        _cleanup(root)


def test_policy_check_fails_generated_knowledge_change():
    root = _workspace()
    try:
        _init_git_workspace(root)
        generated = root / "data" / "province_plugins" / "generated" / "knowledge.json"
        generated.parent.mkdir(parents=True)
        generated.write_text("{}", encoding="utf-8")

        result = run_policy_check(root)

        assert result["policy_check_status"] == "fail"
        assert result["failures"][0]["check"] == "generated_knowledge_modified"
    finally:
        _cleanup(root)


def test_policy_check_allows_small_module_change():
    root = _workspace()
    try:
        _init_git_workspace(root)
        feature = root / "src" / "search_features" / "example.py"
        feature.parent.mkdir(parents=True)
        feature.write_text("def build_query():\n    return 'query'\n", encoding="utf-8")

        result = run_policy_check(root)

        assert result["policy_check_status"] == "pass"
        assert result["complexity_delta"]["production_added_loc"] == 2
    finally:
        _cleanup(root)


def test_policy_check_next_action_allows_owner_module_change():
    root = _workspace()
    try:
        _init_git_workspace(root)
        next_action = _write_next_action(root, "src/search_features")
        feature = root / "src" / "search_features" / "example.py"
        feature.parent.mkdir(parents=True)
        feature.write_text("def build_query():\n    return 'query'\n", encoding="utf-8")

        result = run_policy_check(root, next_action_path=next_action)

        assert result["policy_check_status"] == "pass"
        assert result["owner_scope"]["status"] == "pass"
    finally:
        _cleanup(root)


def test_policy_check_next_action_fails_cross_owner_production_change():
    root = _workspace()
    try:
        _init_git_workspace(root)
        next_action = _write_next_action(root, "src/search_features")
        ranker = root / "src" / "ranking_rules" / "example.py"
        ranker.parent.mkdir(parents=True)
        ranker.write_text("def rank():\n    return 1\n", encoding="utf-8")

        result = run_policy_check(root, next_action_path=next_action)

        assert result["policy_check_status"] == "fail"
        assert result["failures"][0]["check"] == "owner_scope_violation"
        assert result["failures"][0]["paths"] == ["src/ranking_rules/example.py"]
    finally:
        _cleanup(root)
