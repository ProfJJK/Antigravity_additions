# CoChem Architecture Standard: RAM Disk Reparse Point

**Status**: Adopted (Pipeline 3.0.0)
**Context**: The 60-concurrent agent Swarm was causing NVMe drive failure and unrecoverable pipeline crashes due to extreme I/O loads from `pytest` and execution logs. 

## The Solution
Instead of mapping a virtual RAM disk to an arbitrary drive letter (like `R:\`) which requires updating every Python script and agent, we use **NTFS Reparse Points** (Volume Mount Points) to seamlessly mount an ImDisk RAM volume directly over the physical workspace directory (`D:\__CoChem\__agentic\.scripts\tdd_runs`).

### Benefits
1. **Zero-Code Integration**: Scripts continue writing to the exact same paths (`D:\...\tdd_runs`). Python and OS level operations treat it as a standard directory. No path refactoring is needed.
2. **NVMe Preservation**: Tens of thousands of read/write ops per second from 60 concurrent test beds hit the RAM instead of the physical SSD layer.
3. **Sandbox Compliance**: Because the paths originate from `D:\`, LLM sandbox rules (which restrict access outside the workspace) usually remain happy. *(Exception: see Claude CLI Sandbox rule below).*

### Architectural Rules for Future Development
- **Do NOT** use `R:\` or arbitrary drive letters for temporary testing runs. Always mount the RAM disk to `D:\__CoChem\__agentic\.scripts\tdd_runs`.
- **Pre-requisite for Mounting**: Windows requires the target folder to be COMPLETELY EMPTY before mounting a volume to it. The setup script MUST backup and clear the folder first.
- **Claude CLI Sandbox Trap**: The Claude CLI (Node.js) uses `fs.realpath()`. When checking sandbox compliance, it resolves the Reparse Point to `\\?\Volume{...}\` which is *outside* the `D:\` tree. **Rule:** Never place Claude LLM prompt payload temp files on the RAM disk. Place them in a standard physical directory like `D:\__CoChem\__agentic\.scripts\llm_temp` to ensure the Claude CLI can read them.
