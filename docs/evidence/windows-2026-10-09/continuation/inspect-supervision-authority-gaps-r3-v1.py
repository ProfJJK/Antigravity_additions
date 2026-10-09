"""Reproduce two diagnostic gaps using only new disposable ordinary-user ledgers.

This is not a supervisor launcher. It never constructs Supervisor, accesses an
existing ledger, obtains credentials, launches children, or invokes providers.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile

from cochem_supervisor import budget_authority, engine, replay, state


PINS = {
    'cochem_supervisor.budget_authority': '36157718119f518756619b5222be9e930dba82a1726fd4b1bed23d153a217852',
    'cochem_supervisor.engine': 'c2c34601eb5299dc47306e894b7d55743b56d39e79035534198df9bf6c742d64',
    'cochem_supervisor.state': '404abfa2971a8f4a865171fc8fee3fe2c51d7837b6d49975346e6bac3740ff74',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    observed = {module.__name__: sha(module.__file__)
                for module in (budget_authority, engine, replay, state)}
    if any(observed[name] != digest for name, digest in PINS.items()):
        raise ValueError('Expected frozen r3 implementation is unavailable')
    evidence = {'schema': 'cochem-unresolved-budget-evidence/1',
                'reason': 'Synthetic test provenance; no historical authority is inferred.',
                'sources': [{'kind': 'synthetic_fixture', 'sha256': '1' * 64}]}
    with tempfile.TemporaryDirectory(prefix='cochem-supervision-gap-') as directory:
        root = Path(directory)
        ledger = state.Ledger(root / 'supervisor.db')
        ledger.hold_legacy_budget_authority(evidence, now=1)
        observer_calls = []
        publications = []
        supervisor = engine.Supervisor.__new__(engine.Supervisor)
        supervisor.private = root
        supervisor.ledger = ledger
        supervisor.stage = 'fixture'
        supervisor._publish = lambda *args, **kwargs: publications.append(kwargs)
        supervisor._read_observation = lambda: observer_calls.append(True)
        tick = supervisor.tick()
        before = {item.name: sha(item) for item in root.iterdir() if item.is_file()}
        incident = {'fingerprint': 'synthetic', 'category': 'code',
                    'summary': 'TypeError: synthetic fixture only', 'evidence': {}}
        projected = replay.replay_incident(incident, ledger_path=ledger.path, now=10000)
        after = {item.name: sha(item) for item in root.iterdir() if item.is_file()}
        if before != after:
            raise AssertionError('Read-only replay changed disposable source bytes')
        ledger.observe('synthetic', 'code', incident, now=10000)
        guarded = ledger.reserve('synthetic', now=10000)
        return {
            'schema': 'cochem-supervision-authority-gap-reproduction/1',
            'scope': 'Ordinary Windows, frozen installed r3 modules, disposable synthetic SQLite only',
            'module_sha256': observed,
            'held_tick': {'detector_calls': len(observer_calls),
                          'budget_hold_visible': tick['health']['budget_authority_hold'],
                          'publications': len(publications)},
            'held_replay': {'action': projected['action'],
                            'budget_projection': projected['budget_projection'],
                            'execution_authorized': projected['execution_authorized'],
                            'disposable_source_bytes_unchanged': before == after},
            'actual_transactional_reservation_denied': guarded is None,
            'production_databases_opened': False, 'tokens_read': False,
            'supervisor_constructor_called': False, 'native_calls': 0,
            'model_calls': 0, 'service_or_task_changes': 0,
            'linux_evidence_used': False,
        }


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, sort_keys=True, allow_nan=False))
