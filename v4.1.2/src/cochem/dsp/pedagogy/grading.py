"""Multimodal Grading Rubric Evaluator (MC-DSP-29)."""
from __future__ import annotations
import re
from typing import Any

CRITERION_KEYWORDS: dict[str, list[str]] = {
    "thermodynamics": ["enthalpy", "entropy", "gibbs", "free energy", "exothermic", "endothermic", "temperature", "kelvin", "delta"],
    "kinetics": ["rate", "activation", "arrhenius", "catalyst", "order", "half-life", "barrier", "collision"],
    "mechanism": ["intermediate", "transition state", "nucleophile", "electrophile", "step", "pathway", "elementary"],
    "derivation": ["integral", "derivative", "equation", "substitute", "solve", "rearrange", "constant"],
    "quantum": ["wavefunction", "hamiltonian", "schrodinger", "orbital", "eigenvalue", "operator", "spin"],
    "units": ["j/mol", "kj/mol", "kcal/mol", "ev", "nm", "angstrom", "atm", "kpa", "kelvin", "molar"],
}


def evaluate_student_submission(submission_text: str, rubric: dict[str, int]) -> dict[str, Any]:
    """Evaluates student submission against didactic rubric criteria using domain lexical analysis."""
    if not isinstance(submission_text, str):
        raise TypeError("Submission text must be a string")
    if not isinstance(rubric, dict) or not rubric:
        raise ValueError("Rubric must be a non-empty dictionary mapping criteria to point weights")

    cleaned_text = submission_text.strip().lower()
    words = set(re.findall(r"\b[a-z0-9_/.-]+\b", cleaned_text))
    word_count = len(re.findall(r"\b\w+\b", cleaned_text))
    
    total_max = sum(rubric.values())
    if word_count == 0:
        return {
            "score": 0.0,
            "max_score": float(total_max),
            "breakdown": {crit: 0.0 for crit in rubric},
            "feedback": "Submission is empty. Zero credit awarded.",
        }

    breakdown: dict[str, float] = {}
    feedback_notes: list[str] = []
    
    for criterion, max_pts in rubric.items():
        crit_key = criterion.lower()
        matched_keywords: list[str] = []
        
        # Check standard keyword associations or direct substring match
        target_keys = CRITERION_KEYWORDS.get(crit_key, [crit_key])
        for kw in target_keys:
            if kw in cleaned_text:
                matched_keywords.append(kw)
                
        if len(matched_keywords) >= 2 or (len(matched_keywords) == 1 and word_count >= 15):
            pts = float(max_pts)
            feedback_notes.append(f"Strong reasoning on {criterion} (matched concepts: {', '.join(matched_keywords[:3])}).")
        elif len(matched_keywords) == 1:
            pts = round(max_pts * 0.5, 1)
            feedback_notes.append(f"Partial credit on {criterion}: mentioned {matched_keywords[0]} but needs deeper physical justification.")
        else:
            pts = 0.0
            feedback_notes.append(f"Missing core concepts for {criterion}.")

        breakdown[criterion] = pts

    total_score = round(sum(breakdown.values()), 1)
    return {
        "score": total_score,
        "max_score": float(total_max),
        "breakdown": breakdown,
        "feedback": " ".join(feedback_notes),
    }

