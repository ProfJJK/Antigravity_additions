import os
import sys
import subprocess

def main():
    env = os.environ.copy()
    env.pop('ANTHROPIC_API_KEY', None)

    cmd = ['claude.exe', 'mcp', 'serve']

    try:
        process = subprocess.Popen(cmd, env=env, stdin=sys.stdin.buffer, stdout=sys.stdout.buffer, stderr=sys.stderr.buffer)
        process.wait()
        sys.exit(process.returncode)
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
