"""Render one additive launcher from the frozen prior launcher; no deployment."""
from pathlib import Path
import argparse
import hashlib
import re

W = Path(__file__).resolve().parent
OLD = 'run-reboot-setup-r3-v6.ps1'
OLD_HASH = 'dd74da76d96676bc0c1f3d013d7c11661bb04f760759af91c96ec04b72622041'
NEW = 'run-pending-warden-setup-r3-v7.ps1'


def checked(name, pin):
    raw = (W / name).read_bytes()
    if not re.fullmatch('[a-f0-9]{64}', pin) or hashlib.sha256(raw).hexdigest() != pin:
        raise ValueError('Reviewed launcher source changed: ' + name)
    return raw.decode('utf-8-sig')


def once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Expected one reviewed launcher replacement')
    return text.replace(before, after)


def render(recovery_hash, batch_hash):
    checked('resume-pending-warden-r3-v7.ps1', recovery_hash)
    checked('run-post-commissioning-setup-r3-v4.ps1', batch_hash)
    text = checked(OLD, OLD_HASH)
    text = once(text, "One attended invocation of the frozen commissioning and monitoring scripts.",
                "One invocation of pending task recovery and monitoring; saved logins are reused.")
    text = text.replace("'commissioning'", "'pending_registration_recovery'")
    text = once(text, "  if($phase -ceq 'pending_registration_recovery' -and $code -is [int] -and $code -eq 20){return 20}\n", '')
    text = once(text, 'run-pipeline-commissioning-r3-v6.ps1', 'resume-pending-warden-r3-v7.ps1')
    text = once(text, '42cec356b787f3edd1529f8f9c81859459f932b0630494636477a0b2e1c7e8f7', recovery_hash)
    text = once(text, 'run-post-commissioning-setup-r3-v3.ps1', 'run-post-commissioning-setup-r3-v4.ps1')
    text = once(text, '31ad291744eda4a2a453912b64c1b969c943b3fee70b387a503def6d6cd6589d', batch_hash)
    pause = """ if($outcome -eq 20){
  [ordered]@{schema='cochem-reboot-setup-launcher-result/1';status='AUTHENTICATION_PAUSED_BEFORE_LOGIN';monitoring_setup_started=$false;completed_signins_preserved=$true;automatic_retry=$false}|ConvertTo-Json -Compress
  exit 20
 }
"""
    text = once(text, pause, '')
    text = once(text, "status='COMMISSIONING_AND_MONITORING_SETUP_COMPLETED_ACCEPTANCE_PENDING';model_jobs_submitted=0",
                "status='PENDING_REGISTRATION_AND_MONITORING_SETUP_COMPLETED_ACCEPTANCE_PENDING';login_commands_executed=0;model_jobs_submitted=0")
    return text.encode('utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--recovery-sha256', required=True)
    parser.add_argument('--batch-sha256', required=True)
    args = parser.parse_args()
    raw = render(args.recovery_sha256, args.batch_sha256)
    with (W / NEW).open('xb') as stream:
        stream.write(raw)
    print(NEW, hashlib.sha256(raw).hexdigest())
