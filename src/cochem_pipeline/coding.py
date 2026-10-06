"""Registered project policies and controller-owned coding evidence.

Models edit a disposable RAM working copy. Only this module observes and hashes
the resulting files; generated descriptions of changes are never evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ast
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import os
import stat


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def safe_path(value: str) -> str:
    if (not isinstance(value, str) or not value or '\\' in value or '\x00' in value
            or ':' in value or value.startswith('/') or
            any(part in ('', '.', '..', '.git') for part in value.split('/'))):
        raise ValueError('Project paths must be plain relative paths without Git metadata')
    from .coding_git import validate_source_path
    return validate_source_path(str(PurePosixPath(value)))


def within(path: str, roots: tuple[str, ...] | list[str]) -> bool:
    return any(path == root or path.startswith(root + '/') for root in roots)


@dataclass(frozen=True)
class CodingProject:
    project_id: str
    repository: Path
    branch: str
    allowed_paths: tuple[str, ...]
    test_paths: tuple[str, ...] = ('tests',)
    auto_integrate: bool = False
    test_strategy: str = 'red_green'
    planning: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, project_id, raw):
        if not isinstance(project_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', project_id):
            raise ValueError('Invalid coding project identifier')
        if not isinstance(raw, dict) or set(raw) - {'repository', 'branch', 'allowed_paths', 'test_paths', 'auto_integrate', 'test_strategy', 'planning'}:
            raise ValueError('Invalid registered coding project fields')
        repository = raw.get('repository')
        if not isinstance(repository, str) or not repository or '\x00' in repository:
            raise ValueError('Coding project repository must be an absolute path')
        path = Path(repository).expanduser()
        # Windows configurations can be inspected on Linux without changing their paths.
        if not path.is_absolute() and not re.match(r'^[A-Za-z]:[\\/]', repository):
            raise ValueError('Coding project repository must be an absolute path')
        branch = raw.get('branch')
        if (not isinstance(branch, str) or not branch or branch.startswith(('-', '/', '.'))
                or any(x in branch for x in ('..', '@{', '\\', ' ', '\x00', '~', '^', ':', '?', '*', '['))
                or branch.endswith(('/', '.', '.lock'))):
            raise ValueError('Coding project requires a valid branch name')
        def roots(key, default=None):
            value = raw.get(key, default)
            if not isinstance(value, list) or not value or len(value) != len(set(value)):
                raise ValueError(key + ' must be a nonempty list of distinct relative paths')
            return tuple(safe_path(item) for item in value)
        allowed, tests = roots('allowed_paths'), roots('test_paths', ['tests'])
        if any(within(test, allowed) or within(root, tests) for test in tests for root in allowed):
            raise ValueError('Source allowed_paths and protected test_paths must be disjoint')
        auto = raw.get('auto_integrate', False)
        if type(auto) is not bool:
            raise ValueError('auto_integrate must be boolean')
        strategy = raw.get('test_strategy', 'red_green')
        if strategy != 'red_green':
            raise ValueError('Coding requires red_green; preserve_behavior cannot bypass the mandatory failing-first gate')
        from .planning_governance import normalize_policy
        return cls(project_id, path, branch, allowed, tests, auto, strategy,normalize_policy(raw.get('planning')))

    def as_dict(self):
        return {'repository': str(self.repository), 'branch': self.branch,
                'allowed_paths': list(self.allowed_paths), 'test_paths': list(self.test_paths),
                'auto_integrate': self.auto_integrate, 'test_strategy': self.test_strategy,
                'planning':json.loads(json.dumps(self.planning))}


def validate_coding_projects(raw) -> dict[str, CodingProject]:
    if not isinstance(raw, dict):
        raise ValueError('coding_projects must be an object of registered project policies')
    return {key: CodingProject.from_dict(key, value) for key, value in raw.items()}


def manifest(files: dict[str, bytes]) -> dict[str, str]:
    return {safe_path(name): hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}


def bind_test_identities(files, selected_paths, test_cases):
    """Bind planned pytest names to exact sealed source definitions.

    Only top-level functions and directly defined class methods have a static
    identity here. Imported aliases, inherited/generated tests, and ambiguous
    duplicate names require a different reviewed test contract; guessing their
    identity could let an unrelated collected test satisfy the regression gate.
    The container fixes pytest rootdir to its source root before reading JUnit.
    """
    if (not isinstance(selected_paths,list) or not selected_paths
            or any(not isinstance(path,str) for path in selected_paths)
            or len(selected_paths)!=len(set(selected_paths))):
        raise ValueError('Sealed tests require distinct selected Python source files')
    names={item['name'] for item in test_cases}
    found={name:[] for name in names}
    for path in selected_paths:
        safe_path(path)
        if path not in files or not path.endswith('.py'):
            raise ValueError('Static pytest identity requires an actual selected Python test file')
        try:
            module=ast.parse(files[path],filename=path)
        except (SyntaxError,ValueError,UnicodeError) as exc:
            raise ValueError('Selected test source has no valid static Python definition') from exc
        module_name=path[:-3].replace('/','.')
        for definition in module.body:
            definitions=definition.body if isinstance(definition,ast.ClassDef) else [definition]
            class_name=module_name+'.'+definition.name if isinstance(definition,ast.ClassDef) else module_name
            for function in definitions:
                if isinstance(function,(ast.FunctionDef,ast.AsyncFunctionDef)) and function.name in names:
                    found[function.name].append({'path':path,'class_name':class_name,'name':function.name,
                        'file_sha256':hashlib.sha256(files[path]).hexdigest()})
    if not names or any(len(identities)!=1 for identities in found.values()):
        raise ValueError('Every planned test needs one unambiguous static definition in its sealed selected files; dynamic or inherited tests are unsupported')
    return {name:identities[0] for name,identities in sorted(found.items())}


def validate_project_files(files):
    # Refuse credential/cache material before it reaches any native model, not
    # merely when the later container archive is built.
    from .containers import _SECRET_NAMES, _EXCLUDES
    from .coding_git import _validate_files
    _validate_files(files)
    for name in files:
        safe_path(name)
        if any(part.casefold() in _SECRET_NAMES or part.casefold() in _EXCLUDES or part.casefold().startswith('.env.')
               or part.casefold().endswith(('.pem','.key')) for part in name.split('/')):
            raise ValueError('Credential, VCS, and cache material is forbidden in coding input: '+name)


def source_context(files, payload, project, *, max_bytes=98304):
    """Supply actual bounded source bytes to inference-only native processes.

    Native model tools cannot execute repository programs. The packet identifies
    omitted files explicitly and includes the entire active N=1 target, so a
    complete-file proposal cannot silently overwrite unseen target content.
    """
    targets=payload.get('active_leaf',{}).get('file_targets',[])
    names=sorted(files,key=lambda name:(0 if name in targets else
        1 if name.lower().endswith(('.md','.rst')) else 2 if within(name,project.test_paths) else 3,name))
    selected, total = {}, 0
    for name in names:
        try:
            content=files[name].decode('utf-8')
        except UnicodeDecodeError:
            if name in targets:
                raise ValueError('The planned implementation target is not UTF-8 text')
            continue
        size=len(files[name])+len(name.encode())+128
        if total+size>max_bytes:
            if name in targets:
                from .failures import ProviderFailure
                raise ProviderFailure('configuration')
            continue
        selected[name]={'sha256':hashlib.sha256(files[name]).hexdigest(),'text':content}
        total+=size
    inventory=[{'path':name,'sha256':hashlib.sha256(files[name]).hexdigest()} for name in sorted(files)[:500]]
    return {'files':selected,'inventory':inventory,'inventory_omitted':max(0,len(files)-500),
            'content_omitted_count':len(files)-len(selected),'source_snapshot_sha256':digest(manifest(files))}


def materialize(root: Path, files: dict[str, bytes], modes=None) -> None:
    if any(root.iterdir()):
        raise ValueError('RAM staging workspace must be empty')
    for name, content in sorted(files.items()):
        target = root / safe_path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if modes and modes.get(name)=='100755':
            target.chmod(0o755)


def read_workspace(root: Path, *, max_files=10000, max_bytes=67108864) -> dict[str, bytes]:
    from .coding_git import validate_source_path
    files, total, stack, entries = {}, 0, [root], 0
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as listing:
            for item in listing:
                entries+=1
                if entries>max_files*4:
                    raise ValueError('Worker directory inventory exceeds its bounded size')
                path=Path(item.path)
                metadata=item.stat(follow_symlinks=False)
                if stat.S_ISLNK(metadata.st_mode) or getattr(metadata,'st_file_attributes',0)&0x400:
                    raise ValueError('Worker created a symbolic link or reparse point')
                name=validate_source_path(path.relative_to(root).as_posix())
                if stat.S_ISDIR(metadata.st_mode):
                    stack.append(path)
                    continue
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink!=1:
                    raise ValueError('Worker created a non-regular or hard-linked file')
                size=metadata.st_size
                if size>8388608 or total+size>max_bytes or len(files)>=max_files:
                    raise ValueError('Worker snapshot exceeds the bounded project size')
                content=path.read_bytes()
                total+=len(content)
                files[name]=content
    if len({name.casefold() for name in files})!=len(files):
        raise ValueError('Worker created Windows-ambiguous case aliases')
    return files


def observed_changes(before, after, original, project: CodingProject, *, tests_only=False):
    changed = sorted(name for name in set(before) | set(after) if before.get(name) != after.get(name))
    if not changed:
        raise ValueError('The native editor produced no file changes')
    records, total = [], 0
    for name in changed:
        safe_path(name)
        if tests_only:
            if not within(name, project.test_paths) or name in original or name not in after:
                raise ValueError('Test authoring may only add new regression tests under test_paths')
            if Path(name).name in ('conftest.py','pytest.ini','tox.ini','setup.cfg','pyproject.toml','setup.py'):
                raise ValueError('Model-authored test configuration is forbidden')
        elif not within(name, project.allowed_paths):
            raise ValueError('Editor changed a protected test, configuration, or unregistered path: ' + name)
        old = before.get(name, b'').decode('utf-8')
        new = after.get(name, b'').decode('utf-8')
        initial = original.get(name, b'').decode('utf-8')
        def lines_changed(a, b):
            return sum((i2-i1)+(j2-j1) for tag,i1,i2,j1,j2 in
                       difflib.SequenceMatcher(None, a.splitlines(True), b.splitlines(True), autojunk=False).get_opcodes()
                       if tag != 'equal')
        count = lines_changed(old, new)
        aggregate = sum(max(i2-i1,j2-j1) for tag,i1,i2,j1,j2 in
                        difflib.SequenceMatcher(None,initial.splitlines(True),new.splitlines(True),autojunk=False).get_opcodes()
                        if tag!='equal')
        total += count
        if aggregate > 500 or (name in original and aggregate > .8 * len(initial.splitlines())):
            raise ValueError('Aggregate edits exceed the per-file rewrite boundary')
        patch = ''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                           fromfile='a/'+name, tofile='b/'+name, n=3))
        records.append({'path': name, 'before_sha256': hashlib.sha256(before.get(name,b'')).hexdigest(),
                        'after_sha256': hashlib.sha256(after.get(name,b'')).hexdigest(),
                        'diff_sha256': hashlib.sha256(patch.encode()).hexdigest(), 'patch': patch,
                        'changed_lines': count, 'aggregate_changed_lines': aggregate,
                        'small_targeted_change': count < 20})
    if len(changed)>14:
        raise ValueError('A chunk may change at most 14 files so its execution/review batch stays within 20 tasks')
    if total > 100:
        raise ValueError('A staging chunk may change at most 100 lines across all files')
    return records


def validate_leaf_chunk(before, after, original, project):
    """Bound the entire source/test commit by actual added plus deleted lines.

    Context is real neighboring file content, independently bounded to 20–100
    lines. Small edits are not padded: their window expands into unchanged
    source. New short test files contribute their actual contents to the leaf.
    """
    records=[]; windows=[]
    for name in sorted(set(before)|set(after)):
        if before.get(name)==after.get(name):
            continue
        tests=within(name,project.test_paths)
        record=observed_changes({name:before[name]} if name in before else {},
            {name:after[name]} if name in after else {},
            {name:original[name]} if name in original else {},project,tests_only=tests)[0]
        records.append(record)
        old=before.get(name,b'').decode().splitlines(True); new=after.get(name,b'').decode().splitlines(True)
        # Use the larger physical side; include every changed span and extend
        # narrow windows with adjacent unchanged lines, never invented padding.
        use_new=len(new)>=len(old); content=new if use_new else old
        opcodes=difflib.SequenceMatcher(None,old,new,autojunk=False).get_opcodes()
        changed=[(j1,j2) if use_new else (i1,i2) for tag,i1,i2,j1,j2 in opcodes if tag!='equal']
        start=min(a for a,b in changed); end=max(b for a,b in changed)
        width=max(end-start,min(20,len(content)))
        start=max(0,min(start-(width-(end-start))//2,len(content)-width)); end=start+width
        windows.append({'path':name,'side':'after' if use_new else 'before','start_line':start+1,
                        'end_line':end,'line_count':width,
                        'content_sha256':hashlib.sha256(''.join(content[start:end]).encode()).hexdigest()})
    count=sum(item['changed_lines'] for item in records)
    context=sum(item['line_count'] for item in windows)
    if not records or count>100:
        raise ValueError('A complete leaf source/test Git diff may contain at most 100 added plus deleted lines')
    if not 20<=context<=100:
        raise ValueError('A complete leaf requires 20–100 physical context lines; re-fracture its context windows')
    return {'changed_lines':count,'context_lines':context,'context_windows':windows}


class CodingCoordinator:
    """Physical coding stages; all transitions occur afterwards under a lease."""
    def __init__(self, config, store, native_runner, ramdisk, *, host_boot_id=None):
        self.config, self.store, self.native, self.ramdisk = config, store, native_runner, ramdisk
        self.host_boot_id = host_boot_id
        from .coding_git import GitStager
        self.git = GitStager(config.private_root / 'coding-git', git_executable=getattr(config,'git_executable',None))

    def submit(self, project_id, objective, requirements, workflow_id=None):
        from .coding_git import capture_repository
        if project_id not in self.config.coding_projects:
            raise ValueError('Unknown registered coding project')
        if not self.config.ramdisk.enabled or not self.config.docker.enabled:
            raise ValueError('Coding requires verified RAM staging and independent Docker tests')
        project = self.config.coding_projects[project_id]
        snapshot = capture_repository(project.repository, project.branch, git_executable=getattr(self.config,'git_executable',None))
        validate_project_files(snapshot.files)
        from .planning_governance import validate_registration,collect_external_sources
        try:
            planning_evidence=validate_registration(project.planning,snapshot.files)
        except ValueError as exc:
            return self.store.submit_coding_snapshot(project,objective,requirements,snapshot,
                self.config.docker,workflow_id,planning_blocker=str(exc))
        if not planning_evidence['stage_execution_verified']:
            return self.store.submit_coding_snapshot(project,objective,requirements,snapshot,
                self.config.docker,workflow_id,planning_evidence=planning_evidence,
                planning_blocker='Canonical seven-stage source is registered, but its exact execution transitions have no verified controller binding')
        planning_evidence['external_sources']=collect_external_sources(project.planning)
        return self.store.submit_coding_snapshot(project, objective, requirements, snapshot,
                                                 self.config.docker, workflow_id,planning_evidence=planning_evidence)

    def run(self, node, slot, context, heartbeat, launched, cancel_event, *, on_native_start=None,on_native_end=None,container_reservation=None):
        from .coding_git import RepositorySnapshot
        from .container_policy import DockerPolicy
        from .containers import DockerRunner, ContainerCleanupError, ContainerCapacityError
        from .failures import ProviderFailure
        from .worker import WorkerCleanupError, ExecutionRevokedError
        import threading
        import time
        state = self.store.coding_state(node['workflow_id'])
        project = CodingProject.from_dict(node['payload']['project_id'], state['project'])
        original = self.store.coding_files(state['original_snapshot'])
        before = self.store.coding_files(node['payload']['snapshot_sha256'])
        descriptor = self.ramdisk.workspace(slot)
        descriptor.validate(identity=descriptor.identity)
        workspace = descriptor.root / 'project'
        workspace.mkdir()
        descriptor.validate(identity=descriptor.identity,cwd=workspace)
        materialize(workspace, before, state['modes'])
        kind = node['kind']
        if kind in ('CODE_TEST', 'CODE_INTEGRATE'):
            stopped = threading.Event()
            def renew():
                while not stopped.wait(max(.1,min(getattr(self.config,'heartbeat_seconds',5), self.config.lease_seconds / 3))):
                    if not heartbeat():
                        cancel_event.set()
                        return
            monitor = threading.Thread(target=renew, daemon=True)
            monitor.start()
            try:
                if not heartbeat():
                    raise ExecutionRevokedError('Coding stage was revoked before execution')
                if kind == 'CODE_TEST':
                    captured = DockerPolicy.from_dict(state['docker'])
                    current = self.config.docker
                    if (captured.image not in current.allowed_images or any(getattr(captured,key)>getattr(current,key)
                            for key in ('memory_mb','cpus','pids_limit','tmpfs_mb','max_containers'))):
                        raise ProviderFailure('configuration')
                    runner = DockerRunner(captured, self.config.private_root / 'containers',
                        trusted_operator=self.config.operator_name,host_boot_id=self.host_boot_id)
                    try:
                        evidence = runner.run(workspace, job_id=node['job_id'], attempt_id=node['attempt_id'], cancel_event=cancel_event,
                                              ramdisk_workspace=descriptor,source_modes=state['modes'],reservation=container_reservation)
                    except ContainerCleanupError as exc:
                        raise WorkerCleanupError('Docker could not verify removal of its owned container') from exc
                    except ContainerCapacityError as exc:
                        raise ProviderFailure('resource',retry_after_seconds=5) from exc
                    self.store.record_coding_attempt_evidence(node,evidence)
                    if evidence.get('cleanup_verified') is not True:
                        raise WorkerCleanupError('Docker termination could not be verified')
                    candidate_failure=evidence.get('failure_category') in ('oom','timeout','output_limit')
                    source_proven=evidence.get('source_verified') is True or evidence.get('input_source_verified') is True
                    if evidence.get('failure_category')=='container_contract' and source_proven:
                        raise ProviderFailure('configuration')
                    if evidence.get('quarantine_required') and not (candidate_failure and source_proven):
                        raise ProviderFailure('resource',retry_after_seconds=30)
                    if evidence.get('failure_category') == 'cancelled' or cancel_event.is_set():
                        raise ExecutionRevokedError('Docker test stage was cancelled')
                    if not evidence.get('passed') and evidence.get('failure_category') != 'tests_failed' and not (candidate_failure and source_proven):
                        raise ProviderFailure('resource', retry_after_seconds=30)
                    output = {'passed': evidence['passed'], 'source_snapshot_sha256': state['current_snapshot'],
                              'test_receipt_sha256': digest(evidence), 'phase': node['payload']['phase']}
                else:
                    # Only sealed, reviewed bytes enter Git plumbing. Worker Git
                    # configuration, hooks, indexes and filters are never read.
                    baseline = RepositorySnapshot.from_dict(state['repository_snapshot'], original)
                    chunk_id = 'leaf-'+str(state['leaf_index'])+'-cycle-' + str(state['cycle'])
                    if node['payload'].get('approved_integration'):
                        staged = state['chunks'][-1]['staged']
                    else:
                        staged = self.git.stage(baseline, before, workflow_id=node['workflow_id'], chunk_id=chunk_id,
                                                parent_commit=state['last_commit'],max_changed_lines=100)
                    if cancel_event.is_set() or not heartbeat():
                        raise ExecutionRevokedError('Coding integration was revoked')
                    self.store.prepare_coding_integration(node,staged)
                    integration = self.git.integrate(baseline, staged,
                        auto_integrate=bool(node['payload']['done'] and
                                            (project.auto_integrate or node['payload'].get('approved_integration'))),
                        cas_guard=lambda: self.store.coding_cas_guard(node))
                    evidence = {'staged': staged, 'integration': integration,
                                'source_snapshot_sha256': state['current_snapshot'],
                                'reviews_sha256': node['payload']['reviews_sha256'],
                                'test_receipt_sha256': node['payload']['test_receipt_sha256']}
                    output = {'status': integration['status'], 'result_commit': staged['result_commit']}
                receipt = {'executor': 'docker' if kind=='CODE_TEST' else 'git', 'job_id': node['job_id'],
                           'attempt_id': node['attempt_id'], 'fencing_token': node['fencing_token'],
                           'output_sha256': digest(output), 'finished_at': time.time()}
                return output, receipt, evidence, None
            finally:
                stopped.set()
                monitor.join(timeout=2)
        if on_native_start is not None:
            on_native_start(workspace)
        try:
            native_node={**node,'payload':{**node['payload'],'source_context':source_context(before,node['payload'],project)}}
            output, receipt = self.native.run(native_node, slot, context, heartbeat, launched,
                                              workspace=workspace, ramdisk_workspace=descriptor)
        finally:
            if on_native_end is not None:
                on_native_end()
        after = read_workspace(workspace)
        validate_project_files(after)
        if after!=before:
            raise ValueError('Native coding tools modified inputs; complete-file proposals must be applied only by the controller')
        if kind in ('CODE_EDIT', 'CODE_TEST_AUTHOR'):
            blocks=output.get('artifact_blocks')
            if blocks:
                from .coding_plan import apply_artifact_protocol
                protected=[name for name in before if within(name,project.test_paths)]
                after=apply_artifact_protocol(blocks,before,project,tests_only=kind=='CODE_TEST_AUTHOR',protected=protected)
                if kind=='CODE_EDIT' and any(name not in node['payload']['active_leaf']['file_targets']
                        for name in set(before)|set(after) if before.get(name)!=after.get(name)):
                    raise ValueError('A fracture leaf may change only its single planned implementation target')
                validate_project_files(after)
                # Only the controller writes the proposal, after native tree
                # closure and before independently observing the physical diff.
                descriptor.validate(identity=descriptor.identity,cwd=workspace)
                for name,data in after.items():
                    if before.get(name)!=data:
                        target=workspace/safe_path(name)
                        target.parent.mkdir(parents=True,exist_ok=True)
                        target.write_bytes(data)
                after=read_workspace(workspace)
            if kind=='CODE_TEST_AUTHOR' and after==before:
                raise ValueError('Red-green projects require newly authored regression tests before implementation')
            else:
                no_change = kind=='CODE_EDIT' and node['payload'].get('phase')=='P9' and after==before and output.get('remaining_work') is False
                changes = state.get('changes',[]) if no_change else observed_changes(before, after, before if kind=='CODE_TEST_AUTHOR' else original, project, tests_only=kind=='CODE_TEST_AUTHOR')
                if kind=='CODE_EDIT':
                    review_base = self.store.coding_files(state['review_base_snapshot'])
                    changes = observed_changes(review_base,after,original,project)
            if kind=='CODE_EDIT' and type(output.get('done')) is not bool:
                raise ValueError('Editor must explicitly report whether the requested work is complete')
            if kind=='CODE_EDIT' and len(changes)+len(state.get('test_changes',[]))>14:
                raise ValueError('Combined source/test files exceed the bounded review task batch')
            from .coding_checks import validate_generated_files
            validate_generated_files(after,[name for name in after if before.get(name)!=after[name]])
            chunk = (validate_leaf_chunk(self.store.coding_files(state['leaf_baseline_snapshot']),after,original,project)
                     if kind=='CODE_EDIT' else None)
            return output, receipt, {'changes': changes, 'snapshot_sha256': digest(manifest(after)),
                                     'chunk':chunk,
                                     'no_source_change':after==before,
                                     'requirements_traced': output.get('requirements_traced', [])}, after
        evidence = {'source_snapshot_sha256': state['current_snapshot']}
        if kind=='CODE_PLAN':
            from .coding_plan import validate_plan
            evidence['plan']=validate_plan(output,project,before,node['payload']['requirements'])
        if kind == 'CODE_RESEARCH':
            citations = output.get('sources')
            if not isinstance(citations, list) or len(citations)<2 or len(citations)>20:
                raise ValueError('Research requires two to twenty verifiable technical source citations')
            verified = []
            for item in citations:
                if not isinstance(item, dict) or not isinstance(item.get('quote'), str) or len(item['quote'].strip())<12:
                    raise ValueError('Research sources require an actual nontrivial quotation')
                path = item.get('path')
                source = (json.dumps(state['last_test'], sort_keys=True, ensure_ascii=False)
                          if path=='$test_receipt' else before.get(path, b'').decode('utf-8', 'replace'))
                if item['quote'] not in source:
                    raise ValueError('Research quotation is absent from its immutable source')
                verified.append({'path': path, 'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
                                 'quote_sha256': hashlib.sha256(item['quote'].encode()).hexdigest()})
            if (job_phase:=node['payload'].get('research_phase'))!='initial' and (not any(item['path']=='$test_receipt' for item in verified) or not any(item['path']!='$test_receipt' for item in verified)):
                raise ValueError('Research must examine actual test diagnostics and project technical sources')
            evidence['verified_sources'] = verified
            if project.planning:
                from .planning_governance import validate_external_research
                evidence.update(validate_external_research(output,state['planning_evidence']['external_sources'],
                    [f'R{index}' for index in range(1,len(node['payload']['requirements'])+1)]))
        return output, receipt, evidence, None
