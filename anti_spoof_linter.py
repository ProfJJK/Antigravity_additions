"""CoChem Anti-Spoof Linter & Zero-Mock Enforcement Sentinel.

Authoritative AST static analyzer enforcing Zero-Mock & Anti-Spoofing Protocol v4.
Enforces zero mock imports, no NotImplementedError stubs, no empty pass bodies,
and bans synthetic numpy generators and test-skipping markers.
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

AMNESTY_FILENAME: str = ".anti_spoof_amnesty.json"

BANNED_MOCK_MODULES: Set[str] = {
    "unittest.mock",
    "mock",
    "pytest_mock",
}

BANNED_MOCK_ATTRIBUTES: Set[str] = {
    "MagicMock",
    "Mock",
    "patch",
    "PropertyMock",
    "AsyncMock",
    "create_autospec",
    "NonCallableMock",
    "call_args",
    "mock_open",
}

BANNED_NUMPY_GENERATORS: Set[str] = {
    "linspace",
    "zeros",
    "ones",
    "eye",
    "identity",
    "sin",
    "rand",
    "randn",
    "normal",
    "uniform",
    "choice",
    "randint",
}

BANNED_IDENTIFIER_WORDS: Set[str] = {
    "dummy",
    "fake",
    "placeholder",
    "synthetic",
    "stub",
    "mock",
}

BANNED_SKIP_SYMBOLS: Set[str] = {
    "skip",
    "skipif",
    "xfail",
    "exit",
    "skipIf",
    "skipUnless",
    "skipTest",
}

EXCLUDED_DIRS: Set[str] = {
    "build",
    "dist",
    ".venv",
    ".conda",
    "venv",
    "site-packages",
    "artifacts",
    "datasets",
    "data",
    "__pycache__",
    ".pytest_cache",
    ".git",
    ".vscode",
    ".idea",
    ".trash",
    "Report_Archive",
    "scratch",
    "node_modules",
}


@dataclass(frozen=True)
class Violation:
    """Immutable record of an anti-spoof or zero-mock compliance violation."""
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


class SpoofVisitor(ast.NodeVisitor):
    """AST Visitor detecting prohibited mock patterns, stubs, and synthetic generators."""

    def __init__(self, filepath: Path, rel_path: str, is_exempt: bool) -> None:
        self.filepath = filepath
        self.rel_path = rel_path
        self.is_exempt = is_exempt
        self.violations: List[Violation] = []
        self.is_test_file = "test" in filepath.stem.lower() or "tests" in filepath.parts
        self.pytest_aliases: Set[str] = {"pytest"}
        self.unittest_aliases: Set[str] = {"unittest"}

    def _check_ident(self, name: str, node: ast.AST, context: str) -> None:
        if self.is_exempt:
            return
        if self.is_test_file and name.startswith(("test_", "Test")):
            return

        name_lower = name.lower()
        tokens = set(re.findall(r"[a-z]+", re.sub(r"([A-Z])", r" \1", name).lower()))
        for kw in BANNED_IDENTIFIER_WORDS:
            if kw in tokens or kw in name_lower:
                lineno = getattr(node, "lineno", 1)
                col = getattr(node, "col_offset", 0)
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line=lineno,
                        col=col,
                        category="BANNED_IDENTIFIER",
                        symbol=name,
                        message=f"Banned identifier word '{kw}' detected in {context} '{name}'",
                    )
                )

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name == "pytest":
                self.pytest_aliases.add(alias.asname or "pytest")
            elif alias.name == "unittest":
                self.unittest_aliases.add(alias.asname or "unittest")

            is_mock = any(
                alias.name == bm or alias.name.startswith(bm + ".")
                for bm in BANNED_MOCK_MODULES
            )
            if is_mock and not self.is_exempt:
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line=node.lineno,
                        col=node.col_offset,
                        category="MOCK_IMPORT",
                        symbol=alias.name,
                        message=f"Prohibited mock module import '{alias.name}'",
                    )
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        is_mock_mod = any(
            mod == bm or mod.startswith(bm + ".")
            for bm in BANNED_MOCK_MODULES
        ) or (mod == "unittest" and any(a.name == "mock" for a in node.names))

        if is_mock_mod and not self.is_exempt:
            for alias in node.names:
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line=node.lineno,
                        col=node.col_offset,
                        category="MOCK_IMPORT",
                        symbol=f"{mod}.{alias.name}" if mod else alias.name,
                        message=f"Prohibited mock symbol import '{alias.name}' from '{mod}'",
                    )
                )
        else:
            if mod == "numpy" and not self.is_exempt:
                for alias in node.names:
                    if alias.name in BANNED_NUMPY_GENERATORS:
                        self.violations.append(
                            Violation(
                                file_path=self.rel_path,
                                line=node.lineno,
                                col=node.col_offset,
                                category="SYNTHETIC_DATA",
                                symbol=alias.name,
                                message=f"Prohibited synthetic numpy generator '{alias.name}'",
                            )
                        )
            elif (mod == "pytest" or mod.startswith("pytest.")) and not self.is_exempt:
                for alias in node.names:
                    if alias.name in BANNED_SKIP_SYMBOLS:
                        self.violations.append(
                            Violation(
                                file_path=self.rel_path,
                                line=node.lineno,
                                col=node.col_offset,
                                category="PYTEST_SKIP",
                                symbol=alias.name,
                                message=f"Prohibited pytest test suppression symbol '{alias.name}'",
                            )
                        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_ident(node.name, node, "function")
        self._check_function_stubs(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_ident(node.name, node, "async function")
        self._check_function_stubs(node)
        self.generic_visit(node)

    def _check_function_stubs(self, node: Union[ast.FunctionDef, ast.AsyncFunctionDef]) -> None:
        if self.is_exempt:
            return
        decorators = [d.id for d in node.decorator_list if isinstance(d, ast.Name)]
        decorators += [d.attr for d in node.decorator_list if isinstance(d, ast.Attribute)]
        if "abstractmethod" in decorators or "overload" in decorators:
            return

        body = [
            n for n in node.body
            if not (
                isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)
            )
        ]
        if not body or all(
            isinstance(n, ast.Pass)
            or (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and n.value.value is ...)
            for n in body
        ):
            self.violations.append(
                Violation(
                    file_path=self.rel_path,
                    line=node.lineno,
                    col=node.col_offset,
                    category="EMPTY_PASS_STUB",
                    symbol=node.name,
                    message=f"Empty pass/ellipsis stub in function '{node.name}'",
                )
            )

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._check_ident(node.name, node, "class")
        if not self.is_exempt:
            base_names = set()
            for b in node.bases:
                if isinstance(b, ast.Name):
                    base_names.add(b.id)
                elif isinstance(b, ast.Attribute):
                    base_names.add(b.attr)
            is_allowed_empty = any(
                b in {"Exception", "BaseException", "UserWarning", "Warning", "Protocol", "ABC"}
                or "Error" in b
                for b in base_names
            )
            body = [
                n for n in node.body
                if not (
                    isinstance(n, ast.Expr)
                    and isinstance(n.value, ast.Constant)
                    and isinstance(n.value.value, str)
                )
            ]
            if not is_allowed_empty and (
                not body
                or all(
                    isinstance(n, ast.Pass)
                    or (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and n.value.value is ...)
                    for n in body
                )
            ):
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line=node.lineno,
                        col=node.col_offset,
                        category="EMPTY_PASS_STUB",
                        symbol=node.name,
                        message=f"Empty pass/ellipsis stub in class '{node.name}'",
                    )
                )
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if not self.is_exempt and node.exc:
            exc_name = ""
            if isinstance(node.exc, ast.Name):
                exc_name = node.exc.id
            elif isinstance(node.exc, ast.Call) and isinstance(node.exc.func, ast.Name):
                exc_name = node.exc.func.id
            if exc_name == "NotImplementedError":
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line=node.lineno,
                        col=node.col_offset,
                        category="NOT_IMPLEMENTED_ERROR",
                        symbol=exc_name,
                        message="Forbidden NotImplementedError raise dead-end",
                    )
                )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if not self.is_exempt and node.id in BANNED_MOCK_ATTRIBUTES:
            self.violations.append(
                Violation(
                    file_path=self.rel_path,
                    line=node.lineno,
                    col=node.col_offset,
                    category="MOCK_USAGE",
                    symbol=node.id,
                    message=f"Prohibited use of mock symbol '{node.id}'",
                )
            )
        if isinstance(node.ctx, (ast.Store, ast.Param)):
            self._check_ident(node.id, node, "variable")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if not self.is_exempt:
            if node.attr in BANNED_MOCK_ATTRIBUTES:
                self.violations.append(
                    Violation(
                        file_path=self.rel_path,
                        line=node.lineno,
                        col=node.col_offset,
                        category="MOCK_USAGE",
                        symbol=node.attr,
                        message=f"Prohibited use of mock attribute '{node.attr}'",
                    )
                )
            if node.attr in BANNED_SKIP_SYMBOLS:
                if isinstance(node.value, ast.Name) and node.value.id in self.pytest_aliases:
                    self.violations.append(
                        Violation(
                            file_path=self.rel_path,
                            line=node.lineno,
                            col=node.col_offset,
                            category="PYTEST_SKIP",
                            symbol=f"{node.value.id}.{node.attr}",
                            message=f"Prohibited {node.value.id}.{node.attr} test suppression",
                        )
                    )
        self.generic_visit(node)


def check_file(file_path: Path) -> List[Violation]:
    """Analyze a single Python file for AST anti-spoof violations."""
    is_exempt = file_path.name in {
        "anti_spoof_linter.py",
        "mendeleev_ast_linter.py",
    }
    try:
        content = file_path.read_text(encoding="utf-8-sig", errors="replace")
        tree = ast.parse(content, filename=str(file_path))
    except Exception as exc:
        return [
            Violation(
                file_path=str(file_path),
                line=1,
                col=0,
                category="PARSE_ERROR",
                symbol="ast.parse",
                message=str(exc),
            )
        ]

    visitor = SpoofVisitor(filepath=file_path, rel_path=str(file_path), is_exempt=is_exempt)
    visitor.visit(tree)
    return visitor.violations


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CoChem Anti-Spoof Linter")
    parser.add_argument("targets", nargs="*", default=[], help="File(s) to audit")
    parser.add_argument("--strict", action="store_true", default=False, help="Fail with non-zero exit code")
    args = parser.parse_args(argv)

    if args.targets:
        targets = [Path(t).resolve() for t in args.targets]
    else:
        # Default targets: audit engine source and mirror files
        candidates = [
            Path("src/cochem/engine/frozen_monomer.py"),
            Path("CoChem-BASE/src/cochem/engine/frozen_monomer.py"),
            Path("CoChem-TORQ/cochem/engine/frozen_monomer.py"),
            Path("CoChem-TORQ/Libraries/cochem_torq_frozen_monomer.py"),
        ]
        targets = [c.resolve() for c in candidates if c.is_file()]
        if not targets:
            src_dir = Path("src").resolve()
            if src_dir.is_dir():
                targets = [src_dir]
            else:
                targets = [Path.cwd()]

    all_violations: Dict[str, List[Violation]] = {}
    for tp in targets:
        if tp.is_file() and tp.suffix == ".py":
            v = check_file(tp)
            if v:
                all_violations[str(tp)] = v
        elif tp.is_dir():
            for root, dirs, files in os.walk(tp):
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith(".")]
                for f in files:
                    if f.endswith(".py"):
                        p = Path(root) / f
                        v = check_file(p)
                        if v:
                            all_violations[str(p)] = v

    total_violations = sum(len(v_list) for v_list in all_violations.values())
    if total_violations == 0:
        print("[LINT SUCCESS] Zero-mock compliance verified.")
        return 0

    print(f"[SPOOFING DETECTED] Found {total_violations} violation(s):")
    for f, v_list in all_violations.items():
        print(f"File: {f}")
        for v in v_list:
            print(f"  Line {v.line}:{v.col} [{v.category}]: {v.message}")

    return 1 if (total_violations > 0 and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
