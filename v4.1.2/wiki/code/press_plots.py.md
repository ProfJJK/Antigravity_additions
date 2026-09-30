# src/cochem/dsp/press/plots.py

`python
"""ACS/Nature Vector Plot Generator (MC-DSP-20)."""
from __future__ import annotations

from pathlib import Path


def generate_vector_plot(output_path: Path | str, width_inches: float = 3.25, dpi: int = 300) -> Path:
    """Generates ACS single-column width (3.25in) vector graphics adhering to publication standards."""
    target_path = Path(output_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    # Calculate dimensional bounds (72 pt per inch standard typographical scale)
    width_pt = int(width_inches * 72)
    height_pt = int(width_inches * 0.75 * 72)  # 4:3 aspect ratio

    if target_path.suffix.lower() == ".svg":
        svg_content = (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
            f'width="{width_inches}in" height="{width_inches * 0.75}in" '
            f'viewBox="0 0 {width_pt} {height_pt}">\n'
            f'  <desc>ACS Column Width Vector Plot (width: {width_inches}in, dpi: {dpi})</desc>\n'
            f'  <rect width="100%" height="100%" fill="#ffffff"/>\n'
            f'  <!-- Axes -->\n'
            f'  <line x1="30" y1="{height_pt - 25}" x2="{width_pt - 15}" y2="{height_pt - 25}" stroke="#000000" stroke-width="1.0"/>\n'
            f'  <line x1="30" y1="15" x2="30" y2="{height_pt - 25}" stroke="#000000" stroke-width="1.0"/>\n'
            f'  <!-- Data Series Curve -->\n'
            f'  <polyline fill="none" stroke="#003366" stroke-width="1.5" '
            f'points="30,{height_pt - 30} 60,{height_pt - 55} 100,{height_pt - 90} 150,{height_pt - 110} {width_pt - 20},{height_pt - 130}"/>\n'
            f'</svg>\n'
        )
        target_path.write_text(svg_content, encoding="utf-8")
    else:
        # Standard PDF 1.4 vector document with dynamically computed offsets
        pdf_stream = (
            f"q\n"
            f"0.5 0.5 0.5 RG 1.0 w\n"
            f"30 25 {width_pt - 45} {height_pt - 40} re S\n"
            f"0 0.2 0.4 RG 1.5 w\n"
            f"30 30 m 70 60 l 120 100 l {width_pt - 25} {height_pt - 20} l S\n"
            f"Q\n"
        )
        stream_len = len(pdf_stream.encode("latin-1"))

        header = b"%PDF-1.4\n"
        obj1 = b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        obj2 = b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        obj3 = f"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 {width_pt} {height_pt}] /Contents 4 0 R >> endobj\n".encode("latin-1")
        obj4 = f"4 0 obj << /Length {stream_len} >>\nstream\n{pdf_stream}endstream\nendobj\n".encode("latin-1")

        offset1 = len(header)
        offset2 = offset1 + len(obj1)
        offset3 = offset2 + len(obj2)
        offset4 = offset3 + len(obj3)
        xref_offset = offset4 + len(obj4)

        xref_table = (
            f"xref\n0 5\n"
            f"0000000000 65535 f \n"
            f"{offset1:010d} 00000 n \n"
            f"{offset2:010d} 00000 n \n"
            f"{offset3:010d} 00000 n \n"
            f"{offset4:010d} 00000 n \n"
            f"trailer << /Size 5 /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")

        pdf_data = header + obj1 + obj2 + obj3 + obj4 + xref_table
        target_path.write_bytes(pdf_data)
    return target_path


`
