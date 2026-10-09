"""Read-only metadata audit; publish no credentials, logs or production state."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess

WORK = Path(__file__).resolve().parent
REPO = Path(r'D:\Gdrive\__CoChem\GitHub-Repo\Antigravity_additions')
OLD = str(REPO)
TARGET = WORK / 'portable-continuation-inventory-20261009.json'
CURATED = {
    'claude-paste-continuation-v6-evidence-20261008',
    'claude-reboot-continuation-v5-evidence-20261008',
    'pending-warden-recovery-v7-evidence-20261008',
    'running-monitoring-continuation-v8-evidence-20261008',
    'partial-resource-recovery-evidence-20261009',
    'foundation-continuation-doc-update-20261007',
    'docker-v2-final-doc-reconciliation-20261007',
    'ordinary-regression-20261007-r3',
    'ordinary-regression-20261007-r3-final',
    'ordinary-regression-20261007-r3-followup',
}
ALLOWED_EXTENSIONS = {'.py', '.ps1', '.json', '.xml', '.md', '.txt', '.ini', '.toml', '.diff'}
SECRET_PATTERNS = {
    'PRIVATE_KEY': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'OPENAI_SECRET_LITERAL': re.compile(rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}'),
    'GITHUB_SECRET_LITERAL': re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{24,}'),
    'BEARER_LITERAL': re.compile(rb'Bearer\s+[A-Za-z0-9_.-]{24,}'),
    'JWT_LITERAL': re.compile(rb'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}'),
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def selected(path):
    relative = path.relative_to(WORK)
    if any(part in {'__pycache__', '.pytest_cache', '.git'} for part in relative.parts):
        return False
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        return False
    if any(x in path.name.lower() for x in ('.stderr.', '.stdout.', 'git-config-before-')):
        return False
    if len(relative.parts) == 1:
        return path not in {TARGET, WORK / 'portable-continuation-audit-20261009.md'}
    return relative.parts[0] in CURATED or re.fullmatch(r'resource-observer-r3-v[1-4]', relative.parts[0]) is not None


def old_references(raw):
    text = raw.decode('utf-8-sig', errors='replace').replace('\\\\', '\\')
    rows = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if OLD.casefold() in line.casefold() or OLD.replace('\\', '/').casefold() in line.casefold():
            suffixes = []
            for base in (OLD, OLD.replace('\\', '/')):
                for match in re.finditer(re.escape(base) + r'[A-Za-z0-9_. /\\-]*', line, re.I):
                    suffixes.append(match.group().strip())
            rows.append({'line': line_number, 'referenced_paths': sorted(set(suffixes))})
    return rows


def credential_key_flags(raw, suffix):
    if suffix != '.json':
        return []
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        return []
    found = set()
    sensitive = {'access_token', 'refresh_token', 'id_token', 'api_key', 'password', 'client_secret', 'authorization'}
    def visit(item):
        if isinstance(item, dict):
            for key, val in item.items():
                if str(key).lower() in sensitive and isinstance(val, str) and val and val.lower() not in {'redacted', 'unknown', 'held', 'not_read', 'not_accessed'}:
                    found.add('CREDENTIAL_VALUE_KEY_' + str(key).upper())
                visit(val)
        elif isinstance(item, list):
            for val in item:
                visit(val)
    visit(value)
    return sorted(found)


def indexed_hashes():
    result = subprocess.run(['git', '-C', str(REPO), 'ls-files', '-z'], capture_output=True, check=False, timeout=30)
    if result.returncode:
        return {}, {'verified': False, 'git_exit_code': result.returncode, 'reason': 'ORDINARY_GIT_READ_UNAVAILABLE'}
    paths = result.stdout.decode('utf-8').split('\0')
    index = defaultdict(list)
    for name in paths:
        if not name:
            continue
        path = REPO / name
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS:
            index[digest(path.read_bytes())].append(name)
    return index, {'verified': True}


def main():
    index, index_status = indexed_hashes()
    entries = []
    flags = []
    omitted = Counter()
    for path in sorted(WORK.rglob('*')):
        if not path.is_file():
            continue
        if not selected(path):
            relative = path.relative_to(WORK)
            omitted[relative.parts[0] if len(relative.parts) > 1 else 'top_level_excluded_outputs'] += 1
            continue
        first = path.stat()
        raw = path.read_bytes()
        last = path.stat()
        if (first.st_size, first.st_mtime_ns) != (last.st_size, last.st_mtime_ns):
            raise RuntimeError('Input changed during inventory; no inventory published')
        relative = path.relative_to(WORK).as_posix()
        sha = digest(raw)
        markers = sorted(k for k, pattern in SECRET_PATTERNS.items() if pattern.search(raw))
        markers.extend(credential_key_flags(raw, path.suffix.lower()))
        if markers:
            flags.append({'path': relative, 'markers': sorted(set(markers))})
        entries.append({
            'path': relative, 'bytes': len(raw), 'sha256': sha,
            'role': 'source_or_test' if path.suffix.lower() in {'.py', '.ps1'} else 'evidence_configuration_or_historical_instructions',
            'old_repository_references': old_references(raw),
            'same_bytes_as_tracked_repository_paths': sorted(index.get(sha, [])),
            'publication_disposition': 'REVIEW_SECRET_PATTERN_BEFORE_COPY' if markers else 'CANDIDATE_TEXT_NO_DETECTED_SECRET_LITERAL',
        })
    report = {
        'schema': 'cochem-portable-continuation-source-inventory/1',
        'captured_utc': datetime.now(timezone.utc).isoformat(),
        'source_workspace': str(WORK), 'source_repository': OLD,
        'target_repository': r'C:\Users\ansac\source\repos\Pipeline-Mix-Model-Concurrent',
        'inventory_is_copy_or_publish_authorization': False,
        'production_state_or_credentials_copied': False,
        'frozen_sources_modified': False,
        'tracked_worktree_index_comparison': index_status,
        'top_level_files_at_capture': len([x for x in WORK.iterdir() if x.is_file()]),
        'candidate_files': len(entries), 'candidate_bytes': sum(x['bytes'] for x in entries),
        'old_repository_reference_files': sum(bool(x['old_repository_references']) for x in entries),
        'candidate_files_not_byte_matched_to_current_tracked_worktree': sum(not x['same_bytes_as_tracked_repository_paths'] for x in entries) if index_status['verified'] else None,
        'literal_scan_scope': 'Conservative literal patterns and credential-value JSON keys only. This is not a complete privacy review or permission to publish flagged files.',
        'publication_review_flags': flags,
        'excluded_files_by_top_directory': dict(sorted(omitted.items())),
        'entries': entries,
    }
    raw = (json.dumps(report, indent=2) + '\n').encode()
    with TARGET.open('xb') as stream:
        stream.write(raw)
    print(json.dumps({'path': str(TARGET), 'sha256': digest(raw), 'candidate_files': len(entries), 'candidate_bytes': report['candidate_bytes'], 'old_repository_reference_files': report['old_repository_reference_files'], 'unmatched_to_tracked_worktree': report['candidate_files_not_byte_matched_to_current_tracked_worktree'], 'review_flags': flags}, indent=2))


if __name__ == '__main__':
    main()
