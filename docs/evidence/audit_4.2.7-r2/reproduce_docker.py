"""Actual local Docker and SQLite audit; only this script's owned containers are modified/removed."""
import sys, tempfile, shutil, time, threading, json
from pathlib import Path
sys.path.insert(0,'/workspace/Antigravity_additions/src')
from cochem_pipeline.containers import DockerRunner, ContainerError
from cochem_pipeline.container_policy import DockerPolicy
from cochem_pipeline.store import JobStore
from cochem_pipeline.admission import JointAdmission
image='sha256:a3d8c8a1b836c46ffda919139a3d42b7214fe1e8b6603d584bb58b8aa4a589f5'
root=Path(tempfile.mkdtemp(prefix='cochem427-audit-docker-'))
policy=DockerPolicy.from_dict({'enabled':True,'executable':shutil.which('docker'),'endpoint':'unix:///var/run/docker.sock','image':image,'allowed_images':[image],'max_containers':4,'warm_pool_size':2,'commands':[{'name':'unit','argv':['python','-m','pytest','tests']}]})
r=DockerRunner(policy,root/'registry');r.set_capacity(4);report={'scope':'Real Linux Docker and SQLite; no Windows/native inference','image':image,'work':str(root)}
try:
 r.prepare_pool(target=1)
 s=JobStore(root/'jobs.db');s.submit('Create one specification chapter',['REQ-1'],1)
 a=JointAdmission(s,r,capacity=4,max_capacity=4)
 outcome={};start=time.monotonic()
 def maintain():
  try:outcome['maintenance']=a.maintenance()
  except Exception as exc:outcome['error']=str(exc)
 t=threading.Thread(target=maintain);t.start();found=None
 deadline=time.monotonic()+30
 while t.is_alive() and time.monotonic()<deadline:
  current=r.census()
  if current['warm']>=1 and current['preparing']>=1:
   claim=a.claim('audit-owner',worker_slot='slot1',requires_cleanup=False)
   found={'warm':current['warm'],'preparing':current['preparing'],'capacity':4,'native_active':len(s.active_jobs()),'claim_during_preparation':claim,'lock_held':a.lock.locked()};break
  time.sleep(.005)
 t.join(timeout=60);assert not t.is_alive()
 after=a.claim('audit-owner',worker_slot='slot1',requires_cleanup=False)
 report['admission_during_replenishment']={'observed':found,'claim_after_replenishment':None if after is None else after[0]['kind'],'maintenance_seconds':time.monotonic()-start,'error':outcome.get('error')}
 assert found is not None and found['claim_during_preparation'] is None and after is not None
 n=after[0];s.fail(n['job_id'],n['attempt_id'],n['fencing_token'],'Audit issued no native process',retry=False)
 record=next(x for x in r.census()['containers'] if x['status']=='WARM')
 updated=r._call(['update','--memory','512m','--memory-swap','512m',record['container_id']],timeout=30)
 assert updated.returncode==0,updated.stderr
 restarted=DockerRunner(policy,root/'registry');reaped=restarted.reap_orphans(active_attempt_ids=set());after_restart=restarted.census();physical=restarted._inspect_owned(record)
 invalid=False
 try:restarted._verify_limits(physical)
 except ContainerError:invalid=True
 report['warm_policy_restart']={'changed_container_id':record['container_id'],'actual_memory_mb':physical['HostConfig']['Memory']/1048576,'expected_memory_mb':policy.memory_mb,'would_fail_limit_verification':invalid,'retained_as_warm':any(x['lease']==record['lease'] and x['status']=='WARM' for x in after_restart['containers']),'reap_result':reaped,'scope_note':'Execution checks limits again before source injection; this is an adoption/readiness gap, not proof of sandbox escape.'}
 assert invalid and report['warm_policy_restart']['retained_as_warm']
finally:
 report['cleanup']=r.reap_orphans(active_attempt_ids=set(),include_warm=True,now=time.time()+86400)
 report['remaining_owned']=r.census()['owned']
Path('/tmp/audit427-docker-reproductions.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
