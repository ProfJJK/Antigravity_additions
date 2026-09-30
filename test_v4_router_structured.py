"""Red contract suite for the structured LLM router and Windows subprocess hardening.

Source SRS: dropzones/inbox_code/SRS-20260925T214325-OBJECTIVE-V4-GLOBAL-STATE-SCHEMA-INTER-A.md
Acceptance criteria: AC-16 .. AC-23 and AC-49 (Task 196.05). The router
implementation is Task 196.06.

Every test here runs against real OS primitives: the real ``llm_router.py``
source parsed with ``ast``, real Python child processes, real files under
``tmp_path``, real ``tasklist``/``taskkill`` binaries, and a real
``http.server`` bound to 127.0.0.1. Nothing is replaced or patched.

New router symbols (structured_call, run_cli, build_cli_kwargs, route_verifier,
CliProvider, StructuredOutputError, AsymmetricVerificationError) are looked up
inside each test. Against the unremediated router each test fails on its own
with AttributeError instead of breaking collection for the whole file.
"""

import ast
import inspect
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import llm_router  # noqa: E402
from v4_schemas import AuditFeedback  # noqa: E402

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _dotted_name(node):
    """Return the dotted attribute chain of an expression, e.g. 'a.b.c'."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


# --------------------------------------------------------------------------- AC-16


def test_llm_router_signature():
    """AC-16: structured_call exposes schema and max_repair_retries (default 2)."""
    sig = inspect.signature(llm_router.structured_call)
    params = sig.parameters
    assert "schema" in params, f"structured_call params: {list(params)}"
    assert "provider" in params, f"structured_call params: {list(params)}"
    assert "max_repair_retries" in params, f"structured_call params: {list(params)}"
    assert params["max_repair_retries"].default == 2


# --------------------------------------------------------------------------- AC-17


def test_llm_router_has_no_db():
    """AC-17: llm_router.py is network-only: no sqlite3 import, no sqlite3.connect."""
    router_path = ROOT / "llm_router.py"
    source = router_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(router_path))

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlite3" or alias.name.startswith("sqlite3."):
                    offenders.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "sqlite3" or module.startswith("sqlite3."):
                offenders.append(f"line {node.lineno}: from {module} import ...")
        elif isinstance(node, ast.Call):
            chain = _dotted_name(node.func)
            if chain == "sqlite3.connect" or chain.endswith(".sqlite3.connect"):
                offenders.append(f"line {node.lineno}: call {chain}")
            # Dynamic imports of sqlite3 are the same boundary violation.
            if chain in ("__import__", "importlib.import_module", "import_module"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "sqlite3":
                        offenders.append(f"line {node.lineno}: dynamic import sqlite3")

    assert not offenders, "llm_router.py must stay network-only:\n" + "\n".join(offenders)
    assert "network-only" in source, (
        "llm_router.py must keep its documented network-only / WAL-release invariant"
    )


# --------------------------------------------------------------------------- AC-18


def test_router_asymmetric_verification():
    """AC-18: the verifier never reuses the producer's provider."""
    chosen = llm_router.route_verifier(
        producer_provider="claude", available=["claude", "gemini", "ollama"]
    )
    assert chosen != "claude"
    assert chosen in {"gemini", "ollama"}, f"unexpected verifier provider: {chosen!r}"

    assert issubclass(llm_router.AsymmetricVerificationError, Exception)
    with pytest.raises(llm_router.AsymmetricVerificationError):
        llm_router.route_verifier(producer_provider="claude", available=["claude"])


# --------------------------------------------------------------------------- AC-19


