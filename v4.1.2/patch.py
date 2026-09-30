import os, tempfile
with open(r'D:\__CoChem\__agentic\llm_router.py', 'r', encoding='utf-8') as f:
    text = f.read()

if 'filelock' not in text:
    text = 'import filelock\n' + text
    
    mutex_setup = '''
# -- NVMe Kernel Crash Defense (Global Mutex for claude.exe) --
# Ensures absolutely only 1 claude.exe process can run system-wide to prevent NVMe lockups.
_CLAUDE_MUTEX_PATH = os.path.join(tempfile.gettempdir(), \"claude_exe_nvme.lock\")
_claude_mutex = filelock.FileLock(_CLAUDE_MUTEX_PATH, timeout=3600)
'''
    text = text.replace('class ClaudeSubscriptionProvider', mutex_setup + '\nclass ClaudeSubscriptionProvider')
    text = text.replace('result = csm.run_claude_subscription_batch(', 'with _claude_mutex:\n            result = csm.run_claude_subscription_batch(')
    text = text.replace('yield from csm.stream_claude_subscription(', 'with _claude_mutex:\n            yield from csm.stream_claude_subscription(')
    
    with open(r'D:\__CoChem\__agentic\llm_router.py', 'w', encoding='utf-8') as f:
        f.write(text)
