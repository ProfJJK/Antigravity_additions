"""Connect the operator's Codex to the frozen r3 stdio client. No controller/model calls."""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time
import tomllib
import uuid

RUNTIME = Path(r'C:\Program Files\CoChem\Pipeline4.2.7-windows-20261007-r3')
PYTHON = RUNTIME / '.venv/Scripts/python.exe'
OPERATOR = Path(r'C:\Users\ansac')
CODEX_CONFIG = OPERATOR / '.codex/config.toml'
CLIENT_CONFIG = OPERATOR / 'CoChem427/pipeline-client-r3.json'
WORK = Path(r'C:\Users\ansac\Documents\Codex\2026-10-06\the-github-repository-is-located-at\windows-deployment-next')
SERVER = 'cochem-pipeline'
EXPECTED_TOOLS = {
 'pipeline_submit','pipeline_status','pipeline_health','pipeline_cancel',
 'pipeline_resume_routing','pipeline_operator_view','pipeline_provider_preflight_submit',
 'knowledge_search','knowledge_read','knowledge_status','pipeline_projects',
 'pipeline_code','pipeline_code_status','pipeline_code_cancel','pipeline_code_resume',
}
PINS = {
 PYTHON: '560b9ef7d856608ab8da02ded2dc8a1951ad1f424c382c0ec6a698874165a18e',
 RUNTIME/'pipeline.json': '135cd9eccf9efcdbc910679bde83b571913e731a4041ece62f60e42442f1709c',
 RUNTIME/'install-after.json': '3b613128192ac44a0a3d9c4b4476fb4c47541edb7563541b9bbf18f84f9a90f6',
 RUNTIME/'.venv/Lib/site-packages/cochem_pipeline/server.py': '04e61fb6e4abd0e04eb3f7ca37e32727f7d14d8bfe8de3157f69763ec224240f',
}
def digest(data):
    return hashlib.sha256(data).hexdigest()

def ordinary_path(path, *, may_be_absent=False):
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            if may_be_absent and part == path:
                continue
            raise ValueError('Required path missing') from None
        if getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError('Reparse path refused')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('Hard-linked file refused')

def pin_runtime():
    for path, wanted in PINS.items():
        ordinary_path(path)
        if digest(path.read_bytes()) != wanted:
            raise ValueError('Frozen runtime commitment differs')
    from cochem_pipeline.deployment_revision import verify_installed_revision
    report = verify_installed_revision(RUNTIME/'source', RUNTIME/'.venv/Lib/site-packages', RUNTIME/'source')
    if not report['verified'] or report['source_sha256'] != '309eb48d9b1c43179ae1f0784d0139357168314f8312a64fb84dbd8380d44eb4':
        raise ValueError('Installed revision differs')
    return report

def entry():
    return {'command':str(PYTHON), 'args':['-I','-B','-m','cochem_pipeline','mcp','--client-config',str(CLIENT_CONFIG)],
            'enabled':True, 'startup_timeout_sec':20, 'tool_timeout_sec':60}

def proposed(existing):
    before = tomllib.loads(existing.decode('utf-8-sig'))
    servers = before.get('mcp_servers', {})
    if SERVER in servers:
        if servers[SERVER] == entry():
            return existing, before, False
        raise ValueError('Existing CoChem entry conflicts; preserved')
    suffix = ('\n\n# CoChem Windows pipeline: controller owns execution and Chapter 06 routing.\n'
              '[mcp_servers."cochem-pipeline"]\n'
              "command = '" + str(PYTHON) + "'\n"
              "args = ['-I', '-B', '-m', 'cochem_pipeline', 'mcp', '--client-config', '" + str(CLIENT_CONFIG) + "']\n"
              'enabled = true\nstartup_timeout_sec = 20\ntool_timeout_sec = 60\n').encode('utf-8')
    candidate = existing + suffix
    parsed = tomllib.loads(candidate.decode('utf-8-sig'))
    if parsed['mcp_servers'].pop(SERVER) != entry():
        raise ValueError('Candidate MCP entry differs')
    if parsed != before:
        raise ValueError('Candidate changes unrelated settings')
    return candidate, before, True

