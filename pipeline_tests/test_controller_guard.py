"""Real controller measurements and separate native scheduling acceptance."""
import json
import os
import subprocess
import sys

import psutil
import pytest

from cochem_pipeline.controller_guard import ControllerMonitor, assess_controller
from cochem_pipeline.resource_limits import ResourceLimits, ResourcePolicyError, controller_limits


@pytest.mark.parametrize('rss,cpu,ready',[(50,5,True),(50.001,0,False),(1,5.001,False),
                                        (None,1,False),(1,None,False),(float('nan'),0,False)])
def test_strict_warden_budgets_never_report_ready_for_missing_or_excess_measurements(rss,cpu,ready):
    result=assess_controller({'rss_mb':rss,'cpu_percent':cpu})
    assert result['ready'] is ready
    assert result['strict_srs_ready'] is ready
    assert result['action']==('none' if ready else 'pause_admission')


def test_monitor_reports_actual_current_process_and_bounded_scope():
    monitor=ControllerMonitor()
    measured=monitor.sample(force=True)
    assert measured['measurements']['pid']==os.getpid()
    assert measured['measurements']['rss_mb']>0
    assert measured['measurements']['allowed_logical_processors']==psutil.Process().cpu_affinity()
    assert measured['measurements']['cpu_percent']>=0
    assert monitor.sample() is measured
    assert measured['ready']==(measured['measurements']['rss_mb']<=50
                               and measured['measurements']['cpu_percent']<=5)


def test_full_runtime_import_has_a_measured_idle_budget_in_fresh_process():
    result=subprocess.run([sys.executable,'-I','-c',
        'import cochem_pipeline.runtime; from cochem_pipeline.controller_guard import ControllerMonitor; '
        'import json; print(json.dumps(ControllerMonitor().sample(force=True)))'],
        capture_output=True,text=True,timeout=15,check=True)
    observed=json.loads(result.stdout)
    assert observed['measurements']['rss_mb']>0
    assert observed['ready']==(observed['measurements']['rss_mb']<=50
                              and observed['measurements']['cpu_percent']<=5)


def test_native_controller_policy_cannot_be_claimed_on_linux():
    if os.name=='nt':
        pytest.skip('Unsupported-host refusal is a Linux contract')
    with pytest.raises(ResourcePolicyError,match='requires Windows'):
        controller_limits(ResourceLimits(),apply=True)


@pytest.mark.skipif(os.name!='nt' or os.environ.get('COCHEM_RUN_NATIVE_RESOURCE_TESTS')!='1',
                   reason='Actual Warden priority/affinity requires native Windows resource acceptance')
def test_actual_native_controller_priority_and_efficiency_affinity():
    result=subprocess.run([sys.executable,'-I','-c',
        'import json; from cochem_pipeline.resource_limits import ResourceLimits,controller_limits; '
        'controller_limits(ResourceLimits(),apply=True); '
        'print(json.dumps(controller_limits(ResourceLimits())))'],
        capture_output=True,text=True,timeout=15,check=True,creationflags=0x08000200)
    evidence=json.loads(result.stdout)
    assert evidence['controller_policy_verified'] is True
    assert evidence['topology_verified'] is True
    assert set(evidence['observed_logical_processors'])==set(evidence['logical_processors'])
