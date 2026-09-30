import sys
import json
import os
import datetime
import subprocess
import tempfile
import shutil
import re
import math
try:
    import psutil
except ImportError:
    psutil = None

DEFAULT_BUDGET_FILE = r"C:\Users\ansac\.gemini\config\credit_budget.json"
DEFAULT_LOG_FILE = r"C:\Users\ansac\.gemini\config\scripts\credit_guardian.log"

def get_budget_file_path() -> str:
    return os.getenv("CREDIT_BUDGET_FILE", DEFAULT_BUDGET_FILE)

def get_log_file_path() -> str:
    return os.getenv("CREDIT_GUARDIAN_LOG_FILE", DEFAULT_LOG_FILE)

# Backward-compatibility aliases
BUDGET_FILE = get_budget_file_path()
LOG_FILE = get_log_file_path()

def ensure_parent_directory(filepath: str) -> None:
    dirname = os.path.dirname(filepath)
    if dirname:
        os.makedirs(dirname, exist_ok=True)

def log(msg: str) -> None:
    try:
        log_path = get_log_file_path()
        ensure_parent_directory(log_path)
        now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{now_str}] {msg}\n")
    except Exception as e:
        sys.stderr.write(f"Logging error: {e}\n")

def ensure_utc_datetime(dt: datetime.datetime = None) -> datetime.datetime:
    """
    Normalizes any datetime object to a UTC-aware datetime.
    If dt is None or not a datetime instance, returns current UTC datetime.
    If dt is naive, attaches UTC timezone info.
    If dt is aware in another timezone, converts to UTC.
    """
    if dt is None or not isinstance(dt, datetime.datetime):
        return datetime.datetime.now(datetime.timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)

def sanitize_token_count(val, default: int = 0) -> int:
    """
    Sanitizes token counts to non-negative integers.
    Coerces numeric strings and floats. Rejects None, bool, invalid strings, negative numbers,
    infinity (inf/-inf), NaN, and overflowing float values.
    """
    if val is None or isinstance(val, bool):
        return default
    try:
        if isinstance(val, float):
            if math.isinf(val) or math.isnan(val):
                return default
            res = int(val)
            return res if res >= 0 else default
        res = int(val)
        return res if res >= 0 else default
    except (ValueError, TypeError, OverflowError):
        try:
            f = float(val)
            if math.isinf(f) or math.isnan(f):
                return default
            res = int(f)
            return res if res >= 0 else default
        except (ValueError, TypeError, OverflowError):
            return default

def sanitize_budget_limit(val, default: int = 2000000) -> int:
    """
    Sanitizes 5-hour max token budget limit to a positive integer.
    Coerces numeric strings and floats. Rejects None, bool, invalid strings, negative/zero numbers,
    infinity (inf/-inf), NaN, and overflowing float values.
    """
    if val is None or isinstance(val, bool):
        return default
    try:
        if isinstance(val, float):
            if math.isinf(val) or math.isnan(val):
                return default
            res = int(val)
            return res if res > 0 else default
        res = int(val)
        return res if res > 0 else default
    except (ValueError, TypeError, OverflowError):
        try:
            f = float(val)
            if math.isinf(f) or math.isnan(f):
                return default
            res = int(f)
            return res if res > 0 else default
        except (ValueError, TypeError, OverflowError):
            return default