def test_router_bounded_repair(tmp_path):
    """AC-19: 1 initial attempt + 2 repairs, counted by a verifier-owned file."""
    counter = tmp_path / "invocations.txt"
    script = tmp_path / "emit_invalid.py"
    script.write_text(
        "import sys\n"
        f"with open({str(counter)!r}, 'a', encoding='utf-8') as fh:\n"
        "    fh.write('invocation\\n')\n"
        "sys.stdout.write('{not json')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )

    provider = llm_router.CliProvider(argv=[sys.executable, str(script)])
    with pytest.raises(llm_router.StructuredOutputError) as excinfo:
        llm_router.structured_call(
            schema=AuditFeedback,
            provider=provider,
            prompt="Audit the attached change set and return an AuditFeedback object.",
            max_repair_retries=2,
        )

    attempts = excinfo.value.attempts
    assert len(attempts) == 3, f"expected 3 attempts, got {len(attempts)}: {attempts!r}"
    for record in attempts:
        text = str(record).lower()
        assert "validation error" in text, f"attempt lacks ValidationError text: {record!r}"

    assert counter.is_file(), "child script never ran"
    lines = counter.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, f"expected exactly 3 physical invocations, got {len(lines)}"


# --------------------------------------------------------------------------- AC-20


def test_build_cli_kwargs_and_ast():
    """AC-20: hardened kwargs, and all subprocess launches centralised in run_cli."""
    kwargs = llm_router.build_cli_kwargs(timeout=30)
    assert isinstance(kwargs, dict)
    assert kwargs["encoding"] == "utf-8"
    assert kwargs["errors"] == "replace"
    assert kwargs["shell"] is False
    assert kwargs["timeout"] == 30
    if sys.platform == "win32":
        assert (kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW) != 0

    router_path = ROOT / "llm_router.py"
    source = router_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(router_path))

    top_level_funcs = {
        n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "run_cli" in top_level_funcs, "llm_router.py must define run_cli()"
    assert "build_cli_kwargs" in top_level_funcs, "llm_router.py must define build_cli_kwargs()"

    launchers = {"run", "Popen", "call", "check_call", "check_output"}
    module_aliases = {"subprocess"}
    from_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in launchers:
                    from_aliases.add(alias.asname or alias.name)

    sites = []
    shell_true = []

    def visit(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, stack + (child.name,))
                continue
            if isinstance(child, ast.Call):
                func = child.func
                is_launch = (
                    isinstance(func, ast.Attribute)
                    and func.attr in launchers
                    and isinstance(func.value, ast.Name)
                    and func.value.id in module_aliases
                ) or (isinstance(func, ast.Name) and func.id in from_aliases)
                if is_launch:
                    sites.append((child, stack))
                for kw in child.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        shell_true.append(child.lineno)
            visit(child, stack)

    visit(tree, ())

    assert not shell_true, f"shell=True passed at llm_router.py lines {shell_true}"
    assert sites, "run_cli must launch processes via subprocess.run/subprocess.Popen"

    for call, stack in sites:
        assert "run_cli" in stack, (
            f"subprocess launch at llm_router.py:{call.lineno} is outside run_cli "
            f"(enclosing functions: {stack})"
        )
        starred = [
            kw for kw in call.keywords
            if kw.arg is None
            and isinstance(kw.value, ast.Call)
            and _dotted_name(kw.value.func).split(".")[-1] == "build_cli_kwargs"
        ]
        assert starred, (
            f"subprocess launch at llm_router.py:{call.lineno} must pass **build_cli_kwargs(...)"
        )


# --------------------------------------------------------------------------- AC-21


def test_run_cli_no_console_window():
    """AC-21: a child launched by run_cli has no console window on win32."""
    assert sys.platform == "win32", "CoChem subprocess contract targets win32 hosts"
    probe = (
        "import ctypes,sys; "
        "sys.stdout.write(str(ctypes.windll.kernel32.GetConsoleWindow()))"
    )
    result = llm_router.run_cli([sys.executable, "-c", probe], timeout=30)
    assert result.returncode == 0, f"probe failed: {result.stderr!r}"
    assert result.stdout.strip() == "0", (
        f"GetConsoleWindow returned {result.stdout!r}; CREATE_NO_WINDOW not applied"
    )


# --------------------------------------------------------------------------- AC-22


def test_run_cli_timeout_kills_tree(tmp_path):
    """AC-22: timeout tears down child and grandchild (taskkill /F /T /PID)."""
    assert sys.platform == "win32", "process-tree contract targets win32 hosts"
    pids_file = tmp_path / "pids.txt"
    script = tmp_path / "tree_sleeper.py"
    script.write_text(
        "import os, subprocess, sys, time\n"
        "grand = subprocess.Popen(\n"
        "    [sys.executable, '-c', 'import time; time.sleep(60)'],\n"
        "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
        "    stderr=subprocess.DEVNULL, encoding='utf-8', shell=False,\n"
        "    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),\n"
        ")\n"
        f"with open({str(pids_file)!r}, 'w', encoding='utf-8') as fh:\n"
        "    fh.write(f'{os.getpid()}\\n{grand.pid}\\n')\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    def pid_report(pid):
        """Run the real tasklist binary for one PID and return its stdout."""
        listing = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=30,
            creationflags=NO_WINDOW,
        )
        return listing.stdout or ""

    pids = []
    try:
        timeout_types = (TimeoutError, llm_router.StructuredOutputError)
        started = time.monotonic()
        with pytest.raises(timeout_types):
            llm_router.run_cli([sys.executable, str(script)], timeout=2)
        elapsed = time.monotonic() - started
        assert elapsed < 10, f"run_cli took {elapsed:.1f}s to honour timeout=2"

        assert pids_file.is_file(), "child never recorded its PIDs"
        pids = [
            int(line) for line in pids_file.read_text(encoding="utf-8").split() if line.strip()
        ]
        assert len(pids) == 2, f"expected child + grandchild PIDs, got {pids}"

        for pid in pids:
            deadline = time.monotonic() + 5
            report = pid_report(pid)
            while "No tasks are running" not in report and time.monotonic() < deadline:
                time.sleep(0.2)
                report = pid_report(pid)
            assert "No tasks are running" in report, (
                f"PID {pid} survived run_cli timeout (taskkill /F /T not applied):\n{report}"
            )
    finally:
        # Hygiene for failing runs only: never leave 60s sleepers behind.
        if not pids and pids_file.is_file():
            pids = [
                int(line)
                for line in pids_file.read_text(encoding="utf-8").split()
                if line.strip()
            ]
        for pid in pids:
            if "No tasks are running" not in pid_report(pid):
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    encoding="utf-8",
                    errors="replace",
                    shell=False,
                    timeout=30,
                    creationflags=NO_WINDOW,
                )


