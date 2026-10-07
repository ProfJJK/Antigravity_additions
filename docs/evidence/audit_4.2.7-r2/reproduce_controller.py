"""Read-only application audit. Physical SQLite/Git/pytest; fixture receipts are NOT native inference."""
import sys, json, subprocess, tempfile, hashlib, shutil, sqlite3
from pathlib import Path
ROOT=Path('/workspace/Antigravity_additions')
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from cochem_pipeline.store import JobStore,output_digest,artifact_digest
from pipeline_tests.test_store import finish,manifest,chapter
from cochem_pipeline.containers import parse_junit,junit_cases
from pipeline_tests.test_coding_workflow import CodingFixture
from cochem_pipeline.coding import digest,manifest as source_manifest,observed_changes
from cochem_pipeline.operator_views import acceptance_dashboard
from cochem_pipeline.upgrade_preview import _captured_database
work=Path(tempfile.mkdtemp(prefix='cochem427-audit-repros-'))
result={'commit':'af4e335945fbd5d25198166e16ca32323df65bcb','work':str(work),'scope':'Physical SQLite/Git/pytest, controller protocol fixtures, Git source inspection; no native models or Windows execution','checks':{}}
# Manifest and chapter completions use existing explicitly labelled storage-contract receipts.
s=JobStore(work/'planning.db');wf=s.submit('Produce SRS and WBS',['REQ-1'],1)
n=s.claim('manifest');m=manifest(1);finish(s,n,m)
n=s.claim('chapter');output=chapter(n);output['wbs_tasks_defined']=[{'task_id':'unique-wbs','description':'UNIQUE_WBS_MUST_REACH_SYNTHESIS'}];finish(s,n,output)
gather=s.claim('synthesis');payload=gather['payload']
result['checks']['planning_wbs']={'manifest_without_wbs_accepted':s.get(wf['jobs'][1]['job_id'])['status'] if wf['jobs'][1]['kind']=='MANIFEST_GENERATOR' else 'COMPLETED','chapter_structured_wbs_saved':s.get(n['job_id'])['output']['wbs_tasks_defined'],'gather_contains_structured_wbs':any('wbs_tasks_defined' in item for item in payload['chapters']),'unique_wbs_present_anywhere_in_gather_payload':'UNIQUE_WBS_MUST_REACH_SYNTHESIS' in json.dumps(payload),'coverage_binds_full_output_hash':payload['chapter_hashes'][n['chapter_id']]==output_digest(output),'coverage_binds_artifact_text_hash':payload['chapter_hashes'][n['chapter_id']]==artifact_digest(output['artifact_text'])}
assert not result['checks']['planning_wbs']['unique_wbs_present_anywhere_in_gather_payload']
# Author real planned test bytes whose call fails with RuntimeError BEFORE the assertion.
cdir=work/'coding';cdir.mkdir();c=CodingFixture(cdir);c.plan();c.initial_research();node=c.claim('CODE_TEST_AUTHOR')
before=c.store.coding_files(c.state()['current_snapshot']);name=node['payload']['test_cases'][0]['name'];target=node['payload']['active_leaf']['file_targets'][0]
test_source=('from pathlib import Path\nfrom runpy import run_path\ndef '+name+'():\n    raise RuntimeError("audit: infrastructure failure before requirement assertion")\n    source = Path(__file__).parents[1] / "'+target+'"\n    assert run_path(str(source))["ANSWER"] == 42\n').encode()
after=dict(before,**{f'tests/{name}.py':test_source})
evidence={'changes':observed_changes(before,after,c.snapshot.files,c.project,tests_only=True),'snapshot_sha256':digest(source_manifest(after))}
c.complete_native(node,{'summary':'Explicit audit protocol fixture','requirements_traced':['REQ-1']},evidence,after)
node=c.claim('CODE_TEST');execution=work/'physical-pytest';execution.mkdir()
for path,raw in after.items():
 p=execution/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
report=work/'runtime-error.xml'
proc=subprocess.run([sys.executable,'-m','pytest','-q','tests','--junitxml='+str(report)],cwd=execution,capture_output=True,text=True)
raw=report.read_bytes();parsed=parse_junit(raw);cases=junit_cases(raw)
ev=c.test_evidence(node,passed=False);ev['commands'][0].update(exit_code=proc.returncode,junit=parsed,junit_cases=cases,stdout=proc.stdout,stderr=proc.stderr)
c.complete_controller(node,{'passed':False,'source_snapshot_sha256':node['payload']['snapshot_sha256']},ev)
result['checks']['nonassertion_red']={'pytest_exit_code':proc.returncode,'physical_junit':str(report),'parsed_junit':parsed,'cases':cases,'state_after_completion':c.state()['status'],'expected':'Refuse assertion-RED because planned test raised RuntimeError before its assertion','native_receipt_scope':'Explicit controller storage fixture; physical pytest XML is real'}
assert c.state()['status']=='EDITING'
# Demonstrate dashboard is bound to evidence files/spec bytes but not actual current source.
copy=work/'dashboard';copy.mkdir();shutil.copy2(ROOT/'4.2.7_SRS.md',copy/'4.2.7_SRS.md');shutil.copytree(ROOT/'docs/evidence',copy/'docs/evidence')
for name in ('requirements_4.2.7.json','acceptance_4.2.7.json'):shutil.copy2(ROOT/'docs'/name,copy/'docs'/name)
shutil.copytree(ROOT/'src',copy/'src',ignore=shutil.ignore_patterns('__pycache__'))
a=acceptance_dashboard(copy);(copy/'src/cochem_pipeline/store.py').write_text('raise RuntimeError("audit-only broken deployment")\n');b=acceptance_dashboard(copy)
result['checks']['dashboard_stale_source']={'before':a['counts'],'after':b['counts'],'source_file_changed':'src/cochem_pipeline/store.py','all_requirement_statuses_unchanged':[x['status'] for x in a['requirements']]==[x['status'] for x in b['requirements']]}
# mode=ro can create WAL/SHM even though no main database write occurs.
previewdir=work/'preview';previewdir.mkdir();board=JobStore(previewdir/'board.db');board.submit('Preview existing job',['R'],1)
prior={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in previewdir.iterdir()};_captured_database(board.path)
post={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in previewdir.iterdir()}
result['checks']['readonly_preview']={'files_before':sorted(prior),'files_after':sorted(post),'created_files':sorted(set(post)-set(prior)),'main_database_unchanged':prior['board.db']==post['board.db']}
# Actual argument parser rejects Agy for the documented main worker login command.
p=subprocess.run([sys.executable,'-m','cochem_pipeline.windows','login','--provider','gemini'],cwd=ROOT,capture_output=True,text=True)
result['checks']['worker_login']={'exit_code':p.returncode,'rejects_gemini':'invalid choice' in p.stderr and 'gemini' in p.stderr,'message':p.stderr.splitlines()[-1]}
Path('/tmp/audit427-reproductions.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