def get_budget() -> dict:
    default_budget = {
        "max_tokens_per_5_hours": 2000000,
        "usage_events": [],
        "conversations": {}
    }
    budget_path = get_budget_file_path()
    if not os.path.exists(budget_path):
        return default_budget

    try:
        with open(budget_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("Budget data is not a JSON object")

            # 1. Sanitize max_tokens_per_5_hours
            data["max_tokens_per_5_hours"] = sanitize_budget_limit(data.get("max_tokens_per_5_hours"))

            # 2. Sanitize usage_events list and event token values
            raw_events = data.get("usage_events")
            clean_events = []
            if isinstance(raw_events, list):
                for event in raw_events:
                    if not isinstance(event, dict):
                        continue
                    ts = event.get("timestamp")
                    if not isinstance(ts, str) or not ts.strip():
                        continue
                    tokens_val = sanitize_token_count(event.get("tokens"))
                    clean_events.append({
                        "timestamp": ts,
                        "tokens": tokens_val
                    })
            data["usage_events"] = clean_events

            # 3. Sanitize conversations dict
            raw_convs = data.get("conversations")
            clean_convs = {}
            if isinstance(raw_convs, dict):
                for k, v in raw_convs.items():
                    if isinstance(k, str):
                        clean_convs[k] = sanitize_token_count(v)
            data["conversations"] = clean_convs

            return data
    except Exception as e:
        log(f"ERROR: Budget file '{budget_path}' is corrupted or unreadable: {e}")
        try:
            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
            corrupt_backup = f"{budget_path}.corrupt.{timestamp}"
            shutil.copy2(budget_path, corrupt_backup)
            log(f"Preserved corrupted budget file at '{corrupt_backup}'")
        except Exception as backup_err:
            log(f"Failed to preserve corrupted budget file: {backup_err}")
        return default_budget

def save_budget(budget: dict) -> None:
    budget_path = get_budget_file_path()
    ensure_parent_directory(budget_path)
    target_dir = os.path.dirname(budget_path) or "."

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=target_dir, delete=False, encoding="utf-8", suffix=".tmp") as f:
            temp_path = f.name
            json.dump(budget, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, budget_path)
    except Exception as e:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        log(f"ERROR: Failed to save budget atomically to '{budget_path}': {e}")
        raise

def parse_utc_timestamp(ts_str: str) -> datetime.datetime:
    """
    Parses an ISO format timestamp string into a UTC-aware datetime object.
    Falls back to current UTC time if parsing fails or input is invalid.
    """
    if not ts_str or not isinstance(ts_str, str):
        return datetime.datetime.now(datetime.timezone.utc)

    cleaned_ts = ts_str.strip().replace("Z", "+00:00").replace("z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(cleaned_ts)
    except Exception:
        return datetime.datetime.now(datetime.timezone.utc)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)

    return dt

def prune_old_events(usage_events: list, now_utc: datetime.datetime = None) -> list:
    now_utc = ensure_utc_datetime(now_utc)
    cutoff_time = now_utc - datetime.timedelta(hours=5)
    valid_events = []
    if not isinstance(usage_events, list):
        return []
    for event in usage_events:
        if not isinstance(event, dict) or "timestamp" not in event or "tokens" not in event:
            continue
        event_dt = parse_utc_timestamp(event["timestamp"])
        if event_dt > cutoff_time:
            event_copy = dict(event)
            event_copy["tokens"] = sanitize_token_count(event_copy.get("tokens"))
            valid_events.append(event_copy)
    return valid_events

def calculate_delta_tokens(current_tokens: int, last_seen_tokens: int) -> int:
    """
    Calculates token consumption delta for a session.
    If current_tokens < last_seen_tokens, the transcript was reset/truncated,
    so current_tokens represents total tokens in the new session.
    """
    current_tokens = sanitize_token_count(current_tokens)
    last_seen_tokens = sanitize_token_count(last_seen_tokens)

    if current_tokens >= last_seen_tokens:
        return current_tokens - last_seen_tokens
    else:
        return current_tokens

def calculate_resume_time(valid_events: list, max_budget: int, now_utc: datetime.datetime = None) -> datetime.datetime:
    """
    Iteratively simulates dropping oldest usage events until remaining cumulative
    tokens drop below 95% of max_budget (remaining budget > 5%).
    Sets resume_dt = dropped_event.timestamp + 5 hours + 1 minute.
    Fallback: now_utc + 30 minutes.
    """
    now_utc = ensure_utc_datetime(now_utc)
    safe_max_budget = sanitize_budget_limit(max_budget)
    safe_max_usage = int(safe_max_budget * 0.95)

    if not isinstance(valid_events, list) or not valid_events:
        return now_utc + datetime.timedelta(minutes=30)

    sorted_events = sorted(
        valid_events,
        key=lambda e: parse_utc_timestamp(e.get("timestamp", "")) if isinstance(e, dict) else now_utc
    )

    cumulative_tokens = sum(sanitize_token_count(e.get("tokens", 0)) for e in sorted_events if isinstance(e, dict))

    if cumulative_tokens <= safe_max_usage:
        return now_utc + datetime.timedelta(minutes=30)

    target_resume_dt = None

    for event in sorted_events:
        if not isinstance(event, dict):
            continue
        tokens = sanitize_token_count(event.get("tokens", 0))
        cumulative_tokens -= tokens

        event_dt = parse_utc_timestamp(event.get("timestamp", ""))
        drop_time = event_dt + datetime.timedelta(hours=5, minutes=1)

        if cumulative_tokens <= safe_max_usage:
            target_resume_dt = drop_time
            break

    if target_resume_dt is None or target_resume_dt <= now_utc:
        return now_utc + datetime.timedelta(minutes=30)

    return target_resume_dt

