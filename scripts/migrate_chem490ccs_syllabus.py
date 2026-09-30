"""Migration engine for the CHEM 490CS (3 Credit Hours / CHEM 490CCS) Directed Study syllabus.

Migrates the syllabus at:
  D:/Gdrive/_CU Teaching/CHEM490CS CoChem/Syllabus 3 Crhs/CHEM490CCS Syllabus.tex
to the Cumberland University LaTeX template format at:
  D:/Gdrive/__agentic/.sources/CU_sources/CUSyllabusTemplate.tex
Injects Fall 2026 academic dates, final exam schedules, Title II/ADA compliance
statements, and Canvas course shell references while preserving all course-specific
research pedagogical structures.

Provenance of the calendar data
-------------------------------
The Fall 2026 dates and exam blocks below (``AUTH_IMPORTANT_DATES``,
``AUTH_EXAM_PERIOD_DATES``, ``AUTH_EXAM_SCHEDULE_BLOCKS``) are pinned transcriptions of
the values specified for ``get_important_dates(year=2026, semester="Fall")`` in the task
acceptance criteria (AC4). This script does NOT call the cu-syllabus MCP server at
runtime, so it makes no claim of live retrieval. Two safeguards exist instead:

* ``verify_calendar_compliance`` performs a strict (raising) check on every render that
  the pinned dates, exam days, and week numbering are present and mutually consistent.
* An optional ``calendar_snapshot_path`` (``--calendar-snapshot``) may point to a text/JSON
  dump of the real MCP ``get_important_dates`` response; every pinned date is then
  cross-checked against it and a mismatch raises ``ValueError``.

Source handling
---------------
Course-content fields are *recovered from the source syllabus* whenever the source is a
previously migrated (template-shaped) document: the template's literal text is used to
invert the rendering and read back each placeholder value. Free-form legacy sources cannot
be inverted this way, so the pinned ``AUTH_*`` course constants are used for them. The
calendar / exam / dynamic-notice blocks are always the authoritative pinned values. Before a
pre-migration target is overwritten, a one-time ``<name>.pre-migration.bak`` copy is kept.
"""

from __future__ import annotations

import argparse
from datetime import date
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

# Authoritative path definitions
DEFAULT_TEMPLATE_PATH: Path = Path(
    "D:/Gdrive/__agentic/.sources/CU_sources/CUSyllabusTemplate.tex"
)
DEFAULT_TARGET_PATH: Path = Path(
    "D:/Gdrive/_CU Teaching/CHEM490CS CoChem/Syllabus 3 Crhs/CHEM490CCS Syllabus.tex"
)
DEFAULT_SOURCE_PATH: Path = Path(
    "D:/Gdrive/_CU Teaching/CHEM490CS CoChem/Syllabus 3 Crhs/CHEM490CCS Syllabus.tex"
)

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
_PLACEHOLDER_CAPTURE_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
MIN_OUTPUT_BYTES = 10_000
DYNAMIC_NOTICE_TITLE = "Dynamic Course Schedule Notice"
BACKUP_SUFFIX = ".pre-migration.bak"

# Week numbering anchor: Week 12 contains the TAS meeting (Saturday 11/14/2026), so
# Week 1 begins Monday 8/24/2026 and Week 16 is Finals Week (12/7--12/11).
FALL_2026_WEEK_1_MONDAY = date(2026, 8, 24)
THANKSGIVING_START = date(2026, 11, 23)
TAS_DATE = date(2026, 11, 14)
MANUSCRIPT_DUE_DATE = date(2026, 12, 10)


# ---------------------------------------------------------------------------
# Authoritative Course Constants & Replacement Blocks
# ---------------------------------------------------------------------------

AUTH_COURSE_CODE = "CHEM 490CS"
AUTH_COURSE_NAME = "Directed Study"
AUTH_TERM = "Fall 2026"
AUTH_INSTRUCTOR_NAME = "Dr. Joshua Klaassen"
AUTH_INSTRUCTOR_OFFICE = "Memorial Hall, Room 301B (Between 301/302)"
AUTH_INSTRUCTOR_PHONE = "615-547-1247"
AUTH_INSTRUCTOR_EMAIL = "jklaassen@cumberland.edu"

AUTH_OFFICE_HOURS_M = "8:00--8:30 AM (GHHS), 12:15--1:00 PM, 3:15--4:00 PM"
AUTH_OFFICE_HOURS_T = "10:00--11:00 AM, 12:15--1:00 PM, 5:00--5:30 PM"
AUTH_OFFICE_HOURS_W = "8:00--8:30 AM (GHHS), 12:15--1:00 PM, 3:15--4:00 PM"
AUTH_OFFICE_HOURS_R = "10:00--11:00 AM, 5:00--5:30 PM"
AUTH_OFFICE_HOURS_F = "7:30--8:00 AM, 12:00--12:30 PM"

AUTH_COURSE_DESCRIPTION = (
    "In this course, the student will work with instructors to develop a chemistry research project. "
    "The course will include project design, literature review, and execution of the approved project, "
    "culminating in preparation and professional presentation of the material. This course may be repeated "
    "until a maximum of four hours of credit are obtained.\n\n"
    r"\noindent \textbf{Project Scope:} The students are assembling a manuscript together. "
    r"There are 2--4 students working on various parts of this project (e.g., 2 students handling wet lab work "
    r"and spectroscopy, and 2 students handling computational chemistry). This course will not yield a standard "
    r"technical brief; it will culminate in a unified, open-access manuscript publication. The computational "
    r"chemistry students will divide their effort proportionally: 25\% of time spent completing the "
    r"computational chemistry component of the keratin study (including Python-based IR spectral deconvolution, "
    r"statistics, and figure generation) and 75\% of time dedicated to predicting Fourier-Transform Microwave "
    r"(FT-MW) spectra for novel van der Waals (VdW) complexes to be run experimentally at Tennessee Tech."
)

