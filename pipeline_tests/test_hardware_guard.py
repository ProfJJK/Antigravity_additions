"""Functional hardware admission checks against the actual local OS."""

from __future__ import annotations

import json
import math
import shutil
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import psutil
import pytest

from cochem_pipeline.hardware_guard import (
    HardwareGuard, HardwarePolicy, GovernorState, assess_resources, effective_execution_policy,
)


def test_snapshot_reports_real_host_and_workspace_resources(tmp_path: Path) -> None:
    result = HardwareGuard(workspace=tmp_path, max_cpu_percent=100).snapshot()
    assert result["workspace"] == str(tmp_path.resolve())
    assert result["cpu_count"] > 0
    assert result["cpu_count"] <= psutil.cpu_count(logical=True)
    assert 0 <= result["cpu_percent"] <= 100
    assert result["memory_total_mb"] == psutil.virtual_memory().total / (1024 * 1024)
    assert 0 <= result["memory_available_mb"] <= result["memory_total_mb"]
    assert 0 <= result["disk_free_mb"] <= result["disk_total_mb"]
    assert result["disk_total_mb"] > 0
    assert 0 <= result["capacity"] <= 4
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("max_agents", [0, 1, 2, 4, 8, 100])
def test_actual_capacity_obeys_configured_and_hard_limits(tmp_path: Path, max_agents: int) -> None:
    guard = HardwareGuard(
        max_agents=max_agents,
        workspace=tmp_path,
        min_free_memory_mb=0,
        min_free_disk_mb=0,
        per_agent_memory_mb=0.001,
        max_cpu_percent=100,
    )
    snapshot = guard.snapshot()
    assert snapshot["capacity"] <= min(max_agents, snapshot["cpu_count"])
    assert guard.capacity() <= max_agents


def test_real_memory_below_explicit_reserve_blocks_admission(tmp_path: Path) -> None:
    total_mb = psutil.virtual_memory().total / (1024 * 1024)
    guard = HardwareGuard(workspace=tmp_path, min_free_memory_mb=total_mb + 1024)
    result = guard.snapshot()
    assert result["capacity"] == 0
    assert any("memory" in reason for reason in result["reasons"])


def test_per_worker_memory_budget_blocks_admission(tmp_path: Path) -> None:
    total_mb = psutil.virtual_memory().total / (1024 * 1024)
    result = HardwareGuard(workspace=tmp_path, per_agent_memory_mb=total_mb + 1024).snapshot()
    assert result["capacity"] == 0
    assert any("memory" in reason for reason in result["reasons"])


def test_execution_policy_reserves_actual_configured_tree_caps_and_preserves_host_reserves():
    from cochem_pipeline.resource_limits import ResourceLimits
    configured=HardwarePolicy()
    native=ResourceLimits().memory_limit_mb
    effective=effective_execution_policy(configured,native,4096)
    assert native==2048
    assert configured.per_agent_memory_mb==256
    assert effective.per_agent_memory_mb==4096
    assert effective.min_free_memory_mb==configured.min_free_memory_mb
    assert effective.min_free_commit_mb==configured.min_free_commit_mb
    assert effective_execution_policy(effective,1024,0)==effective
    assert effective_execution_policy(configured.as_dict(),native).per_agent_memory_mb==native


def test_conservative_execution_policy_uses_measured_free_ram_without_second_ramdisk_subtraction(tmp_path):
    # This is a real host reading, not evidence of a Windows worker/container.
    total_mb=psutil.virtual_memory().total/(1024*1024)
    effective=effective_execution_policy(HardwarePolicy(),total_mb+1024,4096)
    actual=HardwareGuard(workspace=tmp_path,policy=effective).snapshot()
    assert actual['capacity']==0
    assert actual['per_agent_memory_mb']==total_mb+1024
    assert actual['memory_total_mb']==total_mb
    assert any('memory' in reason for reason in actual['reasons'])


def test_four_container_seats_require_sixteen_gib_plus_configured_host_memory_reserve():
    effective=effective_execution_policy(HardwarePolicy(),2048,4096)
    measured=policy_case()
    measured['memory']['available_mb']=4*4096+effective.min_free_memory_mb
    assert assess_resources(measured,effective)['capacity']==4
    measured['memory']['available_mb']-=1
    assert assess_resources(measured,effective)['capacity']==3


@pytest.mark.parametrize('native,docker',[(0,4096),(-1,4096),(True,4096),(2048,-1),
                                        (2048,True),(math.inf,0),(2048,math.nan)])
