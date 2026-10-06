"""Frozen repair caps, real budget boundaries, and opt-in native readback."""
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import pytest

from cochem_pipeline.resource_limits import ResourceLimits, ResourcePolicyError
from cochem_pipeline.windows import WindowsIsolationError
from cochem_supervisor.config import load_config
from cochem_supervisor.runner import RepairRunner
from cochem_supervisor.windows import verify_repair_execution_limits
from supervisor_tests.test_config import document, write_config
from supervisor_tests.test_engine import LocalSupervisor, incident
from supervisor_tests.test_windows import actual_windows_supervisor


def test_frozen_repair_defaults_and_reviewed_lower_limits_survive_config_reload(tmp_path):
    raw = document(tmp_path)
    config = load_config(write_config(tmp_path,raw))
    assert config['repair_execution_limits'] == ResourceLimits().as_dict()
    raw['repair_execution_limits']={'memory_limit_mb':1024,'cpu_rate_percent':10,'max_processes':8}
    config = load_config(write_config(tmp_path,raw))
    runner = RepairRunner(config)
    config['repair_execution_limits']['memory_limit_mb']=64000
    assert runner.execution_limits.memory_limit_mb == 1024
    assert runner.execution_limits.cpu_rate_percent == 10
    assert runner.execution_limits.max_processes == 8
    assert runner.execution_limits.e_core_policy == 'required'


@pytest.mark.parametrize('limits',[{'memory_limit_mb':True},{'cpu_rate_percent':0},
                                  {'max_processes':0},{'e_core_policy':'guess'},
                                  {'launch_jitter_min_ms':0},{'unbounded':True}])
def test_invalid_limits_fail_at_both_protected_config_and_runner_boundary(tmp_path,limits):
    raw=document(tmp_path)
    raw['repair_execution_limits']=limits
    with pytest.raises(ValueError):
        load_config(write_config(tmp_path,raw))
    with pytest.raises(ValueError):
        RepairRunner({'repair_execution_limits':limits})


class ResourcesUnavailable(LocalSupervisor):
    """Explicit unavailable platform boundary; actual engine/SQLite underneath."""
    def _repair_boundary_check(self):
        raise ResourcePolicyError('Protocol fixture: required native topology or memory unavailable')


def test_resource_hold_charges_no_attempt_and_does_not_stop_watchdog_recovery(tmp_path):
    supervisor=ResourcesUnavailable(tmp_path)
    observed=incident(supervisor)
    assert supervisor._repair(observed) is False
    assert supervisor._version_checks() == []
    assert supervisor.ledger.get_incident(observed['fingerprint'])['attempts'] == 0
    assert supervisor.runner.repair_calls == supervisor.runner.process_calls == []
    assert supervisor.clear_calls == 0
    assert supervisor.recover() is None


@pytest.mark.skipif(os.name=='nt',reason='Actual non-Windows rejection')
def test_linux_cannot_claim_native_repair_topology_or_commit_readiness():
    with pytest.raises(WindowsIsolationError,match='real Windows SYSTEM'):
        verify_repair_execution_limits({})


def test_actual_repair_child_receipt_contains_read_back_native_limits(actual_windows_supervisor):
    config=actual_windows_supervisor
    logs=Path(config['private_root'])/('repair-resource-test-'+uuid.uuid4().hex)
    try:
        result=RepairRunner(config).run_process(config['repair_worker'],
            [str(Path(sys.executable).resolve()),'-I','-c','print("bounded native repair child")'],
            Path(config['repair_workspace']),logs,timeout_seconds=30,heartbeat=lambda:True)
        assert result['exit_code']==0 and result['cleanup_verified'] is True
        assert result['resource_limits']['native_limits_verified'] is True
        for name in ('memory_limit_mb','cpu_rate_percent','max_processes','e_core_policy'):
            assert result['resource_limits'][name]==config['repair_execution_limits'][name]
        assert result['resource_readiness']['commit']['available'] is True
        assert json.loads((logs/'process-receipt.json').read_text())==result
    finally:
        if logs.exists():
            shutil.rmtree(logs)
