"""Real candidate files; inference proposes changes but never writes source itself."""
import json
import hashlib
from pathlib import Path

import pytest

from cochem_supervisor.repair_artifacts import source_packet, apply_proposal, parse_proposal
from cochem_supervisor.runner import repair_command, validate_provider_spec


def candidate(tmp_path):
    path=tmp_path/'src/cochem_pipeline/example.py'
    path.parent.mkdir(parents=True)
    path.write_text('value = 1\n')
    scope=['src/cochem_pipeline/']
    packet=source_packet(tmp_path,scope,{'diagnostic':'example.py TypeError'})
    proposal={'summary':'Correct value','files':{'src/cochem_pipeline/example.py':'value = 2\n'}}
    return path,scope,packet,proposal


def test_controller_applies_hash_bound_valid_python_only(tmp_path):
    path,scope,packet,proposal=candidate(tmp_path)
    receipt=apply_proposal(tmp_path,proposal,packet,scope)
    assert path.read_text()=='value = 2\n'
    assert receipt['application']=='controller-only' and receipt['native_tools_enabled'] is False
    assert receipt['changes'][0]['before_sha256']==packet['files']['src/cochem_pipeline/example.py']['sha256']
    with pytest.raises(ValueError,match='changed'):
        apply_proposal(tmp_path,proposal,packet,scope)


@pytest.mark.parametrize('name',['../outside.py','src/cochem_supervisor/engine.py','tests/test_fake.py',
                                'src/cochem_pipeline/NUL.py','src/cochem_pipeline/stream:ads.py'])
def test_omitted_or_outside_files_cannot_be_edited(tmp_path,name):
    path,scope,packet,proposal=candidate(tmp_path)
    proposal['files']={name:'value = 3\n'}
    with pytest.raises(ValueError):
        apply_proposal(tmp_path,proposal,packet,scope)
    assert path.read_text()=='value = 1\n'


def test_new_allowlisted_python_module_is_created_from_proved_baseline_absence(tmp_path):
    path,scope,packet,proposal=candidate(tmp_path)
    name='src/cochem_pipeline/new_module.py'
    proposal['files'][name]='def remedy():\n    return 42\n'
    receipt=apply_proposal(tmp_path,proposal,packet,scope)
    created=tmp_path/name
    assert created.read_text()=='def remedy():\n    return 42\n'
    assert created.stat().st_nlink==1
    record=next(item for item in receipt['changes'] if item['path']==name)
    assert record['operation']=='create' and record['before_sha256'] is None
    assert record['after_sha256']==hashlib.sha256(created.read_bytes()).hexdigest()
    assert not list(path.parent.glob('.repair-*.tmp'))
    assert name not in packet['baseline_inventory']


def test_inventory_includes_omitted_and_nonpython_baseline_files_but_cannot_edit_omitted(tmp_path):
    from cochem_supervisor.repair_artifacts import MAX_SOURCE_BYTES
    path,scope,_,proposal=candidate(tmp_path)
    omitted=path.with_name('omitted.py')
    omitted.write_text('#'+'x'*MAX_SOURCE_BYTES+'\n')
    metadata=path.with_name('metadata.json');metadata.write_text('{"protected":"baseline"}')
    packet=source_packet(tmp_path,scope,{'diagnostic':'example.py'})
    assert packet['inventory_complete'] is True
    assert packet['baseline_inventory']['src/cochem_pipeline/omitted.py']==hashlib.sha256(omitted.read_bytes()).hexdigest()
    assert packet['baseline_inventory']['src/cochem_pipeline/metadata.json']==hashlib.sha256(metadata.read_bytes()).hexdigest()
    assert 'src/cochem_pipeline/omitted.py' not in packet['files']
    proposal['files']={'src/cochem_pipeline/omitted.py':'value = 3\n'}
    with pytest.raises(ValueError,match='omitted existing'):
        apply_proposal(tmp_path,proposal,packet,scope)
    assert path.read_text()=='value = 1\n'


