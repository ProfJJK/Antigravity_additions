# [MC-RAG-06] SRS-412-06-FR-001
# SPEC: Create the file: this chunk owns the module docstring, imports and constants. Satisfy
# SPEC: SRS-412-06-FR-001: The system shall maintain permanent reference knowledge in `.sources/` and
# SPEC: live ratified specifications in `wiki/`. Neither .sources/ nor v4.1.2_manifest.json exists in
# SPEC: the v4.1.2 tree today: a missing root must raise a typed error, never be created or skipped
# SPEC: silently.
"""Dual Wiki Corpus and Knowledge Root Discovery (SRS-412-06-FR-001)."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Any, NamedTuple

SOURCES_DIR_NAME: str = ".sources"
WIKI_DIR_NAME: str = "wiki"
MANIFEST_FILE_NAME: str = "v4.1.2_manifest.json"
MANIFEST_FILENAME: str = MANIFEST_FILE_NAME

class CorpusRootError(FileNotFoundError):
    """Base typed exception for corpus root discovery failures (SRS-412-06-FR-001)."""

class MissingCorpusRootError(CorpusRootError):
    """Raised when permanent reference knowledge root or live wiki root is missing."""
MissingKnowledgeRootError = MissingCorpusRootError

class CorpusRoots(NamedTuple):
    """Physical directory roots for reference knowledge and live wiki specs."""
    sources: Path
    wiki: Path
    @property
    def sources_dir(self) -> Path: return self.sources
    @property
    def wiki_dir(self) -> Path: return self.wiki

def validate_corpus_root(path: str | Path) -> Path:
    """Validates that a corpus root directory physically exists on disk."""
    p = Path(path).resolve()
    if not p.is_dir(): raise MissingCorpusRootError(f"Corpus root does not exist: {p}")
    return p

def discover_corpus_roots(
    base_dir: Path | str | None = None,
    *,
    sources_base_dir: Path | str | None = None,
) -> CorpusRoots:
    """Discovers and validates .sources/ and wiki/ dual roots (SRS-412-06-FR-001).

    wiki/ is resolved under base_dir (default: the current directory). .sources/ is resolved under
    sources_base_dir when given, otherwise under base_dir as well. The live layout keeps the
    permanent .sources/ one level above the versioned tree (``<agentic>/.sources`` beside
    ``<agentic>/v4.1.2/wiki``), which is expressed as
    ``discover_corpus_roots(repo, sources_base_dir=repo.parent)``. A missing root raises
    MissingCorpusRootError naming the full path; nothing is created.
    """
    root = Path(base_dir).resolve() if base_dir is not None else Path.cwd().resolve()
    src_base = Path(sources_base_dir).resolve() if sources_base_dir is not None else root
    return CorpusRoots(validate_corpus_root(src_base / SOURCES_DIR_NAME), validate_corpus_root(root / WIKI_DIR_NAME))
discover_dual_roots = discover_corpus_roots

# [MC-RAG-07] SRS-412-06-FR-007
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-06-FR-007:
# SPEC: Scientific notation, quantum formulas, and Greek characters (`Ψ(r)`, `ΔG°`, `kJ·mol⁻¹`) shall be
# SPEC: preserved losslessly in UTF-8. Read with encoding='utf-8' (no errors='ignore'); split sections
# SPEC: on Markdown headings. Traceability test:
# SPEC: test_f05_srs_handles_unicode_and_scientific_formula_symbols (tests/test_stage1_srs_dag.py).
HEADING_REGEX: re.Pattern[str] = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_CLOSING_SEQUENCE_REGEX: re.Pattern[str] = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")
_FENCE_OPEN_REGEX: re.Pattern[str] = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_LINE_REGEX: re.Pattern[str] = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+\Z")


class MarkdownSection(NamedTuple):
    """Represents a discrete section split on Markdown headings with lossless content."""
    title: str
    content: str
    line_start: int = 1


Section = MarkdownSection


def read_lossless_utf8(file_path: Path | str) -> str:
    """Decodes the file bytes as strict UTF-8 with no newline translation (SRS-412-06-FR-007).

    A UTF-8 BOM is kept as U+FEFF, CRLF/CR line endings are kept, and invalid UTF-8 raises
    UnicodeDecodeError, so ``read_lossless_utf8(p).encode("utf-8") == p.read_bytes()``.
    """
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Markdown file not found: {path}")
    return path.read_bytes().decode("utf-8", errors="strict")


read_document_utf8 = read_lossless_utf8


def _heading_title(line: str) -> str | None:
    """Returns the ATX heading title of one line, or None if the line is not a heading."""
    m = HEADING_REGEX.match(line.rstrip("\r\n").lstrip("\ufeff"))
    if m is None:
        return None
    return _CLOSING_SEQUENCE_REGEX.sub("", m.group(2) or "").strip()


def _closes_fence(line: str, fence: str) -> bool:
    """True if ``line`` closes a code fence opened with ``fence`` (same char, >= length)."""
    body = line.rstrip("\r\n")
    stripped = body.lstrip(" ")
    if len(body) - len(stripped) > 3:
        return False
    run = len(stripped) - len(stripped.lstrip(fence[0]))
    return run >= len(fence) and not stripped[run:].strip()


def split_markdown_sections(text: str, default_title: str = "Preamble") -> list[MarkdownSection]:
    """Splits Markdown on ATX headings outside fenced code blocks, losslessly.

    Section contents are exact slices of ``text``: ``"".join(s.content for s in sections) == text``.
    Text before the first heading becomes a ``default_title`` section when it has non-blank
    content; blank lead-in is kept at the start of the first heading section. A document with
    no headings is returned as one ``default_title`` section.
    """
    headings: list[tuple[int, int, str]] = []
    fence: str | None = None
    for line_no, m in enumerate(_LINE_REGEX.finditer(text), start=1):
        line = m.group(0)
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
            continue
        opener = _FENCE_OPEN_REGEX.match(line.lstrip("\ufeff"))
        if opener is not None and not (opener.group(1)[0] == "`" and "`" in line[opener.end():]):
            fence = opener.group(1)
            continue
        title = _heading_title(line)
        if title is not None:
            headings.append((m.start(), line_no, title))
    if not headings:
        return [MarkdownSection(title=default_title, content=text, line_start=1)]
    sections: list[MarkdownSection] = []
    first_start = headings[0][0]
    lead_in = text[:first_start]
    if lead_in.strip():
        sections.append(MarkdownSection(default_title, lead_in, 1))
    for i, (start, line_no, title) in enumerate(headings):
        begin = 0 if i == 0 and not lead_in.strip() else start
        end = headings[i + 1][0] if i + 1 < len(headings) else len(text)
        sections.append(MarkdownSection(title, text[begin:end], line_no))
    return sections
split_sections = split_markdown_sections
# [MC-RAG-08] SRS-412-06-FR-004
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-06-FR-004: All
# SPEC: relative Markdown links across wiki chapters shall be programmatically validated with 0 broken
# SPEC: cross-references. Traceability test: test_f05_srs_detects_broken_relative_links_between_chapters
# SPEC: (tests/test_stage1_srs_dag.py).
RELATIVE_LINK_REGEX: re.Pattern[str] = re.compile(r"\[.*?\]\((?!(?:https?://))([^)]*?\.md)(?:#[^)]*)?\)")
MARKDOWN_LINK_REGEX: re.Pattern[str] = RELATIVE_LINK_REGEX

class BrokenCrossReferenceError(ValueError):
    """Raised when relative Markdown cross-references fail validation."""
BrokenLinkError = BrokenCrossReferenceError

class LinkValidationResult(NamedTuple):
    """Validation metrics for relative cross-references within a markdown document."""
    source_file: Path
    links: list[str]
    broken_links: list[str]
    @property
    def is_valid(self) -> bool: return len(self.broken_links) == 0

def extract_relative_links(content: str) -> list[str]:
    """Extracts all relative .md cross-reference links from Markdown text."""
    return RELATIVE_LINK_REGEX.findall(content)
extract_markdown_links = extract_relative_links

def validate_relative_links(file_path: Path | str, base_dir: Path | str | None = None) -> list[str]:
    """Validates relative markdown links, returning a list of broken references."""
    path = Path(file_path)
    base = Path(base_dir).resolve() if base_dir is not None else (path.parent.resolve() if path.is_file() else Path.cwd().resolve())
    text = read_lossless_utf8(path) if path.is_file() else str(file_path)
    links = extract_relative_links(text)
    return [link for link in links if not (base / link.split("#")[0]).exists()]
find_broken_relative_links = validate_relative_links
validate_markdown_links = validate_relative_links
find_broken_links = validate_relative_links

def validate_wiki_cross_references(wiki_dir: Path | str) -> dict[str, list[str]]:
    """Scans wiki chapters and validates that cross-references resolve with 0 broken links."""
    root = Path(wiki_dir).resolve()
    broken: dict[str, list[str]] = {}
    for md in sorted(root.glob("*.md")):
        missing = validate_relative_links(md, base_dir=root)
        if missing: broken[md.name] = missing
    return broken
validate_cross_references = validate_wiki_cross_references
# [MC-RAG-09] SRS-412-06-FR-005
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-06-FR-005: Every
# SPEC: SRS and wiki chapter document shall strictly enforce a maximum length limit of <= 400 lines.
# SPEC: Traceability test: test_f05_srs_chapters_strictly_less_than_400_lines
# SPEC: (tests/test_stage1_srs_dag.py).
MAX_CHAPTER_LINE_LIMIT: int = 400
CHAPTER_MAX_LINES: int = MAX_CHAPTER_LINE_LIMIT
MAX_SRS_CHAPTER_LINES: int = MAX_CHAPTER_LINE_LIMIT

class ChapterLengthExceededError(ValueError):
    """Raised when an SRS or wiki chapter document exceeds the <= 400 lines limit."""
ChapterLengthLimitError = ChapterLengthExceededError

def count_document_lines(file_path: Path | str) -> int:
    """Counts physical lines in document using lossless UTF-8 reading (SRS-412-06-FR-005)."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Chapter file not found: {path}")
    return len(read_lossless_utf8(path).splitlines())