def is_conv_id_match(conv_id: str, cmdline_list: list, cmdline_str: str) -> bool:
    r"""
    Safely determines whether a process command line matches a specific conversation ID.
    Enforces minimum length >= 4, excludes generic/invalid IDs ('unknown', 'none', 'null', 'false', 'true', 'undefined'),
    checks for exact flag arguments (--conversation-id, --conv-id, conversationId=),
    uses regex negative lookbehinds/lookaheads for path separators (\, /) and alphanumerics,
    and excludes inline python execution commands (python -c ..., -m pytest) unless conv_id
    is explicitly passed as a distinct argument flag.
    """
    if not conv_id or not isinstance(conv_id, str):
        return False

    cleaned = conv_id.strip()
    if len(cleaned) < 4 or cleaned.lower() in ("unknown", "none", "null", "false", "true", "undefined"):
        return False

    lower_conv_id = cleaned.lower()

    if not cmdline_list and not cmdline_str:
        return False

    # Normalize cmdline_list and cmdline_str
    clean_list = [str(arg) for arg in cmdline_list if arg is not None] if isinstance(cmdline_list, list) else []
    
    if not clean_list and cmdline_str and isinstance(cmdline_str, str):
        try:
            import shlex
            clean_list = shlex.split(cmdline_str)
        except Exception:
            clean_list = cmdline_str.split()

    clean_str = cmdline_str if (cmdline_str and isinstance(cmdline_str, str)) else " ".join(clean_list)
    if not clean_list and not clean_str.strip():
        return False

    # Recognized flag keys for conversation ID
    FLAG_NAMES = {
        "--conversation-id", "--conversation_id", "--conversationid",
        "--conv-id", "--conv_id", "-conv-id", "-conversation-id"
    }

    # Tier 1: Structured Flag & JSON Key Match (Exact Value Equality)
    n = len(clean_list)
    for i in range(n):
        token = clean_list[i].strip()
        token_lower = token.lower()

        # Separate flag & value: --conversation-id conv-1234
        if token_lower in FLAG_NAMES:
            if i + 1 < n:
                val = clean_list[i+1].strip().strip('"\'')
                if val.lower() == lower_conv_id:
                    return True

        # Equals flag: --conversation-id=conv-1234 or conversationId=conv-1234
        if "=" in token:
            key, val = token.split("=", 1)
            key_clean = key.strip().lower()
            val_clean = val.strip().strip('"\'')
            if key_clean in FLAG_NAMES or key_clean in {"conversationid", "conversation_id", "conv_id", "convid"} or key_clean.startswith("--conv"):
                if val_clean.lower() == lower_conv_id:
                    return True

        # Colon flag / Dict key: conversationId:"conv-1234"
        if ":" in token and any(k in token_lower for k in ["conversationid", "conversation_id", "conv_id"]):
            key, val = token.split(":", 1)
            val_clean = val.strip().strip('"{}\',')
            if val_clean.lower() == lower_conv_id:
                return True

    # JSON regex search in clean_str
    json_pattern = r'["\']?(?:conversation_?id|conv_?id)["\']?\s*[:=]\s*["\']?' + re.escape(lower_conv_id) + r'(?![\w\-])["\']?'
    if re.search(json_pattern, clean_str, re.IGNORECASE):
        return True

    # Tier 2: Check for Inline Python Commands / Test Runners
    is_inline_python = False
    if clean_list:
        for idx, arg in enumerate(clean_list):
            if arg == "-c":
                exec_name = os.path.basename(clean_list[0]).lower()
                if "python" in exec_name or idx > 0:
                    is_inline_python = True
                    break
    if not is_inline_python and re.search(r'\bpython(?:\d+(?:\.\d+)?)?(?:\.exe)?\b.*\s+-c\b', clean_str, re.IGNORECASE):
        is_inline_python = True

    is_test_runner = False
    if clean_list:
        exec_name = os.path.basename(clean_list[0]).lower()
        if "pytest" in exec_name:
            is_test_runner = True
        else:
            for idx, arg in enumerate(clean_list[:-1]):
                if arg == "-m" and clean_list[idx+1].lower() in ("pytest", "unittest"):
                    is_test_runner = True
                    break
    if not is_test_runner and re.search(r'\b(?:pytest|unittest)\b', clean_str, re.IGNORECASE):
        is_test_runner = True

    # If it is an inline command or test runner, and explicit flag match didn't trigger, return False
    if is_inline_python or is_test_runner:
        return False

    # Tier 3: Exact Token Match (for normal process command lines)
    for token in clean_list:
        if token.strip('"\'').lower() == lower_conv_id:
            return True

    # Tier 4: Regex Boundary Search with Negative Lookbehinds/Lookaheads
    pattern = r'(?<![\w\/\-\\=])' + re.escape(lower_conv_id) + r'(?![\w\/\-\\=])'
    if re.search(pattern, clean_str.lower()):
        return True

    return False

