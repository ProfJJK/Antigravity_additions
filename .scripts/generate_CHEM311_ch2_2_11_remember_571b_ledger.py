#!/usr/bin/env python
"""Regenerate the mendeleev-derived numeric fields of the CHEM311 2.11 Remember ledgers.

The two ledger JSON files hold the static chemistry narrative (definitions,
distractor matrix, blueprint). Every atomic mass, species mass and the recorded
mendeleev package version is written by this script from the live ``mendeleev``
library, so no atomic weight is typed by hand.

Usage:
    python .scripts/generate_CHEM311_ch2_2_11_remember_571b_ledger.py           # rewrite both ledgers
    python .scripts/generate_CHEM311_ch2_2_11_remember_571b_ledger.py --check   # verify only, exit 1 on drift
"""
import argparse
import copy
import json
import re
import sys
from functools import lru_cache
from importlib import metadata
from pathlib import Path

from mendeleev import element

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
LEDGER_NAME = "CHEM311_ch2_2.11_remember_571b_ledger.json"
ROOT_LEDGER = WORKSPACE_ROOT / LEDGER_NAME
PROMPT_LEDGER = WORKSPACE_ROOT / ".scripts" / "prompts" / LEDGER_NAME

CONSTITUENT_ELEMENTS = ["H", "B", "C", "N", "O", "F", "Al", "Cl", "Ti", "Fe"]
MASS_DECIMALS = 9

_TOKEN = re.compile(r"[A-Z][a-z]?|\(|\)|\d+")
_CHARGE_SUFFIX = re.compile(r"[+-]\d*$")


@lru_cache(maxsize=None)
def atomic_mass(symbol):
    """Atomic mass of an element straight from the mendeleev library."""
    return float(element(symbol).mass)


def parse_formula(formula):
    """Parse a formula such as (CH3)2O or OH- into {symbol: count}.

    A trailing charge is stripped; the mass convention is the sum of
    neutral-atom masses.
    """
    core = _CHARGE_SUFFIX.sub("", formula)
    tokens = _TOKEN.findall(core)
    if not core or "".join(tokens) != core:
        raise ValueError(f"unsupported formula: {formula!r}")
    stack = [{}]
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "(":
            stack.append({})
            i += 1
        elif tok == ")":
            if len(stack) < 2:
                raise ValueError(f"unbalanced parentheses in {formula!r}")
            group = stack.pop()
            mult = 1
            if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                mult = int(tokens[i + 1])
                i += 1
            for sym, cnt in group.items():
                stack[-1][sym] = stack[-1].get(sym, 0) + cnt * mult
            i += 1
        elif tok.isdigit():
            raise ValueError(f"digit without element in {formula!r}")
        else:
            count = 1
            if i + 1 < len(tokens) and tokens[i + 1].isdigit():
                count = int(tokens[i + 1])
                i += 1
            stack[-1][tok] = stack[-1].get(tok, 0) + count
            i += 1
    if len(stack) != 1:
        raise ValueError(f"unbalanced parentheses in {formula!r}")
    return stack[0]


def formula_mass(formula):
    counts = parse_formula(formula)
    return round(sum(atomic_mass(sym) * n for sym, n in counts.items()), MASS_DECIMALS)


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _write(path, data):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _refresh_example(example):
    for key in ("mass_amu", "neutral_parent_mass_amu"):
        if key in example:
            example[key] = formula_mass(example["formula"])


def _refresh_reaction(reaction):
    masses = {}
    for role in ("acid", "base"):
        for suffix in ("_mass_amu", "_neutral_parent_mass_amu"):
            key = f"reactant_{role}{suffix}"
            if key in reaction:
                masses[role] = formula_mass(reaction[f"reactant_{role}"])
                reaction[key] = masses[role]
    if "adduct_mass_amu" in reaction:
        if set(masses) != {"acid", "base"}:
            raise ValueError("adduct mass requires both reactant masses")
        reaction["adduct_mass_amu"] = round(masses["acid"] + masses["base"], MASS_DECIMALS)


def refresh_root(ledger):
    block = ledger["elemental_masses_mendeleev"]
    block["mass_values_amu"] = {sym: atomic_mass(sym) for sym in CONSTITUENT_ELEMENTS}
    block["species_masses_amu"] = {f: formula_mass(f) for f in block["species_masses_amu"]}
    block["mendeleev_version"] = metadata.version("mendeleev")
    truth = ledger["chemical_ground_truth"]
    for role in ("lewis_acid", "lewis_base"):
        for example in truth[role]["representative_examples"]:
            _refresh_example(example)
    for reaction in ledger["authentic_reactions"]:
        _refresh_reaction(reaction)


def refresh_prompt(prompt):
    prompt["elemental_masses"] = {sym: atomic_mass(sym) for sym in CONSTITUENT_ELEMENTS}
    prompt["molecular_weights"] = {f: formula_mass(f) for f in prompt["molecular_weights"]}
    prompt["elemental_mass_provenance"]["mendeleev_version"] = metadata.version("mendeleev")


def check_parity(root, prompt):
    """Raise ValueError when the two ledgers disagree on shared content."""
    root_block = root["elemental_masses_mendeleev"]
    if prompt["elemental_masses"] != root_block["mass_values_amu"]:
        raise ValueError("elemental masses differ between root and prompt ledger")
    for formula, mass in prompt["molecular_weights"].items():
        if formula in root_block["species_masses_amu"] and root_block["species_masses_amu"][formula] != mass:
            raise ValueError(f"species mass for {formula} differs between ledgers")
    root_ids = [(o["formula"], o["is_correct"], o["misconception_id"]) for o in root["options_blueprint"]]
    prompt_ids = [(o["formula"], o["is_correct"], o["misconception_id"]) for o in prompt["options_blueprint"]]
    if root_ids != prompt_ids:
        raise ValueError("options_blueprint differs between root and prompt ledger")
    if len(root["five_distractor_specification_matrix"]) != 5:
        raise ValueError("distractor matrix must hold exactly five entries")
    if len(root["options_blueprint"]) != root["item_structure_reconciliation"]["single_choice_option_count"]:
        raise ValueError("options_blueprint size disagrees with the declared option count")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify the ledgers without writing")
    args = parser.parse_args(argv)

    root_original = _load(ROOT_LEDGER)
    prompt_original = _load(PROMPT_LEDGER)
    root = copy.deepcopy(root_original)
    prompt = copy.deepcopy(prompt_original)

    refresh_root(root)
    refresh_prompt(prompt)
    try:
        check_parity(root, prompt)
    except ValueError as exc:
        print(f"parity error: {exc}", file=sys.stderr)
        return 2

    drifted = []
    if root != root_original:
        drifted.append(str(ROOT_LEDGER))
    if prompt != prompt_original:
        drifted.append(str(PROMPT_LEDGER))

    if args.check:
        if drifted:
            print("ledger values differ from live mendeleev output:", file=sys.stderr)
            for path in drifted:
                print(f"  {path}", file=sys.stderr)
            return 1
        print("ledgers match live mendeleev output " + metadata.version("mendeleev"))
        return 0

    _write(ROOT_LEDGER, root)
    _write(PROMPT_LEDGER, prompt)
    print(f"wrote {ROOT_LEDGER} and {PROMPT_LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
