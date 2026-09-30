# src/cochem/dsp/press/compliance.py

`python
"""Author Guidelines Compliance Checker (MC-DSP-23)."""
from __future__ import annotations

import re
from typing import Any

# Canonical journal guidelines for Academic Press publishing workflows
JOURNAL_RULES: dict[str, dict[str, Any]] = {
    "ACS_Catalysis": {
        "max_words": 8000,
        "max_figures": 12,
        "required_sections": ["Introduction", "Results", "Discussion", "Computational Methods"],
    },
    "JACS": {
        "max_words": 9000,
        "max_figures": 10,
        "required_sections": ["Introduction", "Results", "Discussion", "Experimental Section"],
    },
    "Nature": {
        "max_words": 4000,
        "max_figures": 6,
        "required_sections": ["Introduction", "Results", "Discussion", "Methods"],
    },
    "Science": {
        "max_words": 4500,
        "max_figures": 4,
        "required_sections": ["Abstract", "Introduction", "Results", "Discussion", "Materials and Methods"],
    },
}


def _normalize_journal_name(target_journal: str) -> str:
    """Normalizes journal identifiers to match canonical keys case-insensitively."""
    cleaned = target_journal.strip().lower().replace(" ", "_").replace("-", "_")
    for key in JOURNAL_RULES:
        if key.lower() == cleaned:
            return key
    return target_journal


def validate_section_headers(headers: list[str], target_journal: str = "ACS_Catalysis") -> bool:
    """Validates that mandatory section headers are present for the target journal."""
    journal_key = _normalize_journal_name(target_journal)
    rules = JOURNAL_RULES.get(journal_key)
    if not rules:
        return True

    # Strip and exclude empty/whitespace-only headers to prevent spoofing
    valid_headers_lower = [h.strip().lower() for h in headers if isinstance(h, str) and h.strip()]
    if not valid_headers_lower:
        return False

    for required in rules["required_sections"]:
        req_lower = required.lower()
        if not any(req_lower in h or (len(h) >= 3 and h in req_lower) for h in valid_headers_lower):
            return False
    return True


def check_author_guidelines(
    word_count: int,
    figure_count: int,
    target_journal: str = "ACS_Catalysis",
    section_headers: list[str] | None = None,
    sections: list[str] | None = None,
) -> bool:
    """Validates word counts, figure counts, and required sections against journal rules."""
    if word_count < 0 or figure_count < 0:
        return False

    journal_key = _normalize_journal_name(target_journal)
    rules = JOURNAL_RULES.get(journal_key)
    if rules:
        if word_count > rules["max_words"] or figure_count > rules["max_figures"]:
            return False

    headers_to_check = section_headers if section_headers is not None else sections
    if headers_to_check is not None:
        if not validate_section_headers(headers_to_check, journal_key):
            return False

    return True


def inspect_manuscript_compliance(content: str, target_journal: str = "ACS_Catalysis") -> dict[str, Any]:
    """Inspects raw manuscript Markdown and validates compliance against author guidelines."""
    words = re.findall(r"\b\w+\b", content)
    word_count = len(words)
    figure_matches = re.findall(r"!\[[\s\S]*?\]\(.*?\)|\<img[\s\S]*?\>|\\begin\{figure\}", content)
    figure_count = len(figure_matches)
    headers = [m.strip("# \t\r\n") for m in re.findall(r"^#{1,3}\s+(.+)$", content, flags=re.MULTILINE)]

    journal_key = _normalize_journal_name(target_journal)
    compliant = check_author_guidelines(
        word_count=word_count,
        figure_count=figure_count,
        target_journal=journal_key,
        section_headers=headers,
    )
    return {
        "target_journal": journal_key,
        "word_count": word_count,
        "figure_count": figure_count,
        "headers": headers,
        "compliant": compliant,
    }

`