# --------------------------------------------------------------------------- AC-23


def test_run_cli_large_prompt(tmp_path):
    """AC-23: prompts >= 40,000 chars reach the child through a temp file."""
    schema_text = json.dumps(AuditFeedback.model_json_schema(), indent=2)
    header = (
        "Return a single JSON object matching this AuditFeedback schema:\n"
        + schema_text
        + "\nThermodynamic note: \u0394G\u00b0 = \u2212RT ln K\n"
        + "Router source under review follows.\n"
    )
    router_text = (ROOT / "llm_router.py").read_text(encoding="utf-8")
    assert router_text, "llm_router.py is empty"
    prompt = header
    while len(prompt) < 40_000:
        prompt += router_text
    prompt = prompt.replace("\r", "")
    assert len(prompt) >= 40_000
    assert schema_text in prompt

    script = tmp_path / "measure_prompt.py"
    script.write_text(
        "import sys\n"
        "path = sys.argv[1]\n"
        "with open(path, encoding='utf-8') as fh:\n"
        "    text = fh.read()\n"
        "sys.stdout.write(str(len(text)) + '\\n' + path + '\\n')\n",
        encoding="utf-8",
    )

    try:
        result = llm_router.run_cli([sys.executable, str(script)], prompt=prompt, timeout=60)
    except OSError as exc:
        pytest.fail(f"run_cli raised OSError (winerror={getattr(exc, 'winerror', None)}): {exc}")

    assert result.returncode == 0, f"child failed: {result.stderr!r}"
    lines = result.stdout.strip().splitlines()
    assert len(lines) >= 2, f"unexpected child output: {result.stdout!r}"
    printed_len = int(lines[0])
    argv_path = Path(lines[1].strip()).resolve()

    assert printed_len == len(prompt), f"child read {printed_len} chars, sent {len(prompt)}"
    temp_root = Path(tempfile.gettempdir()).resolve()
    assert argv_path.is_relative_to(temp_root), (
        f"prompt file {argv_path} is not under {temp_root}"
    )
    assert argv_path != script.resolve(), "argv[1] must be the prompt file, not the script"


