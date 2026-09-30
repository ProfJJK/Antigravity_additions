import sqlite3
import json

conn = sqlite3.connect(r'D:\__CoChem\__agentic\v4.1.2\job_board.db')
c = conn.cursor()
t_id = 'MC-CONCURRENCY-FIX'

desc = '''[ARCHITECTURAL MANDATE - PIPELINE CONCURRENCY OVERHAUL]
CONTEXT & INTENT:
The CoChem v4.1.2 pipeline demands a high-throughput multi-agent swarm architecture using the Claude Code CLI (claude.exe). The original System Requirements Specification (SRS) dictates 20 concurrent Claude agents (1 Fable 5.1 Orchestrator + 19 Opus 5.5 Workers) must run simultaneously. 

Historically, when 20 claude.exe instances were launched natively on the Windows host, the NVMe drive and Windows kernel I/O queues were completely overwhelmed, resulting in hard system crashes. To bypass this, previous agents engaged in "Counterfeit Compliance" (violating Anti-Spoofing Rule 7) by either falling back to Gemini, throttling to a single worker, or hallucinating test passes. This is unacceptable.

THE DIRECTIVE:
You (Fable 5.1) are tasked with designing and implementing an alternative concurrency structure to bypass the hardware I/O bottleneck entirely. The current prevailing theory is to leverage an Ephemeral Docker Sandbox or Linux VM with a RAM Disk (tmpfs). 
Specifically, you must explore configuring the undocumented CLAUDE_PROJECT_DIR (and potentially CLAUDE_TMPDIR/CLAUDE_CODE_TMPDIR) environment variables for each worker_daemon. By mapping these directories to isolated RAM disk paths per worker, all 600MB+ of Claude Code's SQLite caching, Git indexing, and telemetry writes will be contained in physical memory, completely isolating the Windows NVMe drive from the I/O thrashing. CRITICALLY: The global CLAUDE_CONFIG_DIR (where .credentials.json lives) MUST remain untouched on the NVMe so the Claude subscription activation does not drop.

YOUR INSTRUCTIONS:
1. RESEARCH: Do not guess. Extensively research how Node.js/Electron CLI cache redirection, Windows Directory Junctions (mklink /J), and Linux tmpfs RAM disks are typically engineered in advanced multi-agent structures.
2. CONTEXT GATHERING: You MUST query the Wiki RAG to understand the exact mechanics of the current worker_daemon.py, able_srs_director.py, and sandbox.py to see where this RAM Disk mapping logic should physically be injected.
3. ARCHITECTURE DESIGN: Plan the complete integration. Ensure the 20-agent concurrency is fully restored without blowing out the context window (rely on the N=1 WBS fracture chunking). 
4. DELEGATION: Once the plan is concretized, delegate the boilerplate implementation of the RAM Disk mount and environment variable injection to your Opus 5.5 subagents.

Design a flawless, production-ready system that honors the 20-agent SRS requirement without crashing the host NVMe.'''

c.execute('SELECT payload_json FROM jobs WHERE task_id = ?', (t_id,))
row = c.fetchone()
if row:
    data = json.loads(row[0])
    data['description'] = desc
    c.execute('UPDATE jobs SET payload_json = ? WHERE task_id = ?', (json.dumps(data), t_id))
    conn.commit()
    print('Successfully enriched MC-CONCURRENCY-FIX with full context.')
else:
    print('Task not found!')
conn.close()