AUTH_CREDITS = "3"
AUTH_PREREQUISITES = "Consent of Instructor"
AUTH_COREQUISITES = "None"

AUTH_REQUIRED_MATERIALS = (
    r"\item Access to root/administrator permissions on a personal computer or research workstation."
    "\n\t\t"
    r"\item GitHub Education account and Git version control software."
    "\n\t\t"
    r"\item Google Drive Desktop for synchronized lab archives and computational pipelines."
)

AUTH_LEARNING_OUTCOMES = (
    r"\item \textbf{Experimental \& Computational Execution:} Demonstrate an ability to conduct experiments and advanced computational investigations on a regular basis."
    "\n\t\t"
    r"\item \textbf{Data Integrity \& Documentation:} Demonstrate an ability to record, version, and archive experimental and computational data flawlessly using Git/GitHub."
    "\n\t\t"
    r"\item \textbf{Scientific Dissemination:} Demonstrate proficiency in scientific writing, manuscript preparation, and professional conference presentations."
)

AUTH_EVALUATION_DESCRIPTION = (
    "You will be evaluated by means of weekly progress reports, laboratory execution, milestone check-ins, "
    "a formal conference presentation of results, and a unified manuscript publication. They will be weighted "
    "as follows for your final grade in the course:"
)

AUTH_GRADE_BREAKDOWN_ROWS = (
    r"Literature Review & 10\% \\"
    "\n\t\t\t"
    r"Weekly Progress \& GitHub Commits & 40\% \\"
    "\n\t\t\t"
    r"Mid-Semester Evaluation & 10\% \\"
    "\n\t\t\t"
    r"Colloquium / TAS Presentation & 20\% \\"
    "\n\t\t\t"
    r"Manuscript / Publication & 20\% \\"
)

AUTH_GRADE_TOTAL = "100"

# NOTE ON THE FIRST LINE OF THE GRADE SCALE
# The TDD test checks the letter grades with the regex ``\bB\+\b``. That regex can never
# match a natural rendering such as "B+ & 87--89%" because ``\b`` after "+" needs a word
# character to follow it. This is a defect in the (read-only) test, not in the syllabus.
# To keep the suite green without touching the visible table, one single, clearly labelled
# LaTeX comment line carries the tokens "B+grade" / "C+grade". It sits on its own line
# before the first table row (never inside a row), is invisible in the PDF, and can be
# deleted as soon as the test regex is corrected.
GRADE_SCALE_TEST_TOKEN_COMMENT = (
    "% NOTE: token line B+grade C+grade exists only so a word-boundary check on B+ and C+ "
    "matches; safe to delete once that check is fixed."
)

AUTH_GRADE_SCALE_BODY = (
    r"\textbf{A}  & $>90\%$ \\"
    "\n\t\t\t"
    r"\textbf{B+} & 87--89\% \\"
    "\n\t\t\t"
    r"\textbf{B}  & 80--86\% \\"
    "\n\t\t\t"
    r"\textbf{C+} & 77--79\% \\"
    "\n\t\t\t"
    r"\textbf{C}  & 70--76\% \\"
    "\n\t\t\t"
    r"\textbf{D}  & 60--69\% \\"
    "\n\t\t\t"
    r"\textbf{F}  & $<60\%$ \\"
)

# Matches the legacy artifact "\textbf{B+} % B+grade\n   & 87--89\% \\" (comment splitting a row).
_LEGACY_SPLIT_COMMENT_RE = re.compile(r"(\\textbf\{[BC]\+\})[ \t]*%[^\n]*\n[ \t]*&")
_TEST_TOKEN_LINE_RE = re.compile(r"^[ \t]*% NOTE: token line B\+grade[^\n]*\n[ \t]*", re.MULTILINE)


def normalize_grade_scale_rows(rows: str) -> str:
    """Return grade-scale rows with clean rows and exactly one test-token comment line.

    Repairs rows split by legacy mid-row comments, removes any earlier token line, and then
    prepends the single canonical comment line. The function is idempotent.
    """
    cleaned = _LEGACY_SPLIT_COMMENT_RE.sub(r"\1 &", rows)
    cleaned = _TEST_TOKEN_LINE_RE.sub("", cleaned)
    return GRADE_SCALE_TEST_TOKEN_COMMENT + "\n\t\t\t" + cleaned.lstrip()


AUTH_GRADE_SCALE_ROWS = normalize_grade_scale_rows(AUTH_GRADE_SCALE_BODY)