def test_execution_policy_rejects_invalid_native_and_container_caps(native,docker):
    with pytest.raises(ValueError):
        effective_execution_policy(HardwarePolicy(),native,docker)


def test_real_disk_below_explicit_reserve_blocks_admission(tmp_path: Path) -> None:
    reserve = shutil.disk_usage(tmp_path).total/(1024*1024)+1024
    result = HardwareGuard(workspace=tmp_path, min_free_disk_mb=reserve).snapshot()
    assert result["capacity"] == 0
    assert any("disk" in reason for reason in result["reasons"])


def test_missing_workspace_fails_closed_without_creating_it(tmp_path: Path) -> None:
    workspace = tmp_path / "absent"
    result = HardwareGuard(workspace=workspace).snapshot()
    assert result["capacity"] == 0
    assert result["disk_free_mb"] is None
    assert any("workspace volumes" in reason for reason in result["reasons"])
    assert not workspace.exists()


@pytest.mark.parametrize(
    "settings",
    [
        {"max_agents": -1},
        {"max_agents": 2.5},
        {"max_agents": True},
        {"min_free_memory_mb": -1},
        {"min_free_memory_mb": math.nan},
        {"min_free_disk_mb": math.inf},
        {"min_free_disk_mb": "100"},
        {"per_agent_memory_mb": 0},
        {"max_cpu_percent": 101},
        {"max_cpu_percent": -1},
    ],
)
def test_invalid_resource_bounds_are_rejected(settings: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        HardwareGuard(**settings)


def policy_case():
    """Pure policy input, explicitly not evidence of physical host telemetry."""
    return {'cpu':{'available':True,'count':8,'physical_count':8,'physical_count_available':True,'percent':10,'temperature_available':True,'temperature_celsius':50},
            'memory':{'available':True,'total_mb':32768,'available_mb':24000},
            'commit':{'available':True,'total_mb':10000,'limit_mb':64000,'free_mb':54000},
            'gpus':{'available':True,'devices':[{'id':'policy-case','total_mb':24000,'used_mb':6000,
                                               'utilization_percent':20,'temperature_celsius':50}]},
            'disks':{'available':True,'volumes':[{'path':'policy-case','total_mb':100000,'free_mb':80000}],
                     'io':{'available':True,'total_mb_s':2,'total_iops':30}}}


@pytest.mark.parametrize('cpu,free,capacity',[(29,17000,4),(30,17000,3),(49,9000,3),(50,9000,2),
    (69,5000,2),(70,5000,1),(84,3000,1),(85,3000,0),(10,2000,0)])
def test_pure_historical_cpu_ram_bands(cpu,free,capacity):
    sample=policy_case()
    sample['cpu']['percent'],sample['memory']['available_mb']=cpu,free
    assert assess_resources(sample,HardwarePolicy())['capacity']==capacity


@pytest.mark.parametrize('section',['cpu','memory','commit','gpus','disks'])
def test_pure_required_measurement_absence_pauses_without_inventing_critical_pressure(section):
    sample=policy_case()
    sample[section]['available']=False
    result=assess_resources(sample,HardwarePolicy())
    assert result['capacity']==0 and result['action']=='pause_admission'
    assert result['critical_reasons']==[]


def test_gpu_less_host_needs_explicit_policy_and_missing_cpu_temperature_is_still_reported():
    sample=policy_case()
    sample['gpus']={'available':False,'devices':[]}
    sample['cpu'].update(temperature_available=False,temperature_celsius=None)
    result=assess_resources(sample,HardwarePolicy(gpu_required=False))
    assert result['capacity']==0 and any('CPU temperature' in reason for reason in result['reasons'])
    explicit=HardwarePolicy(gpu_required=False,cpu_temperature_required=False)
    assert assess_resources(sample,explicit)['capacity']==4


@pytest.mark.parametrize('percent,capacity,alert',[(80,4,False),(80.1,1,True),(90,0,True),(98,0,True)])
def test_pure_gpu_vram_quarter_ceiling_and_ninety_percent_alert(percent,capacity,alert):
    sample=policy_case()
    sample['gpus']['devices'][0]['used_mb']=sample['gpus']['devices'][0]['total_mb']*percent/100
    result=assess_resources(sample,HardwarePolicy())
    assert result['capacity']==capacity and bool(result['alerts']) is alert


@pytest.mark.parametrize('section,field,value',[
    ('cpu','temperature_celsius',95),('memory','available_mb',128),('commit','free_mb',100),
])
def test_pure_immediate_thermal_and_memory_critical_actions(section,field,value):
    sample=policy_case()
    sample[section][field]=value
    result=assess_resources(sample,HardwarePolicy())
    assert result['state']=='critical' and result['action']=='terminate_active'
    assert result['capacity']==0 and result['critical_reasons']


def test_every_gpu_and_workspace_volume_can_trip_emergency():
    sample=policy_case()
    second_gpu={**sample['gpus']['devices'][0],'id':'second','temperature_celsius':93}
    sample['gpus']['devices'].append(second_gpu)
    assert assess_resources(sample,HardwarePolicy())['action']=='terminate_active'
    sample=policy_case()
    sample['disks']['volumes'].append({'path':'second-volume','total_mb':10000,'free_mb':100})
    assert assess_resources(sample,HardwarePolicy())['action']=='terminate_active'


@pytest.mark.parametrize('field,value',[('total_mb_s',121),('total_iops',4001)])
def test_pure_storage_pressure_pauses_admission_without_claiming_host_termination_needed(field,value):
    sample=policy_case()
    sample['disks']['io'][field]=value
    result=assess_resources(sample,HardwarePolicy())
    assert result['capacity']==0 and result['action']=='pause_admission'


def test_commit_reserve_is_distinct_from_available_physical_ram():
    sample=policy_case()
    sample['commit']['free_mb']=1024
    result=assess_resources(sample,HardwarePolicy())
    assert result['capacity']==0 and any('commit' in reason for reason in result['reasons'])
    assert sample['memory']['available_mb']==24000


def test_missing_cpu_telemetry_does_not_mask_a_real_memory_emergency():
    sample=policy_case()
    sample['cpu']['available']=False
    sample['memory']['available_mb']=128
    result=assess_resources(sample,HardwarePolicy())
    assert result['action']=='terminate_active'
    assert any('Physical memory' in reason for reason in result['critical_reasons'])


def test_missing_memory_telemetry_does_not_mask_sustained_cpu_pressure():
    sample=policy_case()
    sample['memory']['available']=False
    sample['cpu']['percent']=100
    policy=HardwarePolicy(critical_pressure_seconds=5)
    decision=assess_resources(sample,policy)
    state=GovernorState()
    assert state.apply(decision,policy,0)['action']=='pause_admission'
    assert state.apply(decision,policy,5)['action']=='terminate_active'


@pytest.mark.parametrize('free_mb',[-1,64001])
def test_invalid_commit_headroom_cannot_be_admitted_or_claimed_a_measured_emergency(free_mb):
    sample=policy_case()
    sample['commit']['free_mb']=free_mb
    result=assess_resources(sample,HardwarePolicy())
    assert result['action']=='pause_admission' and result['capacity']==0
    assert not result['critical_reasons']


def test_hysteresis_never_releases_new_capacity_from_a_replaced_gate():
    policy=HardwarePolicy(recovery_seconds=5,recovery_samples=2,ramp_up_step=1)
    state=GovernorState()
    safe=assess_resources(policy_case(),policy)
    assert state.apply(safe,policy,0)['capacity']==0
    assert state.apply(safe,policy,4)['capacity']==0
    assert state.apply(safe,policy,5)['capacity']==1
    assert state.apply(safe,policy,10)['capacity']==1  # only one fresh recovery sample
    assert state.apply(safe,policy,11)['capacity']==2
    pressure=policy_case(); pressure['cpu']['percent']=96
    assert state.apply(assess_resources(pressure,policy),policy,12)['capacity']==0
    assert state.apply(safe,policy,13)['capacity']==0
    assert state.apply(safe,policy,18)['capacity']==1
    with pytest.raises(ValueError,match='monotonic'):
        state.apply(safe,policy,17)


@pytest.mark.parametrize('condition',['cpu','vram'])
def test_sustained_pressure_confirmation_does_not_promote_a_single_transient_sample(condition):
    policy=HardwarePolicy(critical_pressure_seconds=5)
    sample=policy_case()
    if condition=='cpu': sample['cpu']['percent']=99.5
    else: sample['gpus']['devices'][0]['used_mb']=23900
    decision=assess_resources(sample,policy)
    state=GovernorState()
    assert state.apply(decision,policy,0)['action']=='pause_admission'
    assert state.apply(decision,policy,4.9)['action']=='pause_admission'
    assert state.apply(decision,policy,5)['action']=='terminate_active'
    state.apply(assess_resources(policy_case(),policy),policy,6)
    assert state.apply(decision,policy,7)['action']=='pause_admission'


@pytest.mark.parametrize('settings',[
    {'gpu_required':'no'}, {'cpu_temperature_required':0}, {'unexpected':1}, {'max_cpu_percent':101},
    {'critical_free_commit_mb':7000}, {'gpu_vram_throttle_percent':95}, {'recovery_samples':True},
    {'cpu_temperature_pause_c':100}, {'sample_interval_seconds':.001}, {'max_disk_iops':0},
])
def test_strict_hardware_policy_rejects_unsafe_or_misspelled_settings(settings):
    with pytest.raises(ValueError):
        HardwarePolicy.from_dict(settings)


def test_guard_caches_real_samples_and_does_not_expose_mutable_state(tmp_path):
    guard=HardwareGuard(workspace=tmp_path,policy=HardwarePolicy(sample_interval_seconds=60))
    first=guard.snapshot()
    second=guard.snapshot()
    assert second==first
    second['measurements']['cpu']['count']=-100
    assert guard.snapshot()['measurements']['cpu']['count']>0
    assert guard.capacity()==first['capacity']


def test_all_requested_workspace_paths_are_physically_probed_and_missing_one_denies_admission(tmp_path):
    existing=tmp_path/'existing'; existing.mkdir()
    missing=tmp_path/'missing'
    guard=HardwareGuard(workspaces=[existing,missing])
    result=guard.evaluate(force=True)
    assert result['workspaces']==[str(existing),str(missing)]
    assert result['capacity']==0 and result['measurements']['disks']['available'] is False
    assert not missing.exists()


def test_latest_snapshot_never_invents_a_cold_measurement_and_copies_completed_sample(tmp_path):
    guard=HardwareGuard(workspace=tmp_path)
    cold=guard.latest_snapshot()
    assert cold['capacity']==0 and cold['measured_at'] is None and cold['measurements']=={}
    assert guard._latest is None  # The read endpoint did not initiate a probe.
    sampled=guard.evaluate()
    assert guard.latest_snapshot()==sampled
    sampled['measurements']['cpu']['count']=-1
    assert guard.latest_snapshot()['measurements']['cpu']['count']>0
    with pytest.raises(ValueError): HardwareGuard(workspaces=[])
    with pytest.raises(ValueError): HardwareGuard(workspaces=str(tmp_path))


def test_configured_high_capacity_is_measured_not_limited_by_provider_agent_counts():
    measured = policy_case()
    measured['cpu']['count'] = 128
    measured['cpu']['physical_count'] = 128
    measured['memory'].update(total_mb=1024*1024,available_mb=512*1024)
    measured['commit'].update(limit_mb=1024*1024,free_mb=512*1024)
    policy = effective_execution_policy(HardwarePolicy(),2048,4096)
    assert assess_resources(measured,policy,max_agents=64)['capacity'] == 64
    measured['memory']['available_mb'] = 5*4096+policy.min_free_memory_mb
    assert assess_resources(measured,policy,max_agents=64)['capacity'] == 5
    measured['cpu']['percent'] = 99
    assert assess_resources(measured,policy,max_agents=64)['capacity'] == 0


def test_physical_core_admission_never_invents_missing_measurement():
    measured=policy_case()
    measured['cpu']['count']=64
    measured['cpu']['physical_count']=2
    assert assess_resources(measured,HardwarePolicy(),max_agents=64)['capacity']==2
    measured['cpu'].update(physical_count=None,physical_count_available=False)
    decision=assess_resources(measured,HardwarePolicy(),max_agents=64)
    assert decision['capacity']==0
    assert any('physical CPU count' in reason for reason in decision['reasons'])
    explicit=assess_resources(measured,HardwarePolicy(physical_cpu_count_required=False),max_agents=64)
    assert explicit['capacity']>0
    assert any('explicitly configured' in alert for alert in explicit['alerts'])


@pytest.mark.parametrize('ceiling,cpu,free,expected',[(1,40,20000,1),(2,40,20000,1),
    (3,40,20000,2),(5,40,20000,3),(5,60,20000,2),(5,80,20000,1),
    (64,40,256000,48),(64,60,256000,32),(64,80,256000,16)])
def test_fractional_hardware_bands_floor_with_one_seat_minimum(ceiling,cpu,free,expected):
    measured=policy_case()
    measured['cpu'].update(count=128,physical_count=128,percent=cpu)
    measured['memory'].update(total_mb=512000,available_mb=free)
    assert assess_resources(measured,HardwarePolicy(),max_agents=ceiling)['capacity']==expected
