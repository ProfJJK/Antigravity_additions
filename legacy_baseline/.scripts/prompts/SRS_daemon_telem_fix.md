# SRS: Fix daemon_telem.pid Pre-Existing Bug in cochem_kanban.py

## Status: QUEUED (deferred — to be executed when Gemini/Claude quota recovers)

## Problem
Five trigger functions in `d:\__CoChem\__agentic\cochem_kanban.py` reference
`daemon_telem.pid` at lines ~216, 249, 283, 312, 346:
  - trigger_improve()
  - trigger_srs()
  - trigger_code()
  - trigger_publish()
  - trigger_syllabus()

`daemon_telem` is never defined or imported in this file. This is a pre-existing
NameError crash (not introduced by the LLM router integration). It does NOT crash
the MCP path (the MCP tools call the kanban functions directly, not via __main__).
But running `cochem_kanban.py improve --target X` from CLI will crash with NameError.

## Required Fix
Replace all 5 occurrences of `daemon_telem.pid` with `0` (integer zero).
The Telemetry object pid=0 means "spawned by MCP, no tracked daemon PID".
The task_work_loop daemon PIDs are tracked separately in pid_table.json.

### Exact lines to patch:
- Line 216: `pid=daemon_telem.pid` → `pid=0`
- Line 249: `pid=daemon_telem.pid` → `pid=0`
- Line 283: `pid=daemon_telem.pid` → `pid=0`
- Line 312: `pid=daemon_telem.pid` → `pid=0`
- Line 346: `pid=daemon_telem.pid` → `pid=0`

## Also: Add presentation/upgrade-presentation dispatch
Lines 492-514 of cochem_kanban.py contain argparse subparsers for
`presentation` and `upgrade-presentation` but no corresponding
trigger functions or dispatch branches. Add:
```python
elif args.command == 'presentation':
    print("[presentation] trigger_presentation not yet implemented. "
          "Use the cochem-kanban MCP tool trigger_presentation_workflow instead.", file=sys.stderr)
    sys.exit(1)
elif args.command == 'upgrade-presentation':
    print("[upgrade-presentation] trigger_upgrade_presentation not yet implemented. "
          "Use trigger_presentation_upgrade MCP tool instead.", file=sys.stderr)
    sys.exit(1)
```

## Priority: LOW — does not block current MCP-based pipeline
## Executor: cochem-coder