def validate_chapter_length(file_path: Path | str, max_lines: int = MAX_CHAPTER_LINE_LIMIT) -> int:
    """Strictly enforces maximum length limit of <= 400 lines on a single chapter document."""
    lines = count_document_lines(file_path)
    if lines > max_lines:
        raise ChapterLengthExceededError(f"Chapter {Path(file_path).name} exceeds {max_lines} lines: {lines}")
    return lines
enforce_chapter_length_limit = validate_chapter_length
check_chapter_length_gate = validate_chapter_length

def validate_all_chapters_length(directory: Path | str, max_lines: int = MAX_CHAPTER_LINE_LIMIT) -> dict[str, int]:
    """Strictly enforces maximum length limit of <= 400 lines across all chapter documents."""
    dir_path = Path(directory).resolve()
    if not dir_path.is_dir():
        raise MissingCorpusRootError(f"Directory not found: {dir_path}")
    results: dict[str, int] = {}
    violations: list[str] = []
    for md in sorted(dir_path.glob("*.md")):
        count = count_document_lines(md)
        results[md.name] = count
        if count > max_lines:
            violations.append(f"{md.name} ({count} > {max_lines})")
    if violations:
        raise ChapterLengthExceededError(f"Chapters exceed line limit: {', '.join(violations)}")
    return results
