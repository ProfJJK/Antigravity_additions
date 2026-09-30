# src/cochem/dsp/toolkit/skill_gen.py

`python
"""Antigravity Skill Manifest Generator (MC-DSP-33)."""
from __future__ import annotations

from pathlib import Path


def generate_skill_manifest(skill_name: str, description: str, out_path: Path) -> Path:
    """Generates standard SKILL.md manifest with YAML frontmatter.

    Args:
        skill_name: The identifier/name of the skill.
        description: Description of the skill's role and capabilities.
        out_path: Destination path for the SKILL.md manifest file.

    Returns:
        The Path to the created SKILL.md manifest.
    """
    dest = Path(out_path)
    content = f"""---
name: {skill_name}
description: {description}
---
# {skill_name}
{description}
"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    return dest

`
