"""Explicit capability gates for physical Windows test fixtures.

These helpers never relax production identity checks or emulate a native pass.
"""
import os
import pytest


def require_controller_git_context():
    if os.name != 'nt':
        return
    from cochem_pipeline.windows import require_system, WindowsIsolationError
    try:
        require_system()
    except WindowsIsolationError as error:
        if str(error) != 'The Warden must run as NT AUTHORITY\\SYSTEM, not an administrator or an agent account':
            raise
        pytest.skip('Physical Windows controller Git fixture requires actual SYSTEM; ordinary storage checks do not waive this boundary')


def create_optional_symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        if os.name != 'nt' or error.winerror != 1314:
            raise
        pytest.skip('Actual Windows symbolic-link creation privilege is unavailable; no symlink result is claimed')
