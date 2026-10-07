"""Read-only Signal ZD-8 checks without pipeline imports or authority fields."""
from __future__ import annotations

import math


def inspect_structure(conn, observed: float) -> dict:
    """Inspect actual queue invariants on an existing query-only connection.

    Availability retries count dispatches, not ordinary failures. Controller
    execution nodes have their own failure counter. Terminal FAILED records
    are diagnosed by the caller; their mere presence never authorizes repair.
    Older metadata schemas report unsupported checks rather than inventing
    missing ownership or budget evidence. No row, lease or counter is changed.
    """
    if type(observed) not in (int, float) or not math.isfinite(observed) or observed < 0:
        raise ValueError('A finite observation timestamp is required')
    if conn.execute('PRAGMA query_only').fetchone()[0] != 1:
        raise ValueError('Structural inspection requires a query-only connection')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(pipeline_jobs)')}
    checks, violations = {}, []
    if {'status', 'lease_owner'} <= columns:
        checks['inactive_lease_owner'] = 'checked'
        count = conn.execute("SELECT count(*) FROM pipeline_jobs WHERE status<>'IN_PROGRESS' "
                             'AND lease_owner IS NOT NULL').fetchone()[0]
        if count:
            violations.append({'invariant': 'inactive_lease_owner', 'count': count,
                'summary': 'Inactive jobs retain execution lease ownership'})
    else:
        checks['inactive_lease_owner'] = 'unsupported'
    if {'job_id', 'kind', 'status', 'attempts', 'max_attempts'} <= columns:
        checks['pending_exhausted_failure_budget'] = 'checked'
        route_columns = {row[1] for row in conn.execute('PRAGMA table_info(pipeline_routing_jobs)')}
        controller_columns = {row[1] for row in conn.execute('PRAGMA table_info(coding_controller_retries)')}
        routed = {'job_id', 'failure_count'} <= route_columns
        controlled = {'job_id', 'failures'} <= controller_columns
        joins = (' LEFT JOIN pipeline_routing_jobs r ON r.job_id=j.job_id' if routed else '')
        joins += (' LEFT JOIN coding_controller_retries c ON c.job_id=j.job_id' if controlled else '')
        ordinary = 'COALESCE(r.failure_count,j.attempts)' if routed else 'j.attempts'
        failures = ('CASE WHEN j.kind IN (\'CODE_TEST\',\'CODE_INTEGRATE\') THEN ' +
                    ('c.failures' if controlled else 'NULL') + ' ELSE ' + ordinary + ' END')
        count = conn.execute('SELECT count(*) FROM pipeline_jobs j' + joins +
            " WHERE j.kind NOT IN ('MACRO_PLANNING_REQUEST','CODE_REQUEST')"
            " AND j.status IN ('PENDING','PENDING_RETRY') AND " + failures + '>=j.max_attempts').fetchone()[0]
        if count:
            violations.append({'invariant': 'pending_exhausted_failure_budget', 'count': count,
                'summary': 'Pending jobs have exhausted their ordinary execution failure budget'})
    else:
        checks['pending_exhausted_failure_budget'] = 'unsupported'
    return {'state': 'violated' if violations else 'healthy', 'checks': checks,
            'violations': violations, 'observed_at': observed}
