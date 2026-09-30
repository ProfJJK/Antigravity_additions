import os

file_path = r'D:\__CoChem\__agentic\.scripts\task_work_loop.py'
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

bad_func = '''async def daemon_loop(
    global _GIT_MERGE_LOCK
    if _GIT_MERGE_LOCK is None:
        _GIT_MERGE_LOCK = asyncio.Lock()

    watch_dir: Path,'''

good_func = '''async def daemon_loop(
    watch_dir: Path,'''

content = content.replace(bad_func, good_func)

# Now insert it inside the function body
old_body = '''    logger.info(f"[DAEMON] Watching {watch_dir} (poll={poll_interval}s, workers={num_workers})")'''
new_body = '''    global _GIT_MERGE_LOCK
    if _GIT_MERGE_LOCK is None:
        _GIT_MERGE_LOCK = asyncio.Lock()
    logger.info(f"[DAEMON] Watching {watch_dir} (poll={poll_interval}s, workers={num_workers})")'''

content = content.replace(old_body, new_body)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)
