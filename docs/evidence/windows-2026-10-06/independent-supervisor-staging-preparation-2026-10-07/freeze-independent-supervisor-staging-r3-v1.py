"""CreateNew local inventory only; never install or invoke a privileged helper."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy

WORK = Path(__file__).absolute().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
ROOT = r'C:\Program Files\CoChem\Supervisor4.2.7-windows-20261007-staging-v1'


def freeze():
    support = runpy.run_path(str(WORK / 'prepare-independent-supervisor-staging-r3-v1.py'), run_name='preparation_definitions')
    plan = support['plan']()
    if plan['source_changes_since_r3']['added'] or plan['source_changes_since_r3']['removed']:
        raise ValueError('Source inventory drifted')
    if {r['relative'] for r in plan['source_changes_since_r3']['changed']} != {
        'src/cochem_pipeline/performance_acceptance.py', 'src/cochem_supervisor/engine.py', 'src/cochem_supervisor/replay.py'}:
        raise ValueError('Source changes differ from reviewed scope')
    ini = WORK / 'acceptance-pytest-staging-r3-v1.ini'
    if support['read'](ini) != b'[pytest]\r\naddopts = -ra\r\n':
        raise ValueError('Acceptance configuration differs')
    def row(path, relative):
        raw = support['read'](path)
        return {'relative': relative, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    return {'schema': 'cochem-independent-supervisor-staging-freeze/1',
        'complete_source_freeze': True, 'activation_authorized': False,
        'target_root': ROOT, 'unpublished_pair_root': ROOT + r'\unpublished-private\unresolved-pair',
        'pipeline_config_sha256': plan['preserved_pipeline_configuration_sha256'],
        'source_files': plan['source_files'],
        'acceptance_files': plan['test_files'] + plan['public_test_support_files'] + [row(ini, 'pytest.ini')],
        'control_files': [row(WORK / name, target) for name, target in (
            ('stage-independent-supervisor-holds-r3-v1.py', 'stage-independent-supervisor-holds-r3-v1.py'),
            ('bootstrap-unresolved-budget-pair.py', 'bootstrap-unresolved-budget-pair.py'),
            ('unresolved-budget-evidence.proposed.json', 'unresolved-budget-evidence.json'))],
        'selected_regression_targets': plan['selected_regression_targets'],
        'source_changes_since_r3': plan['source_changes_since_r3'],
        'scope': 'Fresh unpublished code/test runtime and immutable paired holds only; no activation or allowance.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-name', required=True)
    args = parser.parse_args()
    if Path(args.output_name).name != args.output_name or not args.output_name.endswith('.json'):
        raise ValueError('Output must be a new workspace JSON basename')
    raw = (json.dumps(freeze(), indent=2) + '\n').encode()
    target = WORK / args.output_name
    with target.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'path': str(target), 'sha256': hashlib.sha256(raw).hexdigest()}))