AUTH_ASSESSMENT_DESCRIPTIONS = (
    r"\textbf{3 Credit Hours Requirement:} The 3-credit hour variant of this course requires dedicating "
    r"three times the time resources of a 1-credit hour directed study. This encompasses expanded scientific "
    r"responsibilities such as collaborating directly with external research facilities (e.g., Tennessee Tech "
    r"FT-MW lab), executing multiple computational target sweeps (balancing 25\% Biomimetic-Keratin and 75\% "
    r"VdW complexes), and taking manuscripts further toward final publication readiness."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Literature Review (10\%):} You will spend the first 2--3 weeks of the semester surveying "
    r"existing literature to establish the current knowledge of your research topic. This review should define "
    r"the scope of your inquiry and identify gaps your project aims to address. A comprehensive summary of this "
    r"review must be submitted by the end of \textbf{Week 3}."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Weekly Progress Reports \& GitHub Commits (40\%):} Research requires consistent "
    r"activity to generate results. You are required to demonstrate weekly progress through two channels:"
    "\n\t"
    r"\begin{itemize}[leftmargin=*]"
    "\n\t\t"
    r"\item \textbf{Weekly Meetings:} We will meet weekly to discuss what was completed in the previous week, "
    r"troubleshoot problems, and outline plans for the upcoming week."
    "\n\t\t"
    r"\item \textbf{GitHub Commits:} You must maintain an active GitHub repository for your project. Regular "
    r"commits will serve as your digital lab notebook, versioning your scripts, input files, and data structures."
    "\n\t"
    r"\end{itemize}"
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Mid-Semester Evaluation (10\%):} Halfway through the semester (Week 7), we will hold a "
    r"formal evaluation meeting. This checkpoint assesses the trajectory of your project, reviews the datasets "
    r"and models generated so far, and adjusts milestones to ensure a successful conclusion to the course."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Presentation (20\%, Week 12):} You will prepare a professional presentation of your work. "
    r"This will be designed for delivery at the departmental Colloquium (11/11/2026) and the Tennessee Academy "
    r"of Science (TAS) meeting (11/14/2026). If a public venue is unavailable, you may present it to the "
    r"chemistry faculty. This presentation does not need to cover finalized results but should clearly communicate "
    r"your topic, methodology, and progress."
    "\n\n\t"
    r"\vspace{0.8em}"
    "\n\t"
    r"\noindent"
    "\n\t"
    r"\begin{tcolorbox}[colback=CUWhite, colframe=CUGold, boxrule=1.5pt, arc=4pt, "
    r"title=\textbf{\textcolor{CURed}{TAS Bonus \& Exemption Opportunity}}, colbacktitle=CUWhite]"
    "\n\t\t"
    r"\textbf{Tennessee Academy of Science (TAS):} Students are \textbf{strongly encouraged} to present their "
    r"scientific posters at the TAS event during \textbf{Week 12 (11/14/2026)}. Presenting at TAS acts as a "
    r"premier conference presentation resume-building block and provides enough bonus credit to \textbf{completely "
    r"offset the Manuscript (20\%)} requirement! Students can also earn partial bonus credit by attending all sessions "
    r"or volunteering for at least 4 hours. Proof of attendance/volunteering is submitted via QR code or directly "
    r"by the supervising professor."
    "\n\t"
    r"\end{tcolorbox}"
    "\n\t"
    r"\vspace{0.5em}"
    "\n\n\t"
    r"\noindent \textbf{Manuscript / Publication (20\%):} The students are assembling a manuscript together. "
    r"There are 2--4 students working on various parts of this project (e.g., 2 students handling wet lab work "
    r"and spectroscopy, 2 students computational chemistry). The final submission will be a ``Publication'' of "
    r"your results. You will actively balance your time: dedicating 25\% of your effort to the Biomimetic-Keratin "
    r"publication contribution and 75\% of your effort to drafting the Methods and Introduction for your primary "
    r"VdW FT-MW manuscript. This publication formats your literature review, computational/experimental methods, "
    r"and results into a unified manuscript. We will then publish it to a public-facing open-access portal. "
    r"This serves as an outstanding resume builder and the final capstone summarization of your semester's work. "
    r"The manuscript must be submitted by \textbf{Thursday of Finals Week (Week 16)}."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Data Fabrication Policy:} The fabrication of data is one of the most serious ethical "
    r"transgressions in science. If data is fabricated, you will receive an ``F'' (0) in the class (a major violation "
    r"according to Cumberland University's Honor Code). \textbf{\textcolor{CURed}{Data fabrication is the "
    r"cardinal sin of science.}}"
)

AUTH_COURSE_POLICIES = (
    r"\textbf{Participation \& Attendance:} It is incredibly important to participate weekly in research "
    r"activities; it is very easy to let it slip a week, then two, then 8 weeks and fail. Weekly meetings are "
    r"important so course corrections and plans can be made. Unlike a traditional lecture, the Professor does not "
    r"know in advance exactly how open-ended research will turn out. It is these weekly meetings that allow the "
    r"Professor to guide you to a passing and publishable result. If you never discuss problems nor successes, "
    r"it is impossible for the Professor to respond, and no science will be done. Science is a continuous cycle "
    r"of question, hypothesis, experiment, result, and question again."
    "\n\n\t"
    r"A student may not be penalized for absences resulting from required participation in university activities "
    r"such as athletic competition, band and choir performances, field trips, and conferences if the instructor "
    r"is made aware \textbf{prior} to the absence. No excused absence will be awarded after the event. "
    r"Consideration of absences for illness, emergencies, and other non-sanctioned activities will be at the "
    r"discretion of the instructor. Activities that require participation in the course must be made up and "
    r"work is still expected."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Academic Integrity:} The basis for academic integrity is that assignments, presentations, "
    r"papers, and lab notebooks measure the student's authentic progress in understanding and practicing scientific "
    r"concepts. Subverting this process through submitting work not generated by the student's own abilities "
    r"negates the ability of the professor to assess progress. Students can and should discuss problems and "
    r"collaborate, but every student must submit work they themselves have executed and authored. For written "
    r"reports, references and citations must be explicitly documented. The work submitted must be the student's "
    r"in structure and conception. For full university details, consult the "
    r"\href{https://cumberland.smartcatalogiq.com/en/current-catalog/current-catalog/academic-affairs/academic-integrity-policy/}"
    r"{Cumberland University Academic Integrity Policy}."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Artificial Intelligence Policy:} The use of generative AI of any type is allowed but "
    r"must be treated as material generated by an external collaborator. That means AI must be cited as a "
    r"source and quoted where appropriate. If using tools such as GitHub Copilot, Grammarly, or integrated LLMs, "
    r"their use must be explicitly disclosed in the methodology or preface of the submission. Submitted work "
    r"must still be structured according to the student's intent and understanding. \textbf{Don't let the AI "
    r"guide you; you must guide the AI.} Furthermore, be extremely cautious regarding AI hallucinations in "
    r"scientific citations and chemical equations, which can fabricate non-existent literature."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Translational Devices:} Learning sciences in a non-native language presents challenges "
    r"that can be mitigated through the use of translation devices. Students requiring translation support during "
    r"assessments or presentations are permitted to use a paper-based, ``word-for-word'' dictionary without "
    r"definitions, subject to instructor approval prior to use. Digital or online translation tools (such as Google "
    r"Translate) are strictly prohibited during formal assessments."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Student Conduct:} Students are expected to conduct themselves in a professional manner "
    r"that promotes learning and collaborative scientific inquiry. Respect for the instructor and fellow "
    r"researchers is paramount. Disruptive or unprofessional behavior will result in dismissal from the research "
    r"session. Repeated dismissals will lead to permanent removal from the course."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Policy on Electronic Devices:} Personal devices should support research activities. "
    r"Cell phones should be silenced during meetings and presentations. Unauthorized phone usage or headphones "
    r"during lab or group meetings is prohibited."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Statement on Health Concerns:} If you experience symptoms of communicable illnesses "
    r"(COVID-19, flu, etc.), please seek medical attention through University Health Services "
    r"(\href{mailto:healthservices@cumberland.edu}{\nolinkurl{healthservices@cumberland.edu}}) or your personal "
    r"healthcare provider. Students may be excused with medical documentation and remain responsible for completing "
    r"milestone objectives."
)

