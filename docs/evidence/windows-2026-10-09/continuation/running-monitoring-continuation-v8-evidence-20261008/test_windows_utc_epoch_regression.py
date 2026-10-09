"""Windows PS5 UTC epoch regression and read-only native clock evidence.

No system timezone/clock changes, provider, task, private report or Apply.
Representative zones are converted in memory. The native fixture calls only
clock/filetime and monotonic-counter APIs and saves ordinary workspace evidence.
"""
import base64
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

W = Path(__file__).resolve().parent
PS = r'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
HELD = W / 'install-held-supervisor-observation-r3-v3.ps1'
RESOURCE = W / 'install-resource-observer-r3-v5.ps1'
BATCH = W / 'run-post-commissioning-setup-r3-v4.ps1'
OLD_EXPRESSION = "([DateTime]::UtcNow-[DateTime]'1970-01-01T00:00:00Z').TotalSeconds"
UTC_EXPRESSION = '[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000.0'


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def run(body):
    prefix = r'''$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';Set-StrictMode -Version Latest
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
'''
    encoded = base64.b64encode((prefix + body).encode('utf-16le')).decode()
    result = subprocess.run([PS, '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not result.stderr, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('offset_minutes', [-720, -360, -300, 0, 330, 345, 780, 840])
def test_epoch_conversion_and_thirty_second_freshness_are_timezone_independent(offset_minutes):
    body = '$offset=' + str(offset_minutes) + ';'
    body += r'''
    $zone=[TimeZoneInfo]::CreateCustomTimeZone('inert-fixture',[TimeSpan]::FromMinutes($offset),'inert-fixture','inert-fixture')
    $instant=[DateTimeOffset]::Parse('2026-10-08T12:34:56.123Z',[Globalization.CultureInfo]::InvariantCulture)
    $localized=[TimeZoneInfo]::ConvertTime($instant,$zone)
    $now=$localized.ToUniversalTime().ToUnixTimeMilliseconds()/1000.0
    $expected=$instant.ToUnixTimeMilliseconds()/1000.0
    $epoch=[DateTimeOffset]::FromUnixTimeSeconds(0)
    $localEpoch=[TimeZoneInfo]::ConvertTime($epoch,$zone).DateTime
    $legacy=($instant.UtcDateTime-$localEpoch).TotalSeconds
    $rows=@()
    foreach($age in @(-31.0,-30.0,-29.999,0.0,29.999,30.0,31.0,21600.0)){
      $checkedAt=$expected-$age
      $rows+=@{age=$age;fresh=([Math]::Abs($now-$checkedAt) -le 30);legacy_fresh=([Math]::Abs($legacy-$checkedAt) -le 30)}
    }
    @{same_instant=($now -eq $expected);offset=$localized.Offset.TotalMinutes;legacy_bias=$legacy-$expected;freshness=$rows}|ConvertTo-Json -Compress -Depth 5
    '''
    value = run(body)
    assert value['same_instant'] and value['offset'] == offset_minutes
    assert value['legacy_bias'] == pytest.approx(-offset_minutes*60, abs=0.001)
    assert [(row['age'], row['fresh']) for row in value['freshness']] == [
        (-31, False), (-30, True), (-29.999, True), (0, True),
        (29.999, True), (30, True), (31, False), (21600, False)]
    if offset_minutes:
        assert next(row for row in value['freshness'] if row['age'] == 0)['legacy_fresh'] is False


@pytest.mark.parametrize('date,local_offset', [('2026-01-08T12:34:56.123Z', -360),
                                             ('2026-07-08T12:34:56.123Z', -300)])
def test_central_zone_epoch_bias_uses_1970_offset_not_current_dst(date, local_offset):
    body = '$instant=[DateTimeOffset]::Parse(' + q(date) + ',[Globalization.CultureInfo]::InvariantCulture);'
    body += r'''
    $zone=[TimeZoneInfo]::FindSystemTimeZoneById('Central Standard Time')
    $epoch=[DateTimeOffset]::FromUnixTimeSeconds(0)
    $now=$instant.ToUnixTimeMilliseconds()/1000.0
    $localEpoch=[TimeZoneInfo]::ConvertTime($epoch,$zone).DateTime
    $legacy=($instant.UtcDateTime-$localEpoch).TotalSeconds
    @{local_offset_minutes=$zone.GetUtcOffset($instant).TotalMinutes;epoch_offset_minutes=$zone.GetUtcOffset($epoch).TotalMinutes;legacy_bias=$legacy-$now;correct=([TimeZoneInfo]::ConvertTime($instant,$zone).ToUnixTimeMilliseconds()/1000.0 -eq $now)}|ConvertTo-Json -Compress
    '''
    value = run(body)
    assert value == dict(local_offset_minutes=local_offset, epoch_offset_minutes=-360,
                         legacy_bias=21600, correct=True)


def test_actual_ps51_cast_is_local_and_bias_matches_host_epoch_offset():
    value = run(r'''
    $utc=[DateTime]::UtcNow;$epoch=[DateTime]'1970-01-01T00:00:00Z'
    $correct=([DateTimeOffset]$utc).ToUnixTimeMilliseconds()/1000.0
    $legacy=($utc-$epoch).TotalSeconds
    $offset=[TimeZoneInfo]::Local.GetUtcOffset([DateTimeOffset]::FromUnixTimeSeconds(0)).TotalSeconds
    @{kind=$epoch.Kind.ToString();bias=$legacy-$correct;epoch_offset_seconds=$offset;epoch_utc=$epoch.ToUniversalTime().ToString('o')}|ConvertTo-Json -Compress
    ''')
    assert value['kind'] == 'Local'
    assert value['bias'] == pytest.approx(-value['epoch_offset_seconds'], abs=0.002)
    assert value['epoch_utc'] == '1970-01-01T00:00:00.0000000Z'


def test_actual_native_filetime_agrees_with_utc_epoch_and_monotonic_read_only_evidence():
    value = run(r'''
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class CoChemUtcEpochReadOnlyFixture {
 [DllImport("kernel32.dll")] public static extern void GetSystemTimeAsFileTime(out long value);
 [DllImport("kernel32.dll")] public static extern ulong GetTickCount64();
 [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool QueryPerformanceCounter(out long value);
 [DllImport("kernel32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static extern bool QueryPerformanceFrequency(out long value);
}
'@
    $frequency=[long]0;$beforeCounter=[long]0;$afterCounter=[long]0;$beforeFile=[long]0;$afterFile=[long]0
    if(-not [CoChemUtcEpochReadOnlyFixture]::QueryPerformanceFrequency([ref]$frequency) -or $frequency -le 0){throw 'Native monotonic frequency unavailable'}
    $watch=[Diagnostics.Stopwatch]::StartNew()
    if(-not [CoChemUtcEpochReadOnlyFixture]::QueryPerformanceCounter([ref]$beforeCounter)){throw 'Native monotonic counter unavailable'}
    $beforeTicks=[CoChemUtcEpochReadOnlyFixture]::GetTickCount64()
    $beforeUtc=[DateTimeOffset]::UtcNow
    [CoChemUtcEpochReadOnlyFixture]::GetSystemTimeAsFileTime([ref]$beforeFile)
    [Threading.Thread]::Sleep(20)
    [CoChemUtcEpochReadOnlyFixture]::GetSystemTimeAsFileTime([ref]$afterFile)
    $afterUtc=[DateTimeOffset]::UtcNow
    $afterTicks=[CoChemUtcEpochReadOnlyFixture]::GetTickCount64()
    if(-not [CoChemUtcEpochReadOnlyFixture]::QueryPerformanceCounter([ref]$afterCounter)){throw 'Native monotonic counter unavailable'}
    $watch.Stop()
    $nativeBefore=($beforeFile-[long]116444736000000000)/10000000.0
    $nativeAfter=($afterFile-[long]116444736000000000)/10000000.0
    $dtoBefore=$beforeUtc.ToUnixTimeMilliseconds()/1000.0;$dtoAfter=$afterUtc.ToUnixTimeMilliseconds()/1000.0
    $utc=[DateTime]::UtcNow;$parsedEpoch=[DateTime]'1970-01-01T00:00:00Z'
    $correct=([DateTimeOffset]$utc).ToUnixTimeMilliseconds()/1000.0
    $legacy=($utc-$parsedEpoch).TotalSeconds
    $epochOffset=[TimeZoneInfo]::Local.GetUtcOffset([DateTimeOffset]::FromUnixTimeSeconds(0)).TotalSeconds
    [ordered]@{schema='cochem-windows-utc-epoch-read-only-evidence/1';powershell_version=$PSVersionTable.PSVersion.ToString();timezone_id=[TimeZoneInfo]::Local.Id;
      parsed_epoch_kind=$parsedEpoch.Kind.ToString();epoch_offset_seconds=$epochOffset;observed_legacy_bias_seconds=$legacy-$correct;
      native_filetime_before=$beforeFile;native_filetime_after=$afterFile;native_epoch_before=$nativeBefore;native_epoch_after=$nativeAfter;
      dto_epoch_before=$dtoBefore;dto_epoch_after=$dtoAfter;native_counter_before=$beforeCounter;native_counter_after=$afterCounter;native_counter_frequency=$frequency;
      monotonic_elapsed_seconds=($afterCounter-$beforeCounter)/[double]$frequency;stopwatch_elapsed_seconds=$watch.Elapsed.TotalSeconds;native_tick_elapsed_ms=$afterTicks-$beforeTicks;
      task_operations=0;provider_or_model_calls=0;system_clock_changed=$false;timezone_changed=$false;protected_state_modified=$false}|ConvertTo-Json -Compress -Depth 5
    ''')
    assert value['powershell_version'].startswith('5.1.')
    assert value['parsed_epoch_kind'] == 'Local'
    assert value['observed_legacy_bias_seconds'] == pytest.approx(-value['epoch_offset_seconds'], abs=0.002)
    assert value['native_filetime_after'] >= value['native_filetime_before'] > 116444736000000000
    assert value['native_counter_after'] > value['native_counter_before'] and value['native_counter_frequency'] > 0
    assert value['native_epoch_before'] == pytest.approx(value['dto_epoch_before'], abs=0.05)
    assert value['native_epoch_after'] == pytest.approx(value['dto_epoch_after'], abs=0.05)
    assert value['native_tick_elapsed_ms'] >= 0
    assert 0 < value['monotonic_elapsed_seconds'] <= value['stopwatch_elapsed_seconds'] + 0.01
    assert abs((value['native_epoch_after']-value['native_epoch_before'])-value['monotonic_elapsed_seconds']) <= 0.1
    assert value['task_operations'] == value['provider_or_model_calls'] == 0
    assert not value['system_clock_changed'] and not value['timezone_changed'] and not value['protected_state_modified']
    with (W / 'windows-utc-epoch-evidence-20261008.json').open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2);stream.write('\n')


def test_audited_active_downstream_sources_use_epoch_safe_or_no_epoch_calculation():
    assert HELD.read_text().count(OLD_EXPRESSION) == 1
    assert UTC_EXPRESSION in RESOURCE.read_text() and OLD_EXPRESSION not in RESOURCE.read_text()
    assert '1970-01-01' not in BATCH.read_text() and 'TotalSeconds' not in BATCH.read_text()
    dependencies = ['register-stopped-warden-r3.ps1', 'task-private-acl-v8.ps1',
        'check-worker-native-status-r3.ps1', 'protected-code-inspection-v4.ps1',
        'login-six-workers-status-first-r3-v1.ps1', 'check-oracle-native-acceptance-r3-v1.ps1',
        'install-independent-supervisor-staging-r3-v2.ps1']
    for name in dependencies:
        assert '1970-01-01' not in (W/name).read_text(), name
    for path in (W/'resource-observer-r3-v4').rglob('*.py'):
        assert '1970-01-01' not in path.read_text(), path
    assert 'checked_at=time.time()' in (W/'held-supervisor-observation-r3-v1.py').read_text()
    assert 'time.time()' in (W/'resource-observer-r3-v4/cochem_supervisor/resource_observation.py').read_text()


def test_timestamp_audit_changes_no_production_sources():
    pins = {
        HELD: 'f16cd5e39352d038b6ad81a25effc6a428308eee0106f88f3e42d20dcb939ad6',
        RESOURCE: 'ad64a859ad069a728cc57c324aedc5d48b44a072352e40b2bbed143d57e7c69f',
    }
    for path, pin in pins.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pin
