"""Production-only parity between captured installed SRS and active RAG bytes."""
from __future__ import annotations

from copy import deepcopy
import re
import sqlite3
import threading
import time


class KnowledgeAuthority:
    """Inspect bounded source reads on generation changes and every 30 seconds.

    A mismatch is an admission prerequisite, not a reason to overwrite pinned
    historical source files or spend a subscription call on automated repair.
    Generic domain KnowledgeService instances do not acquire this constraint.
    """
    def __init__(self, knowledge, captured):
        expected = captured.get('specification_sha256') if isinstance(captured, dict) else None
        if not isinstance(expected, str) or not re.fullmatch(r'[0-9a-f]{64}', expected):
            raise ValueError('Production knowledge authority requires captured canonical source bytes')
        amendments = captured.get('owner_amendments')
        if (not isinstance(amendments, list) or not 1 <= len(amendments) <= 32
                or any(not isinstance(row, dict) or not isinstance(row.get('source'), str)
                       or not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}', row['source'])
                       or not isinstance(row.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', row['sha256'])
                       or not isinstance(row.get('revision'), str) or not row['revision'] for row in amendments)):
            raise ValueError('Production knowledge authority requires captured owner amendment hashes and revisions')
        self.knowledge = knowledge
        self.captured = deepcopy(captured)
        self._lock = threading.Lock()
        self._evidence = None
        self._generation = None
        self._checked_monotonic = None

    def status(self, *, force=False):
        current = self.knowledge.status()
        generation = (current.get('generation'), current.get('manifest_sha256'))
        with self._lock:
            due = (force or self._evidence is None or self._generation != generation
                   or self._checked_monotonic is None or time.monotonic() - self._checked_monotonic >= 30)
            if current.get('ready') is not True:
                evidence = {'authority_matches_capture': None,
                    'authority_reason': 'Registered knowledge corpus/index is not ready',
                    'authority_checked_at': time.time(), 'authority_source': '.sources/4.2.7_SRS.md',
                    'captured_specification_sha256': self.captured['specification_sha256']}
                # A subsequent successful refresh must verify again even when
                # it republishes the previous generation after a transient error.
                self._checked_monotonic = None
            elif due:
                evidence = {'authority_matches_capture': None, 'authority_checked_at': time.time(),
                    'authority_source': '.sources/4.2.7_SRS.md',
                    'captured_specification_sha256': self.captured['specification_sha256'],
                    'captured_specification_revision': self.captured.get('specification_revision')}
                try:
                    source = self.knowledge.read('.sources/4.2.7_SRS.md')
                    matched = (source.get('sha256') == self.captured['specification_sha256']
                               and source.get('authority') == 'current_normative'
                               and source.get('authority_revision') == self.captured.get('specification_revision')
                               and source.get('generation') == current.get('generation'))
                    amendments = []
                    for captured in self.captured['owner_amendments']:
                        amendment = self.knowledge.read('.sources/' + captured['source'])
                        amendment_matches = (amendment.get('sha256') == captured['sha256']
                            and amendment.get('authority') == 'owner_decision'
                            and amendment.get('authority_revision') == captured['revision']
                            and amendment.get('generation') == current.get('generation'))
                        matched = matched and amendment_matches
                        amendments.append({'source': captured['source'], 'matches_capture': amendment_matches,
                            'captured_sha256': captured['sha256'], 'observed_sha256': amendment.get('sha256'),
                            'observed_authority': amendment.get('authority'),
                            'observed_revision': amendment.get('authority_revision')})
                    evidence.update(authority_matches_capture=matched,
                        observed_specification_sha256=source.get('sha256'),
                        observed_authority=source.get('authority'),
                        observed_authority_revision=source.get('authority_revision'),
                        owner_amendments=amendments,
                        authority_reason=None if matched else
                        'Active RAG canonical source differs from the installed captured SRS or its reviewed authority; provision a fresh versioned corpus')
                except (OSError, ValueError, sqlite3.Error):
                    evidence['authority_reason'] = ('Active RAG canonical source cannot be verified; '
                        'provision a fresh versioned corpus without rewriting historical source pins')
                self._evidence = evidence
                self._generation = generation
                self._checked_monotonic = time.monotonic()
            else:
                evidence = self._evidence
        return {**current, **evidence, 'index_ready': current.get('ready') is True,
                'ready': current.get('ready') is True and evidence['authority_matches_capture'] is True,
                'authority_max_recheck_seconds': 30}