# --------------------------------------------------------------------------- AC-49


def test_router_ollama_native_format(tmp_path):
    """AC-49: Ollama structured calls hit /api/chat with format=<JSON schema>."""
    bodies_file = tmp_path / "bodies.jsonl"
    port_file = tmp_path / "port.txt"
    server_log = tmp_path / "server.log"
    server_script = tmp_path / "ollama_capture_server.py"
    server_script.write_text(
        "import http.server, json\n"
        f"BODIES = {str(bodies_file)!r}\n"
        f"PORT_FILE = {str(port_file)!r}\n"
        "class Handler(http.server.BaseHTTPRequestHandler):\n"
        "    def do_POST(self):\n"
        "        length = int(self.headers.get('Content-Length', '0'))\n"
        "        raw = self.rfile.read(length).decode('utf-8')\n"
        "        with open(BODIES, 'a', encoding='utf-8') as fh:\n"
        "            fh.write(json.dumps({'path': self.path, 'body': raw}) + '\\n')\n"
        "        reply = json.dumps({\n"
        "            'model': 'capture',\n"
        "            'created_at': '2026-09-26T00:00:00Z',\n"
        "            'message': {'role': 'assistant', 'content': '{}'},\n"
        "            'response': '{}',\n"
        "            'done': True,\n"
        "        }).encode('utf-8')\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.send_header('Content-Length', str(len(reply)))\n"
        "        self.end_headers()\n"
        "        self.wfile.write(reply)\n"
        "server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)\n"
        "with open(PORT_FILE + '.tmp', 'w', encoding='utf-8') as fh:\n"
        "    fh.write(str(server.server_address[1]))\n"
        "import os\n"
        "os.replace(PORT_FILE + '.tmp', PORT_FILE)\n"
        "server.serve_forever()\n",
        encoding="utf-8",
    )

    with open(server_log, "w", encoding="utf-8") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, str(server_script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=log_fh,
            encoding="utf-8",
            shell=False,
            creationflags=NO_WINDOW,
        )
        try:
            deadline = time.monotonic() + 15
            while not port_file.is_file():
                assert proc.poll() is None, (
                    "capture server exited early:\n" + server_log.read_text(encoding="utf-8")
                )
                assert time.monotonic() < deadline, "capture server never published its port"
                time.sleep(0.05)
            port = int(port_file.read_text(encoding="utf-8").strip())

            provider = llm_router.OllamaProvider(
                model="capture-model", base_url=f"http://127.0.0.1:{port}"
            )
            try:
                llm_router.structured_call(
                    schema=AuditFeedback,
                    provider=provider,
                    prompt="Audit the attached change set and return an AuditFeedback object.",
                    max_repair_retries=0,
                    timeout=30,
                )
            except llm_router.StructuredOutputError:
                # The capture server replies '{}'; whether that validates depends
                # on AuditFeedback's required fields. Only the request is under test.
                pass_through = True
                assert pass_through
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    assert bodies_file.is_file(), "Ollama provider never reached the capture server"
    records = [
        json.loads(line)
        for line in bodies_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(records) == 1, f"expected exactly one request, got {len(records)}"
    expected_schema = AuditFeedback.model_json_schema()
    for record in records:
        assert record["path"] == "/api/chat", f"request targeted {record['path']!r}"
        body = json.loads(record["body"])
        assert body.get("format") == expected_schema, (
            f"'format' is not AuditFeedback.model_json_schema(): {body.get('format')!r}"
        )
        assert isinstance(body.get("messages"), list) and body["messages"], (
            "/api/chat body must carry a non-empty messages list"
        )
