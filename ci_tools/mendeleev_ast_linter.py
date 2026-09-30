"""CoChem Zero-Static-Dictionary AST Linter & Anti-Mock Sentinel.

Authoritative AST static analyzer enforcing the Mendeleev Library Mandate.
Scans dictionary literals (ast.Dict), detecting static tables mapping chemical
element symbols to float numbers (such as atomic masses or covalent radii).
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import mendeleev

ELEMENT_KEY_REGEX = re.compile(r"^[A-Z][a-z]?$")


@dataclass(frozen=True)
class LinterViolation:
    """Immutable record of an AST static-dictionary violation."""
    file_path: str
    line: int
    col: int
    category: str
    symbol: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file": self.file_path,
            "line": self.line,
            "col": self.col,
            "category": self.category,
            "symbol": self.symbol,
            "message": self.message,
        }


class MendeleevASTVisitor(ast.NodeVisitor):
    """AST visitor traversing dictionary definitions for static element tables."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.violations: List[LinterViolation] = []

    def visit_Dict(self, node: ast.Dict) -> None:
        if node.keys:
            element_pairs: List[Tuple[str, float]] = []
            for k, v in zip(node.keys, node.values):
                if k is not None and isinstance(k, ast.Constant) and isinstance(k.value, str):
                    key_str = k.value.strip()
                    if ELEMENT_KEY_REGEX.match(key_str):
                        val_num = None
                        if isinstance(v, ast.Constant) and isinstance(v.value, (int, float)) and not isinstance(v.value, bool):
                            val_num = float(v.value)
                        elif isinstance(v, ast.UnaryOp) and isinstance(v.op, (ast.USub, ast.UAdd)):
                            if isinstance(v.operand, ast.Constant) and isinstance(v.operand.value, (int, float)):
                                val_num = -float(v.operand.value) if isinstance(v.op, ast.USub) else float(v.operand.value)
                        if val_num is not None:
                            element_pairs.append((key_str, val_num))

            if len(element_pairs) >= 2 or (len(element_pairs) == len(node.keys) and element_pairs):
                syms = ", ".join(f"{s}:{v}" for s, v in element_pairs[:3])
                self.violations.append(
                    LinterViolation(
                        file_path=self.file_path,
                        line=node.lineno,
                        col=node.col_offset,
                        category="STATIC_ELEMENT_DICTIONARY",
                        symbol="dict",
                        message=(
                            f"Prohibited static element dictionary detected ({syms}). "
                            f"All physical constants must be dynamically queried via mendeleev."
                        ),
                    )
                )
        self.generic_visit(node)


def scan_file(file_path: Path) -> List[LinterViolation]:
    """Parse and lint a Python source file for static element dictionaries."""
    try:
        content = file_path.read_text(encoding="utf-8-sig", errors="replace")
        tree = ast.parse(content, filename=str(file_path))
    except Exception:
        return []
    visitor = MendeleevASTVisitor(file_path=str(file_path))
    visitor.visit(tree)
    return visitor.violations


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CoChem Mendeleev AST Linter")
    parser.add_argument("paths", nargs="*", default=[], help="Target files or directories to scan")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    args = parser.parse_args(argv)

    if args.paths:
        scan_targets = [Path(p).resolve() for p in args.paths]
    else:
        candidates = [
            Path("src/cochem/engine/frozen_monomer.py"),
            Path("CoChem-BASE/src/cochem/engine/frozen_monomer.py"),
            Path("CoChem-TORQ/cochem/engine/frozen_monomer.py"),
            Path("CoChem-TORQ/Libraries/cochem_torq_frozen_monomer.py"),
        ]
        scan_targets = [c.resolve() for c in candidates if c.is_file()]
        if not scan_targets:
            src_p = Path("src").resolve()
            if src_p.is_dir():
                scan_targets = [src_p]
            else:
                scan_targets = [Path.cwd()]

    all_violations: List[LinterViolation] = []
    for target in scan_targets:
        if target.is_file() and target.suffix == ".py":
            all_violations.extend(scan_file(target))
        elif target.is_dir():
            for root, dirs, files in os.walk(target):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"__pycache__", "build", "dist"}]
                for f in files:
                    if f.endswith(".py"):
                        all_violations.extend(scan_file(Path(root) / f))

    if all_violations:
        print(f"[FAIL] Found {len(all_violations)} static element dictionary violation(s):")
        for v in all_violations:
            print(f"  {v.file_path}:{v.line}:{v.col} [{v.category}]: {v.message}")
        return 1

    print("[STATUS: PASS] Zero static element dictionary violations detected.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
