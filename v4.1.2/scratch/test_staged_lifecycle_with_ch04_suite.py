"""Test the staged lifecycle.py against the Chapter 4 lifecycle test suite."""
import importlib.util
from pathlib import Path
import sys
import pytest

# Load staged lifecycle
staged_path = Path(r"D:\__CoChem\__agentic\v4.1.2\.staging\ch04\lifecycle\lifecycle.py")
spec = importlib.util.spec_from_file_location("cochem.blackboard.lifecycle", str(staged_path))
staged_mod = importlib.util.module_from_spec(spec)
sys.modules["cochem.blackboard.lifecycle"] = staged_mod
spec.loader.exec_module(staged_mod)

# Now import tests from test_ch04_task_matrix_blackboard
sys.path.insert(0, r"D:\__CoChem\__agentic\v4.1.2")
sys.path.insert(0, r"D:\__CoChem\__agentic\v4.1.2\src")
from tests.test_ch04_task_matrix_blackboard import (
    test_ch04_fr_005_third_failed_attempt_blocks_and_is_never_reclaimed,
    test_ch04_fr_005_failure_below_limit_is_failed_not_blocked,
    test_ch04_fr_009_every_exit_from_running_clears_lease,
    test_ch04_complete_task_true_only_for_running_task,
    test_ch04_fail_task_stores_error_log_and_honours_max_attempts,
)

def test_staged_ch04_fr_005_third_failed(tmp_path: Path):
    test_ch04_fr_005_third_failed_attempt_blocks_and_is_never_reclaimed(tmp_path)

def test_staged_ch04_fr_005_below_limit(tmp_path: Path):
    test_ch04_fr_005_failure_below_limit_is_failed_not_blocked(tmp_path)

def test_staged_ch04_fr_009_every_exit(tmp_path: Path):
    test_ch04_fr_009_every_exit_from_running_clears_lease(tmp_path)

def test_staged_ch04_complete_task(tmp_path: Path):
    test_ch04_complete_task_true_only_for_running_task(tmp_path)

def test_staged_ch04_fail_task(tmp_path: Path):
    test_ch04_fail_task_stores_error_log_and_honours_max_attempts(tmp_path)