def test_new_module_requires_existing_captured_parent_and_current_absence(tmp_path):
    path,scope,packet,proposal=candidate(tmp_path)
    proposal['files']={'src/cochem_pipeline/new_package/new.py':'value = 1\n'}
    with pytest.raises((ValueError,FileNotFoundError)):
        apply_proposal(tmp_path,proposal,packet,scope)
    assert not (path.parent/'new_package').exists()
    late=path.with_name('new.py');late.write_text('operator_value = 99\n')
    proposal['files']={'src/cochem_pipeline/new.py':'value = 1\n'}
    with pytest.raises(ValueError,match='changed after captured baseline'):
        apply_proposal(tmp_path,proposal,packet,scope)
    assert late.read_text()=='operator_value = 99\n'


def test_new_module_cannot_alias_existing_windows_path_or_inject_parent(tmp_path):
    path,scope,packet,proposal=candidate(tmp_path)
    proposal['files']={'src/cochem_pipeline/EXAMPLE.py':'value = 100\n'}
    with pytest.raises(ValueError,match='proved baseline/current absence'):
        apply_proposal(tmp_path,proposal,packet,scope)
    (path.parent/'added_later').mkdir()
    proposal['files']={'src/cochem_pipeline/added_later/new.py':'value = 1\n'}
    with pytest.raises(ValueError,match='changed after captured baseline'):
        apply_proposal(tmp_path,proposal,packet,scope)


def test_complete_packet_and_all_syntax_required_before_existing_or_new_writes(tmp_path):
    path,scope,packet,proposal=candidate(tmp_path)
    proposal['files']['src/cochem_pipeline/new.py']='def broken syntax!'
    with pytest.raises(SyntaxError):
        apply_proposal(tmp_path,proposal,packet,scope)
    assert path.read_text()=='value = 1\n' and not path.with_name('new.py').exists()
    proposal['files']['src/cochem_pipeline/new.py']='value = 3\n'
    incomplete={**packet,'inventory_complete':False}
    with pytest.raises(ValueError,match='complete controller-captured baseline'):
        apply_proposal(tmp_path,proposal,incomplete,scope)
    assert path.read_text()=='value = 1\n' and not path.with_name('new.py').exists()


def test_new_module_can_use_preexisting_empty_allowlisted_directory(tmp_path):
    path,scope,_,proposal=candidate(tmp_path)
    parent=path.parent/'existing_package';parent.mkdir()
    packet=source_packet(tmp_path,scope,{})
    proposal['files']={'src/cochem_pipeline/existing_package/new.py':'answer = 42\n'}
    apply_proposal(tmp_path,proposal,packet,scope)
    assert (parent/'new.py').read_text()=='answer = 42\n'


def test_syntax_failure_does_not_modify_candidate(tmp_path):
    path,scope,packet,proposal=candidate(tmp_path)
    proposal['files']['src/cochem_pipeline/example.py']='def broken syntax!'
    with pytest.raises(SyntaxError):
        apply_proposal(tmp_path,proposal,packet,scope)
    assert path.read_text()=='value = 1\n'


@pytest.mark.parametrize('provider,model',[('claude','claude-sonnet-5-5'),('codex','gpt-6-sol')])
def test_no_repair_native_tool_can_launch_unrouted_models(tmp_path,provider,model):
    # Command-construction fixture only; no CLI is created or executed.
    executable=str(tmp_path/'native-cli.exe')
    spec={'provider':provider,'model':model,'executable':executable}
    argv=repair_command(spec,[executable],str(tmp_path/'candidate'))
    if provider=='claude':
        assert argv[argv.index('--tools')+1]==''
    else:
        assert 'features.multi_agent=false' in argv and 'features.shell_tool=false' in argv
        assert argv[argv.index('--sandbox')+1]=='read-only'
    with pytest.raises(ValueError,match='cannot enable'):
        validate_provider_spec({**spec,'allowed_tools':['Agent']})