async def probe(stderr_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from anyio import fail_after
    with stderr_path.open('x', encoding='utf-8') as errlog:
        with fail_after(30):
            async with stdio_client(StdioServerParameters(command=str(PYTHON), args=entry()['args']),errlog=errlog) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    initialized = await session.initialize()
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    if names != EXPECTED_TOOLS:
                        raise ValueError('Actual stdio tool inventory differs')
                    return {'protocol_version':initialized.protocolVersion,
                            'server_name':initialized.serverInfo.name,
                            'server_version':initialized.serverInfo.version,
                            'tool_names':sorted(names),'tool_count':len(names),
                            'instructions_sha256':digest((initialized.instructions or '').encode('utf-8')),
                            'requests_sent':['initialize','notifications/initialized','tools/list'],
                            'controller_tool_calls':0,'model_jobs_submitted':0}

def create_json(path, value):
    ordinary_path(path, may_be_absent=True)
    with path.open('xb') as stream:
        stream.write((json.dumps(value,indent=2,sort_keys=True)+'\n').encode())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--connect', action='store_true')
    args = parser.parse_args()
    report = {'schema':'cochem-codex-stdio-connection/1','activation_ready':False,
              'daemon_started':False,'credentials_modified':False,'databases_opened':False,
              'controller_tool_calls':0,'model_jobs_submitted':0}
    revision = pin_runtime()
    ordinary_path(CODEX_CONFIG)
    ordinary_path(CLIENT_CONFIG, may_be_absent=True)
    expected_client = {'port':47824,'token_file':str(OPERATOR/'CoChem427/controller.token')}
    if CLIENT_CONFIG.exists() and json.loads(CLIENT_CONFIG.read_text(encoding='utf-8-sig')) != expected_client:
        raise ValueError('Existing operator client config conflicts; preserved')
    existing = CODEX_CONFIG.read_bytes()
    candidate, before, changed = proposed(existing)
    report.update({'installed_revision':revision,'codex_config_path':str(CODEX_CONFIG),
                   'client_config_path':str(CLIENT_CONFIG),'existing_mcp_server_names':list(before.get('mcp_servers',{})),
                   'codex_config_before_sha256':digest(existing),'entry':entry()})
    if not args.connect:
        report['status']='CONNECTION_PREVIEW_ONLY'
        print(json.dumps(report,indent=2)); return
    archive = WORK/('codex-connection-'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())+'-'+uuid.uuid4().hex[:8])
    archive.mkdir(exist_ok=False)
    if not CLIENT_CONFIG.exists():
        create_json(CLIENT_CONFIG,expected_client)
    report['stdio']=asyncio.run(probe(archive/'stdio-stderr.log'))
    # A verified actual handshake precedes registering the server. Preserve all
    # unrelated TOML bytes, and refuse drift between inspection and append.
    if changed:
        backup = CODEX_CONFIG.with_name('config.toml.before-cochem-'+archive.name)
        ordinary_path(backup,may_be_absent=True)
        with backup.open('xb') as stream:
            stream.write(existing)
        report['config_backup_path']=str(backup)
        with CODEX_CONFIG.open('r+b') as stream:
            if stream.read() != existing:
                raise ValueError('Codex config changed concurrently; preserved')
            stream.seek(0,os.SEEK_END)
            stream.write(candidate[len(existing):]); stream.flush(); os.fsync(stream.fileno())
    if CODEX_CONFIG.read_bytes() != candidate:
        raise ValueError('Registered config verification differs')
    if json.loads(CLIENT_CONFIG.read_text(encoding='utf-8-sig')) != expected_client:
        raise ValueError('Operator client binding changed')
    report.update({'status':'CODEX_MCP_REGISTERED_STDIO_VERIFIED_CONTROLLER_NOT_STARTED',
                   'codex_config_after_sha256':digest(candidate),'unrelated_config_bytes_preserved':True,
                   'source_sha256':digest(Path(__file__).read_bytes()),'archive_path':str(archive),
                   'running_chat_tool_catalog_reloaded':False})
    create_json(archive/'connection.json',report)
    print(json.dumps(report,indent=2))

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'schema':'cochem-codex-stdio-connection/1','status':'CONNECTION_HELD',
                         'error_type':type(exc).__name__,'automatic_retry_allowed':False,
                         'activation_ready':False}),file=sys.stderr)
        raise SystemExit(2) from None