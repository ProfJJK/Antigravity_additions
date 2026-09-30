"""CHEM311 Chapter 1, Section 1 Question Bank Generator.

McMurry 10e Chapter 1, Section 1 (Introduction / General).
Generates an authoritative 10-question multiple-choice databank formatted
for R/exams (exams2nops, exams2pdf) and OCR scanning, distributed across
Bloom's Taxonomy pyramid.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

DEFAULT_OUTPUT_PATH: Path = Path(
    "D:/Gdrive/__agentic/.sources/r_exams/CHEM311/question_bank/Chapter_1/1_Introduction___General.md"
)

QUESTION_BANK_MARKDOWN: str = r"""# Question Bank: Chapter 1, Section 1 — Introduction / General
**Course:** CHEM311 (Organic Chemistry I)  
**Textbook:** Organic Chemistry 10e, John McMurry  
**Section:** Chapter 1, Section 1 Introduction / General  
**Target Format:** R/exams (compatible with `exams2nops`, `exams2pdf`, and Canvas/Moodle) & OCR High-Precision Scanning  
**Total Questions:** 10  
**Bloom's Taxonomy Distribution:**
- **Remember:** Questions 1, 2, 3 (Base of pyramid)
- **Understand:** Questions 4, 5, 6
- **Apply:** Questions 7, 8
- **Analyze:** Question 9
- **Evaluate:** Question 10 (Peak of pyramid)

---

# Question 1

Question
========
What is the modern scientific definition of **organic chemistry**?

Answerlist
----------
* The branch of chemistry dedicated to the study of the structure, properties, composition, reactions, and preparation of carbon-containing compounds.
* The branch of chemistry that studies exclusively compounds isolated directly from living biological organisms.
* The branch of chemistry focused on the extraction and refinement of mineral ores and transition metal complexes.
* The study of macroscopic physical systems and chemical changes that do not contain carbon-hydrogen bonds.
* The study of synthetic polymers composed solely of silicon, germanium, and other non-carbon Group 4A elements.

Solution
========
Historically, organic chemistry was defined as the study of substances derived from living organisms, which were believed to possess a "vital force." In modern science, organic chemistry is defined broadly and rigorously as the chemistry of carbon compounds, regardless of their origin (natural or synthetic). With few traditional exceptions (such as carbon dioxide, carbonate minerals, and cyanides, which are often grouped with inorganic chemistry for historical reasons), any compound based on a carbon framework is classified as an organic molecule.

Answerlist
----------
* Correct. Organic chemistry is modernly defined as the study of carbon-containing compounds.
* Incorrect. This reflects the obsolete 18th-century "vitalism" definition; organic compounds can be readily synthesized in the laboratory from nonliving matter.
* Incorrect. The study of mineral ores and transition metals belongs to inorganic chemistry and metallurgy.
* Incorrect. Systems lacking carbon belong to inorganic and physical chemistry domains.
* Incorrect. Silicon and germanium polymers belong to inorganic or materials chemistry, not organic chemistry.

Meta-information
================
exname: ch01_sec01_definition_organic_chem_remember
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Remember

---

# Question 2

Question
========
In 1828, which German chemist delivered a foundational blow to the theory of "vitalism" by synthesizing the organic compound **urea** from the inorganic salt ammonium cyanate?

Answerlist
----------
* Friedrich Wöhler
* August Kekulé
* Dmitri Mendeleev
* Antoine Lavoisier
* Jacobus Henricus van 't Hoff

Solution
========
In 1828, Friedrich Wöhler successfully synthesized urea ($\text{NH}_2\text{CONH}_2$), a known biological waste product found in mammalian urine, by heating the inorganic salt ammonium cyanate ($\text{NH}_4\text{OCN}$). This landmark discovery demonstrated that organic compounds do not require an enigmatic "vital force" associated with living organisms, establishing that organic and inorganic substances obey the same fundamental physical and chemical laws.

Answerlist
----------
* Correct. Friedrich Wöhler synthesized urea from ammonium cyanate in 1828.
* Incorrect. August Kekulé proposed the ring structure of benzene and carbon tetravalency in the late 1850s and 1860s.
* Incorrect. Dmitri Mendeleev created the Periodic Table of the Elements in 1869.
* Incorrect. Antoine Lavoisier established the law of conservation of mass and modern chemical nomenclature in the late 18th century.
* Incorrect. Jacobus Henricus van 't Hoff independently proposed the tetrahedral carbon atom in 1874.