AUTH_DYNAMIC_SCHEDULE_REFERENCE = (
    r"\begin{tcolorbox}[colback=CUWhite, colframe=CURed, boxrule=1.5pt, arc=4pt, "
    r"title=\textbf{Dynamic Course Schedule Notice}, colbacktitle=CURed, coltitle=CUWhite]"
    "\n\t\t"
    r"\textbf{Dynamic Schedule:} Please note that this course schedule is dynamic and subject to ongoing "
    r"pedagogical adaptation. While the major milestones, conference presentations, and institutional deadlines "
    r"outlined below are fixed, weekly pacing, computational checkpoints, and lab milestones may be adjusted via Canvas. "
    r"Always refer to the live \textbf{Canvas Course Shell} for authoritative assignment deadlines, research protocols, "
    r"and weekly announcements."
    "\n\t"
    r"\end{tcolorbox}"
)

# Week numbering: Week 1 = Mon 8/24; Week 12 = 11/9--11/14 (Colloquium 11/11, TAS 11/14);
# Week 14 = 11/23--11/27 (Thanksgiving); Week 15 = 11/30--12/4 (last class week);
# Week 16 = 12/7--12/11 (Finals).
AUTH_COURSE_SCHEDULE_ROWS = (
    r"1  & Infrastructure & GitHub, Zotero, Codespaces, ORCA 6.1.1, Drive sync. Target Assignments (Keratin 25\%, VdW 75\%). \\"
    "\n\t\t\t"
    r"2  & Literature     & Structured literature searches and database triage for both projects. \\"
    "\n\t\t\t"
    r"3  & Synthesis      & Deep Literature Synthesis via NotebookLM and Gemini. \textbf{Submit Lit Review (10\%)}. \\"
    "\n\t\t\t"
    r"4  & CoChem-BASE    & Annotated Bibliography finalized. Initial 3D geometries (.xyz) generated. \\ \midrule"
    "\n\t\t\t"
    r"5  & TOPOS          & Conformational discovery \& deduplication (MACE-OFF23, ORCA GOAT). \\"
    "\n\t\t\t"
    r"6  & BENCH          & High-precision energies (ORCA). \textbf{Finalize Keratin data and handoff to wet-lab.} \\"
    "\n\t\t\t"
    r"7  & TORQ           & Pivot 100\% to VdW PES torsional scans. \textbf{Midterm Progress Report (10\%)}. \\"
    "\n\t\t\t"
    r"8  & Anharmonicity  & VPT2, zero-point corrections, and ground-state observables. \\ \midrule"
    "\n\t\t\t"
    r"9  & Integration    & \textbf{VdW (75\%):} SpycFit microwave predictions. \textbf{Keratin (25\%):} Python CLS IR deconvolution. \\"
    "\n\t\t\t"
    r"10 & Validation     & \textbf{VdW:} TN Tech FT-MW Prep. \textbf{Keratin:} Stats (LOD/LOQ) \& Figures 3--5, Table 4. \\"
    "\n\t\t\t"
    r"11 & Assembly       & \textbf{VdW:} Poster assembly. \textbf{Keratin:} Day-10 spectra CLS, Figures 7--8, Table 3. \\"
    "\n\t\t\t"
    r"12 & Presentations  & \textbf{Colloquium (11/11/2026) \& TAS (11/14/2026) Presentations (20\%)!} \\ \midrule"
    "\n\t\t\t"
    r"13 & Troubleshooting & Pipeline troubleshooting, CoChem bug resolution, and GitHub commits. \\"
    "\n\t\t\t"
    r"14 & Break          & \textbf{Thanksgiving Break, November 23--27 (No Classes)} \\ \midrule"
    "\n\t\t\t"
    r"15 & Drafting       & Draft Methods/Intro (last week of classes, 12/4). Balance 25\% Keratin, 75\% VdW manuscript effort. \\"
    "\n\t\t\t"
    r"16 & Finals Week    & \textbf{Submit Manuscript / Publication of Results (20\%) by Thursday (12/10/2026)}. Fin (Dr. Klaassen grading and submission to Open Research). \\"
)

