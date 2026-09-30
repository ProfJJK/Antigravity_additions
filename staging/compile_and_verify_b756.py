"""
compile_and_verify_b756.py

Isolated Ahead-of-Time (AOT) headless Rscript compilation of
D:/Gdrive/__agentic/.sources/r_exams/testbank/CHEM311_ch2_2.12_remember_b756.Rmd
in a quarantine temp directory using exams::exams2html(..., mathjax=TRUE)
and exams::exams2nops(..., n=1).
"""

import argparse
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Optional, Sequence, Union

TASK_ID = "CHEM311_ch2_2.12_remember_b756"
R_EXAMS = Path("D:/Gdrive/__agentic/.sources/r_exams")
DEFAULT_RMD = R_EXAMS / "testbank" / f"{TASK_ID}.Rmd"
DEFAULT_RECEIPT = R_EXAMS / "kanban" / "tasks" / f"{TASK_ID}_nops_receipt.json"

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

ERROR_PATTERNS = [
    (r"! LaTeX Error[^\n]*", "LaTeX Error"),
    (r"! Undefined control sequence[^\n]*", "Undefined control sequence"),
    (r"! Missing \$ inserted[^\n]*", "Missing $ inserted"),
    (r"! Missing \} inserted[^\n]*", "Missing } inserted"),
    (r"! Extra \}[^\n]*", "Extra }"),
    (r"! Package [^\n]+ Error[^\n]*", "Package Error"),
    (r"Emergency stop[^\n]*", "Emergency stop"),
    (r"Fatal error occurred[^\n]*", "Fatal error occurred"),
    (r"Misplaced alignment tab[^\n]*", "Misplaced alignment tab"),
    (r"Runaway argument[^\n]*", "Runaway argument"),
    (r"MathJax_Error", "MathJax Error"),
    (r"Math input error[^\n]*", "Math input error"),
    (r"TeX parse error[^\n]*", "TeX parse error"),
    (r"Undefined control sequence", "Undefined control sequence"),
    (r"Missing close brace", "Missing close brace"),
    (r"Extra close brace", "Extra close brace"),
    (r"Double subscripts", "Double subscripts"),
    (r"Double superscripts", "Double superscripts"),
    (r"Unknown environment", "Unknown environment"),
    (r"Execution halted", "Execution halted"),
    (r"\bError in [^\n]*", "R error"),
]


