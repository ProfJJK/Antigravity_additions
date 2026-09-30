"""Anti-Spoof AST Linter (MC-DSP-07, MC-DSP-08, MC-DSP-09)."""
from __future__ import annotations
import ast
from typing import Any

FORBIDDEN_MODULES = {"unittest.mock", "mock"}
FORBIDDEN_NAMES = {"MagicMock", "monkeypatch"}
FORBIDDEN_CALLS = {"np.zeros", "np.ones", "np.eye"}


class AntiSpoofLinter(ast.NodeVisitor):
    """Traverses Python AST to flag forbidden mock imports and synthetic array generators."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name in FORBIDDEN_MODULES:
                self.violations.append(f"Forbidden mock import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module in FORBIDDEN_MODULES:
            self.violations.append(f"Forbidden mock from-import: {node.module}")
        for alias in node.names:
            if alias.name in FORBIDDEN_NAMES:
                self.violations.append(f"Forbidden mock name import: {alias.name}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # Check for np.zeros / np.ones / np.eye synthetic fixtures (MC-DSP-09)
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"zeros", "ones", "eye"}:
            self.violations.append(f"Synthetic array call: {node.func.attr}")
        elif isinstance(node.func, ast.Name) and node.func.id in {"zeros", "ones", "eye"}:
            self.violations.append(f"Synthetic array call: {node.func.id}")
        self.generic_visit(node)

    def lint(self, source_code: str) -> list[str]:
        """Convenience method to parse and lint source code."""
        tree = ast.parse(source_code)
        self.visit(tree)
        return self.violations