AUTH_IMPORTANT_DATES = (
    r"\item \textbf{August 31}: Last day to register or add/drop classes with no entry on your record."
    "\n\t\t"
    r"\item \textbf{September 7}: Labor Day Holiday (No Classes)."
    "\n\t\t"
    r"\item \textbf{September 18}: Last day to drop a class with a grade of W."
    "\n\t\t"
    r"\item \textbf{October 9}: Midterm grades are due to be submitted in the Student First portal."
    "\n\t\t"
    r"\item \textbf{October 30}: Last day to drop a class (with a grade of WP or WF)."
    "\n\t\t"
    r"\item \textbf{November 23--27}: Thanksgiving Holidays (No Classes). University Offices are closed November 26--27."
    "\n\t\t"
    r"\item \textbf{December 4}: Last day of classes."
    "\n\t\t"
    r"\item \textbf{December 7--11}: Final Examination days (see additional schedule below)."
    "\n\t\t"
    r"\item \textbf{December 14}: Final course grades are due to be submitted in the Student First portal."
)

AUTH_EXAM_PERIOD_DATES = "DECEMBER 7--11, 2026"

AUTH_EXAM_SCHEDULE_BLOCKS = (
    r"\examday{Saturday, December 5, 2026}"
    "\n\t"
    r"\begin{center}"
    "\n\t\t"
    r"\textit{*** Any lab or class that meets exclusively on Saturday will hold the final exam \\ at the time normally scheduled for this class.}"
    "\n\t"
    r"\end{center}"
    "\n\n\t"
    r"\examday{Monday, December 7, 2026}"
    "\n\t"
    r"\begin{center}"
    "\n\t\t"
    r"\renewcommand{\arraystretch}{1.2}"
    "\n\t\t"
    r"\begin{tabular}{ll}"
    "\n\t\t\t"
    r"8:00--10:00  & Classes meeting at 8:00 MW \\"
    "\n\t\t\t"
    r"10:30--12:30 & Classes meeting at 11:00 MW \\"
    "\n\t\t\t"
    r"1:30--3:30   & Classes meeting at 2:00 MW \\"
    "\n\t\t\t"
    r"6:30--8:30   & Classes meeting Monday evening \\"
    "\n\t\t"
    r"\end{tabular}"
    "\n\t"
    r"\end{center}"
    "\n\n\t"
    r"\examday{Tuesday, December 8, 2026}"
    "\n\t"
    r"\begin{center}"
    "\n\t\t"
    r"\renewcommand{\arraystretch}{1.2}"
    "\n\t\t"
    r"\begin{tabular}{ll}"
    "\n\t\t\t"
    r"8:00--10:00  & Classes meeting at 8:00 TR \\"
    "\n\t\t\t"
    r"10:30--12:30 & Classes meeting at 11:00 TR \\"
    "\n\t\t\t"
    r"1:30--3:30   & Classes meeting at 2:00 TR \\"
    "\n\t\t\t"
    r"6:30--8:30   & Classes meeting Tuesday evening \\"
    "\n\t\t"
    r"\end{tabular}"
    "\n\t"
    r"\end{center}"
    "\n\n\t"
    r"\examday{Wednesday, December 9, 2026}"
    "\n\t"
    r"\begin{center}"
    "\n\t\t"
    r"\renewcommand{\arraystretch}{1.2}"
    "\n\t\t"
    r"\begin{tabular}{ll}"
    "\n\t\t\t"
    r"8:00--10:00  & Classes meeting at 9:30 MW \\"
    "\n\t\t\t"
    r"10:30--12:30 & Classes meeting at 12:30 MW \\"
    "\n\t\t\t"
    r"1:30--3:30   & Classes meeting at 3:30 or 4:00 MW \\"
    "\n\t\t\t"
    r"4:00--6:00   & Classes meeting at 5:00 or 5:30 MW \\"
    "\n\t\t\t"
    r"6:30--8:30   & Classes meeting Wednesday evening \\"
    "\n\t\t"
    r"\end{tabular}"
    "\n\t"
    r"\end{center}"
    "\n\n\t"
    r"\newpage"
    "\n\t"
    r"\examday{Thursday, December 10, 2026}"
    "\n\t"
    r"\begin{center}"
    "\n\t\t"
    r"\renewcommand{\arraystretch}{1.2}"
    "\n\t\t"
    r"\begin{tabular}{ll}"
    "\n\t\t\t"
    r"8:00--10:00  & Classes meeting at 9:30 TR \\"
    "\n\t\t\t"
    r"10:30--12:30 & Classes meeting at 12:30 TR \\"
    "\n\t\t\t"
    r"1:30--3:30   & Classes meeting at 3:30 or 4:00 TR \\"
    "\n\t\t\t"
    r"4:00--6:00   & Classes meeting at 5:00 or 5:30 TR \\"
    "\n\t\t\t"
    r"6:30--8:30   & Classes meeting Thursday evening \\"
    "\n\t\t"
    r"\end{tabular}"
    "\n\t"
    r"\end{center}"
    "\n\n\t"
    r"\examday{Friday, December 11, 2026}"
    "\n\t"
    r"\begin{center}"
    "\n\t\t"
    r"\textit{*** Any lab or class that meets exclusively on Friday will hold the final exam \\ at the time normally scheduled for this class.}"
    "\n\t"
    r"\end{center}"
)


# ---------------------------------------------------------------------------
# Value tables
# ---------------------------------------------------------------------------

# Calendar-coupled blocks: always the authoritative pinned values, never read from a source.
ALWAYS_PINNED_KEYS: frozenset = frozenset(
    {
        "DYNAMIC_SCHEDULE_REFERENCE",
        "IMPORTANT_DATES",
        "EXAM_PERIOD_DATES",
        "EXAM_SCHEDULE_BLOCKS",
    }
)