def terminate_swarm_processes(conv_id: str = None, root_pid: int = None, timeout: float = 3.0) -> int:
    """
    Discovers and forcefully cleans up process trees associated with the agent swarm.
    Includes target_root (root_pid / parent_pid) in termination targets and uses safe,
    structured criteria matching for conv_id. Child processes are terminated first,
    followed by the target_root process.

    Returns:
        int: Number of successfully terminated processes.
    """
    current_pid = os.getpid()
    parent_pid = os.getppid()

    protected_pids = {current_pid, 0, 1, 4}
    if parent_pid is not None and isinstance(parent_pid, int) and parent_pid > 0:
        protected_pids.add(parent_pid)

    target_root = root_pid if root_pid is not None else None

    targets = {}

    if psutil is not None:
        # 1. Tree-based discovery from target_root
        if target_root is not None and target_root not in protected_pids:
            try:
                root_proc = psutil.Process(target_root)
                if root_proc.is_running():
                    targets[root_proc.pid] = root_proc
                    for child in root_proc.children(recursive=True):
                        if child.pid not in protected_pids and child.pid not in targets:
                            targets[child.pid] = child
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass
            except Exception as e:
                log(f"Warning: Error during root process tree discovery: {e}")

        # 2. Safe Criteria-based discovery
        try:
            has_valid_conv_id = bool(
                conv_id
                and isinstance(conv_id, str)
                and conv_id.strip()
                and len(conv_id.strip()) >= 4
                and conv_id.strip().lower() not in ("unknown", "none", "null", "false", "true", "undefined")
            )
            has_explicit_root_pid = root_pid is not None

            # Fallback keywords: ONLY explicit executable/script filenames or CLI flags.
            # Never include broad directory or path strings like "antigravity" or "credit_guardian".
            fallback_keywords = ["agent_runner.py", "--subagent", "subagent.py"]

            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    pid = proc.info['pid']
                    if pid in protected_pids or pid in targets:
                        continue

                    cmdline_list = proc.info.get('cmdline') or []
                    if not cmdline_list:
                        continue

                    cmdline_str = " ".join(cmdline_list)
                    cmdline_str_lower = cmdline_str.lower()
                    proc_name = (proc.info.get('name') or "").lower()

                    matched = False

                    if has_valid_conv_id:
                        # Requirement 1: When conv_id is provided, ONLY target processes matching conv_id.
                        # Disable fallback keyword matching completely when conv_id is supplied.
                        if is_conv_id_match(conv_id, cmdline_list, cmdline_str):
                            matched = True
                    elif not has_explicit_root_pid:
                        # Requirement 2: Fallback keyword matching runs ONLY when conv_id is None and root_pid is None.
                        # Matches explicit process executable names or CLI script flags.
                        if any(kw in cmdline_str_lower for kw in fallback_keywords):
                            if "python" in proc_name or "node" in proc_name:
                                matched = True

                    if matched:
                        targets[pid] = proc
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception as e:
            log(f"Warning: Error during criteria process scanning: {e}")

    # Fallback if psutil is not available
    if not targets:
        if psutil is None and target_root and target_root not in protected_pids:
            try:
                cmd = f"taskkill /F /T /PID {target_root}"
                res = subprocess.run(creationflags=0x08000000, cmd, shell=True, capture_output=True, text=True)
                if res.returncode == 0:
                    return 1
            except Exception as e:
                log(f"Error during taskkill fallback: {e}")
            return 0
        log("No active swarm processes found for termination.")
        return 0

    log(f"Discovered {len(targets)} candidate process(es) for swarm cleanup: {list(targets.keys())}")

    # Order termination: children and descendants first, target_root last
    procs_to_terminate = [p for p in targets.values() if p.pid != target_root]
    if target_root in targets:
        procs_to_terminate.append(targets[target_root])

    # Stage 1: Soft termination (SIGTERM / terminate)
    for proc in procs_to_terminate:
        try:
            log(f"Sending SIGTERM/terminate to PID {proc.pid}")
            proc.terminate()
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            pass
        except psutil.AccessDenied:
            log(f"AccessDenied terminating PID {proc.pid}")
        except Exception as e:
            log(f"Error terminating PID {proc.pid}: {e}")

    # Stage 2: Wait for graceful termination
    gone, alive = psutil.wait_procs(procs_to_terminate, timeout=timeout)
    terminated_count = len(gone)

    # Stage 3: Hard termination (SIGKILL / kill) for remaining processes
    if alive:
        log(f"{len(alive)} process(es) unresponsive to SIGTERM. Sending hard kill.")
        for proc in alive:
            try:
                log(f"Sending SIGKILL/kill to PID {proc.pid}")
                proc.kill()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                pass
            except psutil.AccessDenied:
                log(f"AccessDenied killing PID {proc.pid}")
            except Exception as e:
                log(f"Error killing PID {proc.pid}: {e}")

        gone_hard, alive = psutil.wait_procs(alive, timeout=1.0)
        terminated_count += len(gone_hard)

    # Stage 4: Windows Taskkill Fallback
    if alive and os.name == 'nt':
        log(f"Attempting taskkill /F /T fallback on Windows for {len(alive)} process(es).")
        for proc in alive:
            try:
                cmd = ["taskkill", "/F", "/T", "/PID", str(proc.pid)]
                res = subprocess.run(creationflags=0x08000000, cmd, capture_output=True, text=True, check=False)
                if res.returncode == 0:
                    log(f"taskkill succeeded for PID {proc.pid}")
                    terminated_count += 1
                else:
                    log(f"taskkill output for PID {proc.pid}: {res.stderr.strip() or res.stdout.strip()}")
            except Exception as e:
                log(f"taskkill fallback failed for PID {proc.pid}: {e}")

    return terminated_count

