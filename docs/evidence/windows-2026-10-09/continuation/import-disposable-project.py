"""Fixed disposable bare Git import; invoked only by the reviewed private wrapper.

No checkout, model, network, authentication, repair, database or task operation.
The wrapper creates the fresh private target, then passes its exact file identity.
Any partial repository is preserved and must never be retried automatically.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

PROJECT = Path(r'C:\ProgramData\CoChemPipeline427\projects\windows-acceptance')
SESSION = Path(r'C:\Program Files\CoChem\ProjectImport4.2.7-windows-20261007-r3')
GIT = Path(r'C:\Program Files\Git\mingw64\libexec\git-core\git.exe')
BUNDLE_HASH = '08b7006e1eaf4a69503c7dbefd4e0e340734fcff9d6cb011a509a63cff0dff87'
BASELINE = 'c52a3a97eb085e6bafbbd14bd6a75f3274288530'
TREE = '0e7fe3edd935e4a839d17cb99b30b08db18d8ae9'
BRANCH = 'pipeline/accepted'
REF = 'refs/heads/' + BRANCH
FILES = {
    'README.md': ('100644','415aade8c462f8cd7d1b5530a3e587b7bb172f0f',96,'c33ac1c425ce64bf18e4d027f9b998eb8782f797b2103ad24b8c47d70751a59a'),
    'src/calculator.py': ('100644','ab181a8c44960bda8f2565986dadce2fd8dc72f4',38,'89fb6a0b833a9db8edfc28b3c34b2bc1509abde1318e93116dd9e2fe2cf2061f'),
    'tests/.gitkeep': ('100644','e69de29bb2d1d6434b8b29ae775ad8c2e48c5391',0,'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
}


class ImportHeld(ValueError):
    pass


def file_id(path):
    info = Path(path).lstat()
    if info.st_file_attributes & 0x400:
        raise ImportHeld('reparse_refused')
    return f'{info.st_dev}:{info.st_ino}'


def verified_pack(bundle):
    if len(bundle) != 622 or hashlib.sha256(bundle).hexdigest() != BUNDLE_HASH:
        raise ImportHeld('bundle_pin')
    header, pack = bundle.split(b'\n\n',1)
    if header != ('# v2 git bundle\n'+BASELINE+' '+REF).encode():
        raise ImportHeld('bundle_header')
    if pack[:4] != b'PACK' or int.from_bytes(pack[4:8],'big') != 2 or int.from_bytes(pack[8:12],'big') != 7:
        raise ImportHeld('bundle_pack_shape')
    if hashlib.sha1(pack[:-20]).digest() != pack[-20:]:
        raise ImportHeld('bundle_pack_checksum')
    return pack


def child_environment(temporary):
    # Construct, rather than filter, the environment. No inherited Git config,
    # SSH/editor/askpass settings, profile path, user PATH or credentials.
    return {
        'SystemRoot':r'C:\Windows','WINDIR':r'C:\Windows','SystemDrive':'C:',
        'PATH':r'C:\Windows\System32','TEMP':str(temporary),'TMP':str(temporary),
        'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_SYSTEM':'NUL','GIT_CONFIG_GLOBAL':'NUL',
        'GIT_CONFIG_COUNT':'0','GIT_TERMINAL_PROMPT':'0','GIT_NO_LAZY_FETCH':'1',
        'GIT_NO_REPLACE_OBJECTS':'1','GIT_OPTIONAL_LOCKS':'0','GIT_ATTR_NOSYSTEM':'1',
        'GIT_EXEC_PATH':str(GIT.parent),'GIT_PAGER':'','LC_ALL':'C',
        'GIT_AUTHOR_NAME':'CoChem Acceptance','GIT_AUTHOR_EMAIL':'acceptance@localhost',
        'GIT_COMMITTER_NAME':'CoChem Acceptance','GIT_COMMITTER_EMAIL':'acceptance@localhost',
    }


class Plumbing:
    def __init__(self, repository, temporary, *, git=GIT):
        self.repository, self.temporary, self.git = Path(repository), Path(temporary), Path(git)
        self.deadline=time.monotonic()+120
        self.calls=0

    def run(self, *args, data=None):
        remaining=self.deadline-time.monotonic()
        if remaining <= 0:
            raise ImportHeld('git_deadline')
        command=[str(self.git),'--no-replace-objects','-C',str(self.repository),
                 '-c','safe.directory='+str(self.repository),
                 '-c','core.hooksPath=NUL','-c','core.fsmonitor=false',
                 '-c','core.attributesFile=NUL','-c','core.quotePath=false',
                 '-c','core.pager=','-c','protocol.allow=never',
                 '-c','submodule.recurse=false','-c','maintenance.auto=false',
                 '-c','gc.auto=0','-c','commit.gpgsign=false','-c','tag.gpgsign=false',
                 '-c','pack.threads=1','-c','core.useReplaceRefs=false',
                 '-c','core.logAllRefUpdates=false','-c','core.autocrlf=false',*args]
        try:
            result=subprocess.run(command,input=data,capture_output=True,
                                  env=child_environment(self.temporary),timeout=min(15,remaining),check=False)
        except subprocess.TimeoutExpired:
            # subprocess.run kills and reaps the direct native Git process.
            # Fixed local builtins only; this is not arbitrary helper execution.
            raise ImportHeld('git_timeout_preserve_partial') from None
        self.calls+=1
        if len(result.stdout)>65536 or len(result.stderr)>65536:
            raise ImportHeld('git_output_bound')
        if result.returncode:
            # Never publish raw child output or inherited/project data.
            raise ImportHeld('git_nonzero_'+str(result.returncode))
        return result.stdout


def validate_empty_target(repository, expected_id):
    root=Path(repository)
    if not root.is_dir() or file_id(root) != expected_id:
        raise ImportHeld('fresh_target_identity')
    if next(root.iterdir(),None) is not None:
        raise ImportHeld('fresh_target_not_empty')


def verify_repository(git):
    root=git.repository
    if git.run('rev-parse','--is-bare-repository').strip()!=b'true':
        raise ImportHeld('not_bare')
    if git.run('rev-parse','--show-object-format').strip()!=b'sha1':
        raise ImportHeld('object_format')
    if Path(os.fsdecode(git.run('rev-parse','--absolute-git-dir')).strip())!=root:
        raise ImportHeld('repository_path')
    refs=git.run('for-each-ref','--format=%(refname) %(objectname)').decode().splitlines()
    if refs != [REF+' '+BASELINE] or git.run('symbolic-ref','HEAD').strip()!=REF.encode():
        raise ImportHeld('reference_identity')
    if git.run('rev-parse',REF+'^{commit}').strip()!=BASELINE.encode() or git.run('rev-parse',REF+'^{tree}').strip()!=TREE.encode():
        raise ImportHeld('baseline_identity')
    listing=git.run('ls-tree','-r','-l','-z','--full-tree',BASELINE)
    observed={}
    for record in listing.split(b'\0'):
        if not record:continue
        header,path=record.split(b'\t',1);mode,kind,oid,length=header.split()
        if kind!=b'blob':raise ImportHeld('non_blob_tree')
        observed[path.decode('utf-8','strict')]=(mode.decode(),oid.decode(),int(length))
    if observed != {name:expected[:3] for name,expected in FILES.items()}:
        raise ImportHeld('tree_files')
    for name,(_,oid,length,digest) in FILES.items():
        raw=git.run('cat-file','blob',oid)
        if len(raw)!=length or hashlib.sha256(raw).hexdigest()!=digest:
            raise ImportHeld('blob_bytes')
    objects=git.run('cat-file','--batch-all-objects','--batch-check=%(objectname) %(objecttype) %(objectsize)').decode().splitlines()
    if len(objects)!=7 or len({line.split()[0] for line in objects})!=7:
        raise ImportHeld('object_count')
    expected_types={'commit':1,'tree':3,'blob':3}
    for kind,count in expected_types.items():
        if sum(line.split()[1]==kind for line in objects)!=count:raise ImportHeld('object_types')
    # Enumerate bounded metadata, not working-tree files or project commands.
    entries=[]
    for entry in root.rglob('*'):
        info=entry.lstat()
        if len(entries)>=128 or info.st_file_attributes&0x400 or (stat.S_ISREG(info.st_mode) and info.st_nlink!=1):
            raise ImportHeld('repository_entry_custody')
        relative=entry.relative_to(root).as_posix();entries.append(relative)
        if relative in ('config.worktree','commondir','shallow','packed-refs','info/grafts','objects/info/alternates','objects/info/http-alternates') or relative.startswith(('worktrees/','hooks/','modules/','refs/replace/','refs/remotes/')):
            raise ImportHeld('unexpected_repository_control')
    # With empty templates the local config is a fixed bare-init record only.
    config=git.run('config','--local','--list','-z')
    pairs=dict(item.split(b'\n',1) for item in config.split(b'\0') if item)
    if pairs!={b'core.repositoryformatversion':b'0',b'core.filemode':b'false',b'core.bare':b'true',b'core.ignorecase':b'true',b'core.symlinks':b'false'}:
        raise ImportHeld('unexpected_local_config')
    return {'bare':True,'branch':BRANCH,'baseline_commit':BASELINE,'baseline_tree':TREE,
            'files':3,'objects':7,'repository_metadata_entries':len(entries),'git_commands':git.calls}


def import_repository(repository,bundle,empty_template,temporary,expected_id,*,git_path=GIT):
    # All content/shape/identity checks precede the first Git mutation.
    pack=verified_pack(bundle)
    validate_empty_target(repository,expected_id)
    if not Path(empty_template).is_dir() or next(Path(empty_template).iterdir(),None) is not None:
        raise ImportHeld('template_not_empty')
    git=Plumbing(repository,temporary,git=git_path)
    git.run('init','--bare','--object-format=sha1','--initial-branch='+BRANCH,'--template='+str(empty_template),str(repository))
    # Explicitly fix the sole init setting that depends on Windows token
    # symlink privileges. Never rely on an ordinary-user fixture's default.
    git.run('config','--local','core.symlinks','false')
    git.run('index-pack','--stdin','--strict',data=pack)
    git.run('update-ref',REF,BASELINE,'0'*40)
    result=verify_repository(git)
    if file_id(repository)!=expected_id:raise ImportHeld('target_identity_changed')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--nonce',required=True)
    parser.add_argument('--root-id',required=True)
    args=parser.parse_args()
    if not args.apply or not re.fullmatch('[a-f0-9]{32}',args.nonce) or not re.fullmatch(r'\d+:\d+',args.root_id):
        raise ImportHeld('explicit_fixed_invocation_required')
    if os.name!='nt' or not ctypes.windll.shell32.IsUserAnAdmin() or Path(__file__).parent!=SESSION:
        raise ImportHeld('protected_administrator_context_required')
    report={'schema':'cochem-private-project-import/1','status':'PROJECT_IMPORT_HELD','nonce':args.nonce,
            'helper_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'target':str(PROJECT),'root_id':args.root_id,'bundle_sha256':BUNDLE_HASH,
            'model_jobs':0,'checkout_performed':False,'network_protocols_allowed':False,
            'started_at_unix_ms':int(time.time()*1000)}
    try:
        report.update(import_repository(PROJECT,(SESSION/'acceptance.bundle').read_bytes(),SESSION/'empty-template',SESSION/'temporary',args.root_id))
        report['status']='PRIVATE_BARE_PROJECT_VERIFIED'
    except Exception as error:
        report['failure']={'error_type':type(error).__name__,'code':str(error) if isinstance(error,ImportHeld) else 'unexpected_exception','winerror':getattr(error,'winerror',None)}
    report['finished_at_unix_ms']=int(time.time()*1000)
    with (SESSION/'project-import.json').open('x',encoding='utf-8') as stream:
        json.dump(report,stream,indent=2,sort_keys=True);stream.flush();os.fsync(stream.fileno())
    return 0 if report['status']=='PRIVATE_BARE_PROJECT_VERIFIED' else 2


if __name__=='__main__':
    try:
        exit_code=main()
    except Exception as error:
        # Bootstrap may fail before the private session is trusted. Do not
        # create any receipt there or expose a traceback/raw child output.
        import sys
        print(json.dumps({'schema':'cochem-private-project-bootstrap/1',
                          'status':'HELD','error_type':type(error).__name__,
                          'code':'protected_bootstrap_failure'}),file=sys.stderr)
        exit_code=2
    raise SystemExit(exit_code)
