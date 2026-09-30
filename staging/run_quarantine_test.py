import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
rscript = r"C:\Users\ansac\scoop\shims\rscript.exe"

test_dir = Path(r"D:\__CoChem\__agentic\staging\quarantine_test")
test_dir.mkdir(parents=True, exist_ok=True)

rmd_file = r"D:/__CoChem/__agentic/CHEM311_ch2_2.11_remember_571b.Rmd"
quarantine_str = str(test_dir).replace("\\", "/")

r_code = f"""
library(exams)
library(qpdf)
if (requireNamespace('tinytex', quietly=TRUE)) {{ tinytex::tlmgr_path('add') }}

# 1. HTML compilation
cat("Compiling HTML...\\n")
h_res <- tryCatch({{
  exams2html("{rmd_file}", n = 1, dir = "{quarantine_str}", name = "CHEM311_ch2_2.11_remember_571b_compiled", mathjax = TRUE)
  0
}}, error = function(e) {{
  cat("HTML Error: ", conditionMessage(e), "\\n")
  1
}})

# 2. NOPS compilation n = 2
cat("Compiling NOPS...\\n")
n_res <- tryCatch({{
  exams2nops(
    "{rmd_file}",
    n = 2,
    dir = "{quarantine_str}",
    name = "CHEM311_ch2_2.11_remember_571b_nops",
    title = "CHEM311 Quiz - Lewis Acid Definition",
    institution = "Cumberland University",
    duplex = FALSE,
    blank = 0,
    replacement = TRUE
  )
  0
}}, error = function(e) {{
  cat("NOPS Error: ", conditionMessage(e), "\\n")
  1
}})

cat("HTML exit: ", h_res, "\\n")
cat("NOPS exit: ", n_res, "\\n")
"""

proc = subprocess.run(
    [rscript, "--vanilla", "-e", r_code],
    capture_output=True,
    text=True,
    encoding="utf-8",
    creationflags=CREATE_NO_WINDOW,
)

print("Returncode:", proc.returncode)
print("STDOUT:\n", proc.stdout)
print("STDERR:\n", proc.stderr)
print("\nFiles in quarantine:")
for f in test_dir.iterdir():
    print(f.name, f.stat().st_size)