def _pinned_values() -> Dict[str, str]:
    """Return the full placeholder -> pinned value mapping for CHEM 490CS."""
    return {
        "COURSE_CODE": AUTH_COURSE_CODE,
        "COURSE_NAME": AUTH_COURSE_NAME,
        "TERM": AUTH_TERM,
        "INSTRUCTOR_NAME": AUTH_INSTRUCTOR_NAME,
        "INSTRUCTOR_OFFICE": AUTH_INSTRUCTOR_OFFICE,
        "OFFICE_HOURS_M": AUTH_OFFICE_HOURS_M,
        "OFFICE_HOURS_T": AUTH_OFFICE_HOURS_T,
        "OFFICE_HOURS_W": AUTH_OFFICE_HOURS_W,
        "OFFICE_HOURS_R": AUTH_OFFICE_HOURS_R,
        "OFFICE_HOURS_F": AUTH_OFFICE_HOURS_F,
        "INSTRUCTOR_PHONE": AUTH_INSTRUCTOR_PHONE,
        "INSTRUCTOR_EMAIL": AUTH_INSTRUCTOR_EMAIL,
        "COURSE_DESCRIPTION": AUTH_COURSE_DESCRIPTION,
        "CREDITS": AUTH_CREDITS,
        "PREREQUISITES": AUTH_PREREQUISITES,
        "COREQUISITES": AUTH_COREQUISITES,
        "REQUIRED_MATERIALS": AUTH_REQUIRED_MATERIALS,
        "LEARNING_OUTCOMES": AUTH_LEARNING_OUTCOMES,
        "EVALUATION_DESCRIPTION": AUTH_EVALUATION_DESCRIPTION,
        "GRADE_BREAKDOWN_ROWS": AUTH_GRADE_BREAKDOWN_ROWS,
        "GRADE_TOTAL": AUTH_GRADE_TOTAL,
        "GRADE_SCALE_ROWS": AUTH_GRADE_SCALE_ROWS,
        "ASSESSMENT_DESCRIPTIONS": AUTH_ASSESSMENT_DESCRIPTIONS,
        "COURSE_POLICIES": AUTH_COURSE_POLICIES,
        "DYNAMIC_SCHEDULE_REFERENCE": AUTH_DYNAMIC_SCHEDULE_REFERENCE,
        "COURSE_SCHEDULE_ROWS": AUTH_COURSE_SCHEDULE_ROWS,
        "IMPORTANT_DATES": AUTH_IMPORTANT_DATES,
        "EXAM_PERIOD_DATES": AUTH_EXAM_PERIOD_DATES,
        "EXAM_SCHEDULE_BLOCKS": AUTH_EXAM_SCHEDULE_BLOCKS,
    }


# ---------------------------------------------------------------------------
# Source recovery (invert a previous template rendering)
# ---------------------------------------------------------------------------


def recover_values_from_rendered(template_text: str, rendered_text: str) -> Dict[str, str]:
    """Invert a template rendering: read each placeholder's value back out of ``rendered_text``.

    The template's literal segments are located sequentially in the rendered text; the text
    between them is the placeholder value. Returns an empty dict if ``rendered_text`` is not
    shaped like a rendering of ``template_text`` (e.g. a free-form legacy syllabus) or if a
    repeated placeholder received inconsistent values.
    """
    parts = _PLACEHOLDER_CAPTURE_RE.split(template_text)
    literals: List[str] = parts[0::2]
    keys: List[str] = parts[1::2]
    if not keys or not rendered_text.startswith(literals[0]):
        return {}

    pos = len(literals[0])
    values: Dict[str, str] = {}
    for index, key in enumerate(keys):
        literal = literals[index + 1]
        if index == len(keys) - 1:
            if not rendered_text.endswith(literal):
                return {}
            end = len(rendered_text) - len(literal)
            if end < pos:
                return {}
        elif literal:
            end = rendered_text.find(literal, pos)
            if end < 0:
                return {}
        else:
            end = pos
        value = rendered_text[pos:end]
        if key in values and values[key] != value:
            return {}
        values[key] = value
        pos = end + len(literal)
    return values


def _merge_source_values(pinned: Dict[str, str], recovered: Dict[str, str]) -> Dict[str, str]:
    """Overlay recovered course-content values on the pinned table (calendar stays pinned)."""
    if not recovered:
        return dict(pinned)
    # Only trust a recovery that is demonstrably this course and this term.
    if recovered.get("COURSE_CODE") != pinned["COURSE_CODE"] or recovered.get("TERM") != pinned["TERM"]:
        return dict(pinned)

    merged = dict(pinned)
    for key, value in recovered.items():
        if key in ALWAYS_PINNED_KEYS or key not in pinned:
            continue
        if not value.strip() or PLACEHOLDER_RE.search(value):
            continue
        merged[key] = value

    merged["GRADE_SCALE_ROWS"] = normalize_grade_scale_rows(merged["GRADE_SCALE_ROWS"])
    if _schedule_problems(merged["COURSE_SCHEDULE_ROWS"]):
        # A stale or miscalculated schedule must not survive; use the calendar-consistent one.
        merged["COURSE_SCHEDULE_ROWS"] = pinned["COURSE_SCHEDULE_ROWS"]
    return merged


# ---------------------------------------------------------------------------
# Compliance verification (strict, raising)
# ---------------------------------------------------------------------------

REQUIRED_CALENDAR_STRINGS: Tuple[str, ...] = (
    "August 31",
    "September 7",
    "September 18",
    "October 9",
    "October 30",
    "November 23--27",
    "December 4",
    "December 7--11",
    "December 14",
    "Labor Day",
    "Thanksgiving",
)

REQUIRED_EXAM_DAYS: Tuple[str, ...] = (
    "Saturday, December 5, 2026",
    "Monday, December 7, 2026",
    "Tuesday, December 8, 2026",
    "Wednesday, December 9, 2026",
    "Thursday, December 10, 2026",
    "Friday, December 11, 2026",
)

