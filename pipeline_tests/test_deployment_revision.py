import json
import shutil
import subprocess
import sys

import pytest

from cochem_pipeline.deployment_revision import verify_installed_revision


def installation(tmp_path):
    repo = tmp_path/'repository'
    packages = tmp_path/'site-packages'
    source = tmp_path/'protected-source'
    acceptance = tmp_path/'acceptance'
    repo.mkdir()
    source.mkdir()
    for package in ('cochem_pipeline', 'cochem_mcp', 'cochem_supervisor'):
        root = repo/'src'/package
        root.mkdir(parents=True)
        (root/'__init__.py').write_text('__version__ = "4.2.7"\n')
        (root/'revision.json').write_text(json.dumps({'canonical': 'owner-amendment-2026-10-06'}))
        shutil.copytree(root, packages/package)
    for name in ('pyproject.toml', 'uv.lock'):
        (repo/name).write_text('exact reviewed bytes\n')
        shutil.copyfile(repo/name, source/name)
    for suite in ('pipeline_tests', 'mcp_tests'):
        (repo/suite).mkdir()
        (repo/suite/'test_exact.py').write_text('def test_exact():\n    assert 1 == 1\n')
        shutil.copytree(repo/suite, acceptance/suite)
    return repo, packages, source, acceptance


def test_installed_revision_requires_actual_code_assets_lock_and_policy_bytes(tmp_path):
    repo, packages, source, acceptance = installation(tmp_path)
    result = verify_installed_revision(repo, packages, source, acceptance_root=acceptance)
    assert result['verified'] and result['files'] == 6 and result['acceptance_files'] == 2
    asset = packages/'cochem_pipeline'/'revision.json'
    previous = asset.read_bytes()
    asset.write_text('{"canonical":"old-release-same-package-version"}')
    with pytest.raises(ValueError, match='canonical revision differs'):
        verify_installed_revision(repo, packages, source, acceptance_root=acceptance)
    asset.write_bytes(previous)
    (source/'uv.lock').write_text('unreviewed lock')
    with pytest.raises(ValueError, match='dependency/source policy differs'):
        verify_installed_revision(repo, packages, source)
    shutil.copyfile(repo/'uv.lock', source/'uv.lock')
    (acceptance/'pipeline_tests'/'test_exact.py').write_text('def test_exact(): pass')
    with pytest.raises(ValueError, match='immutable acceptance suite differs'):
        verify_installed_revision(repo, packages, source, acceptance_root=acceptance)


def test_revision_inventory_rejects_extra_module_and_link(tmp_path):
    repo, packages, source, _ = installation(tmp_path)
    extra = packages/'cochem_pipeline'/'surprise.py'
    extra.write_text('arbitrary code')
    with pytest.raises(ValueError, match='canonical revision differs'):
        verify_installed_revision(repo, packages, source)
    extra.unlink()
    extra.symlink_to(repo/'uv.lock')
    with pytest.raises(ValueError, match='links or reparse'):
        verify_installed_revision(repo, packages, source)


def test_native_provisioning_parser_accepts_256_isolated_identities_but_not_257():
    prefix = [sys.executable, '-m', 'cochem_pipeline.windows', 'provision', '--slots']
    allowed = subprocess.run([*prefix, '256'], capture_output=True, text=True, timeout=30)
    assert 'provision/validate requires --private-root and --workers-root' in allowed.stderr
    assert 'invalid choice' not in allowed.stderr
    refused = subprocess.run([*prefix, '257'], capture_output=True, text=True, timeout=30)
    assert 'invalid choice' in refused.stderr