Meta-information
================
exname: ch01_sec01_wohler_synthesis_remember
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Remember

---

# Question 3

Question
========
What chemical term specifically designates the unique ability of carbon atoms to link together via strong, stable covalent bonds to form extensive linear chains, branched frameworks, and rings?

Answerlist
----------
* Catenation
* Allotropy
* Electronegativity
* Solvation
* Ionization

Solution
========
Catenation is the linkage of atoms of the same element into longer chains, branched trees, or cyclic structures through covalent bonds. While a few other elements (such as silicon and sulfur) exhibit catenation to a limited degree, carbon possesses an exceptional ability to catenate into virtually limitless architectures because carbon-carbon ($\text{C--C}$) single, double, and triple bonds are extraordinarily stable and strong.

Answerlist
----------
* Correct. Catenation is the self-linking of carbon atoms into chains and rings.
* Incorrect. Allotropy refers to the existence of an element in two or more different physical forms (e.g., diamond, graphite, and fullerene for elemental carbon).
* Incorrect. Electronegativity is the intrinsic ability of an atom in a molecule to attract shared electrons toward itself.
* Incorrect. Solvation describes the stabilization of solute particles by solvent molecules.
* Incorrect. Ionization is the physical process of removing or adding electrons to an atom or molecule to form ions.

Meta-information
================
exname: ch01_sec01_catenation_remember
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Remember

---

# Question 4

Question
========
Which factor best explains why carbon forms an enormous diversity of stable molecular compounds (more than 50 million identified structures), whereas other elements in Group 4A (such as silicon and lead) do not?

Answerlist
----------
* Carbon has a small covalent radius and intermediate electronegativity, enabling it to form short, strong, and unreactive $\text{C--C}$ and $\text{C--heteroatom}$ bonds that resist spontaneous decomposition.
* Carbon possesses low-energy d orbitals that allow it to expand its valence octet up to 12 electrons in typical organic molecules.
* Carbon is exceptionally electropositive, allowing it to easily lose all four valence electrons to form stable $\text{C}^{4+}$ cations in solution.
* Carbon forms exclusively ionic bonds with hydrogen and other nonmetals, preventing bond rupture under ambient conditions.
* Carbon atoms can form stable bonds only with other carbon atoms and are incapable of reacting with elements outside of Group 4A.

Solution
========
Carbon occupies a unique position in the periodic table: as a second-row Group 4A element (atomic number $Z = 6$), it has a compact atomic size ($2s$ and $2p$ valence orbitals) and an intermediate Pauling electronegativity of approximately $2.55$. Because its valence electrons are close to the nucleus without intervening d-subshell shielding, carbon forms very strong, short covalent bonds to itself ($\text{C--C}$, $\text{C=C}$, $\text{C}\equiv\text{C}$) as well as to hydrogen, oxygen, nitrogen, sulfur, and halogens. Silicon, by contrast, is larger and has weaker $\text{Si--Si}$ bonds that are highly susceptible to oxidation and nucleophilic cleavage.

Answerlist
----------
* Correct. Compact atomic size and intermediate electronegativity provide exceptionally strong, stable covalent bonds.
* Incorrect. Second-row elements such as carbon lack valence d orbitals and strictly adhere to the octet rule (maximum of 8 valence electrons).
* Incorrect. Carbon has an intermediate electronegativity ($2.55$) and does not readily form bare $\text{C}^{4+}$ ions due to an astronomically high ionization energy.
* Incorrect. Carbon forms predominantly covalent bonds through electron sharing, not ionic bonds.
* Incorrect. Carbon bonds readily with a wide variety of heteroatoms (e.g., $\text{H}$, $\text{O}$, $\text{N}$, $\text{S}$, $\text{P}$, halogens), which is a key driver of organic diversity.

Meta-information
================
exname: ch01_sec01_carbon_diversity_understand
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Understand

---

# Question 5

Question
========
How do typical neutral **organic molecular compounds** generally compare to typical **inorganic ionic salts** in terms of chemical bonding and macroscopic physical properties?

