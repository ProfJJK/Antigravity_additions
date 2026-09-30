"""Migration engine for the CHEM 490CS (1 credit) Directed Study syllabus.

Migrates the syllabus at
    D:/Gdrive/_CU Teaching/CHEM490CS CoChem/Syllabus/CHEM490CS Syllabus.tex
to the Cumberland University LaTeX template at
    D:/Gdrive/__agentic/.sources/CU_sources/CUSyllabusTemplate.tex
and rewrites the syllabus in place.

Provenance of the calendar and compliance data
----------------------------------------------
The Fall 2026 registrar dates and final-exam blocks below (``IMPORTANT_DATES``,
``EXAM_PERIOD_DATES``, ``EXAM_SCHEDULE_BLOCKS``) are pinned transcriptions of the values
specified for the cu-syllabus ``get_important_dates(year=2026, semester="Fall")`` result in
the task acceptance criteria (AC4). This script does NOT call the cu-syllabus MCP server
at runtime, so it makes no claim of live retrieval. Instead, ``verify_rendered_syllabus``
performs a strict, raising check on every render that the required dates, exam days,
Canvas dynamic-schedule notice, Title II / ADA statement, and academic integrity / AI
policies are present. Drift or edits that break them fail the build instead of passing
silently.

The Title II / ADA Accessibility Services statement normally comes from the master
template. If the template lacks it, ``_ensure_ada_statement`` injects the mandated text
before ``\\end{document}``. The master template is only ever read, never written.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

PathLike = Union[str, Path]

# ---------------------------------------------------------------------------
# Authoritative path definitions
# ---------------------------------------------------------------------------

DEFAULT_TEMPLATE_PATH: Path = Path(
    "D:/Gdrive/__agentic/.sources/CU_sources/CUSyllabusTemplate.tex"
)
TARGET_SYLLABUS_PATH: Path = Path(
    "D:/Gdrive/_CU Teaching/CHEM490CS CoChem/Syllabus/CHEM490CS Syllabus.tex"
)
DEFAULT_TARGET_PATH: Path = TARGET_SYLLABUS_PATH

TEMPLATE_TAG_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

_ITEM_SEP = "\n\t\t"
_ROW_SEP = "\n\t\t\t"
_PARA_SEP = "\n\n\t"

# ---------------------------------------------------------------------------
# Course constants (CHEM 490CS, 1 credit)
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
    "In this course, the student will work with instructors to develop a chemistry "
    "research project. The course will include project design, literature review, and "
    "execution of the approved project, culminating in preparation and professional "
    "presentation of the material. This course may be repeated until a maximum of "
    "four hours of credit are obtained.\n\n\t"
    r"\noindent \textbf{Project Scope:} The students are assembling a manuscript together. "
    r"There are 2--4 students working on various parts of this project (e.g., 2 students "
    r"handling wet lab work and spectroscopy, and 2 students handling computational chemistry). "
    r"This course will not yield a standard technical brief; it will culminate in a unified, "
    r"open-access manuscript publication. The computational chemistry students will divide "
    r"their effort proportionally: 25\% of time spent completing the computational chemistry "
    r"component of the keratin study (including Python-based IR spectral deconvolution, "
    r"statistics, and figure generation) and 75\% of time dedicated to predicting "
    r"Fourier-Transform Microwave (FT-MW) spectra for novel van der Waals (VdW) complexes "
    r"to be run experimentally at Tennessee Tech."
)

AUTH_CREDITS = "1"
AUTH_PREREQUISITES = "Instructor Approval"
AUTH_COREQUISITES = "None"

AUTH_REQUIRED_MATERIALS = _ITEM_SEP.join(
    [
        r"\item Access to root/administrator permissions on a personal computer or development workstation.",
        r"\item Active GitHub Education account (for version-controlled laboratory notebooks and commit tracking).",
        r"\item Google Drive Desktop client configured for project synchronization and data sharing.",
        r"\item \textbf{Additional Material:} N/A",
    ]
)

AUTH_LEARNING_OUTCOMES = _ITEM_SEP.join(
    [
        r"\item Demonstrate an ability to conduct experiments and computational investigations on a regular basis.",
        r"\item Demonstrate an ability to record experimental and computational data flawlessly.",
        r"\item Demonstrate proficiency in scientific writing and presentation.",
    ]
)

AUTH_EVALUATION_DESCRIPTION = (
    "You will be evaluated by means of weekly progress reports, laboratory execution, "
    "and a presentation of results. Grades will be weighted according to the "
    "following distribution:"
)

# Labels deliberately contain no backslash before the ``&`` column separator.
_GRADE_ROWS = [
    ("Literature Review", 10),
    ("Weekly Progress / GitHub Commits", 40),
    ("Mid-Semester Eval.", 10),
    ("Presentation", 20),
    ("Manuscript", 20),
]
assert sum(p for _, p in _GRADE_ROWS) == 100

AUTH_GRADE_BREAKDOWN_ROWS = _ROW_SEP.join(
    label + " & " + str(pct) + r"\% \\" for label, pct in _GRADE_ROWS
)

AUTH_GRADE_TOTAL = "100"

AUTH_GRADE_SCALE_ROWS = _ROW_SEP.join(
    [
        r"A  & $>$ 90\% \\",
        r"B+ & 87--89\% \\",
        r"B  & 80--86\% \\",
        r"C+ & 77--79\% \\",
        r"C  & 70--76\% \\",
        r"D  & 60--69\% \\",
        r"F  & $<$ 60\% \\",
    ]
)

_VSPACE = r"\vspace{0.5em}"

AUTH_ASSESSMENT_DESCRIPTIONS = (_PARA_SEP + _VSPACE + "\n\t").join(
    [
        (
            r"\noindent \textbf{Credit Hours (1):} This directed study carries one credit hour. "
            r"The workload corresponds to a single-credit research commitment: regular weekly "
            r"meetings, steady progress on the assigned project, and the deliverables described below."
        ),
        (
            r"\noindent \textbf{Literature Review (10\%):} You will spend the first 2--3 weeks of the "
            r"semester surveying existing literature to establish the current knowledge of your research topic. "
            r"This review should define the scope of your inquiry and identify gaps your project aims to address. "
            r"A comprehensive summary of this review must be submitted by the end of \textbf{Week 3}."
        ),
        (
            r"\noindent \textbf{Weekly Progress Reports \& GitHub Commits (40\%):} Research requires "
            r"consistent activity to generate results. You are required to demonstrate weekly progress "
            r"through two channels:" + "\n\t"
            r"\begin{itemize}[leftmargin=*, noitemsep]" + _ITEM_SEP +
            r"\item \textbf{Weekly Meetings:} We will meet weekly to discuss what was completed in the "
            r"previous week, troubleshoot problems, and outline plans for the upcoming week." + _ITEM_SEP +
            r"\item \textbf{GitHub Commits:} You must maintain a GitHub repository for your project. "
            r"Regular commits will serve as your digital lab notebook, versioning your attempts and "
            r"preserving your data." + "\n\t"
            r"\end{itemize}"
        ),
        (
            r"\noindent \textbf{Mid-Semester Evaluation (10\%):} Halfway through the semester (Week 7), "
            r"we will hold a formal evaluation meeting. This is a ``checkpoint'' to assess the trajectory of "
            r"your project, review the data collected so far, and determine if the initial goals need to be "
            r"adjusted to ensure a successful conclusion to the course."
        ),
        (
            r"\noindent \textbf{Presentation (20\%):} You will prepare a professional presentation of your "
            r"work. This will be designed for delivery at the departmental Colloquium (11/11/2026) and the "
            r"Tennessee Academy of Science (TAS) meeting (11/14/2026). If a public venue is unavailable, you "
            r"may present it to the chemistry faculty. This presentation does not need to cover finalized "
            r"results but should clearly communicate your topic, methodology, and progress."
        ),
        (
            r"\vspace{0.3em}" "\n\t"
            r"\noindent" "\n\t"
            r"\begin{tcolorbox}[colback=CUWhite, colframe=CUGold, boxrule=1.5pt, arc=4pt, "
            r"title=\textbf{TAS Bonus \& Exemption Opportunity}, colbacktitle=CURed, coltitle=CUWhite]"
            + _ITEM_SEP +
            r"\textbf{Tennessee Academy of Science (TAS):} Students are \textbf{strongly encouraged} to "
            r"present their scientific posters at the TAS event during \textbf{Week 12 (11/14/2026)}. "
            r"Presenting at TAS acts as a conference presentation resume-building block and provides enough "
            r"bonus credit to \textbf{completely offset the Manuscript (20\%)} requirement! Students can also "
            r"earn partial bonus credit by attending all sessions or volunteering for at least 4 hours. Proof "
            r"of attendance/volunteering is submitted via QR code or directly by the supervising professor."
            "\n\t"
            r"\end{tcolorbox}"
        ),
        (
            r"\noindent \textbf{Manuscript / Publication (20\%):} The students are assembling a manuscript "
            r"together. There are 2--4 students working on various parts of this project (2 students wet lab "
            r"work/spectroscopy, 2 students computational chemistry). The final submission will be a "
            r"``Publication'' of your results. You will actively balance your time: dedicating 25\% of your "
            r"effort to the Biomimetic-Keratin publication contribution and 75\% of your effort to drafting the "
            r"Methods and Introduction for your primary VdW FT-MW manuscript. This publication will format your "
            r"literature review, experimental methods, and results into a unified manuscript. We will then "
            r"publish it to a public-facing open-access portal. This will act as a valuable resume builder and "
            r"the final summarization of your work for the semester. The manuscript must be submitted by "
            r"\textbf{Thursday of Finals Week (Week 16)}."
        ),
        (
            r"\noindent \textbf{Data Fabrication Policy:} The fabrication of data is one of the most serious "
            r"ethical transgressions in science. If data is fabricated, you will receive an ``F'' (0) in the "
            r"class (a major violation according to Cumberland University's honor code). "
            r"\textbf{\textcolor{CURed}{Data fabrication is the cardinal sin of science.}}"
        ),
    ]
)

AUTH_COURSE_POLICIES = _PARA_SEP.join(
    [
        r"\subsection*{Participation \& Attendance}",
        (
            r"It is incredibly important to participate weekly in research activities; it is very easy to let "
            r"it slip a week, then two, then 8 weeks and fail. Weekly meetings are important so course "
            r"corrections and plans can be made. Unlike a traditional lecture, the Professor does not know in "
            r"advance exactly how open-ended research will turn out. It is these weekly meetings that allow the "
            r"Professor to guide you to a passing and publishable result. If you never discuss problems nor "
            r"successes, it is impossible for the Professor to respond, and no science will be done. Science is "
            r"a continuous cycle of question, hypothesis, experiment, result, and question again."
        ),
        (
            r"A student may not be penalized for absences resulting from required participation in university "
            r"activities such as, but not limited to, athletic competition, band and choir performances, field "
            r"trips, and conferences if the instructor is made aware \textbf{prior} to the absence. No excused "
            r"absence will be awarded after the event. Practice associated with university activities is not "
            r"included. Consideration of absences for illness, emergencies, and other non-sanctioned activities "
            r"will be at the discretion of the instructor. Activities that require participation in the course "
            r"must be made up and work is still expected. However, this course is unique as there are no hard "
            r"deadlines for routine work. Talk with your professor."
        ),
        r"\subsection*{Academic Integrity}",
        (
            r"Please note that the basis for academic integrity is that assignments, presentations, papers, "
            r"and lab notebooks are all used to measure the student's progress in understanding and practicing "
            r"concepts they should be improving through the course. A subversion of this process through not "
            r"submitting work that was done through the student's own abilities negates the ability of the "
            r"professor to measure progress in the course. The basis of academic integrity is displaying the "
            r"current state of your own abilities. Students can and should discuss problems and help each other "
            r"better understand problems, but the student should always submit work they themselves have done "
            r"even though they may have had help noticing issues."
        ),
        (
            r"For written responses like essays, lab notebooks, scientific papers/reports, or posters, "
            r"references and quotes of various types are required. The references used should be clearly "
            r"indicated both in their use and where they are specifically applied. The work submitted should be "
            r"the student's work in structure and conception. Please check the "
            r"\href{https://cumberland.smartcatalogiq.com/en/current-catalog/current-catalog/academic-affairs/academic-integrity-policy/}"
            r"{Cumberland University Academic Integrity Policy}."
        ),
        r"\subsection*{Artificial Intelligence Policy}",
        (
            r"The use of AI of any type is allowed but should be treated as material generated by someone "
            r"else. That means the AI should be referenced as a source and quoted where appropriate. If using "
            r"Copilot or similar integrated AI in a word processor or other such programs (like Grammarly, "
            r"etc.), then the use of such a program should be indicated somewhere in the beginning of the "
            r"assignment. The submitted work should also still be structured according to the student's intent "
            r"and understanding. \textbf{Don't let the AI guide you, you should be guiding the AI} which helps "
            r"flesh out what you are writing. Also please be extremely careful in the use of AI, which still "
            r"tends to hallucinate in a convincing (to most students) way topics which even a cursory search "
            r"would have disproven, including non-existent literature citations."
        ),
        r"\subsection*{Translation Devices}",
        (
            r"Learning science in a non-native language presents challenges that can be mitigated through the "
            r"use of translation devices, facilitating the translation of English terms into a student's native "
            r"language. Students who require translation support during an exam are permitted to use a "
            r"paper-based, ``word-for-word'' dictionary. This dictionary should only offer direct translations "
            r"of words without providing definitions. It must be submitted to the instructor for approval before "
            r"every exam, and the instructor has the final say on whether it is appropriate for use. The use of "
            r"Google Translate or any other online translation tools is strictly prohibited during in-person "
            r"exams, as instructors cannot effectively oversee the use of such digital resources."
        ),
        r"\subsection*{Student Conduct}",
        (
            r"It is expected that students will conduct themselves in a manner that promotes learning by "
            r"themselves and others. Respect for the instructor and fellow researchers is paramount to "
            r"creating a productive learning environment. Students are encouraged to ask any questions at any "
            r"time, as long as they are relevant to the research project."
        ),
        (
            r"If for any reason a student is dismissed due to conduct, dismissal will result in a 10\% penalty "
            r"on the final course grade. If a student is dismissed twice for any reason, i.e. disruptive "
            r"behavior, disrespect for instructor or classmates, etc., there will not be a third. The student "
            r"will not be allowed to rejoin the class either in person or online."
        ),
        r"\subsection*{Policy on Electronic Devices}",
        (
            r"Personal devices should support research activities. Cell phones will be turned off or placed on "
            r"silent (not vibrate) at the beginning of meetings and presentations. The use of earbuds during "
            r"group meetings is prohibited. If there is a situation that requires a phone to be on, please "
            r"report the matter to the instructor. The instructor may ask students at any time to turn off and "
            r"store devices."
        ),
        r"\subsection*{Statement on Health Concerns}",
        (
            r"Members of the Cumberland University community are asked to help maintain a campus that is safe "
            r"for all by undertaking appropriate health precautions. If you experience symptoms of COVID-19, "
            r"the flu, or other ailments, I encourage you to seek medical attention through the University "
            r"Health Services (\href{mailto:healthservices@cumberland.edu}"
            r"{\nolinkurl{healthservices@cumberland.edu}}) or your personal health provider. Students may be "
            r"excused from class attendance with a note from University Health Services or a private health "
            r"provider, though anyone receiving an excused absence will continue to be responsible for "
            r"completing all required course assignments as outlined in the attendance policy above."
        ),
    ]
)

AUTH_DYNAMIC_SCHEDULE_REFERENCE = (
    r"\vspace{0.8em}" "\n\t"
    r"\noindent \textbf{Dynamic Course Schedule Notice:} Please note that while a tentative schedule is "
    r"outlined below, the authoritative dynamic schedule is maintained in the Canvas course shell. Students "
    r"are expected to consult Canvas regularly for real-time announcements, assignment due dates, pacing "
    r"adjustments, computational checkpoints, and supplementary learning modules."
)

# Week 1 begins Monday 8/24/2026; Week 12 contains Colloquium (11/11) and TAS (11/14);
# Week 14 is Thanksgiving (11/23--27); Week 16 is Finals Week (12/7--12/11).
AUTH_COURSE_SCHEDULE_ROWS = _ROW_SEP.join(
    [
        r"1  & Infrastructure  & GitHub, Zotero, Codespaces, ORCA 6.1.1, Drive sync. Target Assignments (Keratin 25\%, VdW 75\%). \\",
        r"2  & Literature      & Structured literature searches and database triage for both projects. \\",
        r"3  & Synthesis       & Deep Literature Synthesis via NotebookLM and Gemini. \textbf{\textcolor{CURed}{Submit Lit Review (10\%)}}. \\",
        r"4  & CoChem-BASE     & Annotated Bibliography finalized. Initial 3D geometries (.xyz) generated. \\",
        r"5  & TOPOS           & Conformational discovery \& deduplication (MACE-OFF23, ORCA GOAT). \\",
        r"6  & BENCH           & High-precision energies (ORCA). \textbf{Finalize Keratin data and handoff to wet-lab.} \\",
        r"7  & TORQ            & Pivot 100\% to VdW PES torsional scans. \textbf{\textcolor{CURed}{Midterm Progress Report (10\%)}}. \\",
        r"8  & Anharmonicity   & VPT2, zero-point corrections, and ground-state observables. \\",
        r"9  & Integration     & \textbf{VdW (75\%):} SpycFit microwave predictions. \textbf{Keratin (25\%):} Python CLS IR deconvolution. \\",
        r"10 & Validation      & \textbf{VdW:} TN Tech FT-MW Prep. \textbf{Keratin:} Stats (LOD/LOQ) \& Figures 3--5, Table 4. \\",
        r"11 & Assembly        & \textbf{VdW:} Poster assembly. \textbf{Keratin:} Day-10 spectra CLS, Figures 7--8, Table 3. \\",
        r"12 & Presentations   & \textbf{\textcolor{CURed}{Colloquium (11/11/2026) \& TAS (11/14/2026) Presentations (20\%)!}} \\",
        r"13 & Troubleshooting & Pipeline troubleshooting, CoChem bug resolution, and GitHub commits. \\",
        r"14 & Break           & \textbf{\textcolor{CURed}{Thanksgiving Break, November 23--27 (No Classes)}} \\",
        r"15 & Drafting        & Draft Methods/Intro (last week of classes ends 12/4). Balance 25\% Keratin, 75\% VdW manuscript effort. \\",
        r"16 & Finals Week     & \textbf{\textcolor{CURed}{Submit Manuscript / Publication of Results (20\%) by Thursday (12/10/2026)}} (Dr.~Klaassen grading and submission to Open Research). \\",
    ]
)

# ---------------------------------------------------------------------------
# Fall 2026 registrar dates and exam blocks (pinned from get_important_dates)
# ---------------------------------------------------------------------------

AUTH_IMPORTANT_DATES = _ITEM_SEP.join(
    [
        r"\item \textbf{August 31}: Last day to register or add/drop classes with no entry on your record.",
        r"\item \textbf{September 7}: Labor Day Holiday (No Classes).",
        r"\item \textbf{September 18}: Last day to drop a class with a grade of W.",
        r"\item \textbf{October 9}: Midterm grades are due to be submitted in the Student First portal.",
        r"\item \textbf{October 30}: Last day to drop a class (with a grade of WP or WF).",
        r"\item \textbf{November 23--27}: Thanksgiving Holidays (No Classes). University Offices are closed November 26--27.",
        r"\item \textbf{December 4}: Last day of classes.",
        r"\item \textbf{December 7--11}: Final examination week (see detailed schedule below).",
        r"\item \textbf{December 14}: Final course grades are due to be submitted in the Student First portal.",
    ]
)

AUTH_EXAM_PERIOD_DATES = "DECEMBER 7--11, 2026"


def _exam_day_note(title: str, weekday: str) -> str:
    return (
        r"\examday{" + title + "}\n\t"
        r"\begin{center}" "\n\t\t"
        r"\textit{*** Any lab or class that meets exclusively on " + weekday +
        r" will hold the final exam at the time normally scheduled for this class.}" "\n\t"
        r"\end{center}"
    )


def _exam_day_table(title: str, rows: Sequence[Sequence[str]]) -> str:
    body = "\n\t\t\t".join(slot + " & " + label + r" \\" for slot, label in rows)
    return (
        r"\examday{" + title + "}\n\t"
        r"\begin{center}" "\n\t\t"
        r"\renewcommand{\arraystretch}{1.3}" "\n\t\t"
        r"\begin{tabular}{@{}ll@{}}" "\n\t\t\t" + body + "\n\t\t"
        r"\end{tabular}" "\n\t"
        r"\end{center}"
    )


AUTH_EXAM_SCHEDULE_BLOCKS = (_PARA_SEP).join(
    [
        _exam_day_note("Saturday, December 5, 2026", "Saturday"),
        _exam_day_table(
            "Monday, December 7, 2026",
            [
                ("8:00--10:00", "Classes meeting at 8:00 MW"),
                ("10:30--12:30", "Classes meeting at 11:00 MW"),
                ("1:30--3:30", "Classes meeting at 2:00 MW"),
                ("6:30--8:30", "Classes meeting Monday evening"),
            ],
        ),
        _exam_day_table(
            "Tuesday, December 8, 2026",
            [
                ("8:00--10:00", "Classes meeting at 8:00 TR"),
                ("10:30--12:30", "Classes meeting at 11:00 TR"),
                ("1:30--3:30", "Classes meeting at 2:00 TR"),
                ("6:30--8:30", "Classes meeting Tuesday evening"),
            ],
        ),
        _exam_day_table(
            "Wednesday, December 9, 2026",
            [
                ("8:00--10:00", "Classes meeting at 9:30 MW"),
                ("10:30--12:30", "Classes meeting at 12:30 MW"),
                ("1:30--3:30", "Classes meeting at 3:30 or 4:00 MW"),
                ("4:00--6:00", "Classes meeting at 5:00 or 5:30 MW"),
                ("6:30--8:30", "Classes meeting Wednesday evening"),
            ],
        ),
        _exam_day_table(
            "Thursday, December 10, 2026",
            [
                ("8:00--10:00", "Classes meeting at 9:30 TR"),
                ("10:30--12:30", "Classes meeting at 12:30 TR"),
                ("1:30--3:30", "Classes meeting at 3:30 or 4:00 TR"),
                ("4:00--6:00", "Classes meeting at 5:00 or 5:30 TR"),
                ("6:30--8:30", "Classes meeting Thursday evening"),
            ],
        ),
        _exam_day_note("Friday, December 11, 2026", "Friday"),
    ]
)

# ---------------------------------------------------------------------------
# Placeholder mapping
# ---------------------------------------------------------------------------

COURSE_DATA: Dict[str, str] = {
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
    # GRADE_TOTAL is context-sensitive and handled in _expand_template_tags.
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


def _grade_total_value(source: str, start: int, end: int) -> str:
    """Render GRADE_TOTAL so the final text always reads ``\\textbf{100\\%}``.

    The template may wrap the tag as ``\\textbf{{{GRADE_TOTAL}}\\%}``, as
    ``{{GRADE_TOTAL}}\\%``, or use it bare. Inspect the surrounding text to avoid
    producing a doubled ``\\%`` or a missing percent sign.
    """
    before = source[:start]
    after = source[end:]
    in_textbf = before.endswith(r"\textbf{")
    followed_by_percent = after.startswith(r"\%")
    if in_textbf:
        return AUTH_GRADE_TOTAL if followed_by_percent else AUTH_GRADE_TOTAL + r"\%"
    if followed_by_percent:
        return AUTH_GRADE_TOTAL
    return r"\textbf{" + AUTH_GRADE_TOTAL + r"\%}"


def _expand_template_tags(template_text: str) -> str:
    """Replace every ``{{KEY}}`` in a single pass; unknown keys raise ValueError."""
    unknown = sorted({k for k in TEMPLATE_TAG_RE.findall(template_text) if k not in COURSE_DATA})
    if unknown:
        raise ValueError(f"Template contains tokens with no CHEM 490CS mapping: {unknown}")

    def _sub(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key == "GRADE_TOTAL":
            return _grade_total_value(match.string, match.start(), match.end())
        return COURSE_DATA[key]

    return TEMPLATE_TAG_RE.sub(_sub, template_text)


# ---------------------------------------------------------------------------
# Compliance enforcement and verification
# ---------------------------------------------------------------------------

ADA_REQUIRED_STRINGS = (
    "Title II",
    "ADA",
    "Accessibility Services",
    "Labry Hall 226",
    "615-547-1286",
    "MCurtis@cumberland.edu",
)

ADA_STATEMENT = (
    r"\subsection*{Title II / ADA Accessibility Services}" "\n\t"
    r"Cumberland University is committed to providing equal access in accordance with Title II of the "
    r"Americans with Disabilities Act (ADA) and Section 504 of the Rehabilitation Act. Students who "
    r"require accommodations for a documented disability should contact Accessibility Services in "
    r"Labry Hall 226, by phone at 615-547-1286, or by email at "
    r"\href{mailto:MCurtis@cumberland.edu}{\nolinkurl{MCurtis@cumberland.edu}}. Accommodations are "
    r"not retroactive, so please contact Accessibility Services as early in the semester as possible."
)

REQUIRED_CALENDAR_STRINGS = (
    "Fall 2026",
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
    "DECEMBER 7--11, 2026",
)

REQUIRED_EXAM_DAYS = (
    "Saturday, December 5, 2026",
    "Monday, December 7, 2026",
    "Tuesday, December 8, 2026",
    "Wednesday, December 9, 2026",
    "Thursday, December 10, 2026",
    "Friday, December 11, 2026",
)


def _ensure_ada_statement(content: str) -> str:
    """Inject the Title II / ADA statement before ``\\end{document}`` if the template lacks it."""
    if all(token in content for token in ADA_REQUIRED_STRINGS):
        return content
    marker = r"\end{document}"
    idx = content.rfind(marker)
    if idx < 0:
        raise ValueError(r"Template has no \end{document}; cannot enforce Title II / ADA statement")
    return content[:idx] + ADA_STATEMENT + "\n\n" + content[idx:]


def _normalize_percent(content: str) -> str:
    """Escape any bare ``%`` that directly follows a digit (it would comment out the line)."""
    return re.sub(r"(?<=\d)%", r"\\%", content)


def verify_rendered_syllabus(content: str) -> None:
    """Strictly verify the rendered syllabus; raise ValueError on any violation."""
    problems: List[str] = []

    leftovers = TEMPLATE_TAG_RE.findall(content)
    if leftovers:
        problems.append(f"unexpanded template tags: {sorted(set(leftovers))}")
    if "{{" in content:
        problems.append("stray '{{' present")

    for token in REQUIRED_CALENDAR_STRINGS:
        if token not in content:
            problems.append(f"missing calendar entry: {token}")
    for token in REQUIRED_EXAM_DAYS:
        if token not in content:
            problems.append(f"missing exam block: {token}")
    for token in ADA_REQUIRED_STRINGS:
        if token not in content:
            problems.append(f"missing ADA/Title II element: {token}")
    for token in (
        "Dynamic Course Schedule",
        "Canvas course shell",
        "Academic Integrity",
        "Artificial Intelligence",
    ):
        if token not in content:
            problems.append(f"missing compliance element: {token}")

    if content.count(r"\begin{document}") != 1 or r"\end{document}" not in content:
        problems.append("invalid document structure")

    weights = [
        int(m.group(1))
        for label, _ in _GRADE_ROWS
        for m in [re.search(re.escape(label) + r"[^&\n\\]*&\s*(\d+)\s*\\%", content)]
        if m
    ]
    if len(weights) != len(_GRADE_ROWS) or sum(weights) != 100:
        problems.append(f"grade weights do not sum to 100: {weights}")

    if problems:
        raise ValueError("Rendered CHEM 490CS syllabus failed verification: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_syllabus_content(template_path: Optional[PathLike] = None) -> str:
    """Read the CU template and render the CHEM 490CS syllabus text (template is never written)."""
    resolved_template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE_PATH
    if not resolved_template.is_file():
        raise FileNotFoundError(f"CU template not found at {resolved_template}")

    template_text = resolved_template.read_text(encoding="utf-8-sig")
    content = _expand_template_tags(template_text)
    content = _ensure_ada_statement(content)
    content = _normalize_percent(content)
    verify_rendered_syllabus(content)
    return content


def migrate_chem490cs_syllabus(
    template_path: Optional[PathLike] = None,
    target_path: Optional[PathLike] = None,
) -> str:
    """Render the migrated syllabus, write it to ``target_path`` in place, and return the text."""
    resolved_template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE_PATH
    resolved_target = Path(target_path) if target_path is not None else TARGET_SYLLABUS_PATH

    try:
        same = resolved_template.resolve() == resolved_target.resolve()
    except OSError:
        same = False
    if same:
        raise ValueError("Refusing to overwrite the master template; choose a different target path")

    rendered = build_syllabus_content(template_path=resolved_template)

    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    with open(resolved_target, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(rendered)
    return rendered


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint. Returns 0 on success, 1 on failure."""
    parser = argparse.ArgumentParser(
        description="Migrate the CHEM 490CS Directed Study syllabus to the Cumberland University template."
    )
    parser.add_argument(
        "--template",
        dest="template_path",
        default=str(DEFAULT_TEMPLATE_PATH),
        help="Path to CUSyllabusTemplate.tex",
    )
    parser.add_argument(
        "--target",
        dest="target_path",
        default=str(TARGET_SYLLABUS_PATH),
        help="Path of the syllabus .tex file to write",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        rendered = migrate_chem490cs_syllabus(
            template_path=args.template_path, target_path=args.target_path
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Migrated CHEM 490CS syllabus -> {args.target_path} ({len(rendered)} characters)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
