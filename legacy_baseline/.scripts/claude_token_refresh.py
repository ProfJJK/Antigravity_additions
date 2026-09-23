"""
claude_token_refresh.py — Windows Task Scheduler token keeper.
Runs every 5 hours via Task Scheduler. Calls `claude --version` to trigger
claude.exe's native OAuth refresh, keeping the token always fresh.
No browser, no manual login needed while refresh token is valid (~30-90 days).
"""
import subprocess, pathlib, json, datetime, sys, logging

LOG = pathlib.Path(r"d:\__CoChem\__agentic\.scripts\claude_token_refresh.log")
CLAUDE = pathlib.Path(r"C:\Users\ansac\.local\bin\claude.exe")
CREDS = pathlib.Path.home() / ".claude" / ".credentials.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[
        logging.FileHandler(str(LOG), encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

def check_expiry():
    try:
        oauth = json.loads(CREDS.read_text(encoding="utf-8")).get("claudeAiOauth", {})
        exp = datetime.datetime.fromtimestamp(oauth.get("expiresAt", 0) / 1000)
        secs = (exp - datetime.datetime.now()).total_seconds()
        return secs, oauth.get("accessToken", "")[:20]
    except Exception as e:
        return None, str(e)

if not CLAUDE.exists():
    logging.error(f"claude.exe not found at {CLAUDE}")
    sys.exit(1)

secs_left, tok_prefix = check_expiry()
logging.info(f"Token check: {tok_prefix}... expires in {secs_left:.0f}s ({secs_left/3600:.1f}h)")

# Run claude --version to trigger native OAuth refresh
import os
env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
r = subprocess.run(
    [str(CLAUDE), "--version"],
    capture_output=True, text=True, timeout=30,
    env=env, creationflags=subprocess.CREATE_NO_WINDOW,
)
logging.info(f"claude --version: rc={r.returncode} stdout={r.stdout.strip()[:80]}")

secs_after, tok_after = check_expiry()
logging.info(f"After refresh: {tok_after}... expires in {secs_after:.0f}s ({secs_after/3600:.1f}h)")

if secs_after and secs_after > 3600:
    logging.info("SUCCESS: Token is fresh.")
else:
    logging.warning("WARNING: Token may not have refreshed — manual 'claude auth login' may be needed.")