Answerlist
----------
* Organic compounds are dominated by covalent bonds and typically exhibit lower melting and boiling points due to intermolecular attractions, whereas inorganic salts consist of continuous ionic lattices with high melting points.
* Organic compounds are held together by high-energy ionic lattices, resulting in significantly higher melting points than inorganic salts.
* Organic compounds are universally noncombustible and insoluble in nonpolar organic solvents, whereas inorganic salts dissolve readily in hydrocarbons.
* Organic compounds dissociate completely into mobile cations and anions in aqueous solution, making them universal electrical conductors in water.
* Organic compounds possess infinite crystalline network bonding in all dimensions, whereas inorganic salts form discrete, volatile gaseous molecules.

Solution
========
Typical organic compounds are composed of discrete molecules held together internally by strong covalent bonds, but interact with neighboring molecules through relatively weak noncovalent intermolecular forces (London dispersion forces, dipole-dipole interactions, and hydrogen bonds). Consequently, organic compounds generally have lower melting points ($< 300\ ^\circ\text{C}$), lower boiling points, and greater volatility than inorganic salts (such as $\text{NaCl}$), which are structured as rigid, high-melting ($> 800\ ^\circ\text{C}$) three-dimensional arrays of oppositely charged ions held by strong electrostatic attractions.

Answerlist
----------
* Correct. Covalent bonding and weaker intermolecular forces impart lower melting/boiling points to typical organic compounds compared to ionic salts.
* Incorrect. Organic compounds are held by covalent bonds in discrete units, not high-energy ionic lattices.
* Incorrect. Most organic compounds are combustible and dissolve preferentially in organic solvents rather than refusing to dissolve in hydrocarbons.
* Incorrect. Most neutral organic compounds (like hydrocarbons, alcohols, ethers) do not dissociate into ions in water and are nonelectrolytes.
* Incorrect. Infinite three-dimensional network lattices describe network solids (like quartz or diamond) or ionic lattices, not typical organic molecular compounds.

Meta-information
================
exname: ch01_sec01_organic_vs_inorganic_props_understand
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Understand

---

# Question 6

Question
========
In organic chemistry, what is a **heteroatom**, and what is its primary chemical significance in an organic molecule?

Answerlist
----------
* Any element present in an organic molecule that is not carbon or hydrogen; heteroatoms often possess unshared lone pairs and differences in electronegativity that create reactive functional sites.
* An unstable radioactive isotope of carbon that spontaneously undergoes beta decay to initiate chain reactions.
* A carbon atom that bears a full formal positive or negative charge in an aliphatic hydrocarbon chain.
* A spectator metal ion that balances the charge of an organic salt without participating in any covalent interaction.
* A hydrogen atom bonded directly to another hydrogen atom rather than to a carbon skeleton.

Solution
========
In organic chemistry, the term "heteroatom" is defined as any atom in an organic compound that is neither carbon nor hydrogen. The most common heteroatoms are oxygen ($\text{O}$), nitrogen ($\text{N}$), sulfur ($\text{S}$), phosphorus ($\text{P}$), and the halogens ($\text{F}$, $\text{Cl}$, $\text{Br}$, $\text{I}$). Because heteroatoms typically have different electronegativities than carbon and frequently possess nonbonding electron pairs (lone pairs), their presence polarizes adjacent covalent bonds and imparts characteristic physical properties and chemical reactivity, defining organic functional groups.

Answerlist
----------
* Correct. Heteroatoms are non-carbon, non-hydrogen atoms whose lone pairs and electronegativity dictate chemical reactivity.
* Incorrect. Radioisotopes of carbon (such as carbon-14) are isotopes, not heteroatoms.
* Incorrect. Charged carbon atoms are called carbocations or carbanions, not heteroatoms.
* Incorrect. Counterions (like $\text{Na}^+$ in sodium acetate) are ionic counterions, not the general definition of heteroatoms within organic molecules.
* Incorrect. Molecular hydrogen ($\text{H}_2$) is an elemental gas, not a heteroatom component of an organic structure.

Meta-information
================
exname: ch01_sec01_heteroatoms_role_understand
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Understand

---

# Question 7

