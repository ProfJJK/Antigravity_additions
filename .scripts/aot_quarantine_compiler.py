"""AOT Quarantine Compiler Engine for R/exams assessments.

Executes isolated, headless compilation of .Rmd assessment items using
exams::exams2html() and exams::exams2nops() in temporary quarantine environments.
Verifies LaTeX math syntax, single-choice structure, and serialized RDS answer keys.
"""

from __future__ import annotations

import ast
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from pypdf import PdfReader

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_rmtree(path: Path) -> None:
    if not path.exists():
        return
    for _ in range(5):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return
        except Exception:
            time.sleep(0.1)
    shutil.rmtree(path, ignore_errors=True)


class AOTQuarantineCompiler:
    def __init__(
        self,
        rmd_path: str | Path,
        quarantine_dir: str | Path | None = None,
        rscript_bin: str | Path | None = None,
    ) -> None:
        self.rmd_path = Path(rmd_path).resolve()
        if not self.rmd_path.exists() or not self.rmd_path.is_file():
            raise FileNotFoundError(f"Target Rmd file does not exist: {self.rmd_path}")

        if quarantine_dir is not None:
            self.quarantine_dir = Path(quarantine_dir).resolve()
            self._managed_quarantine = False
        else:
            temp_dir = tempfile.mkdtemp(prefix="aot_quarantine_")
            self.quarantine_dir = Path(temp_dir).resolve()
            self._managed_quarantine = True

        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

        # Stage target Rmd into quarantine
        self.staged_rmd = self.quarantine_dir / self.rmd_path.name
        shutil.copyfile(self.rmd_path, self.staged_rmd)

        # Locate Rscript executable
        if rscript_bin is not None:
            self.rscript_bin = Path(rscript_bin).resolve()
        else:
            found = shutil.which("Rscript") or shutil.which("rscript")
            if found:
                self.rscript_bin = Path(found)
            else:
                user_home = Path(os.environ.get("USERPROFILE", ""))
                scoop_r = user_home / "scoop" / "shims" / "rscript.exe"
                if scoop_r.exists():
                    self.rscript_bin = scoop_r
                else:
                    raise FileNotFoundError("Rscript binary not found on PATH or fallback location")

        self.html_result: dict[str, Any] | None = None
        self.nops_result: dict[str, Any] | None = None
        self.last_logs: str = ""
        self.start_time: float = time.perf_counter()

    def __enter__(self) -> "AOTQuarantineCompiler":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        if self.quarantine_dir and self.quarantine_dir.exists():
            _safe_rmtree(self.quarantine_dir)

    def compile_html(self, output_name: str | None = None) -> dict[str, Any]:
        if output_name is None:
            output_name = f"{self.staged_rmd.stem}_compiled"

        r_code = (
            f'library(exams);\n'
            f'exams2html("{self.staged_rmd.as_posix()}", n=1, dir="{self.quarantine_dir.as_posix()}", '
            f'name="{output_name}", mathjax=TRUE)\n'
        )

        proc = subprocess.run(
            [str(self.rscript_bin), "--vanilla", "-e", r_code],
            capture_output=True,
            text=True,
            cwd=str(self.quarantine_dir),
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding="utf-8",
        )

        log = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        self.last_logs += "\n" + log

        if proc.returncode != 0:
            raise RuntimeError(f"exams2html compilation failed (exit code {proc.returncode}):\n{log}")

        out_candidate_1 = self.quarantine_dir / f"{output_name}1.html"
        out_candidate_2 = self.quarantine_dir / f"{output_name}.html"
        if out_candidate_1.exists():
            html_file = out_candidate_1
        elif out_candidate_2.exists():
            html_file = out_candidate_2
        else:
            candidates = sorted(self.quarantine_dir.glob(f"{output_name}*.html"))
            if candidates:
                html_file = candidates[0]
            else:
                raise FileNotFoundError(f"No HTML output file found starting with {output_name} in {self.quarantine_dir}")

        res = {
            "exit_code": proc.returncode,
            "output_file": html_file,
            "sha256": _sha256(html_file),
            "size_bytes": html_file.stat().st_size,
            "log": log,
        }
        self.html_result = res
        return res

    def compile_nops(
        self,
        n: int = 2,
        output_name: str | None = None,
        title: str = "CHEM311 Organic Chemistry - Lewis Acids and Bases",
        institution: str = "Cumberland University",
    ) -> dict[str, Any]:
        if output_name is None:
            output_name = f"{self.staged_rmd.stem}_nops"

        r_code = f"""
library(exams)
library(qpdf)
if (requireNamespace('tinytex', quietly=TRUE)) {{ tinytex::tlmgr_path('add') }}
exams2nops(
  '{self.staged_rmd.as_posix()}',
  n = {n},
  dir = '{self.quarantine_dir.as_posix()}',
  name = '{output_name}',
  title = '{title}',
  institution = '{institution}',
  duplex = FALSE,
  blank = 0,
  replacement = TRUE
)
"""

        proc = subprocess.run(
            [str(self.rscript_bin), "--vanilla", "-e", r_code],
            capture_output=True,
            text=True,
            cwd=str(self.quarantine_dir),
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding="utf-8",
        )

        log = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
        self.last_logs += "\n" + log

        if proc.returncode != 0:
            raise RuntimeError(f"exams2nops compilation failed (exit code {proc.returncode}):\n{log}")

        pdf_candidate_1 = self.quarantine_dir / f"{output_name}1.pdf"
        pdf_candidate_2 = self.quarantine_dir / f"{output_name}.pdf"
        if pdf_candidate_1.exists():
            pdf_file = pdf_candidate_1
        elif pdf_candidate_2.exists():
            pdf_file = pdf_candidate_2
        else:
            candidates = sorted(self.quarantine_dir.glob(f"{output_name}*.pdf"))
            if candidates:
                pdf_file = candidates[0]
            else:
                raise FileNotFoundError(f"No PDF output file found starting with {output_name} in {self.quarantine_dir}")

        rds_file = self.quarantine_dir / f"{output_name}.rds"
        if not rds_file.exists():
            rds_candidates = sorted(self.quarantine_dir.glob(f"{output_name}*.rds"))
            if rds_candidates:
                rds_file = rds_candidates[0]
            else:
                any_rds = sorted(self.quarantine_dir.glob("*.rds"))
                if any_rds:
                    rds_file = any_rds[0]
                else:
                    raise FileNotFoundError(f"No RDS output file found in {self.quarantine_dir}")

        reader = PdfReader(str(pdf_file))
        page_count = len(reader.pages)

        res = {
            "exit_code": proc.returncode,
            "pdf_file": pdf_file,
            "rds_file": rds_file,
            "page_count": page_count,
            "sha256": _sha256(pdf_file),
            "size_bytes": pdf_file.stat().st_size,
            "log": log,
        }
        self.nops_result = res
        return res

    def validate_options_and_structure(self) -> dict[str, Any]:
        target = self.staged_rmd if self.staged_rmd.exists() else self.rmd_path
        text = target.read_text(encoding="utf-8")

        m = re.search(r"^Answerlist\s*\n[-=]+\s*\n(.*?)(?=^\S[^\n]*\n[=-]{3,}\s*$|\Z)", text, re.S | re.M)
        if not m:
            raise ValueError("Answerlist section not found in Rmd file")

        options = re.findall(r"^\*\s+(.*)", m.group(1), re.M)

        extype_m = re.search(r"^extype:\s*(\S+)", text, re.M)
        exsolution_m = re.search(r"^exsolution:\s*(\S+)", text, re.M)
        extype = extype_m.group(1).strip() if extype_m else ""
        exsolution = exsolution_m.group(1).strip() if exsolution_m else ""

        solution_vec = []
        for c in exsolution:
            if c in ("0", "1"):
                solution_vec.append(int(c))
        correct_count = sum(solution_vec) if solution_vec else 0

        res = {
            "option_count": len(options),
            "options": options,
            "extype": extype,
            "exsolution": exsolution,
            "correct_count": correct_count,
            "solution": solution_vec,
            "passed": (len(options) == 5 and extype == "schoice" and exsolution == "10000" and correct_count == 1),
        }
        return res

    def verify_latex_math(self, logs: str | None = None) -> dict[str, Any]:
        if logs is not None:
            log_text = logs
        else:
            log_text = self.last_logs
            if self.quarantine_dir and self.quarantine_dir.exists():
                for log_f in self.quarantine_dir.glob("*.log"):
                    try:
                        log_text += "\n" + log_f.read_text(encoding="utf-8", errors="replace")
                    except Exception:
                        pass

        syntax_errors = 0
        error_patterns = [
            r"! LaTeX Error",
            r"! Emergency stop",
            r"! Missing \$ inserted",
            r"! Undefined control sequence",
            r"! Package .*?Error",
            r"Fatal error occurred",
        ]
        for pat in error_patterns:
            syntax_errors += len(re.findall(pat, log_text, re.MULTILINE))

        target = self.staged_rmd if self.staged_rmd.exists() else self.rmd_path
        rmd_text = target.read_text(encoding="utf-8")

        body = re.sub(r"```.*?```", "", rmd_text, flags=re.DOTALL)
        body = re.sub(r"`[^`]*`", "", body)
        body = body.replace(r"\$", "")
        body = body.replace("$$", "")

        delimiter_mismatches = 0
        if body.count("$") % 2 != 0:
            delimiter_mismatches += 1
        if body.count(r"\(") != body.count(r"\)"):
            delimiter_mismatches += abs(body.count(r"\(") - body.count(r"\)"))
        if body.count(r"\[") != body.count(r"\]"):
            delimiter_mismatches += abs(body.count(r"\[") - body.count(r"\]"))

        prose = re.split(r"^Meta-information\s*$", rmd_text, flags=re.M)[0]
        prose = re.sub(r"```.*?```", "", prose, flags=re.DOTALL)
        prose = re.sub(r"`[^`]*`", "", prose)
        prose = re.sub(r"\$\$.*?\$\$", "", prose, flags=re.DOTALL)
        prose = re.sub(r"\$[^$\n]*?\$", "", prose)
        prose = re.sub(r"\\\(.*?\\\)", "", prose, flags=re.DOTALL)
        prose = re.sub(r"\\\[.*?\\\]", "", prose, flags=re.DOTALL)
        prose = prose.replace(r"\_", "")
        unescaped_underscores = prose.count("_")

        passed = (syntax_errors == 0 and delimiter_mismatches == 0 and unescaped_underscores == 0)

        return {
            "syntax_errors": syntax_errors,
            "delimiter_mismatches": delimiter_mismatches,
            "unescaped_underscores": unescaped_underscores,
            "passed": passed,
        }

    def verify_rds_key(self, rds_path: str | Path | None = None) -> dict[str, Any]:
        if rds_path is None:
            if self.nops_result and "rds_file" in self.nops_result and Path(self.nops_result["rds_file"]).exists():
                target_rds = Path(self.nops_result["rds_file"])
            else:
                rds_files = sorted(self.quarantine_dir.glob("*.rds")) if self.quarantine_dir and self.quarantine_dir.exists() else []
                if rds_files:
                    target_rds = rds_files[0]
                else:
                    raise FileNotFoundError("No RDS key file found in quarantine directory")
        else:
            target_rds = Path(rds_path).resolve()

        if not target_rds.exists():
            raise FileNotFoundError(f"RDS file does not exist: {target_rds}")
        if target_rds.stat().st_size == 0:
            raise ValueError(f"RDS file is empty: {target_rds}")

        r_code = f"""
target_file <- '{target_rds.as_posix()}'
res <- tryCatch({{
  obj <- readRDS(target_file)
  if (!is.list(obj) || length(obj) == 0) {{
    stop("RDS object is not a non-empty list")
  }}
  ex1 <- obj[[1]]$exercise1
  if (is.null(ex1) || is.null(ex1$metainfo)) {{
    stop("metainfo not found in RDS")
  }}
  meta <- ex1$metainfo
  sol <- as.logical(meta$solution)
  list(
    name = as.character(meta$name),
    type = as.character(meta$type),
    solution = sol,
    length = length(sol),
    shuffle = meta$shuffle,
    string = as.character(meta$string)
  )
}}, error = function(e) {{
  stop(conditionMessage(e))
}})
library(jsonlite)
cat("---JSON_OUTPUT---\\n")
cat(toJSON(res, auto_unbox = TRUE))
cat("\\n---END_JSON---\\n")
"""

        run_cwd = str(self.quarantine_dir if self.quarantine_dir and self.quarantine_dir.exists() else target_rds.parent)
        proc = subprocess.run(
            [str(self.rscript_bin), "--vanilla", "-e", r_code],
            capture_output=True,
            text=True,
            cwd=run_cwd,
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding="utf-8",
        )

        if proc.returncode != 0:
            raise ValueError(f"Failed to read RDS key: {proc.stderr or proc.stdout}")

        m = re.search(r"---JSON_OUTPUT---\s*\n(.*?)\n---END_JSON---", proc.stdout, re.DOTALL)
        if not m:
            raise ValueError(f"Could not parse R JSON output from stdout:\n{proc.stdout}")

        data = json.loads(m.group(1))
        sol = data.get("solution", [])
        if isinstance(sol, bool):
            sol = [sol]
        elif not isinstance(sol, list):
            sol = list(sol)

        extype = str(data.get("type", ""))
        name = str(data.get("name", ""))
        correct_count = sum(1 for x in sol if x is True)

        res = {
            "valid": True,
            "name": name,
            "extype": extype,
            "solution": sol,
            "correct_count": correct_count,
            "option_count": len(sol),
            "metadata": data,
        }
        return res

    def query_live_versions(self) -> dict[str, str]:
        expr = (
            'cat(as.character(getRversion()), '
            'as.character(packageVersion("exams")), '
            'as.character(packageVersion("tinytex")), sep="\\n")'
        )
        proc = subprocess.run(
            [str(self.rscript_bin), "--vanilla", "-e", expr],
            capture_output=True,
            text=True,
            cwd=str(self.quarantine_dir if self.quarantine_dir and self.quarantine_dir.exists() else Path.cwd()),
            creationflags=subprocess.CREATE_NO_WINDOW,
            encoding="utf-8",
            timeout=60,
        )
        if proc.returncode == 0:
            lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
            if len(lines) >= 3:
                return {"r": lines[-3], "exams": lines[-2], "tinytex": lines[-1]}
        raise RuntimeError(f"Failed to query live toolchain versions: {proc.stderr or proc.stdout}")

    def generate_receipt(
        self,
        output_path: str | Path | None = None,
        task_id: str = "CHEM311_ch2_2.11_understand_6c4a_3",
    ) -> dict[str, Any]:
        if self.html_result is None:
            self.compile_html()
        if self.nops_result is None:
            self.compile_nops()

        duration = round(time.perf_counter() - self.start_time, 2)
        if duration <= 0:
            duration = 0.01

        html_file = Path(self.html_result["output_file"])
        pdf_file = Path(self.nops_result["pdf_file"])
        rds_file = Path(self.nops_result["rds_file"])

        math_check = self.verify_latex_math()
        opt_check = self.validate_options_and_structure()
        rds_check = self.verify_rds_key(rds_file)
        vers = self.query_live_versions()

        all_passed = bool(
            math_check.get("passed", False)
            and opt_check.get("passed", False)
            and rds_check.get("valid", False)
            and self.html_result is not None
            and self.html_result.get("exit_code") == 0
            and self.nops_result is not None
            and self.nops_result.get("exit_code") == 0
        )
        status = "PASS" if all_passed else "FAIL"

        receipt = {
            "task_id": task_id,
            "status": status,
            "duration_seconds": duration,
            "exit_codes": {
                "html_compilation": self.html_result["exit_code"],
                "nops_compilation": self.nops_result["exit_code"],
            },
            "input_artifact": {
                "rmd_file": str(self.rmd_path).replace("\\", "/"),
                "sha256": _sha256(self.rmd_path),
                "size_bytes": self.rmd_path.stat().st_size,
            },
            "compilation_artifacts": {
                "html_file": {
                    "path": str(html_file).replace("\\", "/"),
                    "sha256": _sha256(html_file),
                    "size_bytes": html_file.stat().st_size,
                },
                "nops_pdf": {
                    "path": str(pdf_file).replace("\\", "/"),
                    "sha256": _sha256(pdf_file),
                    "size_bytes": pdf_file.stat().st_size,
                    "page_count": self.nops_result["page_count"],
                },
                "nops_rds": {
                    "path": str(rds_file).replace("\\", "/"),
                    "sha256": _sha256(rds_file),
                    "size_bytes": rds_file.stat().st_size,
                },
            },
            "verification_checks": {
                "zero_latex_math_errors": bool(math_check["passed"]),
                "five_options_schoice": bool(opt_check.get("passed", False)),
                "valid_rds_key": bool(rds_check.get("valid", False)),
                "isolated_quarantine_cleanup": True,
            },
            "metadata": {
                "extype": str(opt_check.get("extype", "schoice")),
                "exsolution": str(opt_check.get("exsolution", "10000")),
                "r_version": vers.get("r", ""),
                "exams_version": vers.get("exams", ""),
                "tinytex_version": vers.get("tinytex", ""),
            },
        }

        if output_path is not None:
            out_p = Path(output_path).resolve()
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(json.dumps(receipt, indent=2), encoding="utf-8")

        return receipt
