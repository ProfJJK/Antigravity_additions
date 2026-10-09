"""Create additive post-reboot observer compatibility from exact frozen inputs.

Requires the reviewed final first-start Python v5 and commissioning wrapper v5
hashes explicitly. It only prepares new files in this workspace. No provider,
task, protected runtime, database, configuration or private receipt is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

W = Path(__file__).resolve().parent
OLD_HELPER = '9770007a8c658a73e68ccc0c370eeaab2cc6568cf20cd009601e8c35c25de755'
OLD_TOP = 'fdd8f5fcacae85f2d80cb908d87d6fdad0b06c0783d2ff20fa2d59a458c4939d'
OLD_INSTALLER = '04f5d032fb56404e3031fa3137416126734d45d6ebbb7ed74f5bbc5c7149db6b'
OLD_BATCH = '6b1c7c7eb0a9f70d24abb9a778400b43917bb44007f2fa61bdf66c16e29b4ec7'
OLD_MANIFEST = 'f44a356bd0bdd4d27aff35e3f3d204509b608f3b72e31a39698003803681ee59'
DEPENDENCIES = 'ca32f0703edfb1091201b3f37101091145d09c16452b5b3f0145ab0b6755d429'
FILES = (
    'observe_resources.py', 'detector_bootstrap.py', 'cochem_supervisor/__init__.py',
    'cochem_supervisor/windows.py', 'cochem_supervisor/resource_observation.py',
    'cochem_supervisor/performance_acceptance.py', 'cochem_supervisor/shared_io.py',
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pinned(relative, expected):
    raw = (W / relative).read_bytes()
    if sha(raw) != expected:
        raise ValueError('Frozen source changed: ' + relative)
    return raw


def replace_exact(text, old, new, count):
    if text.count(old) != count:
        raise ValueError('Unexpected frozen replacement count: ' + old)
    return text.replace(old, new)


def render(first_start_sha256, commissioning_sha256):
    for value in (first_start_sha256, commissioning_sha256):
        if not isinstance(value, str) or re.fullmatch('[a-f0-9]{64}', value) is None:
            raise ValueError('An explicit lowercase final SHA256 is required')
    if first_start_sha256 == OLD_HELPER or commissioning_sha256 == OLD_TOP:
        raise ValueError('The reviewed v5 first-start and commissioning sources are required')
    pinned('commission-first-warden-r3-v5.py', first_start_sha256)
    pinned('run-pipeline-commissioning-r3-v5.ps1', commissioning_sha256)
    pinned('resource-observer-r3-v2-dependencies.json', DEPENDENCIES)
    old_manifest = json.loads(pinned('resource-observer-r3-v3/source-manifest.json', OLD_MANIFEST))
    if (old_manifest.get('schema') != 'cochem-external-observer-source-manifest/1' or
            [row.get('path') for row in old_manifest.get('files', [])] != list(FILES)):
        raise ValueError('Frozen seven-file observer inventory differs')
    payloads = {}
    for row in old_manifest['files']:
        name = row['path']
        raw = pinned('resource-observer-r3-v3/' + name, row['sha256'])
        if len(raw) != row['size']:
            raise ValueError('Frozen observer source length differs')
        if name == 'observe_resources.py':
            text = raw.decode('utf-8')
            text = replace_exact(text, 'ResourceObservation4.2.7-windows-20261008-r3-v3',
                                 'ResourceObservation4.2.7-windows-20261008-r3-v4', 1)
            text = replace_exact(text, 'ResourceObservationState4.2.7-windows-20261008-r3-v3',
                                 'ResourceObservationState4.2.7-windows-20261008-r3-v4', 1)
            raw = text.encode('utf-8')
        elif name == 'cochem_supervisor/resource_observation.py':
            raw = replace_exact(raw.decode('utf-8'), OLD_HELPER, first_start_sha256, 1).encode('utf-8')
        payloads[name] = raw
    new_manifest = {'schema': old_manifest['schema'], 'files': [
        {'path': name, 'size': len(payloads[name]), 'sha256': sha(payloads[name])} for name in FILES
    ]}
    manifest_raw = (json.dumps(new_manifest, indent=2) + '\n').encode('utf-8')
    manifest_sha256 = sha(manifest_raw)
    outputs = {'resource-observer-r3-v4/' + name: raw for name, raw in payloads.items()}
    outputs['resource-observer-r3-v4/source-manifest.json'] = manifest_raw

    installer = pinned('install-resource-observer-r3-v3.ps1', OLD_INSTALLER).decode('utf-8-sig')
    installer = replace_exact(installer, OLD_HELPER, first_start_sha256, 1)
    installer = replace_exact(installer, '20261008-r3-v3', '20261008-r3-v4', 3)
    installer = replace_exact(installer, "'resource-observer-r3-v3'", "'resource-observer-r3-v4'", 1)
    installer = replace_exact(installer, OLD_MANIFEST, manifest_sha256, 1)
    installer_raw = installer.encode('utf-8')
    installer_sha256 = sha(installer_raw)
    outputs['install-resource-observer-r3-v4.ps1'] = installer_raw

    batch = pinned('run-post-commissioning-setup-r3-v1.ps1', OLD_BATCH).decode('utf-8-sig')
    batch = replace_exact(batch, 'run-pipeline-commissioning-r3-v4.ps1', 'run-pipeline-commissioning-r3-v5.ps1', 1)
    batch = replace_exact(batch, OLD_TOP, commissioning_sha256, 1)
    batch = replace_exact(batch, 'install-resource-observer-r3-v3.ps1', 'install-resource-observer-r3-v4.ps1', 2)
    batch = replace_exact(batch, OLD_INSTALLER, installer_sha256, 2)
    batch = replace_exact(batch, OLD_MANIFEST, manifest_sha256, 2)
    batch = replace_exact(batch, 'ResourceObservation4.2.7-windows-20261008-r3-v3',
                          'ResourceObservation4.2.7-windows-20261008-r3-v4', 1)
    batch = replace_exact(batch, 'PostCommissioningSetup4.2.7-windows-20261008-r3-v1',
                          'PostCommissioningSetup4.2.7-windows-20261008-r3-v2', 1)
    outputs['run-post-commissioning-setup-r3-v2.ps1'] = batch.encode('utf-8')
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--first-start-python-sha256', required=True)
    parser.add_argument('--commissioning-wrapper-sha256', required=True)
    args = parser.parse_args()
    outputs = render(args.first_start_python_sha256, args.commissioning_wrapper_sha256)
    # Refuse all existing targets before the first mutation. Partial results are
    # preserved after an interruption; this generator never overwrites/resumes.
    root = W / 'resource-observer-r3-v4'
    if root.exists() or root.is_symlink() or any((W / name).exists() or (W / name).is_symlink() for name in outputs):
        raise FileExistsError('Fresh observer/batch outputs already exist; preserve them')
    for name, raw in outputs.items():
        path = W / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as output:
            output.write(raw)
        print(name, sha(raw))


if __name__ == '__main__':
    main()