validate_corpus_chapters_length = validate_all_chapters_length
# [MC-RAG-10] SRS-412-06-FR-006
# SPEC: Append after the previous chunk without editing earlier lines. Satisfy SRS-412-06-FR-006: The
# SPEC: knowledge base structure and document catalog shall be synchronized with `v4.1.2_manifest.json`.
# SPEC: v4.1.2_manifest.json does not exist yet; report its absence as a sync failure rather than
# SPEC: writing a manifest from this chunk.
import hashlib
import json

REPO_ROOT: Path = Path(__file__).resolve().parents[3]


class ManifestSyncError(ValueError):
    """Base typed error: knowledge base is not synchronized with the manifest (SRS-412-06-FR-006)."""


class ManifestNotFoundError(ManifestSyncError, CorpusRootError):
    """Raised when v4.1.2_manifest.json is absent; absence is a sync failure, never 'in sync'."""


class ManifestFormatError(ManifestSyncError):
    """Raised when the manifest is not valid UTF-8 JSON or its catalog schema is invalid."""


class ManifestDesyncError(ManifestSyncError):
    """Raised by assert_manifest_in_sync when the catalog and the files on disk differ."""


class ManifestSyncReport(NamedTuple):
    """Real differences between the manifest catalog and files physically on disk."""
    manifest_path: Path
    missing_directories: list[str]
    missing_on_disk: list[str]
    unlisted_on_disk: list[str]
    hash_mismatches: list[str]

    @property
    def in_sync(self) -> bool:
        return not (self.missing_directories or self.missing_on_disk or self.unlisted_on_disk or self.hash_mismatches)