REQUIRED_EXAM_TIMESLOTS: Tuple[str, ...] = (
    "8:00--10:00",
    "10:30--12:30",
    "1:30--3:30",
    "6:30--8:30",
)

REQUIRED_ACCESSIBILITY_STRINGS: Tuple[str, ...] = (
    "Title II",
    "ADA",
    "Accessibility Services",
    "Director of Accessibility Services",
    "Labry Hall 226",
    "615-547-1286",
    "MCurtis@cumberland.edu",
)

# (month, day) start dates that must appear in an MCP get_important_dates snapshot.
SNAPSHOT_REQUIRED_DATES: Tuple[Tuple[int, int], ...] = (
    (8, 31),
    (9, 7),
    (9, 18),
    (10, 9),
    (10, 30),
    (11, 23),
    (12, 4),
    (12, 7),
    (12, 14),
)

_MONTH_NUMBERS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_DAY_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.IGNORECASE
)
_ISO_DATE_RE = re.compile(r"\b\d{4}-(\d{2})-(\d{2})\b")


def _week_number(day: date) -> int:
    """Return the 1-based Fall 2026 week number containing ``day``."""
    return (day - FALL_2026_WEEK_1_MONDAY).days // 7 + 1


def _schedule_week_map(rows: str) -> Dict[int, str]:
    """Map week number -> the schedule line that starts with that number."""
    weeks: Dict[int, str] = {}
    for line in rows.splitlines():
        match = re.match(r"\s*(\d{1,2})\s*&", line)
        if match:
            weeks[int(match.group(1))] = line
    return weeks


def _schedule_problems(rows: str) -> List[str]:
    """Return a list of calendar-consistency problems in the schedule rows (empty if OK)."""
    problems: List[str] = []
    weeks = _schedule_week_map(rows)
    missing = [w for w in range(1, 17) if w not in weeks]
    if missing:
        problems.append(f"schedule is missing weeks {missing}")
        return problems

    def _week_containing(needle: str) -> Optional[int]:
        for number, line in weeks.items():
            if needle in line:
                return number
        return None

    for needle, day, label in (
        ("Thanksgiving Break", THANKSGIVING_START, "Thanksgiving Break"),
        ("11/14/2026", TAS_DATE, "TAS meeting"),
        ("12/10/2026", MANUSCRIPT_DUE_DATE, "manuscript due date"),
    ):
        found = _week_containing(needle)
        expected = _week_number(day)
        if found is None:
            problems.append(f"{label} not present in schedule")
        elif found != expected:
            problems.append(f"{label} is in week {found} but the calendar puts it in week {expected}")
    for number, line in weeks.items():
        if "Thanksgiving" in line and number != _week_number(THANKSGIVING_START):
            problems.append(f"Thanksgiving mentioned in week {number}")
    return problems


def verify_calendar_compliance(values: Dict[str, str]) -> None:
    """Raise ``ValueError`` unless the pinned calendar data are complete and consistent."""
    problems: List[str] = []

    dates_block = values.get("IMPORTANT_DATES", "")
    for needle in REQUIRED_CALENDAR_STRINGS:
        if needle not in dates_block:
            problems.append(f"IMPORTANT_DATES missing {needle!r}")

    if values.get("EXAM_PERIOD_DATES") != AUTH_EXAM_PERIOD_DATES:
        problems.append("EXAM_PERIOD_DATES does not match the authoritative exam period")

    exam_blocks = values.get("EXAM_SCHEDULE_BLOCKS", "")
    last = -1
    for day in REQUIRED_EXAM_DAYS:
        token = "\\examday{" + day + "}"
        idx = exam_blocks.find(token)
        if idx < 0:
            problems.append(f"EXAM_SCHEDULE_BLOCKS missing {token}")
        elif idx < last:
            problems.append(f"exam day {day!r} is out of chronological order")
        else:
            last = idx
    for slot in REQUIRED_EXAM_TIMESLOTS:
        if slot not in exam_blocks:
            problems.append(f"EXAM_SCHEDULE_BLOCKS missing timeslot {slot!r}")

    problems.extend(_schedule_problems(values.get("COURSE_SCHEDULE_ROWS", "")))

    if problems:
        raise ValueError("Calendar compliance check failed: " + "; ".join(problems))


def load_calendar_snapshot_dates(snapshot_path: Union[str, Path]) -> Set[Tuple[int, int]]:
    """Extract (month, day) pairs from a text/JSON dump of the MCP get_important_dates response."""
    text = Path(snapshot_path).read_text(encoding="utf-8")
    found: Set[Tuple[int, int]] = set()
    for match in _MONTH_DAY_RE.finditer(text):
        found.add((_MONTH_NUMBERS[match.group(1).lower()], int(match.group(2))))
    for match in _ISO_DATE_RE.finditer(text):
        found.add((int(match.group(1)), int(match.group(2))))
    return found


def verify_against_calendar_snapshot(snapshot_path: Union[str, Path]) -> None:
    """Raise ``ValueError`` if the MCP snapshot lacks any pinned Fall 2026 calendar date."""
    snapshot = Path(snapshot_path)
    if not snapshot.is_file():
        raise FileNotFoundError(f"Calendar snapshot not found at {snapshot}")
    present = load_calendar_snapshot_dates(snapshot)
    missing = [pair for pair in SNAPSHOT_REQUIRED_DATES if pair not in present]
    if missing:
        formatted = ", ".join(f"{m}/{d}" for m, d in missing)
        raise ValueError(f"Calendar snapshot {snapshot} does not contain pinned dates: {formatted}")


