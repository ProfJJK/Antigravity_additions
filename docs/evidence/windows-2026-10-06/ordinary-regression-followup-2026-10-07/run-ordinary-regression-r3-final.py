"""Run current deployment regression suites without live/native opt-ins."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

REPO=Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
OUT=Path(__file__).parent/'ordinary-regression-20261007-r3-final'
PYTHON=Path(r'C:\Users\ansac\AppData\Local\CoChem\staging\windows-427-20261006\venv\Scripts\python.exe')

def inventory():
    files=[REPO/'conftest.py',REPO/'pyproject.toml',REPO/'uv.lock']
    for name in ('src','pipeline_tests','mcp_tests','supervisor_tests','scripts'):
        files.extend(p for p in (REPO/name).rglob('*') if p.is_file() and p.suffix in ('.py','.ps1','.md','.json','.xml') and '__pycache__' not in p.parts)
    return {p.relative_to(REPO).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}

def main():
    OUT.mkdir(exist_ok=False)
    before=inventory()
    (OUT/'source-before.json').write_text(json.dumps(before,sort_keys=True,indent=2))
    environment=dict(os.environ)
    # Explicitly prevent inherited privileged/Docker/native activation fixtures.
    for key in ('COCHEM_SYSTEM_TESTS','COCHEM_RUN_NATIVE_RESOURCE_TESTS','COCHEM_TEST_DOCKER_IMAGE',
        'COCHEM_WINDOWS_TEST_LAYOUT','COCHEM_WINDOWS_DEPLOYMENT_CONFIG','COCHEM_WINDOWS_RAMDISK_CONFIG',
        'COCHEM_TEST_DISPOSABLE_RAM_CLEANUP','COCHEM_SUPERVISOR_WINDOWS_CONFIG','COCHEM_SUPERVISOR_WINDOWS_TASK_CONTROL',
        'COCHEM_TEST_CLAUDE_JS'):
        environment.pop(key,None)
    environment['PYTHONPATH']=str(REPO/'src')
    environment['PYTHONDONTWRITEBYTECODE']='1'
    environment['COCHEM_WINDOWS_TEST_GIT_EXECUTABLE']=r'C:\Program Files\Git\cmd\git.exe'
    argv=[str(PYTHON),'-m','pytest','pipeline_tests','mcp_tests','supervisor_tests','-q',
        '--deselect=pipeline_tests/test_inference_policy.py::test_actual_codex_offline_features_and_mcp_disable_readback',
        '--deselect=pipeline_tests/test_inference_policy.py::test_actual_claude_help_confirms_empty_tools_and_subscription_compatible_flags',
        '--junitxml='+str(OUT/'tests.xml')]
    started=time.time()
    with (OUT/'pytest.log').open('xb') as log:
        result=subprocess.run(argv,cwd=REPO,env=environment,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT)
    after=inventory()
    tree=ET.parse(OUT/'tests.xml');suites=list(tree.getroot().iter('testsuite'))
    counts={name:sum(int(s.get(name,'0')) for s in suites) for name in ('tests','failures','errors','skipped')}
    report={'schema':'cochem-ordinary-windows-regression/1','python':sys.version.split()[0],
        'exit_code':result.returncode,'seconds':round(time.time()-started,3),'counts':counts,
        'source_files':len(before),'source_unchanged':before==after,
        'source_before_sha256':hashlib.sha256((OUT/'source-before.json').read_bytes()).hexdigest(),
        'xml_sha256':hashlib.sha256((OUT/'tests.xml').read_bytes()).hexdigest(),
        'scope':'Ordinary Windows process, disposable fixtures; privileged/Docker/model opt-ins absent; two native CLI capability calls deselected',
        'linux_evidence_used':False,'privileged_acceptance_claimed':False,'pipeline_activation_claimed':False}
    (OUT/'summary.json').write_text(json.dumps(report,sort_keys=True,indent=2))
    print(json.dumps(report,indent=2));return result.returncode if before==after else 3

if __name__=='__main__':raise SystemExit(main())