Question
========
A chemist evaluates the following five chemical substances stored in a laboratory:
1. Calcium carbonate ($\text{CaCO}_3$)
2. Sodium chloride ($\text{NaCl}$)
3. Ethanol ($\text{CH}_3\text{CH}_2\text{OH}$)
4. Magnesium sulfate ($\text{MgSO}_4$)
5. Ammonium nitrate ($\text{NH}_4\text{NO}_3$)

Applying standard chemical principles, which substance is unambiguously categorized as an **organic compound**?

Answerlist
----------
* Ethanol ($\text{CH}_3\text{CH}_2\text{OH}$)
* Calcium carbonate ($\text{CaCO}_3$)
* Sodium chloride ($\text{NaCl}$)
* Magnesium sulfate ($\text{MgSO}_4$)
* Ammonium nitrate ($\text{NH}_4\text{NO}_3$)

Solution
========
Ethanol ($\text{CH}_3\text{CH}_2\text{OH}$) is a primary alcohol composed of a carbon-carbon backbone with covalent $\text{C--H}$, $\text{C--C}$, $\text{C--O}$, and $\text{O--H}$ bonds, making it a classic organic compound. In contrast:
- $\text{NaCl}$ and $\text{MgSO}_4$ contain no carbon and are classic inorganic salts.
- $\text{NH}_4\text{NO}_3$ contains nitrogen, hydrogen, and oxygen in an ionic lattice with zero carbon atoms.
- $\text{CaCO}_3$ contains carbon as part of the carbonate polyatomic ion ($\text{CO}_3^{2-}$); however, carbonates, bicarbonates, cyanides, and simple carbon oxides ($\text{CO}$, $\text{CO}_2$) are traditionally classified as inorganic substances due to their mineral origins and ionic lattice structures.

Answerlist
----------
* Correct. Ethanol is an organic molecule with a covalent carbon-carbon framework and functional hydroxyl group.
* Incorrect. Calcium carbonate is an inorganic ionic mineral salt containing the carbonate ion.
* Incorrect. Sodium chloride is an inorganic ionic salt containing no carbon.
* Incorrect. Magnesium sulfate is an inorganic salt containing magnesium, sulfur, and oxygen.
* Incorrect. Ammonium nitrate is an inorganic nitrogenous salt containing no carbon.

Meta-information
================
exname: ch01_sec01_classify_organic_apply
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Apply

---

# Question 8

Question
========
Applying the rule of **carbon tetravalency** (carbon forming four covalent bonds in neutral stable molecules), which molecular formula represents a neutral, open-chain (acyclic), fully saturated hydrocarbon (an alkane) containing five carbon atoms?

Answerlist
----------
* $\text{C}_5\text{H}_{12}$
* $\text{C}_5\text{H}_{10}$
* $\text{C}_5\text{H}_8$
* $\text{C}_5\text{H}_{14}$
* $\text{C}_5\text{H}_6$

Solution
========
In neutral, stable organic molecules, carbon is tetravalent, meaning it forms four covalent bonds. For an open-chain (acyclic) saturated hydrocarbon containing no double bonds, triple bonds, or rings, every carbon atom forms four single bonds. The general mathematical formula for an acyclic alkane is $\text{C}_n\text{H}_{2n+2}$. 

For a five-carbon alkane ($n = 5$):
$$\text{Number of hydrogens} = 2(5) + 2 = 12$$
Thus, pentane has the formula $\text{C}_5\text{H}_{12}$.
- $\text{C}_5\text{H}_{10}$ has one degree of unsaturation (an alkene or cycloalkane).
- $\text{C}_5\text{H}_8$ has two degrees of unsaturation (an alkyne, diene, or bicyclic compound).
- $\text{C}_5\text{H}_{14}$ is impossible because it would require carbon atoms to be pentavalent (forming more than 4 bonds), violating the octet rule.
- $\text{C}_5\text{H}_6$ has three degrees of unsaturation.

Answerlist
----------
* Correct. Pentane follows the formula $\text{C}_n\text{H}_{2n+2}$ with $n=5$, yielding $\text{C}_5\text{H}_{12}$.
* Incorrect. $\text{C}_5\text{H}_{10}$ contains one degree of unsaturation (a double bond or a ring).
* Incorrect. $\text{C}_5\text{H}_8$ contains two degrees of unsaturation.
* Incorrect. $\text{C}_5\text{H}_{14}$ violates carbon tetravalency by exceeding the maximum possible hydrogen count for 5 carbon atoms.
* Incorrect. $\text{C}_5\text{H}_6$ has three degrees of unsaturation.

