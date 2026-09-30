import json, sys
sys.path.insert(0, '.')
from v2.task_planning_orchestra import Dossier

raw = r'''{
  "lane": "api_dependencies",
  "findings": [
    ".scripts/task_work_loop.py contains the _artifact_protocol function at line 1548, which serves as the immutable primary chunking protocol delivering 20-100 line semantic slices via Fracture Manifests.",
    "The environment runs Python 3.14.7 with key dependencies installed: filelock 3.32.2, pytest 9.1.1, pydantic 2.13.4, mendeleev 1.2.0, numpy 2.4.6, scipy 1.18.0, ase 3.29.0, jax 0.11.0, h5py 3.16.0, and hdf5plugin 7.1.0.",
    "llm_router and claude_subscription_manager are listed as NOT INSTALLED as distribution packages, but llm_router.py physically exists as a 719-line writable script in the repository root (sha256=e2643db3e5f4f34c).",
    "Pipeline core files v2/task_planning_orchestra.py (1597 lines, sha256=1f3168fa89bd1656) and v2/MODEL_REGISTRY_V2.py (323 lines, sha256=4a6db252d6ebad58) are verified as READONLY and unauthorized for direct modification.",
    "The workspace root D:\\__CoChem\\__agentic is explicitly verified as not currently a git repository, requiring git initialization as part of the V4 upgrade bootstrap.",
    "Concurrency and daemon architecture components are present as writable scripts: .scripts/kanban_v3_daemon.py (149 lines), .scripts/cochem_meta_auditor.py (221 lines), .scripts/concurrency_guard.py (433 lines), and .scripts/resource_governor.py (205 lines), with standard library sqlite3 and subprocess utilized.",
    "The canonical database is designated as cochem_kanban.db rather than cochem_kanban_v3.db.",
    "No physical evidence was provided in the input regarding Docker Desktop daemon availability, Windows NTFS bind mount behaviors with --user, git configuration states, or Ollama service reachability on localhost:11434."
  ],
  "evidence": [
    {
      "path": ".scripts/task_work_loop.py",
      "line": 1548,
      "snippet": "_artifact_protocol (task_work_loop.py line 1548) is the PRIMARY chunk protocol. Agents receive 20-100 line semantic slices via Fracture Manifests. Docker/Git wraps AROUND this, not replaces it."
    },
    {
      "path": "<physical_evidence>.installed",
      "line": 0,
      "snippet": "\"python\": \"3.14.7\", \"installed\": {\"filelock\": \"3.32.2\", \"pytest\": \"9.1.1\", \"pydantic\": \"2.13.4\", \"numpy\": \"2.4.6\", \"mendeleev\": \"1.2.0\", \"jax\": \"0.11.0\", \"ase\": \"3.29.0\", \"h5py\": \"3.16.0\", \"hdf5plugin\": \"7.1.0\"}"
    },
    {
      "path": "llm_router.py",
      "line": 1,
      "snippet": "llm_router.py | 719  lines | sha256=e2643db3e5f4f34c | WRITABLE"
    },
    {
      "path": "<physical_evidence>.installed",
      "line": 0,
      "snippet": "\"llm_router\": \"NOT INSTALLED (or import name != dist name)\", \"claude_subscription_manager\": \"NOT INSTALLED (or import name != dist name)\""
    },
    {
      "path": "v2/task_planning_orchestra.py",
      "line": 1,
      "snippet": "v2/task_planning_orchestra.py | 1597 lines | sha256=1f3168fa89bd1656 | READONLY"
    },
    {
      "path": "v2/MODEL_REGISTRY_V2.py",
      "line": 1,
      "snippet": "v2/MODEL_REGISTRY_V2.py | 323  lines | sha256=4a6db252d6ebad58 | READONLY"
    },
    {
      "path": "D:\\__CoChem\\__agentic",
      "line": 0,
      "snippet": "D:\\__CoChem\\__agentic is NOT currently a git repo. Git init is part of the V4 work."
    },
    {
      "path": ".scripts/kanban_v3_daemon.py",
      "line": 1,
      "snippet": ".scripts/kanban_v3_daemon.py | 149  lines | sha256=50b66df1db5199f5 | WRITABLE"
    },
    {
      "path": ".scripts/cochem_meta_auditor.py",
      "line": 1,
      "snippet": ".scripts/cochem_meta_auditor.py | 221  lines | sha256=73dfdd9cf81a74f0 | WRITABLE"
    },
    {
      "path": ".scripts/concurrency_guard.py",
      "line": 1,
      "snippet": ".scripts/concurrency_guard.py | 433  lines | sha256=cff1b214ee2a5e3f | WRITABLE"
    },
    {
      "path": ".scripts/resource_governor.py",
      "line": 1,
      "snippet": ".scripts/resource_governor.py | 205  lines | sha256=db137e2aa335e324 | WRITABLE"
    },
    {
      "path": "cochem_kanban.db",
      "line": 0,
      "snippet": "Database is cochem_kanban.db (NOT cochem_kanban_v3.db). Resolve this ambiguity."
    }
  ],
  "risks": [
    "Docker execution blind spot: No evidence confirms Docker engine endpoint availability (npipe vs WSL2), daemon liveness, or Windows bind-mount ACL behavior under non-root users. If containers are invoked without pre-flight validation, execution phases will fail at runtime.",
    "Git index.lock race conditions on Windows: Multiple concurrent workers operating on a newly initialized Git repository without a host-side serialized lock gate will cause index.lock collisions and corrupted git state on NTFS.",
    "Line-ending and path length truncation: Absence of verified core.autocrlf and core.longpaths configurations on Windows risks mangling chunk patch applications and failing SHA256 integrity verifications.",
    "Uninstalled local module import failures: Because llm_router.py is not an installed package, any subprocess or container invoked without PYTHONPATH pointing to the host repository root will raise ModuleNotFoundError.",
    "Python 3.14 runtime edge cases: Running Python 3.14.7 may cause binary incompatibility or unexpected behavior if worker container images utilize differing Python minor versions (e.g., 3.11/3.12).",
    "Silent SQLite database creation: Accessing cochem_kanban.db without explicit URI read-write mode (mode=rw) risks silently creating an unpopulated database file if path resolution drifts."
  ],
  "recommendations": [
    "Mandate a pre-flight probe in the SRS: Validate docker info, docker inspect capabilities, and git --version prior to initiating the V4 bootstrap sequence.",
    "Configure Git explicitly upon initialization: Immediately after git init, set git config core.autocrlf false, git config core.longpaths true, and configure local user.name and user.email to ensure deterministic commit hashes.",
    "Serialize all Git mutations via filelock: Utilize installed filelock (3.32.2) to gate all git add, git commit, and git checkout operations through a host-side mutex to prevent index.lock conflicts.",
    "Isolate container execution with strict read-only mounts: Mount only the chunk target directory read-write into containers; mount .git and read-only core files (:ro), enforce network=none, and keep all LLM router calls host-side with PYTHONPATH=D:\\__CoChem\\__agentic.",
    "Enforce SQLite WAL and URI mode: Open cochem_kanban.db using file:cochem_kanban.db?mode=rw with PRAGMA journal_mode=WAL; and PRAGMA busy_timeout=5000; to fail immediately on missing database files and prevent Windows lock contention.",
    "Enforce Windows subprocess flags: All subprocess calls spawning Docker, Git, or Python daemons must specify creationflags=subprocess.CREATE_NO_WINDOW (0x08000000) to prevent visible console popup windows."
  ],
  "unanswered_questions": [
    "Docker Desktop engine configuration: Is Docker running via named pipe (npipe:////./pipe/docker_engine) or WSL2 socket, do Windows NTFS bind mounts of D:\\ paths function with --user non-root without entrypoint chown, and what exact inspect/events fields are exposed for label filtering?",
    "Host Git environment: What is the installed Git version, what are the host's existing global core.autocrlf and core.longpaths settings, and what specific git fsck flags should be enforced during CI auditing?",
    "Ollama service and provider status: Is Ollama currently running on localhost:11434 with sentinel models pulled, and what exact HTTP status codes and response bodies are returned across provider fallback chains during key rejection to test AC-40 deterministically?"
  ],
  "confidence": 0.45
}'''

try:
    d = Dossier.model_validate_json(raw)
    print("VALIDATION SUCCESS")
    print(f"lane: {d.lane}")
    print(f"findings count: {len(d.findings)}")
    print(f"evidence count: {len(d.evidence)}")
    print(f"risks count: {len(d.risks)}")
    print(f"recommendations count: {len(d.recommendations)}")
    print(f"unanswered_questions count: {len(d.unanswered_questions)}")
    print(f"confidence: {d.confidence}")
except Exception as e:
    print(f"VALIDATION ERROR: {e}")
