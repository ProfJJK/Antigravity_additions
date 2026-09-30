# scratch/assemble_ast_eradication_engine.py
"""Assembler script to construct the complete, unified ast_eradication_engine.py."""

from pathlib import Path

CODE = '''\
# -*- coding: utf-8 -*-
"""AST defect inspector, production purge engine, atomic remediation engine, dual-tree synchronizer and CLI.

The module performs four families of operations:

1. Read-only diagnostics. A structural pass driven by :class:`ASTDefectVisitor`
   (an ``ast.NodeVisitor``) detects prohibited test-double imports and attribute
   access, dead-end ``NotImplementedError`` raises, empty ``pass`` statements in
   execution bodies and counterfeit identifiers or marker literals. A ``pass``
   that forms a class body definition is exempt. A lexical pass driven by
   :func:`scan_lexical_tokens` (``tokenize``) detects marker comments and test
   suppression directives. In ``strict_comments`` mode every comment other than a
   shebang or PEP 263 encoding declaration is reported as well.
2. Eradication. :func:`eradicate_source` purges comments, empty ``pass``
   statements and hollow functions whose only behaviour is ``pass`` or
   ``raise NotImplementedError``. Every sanitized result is verified to parse into
   exactly the pruned syntax tree before it is returned; control blocks that would
   become empty are left untouched and remain reported instead of being laundered.
3. Production purge. :class:`StubEradicationTransformer`,
   :class:`PassEradicationTransformer` and :class:`ProductionPurgeTransformer`
   rewrite syntax trees in memory, :class:`CommentSanitizationEngine` excises
   unauthorized marker comments from the ``tokenize`` stream, and
   :func:`purge_production_stubs_and_comments` combines a token-stream comment
   purge with coordinate-exact source surgery so legitimate comments, docstrings
   and string literals survive byte-for-byte. Dead-end raises become
   :class:`~cochem.core.exceptions.PhysicalConvergenceError` or, in hardware
   contexts, :class:`~cochem.core.exceptions.HardwareTopologyError`; abstract
   interface declarations become docstring-only bodies. The on-disk rewrite and
   the in-memory transformers share one deterministic planner, and the on-disk
   result must parse into exactly the tree the in-memory transformer produces.
4. Crash-safe disk mutation. :func:`write_atomic` stages bytes in an ephemeral
   ``.tmp_*`` file inside the destination directory, verifies its SHA-256 digest,
   commits through ``os.replace`` and restores the pre-write bytes if the
   committed digest cannot be verified. :func:`sync_trees` uses the same
   primitive to enforce bitwise parity between ``src/cochem/core`` and
   ``cochem/core``.

The :func:`main` entrypoint exposes ``--check``, ``--remediate``, ``--purge``,
``--diff``, ``--sync-trees`` and ``--json-output``.
"""

import argparse
import ast
import difflib
import functools
import hashlib
import io
import json
import os
import re
import sys
import time
import tokenize
import types
import uuid
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    List,
    Optional,
    Pattern,
    Sequence,
    Set,
    Tuple,
    Union,
)

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cochem.core.config import (
    DEFAULT_PURGE_CONFIG,
    PURGE_EXCLUDED_DIR_NAMES,
    PURGE_EXCLUDED_FILE_NAMES,
    PurgeConfig,
)
from cochem.core.exceptions import (
    AtomicWriteError,
    CoChemError,
    HardwareTopologyError,
    PhysicalConvergenceError,
    PurgeVerificationError,
)

__all__ = (
    "DefectCategory",
    "ASTDefectRecord",
    "FileASTReport",
    "EradicationPlan",
    "ASTDefectVisitor",
    "ASTEradicationEngine",
    "CoChemError",
    "AtomicWriteError",
    "PurgeVerificationError",
    "DualTreeSyncResult",
    "PhysicalConvergenceError",
    "HardwareTopologyError",
    "StubEradicationTransformer",
    "PassEradicationTransformer",
    "ProductionPurgeTransformer",
    "CommentSanitizationEngine",
    "strip_unauthorized_comments",
    "purge_source",
    "purge_production_stubs_and_comments",
    "scan_purge_defects",
    "scan_lexical_tokens",
    "eradicate_source",
    "compute_sha256",
    "write_atomic",
    "remediate_file",
    "compute_diff",
    "sync_trees",
    "build_cli_parser",
    "main",
)


class DefectCategory(str, Enum):
    """Taxonomy of the nine defect categories recognised by the engine."""

    PROHIBITED_MODULE_IMPORT = "PROHIBITED_MODULE_IMPORT"
    PROHIBITED_ATTRIBUTE_IMPORT = "PROHIBITED_ATTRIBUTE_IMPORT"
    PROHIBITED_ATTRIBUTE_ACCESS = "PROHIBITED_ATTRIBUTE_ACCESS"
    UNIMPLEMENTED_INTERFACE_EXCEPTION = "UNIMPLEMENTED_INTERFACE_EXCEPTION"
    EMPTY_PASS_STATEMENT = "EMPTY_PASS_STATEMENT"
    UNAUTHORIZED_COMMENT = "UNAUTHORIZED_COMMENT"
    TEST_SUPPRESSION_DIRECTIVE = "TEST_SUPPRESSION_DIRECTIVE"
    PROHIBITED_IDENTIFIER = "PROHIBITED_IDENTIFIER"
    PROHIBITED_LITERAL = "PROHIBITED_LITERAL"


_PURGE_CATEGORIES: FrozenSet[str] = frozenset(
    {
        DefectCategory.UNIMPLEMENTED_INTERFACE_EXCEPTION.value,
        DefectCategory.EMPTY_PASS_STATEMENT.value,
        DefectCategory.UNAUTHORIZED_COMMENT.value,
    }
)
_PROHIBITED_MODULE_PATTERN = re.compile(r"(?:unittest\.mock|pytest_mock|mockito)(?:\..+)?")
_PROHIBITED_SYMBOL_PATTERN = re.compile(r"(?:Magic|Property|Async)?Mock|patch")
_PROHIBITED_ACCESS_PATTERN = re.compile(r"(?:[\w.]+\.)?monkeypatch|(?:[\w.]+\.)?mock\.patch")
_UNIMPLEMENTED_EXCEPTION_NAME = "NotImplementedError"
_COMMENT_PATTERN = re.compile(r"#\s*(?:TODO|FIXME|mock|stub|placeholder|hack)\b", re.IGNORECASE)
_UNAUTHORIZED_COMMENT_PATTERN = _COMMENT_PATTERN
_SUPPRESSION_PATTERN = re.compile(
    r"pytest\.mark\.skip(?:if)?|pytest\.skip|unittest\.skip(?:If|Unless)?"
)
_IDENTIFIER_PATTERN = re.compile(r"mock|stub|fake_data", re.IGNORECASE)
_LITERAL_PATTERN = re.compile(r"\s*(?:mock|stub|mocked|stubbed|fake[_ ]data)\s*", re.IGNORECASE)
_GENERATED_TEXT_BLOCKLIST = re.compile(r"todo|fixme|stub|mock|hack|placeholder", re.IGNORECASE)
_PHYSICAL_LINE_SPLIT = re.compile(r"\r\n|\r|\n")
_LINE_WITH_TERMINATOR = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")
_ENCODING_COOKIE_PATTERN = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*[-\w.]+")
_FIRST_TERMINATOR_PATTERN = re.compile(r"\r\n|\r|\n")
_WORD_PATTERN = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")

_BOM = "\ufeff"
_HASH_CHUNK_SIZE = 1048576
_SHARING_RETRY_WINDOW_SECONDS = 10.0
_SHARING_RETRY_INTERVAL_SECONDS = 0.05
_TEMP_PREFIX = ".tmp_"
_MAX_TEMP_NAME_TAIL = 150
_SOURCE_LABEL = "<string>"
_DEFAULT_PATTERN = "*.py"
_EXCLUDED_DIR_NAMES: FrozenSet[str] = PURGE_EXCLUDED_DIR_NAMES
_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)
_LOOP_NODES = (ast.For, ast.AsyncFor, ast.While)
_ABSTRACT_DECORATORS: FrozenSet[str] = frozenset({"abstractmethod", "abc.abstractmethod"})
_PHYSICAL_EXCEPTION_NAME = "PhysicalConvergenceError"
_HARDWARE_EXCEPTION_NAME = "HardwareTopologyError"

_EDIT_DROP = "drop"
_EDIT_DOCSTRING = "docstring"
_EDIT_CONTINUE = "continue"

_NOOP_DOCSTRING_FALLBACK = "Complete without side effects and return None."
_ABSTRACT_DOCSTRING_FALLBACK = "Declare an abstract interface; concrete subclasses provide the behaviour."
_IMPORT_KEYWORD_PATTERN = re.compile(r"\bimport\b")
_LEXICAL_SKIP_TYPES = frozenset(
    {tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT}
)


def _category_value(value: Union[DefectCategory, str]) -> str:
    """Return the plain string value of a defect category."""
    return value.value if isinstance(value, DefectCategory) else str(value)


def _strip_bom(source: str) -> str:
    """Return ``source`` without a leading byte order mark."""
    return source[1:] if source.startswith(_BOM) else source


def _split_physical_lines(source: str) -> List[str]:
    """Split source text on the same line terminators the Python tokenizer uses."""
    return _PHYSICAL_LINE_SPLIT.split(source)


def _split_with_terminators(text: str) -> List[str]:
    """Split text into physical lines while keeping each line terminator."""
    if not text:
        return []
    return _LINE_WITH_TERMINATOR.findall(text)


def _line_terminator(line: str) -> str:
    """Return the terminator that ends a physical line, or an empty string."""
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith(("\n", "\r")):
        return line[-1]
    return ""


def _strip_terminator(line: str) -> str:
    """Return a physical line without its terminator."""
    term = _line_terminator(line)
    return line[:-len(term)] if term else line


def _char_column(line: str, byte_col: int) -> int:
    """Convert an AST UTF-8 byte offset into a character column of ``line``."""
    if byte_col <= 0:
        return 0
    encoded = line.encode("utf-8", errors="surrogatepass")
    clamped = min(max(0, byte_col), len(encoded))
    return len(encoded[:clamped].decode("utf-8", errors="ignore"))


def _is_exempt_comment(row: int, comment_text: str, line_text: str) -> bool:
    """Return True for a shebang on line 1 or a PEP 263 encoding declaration."""
    if row == 1 and comment_text.startswith("#!"):
        return True
    if row in (1, 2) and _ENCODING_COOKIE_PATTERN.match(line_text):
        return True
    return False


def _detect_terminator(text: str) -> str:
    """Return the first line terminator used by ``text`` (``\\n`` by default)."""
    found = _FIRST_TERMINATOR_PATTERN.search(text)
    if found:
        return found.group(0)
    return "\n"


def _describe_error(error: BaseException) -> str:
    """Render a parse, decode or tokenize failure as a single diagnostic line."""
    cls_name = type(error).__name__
    text = str(error).strip()
    return f"{cls_name}: {text}" if text else cls_name


def _sha256_bytes(data: bytes) -> str:
    """Return the hexadecimal SHA-256 digest of ``data``."""
    return hashlib.sha256(data).hexdigest()


def _decode_source_bytes(data: bytes) -> str:
    """Decode Python source bytes honouring PEP 263 declarations and UTF-8 BOM."""
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    return data.decode(encoding)


class ASTDefectRecord(BaseModel):
    """Immutable record of an individual architectural defect."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_path: str = Field(...)
    line: int = Field(..., ge=1)
    column: int = Field(..., ge=0)
    defect_type: DefectCategory = Field(...)
    snippet: str = Field(...)
    message: str = Field(...)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-compatible dictionary representation."""
        return self.model_dump(mode="json")

    def to_json(self, indent: Optional[int] = None) -> str:
        """Return a JSON string representation."""
        return json.dumps(self.to_dict(), indent=indent)


class FileASTReport(BaseModel):
    """Immutable assessment of a single inspected file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_path: str = Field(...)
    defects: List[ASTDefectRecord] = Field(default_factory=list)
    is_compliant: bool = Field(default=True)
    defect_count: int = Field(default=0, ge=0)
    file_hash: Optional[str] = Field(default=None)
    parse_error: Optional[str] = Field(default=None)

    @model_validator(mode="after")
    def validate_consistency(self) -> "FileASTReport":
        """Reject reports whose aggregate fields contradict their defect records."""
        expected_count = len(self.defects)
        if self.defect_count != expected_count:
            raise ValueError(
                f"defect_count={self.defect_count} contradicts defects list length {expected_count}"
            )
        expected_compliant = expected_count == 0 and self.parse_error is None
        if self.is_compliant != expected_compliant:
            raise ValueError(
                f"is_compliant={self.is_compliant} contradicts defects/parse_error state "
                f"(expected {expected_compliant})"
            )
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-compatible dictionary representation."""
        return self.model_dump(mode="json")

    def to_json(self, indent: Optional[int] = None) -> str:
        """Return a JSON string representation."""
        return json.dumps(self.to_dict(), indent=indent)


class EradicationPlan(BaseModel):
    """Aggregate report over an inspection sweep across one or more files."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reports: List[FileASTReport] = Field(default_factory=list)
    total_files: int = Field(default=0, ge=0)
    total_defects: int = Field(default=0, ge=0)
    is_compliant: bool = Field(default=True)

    @model_validator(mode="after")
    def validate_consistency(self) -> "EradicationPlan":
        """Reject plans whose totals contradict their reports."""
        expected_files = len(self.reports)
        if self.total_files != expected_files:
            raise ValueError(
                f"total_files={self.total_files} contradicts reports length {expected_files}"
            )
        expected_defects = sum(report.defect_count for report in self.reports)
        if self.total_defects != expected_defects:
            raise ValueError(
                f"total_defects={self.total_defects} contradicts defects sum {expected_defects}"
            )
        expected_compliant = all(report.is_compliant for report in self.reports)
        if self.is_compliant != expected_compliant:
            raise ValueError(
                f"is_compliant={self.is_compliant} contradicts file reports compliance state"
            )
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-compatible dictionary representation."""
        return self.model_dump(mode="json")

    def to_json(self, indent: Optional[int] = None) -> str:
        """Return a JSON string representation."""
        return json.dumps(self.to_dict(), indent=indent)


class DualTreeSyncResult(BaseModel):
    """Outcome of a bitwise synchronization between the source tree and its mirror."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_root: str = Field(...)
    mirror_root: str = Field(...)
    copied_files: List[str] = Field(default_factory=list)
    unchanged_files: List[str] = Field(default_factory=list)
    dry_run: bool = Field(default=False)
    is_synchronized: bool = Field(default=True)

    @model_validator(mode="after")
    def validate_consistency(self) -> "DualTreeSyncResult":
        expected = not self.copied_files if not self.dry_run else True
        if self.is_synchronized != expected:
            raise ValueError(
                f"is_synchronized={self.is_synchronized} contradicts dry_run/copied state"
            )
        return self

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _plan_from_reports(reports: Sequence[FileASTReport]) -> EradicationPlan:
    """Aggregate file reports into a validated :class:`EradicationPlan`."""
    reports_list = list(reports)
    total_files = len(reports_list)
    total_defects = sum(report.defect_count for report in reports_list)
    is_compliant = all(report.is_compliant for report in reports_list)
    return EradicationPlan(
        reports=reports_list,
        total_files=total_files,
        total_defects=total_defects,
        is_compliant=is_compliant,
    )


def _build_report(
    file_path: str,
    defects: Sequence[ASTDefectRecord],
    file_hash: Optional[str] = None,
    parse_error: Optional[str] = None,
) -> FileASTReport:
    """Build a self-consistent :class:`FileASTReport` with deterministic defect ordering."""
    ordered = sorted(
        defects,
        key=lambda record: (
            record.line,
            record.column,
            _category_value(record.defect_type),
        ),
    )
    defect_count = len(ordered)
    is_compliant = defect_count == 0 and parse_error is None
    return FileASTReport(
        file_path=file_path,
        defects=ordered,
        is_compliant=is_compliant,
        defect_count=defect_count,
        file_hash=file_hash,
        parse_error=parse_error,
    )


def _dotted_name(node: ast.AST) -> Optional[str]:
    """Resolve ``a.b.c`` attribute chains rooted in a plain name."""
    parts: List[str] = []
    current: ast.AST = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return ".".join(reversed(parts))
    return None


def _raises_unimplemented(node: ast.AST) -> bool:
    """Return True when ``node`` is a ``raise NotImplementedError`` statement."""
    if not isinstance(node, ast.Raise) or node.exc is None:
        return False
    raised = node.exc
    target = raised.func if isinstance(raised, ast.Call) else raised
    if isinstance(target, ast.Name):
        return target.id == _UNIMPLEMENTED_EXCEPTION_NAME
    if isinstance(target, ast.Attribute):
        return target.attr == _UNIMPLEMENTED_EXCEPTION_NAME
    return False


class ASTDefectVisitor(ast.NodeVisitor):
    """Non-destructive structural visitor collecting :class:`ASTDefectRecord` items."""

    def __init__(self, source: str, file_path: str = _SOURCE_LABEL) -> None:
        self.source = source
        self.file_path = file_path
        self.source_lines = _split_physical_lines(_strip_bom(source))
        self.defects: List[ASTDefectRecord] = []

    def get_snippet(self, lineno: int) -> str:
        """Return the physical source line for a 1-indexed line number."""
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1]
        return ""

    def _add(self, lineno: int, byte_col: int, category: DefectCategory, message: str) -> None:
        """Record a defect at an AST line and UTF-8 byte column."""
        line_text = self.get_snippet(lineno)
        self.defects.append(
            ASTDefectRecord(
                file_path=self.file_path,
                line=max(1, lineno),
                column=max(0, _char_column(line_text, byte_col)),
                defect_type=category,
                snippet=line_text,
                message=message,
            )
        )

    def _add_at_node(self, node: ast.AST, category: DefectCategory, message: str) -> None:
        """Record a defect at the start coordinate of ``node``."""
        lineno = getattr(node, "lineno", 1)
        col_offset = getattr(node, "col_offset", 0)
        self._add(lineno, col_offset, category, message)

    def _alias_position(self, node: ast.AST, alias: ast.alias) -> Tuple[int, int]:
        """Return (line, byte column) of an imported alias."""
        alias_lineno = getattr(alias, "lineno", None)
        alias_col = getattr(alias, "col_offset", None)
        if alias_lineno is not None and alias_col is not None:
            return alias_lineno, alias_col
        base_line = getattr(node, "lineno", 1)
        base_col = getattr(node, "col_offset", 0)
        line_text = self.get_snippet(base_line)
        idx = line_text.find(alias.name)
        if idx >= 0:
            byte_idx = len(line_text[:idx].encode("utf-8", errors="surrogatepass"))
            return base_line, byte_idx
        return base_line, base_col

    def _check_identifier(self, name: str, node: ast.AST, kind: str) -> None:
        """Flag ``name`` when it advertises a test double or counterfeit component."""
        if _IDENTIFIER_PATTERN.search(name):
            self._add_at_node(
                node,
                DefectCategory.PROHIBITED_IDENTIFIER,
                f"prohibited {kind} identifier '{name}' implies test double or counterfeit logic",
            )

    def _check_alias_rebinding(self, node: ast.AST, alias: ast.alias) -> None:
        """Flag an import alias whose bound name advertises a test double."""
        bound = alias.asname or alias.name
        if _IDENTIFIER_PATTERN.search(bound):
            alias_line, alias_col = self._alias_position(node, alias)
            self._add(
                alias_line,
                alias_col,
                DefectCategory.PROHIBITED_IDENTIFIER,
                f"import alias binds prohibited identifier '{bound}'",
            )

    def visit_Import(self, node: ast.Import) -> None:
        """Detect prohibited module imports and prohibited alias names."""
        for alias in node.names:
            if _PROHIBITED_MODULE_PATTERN.search(alias.name):
                alias_line, alias_col = self._alias_position(node, alias)
                self._add(
                    alias_line,
                    alias_col,
                    DefectCategory.PROHIBITED_MODULE_IMPORT,
                    f"prohibited test-double module imported: '{alias.name}'",
                )
            self._check_alias_rebinding(node, alias)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Detect prohibited source modules, prohibited symbols and alias names."""
        module_name = "." * node.level + (node.module or "")
        module_prohibited = bool(_PROHIBITED_MODULE_PATTERN.search(module_name))
        for alias in node.names:
            alias_line, alias_col = self._alias_position(node, alias)
            if module_prohibited:
                self._add(
                    alias_line,
                    alias_col,
                    DefectCategory.PROHIBITED_MODULE_IMPORT,
                    f"prohibited test-double module imported: '{module_name}'",
                )
            elif _PROHIBITED_SYMBOL_PATTERN.search(alias.name):
                self._add(
                    alias_line,
                    alias_col,
                    DefectCategory.PROHIBITED_ATTRIBUTE_IMPORT,
                    f"prohibited test-double symbol '{alias.name}' imported from '{module_name}'",
                )
            self._check_alias_rebinding(node, alias)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Detect access to prohibited test-double facilities."""
        dotted = _dotted_name(node)
        if dotted is not None and _PROHIBITED_ACCESS_PATTERN.search(dotted):
            self._add_at_node(
                node,
                DefectCategory.PROHIBITED_ATTRIBUTE_ACCESS,
                f"prohibited test-double attribute accessed: '{dotted}'",
            )
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        """Detect dead-end ``NotImplementedError`` raises."""
        if _raises_unimplemented(node):
            self._add_at_node(
                node,
                DefectCategory.UNIMPLEMENTED_INTERFACE_EXCEPTION,
                "unimplemented interface raise (raise NotImplementedError)",
            )
        self.generic_visit(node)

    def visit_Pass(self, node: ast.Pass) -> None:
        """Detect every empty ``pass`` statement."""
        self._add_at_node(
            node,
            DefectCategory.EMPTY_PASS_STATEMENT,
            "empty pass statement in execution body",
        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Check function names."""
        self._check_identifier(node.name, node, "function")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Check async function names."""
        self._check_identifier(node.name, node, "async function")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Check class names."""
        self._check_identifier(node.name, node, "class")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Check names bound by ``except ... as name`` clauses."""
        if node.name is not None:
            self._check_identifier(node.name, node, "exception handler")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """Check names bound by assignment targets."""
        if isinstance(node.ctx, ast.Store):
            self._check_identifier(node.id, node, "variable")
        self.generic_visit(node)

    def visit_arg(self, node: ast.arg) -> None:
        """Check parameter names."""
        self._check_identifier(node.arg, node, "argument")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        """Detect string constants that are test-double marker literals."""
        if isinstance(node.value, str) and _LITERAL_PATTERN.fullmatch(node.value):
            self._add_at_node(
                node,
                DefectCategory.PROHIBITED_LITERAL,
                f"prohibited test-double marker literal: '{node.value}'",
            )
        self.generic_visit(node)


def _collect_tokens(text: str) -> List[tokenize.TokenInfo]:
    """Return every token ``tokenize.generate_tokens`` yields before exhaustion."""
    stream = io.StringIO(text, newline="")
    collected: List[tokenize.TokenInfo] = []
    try:
        for token in tokenize.generate_tokens(stream.readline):
            collected.append(token)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return collected


def _is_operator(token: tokenize.TokenInfo, symbol: str) -> bool:
    """Return True when ``token`` is the operator ``symbol``."""
    return token.type in (tokenize.OP, tokenize.ERRORTOKEN) and token.string == symbol


def scan_lexical_tokens(
    source: str,
    file_path: str = _SOURCE_LABEL,
    strict_comments: bool = False,
) -> List[ASTDefectRecord]:
    """Scan the lexical token stream using ``tokenize.generate_tokens``."""
    text = _strip_bom(source)
    lines = _split_physical_lines(text)

    def line_text(row: int) -> str:
        if 1 <= row <= len(lines):
            return lines[row - 1]
        return ""

    tokens = _collect_tokens(text)
    records: List[ASTDefectRecord] = []

    for i, token in enumerate(tokens):
        if token.type == tokenize.COMMENT:
            row, col = token.start
            raw_line = line_text(row)
            comment_content = token.string
            if _is_exempt_comment(row, comment_content, raw_line):
                continue
            if _COMMENT_PATTERN.search(comment_content):
                records.append(
                    ASTDefectRecord(
                        file_path=file_path,
                        line=row,
                        column=col,
                        defect_type=DefectCategory.UNAUTHORIZED_COMMENT,
                        snippet=raw_line,
                        message=f"unauthorized marker comment detected: '{comment_content.strip()}'",
                    )
                )
            elif strict_comments:
                records.append(
                    ASTDefectRecord(
                        file_path=file_path,
                        line=row,
                        column=col,
                        defect_type=DefectCategory.UNAUTHORIZED_COMMENT,
                        snippet=raw_line,
                        message=f"comment not permitted in strict mode: '{comment_content.strip()}'",
                    )
                )
            continue

        if (
            token.type == tokenize.NAME
            and token.string in ("pytest", "unittest")
            and i + 1 < len(tokens)
            and _is_operator(tokens[i + 1], ".")
        ):
            chain_parts: List[str] = [token.string]
            cursor = i + 1
            last_end = token.end
            while cursor < len(tokens):
                sep = tokens[cursor]
                if not _is_operator(sep, "."):
                    break
                if cursor + 1 >= len(tokens):
                    break
                part = tokens[cursor + 1]
                if part.type != tokenize.NAME:
                    break
                chain_parts.append(part.string)
                last_end = part.end
                cursor += 2
            dotted = ".".join(chain_parts)
            if _SUPPRESSION_PATTERN.fullmatch(dotted):
                row, col = token.start
                raw_line = line_text(row)
                records.append(
                    ASTDefectRecord(
                        file_path=file_path,
                        line=row,
                        column=col,
                        defect_type=DefectCategory.TEST_SUPPRESSION_DIRECTIVE,
                        snippet=raw_line,
                        message=f"test suppression directive detected: '{dotted}'",
                    )
                )

    records.sort(
        key=lambda record: (
            record.line,
            record.column,
            _category_value(record.defect_type),
        )
    )
    return records


class CommentSanitizationEngine:
    """Tokenize-stream comment stripper that removes unauthorized comment stems."""

    def __init__(
        self,
        strict: bool = False,
        pattern: Optional[Pattern[str]] = None,
    ) -> None:
        self.strict = bool(strict)
        self.pattern = pattern if pattern is not None else _UNAUTHORIZED_COMMENT_PATTERN

    def find_unauthorized_comments(self, source: str) -> List[Tuple[int, int, str]]:
        """Return ``(row, character column, comment text)`` for each comment."""
        body = source[1:] if source.startswith(_BOM) else source
        lines = _split_physical_lines(body)
        found: List[Tuple[int, int, str]] = []
        for token in tokenize.generate_tokens(io.StringIO(body, newline="").readline):
            if token.type != tokenize.COMMENT:
                continue
            row, col = token.start
            line_text = lines[row - 1] if 1 <= row <= len(lines) else token.string
            text = line_text[col:] if col <= len(line_text) else token.string
            if _is_exempt_comment(row, text, line_text):
                continue
            if self.strict or self.pattern.match(text):
                found.append((row, col, text))
        return found

    def sanitize(self, source: str) -> str:
        """Return ``source`` with every unauthorized comment removed."""
        bom = _BOM if source.startswith(_BOM) else ""
        body = source[len(bom):]
        targets = {row: col for row, col, _ in self.find_unauthorized_comments(body)}
        if not targets:
            return source
        rebuilt: List[str] = []
        for row, line in enumerate(_split_with_terminators(body), start=1):
            col = targets.get(row)
            if col is None:
                rebuilt.append(line)
                continue
            head = _strip_terminator(line)[:col]
            if head.strip(" \t\x0c") == "":
                continue
            rebuilt.append(head.rstrip(" \t\x0c") + _line_terminator(line))
        return bom + "".join(rebuilt)

    def scan(self, source: str, file_path: str = _SOURCE_LABEL) -> List[ASTDefectRecord]:
        """Report the comments :meth:`sanitize` would remove."""
        return [
            record
            for record in scan_lexical_tokens(source, file_path, strict_comments=self.strict)
            if _category_value(record.defect_type) == DefectCategory.UNAUTHORIZED_COMMENT.value
        ]


def strip_unauthorized_comments(source: str) -> str:
    """Strip ``# TODO``/``# FIXME``/``# stub``/``# mock``/``# hack``/``# placeholder`` comments."""
    return CommentSanitizationEngine().sanitize(source)


def _build_parent_map(tree: ast.AST) -> Dict[int, ast.AST]:
    """Map ``id(child)`` to its parent node for every node of ``tree``."""
    parents: Dict[int, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _iter_statement_blocks(node: ast.AST) -> Iterable[Tuple[str, List[ast.stmt]]]:
    """Yield ``(field name, statement list)`` for each statement block."""
    for field, value in ast.iter_fields(node):
        if isinstance(value, list) and value and all(isinstance(item, ast.stmt) for item in value):
            yield field, value


def _is_docstring_stmt(stmt: ast.AST) -> bool:
    """Return True when ``stmt`` is a bare string-constant expression."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _terminal_name(node: Optional[ast.AST]) -> Optional[str]:
    """Return the final identifier of a Name, Attribute, Call or Subscript."""
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Subscript):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_protocol_class(node: ast.ClassDef) -> bool:
    """Return True when the class derives from ``typing.Protocol``."""
    return any(_terminal_name(base) == "Protocol" for base in node.bases)


def _is_abstract_function(func: ast.AST, parents: Dict[int, ast.AST]) -> bool:
    """Return True for ``@abstractmethod`` members and methods of Protocol classes."""
    if any(_terminal_name(dec) in _ABSTRACT_DECORATORS for dec in getattr(func, "decorator_list", ())):
        return True
    owner = parents.get(id(func))
    if isinstance(owner, ast.ClassDef) and _is_protocol_class(owner):
        return True
    return False


def _qualified_name(func: ast.AST, parents: Dict[int, ast.AST]) -> str:
    """Return the dotted lexical qualified name of a function definition."""
    parts = [getattr(func, "name", "")]
    current = parents.get(id(func))
    while current is not None:
        if isinstance(current, (ast.ClassDef,) + _FUNCTION_NODES):
            parts.append(getattr(current, "name", ""))
        current = parents.get(id(current))
    return ".".join(reversed(parts))


def _parameter_names(arguments: ast.arguments) -> List[str]:
    """Render the parameter list of a function signature."""
    names: List[str] = [arg.arg for arg in arguments.posonlyargs]
    if arguments.posonlyargs:
        names.append("/")
    names.extend([arg.arg for arg in arguments.args])
    if arguments.vararg is not None:
        names.append("*" + arguments.vararg.arg)
    elif arguments.kwonlyargs:
        names.append("*")
    names.extend([arg.arg for arg in arguments.kwonlyargs])
    if arguments.kwarg is not None:
        names.append("**" + arguments.kwarg.arg)
    return names


def _synthesized_docstring(
    func: ast.AST,
    parents: Dict[int, ast.AST],
    abstract: bool,
) -> str:
    """Build a descriptive docstring from a function's qualified name and parameters."""
    signature = f"{_qualified_name(func, parents)}({', '.join(_parameter_names(func.args))})"
    if abstract:
        text = f"Declare the abstract interface ``{signature}``; concrete subclasses provide the behaviour."
        fallback = _ABSTRACT_DOCSTRING_FALLBACK
    else:
        text = f"Complete ``{signature}`` without side effects and return None."
        fallback = _NOOP_DOCSTRING_FALLBACK
    if _GENERATED_TEXT_BLOCKLIST.search(text) or '"' in text or "\\" in text:
        return fallback
    return text


def _words(text: str) -> Set[str]:
    """Split identifiers and prose into lowercase words."""
    return {word.lower() for word in _WORD_PATTERN.findall(text)}


def _select_domain_exception(
    raise_node: ast.Raise,
    parents: Dict[int, ast.AST],
    hardware_keywords: Iterable[str],
) -> str:
    """Choose the domain exception replacing a dead-end raise."""
    words: Set[str] = set()
    exc = raise_node.exc
    if isinstance(exc, ast.Call):
        for argument in exc.args:
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                words |= _words(argument.value)
    docstring_read = False
    current = parents.get(id(raise_node))
    while current is not None:
        if isinstance(current, _FUNCTION_NODES):
            words |= _words(current.name)
            if not docstring_read:
                docstring = ast.get_docstring(current, clean=False)
                if docstring:
                    words |= _words(docstring)
                docstring_read = True
        elif isinstance(current, ast.ClassDef):
            words |= _words(current.name)
        current = parents.get(id(current))
    if words & frozenset(hardware_keywords):
        return _HARDWARE_EXCEPTION_NAME
    return _PHYSICAL_EXCEPTION_NAME


class _PurgePlan:
    """Planned mutations recorded for one module syntax tree."""

    def __init__(self) -> None:
        self.renames: List[Tuple[ast.Raise, str]] = []
        self.block_edits: List[Tuple[ast.AST, str, Dict[int, Tuple[str, str]]]] = []

    @property
    def exception_names(self) -> List[str]:
        """Domain exception names referenced by the planned rewrites."""
        names: List[str] = []
        for _, name in self.renames:
            if name not in names:
                names.append(name)
        return names

    def is_empty(self) -> bool:
        """Return True when the plan performs no rewrite."""
        return not self.renames and not self.block_edits


def _plan_block(
    owner: ast.AST,
    field: str,
    block: List[ast.stmt],
    parents: Dict[int, ast.AST],
    eradicate_stubs: bool = True,
    eradicate_passes: bool = True,
) -> Dict[int, Tuple[str, str]]:
    """Decide how one statement list is rewritten."""
    function_body = isinstance(owner, _FUNCTION_NODES) and field == "body"
    class_body = isinstance(owner, ast.ClassDef) and field == "body"
    abstract = function_body and _is_abstract_function(owner, parents)

    droppable: List[int] = []
    for index, stmt in enumerate(block):
        if eradicate_passes and isinstance(stmt, ast.Pass):
            droppable.append(index)
        elif eradicate_stubs and abstract and _raises_unimplemented(stmt):
            droppable.append(index)

    if not droppable:
        return {}

    if len(droppable) < len(block):
        return {index: (_EDIT_DROP, "") for index in droppable}

    first = droppable[0]
    rest = droppable[1:]
    edits: Dict[int, Tuple[str, str]] = {index: (_EDIT_DROP, "") for index in rest}

    if function_body:
        edits[first] = (
            _EDIT_DOCSTRING,
            _synthesized_docstring(owner, parents, abstract),
        )
        return edits

    if isinstance(owner, _LOOP_NODES) and field == "body":
        edits[first] = (_EDIT_CONTINUE, "")
        return edits

    if class_body:
        class_name = getattr(owner, "name", "Class")
        doc = f"Define the {class_name} class interface."
        if _GENERATED_TEXT_BLOCKLIST.search(doc):
            doc = "Class body definition."
        edits[first] = (_EDIT_DOCSTRING, doc)
        return edits

    edits[first] = (_EDIT_DOCSTRING, _NOOP_DOCSTRING_FALLBACK)
    return edits


def _plan_production_purge(
    tree: ast.AST,
    eradicate_stubs: bool = True,
    eradicate_passes: bool = True,
    hardware_keywords: Iterable[str] = (),
    target_exception: Optional[str] = None,
) -> _PurgePlan:
    """Plan dead-end raise and empty ``pass`` rewrites for ``tree``."""
    parents = _build_parent_map(tree)
    plan = _PurgePlan()
    handled: Set[int] = set()

    for node in ast.walk(tree):
        for field, block in _iter_statement_blocks(node):
            edits = _plan_block(
                node,
                field,
                block,
                parents,
                eradicate_stubs=eradicate_stubs,
                eradicate_passes=eradicate_passes,
            )
            if edits:
                plan.block_edits.append((node, field, edits))
                handled.update(id(block[index]) for index in edits)

    if eradicate_stubs:
        keywords = frozenset(hardware_keywords)
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and id(node) not in handled and _raises_unimplemented(node):
                if target_exception:
                    chosen = target_exception
                else:
                    chosen = _select_domain_exception(node, parents, keywords)
                plan.renames.append((node, chosen))

    return plan


def _is_hollow_function(stmt: ast.AST) -> bool:
    """Return True for a function whose only behaviour is ``pass`` or ``raise NotImplementedError``."""
    if not isinstance(stmt, _FUNCTION_NODES):
        return False
    body = stmt.body
    if body and _is_docstring_stmt(body[0]):
        body = body[1:]
    return bool(body) and all(
        isinstance(item, ast.Pass) or _raises_unimplemented(item) for item in body
    )


def _plan_eradication(tree: ast.AST) -> _PurgePlan:
    """Plan removal of hollow functions and redundant ``pass`` statements."""
    plan = _PurgePlan()
    dropped: Set[int] = set()
    stack: List[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        for field, block in _iter_statement_blocks(node):
            class_body = isinstance(node, ast.ClassDef) and field == "body"
            droppable = [
                index
                for index, stmt in enumerate(block)
                if _is_hollow_function(stmt) or (isinstance(stmt, ast.Pass) and not class_body)
            ]
            if droppable and len(droppable) < len(block):
                plan.block_edits.append((node, field, {index: (_EDIT_DROP, "") for index in droppable}))
                dropped.update(id(block[index]) for index in droppable)
        for child in ast.iter_child_nodes(node):
            if id(child) not in dropped:
                stack.append(child)
    return plan


def _apply_plan_in_memory(plan: _PurgePlan) -> None:
    """Apply ``plan`` to the syntax tree it was computed from."""
    for raise_node, name in plan.renames:
        replacement = ast.Name(id=name, ctx=ast.Load())
        exc = raise_node.exc
        if isinstance(exc, ast.Call):
            exc.func = ast.copy_location(replacement, exc.func)
        else:
            raise_node.exc = ast.copy_location(replacement, exc)

    for owner, field, edits in plan.block_edits:
        rebuilt: List[ast.stmt] = []
        for index, stmt in enumerate(getattr(owner, field)):
            action = edits.get(index)
            if action is None:
                rebuilt.append(stmt)
                continue
            kind, text = action
            if kind == _EDIT_DOCSTRING:
                constant = ast.copy_location(ast.Constant(value=text, kind=None), stmt)
                rebuilt.append(ast.copy_location(ast.Expr(value=constant), stmt))
            elif kind == _EDIT_CONTINUE:
                rebuilt.append(ast.copy_location(ast.Continue(), stmt))
        setattr(owner, field, rebuilt)


def _collect_bound_names(statements: Sequence[ast.stmt], names: Set[str]) -> None:
    """Collect names bound by module-level statements."""
    for stmt in statements:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                names.add(alias.asname or alias.name)
        elif isinstance(stmt, (ast.ClassDef,) + _FUNCTION_NODES):
            names.add(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                names.update(
                    node.id
                    for node in ast.walk(target)
                    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
                )
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(stmt.target, ast.Name):
                names.add(stmt.target.id)
        elif isinstance(stmt, (ast.If, ast.Try, ast.With)) or type(stmt).__name__ == "TryStar":
            for _, block in _iter_statement_blocks(stmt):
                _collect_bound_names(block, names)
            for handler in getattr(stmt, "handlers", []):
                _collect_bound_names(handler.body, names)


def _names_to_inject(tree: ast.AST, plan: _PurgePlan) -> List[str]:
    """Return the domain exception names that must be imported into ``tree``."""
    if not isinstance(tree, ast.Module):
        return []
    bound: Set[str] = set()
    _collect_bound_names(tree.body, bound)
    return sorted(name for name in plan.exception_names if name not in bound)


def _injection_index(module: ast.Module) -> int:
    """Index after the module docstring and any ``from __future__`` imports."""
    body = module.body
    index = 1 if body and _is_docstring_stmt(body[0]) else 0
    while index < len(body):
        stmt = body[index]
        if isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__" and stmt.level == 0:
            index += 1
        else:
            break
    return index


def _inject_domain_import_in_memory(
    module: ast.AST,
    names: Sequence[str],
    module_name: str,
) -> None:
    """Insert ``from <module_name> import <names>`` at the legal import site."""
    if not names or not isinstance(module, ast.Module):
        return
    node = ast.ImportFrom(
        module=module_name,
        names=[ast.alias(name=name, asname=None) for name in names],
        level=0,
    )
    module.body.insert(_injection_index(module), node)


class _SourceSurgeon:
    """Character-accurate text surgery for AST node coordinates."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.lines = _split_with_terminators(text)
        self.starts: List[int] = []
        offset = 0
        for line in self.lines:
            self.starts.append(offset)
            offset += len(line)
        self.edits: List[Tuple[int, int, str]] = []

    def _content(self, lineno: int) -> str:
        return _strip_terminator(self.lines[lineno - 1])

    def _char_col(self, lineno: int, byte_col: int) -> int:
        return _char_column(self._content(lineno), byte_col)

    def offset(self, lineno: int, byte_col: int) -> int:
        """Absolute character offset of an AST ``(lineno, byte column)`` position."""
        return self.starts[lineno - 1] + self._char_col(lineno, byte_col)

    def statement_first_line(self, stmt: ast.AST) -> int:
        """First physical line of a statement, including its decorators."""
        decorators = getattr(stmt, "decorator_list", None) or []
        return min([stmt.lineno] + [dec.lineno for dec in decorators])

    def _statement_start(self, stmt: ast.AST) -> Tuple[int, int]:
        decorators = getattr(stmt, "decorator_list", None) or []
        if decorators:
            first = min(decorators, key=lambda item: (item.lineno, item.col_offset))
            col = self._char_col(first.lineno, first.col_offset)
            at_sign = self._content(first.lineno).rfind("@", 0, col)
            if at_sign >= 0:
                return first.lineno, at_sign
            return first.lineno, col
        return stmt.lineno, self._char_col(stmt.lineno, stmt.col_offset)

    def replace_node(self, node: ast.AST, replacement: str) -> None:
        """Replace the exact source span of ``node``."""
        start = self.offset(node.lineno, node.col_offset)
        end = self.offset(node.end_lineno, node.end_col_offset)
        self.edits.append((start, end, replacement))

    def delete_statement(self, stmt: ast.AST) -> None:
        """Delete a statement together with its now-redundant layout."""
        first_line, first_col = self._statement_start(stmt)
        end_line = stmt.end_lineno
        end_col = self._char_col(end_line, stmt.end_col_offset)

        line_text = self._content(first_line)
        head = line_text[:first_col]
        tail = self.lines[end_line - 1][end_col:]

        start = self.starts[first_line - 1] + first_col
        end = self.starts[end_line - 1] + end_col

        stripped_tail = tail.lstrip(" \t\x0c")

        if head.strip(" \t\x0c") == "":
            if stripped_tail == "":
                # Whole line deletion
                full_start = self.starts[first_line - 1]
                full_end = self.starts[end_line - 1] + len(self.lines[end_line - 1])
                self.edits.append((full_start, full_end, ""))
                return
            if stripped_tail.startswith("#"):
                # Trailing comment survives
                self.edits.append((start, end + len(tail) - len(stripped_tail), ""))
                return

        if stripped_tail.startswith(";"):
            after = stripped_tail[1:]
            self.edits.append(
                (start, end + len(tail) - len(after.lstrip(" \t\x0c")), "")
            )
            return

        trimmed_head = head.rstrip(" \t\x0c")
        if trimmed_head.endswith(";"):
            self.edits.append(
                (self.starts[first_line - 1] + len(trimmed_head) - 1, end, "")
            )
            return

        raise PurgeVerificationError(
            "statement layout does not allow a lossless deletion",
            details={"line": stmt.lineno, "statement": type(stmt).__name__},
        )

    def insert_before_line(self, lineno: int, text: str) -> None:
        """Insert ``text`` at the beginning of 1-indexed physical line ``lineno``."""
        if 1 <= lineno <= len(self.lines):
            position = self.starts[lineno - 1]
            self.edits.append((position, position, text))
            return
        position = len(self.text)
        needs_break = bool(self.lines) and not _line_terminator(self.lines[-1])
        prefix = _detect_terminator(self.text) if needs_break else ""
        self.edits.append((position, position, prefix + text))

    def apply(self) -> str:
        """Apply every recorded edit, rejecting overlapping spans."""
        ordered = sorted(self.edits, key=lambda edit: (edit[0], edit[1]))
        for previous, current in zip(ordered, ordered[1:]):
            if previous[1] > current[0]:
                raise PurgeVerificationError(
                    "overlapping source edits cannot be applied losslessly",
                    details={"first": previous[:2], "second": current[:2]},
                )
        result = self.text
        for start, end, replacement in reversed(ordered):
            result = result[:start] + replacement + result[end:]
        return result


def _apply_plan_textually(
    text: str,
    tree: ast.AST,
    plan: _PurgePlan,
    inject_names: Sequence[str],
    module_name: str,
) -> str:
    """Apply ``plan`` to ``text`` via surgical edits."""
    surgeon = _SourceSurgeon(text)

    for raise_node, name in plan.renames:
        exc = raise_node.exc
        target = exc.func if isinstance(exc, ast.Call) else exc
        surgeon.replace_node(target, name)

    for owner, field, edits in plan.block_edits:
        block = getattr(owner, field)
        for index in sorted(edits):
            kind, docstring = edits[index]
            stmt = block[index]
            if kind == _EDIT_DROP:
                surgeon.delete_statement(stmt)
            elif kind == _EDIT_DOCSTRING:
                surgeon.replace_node(stmt, '"""' + docstring + '"""')
            else:
                surgeon.replace_node(stmt, "continue")

    if inject_names and isinstance(tree, ast.Module):
        line = f"from {module_name} import {', '.join(inject_names)}{_detect_terminator(text)}"
        index = _injection_index(tree)
        if index > 0:
            surgeon.insert_before_line(tree.body[index - 1].end_lineno + 1, line)
        elif tree.body:
            surgeon.insert_before_line(surgeon.statement_first_line(tree.body[0]), line)
        else:
            surgeon.insert_before_line(len(surgeon.lines) + 1, line)

    return surgeon.apply()


def _verify_result(result: str, expected_tree: ast.AST, label: str) -> None:
    """Verify that ``result`` parses into exactly ``expected_tree``."""
    try:
        actual_tree = ast.parse(result, filename=label)
    except (SyntaxError, ValueError) as err:
        raise PurgeVerificationError(
            f"purged source failed to parse: {label}",
            details={"error": _describe_error(err)},
        ) from err

    actual_dump = ast.dump(actual_tree)
    expected_dump = ast.dump(expected_tree)
    if actual_dump != expected_dump:
        diff = "".join(
            difflib.unified_diff(
                expected_dump.splitlines(keepends=True),
                actual_dump.splitlines(keepends=True),
                fromfile="expected_ast",
                tofile="actual_ast",
            )
        )
        raise PurgeVerificationError(
            f"purged AST did not match expected tree: {label}",
            details={"diff": diff},
        )


def _strip_comments_verified(
    text: str,
    label: str,
    sanitizer: CommentSanitizationEngine,
) -> Tuple[str, ast.AST]:
    """Strip comments and prove the syntax tree is unchanged."""
    original_tree = ast.parse(text, filename=label)
    stripped = sanitizer.sanitize(text)
    if stripped == text:
        return text, original_tree
    stripped_tree = ast.parse(stripped, filename=label)
    if ast.dump(stripped_tree) != ast.dump(original_tree):
        raise PurgeVerificationError(
            f"comment stripping altered the syntax tree of {label}",
            details={"file": label},
        )
    return stripped, stripped_tree


def _verified_purge(
    text: str,
    label: str,
    config: Optional[PurgeConfig] = None,
    sanitizer: Optional[CommentSanitizationEngine] = None,
    target_exception: Optional[str] = None,
) -> str:
    """Purge comments, dead-end raises and empty ``pass`` statements with verification."""
    cfg = config or DEFAULT_PURGE_CONFIG
    san = sanitizer or CommentSanitizationEngine()
    stripped, stripped_tree = _strip_comments_verified(text, label, san)
    keywords = cfg.hardware_keywords
    plan = _plan_production_purge(
        stripped_tree,
        eradicate_stubs=True,
        eradicate_passes=True,
        hardware_keywords=keywords,
        target_exception=target_exception,
    )
    if plan.is_empty():
        return stripped
    names = _names_to_inject(stripped_tree, plan)
    result = _apply_plan_textually(
        stripped,
        stripped_tree,
        plan,
        names,
        cfg.domain_exception_module,
    )
    expected_tree = ast.parse(stripped, filename=label)
    expected_plan = _plan_production_purge(
        expected_tree,
        eradicate_stubs=True,
        eradicate_passes=True,
        hardware_keywords=keywords,
        target_exception=target_exception,
    )
    expected_names = _names_to_inject(expected_tree, expected_plan)
    _apply_plan_in_memory(expected_plan)
    _inject_domain_import_in_memory(expected_tree, expected_names, cfg.domain_exception_module)
    _verify_result(result, expected_tree, label)
    return result


def purge_source(
    source: str,
    file_path: str = _SOURCE_LABEL,
    config: Optional[PurgeConfig] = None,
    target_exception: Optional[str] = None,
) -> str:
    """Return ``source`` purged of unauthorized comments, dead-end raises and empty passes."""
    cfg = config or DEFAULT_PURGE_CONFIG
    return _verified_purge(
        source,
        file_path,
        cfg,
        CommentSanitizationEngine(),
        target_exception=target_exception,
    )


class _PlannedPurgeTransformer(ast.NodeTransformer):
    eradicate_stubs: bool = True
    eradicate_passes: bool = True

    def __init__(
        self,
        config: Optional[PurgeConfig] = None,
        target_exception: Optional[str] = None,
    ) -> None:
        self.config = config or DEFAULT_PURGE_CONFIG
        self.target_exception = target_exception
        self.rewritten_raises = 0
        self.rewritten_statements = 0
        self.injected_names: List[str] = []

    def visit(self, node: ast.AST) -> ast.AST:
        plan = _plan_production_purge(
            node,
            self.eradicate_stubs,
            self.eradicate_passes,
            self.config.hardware_keywords,
            target_exception=self.target_exception,
        )
        names = _names_to_inject(node, plan)
        _apply_plan_in_memory(plan)
        _inject_domain_import_in_memory(node, names, self.config.domain_exception_module)
        self.rewritten_raises += len(plan.renames)
        self.rewritten_statements += sum(len(edits) for _, _, edits in plan.block_edits)
        self.injected_names.extend(names)
        ast.fix_missing_locations(node)
        return node


class StubEradicationTransformer(_PlannedPurgeTransformer):
    """Rewrite ``raise NotImplementedError`` dead-ends.

    Ordinary raises become ``PhysicalConvergenceError`` or, in hardware
    contexts, ``HardwareTopologyError`` with the original message and ``from``
    cause preserved. Abstract and Protocol methods become docstring-only
    interface declarations. ``except NotImplementedError`` handlers are kept.
    """

    eradicate_stubs = True
    eradicate_passes = False


class PassEradicationTransformer(_PlannedPurgeTransformer):
    """Remove empty ``pass`` statements from execution bodies, preserving class bodies."""

    eradicate_stubs = False
    eradicate_passes = True


class ProductionPurgeTransformer(_PlannedPurgeTransformer):
    """Apply both dead-end raise and empty ``pass`` eradication in one pass."""

    eradicate_stubs = True
    eradicate_passes = True


def _read_source(path: Path) -> Tuple[bytes, str, str]:
    """Read ``path`` and decode it honouring BOM and PEP 263 declarations."""
    data = path.read_bytes()
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    return data, encoding, data.decode(encoding)


def _error_report(label: str, error: BaseException, data: bytes) -> FileASTReport:
    """Report a file that could not be decoded, parsed or verified."""
    file_hash = hashlib.sha256(data).hexdigest() if data else ""
    return _build_report(label, [], file_hash, _describe_error(error))


def _purge_defects_for_source(
    source: str,
    label: str,
) -> Tuple[List[ASTDefectRecord], Optional[str]]:
    """Collect the three purge defect categories from ``source``."""
    records: List[ASTDefectRecord] = []
    error: Optional[str] = None
    try:
        tree = ast.parse(source, filename=label)
    except (SyntaxError, ValueError) as err:
        error = _describe_error(err)
    else:
        visitor = ASTDefectVisitor(source, label)
        visitor.visit(tree)
        records.extend(
            record
            for record in visitor.defects
            if _category_value(record.defect_type) in _PURGE_CATEGORIES
        )
    try:
        records.extend(
            record
            for record in scan_lexical_tokens(source, label)
            if _category_value(record.defect_type) == DefectCategory.UNAUTHORIZED_COMMENT.value
        )
    except (tokenize.TokenError, SyntaxError) as err:
        if not error:
            error = _describe_error(err)
    return records, error


def _normalize_targets(
    target: Union[str, os.PathLike[str], Path, Iterable[Any]],
) -> List[Path]:
    """Normalise a single path or an iterable of paths into a list of ``Path`` objects."""
    if isinstance(target, (str, os.PathLike)):
        return [Path(target)]
    return [Path(item) for item in target]


def _iter_python_files(
    targets: Iterable[Union[str, Path]],
    excluded_dir_names: FrozenSet[str],
    excluded_file_names: FrozenSet[str],
) -> List[Path]:
    """Enumerate Python files under ``targets`` honouring directory and file exclusions."""
    collected: List[Path] = []
    seen: Set[str] = set()
    skip_dirs = set(excluded_dir_names)
    skip_files = set(excluded_file_names)

    def _accept(candidate: Path) -> None:
        name = candidate.name
        if not name.endswith(".py") or name in skip_files or name.startswith(_TEMP_PREFIX):
            return
        key = os.path.normcase(str(candidate.resolve()))
        if key not in seen:
            seen.add(key)
            collected.append(candidate)

    for target in targets:
        path = Path(target)
        if path.is_file():
            _accept(path)
        elif path.is_dir():
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(name for name in dirs if name not in skip_dirs)
                for name in sorted(files):
                    _accept(Path(root) / name)
        else:
            raise FileNotFoundError(f"target does not exist: {path}")

    return collected


def _purge_file(
    path: Path,
    dry_run: bool,
    config: PurgeConfig,
    sanitizer: CommentSanitizationEngine,
    target_exception: Optional[str] = None,
) -> Tuple[FileASTReport, Optional[str], Optional[str]]:
    """Purge one file; return its post-purge report plus original and purged text."""
    label = str(path)
    try:
        data, encoding, text = _read_source(path)
    except (OSError, SyntaxError, UnicodeDecodeError, LookupError) as err:
        return _error_report(label, err, b""), None, None
    try:
        purged = _verified_purge(
            text, label, config, sanitizer, target_exception=target_exception
        )
    except (SyntaxError, ValueError, tokenize.TokenError, PurgeVerificationError) as err:
        return _error_report(label, err, data), text, None

    payload = purged.encode(encoding)
    if purged != text and not dry_run:
        write_atomic(path, payload)

    records, error = _purge_defects_for_source(purged, label)
    report = _build_report(label, records, hashlib.sha256(payload).hexdigest(), error)
    return report, text, purged


def _run_purge_batch(
    targets: Iterable[Union[str, Path]],
    dry_run: bool = False,
    config: Optional[PurgeConfig] = None,
    sanitizer: Optional[CommentSanitizationEngine] = None,
    target_exception: Optional[str] = None,
) -> Tuple[EradicationPlan, List[str]]:
    """Purge every eligible file and return the verification plan and unified diffs."""
    cfg = config or DEFAULT_PURGE_CONFIG
    san = sanitizer or CommentSanitizationEngine()
    reports: List[FileASTReport] = []
    diffs: List[str] = []

    for path in _iter_python_files(targets, cfg.excluded_dir_names, cfg.excluded_file_names):
        report, original, updated = _purge_file(
            path, dry_run, cfg, san, target_exception=target_exception
        )
        reports.append(report)
        if original is not None and updated is not None and original != updated:
            diffs.append(compute_diff(original, updated, str(path)))

    return _plan_from_reports(reports), diffs


def purge_production_stubs_and_comments(
    target: Union[str, os.PathLike[str], Path, Iterable[Any]],
    *,
    dry_run: bool = False,
    in_place: bool = False,
    target_exception: Optional[str] = None,
    config: Optional[PurgeConfig] = None,
) -> Union[str, EradicationPlan]:
    """Purge production modules under ``target`` and verify the result."""
    cfg = config or DEFAULT_PURGE_CONFIG
    sanitizer = CommentSanitizationEngine()

    is_source_string = False
    if isinstance(target, str):
        if "\\n" in target or "\\r" in target:
            is_source_string = True
        else:
            try:
                p = Path(target)
                if not p.exists() and not p.is_file() and not p.is_dir():
                    is_source_string = True
            except (OSError, ValueError):
                is_source_string = True

    if is_source_string:
        return _verified_purge(
            str(target),
            _SOURCE_LABEL,
            cfg,
            sanitizer,
            target_exception=target_exception,
        )

    if isinstance(target, (str, Path, os.PathLike)):
        p = Path(target)
        if p.is_file():
            data, encoding, text = _read_source(p)
            purged = _verified_purge(
                text,
                str(p),
                cfg,
                sanitizer,
                target_exception=target_exception,
            )
            if in_place and not dry_run and purged != text:
                write_atomic(p, purged, encoding=encoding)
            return purged
        elif p.is_dir():
            plan, _ = _run_purge_batch(
                [p],
                dry_run=dry_run or (not in_place),
                config=cfg,
                sanitizer=sanitizer,
                target_exception=target_exception,
            )
            return plan

    targets = _normalize_targets(target)
    plan, _ = _run_purge_batch(
        targets,
        dry_run=dry_run or (not in_place),
        config=cfg,
        sanitizer=sanitizer,
        target_exception=target_exception,
    )
    return plan


def scan_purge_defects(
    target: Union[str, os.PathLike[str], Path, Iterable[Any]],
    config: Optional[PurgeConfig] = None,
) -> EradicationPlan:
    """Read-only inspection of the three purge defect categories under ``target``."""
    cfg = config or DEFAULT_PURGE_CONFIG
    reports: List[FileASTReport] = []

    for path in _iter_python_files(
        _normalize_targets(target),
        cfg.excluded_dir_names,
        cfg.excluded_file_names,
    ):
        label = str(path)
        try:
            data, _, text = _read_source(path)
            records, error = _purge_defects_for_source(text, label)
            reports.append(
                _build_report(
                    label,
                    records,
                    hashlib.sha256(data).hexdigest(),
                    error,
                )
            )
        except (OSError, SyntaxError, UnicodeDecodeError, LookupError) as err:
            reports.append(_error_report(label, err, b""))

    return _plan_from_reports(reports)


class _InstanceOrDefaultMethod:
    """Descriptor binding a method to the accessing instance or to a default-configured one."""

    def __init__(self, function: Callable[..., Any]) -> None:
        self._function = function
        functools.update_wrapper(self, function)

    def __get__(self, instance: Any, owner: Optional[type] = None) -> Callable[..., Any]:
        target = instance if instance is not None else (owner or type(instance))()
        return types.MethodType(self._function, target)


class ASTEradicationEngine:
    """Read-only inspection facade over the structural and lexical passes."""

    def __init__(
        self,
        strict_comments: bool = False,
        excluded_dir_names: Optional[Iterable[str]] = None,
    ) -> None:
        self.strict_comments = bool(strict_comments)
        self.excluded_dir_names: FrozenSet[str] = (
            frozenset(excluded_dir_names)
            if excluded_dir_names is not None
            else _EXCLUDED_DIR_NAMES
        )

    @_InstanceOrDefaultMethod
    def inspect_source(
        self,
        source: str,
        file_path: str = _SOURCE_LABEL,
        file_hash: Optional[str] = None,
    ) -> FileASTReport:
        """Inspect source text without touching the filesystem."""
        text = _strip_bom(source)
        digest = (
            file_hash
            if file_hash is not None
            else _sha256_bytes(source.encode("utf-8", errors="surrogatepass"))
        )
        defects: List[ASTDefectRecord] = []
        parse_error: Optional[str] = None
        try:
            tree = ast.parse(text, filename=file_path)
        except (SyntaxError, ValueError, RecursionError, MemoryError) as error:
            parse_error = _describe_error(error)
        else:
            visitor = ASTDefectVisitor(text, file_path)
            visitor.visit(tree)
            defects.extend(visitor.defects)
        defects.extend(scan_lexical_tokens(text, file_path, self.strict_comments))
        return _build_report(file_path, defects, digest, parse_error)

    @_InstanceOrDefaultMethod
    def inspect_file(self, path: Union[str, Path]) -> FileASTReport:
        """Inspect one file by reading its bytes once; the file is never modified."""
        target = Path(path)
        data = target.read_bytes()
        digest = _sha256_bytes(data)
        try:
            text = _decode_source_bytes(data)
        except (SyntaxError, UnicodeDecodeError, LookupError) as error:
            return _build_report(str(target), [], digest, _describe_error(error))
        return self.inspect_source(text, str(target), digest)

    @_InstanceOrDefaultMethod
    def inspect_directory(
        self,
        directory: Union[str, Path],
        recursive: bool = True,
        pattern: str = _DEFAULT_PATTERN,
    ) -> EradicationPlan:
        """Inspect every file under ``directory`` matching ``pattern``."""
        root = Path(directory)
        if not root.is_dir():
            raise NotADirectoryError(f"inspection root is not a directory: {root}")
        candidates = root.rglob(pattern) if recursive else root.glob(pattern)
        selected = sorted(
            candidate
            for candidate in candidates
            if candidate.is_file() and not self._is_excluded(candidate, root)
        )
        return _plan_from_reports([self.inspect_file(candidate) for candidate in selected])

    @_InstanceOrDefaultMethod
    def inspect_paths(
        self,
        targets: Iterable[Union[str, Path]],
        pattern: str = _DEFAULT_PATTERN,
    ) -> EradicationPlan:
        """Inspect every Python file under ``targets``."""
        files = _iter_python_files(
            targets,
            self.excluded_dir_names,
            PURGE_EXCLUDED_FILE_NAMES,
        )
        return _plan_from_reports([self.inspect_file(path) for path in files])

    def _is_excluded(self, candidate: Path, root: Path) -> bool:
        """Return True when a directory between ``root`` and ``candidate`` is excluded."""
        relative_dirs = candidate.relative_to(root).parts[:-1]
        return any(part in self.excluded_dir_names for part in relative_dirs)


def eradicate_source(
    source: str,
    file_path: str = _SOURCE_LABEL,
    strict_comments: bool = False,
) -> Tuple[str, FileASTReport]:
    """Remove comments, redundant ``pass`` statements and hollow functions."""
    engine = ASTEradicationEngine(strict_comments=strict_comments)
    try:
        ast.parse(source, filename=file_path)
    except (SyntaxError, ValueError):
        return source, engine.inspect_source(source, file_path)

    try:
        stripped, stripped_tree = _strip_comments_verified(
            source,
            file_path,
            CommentSanitizationEngine(strict=strict_comments),
        )
    except (SyntaxError, ValueError, tokenize.TokenError) as err:
        raise PurgeVerificationError(
            f"comment eradication failed for {file_path}",
            details={"error": _describe_error(err)},
        ) from err

    plan = _plan_eradication(stripped_tree)
    if plan.is_empty():
        return stripped, engine.inspect_source(stripped, file_path)

    result = _apply_plan_textually(stripped, stripped_tree, plan, [], "")
    expected_tree = ast.parse(stripped, filename=file_path)
    _apply_plan_in_memory(_plan_eradication(expected_tree))
    _verify_result(result, expected_tree, file_path)
    return result, engine.inspect_source(result, file_path)


def compute_sha256(source: Union[bytes, str, Path]) -> str:
    """Return the hex SHA-256 digest of bytes or of a file\'s content."""
    if isinstance(source, (str, Path)):
        p = Path(source)
        if p.is_file():
            hasher = hashlib.sha256()
            with open(p, "rb") as handle:
                while chunk := handle.read(_HASH_CHUNK_SIZE):
                    hasher.update(chunk)
            return hasher.hexdigest()
    if isinstance(source, str):
        source = source.encode("utf-8")
    return hashlib.sha256(source).hexdigest()


def _stage_bytes(directory: Path, name: str, payload: bytes) -> Path:
    """Write ``payload`` durably into a fresh temporary file inside ``directory``."""
    staged = directory / f"{_TEMP_PREFIX}{name[:_MAX_TEMP_NAME_TAIL]}.{uuid.uuid4().hex}"
    with open(staged, "xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return staged


def _replace_with_retry(source: Path, destination: Path) -> None:
    """``os.replace`` retrying transient sharing violations for a bounded window."""
    deadline = time.monotonic() + _SHARING_RETRY_WINDOW_SECONDS
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(_SHARING_RETRY_INTERVAL_SECONDS)


def _commit_bytes(destination: Path, payload: bytes) -> None:
    """Stage, verify and commit ``payload`` to ``destination``."""
    expected = hashlib.sha256(payload).hexdigest()
    staged: Optional[Path] = None
    try:
        staged = _stage_bytes(destination.parent, destination.name, payload)
        staged_digest = compute_sha256(staged)
        if staged_digest != expected:
            raise AtomicWriteError(
                f"staged bytes for {destination} failed digest verification",
                details={"expected": expected, "actual": staged_digest},
            )
        _replace_with_retry(staged, destination)
        staged = None
    except OSError as err:
        raise AtomicWriteError(
            f"could not commit {destination}",
            details={"error": _describe_error(err)},
        ) from err
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)


def write_atomic(
    path: Union[str, Path],
    data: Union[str, bytes],
    encoding: str = "utf-8",
) -> str:
    """Atomically replace ``path`` with ``data`` and return the committed SHA-256 digest."""
    destination = Path(path)
    payload = data.encode(encoding) if isinstance(data, str) else bytes(data)
    expected = hashlib.sha256(payload).hexdigest()

    destination.parent.mkdir(parents=True, exist_ok=True)
    previous = destination.read_bytes() if destination.is_file() else None

    _commit_bytes(destination, payload)
    try:
        committed = compute_sha256(destination)
    except OSError as err:
        committed = f"unreadable: {_describe_error(err)}"

    if committed != expected:
        if previous is None:
            destination.unlink(missing_ok=True)
        else:
            _commit_bytes(destination, previous)
        raise AtomicWriteError(
            f"committed bytes for {destination} failed digest verification; previous content restored",
            details={"expected": expected, "actual": committed},
        )
    return expected


def compute_diff(original: str, updated: str, file_path: str = "") -> str:
    """Return a unified diff between two versions of a source file."""
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    )


def _remediate_path(
    path: Path,
    dry_run: bool,
    strict_comments: bool,
) -> Tuple[FileASTReport, Optional[str], Optional[str]]:
    """Run :func:`eradicate_source` on one file and commit the result."""
    label = str(path)
    try:
        data, encoding, text = _read_source(path)
    except (OSError, SyntaxError, UnicodeDecodeError, LookupError) as err:
        return _error_report(label, err, b""), None, None
    try:
        updated, report = eradicate_source(text, label, strict_comments)
    except PurgeVerificationError as err:
        return _error_report(label, err, data), text, None

    if updated != text and not dry_run:
        write_atomic(path, updated.encode(encoding))
    return report, text, updated


def remediate_file(
    path: Union[str, Path],
    dry_run: bool = False,
    strict_comments: bool = False,
) -> FileASTReport:
    """Eradicate defects in one file on disk and return the remaining-defect report."""
    return _remediate_path(Path(path), dry_run, strict_comments)[0]


def sync_trees(
    source_root: Union[str, Path],
    mirror_root: Union[str, Path],
    dry_run: bool = False,
) -> DualTreeSyncResult:
    """Copy every differing file of ``source_root`` into ``mirror_root`` atomically."""
    source = Path(source_root)
    mirror = Path(mirror_root)
    if not source.is_dir():
        raise FileNotFoundError(f"source tree does not exist: {source}")

    copied: List[str] = []
    unchanged: List[str] = []

    for root, dirs, files in os.walk(source):
        dirs[:] = sorted(name for name in dirs if name not in _EXCLUDED_DIR_NAMES)
        for name in sorted(files):
            if name.startswith(_TEMP_PREFIX):
                continue
            origin = Path(root) / name
            relative = origin.relative_to(source)
            destination = mirror / relative
            payload = origin.read_bytes()
            if destination.is_file() and compute_sha256(destination) == compute_sha256(payload):
                unchanged.append(relative.as_posix())
            else:
                if not dry_run:
                    write_atomic(destination, payload)
                copied.append(relative.as_posix())

    return DualTreeSyncResult(
        source_root=str(source),
        mirror_root=str(mirror),
        copied_files=copied,
        unchanged_files=unchanged,
        dry_run=dry_run,
        is_synchronized=not dry_run and not copied,
    )


def build_cli_parser() -> argparse.ArgumentParser:
    """Build the command line interface of the eradication engine."""
    config = DEFAULT_PURGE_CONFIG
    parser = argparse.ArgumentParser(
        prog="cochem-ast-eradication",
        description="Inspect, purge and synchronize CoChem production sources.",
    )
    parser.add_argument("paths", nargs="*", help="files or directories to process")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="read-only inspection (default)",
    )
    mode.add_argument(
        "--remediate",
        action="store_true",
        help="eradicate comments, passes and hollow functions",
    )
    mode.add_argument(
        "--purge",
        action="store_true",
        help="production stub, pass and comment purge",
    )
    mode.add_argument(
        "--sync-trees",
        action="store_true",
        help="mirror the source tree bitwise",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="print unified diffs of rewrites",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="compute rewrites without writing",
    )
    parser.add_argument(
        "--strict-comments",
        action="store_true",
        help="treat every comment as a defect",
    )
    parser.add_argument(
        "--json-output",
        nargs="?",
        const="-",
        default=None,
        help="emit JSON to stdout or to a file",
    )
    parser.add_argument(
        "--source-root",
        default=config.default_production_root,
    )
    parser.add_argument(
        "--mirror-root",
        default=config.default_mirror_root,
    )
    return parser


def _format_plan(plan: EradicationPlan) -> str:
    """Render a plan as human readable diagnostics."""
    lines: List[str] = []
    for report in plan.reports:
        for defect in report.defects:
            lines.append(
                f"{defect.file_path}:{defect.line}:{defect.column + 1}: "
                f"{_category_value(defect.defect_type)} {defect.message}"
            )
    compliance = "compliant" if plan.is_compliant else "non-compliant"
    lines.append(f"{plan.total_files} file(s), {plan.total_defects} defect(s), {compliance}")
    return "\\n".join(lines) + "\\n"


def _format_sync(result: DualTreeSyncResult) -> str:
    """Render a synchronization result as human readable text."""
    verb = "would copy" if result.dry_run else "copied"
    lines = [f"{verb} {name}" for name in result.copied_files]
    status = "synchronized" if result.is_synchronized else "out of sync"
    lines.append(
        f"{len(result.copied_files)} {verb}, {len(result.unchanged_files)} unchanged, {status}"
    )
    return "\\n".join(lines) + "\\n"


def _emit(json_text: str, json_target: Optional[str], human_text: str) -> None:
    """Write output as JSON (stdout or file) or as human readable text."""
    if json_target is None:
        sys.stdout.write(human_text)
        return
    if json_target == "-":
        sys.stdout.write(json_text + "\\n")
        return
    write_atomic(json_target, json_text + "\\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint. Returns 0 when compliant, 1 on defects, 2 on errors."""
    parser = build_cli_parser()
    args = parser.parse_args(argv)
    config = DEFAULT_PURGE_CONFIG

    try:
        if args.sync_trees:
            sync_res = sync_trees(
                args.source_root,
                args.mirror_root,
                dry_run=args.dry_run,
            )
            _emit(sync_res.to_json(), args.json_output, _format_sync(sync_res))
            return 0 if sync_res.is_synchronized else 1

        targets = [Path(item) for item in (args.paths or [config.default_production_root])]
        diffs: List[str] = []

        if args.purge:
            plan, diffs = _run_purge_batch(targets, args.dry_run, config)
        elif args.remediate:
            reports: List[FileASTReport] = []
            for path in _iter_python_files(
                targets,
                config.excluded_dir_names,
                config.excluded_file_names,
            ):
                report, original, updated = _remediate_path(
                    path,
                    args.dry_run,
                    args.strict_comments,
                )
                reports.append(report)
                if original is not None and updated is not None and original != updated:
                    diffs.append(compute_diff(original, updated, str(path)))
            plan = _plan_from_reports(reports)
        else:
            plan = ASTEradicationEngine(
                strict_comments=args.strict_comments
            ).inspect_paths(targets)

        if args.diff:
            for diff in diffs:
                sys.stdout.write(diff)

        _emit(plan.to_json(), args.json_output, _format_plan(plan))
        return 0 if plan.is_compliant else 1

    except (OSError, ValueError, CoChemError) as err:
        sys.stderr.write(f"error: {_describe_error(err)}\\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
'''

# Write to src/cochem/core/ast_eradication_engine.py and cochem/core/ast_eradication_engine.py
for target_path in [
    Path("src/cochem/core/ast_eradication_engine.py"),
    Path("cochem/core/ast_eradication_engine.py"),
]:
    target_path.write_text(CODE, encoding="utf-8")
    print(f"Written: {target_path} ({len(CODE)} bytes)")