Meta-information
================
exname: ch01_sec01_tetravalency_alkane_apply
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Apply

---

# Question 9

Question
========
Silicon is located directly beneath carbon in Group 4A (14) and similarly has four valence electrons ($3s^2 3p^2$). When analyzing why biological macromolecules are based on carbon rather than silicon, which thermodynamic comparison of bond energies correctly identifies a primary chemical obstacle to a silicon-based biochemistry in an aqueous, oxygen-rich environment?

Answerlist
----------
* Silicon-oxygen single bonds ($\text{Si--O}$, $\sim 460\ \text{kJ/mol}$) are much stronger than silicon-silicon single bonds ($\text{Si--Si}$, $\sim 226\ \text{kJ/mol}$), causing silicon chains to oxidize irreversibly into intractable, insoluble mineral networks ($\text{SiO}_2$), whereas $\text{C--C}$ ($\sim 348\ \text{kJ/mol}$) and $\text{C--O}$ ($\sim 358\ \text{kJ/mol}$) bond energies are balanced.
* Silicon cannot form covalent bonds with hydrogen because silicon's electronegativity is higher than that of fluorine.
* Silicon-silicon single bonds are substantially stronger than carbon-carbon bonds, preventing enzymes from breaking down silicon polymers.
* Silicon forms multiple bonds ($\text{Si=Si}$ and $\text{Si=O}$) much more readily than carbon, leading to violent spontaneous polymerization into volatile gases.
* Silicon possesses zero accessible bonding orbitals because its $3p$ subshell is fully occupied in the ground state.

Solution
========
Analyzing the bond energetics of carbon versus silicon reveals why life is carbon-based:
1. In carbon chemistry, the bond dissociation energies of a $\text{C--C}$ bond ($\approx 348\ \text{kJ/mol}$) and a $\text{C--O}$ bond ($\approx 358\ \text{kJ/mol}$) are very comparable. This thermodynamic balance allows organic molecules to form stable carbon skeletons while readily undergoing reversible oxidation-reduction reactions, forming carbon dioxide ($\text{CO}_2$) as a soluble, easily diffusible gas that can be exhaled or fixed.
2. In silicon chemistry, the $\text{Si--O}$ bond ($\approx 460\ \text{kJ/mol}$) is vastly stronger and more thermodynamically favored than the $\text{Si--Si}$ bond ($\approx 226\ \text{kJ/mol}$). Consequently, silicon catenated chains in the presence of oxygen or water rapidly and irreversibly oxidize into solid, insoluble silica ($\text{SiO}_2$, quartz) polymers, arresting metabolic exchange.
3. Furthermore, due to the larger radius of the $3p$ orbitals on silicon, effective $p\text{--}p$ $\pi$-orbital overlap is weak, making stable $\text{Si=O}$ and $\text{Si=Si}$ multiple bonds difficult to form under ambient conditions.

Answerlist
----------
* Correct. The disproportionate strength of $\text{Si--O}$ vs $\text{Si--Si}$ bonds drives silicon irreversibly toward solid silica, unlike the balanced $\text{C--C}$ and $\text{C--O}$ energetics in carbon.
* Incorrect. Silicon's Pauling electronegativity is $\sim 1.90$, far lower than fluorine ($3.98$), and it readily forms covalent $\text{Si--H}$ bonds (silanes).
* Incorrect. $\text{Si--Si}$ bonds ($\sim 226\ \text{kJ/mol}$) are significantly weaker than $\text{C--C}$ bonds ($\sim 348\ \text{kJ/mol}$), not stronger.
* Incorrect. Due to poor $3p\text{--}3p$ $\pi$ orbital overlap, silicon forms multiple bonds much less readily than carbon.
* Incorrect. Silicon has an electron configuration of $[\text{Ne}]\, 3s^2 3p^2$, meaning its $3p$ subshell is only partially occupied (two electrons with room for four more), enabling $sp^3$ hybridization and tetravalent bonding.

Meta-information
================
exname: ch01_sec01_carbon_vs_silicon_analyze
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Analyze

---

# Question 10

Question
========
Consider the following statements regarding the historical development, definition, and scientific scope of **organic chemistry**:

