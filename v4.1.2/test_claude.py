import subprocess
CLAUDE_EXE = r"C:\Users\ansac\.local\bin\claude.exe"
cmd = [CLAUDE_EXE, "--dangerously-skip-permissions", "-p", "hello"]
res = subprocess.run(cmd, creationflags=0x08000000, capture_output=True, text=True, errors="replace")
print("RC:", res.returncode)
print("STDOUT:", res.stdout)
print("STDERR:", res.stderr)