def schedule_resume_task(resume_dt: datetime.datetime, conv_id: str = None, custom_cmd: str = None) -> bool:
    """
    Schedules a Windows Task Scheduler job to resume the swarm at resume_dt.
    Returns True on success, False on failure.
    """
    if sys.platform != "win32":
        log("Task scheduling skipped: sys.platform is not win32.")
        return False

    try:
        resume_utc = ensure_utc_datetime(resume_dt)
        resume_local = resume_utc.astimezone()

        now_local = datetime.datetime.now().astimezone()
        if resume_local < now_local + datetime.timedelta(seconds=60):
            resume_local = now_local + datetime.timedelta(seconds=60)

        date_str = resume_local.strftime("%m/%d/%Y")
        time_str = resume_local.strftime("%H:%M:%S")

        safe_conv_id = ""
        if conv_id and isinstance(conv_id, str):
            cleaned = re.sub(r'[^a-zA-Z0-9_-]', '', conv_id.strip())
            if cleaned and cleaned.lower() not in ("unknown", "none", "null", "false", "true", "undefined", ""):
                safe_conv_id = cleaned[:64]

        task_name = f"AgentSwarmResume_{safe_conv_id}" if safe_conv_id else "AgentSwarmResume"

        if custom_cmd:
            resume_cmd_str = custom_cmd
        elif os.getenv("CREDIT_RESUME_CMD"):
            resume_cmd_str = os.getenv("CREDIT_RESUME_CMD")
        else:
            resume_cmd_str = f'"{sys.executable}" "{os.path.abspath(__file__)}" --resume'

        cmd_primary = [
            "schtasks", "/Create", "/F", "/Z", "/V1",
            "/TN", task_name,
            "/TR", resume_cmd_str,
            "/SC", "ONCE",
            "/ST", time_str,
            "/SD", date_str
        ]

        try:
            res = subprocess.run(creationflags=0x08000000, cmd_primary, capture_output=True, text=True, timeout=15)
            log(f"schtasks primary output (exit code {res.returncode}): stdout='{res.stdout.strip()}', stderr='{res.stderr.strip()}'")
            if res.returncode == 0:
                log(f"Successfully scheduled resume task '{task_name}' for {time_str} on {date_str} local time.")
                return True
            else:
                log(f"Primary schtasks with /Z failed (exit code {res.returncode}). Attempting fallback without /Z...")
                cmd_fallback = [
                    "schtasks", "/Create", "/F",
                    "/TN", task_name,
                    "/TR", resume_cmd_str,
                    "/SC", "ONCE",
                    "/ST", time_str,
                    "/SD", date_str
                ]
                res_fb = subprocess.run(creationflags=0x08000000, cmd_fallback, capture_output=True, text=True, timeout=15)
                log(f"schtasks fallback output (exit code {res_fb.returncode}): stdout='{res_fb.stdout.strip()}', stderr='{res_fb.stderr.strip()}'")
                if res_fb.returncode == 0:
                    log(f"Successfully scheduled resume task '{task_name}' via fallback for {time_str} on {date_str} local time.")
                    return True
                else:
                    log(f"Fallback schtasks failed with exit code {res_fb.returncode}.")
                    return False
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as sub_err:
            log(f"Subprocess exception executing schtasks command: {sub_err}")
            return False

    except Exception as e:
        log(f"Exception scheduling task: {e}")
        return False