1. Friedrich Wöhler's 1828 conversion of ammonium cyanate into urea demonstrated that organic compounds obey the same chemical and physical principles as inorganic compounds.
2. In modern chemical taxonomy, any compound containing carbon is automatically classified as organic, including elemental diamond, graphite, calcium carbonate, and carbon dioxide.
3. The ability of carbon to form four stable covalent bonds and link to other carbon atoms in chains and rings (catenation) accounts for the existence of tens of millions of distinct organic structures.
4. Synthesized pharmaceutical drugs such as aspirin, ibuprofen, and penicillin are classified as organic molecules even though they are prepared industrially in chemical factories.
5. All organic compounds must contain at least one heteroatom (such as oxygen, nitrogen, or halogen) in addition to carbon and hydrogen to be considered true organic molecules.

Which combination of statements represents a completely accurate evaluation of the principles defining organic chemistry?

Answerlist
----------
* Statements 1, 3, and 4 only
* Statements 1, 2, and 5 only
* Statements 2, 3, and 4 only
* Statements 1, 2, 3, and 4
* Statements 3, 4, and 5 only

Solution
========
Evaluating each statement systematically:
- **Statement 1 is TRUE:** Wöhler's synthesis of urea from ammonium cyanate refuted the doctrine of vitalism, proving that organic molecules do not possess an exclusive "vital force" and obey standard chemical and physical laws.
- **Statement 2 is FALSE:** Not all carbon-containing substances are classified as organic. Carbon allotropes (diamond, graphite, graphene), carbon oxides ($\text{CO}$, $\text{CO}_2$), carbonates (e.g., $\text{CaCO}_3$, $\text{NaHCO}_3$), and cyanides are historically and conventionally categorized as inorganic compounds.
- **Statement 3 is TRUE:** Carbon tetravalency and catenation are the two foundational structural principles explaining why millions of distinct organic compounds exist.
- **Statement 4 is TRUE:** Organic chemistry encompasses all carbon-based molecular compounds regardless of origin; laboratory-synthesized pharmaceuticals are quintessential organic molecules.
- **Statement 5 is FALSE:** Pure hydrocarbons (such as methane $\text{CH}_4$, ethylene $\text{C}_2\text{H}_4$, and benzene $\text{C}_6\text{H}_6$) contain only carbon and hydrogen and are quintessential organic compounds; heteroatoms are not required for a molecule to be classified as organic.

Therefore, the valid and accurate combination is **Statements 1, 3, and 4 only**.

Answerlist
----------
* Correct. Statements 1, 3, and 4 provide an accurate assessment of organic chemistry's origins, structural basis, and scope.
* Incorrect. Statement 2 is false (simple oxides and carbonates are inorganic) and Statement 5 is false (hydrocarbons contain no heteroatoms).
* Incorrect. Statement 2 is false; diamond and carbonates are classified as inorganic.
* Incorrect. Statement 2 is false, making any option including Statement 2 incorrect.
* Incorrect. Statement 5 is false; hydrocarbons are organic molecules despite having no heteroatoms.

Meta-information
================
exname: ch01_sec01_vitalism_scope_evaluation_evaluate
extype: schoice
exsolution: 10000
exshuffle: true
exsection: Chapter 1/Section 1 Introduction / General
extopic: Organic chemistry
exextra[bloom]: Evaluate
"""


def build_question_bank_content() -> str:
    """Return the authoritative 10-question Markdown text for Chapter 1, Section 1."""
    return QUESTION_BANK_MARKDOWN


def generate_questions(target_path: Optional[Union[str, Path]] = None) -> Path:
    """Generate and write the Chapter 1, Section 1 question bank file.

    Args:
        target_path: Destination path. Defaults to DEFAULT_OUTPUT_PATH.

    Returns:
        Path to the written question bank file.
    """
    dest = Path(target_path) if target_path is not None else DEFAULT_OUTPUT_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = build_question_bank_content()
    dest.write_text(content, encoding="utf-8")
    return dest


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint supporting --output / -o flag."""
    parser = argparse.ArgumentParser(
        description="Generate CHEM311 McMurry 10e Ch 1 Sec 1 R/exams Question Bank."
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Target output markdown path (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    output_path = generate_questions(args.output)
    print(f"Successfully generated question bank at: {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
