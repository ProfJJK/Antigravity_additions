"""Isolated observation child: standard library, psutil and observer code only.

The privileged recovery actuator is a separate process. This child never loads
pipeline application modules, executes a repair, or acquires a job-board write
connection. Its parent owns the deadline and bounded subprocess collection.
"""
from __future__ import annotations

import argparse
import importlib.abc
import json
from pathlib import Path
import sys


MAXIMUM_OUTPUT_BYTES = 1024 * 1024
_FORBIDDEN = {'cochem_pipeline', 'cochem_mcp', 'cochem'}
_ALLOWED = set(sys.stdlib_module_names) | {'__main__', 'cochem_supervisor', 'psutil'}


class _SterileImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] not in _ALLOWED:
            raise ImportError('The observation process cannot import application or third-party modules')
        return None


def install_import_guard() -> None:
    """Reject application imports before they can execute, including later ones."""
    if any(name.split('.')[0] not in _ALLOWED for name in sys.modules):
        raise RuntimeError('Observation process already contains forbidden imports')
    if not any(isinstance(finder, _SterileImports) for finder in sys.meta_path):
        sys.meta_path.insert(0, _SterileImports())


def import_audit() -> dict:
    roots = {name.split('.')[0] for name in sys.modules}
    forbidden = sorted(root for root in roots if root in _FORBIDDEN or root not in _ALLOWED)
    return {'sterile': not forbidden, 'forbidden_modules': forbidden,
            'isolated': bool(sys.flags.isolated), 'site_disabled': bool(sys.flags.no_site),
            'external_roots': sorted(roots - set(sys.stdlib_module_names) -
                                     {'__main__', 'cochem_supervisor'})}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Read-only isolated supervisor observation')
    parser.add_argument('--private-root', required=True, type=Path)
    parser.add_argument('--heartbeat-timeout', type=float, default=30)
    parser.add_argument('--stall-timeout', type=float, default=600)
    parser.add_argument('--repeated-failures', type=int, default=3)
    parser.add_argument('--wal-limit-mb', type=int, default=256)
    parser.add_argument('--now', type=float)
    parser.add_argument('--process-history', type=Path)
    args = parser.parse_args(argv)
    try:
        if not sys.flags.isolated or not sys.flags.no_site:
            raise RuntimeError('Observation requires isolated startup with site disabled')
        install_import_guard()
        from .monitor import read_observation, _incident
        observation = read_observation(args.private_root, now=args.now,
            heartbeat_timeout=args.heartbeat_timeout, stall_timeout=args.stall_timeout,
            repeated_failures=args.repeated_failures, wal_limit_mb=args.wal_limit_mb)
        pid = observation['health'].get('heartbeat_pid')
        if args.process_history is not None and type(pid) is int and pid > 0:
            from .process_observer import ProcessObserver
            processes = ProcessObserver(args.process_history).observe(pid, now=args.now)
            observation['health']['process_resources'] = processes
            for alarm in processes['alarms']:
                observation['incidents'].append(_incident('resource', False,
                    'Sustained native process memory or handle growth requires resource recovery',
                    {'component': 'process_leak', **alarm}, 'process leak ' + alarm['metric']))
                observation['health']['repair_hold'] = True
                observation['health']['state'] = 'blocked'
        audit = import_audit()
        if not audit['sterile']:
            raise RuntimeError('Observation import boundary was violated')
        output = json.dumps({'observation': observation, 'import_audit': audit},
                            ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        if len(output) > MAXIMUM_OUTPUT_BYTES:
            raise ValueError('Observation exceeds its output bound')
        sys.stdout.buffer.write(output + b'\n')
        return 0
    except Exception as exc:
        # Diagnostic messages can contain paths or payload echoes. The parent
        # needs the failure class, not those private exception values.
        sys.stdout.write(json.dumps({'error_type': type(exc).__name__, 'import_audit': import_audit()}) + '\n')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