def update_task_md_banner(artifact_dir: str, current_usage: int, max_budget: int, resume_dt: datetime.datetime) -> None:
    """
    Updates or creates task.md status banner in artifact_dir idempotently using HTML comment delimiters.
    """
    if not artifact_dir or not isinstance(artifact_dir, str) or not artifact_dir.strip():
        return

    try:
        clean_dir = artifact_dir.strip()
        os.makedirs(clean_dir, exist_ok=True)
        task_md_path = os.path.join(clean_dir, "task.md")

        resume_utc = ensure_utc_datetime(resume_dt)
        resume_local = resume_utc.astimezone()

        usage_pct = (current_usage / max(1, max_budget)) * 100.0
        resume_local_str = resume_local.strftime("%Y-%m-%d %H:%M:%S %z")
        resume_utc_str = resume_utc.strftime("%Y-%m-%d %H:%M:%S UTC")

        start_marker = "<!-- CREDIT_GUARDIAN_STATUS_START -->"
        end_marker = "<!-- CREDIT_GUARDIAN_STATUS_END -->"

        banner_content = (
            f"{start_marker}\n"
            f"> [!CAUTION]\n"
            f"> **SWARM PAUSED:** Rolling 5-hour quota limit reached.\n"
            f"> - **Token Usage:** {current_usage:,} / {max_budget:,} tokens ({usage_pct:.1f}% consumed)\n"
            f"> - **Scheduled Resume (Local):** {resume_local_str}\n"
            f"> - **Scheduled Resume (UTC):** {resume_utc_str}\n"
            f"{end_marker}"
        )

        if os.path.exists(task_md_path):
            with open(task_md_path, "r", encoding="utf-8") as f:
                existing_content = f.read()

            if start_marker in existing_content and end_marker in existing_content:
                pattern = re.escape(start_marker) + r".*?" + re.escape(end_marker)
                new_content = re.sub(pattern, banner_content, existing_content, flags=re.DOTALL)
            else:
                new_content = banner_content + "\n\n" + existing_content
        else:
            new_content = banner_content + "\n"

        with open(task_md_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        log(f"Successfully updated task.md status banner at '{task_md_path}'.")
    except Exception as e:
        log(f"Failed to update task.md status banner: {e}")

def calculate_new_tokens(transcript_path: str) -> int:
    if not transcript_path or not isinstance(transcript_path, str):
        return 0

    target_path = transcript_path.replace("transcript.jsonl", "transcript_full.jsonl")
    if not os.path.exists(target_path):
        if os.path.exists(transcript_path):
            target_path = transcript_path
        else:
            return 0

    total = 0
    try:
        with open(target_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data, dict) and data.get("type") == "PLANNER_RESPONSE":
                        usage = data.get("usage", {})
                        if isinstance(usage, dict):
                            total += sanitize_token_count(usage.get("total_token_count", 0))
                except Exception:
                    pass
    except Exception as e:
        log(f"Error reading transcript '{target_path}': {e}")
    return sanitize_token_count(total)

def main():
    try:
        payload_str = sys.stdin.read()
        if not payload_str.strip():
            print("{}")
            sys.stdout.flush()
            return

        try:
            payload = json.loads(payload_str)
        except Exception as e:
            log(f"Invalid JSON received on STDIN: {e}")
            print("{}")
            sys.stdout.flush()
            return

        if not isinstance(payload, dict):
            log("Payload received on STDIN is not a JSON object")
            print("{}")
            sys.stdout.flush()
            return

        transcript_path = str(payload.get("transcriptPath") or "").strip()
        artifact_dir = str(payload.get("artifactDirectoryPath") or "").strip()
        conv_id = str(payload.get("conversationId") or "").strip()
        if not conv_id:
            conv_id = "unknown"

        budget = get_budget()
        now = datetime.datetime.now(datetime.timezone.utc)

        valid_events = prune_old_events(budget.get("usage_events", []), now)
        budget["usage_events"] = valid_events

        current_session_tokens = calculate_new_tokens(transcript_path)
        last_seen = sanitize_token_count(budget.get("conversations", {}).get(conv_id, 0))
        delta = calculate_delta_tokens(current_session_tokens, last_seen)

        if "conversations" not in budget or not isinstance(budget["conversations"], dict):
            budget["conversations"] = {}
        budget["conversations"][conv_id] = current_session_tokens

        budget["usage_events"].append({
            "timestamp": now.isoformat(),
            "tokens": delta
        })

        current_usage = sum(sanitize_token_count(e.get("tokens", 0)) for e in budget["usage_events"] if isinstance(e, dict))
        max_budget = sanitize_budget_limit(budget.get("max_tokens_per_5_hours", 2000000))
        remaining = max_budget - current_usage
        percentage = remaining / max(1, max_budget)

        log(f"Conv {conv_id} used {delta} tokens. Current 5hr Usage: {current_usage}. Remaining: {percentage:.1%}")
        save_budget(budget)

        if percentage <= 0.02:
            log("CRITICAL: Credit limit reached. Calculating resume time and issuing protocol response.")
            resume_dt = calculate_resume_time(budget["usage_events"], max_budget, now)

            if artifact_dir:
                update_task_md_banner(artifact_dir, current_usage, max_budget, resume_dt)

            schedule_resume_task(resume_dt, conv_id=conv_id)
            resume_local = resume_dt.astimezone()
            time_str = resume_local.strftime("%H:%M:%S")

            response = {
                "terminationBehavior": "terminate",
                "injectSteps": [
                    {
                        "ephemeralMessage": f"CRITICAL: Global rolling token budget depleted (<= 2% remaining). Swarm forcefully terminated. Resume scheduled for {time_str}."
                    }
                ]
            }
            print(json.dumps(response))
            sys.stdout.flush()

            killed_count = terminate_swarm_processes(conv_id=conv_id)
            log(f"Terminated {killed_count} active swarm process(es).")
        else:
            print("{}")
            sys.stdout.flush()

    except Exception as e:
        log(f"Fatal error in credit_guardian: {e}")
        print("{}")
        sys.stdout.flush()

if __name__ == "__main__":
    main()