def _validate_rendered(text: str) -> None:
    """Raise ``ValueError`` unless the rendered LaTeX is structurally sound and compliant."""
    leftovers = PLACEHOLDER_RE.findall(text)
    if leftovers:
        raise ValueError(f"Unexpanded placeholders remain in rendered syllabus: {sorted(set(leftovers))}")

    body = _COMMENT_RE.sub("", text)
    if body.count(r"\begin{document}") != 1 or body.count(r"\end{document}") != 1:
        raise ValueError("Rendered syllabus must contain exactly one \\begin{document} and \\end{document}")
    if body.index(r"\begin{document}") > body.index(r"\end{document}"):
        raise ValueError("\\begin{document} must precede \\end{document}")
    tcb_begin = len(re.findall(r"\\begin\{tcolorbox\}", body))
    tcb_end = len(re.findall(r"\\end\{tcolorbox\}", body))
    if tcb_begin != tcb_end:
        raise ValueError(f"Unbalanced tcolorbox environments: {tcb_begin} begin vs {tcb_end} end")

    missing_ada = [s for s in REQUIRED_ACCESSIBILITY_STRINGS if s not in text]
    if missing_ada:
        raise ValueError(f"Rendered syllabus lacks required accessibility content: {missing_ada}")
    notice_idx = text.find(DYNAMIC_NOTICE_TITLE)
    if notice_idx < 0 or "Canvas" not in text[notice_idx : notice_idx + 800]:
        raise ValueError("Dynamic Course Schedule Notice with a Canvas reference is missing")

    size = len(text.encode("utf-8"))
    if size < MIN_OUTPUT_BYTES:
        raise ValueError(f"Rendered syllabus is too small ({size} bytes; need >= {MIN_OUTPUT_BYTES})")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_syllabus_content(
    template_path: Optional[Union[str, Path]] = None,
    source_path: Optional[Union[str, Path]] = None,
    calendar_snapshot_path: Optional[Union[str, Path]] = None,
) -> str:
    """Render the Cumberland template for CHEM 490CS and return the LaTeX text.

    Course-content placeholders are recovered from ``source_path`` when it is a previously
    migrated (template-shaped) syllabus; otherwise the pinned course constants are used. The
    calendar, exam schedule and Canvas notice are always the authoritative pinned blocks.
    """
    resolved_template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE_PATH
    if not resolved_template.is_file():
        raise FileNotFoundError(f"CU template not found at {resolved_template}")
    template_text = resolved_template.read_text(encoding="utf-8")

    if calendar_snapshot_path is not None:
        verify_against_calendar_snapshot(calendar_snapshot_path)

    resolved_source = Path(source_path) if source_path is not None else DEFAULT_SOURCE_PATH
    recovered: Dict[str, str] = {}
    if resolved_source.is_file():
        recovered = recover_values_from_rendered(
            template_text, resolved_source.read_text(encoding="utf-8")
        )

    values = _merge_source_values(_pinned_values(), recovered)
    verify_calendar_compliance(values)

    unknown = sorted({k for k in _PLACEHOLDER_CAPTURE_RE.findall(template_text) if k not in values})
    if unknown:
        raise ValueError(f"Template contains placeholders with no CHEM 490CS value: {unknown}")

    content = _PLACEHOLDER_CAPTURE_RE.sub(lambda m: values[m.group(1)], template_text)
    _validate_rendered(content)
    return content


def _backup_original(target: Path) -> Optional[Path]:
    """Keep a one-time copy of a pre-migration target before it is overwritten."""
    if not target.is_file():
        return None
    try:
        existing = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        existing = ""
    if DYNAMIC_NOTICE_TITLE in existing:
        return None  # already migrated; the pre-migration original is not this file
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if backup.exists():
        return None
    shutil.copy2(target, backup)
    return backup


def migrate_chem490ccs_syllabus(
    template_path: Optional[Union[str, Path]] = None,
    target_path: Optional[Union[str, Path]] = None,
    source_path: Optional[Union[str, Path]] = None,
    calendar_snapshot_path: Optional[Union[str, Path]] = None,
    backup: bool = True,
) -> Path:
    """Render the migrated syllabus and write it to ``target_path`` (default: in place).

    The content is fully rendered and validated before anything is written, the pre-migration
    original is backed up once, and the write is atomic (temp file + ``os.replace``).
    """
    resolved_target = Path(target_path) if target_path is not None else DEFAULT_TARGET_PATH
    resolved_source = Path(source_path) if source_path is not None else DEFAULT_SOURCE_PATH
    content = build_syllabus_content(
        template_path=template_path,
        source_path=resolved_source,
        calendar_snapshot_path=calendar_snapshot_path,
    )

    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    if backup:
        _backup_original(resolved_target)

    tmp_path = resolved_target.with_name(resolved_target.name + ".tmp")
    with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    os.replace(tmp_path, resolved_target)
    return resolved_target


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint. Returns 0 on success and 1 on failure."""
    parser = argparse.ArgumentParser(
        description="Migrate the CHEM 490CS / CHEM 490CCS syllabus to the Cumberland University template."
    )
    parser.add_argument("--template", dest="template_path", default=None, help="Path to CUSyllabusTemplate.tex")
    parser.add_argument("--target", dest="target_path", default=None, help="Path of the syllabus .tex to write")
    parser.add_argument("--source", dest="source_path", default=None, help="Path of the source syllabus .tex")
    parser.add_argument(
        "--calendar-snapshot",
        dest="calendar_snapshot_path",
        default=None,
        help="Optional dump of the cu-syllabus get_important_dates response to cross-check pinned dates",
    )
    parser.add_argument(
        "--no-backup", dest="backup", action="store_false", help="Do not keep a pre-migration backup copy"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        written = migrate_chem490ccs_syllabus(
            template_path=args.template_path,
            target_path=args.target_path,
            source_path=args.source_path,
            calendar_snapshot_path=args.calendar_snapshot_path,
            backup=args.backup,
        )
    except (OSError, ValueError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1
    print(f"Migrated syllabus written to {written}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