def load_manifest_catalog(manifest_path: Path | str) -> tuple[list[str], dict[str, str | None]]:
    """Parses the manifest into (structure directories, {posix relative path: sha256 or None})."""
    path = Path(manifest_path)
    if not path.is_file():
        raise ManifestNotFoundError(f"Manifest not found (knowledge base cannot be synchronized): {path}")
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestFormatError(f"Manifest is not valid UTF-8 JSON: {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ManifestFormatError(f"Manifest must be an object with a 'documents' list: {path}")
    dirs = data.get("directories", [WIKI_DIR_NAME, SOURCES_DIR_NAME])
    if not isinstance(dirs, list) or not all(isinstance(d, str) and d for d in dirs):
        raise ManifestFormatError(f"Manifest 'directories' must be a list of non-empty strings: {path}")
    catalog: dict[str, str | None] = {}
    for entry in data["documents"]:
        if isinstance(entry, str):
            rel, digest = entry, None
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
            rel, digest = entry["path"], entry.get("sha256")
        else:
            raise ManifestFormatError(f"Invalid manifest document entry: {entry!r}")
        norm = Path(rel.replace("\\", "/"))
        if not rel or norm.is_absolute() or norm.anchor or ".." in norm.parts:
            raise ManifestFormatError(f"Manifest document path must be relative inside the repo: {rel!r}")
        if digest is not None and not isinstance(digest, str):
            raise ManifestFormatError(f"Manifest sha256 must be a string: {rel!r}")
        key = norm.as_posix()
        if key in catalog:
            raise ManifestFormatError(f"Duplicate manifest document entry: {key}")
        catalog[key] = digest.lower() if digest else None
    return [Path(d.replace("\\", "/")).as_posix() for d in dirs], catalog


def check_manifest_sync(base_dir: Path | str | None = None, manifest_path: Path | str | None = None) -> ManifestSyncReport:
    """Compares the manifest document catalog with real files under its directories (SRS-412-06-FR-006)."""
    root = Path(base_dir).resolve() if base_dir is not None else REPO_ROOT
    mpath = Path(manifest_path) if manifest_path is not None else root / MANIFEST_FILE_NAME
    dirs, catalog = load_manifest_catalog(mpath)
    missing_dirs = [d for d in dirs if not (root / d).is_dir()]
    on_disk: set[str] = set()
    for d in dirs:
        if (root / d).is_dir():
            on_disk.update(p.relative_to(root).as_posix() for p in (root / d).rglob("*") if p.is_file())
    missing = sorted(rel for rel in catalog if not (root / rel).is_file())
    unlisted = sorted(on_disk - set(catalog))
    mismatched = sorted(rel for rel, digest in catalog.items() if digest is not None and (root / rel).is_file()
                        and hashlib.sha256((root / rel).read_bytes()).hexdigest() != digest)
    return ManifestSyncReport(mpath, missing_dirs, missing, unlisted, mismatched)


def assert_manifest_in_sync(base_dir: Path | str | None = None, manifest_path: Path | str | None = None) -> ManifestSyncReport:
    """Raises ManifestDesyncError listing every real difference; returns the report when in sync."""
    report = check_manifest_sync(base_dir, manifest_path)
    if not report.in_sync:
        raise ManifestDesyncError(
            f"Knowledge base out of sync with {report.manifest_path.name}: missing_directories="
            f"{report.missing_directories} missing_on_disk={report.missing_on_disk} "
            f"unlisted_on_disk={report.unlisted_on_disk} hash_mismatches={report.hash_mismatches}")
    return report
sync_manifest_catalog = check_manifest_sync