def sha256_of(path: Union[str, Path]) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_rscript() -> str:
    found = shutil.which("Rscript")
    if found:
        return found
    candidates = [
        Path(r"C:\Users\ansac\scoop\shims\rscript.exe"),
        Path(os.environ.get("R_HOME", "")) / "bin" / "Rscript.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    prog_r = list(Path(r"C:\Program Files\R").glob("R-*/bin/Rscript.exe"))
    if prog_r:
        return str(sorted(prog_r)[-1])
    return "Rscript"


def build_rscript_commands(
    rscript: str,
    rmd_path: Union[str, Path],
    html_dir: Union[str, Path],
    pdf_dir: Union[str, Path],
) -> dict[str, list[str]]:
    rmd_posix = str(rmd_path).replace("\\", "/")
    html_posix = str(html_dir).replace("\\", "/")
    pdf_posix = str(pdf_dir).replace("\\", "/")

    html_expr = (
        f"options(warn=1); suppressPackageStartupMessages(library(exams)); "
        f"exams2html('{rmd_posix}', n=1, dir='{html_posix}', mathjax=TRUE, quiet=TRUE)"
    )
    nops_expr = (
        f"options(warn=1); suppressPackageStartupMessages(library(exams)); "
        f"exams2nops('{rmd_posix}', n=1, dir='{pdf_posix}', quiet=TRUE)"
    )

    return {
        "html": [str(rscript), "-e", html_expr],
        "nops": [str(rscript), "-e", nops_expr],
    }


class OptionParser(HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img",
        "input", "link", "meta", "param", "source", "track", "wbr"
    }

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.target_depth: Optional[int] = None
        self.finished: bool = False
        self.count: int = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in self.VOID_TAGS:
            return
        attr_dict = {k.lower(): (v or "") for k, v in attrs}
        if not self.finished and self.target_depth is None and tag == "ol":
            classes = attr_dict.get("class", "").split()
            is_answerlist = "answerlist" in classes
            is_type_a = attr_dict.get("type", "").lower() == "a"
            if is_answerlist or is_type_a:
                self.target_depth = len(self.stack)

        if self.target_depth is not None:
            if tag == "li" and len(self.stack) == self.target_depth + 2 and self.stack[-1] == "li":
                self.stack.pop()

            if tag == "li" and len(self.stack) == self.target_depth + 1 and self.stack[self.target_depth] == "ol":
                self.count += 1

        self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.VOID_TAGS:
            return
        while self.stack:
            popped = self.stack.pop()
            if self.target_depth is not None and len(self.stack) == self.target_depth:
                self.target_depth = None
                self.finished = True
            if popped == tag:
                break


def count_html_options(html_text: str) -> int:
    parser = OptionParser()
    parser.feed(html_text)
    return parser.count


def scan_for_errors(text: str) -> list[str]:
    if not text:
        return []
    errors: list[str] = []
    seen: set[str] = set()

    for pattern, name in ERROR_PATTERNS:
        matches = re.findall(pattern, text)
        for m in matches:
            msg = f"{name}: {m.strip()}"
            if msg not in seen:
                seen.add(msg)
                errors.append(msg)

    open_inline = text.count(r"\(")
    close_inline = text.count(r"\)")
    if open_inline != close_inline:
        errors.append(
            f"Mismatched inline math delimiters: {open_inline} '\\(' vs {close_inline} '\\)'"
        )

    open_display = text.count(r"\[")
    close_display = text.count(r"\]")
    if open_display != close_display:
        errors.append(
            f"Mismatched display math delimiters: {open_display} '\\[' vs {close_display} '\\]'"
        )

    no_escaped_dollars = text.replace(r"\$", "")
    if no_escaped_dollars.count("$") % 2 != 0:
        errors.append(
            f"Odd number of unescaped dollar signs: {no_escaped_dollars.count('$')}"
        )

    return errors


def make_quarantine_dir() -> Path:
    quarantine = tempfile.mkdtemp(prefix="b756_aot_", dir=tempfile.gettempdir())
    return Path(quarantine).resolve()


def run_step(
    cmd: list[str],
    cwd: Optional[Union[str, Path]] = None,
    timeout: float = 300.0,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    cwd_str = str(cwd) if cwd is not None else None
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd_str,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            duration_s = time.perf_counter() - t0
            return {
                "exit_code": proc.returncode,
                "stdout": stdout or "",
                "stderr": stderr or "",
                "duration_s": duration_s,
                "timed_out": False,
            }
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW,
                )
            else:
                proc.kill()
            stdout, stderr = proc.communicate()
            duration_s = time.perf_counter() - t0
            return {
                "exit_code": (
                    proc.returncode
                    if (proc.returncode is not None and proc.returncode != 0)
                    else -1
                ),
                "stdout": stdout or "",
                "stderr": stderr or "",
                "duration_s": duration_s,
                "timed_out": True,
            }
    except Exception as e:
        duration_s = time.perf_counter() - t0
        return {
            "exit_code": 127,
            "stdout": "",
            "stderr": str(e),
            "duration_s": duration_s,
            "timed_out": False,
        }


def build_receipt(
    *,
    rmd_path: Union[str, Path],
    rmd_sha256: str,
    quarantine_dir: Union[str, Path],
    html_step: dict[str, Any],
    nops_step: dict[str, Any],
    option_count: int,
    errors: list[str],
    html_files: list[Union[str, Path]],
    pdf_files: list[Union[str, Path]],
) -> dict[str, Any]:
    artifacts: list[dict[str, Any]] = []

    for h in html_files:
        hp = Path(h)
        if hp.is_file():
            artifacts.append({
                "kind": "html",
                "path": str(hp),
                "size_bytes": hp.stat().st_size,
                "sha256": sha256_of(hp),
            })
        else:
            artifacts.append({
                "kind": "html",
                "path": str(hp),
                "size_bytes": 0,
                "sha256": "",
            })

    for p in pdf_files:
        pp = Path(p)
        if pp.is_file():
            artifacts.append({
                "kind": "pdf",
                "path": str(pp),
                "size_bytes": pp.stat().st_size,
                "sha256": sha256_of(pp),
            })
        else:
            artifacts.append({
                "kind": "pdf",
                "path": str(pp),
                "size_bytes": 0,
                "sha256": "",
            })

    valid_pdfs = (
        len(pdf_files) > 0
        and all(
            Path(p).is_file()
            and Path(p).stat().st_size > 0
            and Path(p).read_bytes()[:5] == b"%PDF-"
            for p in pdf_files
        )
    )
    valid_htmls = (
        len(html_files) > 0
        and all(Path(h).is_file() and Path(h).stat().st_size > 0 for h in html_files)
    )

    is_success = (
        html_step.get("exit_code") == 0
        and nops_step.get("exit_code") == 0
        and not html_step.get("timed_out", False)
        and not nops_step.get("timed_out", False)
        and option_count == 5
        and len(errors) == 0
        and valid_pdfs
        and valid_htmls
    )

    status = "SUCCESS" if is_success else "FAILURE"

    return {
        "task_id": TASK_ID,
        "rmd_path": str(rmd_path),
        "rmd_sha256": rmd_sha256,
        "quarantine_dir": str(quarantine_dir),
        "steps": {
            "html": html_step,
            "nops": nops_step,
        },
        "option_count": option_count,
        "errors": list(errors),
        "artifacts": artifacts,
        "compilation_status": status,
    }


def write_receipt(receipt: dict[str, Any], path: Union[str, Path]) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    data = json.dumps(receipt, indent=2, ensure_ascii=False)
    fd, tmp_file_str = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    tmp_path = Path(tmp_file_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        for attempt in range(5):
            try:
                os.replace(tmp_path, target)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def preflight_check(rmd_path: Union[str, Path]) -> list[str]:
    p = Path(rmd_path)
    if not p.exists():
        return [f"File does not exist: {p}"]
    if not p.is_file():
        return [f"Path is not a regular file: {p}"]
    if p.suffix.lower() != ".rmd":
        return [f"File extension must be .Rmd, got: {p.suffix}"]
    try:
        content = p.read_text(encoding="utf-8")
    except Exception as e:
        return [f"Failed to read file as UTF-8: {e}"]

    if not content.strip():
        return ["Rmd file is empty"]

    problems: list[str] = []

    extype_match = re.search(r"^\s*extype:\s*(\S+)", content, re.MULTILINE)
    if not extype_match:
        problems.append("Missing 'extype' in Meta-information")
    elif extype_match.group(1).lower() != "schoice":
        problems.append(f"Expected extype 'schoice', got '{extype_match.group(1)}'")

    exsol_match = re.search(r"^\s*exsolution:\s*(\S+)", content, re.MULTILINE)
    if not exsol_match:
        problems.append("Missing 'exsolution' in Meta-information")
    else:
        sol = exsol_match.group(1).strip()
        if len(sol) != 5:
            problems.append(f"exsolution must be 5 characters long, got length {len(sol)} ('{sol}')")
        elif not set(sol).issubset({"0", "1"}):
            problems.append(f"exsolution must contain only '0' and '1', got '{sol}'")
        elif sol.count("1") != 1:
            problems.append(f"exsolution for schoice must contain exactly one '1', got {sol.count('1')} in '{sol}'")

    if not re.search(r"^\s*Question\s*$", content, re.MULTILINE):
        problems.append("Missing 'Question' section header")
    if not re.search(r"^\s*Answerlist\s*$", content, re.MULTILINE):
        problems.append("Missing 'Answerlist' section header")
    if not re.search(r"^\s*Solution\s*$", content, re.MULTILINE):
        problems.append("Missing 'Solution' section header")

    return problems


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=f"AOT headless compilation for {TASK_ID}")
    parser.add_argument("--rmd", type=str, default=str(DEFAULT_RMD))
    parser.add_argument("--receipt", type=str, default=str(DEFAULT_RECEIPT))
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    rmd_path = Path(args.rmd).resolve()
    receipt_path = Path(args.receipt).resolve()
    timeout = args.timeout

    problems = preflight_check(rmd_path)
    if problems:
        print(f"Preflight check failed: {problems}", file=sys.stderr)
        return 2

    rmd_sha256 = sha256_of(rmd_path)
    mtime_before = rmd_path.stat().st_mtime_ns

    quarantine_dir = make_quarantine_dir()
    html_dir = quarantine_dir / "html"
    pdf_dir = quarantine_dir / "pdf"
    html_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    quarantine_rmd = quarantine_dir / rmd_path.name
    shutil.copy2(rmd_path, quarantine_rmd)

    rscript = find_rscript()
    cmds = build_rscript_commands(rscript, quarantine_rmd, html_dir, pdf_dir)

    html_step = run_step(cmds["html"], cwd=str(quarantine_dir), timeout=timeout)
    nops_step = run_step(cmds["nops"], cwd=str(quarantine_dir), timeout=timeout)

    html_files = sorted(list(html_dir.glob("*.html")))
    pdf_files = sorted(list(pdf_dir.glob("*.pdf")))

    errors: list[str] = []
    errors.extend(scan_for_errors(html_step.get("stdout", "")))
    errors.extend(scan_for_errors(html_step.get("stderr", "")))
    errors.extend(scan_for_errors(nops_step.get("stdout", "")))
    errors.extend(scan_for_errors(nops_step.get("stderr", "")))

    option_count = 0
    if html_files:
        html_text = html_files[0].read_text(encoding="utf-8", errors="replace")
        errors.extend(scan_for_errors(html_text))
        option_count = count_html_options(html_text)
    else:
        errors.append("No compiled HTML file generated")

    if not pdf_files:
        errors.append("No compiled NOPS PDF file generated")

    if rmd_path.stat().st_mtime_ns != mtime_before or sha256_of(rmd_path) != rmd_sha256:
        errors.append("Source Rmd was modified during compilation")

    receipt = build_receipt(
        rmd_path=rmd_path,
        rmd_sha256=rmd_sha256,
        quarantine_dir=quarantine_dir,
        html_step=html_step,
        nops_step=nops_step,
        option_count=option_count,
        errors=errors,
        html_files=html_files,
        pdf_files=pdf_files,
    )

    write_receipt(receipt, receipt_path)
    return 0 if receipt["compilation_status"] == "SUCCESS" else 1


if __name__ == "__main__":
    sys.exit(main())
