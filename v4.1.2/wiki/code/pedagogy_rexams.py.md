# src/cochem/dsp/pedagogy/rexams.py

`python
"""R/exams Question Generator and Compiler (MC-DSP-27, MC-DSP-28)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import random
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

CREATE_NO_WINDOW: int = 0x08000000

DEFAULT_CHEMISTRY_TEMPLATE = (
    "Question {num}: Calculate the standard Gibbs Free Energy change (Delta G deg) for the dissociation "
    "at T = {temp} K. Given: Delta H deg = {dh} kJ/mol, Delta S deg = {ds} J/(mol*K).\n"
    "Correct Answer: {dg:.2f} kJ/mol"
)


def generate_rexams_questions(template_path: Path | None = None, count: int = 5, seed: int = 42) -> list[str]:
    """Renders parameterized chemistry question templates with deterministic random seeds (MC-DSP-27)."""
    if count <= 0:
        return []

    rng = random.Random(seed)
    template_str = DEFAULT_CHEMISTRY_TEMPLATE
    if template_path is not None:
        p = Path(template_path)
        if p.exists() and p.is_file():
            template_str = p.read_text(encoding="utf-8")

    questions: list[str] = []
    for i in range(1, count + 1):
        temp = rng.randint(273, 450)  # Kelvin
        # Physically authentic dissociation thermodynamics:
        # Bond cleavage is strictly endothermic (Delta H > 0)
        # Dissociation into multiple particles increases system entropy (Delta S > 0)
        dh = rng.randint(10, 250)     # kJ/mol
        ds = rng.randint(10, 150)     # J/(mol*K)
        dg = float(dh) - (float(temp) * float(ds) / 1000.0)

        # Robust token replacement supporting both simple templates and complex Rmd/LaTeX syntax
        tokens: dict[str, str] = {
            "{num}": str(i),
            "{temp}": str(temp),
            "{dh}": str(dh),
            "{ds}": str(ds),
            "{dg}": f"{dg:.2f}",
            "{dg:.2f}": f"{dg:.2f}",
        }
        rendered = template_str
        for token, val in tokens.items():
            rendered = rendered.replace(token, val)

        questions.append(rendered)

    return questions


def _synthesize_multipage_pdf(text_lines: list[str]) -> bytes:
    """Synthesizes an authentic, multi-page PDF-1.4 binary stream containing all text lines."""
    def _escape_pdf(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    page_line_limit = 45
    chunks = [text_lines[i:i + page_line_limit] for i in range(0, max(len(text_lines), 1), page_line_limit)]
    if not chunks:
        chunks = [[""]]

    num_pages = len(chunks)
    font_id = 3 + 2 * num_pages
    objects: list[bytes] = []

    # 1 0 obj: Catalog
    objects.append(b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n")

    # 2 0 obj: Pages collection
    kids_refs = " ".join(f"{3 + i} 0 R" for i in range(num_pages))
    objects.append(f"2 0 obj << /Type /Pages /Kids [{kids_refs}] /Count {num_pages} >> endobj\n".encode("latin-1"))

    # 3..2+num_pages: Page dictionaries
    for i in range(num_pages):
        stream_id = 3 + num_pages + i
        page_dict = (
            f"{3 + i} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {stream_id} 0 R >> endobj\n"
        ).encode("latin-1")
        objects.append(page_dict)

    # 3+num_pages..2+2*num_pages: Content streams (all text rendered)
    for i, chunk in enumerate(chunks):
        pdf_cmds = ["BT", "/F1 10 Tf", "14 TL", "50 750 Td"]
        for line in chunk:
            sanitized = _escape_pdf(line[:90])
            pdf_cmds.append(f"({sanitized}) Tj T*")
        pdf_cmds.append("ET\n")
        stream_bytes = "\n".join(pdf_cmds).encode("latin-1", errors="replace")
        stream_obj = (
            f"{3 + num_pages + i} 0 obj << /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
            + stream_bytes
            + b"endstream\nendobj\n"
        )
        objects.append(stream_obj)

    # Font dictionary
    objects.append(f"{font_id} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n".encode("latin-1"))

    # Document assembly with cross-reference table and trailer
    header = b"%PDF-1.4\n"
    body = b""
    offsets: list[int] = []
    current_offset = len(header)
    for obj in objects:
        offsets.append(current_offset)
        body += obj
        current_offset += len(obj)

    xref_offset = current_offset
    total_objects = len(objects) + 1
    xref_lines = [f"xref\n0 {total_objects}\n0000000000 65535 f \n"]
    for off in offsets:
        xref_lines.append(f"{off:010d} 00000 n \n")
    xref_bytes = "".join(xref_lines).encode("latin-1")
    trailer_bytes = f"trailer << /Size {total_objects} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("latin-1")

    return header + body + xref_bytes + trailer_bytes


def compile_exam_pdf(text_file: Path, pdf_out: Path | None = None) -> Path:
    """Compiles text/markdown exam or answer key to PDF format via CLI runner or direct synthesis."""
    src_path = Path(text_file)
    if not src_path.exists():
        raise FileNotFoundError(f"Source file not found: {src_path}")

    target_pdf = Path(pdf_out) if pdf_out else src_path.with_suffix(".pdf")
    target_pdf.parent.mkdir(parents=True, exist_ok=True)

    # Attempt compilation via typst CLI if installed
    try:
        res = subprocess.run(
            ["typst", "compile", str(src_path), str(target_pdf)],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=30,
        )
        if res.returncode == 0 and target_pdf.exists():
            return target_pdf
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as err:
        logger.debug("Typst CLI unavailable (%s); executing direct physical PDF synthesis", err)

    # Direct physical fallback: synthesize complete multi-line/multi-page PDF-1.4
    raw_content = src_path.read_text(encoding="utf-8", errors="replace")
    pdf_bytes = _synthesize_multipage_pdf(raw_content.splitlines())
    target_pdf.write_bytes(pdf_bytes)
    return target_pdf


def compile_exam_permutations(
    exam_id: str,
    num_versions: int = 4,
    questions: list[str] | None = None,
    output_dir: Path | None = None,
    compile_pdf: bool = True,
) -> list[Path]:
    """Compiles scrambled exam versions and master answer keys to disk (MC-DSP-28)."""
    if not exam_id:
        raise ValueError("exam_id must be non-empty string")
    if num_versions < 1:
        num_versions = 1

    out_dir = Path(output_dir) if output_dir else Path(r"D:\__CoChem\__agentic\v4.1.2\.evidence\exams") / exam_id
    out_dir.mkdir(parents=True, exist_ok=True)

    base_questions = questions if questions else generate_rexams_questions(count=4, seed=100)
    compiled_paths: list[Path] = []
    master_keys_index: dict[str, list[dict[str, Any]]] = {}

    for v in range(1, num_versions + 1):
        rng = random.Random(f"{exam_id}_version_{v}")
        indexed_pool = list(enumerate(base_questions, 1))
        rng.shuffle(indexed_pool)

        # 1. Student Exam Text Version
        version_file = out_dir / f"exam_{exam_id}_v{v}.txt"
        exam_lines = [
            f"=== Physical Chemistry Examination (ID: {exam_id} | Version: {v}) ===",
            f"Generated from master question pool ({len(base_questions)} items)\n",
        ]
        version_keys = []
        for idx, (orig_idx, q_text) in enumerate(indexed_pool, 1):
            exam_lines.append(f"[{idx}] {q_text}\n")
            ans_str = "See Rubric"
            if "Correct Answer:" in q_text:
                ans_str = q_text.split("Correct Answer:")[-1].strip()
            version_keys.append({"question_number": idx, "original_pool_index": orig_idx, "answer": ans_str})

        version_file.write_text("\n".join(exam_lines), encoding="utf-8")
        compiled_paths.append(version_file)

        if compile_pdf:
            exam_pdf = compile_exam_pdf(version_file, out_dir / f"exam_{exam_id}_v{v}.pdf")
            compiled_paths.append(exam_pdf)

        # 2. Version Answer Key File
        key_file = out_dir / f"exam_{exam_id}_v{v}_key.txt"
        key_lines = [
            f"=== Answer Key: Physical Chemistry Examination (ID: {exam_id} | Version: {v}) ===",
            f"Generated for Version {v} ({len(version_keys)} questions)\n",
        ]
        for item in version_keys:
            key_lines.append(
                f"Question {item['question_number']} (from pool #{item['original_pool_index']}): {item['answer']}"
            )
        key_file.write_text("\n".join(key_lines), encoding="utf-8")
        compiled_paths.append(key_file)

        if compile_pdf:
            key_pdf = compile_exam_pdf(key_file, out_dir / f"exam_{exam_id}_v{v}_key.pdf")
            compiled_paths.append(key_pdf)

        master_keys_index[f"version_{v}"] = version_keys

    # 3. Master Answer Key across all versions
    master_key_file = out_dir / f"exam_{exam_id}_master_key.json"
    master_key_file.write_text(json.dumps(master_keys_index, indent=2), encoding="utf-8")
    compiled_paths.append(master_key_file)

    return compiled_paths

`
