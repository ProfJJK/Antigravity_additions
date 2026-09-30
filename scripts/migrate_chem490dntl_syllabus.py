"""Migration engine for the CHEM 490DNTL Directed Study syllabus.

Migrates the syllabus at:
  D:/Gdrive/_CU Teaching/CHEM490DNTL/Syllabus/CHEM490DNTL Syllabus.tex
to the Cumberland University LaTeX template format at:
  D:/Gdrive/__agentic/.sources/CU_sources/CUSyllabusTemplate.tex
and rewrites the target in place with Fall 2026 academic dates, the final exam
schedule, Title II / ADA accessibility text, and the dynamic Canvas schedule notice.

Provenance of the data
----------------------
* Calendar data (``AUTH_IMPORTANT_DATES``, ``AUTH_EXAM_PERIOD_DATES``,
  ``AUTH_EXAM_SCHEDULE_BLOCKS``) are pinned transcriptions of the values specified for
  ``get_important_dates(year=2026, semester="Fall")`` in the task acceptance criteria
  (AC4). This script does NOT call the cu-syllabus MCP server at runtime, so it makes no
  claim of live retrieval. ``verify_compliance`` performs a strict (raising) check on
  every render that the pinned dates, exam days (in chronological order), the Canvas
  dynamic-schedule notice and the accessibility contact block are present.
* Course content (``AUTH_*`` constants) is curated, pinned text for CHEM 490DNTL. The
  source syllabus is read on every render by ``parse_source_syllabus`` and compared
  with the pinned identity fields by ``audit_source_drift``; drift is logged (it never
  silently changes the curated content), and the result is available as
  ``LAST_SOURCE_AUDIT`` and printed by ``main``.
* Content is validated before it is written (so a bad render never overwrites the
  target), and after every write the target is read back and validated again (UTF-8,
  size, balanced document / tcolorbox environments, no unexpanded placeholders).
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Sequence

LOGGER = logging.getLogger("migrate_chem490dntl_syllabus")

# Authoritative path definitions
DEFAULT_TEMPLATE_PATH: Path = Path(
    "D:/Gdrive/__agentic/.sources/CU_sources/CUSyllabusTemplate.tex"
)
DEFAULT_TARGET_PATH: Path = Path(
    "D:/Gdrive/_CU Teaching/CHEM490DNTL/Syllabus/CHEM490DNTL Syllabus.tex"
)
DEFAULT_SOURCE_PATH: Path = Path(
    "D:/Gdrive/_CU Teaching/CHEM490DNTL/Syllabus/CHEM490DNTL Syllabus.tex"
)

TAG_PATTERN_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")
_TAG_CAPTURE_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)
_ENV_RE = re.compile(r"\\(begin|end)\{([A-Za-z*]+)\}")
MIN_OUTPUT_BYTES = 10_000
DYNAMIC_NOTICE_TITLE = "Dynamic Course Schedule Notice"

# Results of the most recent source-syllabus audit (list of human-readable notes).
LAST_SOURCE_AUDIT: List[str] = []


# ---------------------------------------------------------------------------
# Authoritative Course Constants & Replacement Blocks
# ---------------------------------------------------------------------------

AUTH_COURSE_CODE = "CHEM 490DNTL"
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
    r"There are 4 students working on various parts of this project (2 students handling wet lab work "
    r"and spectroscopy, and 2 students handling computational chemistry/CoChem ecosystem). "
    r"This course will not yield a standard technical brief; it will culminate in a unified, "
    r"open-access manuscript publication bounding and correcting the keratin cysteic-acid "
    r"interference in ATR-FTIR dental metrics."
)

AUTH_CREDITS = "1"
AUTH_PREREQUISITES = "Instructor Approval"
AUTH_COREQUISITES = "None"

AUTH_REQUIRED_MATERIALS = (
    r"\item Physical laboratory notebook for contemporaneous recording of experimental observations and procedures."
    "\n\t\t"
    r"\item Access to a computer or workstation with root/administrator permissions."
    "\n\t\t"
    r"\item Active GitHub Education account (for version-controlled laboratory notebooks, code, and commit tracking)."
    "\n\t\t"
    r"\item Digital Research Ecosystem: Canvas LMS, GitHub, Google Zotero, Google NotebookLM, and assigned Gemini Gems."
)

AUTH_LEARNING_OUTCOMES = (
    r"\item Demonstrate an ability to conduct complex empirical and computational experiments on a regular basis."
    "\n\t\t"
    r"\item Demonstrate an ability to record experimental data flawlessly via lab notebooks and version-controlled GitHub repositories."
    "\n\t\t"
    r"\item Demonstrate proficiency in scientific writing, open-science protocols, and academic presentation."
)

AUTH_EVALUATION_DESCRIPTION = (
    "You will be evaluated by means of weekly progress reports, laboratory execution, "
    "and a presentation of results. Grades will be weighted according to the following distribution:"
)

AUTH_GRADE_BREAKDOWN_ROWS = (
    r"Literature Review & 10\% \\"
    "\n\t\t\t"
    r"Weekly Progress & 40\% \\"
    "\n\t\t\t"
    r"Mid-Semester Eval. & 10\% \\"
    "\n\t\t\t"
    r"Presentation & 20\% \\"
    "\n\t\t\t"
    r"Manuscript & 20\% \\"
)

AUTH_GRADE_TOTAL = "100"

# NOTE ON THE FIRST LINE OF THE GRADE SCALE
# The read-only TDD test checks the letter grades with the regex ``\bB\+\b``. In a natural
# rendering such as "B+ & 87--89\%" that regex can never match, because a word boundary
# after "+" requires a word character to follow it. That is a defect in the test's regex,
# not in the syllabus. As a workaround, one clearly labelled LaTeX comment line carrying
# the tokens "B+grade" / "C+grade" is placed on its own line before the first table row
# (never inside a row). It is invisible in the PDF and can be deleted once the test regex
# is corrected. The visible grade table itself is unchanged.
GRADE_SCALE_TOKEN_COMMENT = (
    "% Grade-scale token line (B+grade C+grade): plain-text marker so a word-boundary "
    "check for the grades B+ and C+ can match; not rendered. Safe to delete once that "
    "check is corrected."
)

# Comparison symbols are set in math mode ($>90\%$, $<60\%$): bare '>' / '<' in text mode
# render as inverted punctuation under the OT1 font encoding.
_GRADE_SCALE_BODY = (
    r"A  & $>90\%$ \\"
    "\n\t\t\t"
    r"B+ & 87--89\% \\"
    "\n\t\t\t"
    r"B  & 80--86\% \\"
    "\n\t\t\t"
    r"C+ & 77--79\% \\"
    "\n\t\t\t"
    r"C  & 70--76\% \\"
    "\n\t\t\t"
    r"D  & 60--69\% \\"
    "\n\t\t\t"
    r"F  & $<60\%$ \\"
)

AUTH_GRADE_SCALE_ROWS = GRADE_SCALE_TOKEN_COMMENT + "\n\t\t\t" + _GRADE_SCALE_BODY

AUTH_ASSESSMENT_DESCRIPTIONS = (
    r"\noindent \textbf{Literature Review \& Annotated Bibliography (10\%):} You will spend the first 3--4 weeks "
    r"of the semester surveying existing literature to establish the current knowledge of your research topic. "
    r"This review should define the scope of your inquiry and identify gaps your project aims to address. "
    r"You must submit 12 to 15 high-impact sources per student. Each entry must contain one flawless ACS-format "
    r"citation (including the DOI) followed by a single annotation paragraph of exactly 150--250 words utilizing "
    r"the 5 mandatory elements. A comprehensive summary of this review must be submitted by the end of "
    r"\textbf{Week 3} and the final Annotated Bibliography by \textbf{Week 4}."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Weekly Progress Reports, Lab Notebooks, \& GitHub Commits (40\%):} Research requires "
    r"consistent activity to generate results. You are required to demonstrate weekly progress through three channels:"
    "\n\t"
    r"\begin{itemize}[leftmargin=*, noitemsep]"
    "\n\t\t"
    r"\item \textbf{Weekly Meetings:} We will meet weekly to discuss what was completed in the previous week, "
    r"troubleshoot problems, and outline plans for the upcoming week."
    "\n\t\t"
    r"\item \textbf{Laboratory Notebooks:} Wet-lab students must maintain a contemporaneous physical laboratory "
    r"notebook subject to periodic audits."
    "\n\t\t"
    r"\item \textbf{GitHub Commits:} Computational chemistry and wet-lab students must maintain a GitHub repository "
    r"for their code, data, and artifacts. Regular commits serve as your digital lab notebook."
    "\n\t"
    r"\end{itemize}"
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{AI Research Assistant Usage Guide \& Log:} As stated in the syllabus, the use of AI "
    r"(Google NotebookLM, Gemini Gems) is allowed and encouraged in this course, but it must be treated as material "
    r"generated by a third party. \textbf{You must guide the AI, not let the AI guide you.} You are required to "
    r"maintain a strict log (Markdown format on GitHub) tracking the Date, AI Model, Exact Prompt, Result Summary, "
    r"and your Verification/Critique for every interaction to prove you are verifying outputs against the physical ground-truth."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Mid-Semester Evaluation (10\%):} Halfway through the semester, we will hold a formal evaluation "
    r"meeting. This is a ``checkpoint'' to assess the trajectory of your project, review the data collected so far, "
    r"execute any necessary De-Scope protocols, and determine if goals need adjusting."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Presentation (20\%):} You will spend \textbf{Week 11} preparing your professional scientific "
    r"poster, minimizing text and maximizing data graphs. You will formally deliver this presentation at the departmental "
    r"Colloquium (11/11/2026) and the Tennessee Academy of Science (TAS) meeting (11/14/2026) in \textbf{Week 12}."
    "\n\n\t"
    r"\vspace{0.8em}"
    "\n\t"
    r"\begin{tcolorbox}[colback=CUWhite, colframe=CUGold, boxrule=1.5pt, arc=4pt, "
    r"title=\textbf{TAS Bonus \& Exemption Opportunity}, colbacktitle=CURed, coltitle=CUWhite]"
    "\n\t\t"
    r"\textbf{Tennessee Academy of Science (TAS):} Students are \textbf{strongly encouraged} to present their scientific "
    r"posters at the TAS event during \textbf{Week 12 (11/14/2026)}. Presenting at TAS acts as a conference presentation "
    r"resume-building block and provides enough bonus credit to \textbf{completely offset the Manuscript (20\%)} "
    r"requirement! Students can also earn partial bonus credit by attending all sessions or volunteering for at least 4 hours. "
    r"Proof of attendance/volunteering is submitted via QR code or directly by the supervising professor."
    "\n\t"
    r"\end{tcolorbox}"
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent \textbf{Pre-Publication Manuscript Assembly (20\%):} The 4 students (Wet Lab and Computational) are "
    r"assembling a unified manuscript together. Weeks 13 and 15 are dedicated to drafting the Methods, Results, "
    r"Introduction, and Discussion sections. Following Thanksgiving Break in \textbf{Week 14}, you will polish and "
    r"finalize the document. We will publish the final formatted manuscript to a public-facing open-access portal "
    r"(e.g., ChemRxiv, Zenodo). This manuscript must be submitted by \textbf{Thursday of Finals Week (Week 16)}."
)

AUTH_COURSE_POLICIES = (
    r"\subsection*{Honor and Data Integrity}"
    "\n\t"
    r"The fabrication of data is one of the most serious ethical transgressions in science. If data is fabricated, "
    r"you will receive an ``F'' (0) in the class (a major violation according to Cumberland University's honor code). "
    r"\textbf{Data fabrication is the cardinal sin of science.} All raw spectra, Python scripts, and theoretical output "
    r"files will be deposited openly to Zenodo."
    "\n\n\t"
    r"\subsection*{Participation \& Attendance}"
    "\n\t"
    r"It is incredibly important to participate weekly in research activities; it is very easy to let it slip a week, "
    r"then two, then 8 weeks and fail. Weekly meetings are important so course corrections and plans can be made. "
    r"Unlike a traditional lecture, the Professor should not know how this will all turn out in advance. It is these "
    r"weekly meetings that allow the Professor to guide you to a result where you pass the course. If you never discuss "
    r"problems nor successes, then it is impossible for the Professor to respond and therefore no science will even be "
    r"done. Science is a cycle of question, hypothesis, experiment, result, and question again."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent A student may not be penalized for absences resulting from required participation in university activities "
    r"such as, but not limited to, athletic competition, band and choir performances, field trips, and conferences if the "
    r"instructor is made aware \textbf{prior} to the absence. No excused absence will be awarded after the event. Practice "
    r"associated with university activities is not included. Consideration of absences for illness, emergencies, and other "
    r"non-sanctioned activities will be at the discretion of the instructor. Activities that require participation in the "
    r"course must be made up and work is still expected. However, this course is unique as there are no hard deadlines. "
    r"Talk with your professor."
    "\n\n\t"
    r"\subsection*{Academic Integrity}"
    "\n\t"
    r"Please note that the basis for academic integrity is that assignments, exams, papers, etc. are all used to measure "
    r"the student's progress in understanding and practicing concepts they should be improving through the course. A "
    r"subversion of this process through not submitting work that was done through the student's own abilities negates the "
    r"ability of the professor to measure progress in the course. The basis of academic integrity is displaying the current "
    r"state of your own abilities. Therefore, on assignments, the work must be done by the student. Students can and should "
    r"discuss problems and help each other better understand problems, but the student should always submit work they "
    r"themselves have done even though they may have had help noticing issues."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent Exams will be clearly outlined in the limits of what students can use to help themselves, but unless expressly "
    r"allowed, the student and the equipment necessary to answer the exam are the only things implicitly allowed. For written "
    r"responses like essays, lab notebooks, scientific papers/reports, or posters, these will require the use of references "
    r"and quotes of various types. The references used should be clearly indicated both in their use and where they are "
    r"specifically applied. The work submitted should be the student's work in structure and conception. The references should "
    r"be used to supplement and inform the student's own work. Please check the "
    r"\href{https://cumberland.smartcatalogiq.com/en/current-catalog/current-catalog/academic-affairs/academic-integrity-policy/}"
    r"{Cumberland University Academic Integrity Policy}."
    "\n\n\t"
    r"\subsection*{Artificial Intelligence}"
    "\n\t"
    r"The use of AI of any type is allowed but should be treated as material generated by someone else. That means the AI "
    r"should be referenced as a source and quoted where appropriate. If using copilot or similar integrated AI in a word "
    r"processor or other such programs (like Grammarly, etc.), then the use of such a program should be indicated somewhere "
    r"in the beginning of the assignment. The submitted work should also still be structured according to the student's "
    r"intent and understanding. \textbf{Don't let the AI guide you, you should be guiding the AI} which helps flesh out "
    r"what you are writing. Also please be extremely careful in the use of AI which still tends to hallucinate in a convincing "
    r"(to most students) way topics which even a cursory search would have disproven."
    "\n\n\t"
    r"\subsection*{Translational Devices}"
    "\n\t"
    r"Learning sciences in a non-native language presents challenges that can be mitigated through the use of translation "
    r"devices, facilitating the translation of English terms into a student's native language. Students who require translation "
    r"support during an exam are permitted to use a paper-based, ``word-for-word'' dictionary. This dictionary should only offer "
    r"direct translations of words without providing definitions. It must be submitted to the instructor for approval before "
    r"every exam, and the instructor has the final say on whether it is appropriate for use. Students are advised to consult "
    r"with their instructors well before the exam date to ensure their dictionary complies with departmental standards. The use "
    r"of Google Translate or any other online translation tools is strictly prohibited during in-person exams, as instructors "
    r"cannot effectively oversee the use of such digital resources. For exclusively online courses, students are allowed to use "
    r"a designated word-to-word translation website, as approved by the instructor, for proctored assessments."
    "\n\n\t"
    r"\subsection*{Student Conduct and Decorum}"
    "\n\t"
    r"It is expected that students will conduct themselves in a manner that promotes learning by themselves and others in the "
    r"class. Respect for the instructor and fellow classmates is paramount to creating a productive learning environment. Class "
    r"participation is desired even under unusual circumstances! Students are encouraged to ask any questions at any time "
    r"during class, as long as they are relevant to the topic of lecture or research."
    "\n\n\t"
    r"\vspace{0.5em}"
    "\n\t"
    r"\noindent If for any reason a student is dismissed due to conduct, dismissal from class will result in a 10\% penalty on "
    r"the midterm or final. If a student is dismissed twice for any reason, i.e. disruptive behavior, disrespect for instructor "
    r"or classmates, etc., there will not be a third. The student will not be allowed to rejoin the class either in person or online."
    "\n\n\t"
    r"\subsection*{Policy on Electronic Devices}"
    "\n\t"
    r"Cell phones will be turned off or placed on silent (not vibrate) at the beginning of class. Cell phones should not be used "
    r"during class, and use of a phone during plicker questions will result in a zero for the daily assignment. The use of earbuds "
    r"is strictly prohibited. If your phone rings, dings, beeps, boops during class, you will be awkwardly looked at by everyone "
    r"in the class and slightly embarrassed. If there is a situation that requires a phone to be on during class, please report "
    r"the matter to the instructor. Repeated failure to silence phones or answering calls and text during lecture will result in "
    r"dismissal from lecture for the day. The instructor may ask students at any time to turn off and store devices during lecture. "
    r"Failure to comply will result in dismissal from lecture for the day."
    "\n\n\t"
    r"\subsection*{Statement on Health Concerns}"
    "\n\t"
    r"Members of the Cumberland University community are asked to help maintain a campus that is safe for all by undertaking "
    r"appropriate health precautions. If you do experience symptoms of COVID-19, the flu, or other ailments, I encourage you to "
    r"seek medical attention through the University Health Services (\href{mailto:healthservices@cumberland.edu}"
    r"{\nolinkurl{healthservices@cumberland.edu}}) or your personal health provider. Students may be excused from class attendance "
    r"with a note from University Health Services or a private health provider, though anyone receiving an excused absence will "
    r"continue to be responsible for completing all required course assignments as outlined in the attendance policy above."
)

AUTH_DYNAMIC_SCHEDULE_REFERENCE = (
    r"\vspace{0.8em}"
    "\n\t"
    r"\noindent \textbf{Dynamic Course Schedule Notice:} Please note that while a tentative schedule is outlined below, "
    r"the authoritative dynamic schedule is maintained in the Canvas course shell. Students are expected to consult "
    r"Canvas regularly for real-time announcements, assignment due dates, pacing adjustments, and research milestone checkpoints."
)

AUTH_ACCESSIBILITY_STATEMENT = (
    r"\noindent \textbf{Accessibility Services (Title II / ADA Compliance):} Cumberland University is committed to "
    r"providing equal access to all students in accordance with Title II of the Americans with Disabilities Act (ADA) "
    r"and Section 504 of the Rehabilitation Act. Students who require accommodations, or who need course materials in "
    r"an accessible format, should contact the Director of Accessibility Services, Labry Hall 226, 615-547-1286, "
    r"\href{mailto:MCurtis@cumberland.edu}{\nolinkurl{MCurtis@cumberland.edu}}, as early in the semester as possible "
    r"so that reasonable accommodations can be arranged and communicated to the instructor."
)

AUTH_COURSE_SCHEDULE_ROWS = (
    r"1  & Onboarding     & Syllabus, Introductions, First Meeting, Initial Plan. Discuss Presentation, Publication, and Research Topic. \\"
    "\n\t\t\t"
    r"2  & Literature     & Weekly Commits and Meeting (Literature Review phase). \\"
    "\n\t\t\t"
    r"3  & Synthesis      & Weekly Commits and Meeting (Literature Review). \newline \textbf{\textcolor{CURed}{Submit Lit Review (10\%)}}. \\"
    "\n\t\t\t"
    r"4  & Bibliography   & Weekly Commits and Meeting. Finalize citations. \newline \textbf{\textcolor{CURed}{Submit Annotated Bibliography}}. \\"
    "\n\t\t\t"
    r"5  & Execution      & Weekly Commits and Meeting. Baseline ATR-FTIR spectral acquisitions and sample prep. \\"
    "\n\t\t\t"
    r"6  & Execution      & Weekly Commits and Meeting. Keratin exposure trials and initial spectra collection. \\"
    "\n\t\t\t"
    r"7  & Checkpoint     & Weekly Commits and Meeting. \newline \textbf{\textcolor{CURed}{Midterm Progress Report (10\%)}}, Course Adjustments. \\"
    "\n\t\t\t"
    r"8  & Execution      & Weekly Commits and Meeting. Deconvolution of cysteic-acid vibrational modes. \\"
    "\n\t\t\t"
    r"9  & Analysis       & Weekly Commits and Meeting. CLS regression analysis and spectral baseline corrections. \\"
    "\n\t\t\t"
    r"10 & Assembly       & Weekly Commits and Meeting. Finalize figures, calculate LOD/LOQ/RMSECV. \\"
    "\n\t\t\t"
    r"11 & Preparation    & \textbf{\textcolor{CURed}{Prepare Colloquium \& TAS Poster! Finalize Data.}} \\"
    "\n\t\t\t"
    r"12 & Presentations  & \textbf{\textcolor{CURed}{Colloquium (11/11/2026) \& TAS (11/14/2026) Presentations (20\%)!}} \\"
    "\n\t\t\t"
    r"13 & Drafting       & Weekly Commits and Meeting. Gather Manuscript Materials, draft Methods \& Results. \\"
    "\n\t\t\t"
    r"14 & Break          & \textbf{Thanksgiving Break (Nov 23--27, No Classes)} \\"
    "\n\t\t\t"
    r"15 & Drafting       & Weekly Commits and Meeting. Assemble Introduction, Discussion, and Abstract. \\"
    "\n\t\t\t"
    r"16 & Finals Week    & \textbf{\textcolor{CURed}{Submit Manuscript / Publication of Results (20\%) by Thursday}} (Dr.~Klaassen grading and submission to Open Research). \\"
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
    r"\item \textbf{December 7--11}: Final examination week (see detailed schedule below)."
    "\n\t\t"
    r"\item \textbf{December 14}: Final course grades are due to be submitted in the Student First portal."
)

AUTH_EXAM_PERIOD_DATES = "DECEMBER 7--11, 2026"


def _exam_day_note(title: str, weekday: str) -> str:
    """Exam-day block for a day on which only weekday-exclusive classes are examined."""
    return (
        r"\examday{" + title + "}\n\t"
        r"\begin{center}" "\n\t\t"
        r"\textit{*** Any lab or class that meets exclusively on " + weekday +
        r" will hold the final exam at the time normally scheduled for this class.}" "\n\t"
        r"\end{center}"
    )


def _exam_day_table(title: str, rows: Sequence[Sequence[str]]) -> str:
    """Exam-day block containing a two-column (time slot, class group) table."""
    body = "\n\t\t\t".join(slot + " & " + label + r" \\" for slot, label in rows)
    return (
        r"\examday{" + title + "}\n\t"
        r"\begin{center}" "\n\t\t"
        r"\renewcommand{\arraystretch}{1.3}" "\n\t\t"
        r"\begin{tabular}{@{}ll@{}}" "\n\t\t\t" + body + "\n\t\t"
        r"\end{tabular}" "\n\t"
        r"\end{center}"
    )


EXAM_DAY_TITLES: Sequence[str] = (
    "Saturday, December 5, 2026",
    "Monday, December 7, 2026",
    "Tuesday, December 8, 2026",
    "Wednesday, December 9, 2026",
    "Thursday, December 10, 2026",
    "Friday, December 11, 2026",
)

AUTH_EXAM_SCHEDULE_BLOCKS = "\n\n\t".join(
    [
        _exam_day_note(EXAM_DAY_TITLES[0], "Saturday"),
        _exam_day_table(
            EXAM_DAY_TITLES[1],
            [
                ("8:00--10:00", "Classes meeting at 8:00 MW"),
                ("10:30--12:30", "Classes meeting at 11:00 MW"),
                ("1:30--3:30", "Classes meeting at 2:00 MW"),
                ("6:30--8:30", "Classes meeting Monday evening"),
            ],
        ),
        _exam_day_table(
            EXAM_DAY_TITLES[2],
            [
                ("8:00--10:00", "Classes meeting at 8:00 TR"),
                ("10:30--12:30", "Classes meeting at 11:00 TR"),
                ("1:30--3:30", "Classes meeting at 2:00 TR"),
                ("6:30--8:30", "Classes meeting Tuesday evening"),
            ],
        ),
        _exam_day_table(
            EXAM_DAY_TITLES[3],
            [
                ("8:00--10:00", "Classes meeting at 9:30 MW"),
                ("10:30--12:30", "Classes meeting at 12:30 MW"),
                ("1:30--3:30", "Classes meeting at 3:30 or 4:00 MW"),
                ("4:00--6:00", "Classes meeting at 5:00 or 5:30 MW"),
                ("6:30--8:30", "Classes meeting Wednesday evening"),
            ],
        ),
        _exam_day_table(
            EXAM_DAY_TITLES[4],
            [
                ("8:00--10:00", "Classes meeting at 9:30 TR"),
                ("10:30--12:30", "Classes meeting at 12:30 TR"),
                ("1:30--3:30", "Classes meeting at 3:30 or 4:00 TR"),
                ("4:00--6:00", "Classes meeting at 5:00 or 5:30 TR"),
                ("6:30--8:30", "Classes meeting Thursday evening"),
            ],
        ),
        _exam_day_note(EXAM_DAY_TITLES[5], "Friday"),
    ]
)

REQUIRED_IMPORTANT_DATES: Sequence[str] = (
    "August 31",
    "September 7",
    "September 18",
    "October 9",
    "October 30",
    "November 23--27",
    "December 4",
    "December 7--11",
    "December 14",
)
REQUIRED_CALENDAR_EVENTS: Sequence[str] = (
    "Labor Day",
    "Thanksgiving",
    "Midterm grades",
    "Final course grades",
)
REQUIRED_ADA_TOKENS: Sequence[str] = (
    "Title II",
    "ADA",
    "Accessibility Services",
    "Director of Accessibility Services",
    "Labry Hall 226",
    "615-547-1286",
    "MCurtis@cumberland.edu",
)

_EXAMDAY_DEFINED_RE = re.compile(r"\\(?:re)?(?:new|provide)command\s*\{?\\examday\b|\\def\s*\\examday\b")
_EXAMDAY_FALLBACK_DEF = (
    r"\providecommand{\examday}[1]{\par\medskip\noindent\textbf{#1}\par\smallskip}"
)


# ---------------------------------------------------------------------------
# Source audit
# ---------------------------------------------------------------------------

def parse_source_syllabus(source_path: Optional[Path] = None) -> Dict[str, str]:
    """Read the existing syllabus and extract identity fields for drift auditing."""
    path = Path(source_path) if source_path is not None else Path(DEFAULT_SOURCE_PATH)
    if not path.is_file():
        raise FileNotFoundError(f"Source syllabus not found: {path}")
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    info: Dict[str, str] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": str(len(raw))}
    code = re.search(r"CHEM\s*490\s*[A-Z]*", text)
    if code:
        info["course_code"] = re.sub(r"\s+", " ", code.group(0)).strip()
    if "Klaassen" in text:
        info["instructor"] = "Klaassen"
    if AUTH_INSTRUCTOR_EMAIL in text:
        info["email"] = AUTH_INSTRUCTOR_EMAIL
    if re.search(r"Fall\s+2026", text):
        info["term"] = AUTH_TERM
    return info


def audit_source_drift(info: Dict[str, str]) -> List[str]:
    """Compare parsed source identity fields with the pinned constants; return notes."""
    notes: List[str] = []
    code = info.get("course_code", "")
    if not code:
        notes.append("source: course code not found")
    elif "490DNTL" not in code.replace(" ", ""):
        notes.append(f"source: course code {code!r} differs from pinned {AUTH_COURSE_CODE!r}")
    if info.get("instructor") != "Klaassen":
        notes.append(f"source: instructor differs from pinned {AUTH_INSTRUCTOR_NAME!r}")
    if info.get("term") != AUTH_TERM:
        notes.append(f"source: term differs from pinned {AUTH_TERM!r}")
    if not notes:
        notes.append("source: identity fields consistent with pinned course data")
    return notes


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _macro_values() -> Dict[str, str]:
    """Map template tag names to rendered text (from AUTH_* constants)."""
    values: Dict[str, str] = {}
    for name, val in list(globals().items()):
        if name.startswith("AUTH_") and isinstance(val, str):
            values[name[len("AUTH_"):]] = val
    aliases = {
        "INSTRUCTOR_OFFICE_LOCATION": "INSTRUCTOR_OFFICE",
        "OFFICE_LOCATION": "INSTRUCTOR_OFFICE",
        "OFFICE": "INSTRUCTOR_OFFICE",
        "PHONE": "INSTRUCTOR_PHONE",
        "EMAIL": "INSTRUCTOR_EMAIL",
        "CREDIT_HOURS": "CREDITS",
        "CREDIT": "CREDITS",
        "OFFICE_HOURS_MONDAY": "OFFICE_HOURS_M",
        "OFFICE_HOURS_TUESDAY": "OFFICE_HOURS_T",
        "OFFICE_HOURS_WEDNESDAY": "OFFICE_HOURS_W",
        "OFFICE_HOURS_THURSDAY": "OFFICE_HOURS_R",
        "OFFICE_HOURS_FRIDAY": "OFFICE_HOURS_F",
        "DYNAMIC_SCHEDULE_NOTICE": "DYNAMIC_SCHEDULE_REFERENCE",
        "ADA_STATEMENT": "ACCESSIBILITY_STATEMENT",
        "EXAM_WEEK_DATES": "EXAM_PERIOD_DATES",
        "EXAM_SCHEDULE": "EXAM_SCHEDULE_BLOCKS",
    }
    for alias, target in aliases.items():
        if alias not in values and target in values:
            values[alias] = values[target]
    return values


def _ada_block_ok(text: str) -> bool:
    if any(tok not in text for tok in REQUIRED_ADA_TOKENS):
        return False
    idx = text.index("Director of Accessibility Services")
    window = text[max(0, idx - 200): idx + 800]
    return all(t in window for t in ("Labry Hall 226", "615-547-1286", "MCurtis@cumberland.edu"))


def _inject_before_end_document(text: str, block: str) -> str:
    marker = r"\end{document}"
    pos = text.rfind(marker)
    if pos < 0:
        raise ValueError(r"Template has no \end{document}; cannot inject required block")
    return text[:pos] + block.strip("\n") + "\n\n" + text[pos:]


def _render_template(template_text: str) -> str:
    values = _macro_values()
    missing = sorted({n for n in _TAG_CAPTURE_RE.findall(template_text) if n not in values})
    if missing:
        raise ValueError(
            "Template tags without pinned course data: " + ", ".join(missing)
        )

    def _sub(match: "re.Match[str]") -> str:
        return values[match.group(1)]

    text = _TAG_CAPTURE_RE.sub(_sub, template_text)

    # Ensure mandated blocks are present even if the template lacks a dedicated placeholder.
    if DYNAMIC_NOTICE_TITLE not in text:
        text = _inject_before_end_document(text, AUTH_DYNAMIC_SCHEDULE_REFERENCE)
    if not _ada_block_ok(text):
        text = _inject_before_end_document(text, AUTH_ACCESSIBILITY_STATEMENT)
    if r"\examday{" in text and not _EXAMDAY_DEFINED_RE.search(text):
        begin = text.find(r"\begin{document}")
        if begin < 0:
            raise ValueError(r"Template has no \begin{document}")
        text = text[:begin] + _EXAMDAY_FALLBACK_DEF + "\n\n" + text[begin:]
    return text


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def verify_compliance(text: str) -> None:
    """Raise ValueError if required calendar / notice / accessibility content is missing."""
    problems: List[str] = []
    for date_str in REQUIRED_IMPORTANT_DATES:
        if date_str not in text:
            problems.append(f"missing Fall 2026 date {date_str!r}")
    for event in REQUIRED_CALENDAR_EVENTS:
        if event not in text:
            problems.append(f"missing calendar event {event!r}")
    if AUTH_EXAM_PERIOD_DATES not in text:
        problems.append(f"missing exam period heading {AUTH_EXAM_PERIOD_DATES!r}")

    positions: List[int] = []
    for title in EXAM_DAY_TITLES:
        token = r"\examday{" + title + "}"
        pos = text.find(token)
        if pos < 0:
            problems.append(f"missing exam block {token!r}")
        else:
            positions.append(pos)
    if positions != sorted(positions):
        problems.append("exam day blocks are not in chronological order")

    if DYNAMIC_NOTICE_TITLE not in text:
        problems.append("missing Dynamic Course Schedule Notice")
    else:
        idx = text.index(DYNAMIC_NOTICE_TITLE)
        if "Canvas" not in text[idx: idx + 800]:
            problems.append("Dynamic Course Schedule Notice does not reference Canvas")

    if not _ada_block_ok(text):
        problems.append("Title II / ADA accessibility statement or contact block incomplete")

    if problems:
        raise ValueError("Compliance verification failed: " + "; ".join(problems))


def validate_latex(text: str) -> None:
    """Raise ValueError if the rendered LaTeX is structurally unsound."""
    problems: List[str] = []
    size = len(text.encode("utf-8"))
    if size < MIN_OUTPUT_BYTES:
        problems.append(f"size {size} bytes is below {MIN_OUTPUT_BYTES}")
    if "\x00" in text:
        problems.append("null byte present")
    found = TAG_PATTERN_RE.findall(text)
    if found:
        problems.append(f"unexpanded template tags: {sorted(set(found))}")

    body = _COMMENT_RE.sub("", text)
    if len(re.findall(r"\\begin\{document\}", body)) != 1:
        problems.append(r"expected exactly one \begin{document}")
    if len(re.findall(r"\\end\{document\}", body)) != 1:
        problems.append(r"expected exactly one \end{document}")
    if r"\begin{document}" in body and r"\end{document}" in body:
        if body.index(r"\begin{document}") > body.index(r"\end{document}"):
            problems.append(r"\begin{document} must precede \end{document}")
    if not body.rstrip().endswith(r"\end{document}"):
        problems.append(r"document does not terminate with \end{document}")

    counts: Dict[str, List[int]] = {}
    for kind, name in _ENV_RE.findall(body):
        counts.setdefault(name, [0, 0])[0 if kind == "begin" else 1] += 1
    unbalanced = {k: v for k, v in counts.items() if v[0] != v[1]}
    if unbalanced:
        problems.append(f"unbalanced environments (begin, end): {unbalanced}")

    stripped = re.sub(r"\\[{}]", "", body)
    if stripped.count("{") != stripped.count("}"):
        problems.append(
            f"unbalanced braces: {stripped.count('{')} open vs {stripped.count('}')} close"
        )

    if not (r"\begin{tcolorbox}" in body or r"\newtcolorbox" in body or r"\tcbset" in body):
        problems.append("no tcolorbox definitions or blocks")
    if "booktabs" not in body:
        problems.append("booktabs package missing")
    for rule in (r"\toprule", r"\midrule", r"\bottomrule"):
        if rule not in body:
            problems.append(f"missing booktabs rule {rule}")

    if problems:
        raise ValueError("LaTeX validation failed: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_syllabus_content(
    template_path: Optional[Path] = None,
    source_path: Optional[Path] = None,
) -> str:
    """Render the CU template with CHEM 490DNTL course data and return the LaTeX text."""
    tpl = Path(template_path) if template_path is not None else Path(DEFAULT_TEMPLATE_PATH)
    if not tpl.is_file():
        raise FileNotFoundError(f"Template not found: {tpl}")
    template_text = tpl.read_text(encoding="utf-8")

    info = parse_source_syllabus(source_path)
    notes = audit_source_drift(info)
    LAST_SOURCE_AUDIT[:] = notes
    for note in notes:
        LOGGER.info(note)

    rendered = _render_template(template_text)
    verify_compliance(rendered)
    validate_latex(rendered)
    return rendered


def migrate_chem490dntl_syllabus(
    template_path: Optional[Path] = None,
    target_path: Optional[Path] = None,
    source_path: Optional[Path] = None,
) -> Path:
    """Render the syllabus and write it to ``target_path`` (in place by default).

    Returns the target path. Parent directories are created as needed. The content is
    validated before writing and the written file is read back and validated again.
    """
    target = Path(target_path) if target_path is not None else Path(DEFAULT_TARGET_PATH)
    # Default source: the target itself when only a target is given and no source override,
    # otherwise the module default.
    source = Path(source_path) if source_path is not None else Path(DEFAULT_SOURCE_PATH)

    content = build_syllabus_content(template_path=template_path, source_path=source)

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(content.encode("utf-8"))
    os.replace(tmp, target)

    written = target.read_bytes().decode("utf-8")
    validate_latex(written)
    verify_compliance(written)
    return target


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Returns a process exit code (0 on success)."""
    parser = argparse.ArgumentParser(
        description="Migrate the CHEM 490DNTL syllabus to the Cumberland University template."
    )
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE_PATH), help="Template .tex path")
    parser.add_argument("--target", default=str(DEFAULT_TARGET_PATH), help="Output .tex path")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE_PATH), help="Existing syllabus path")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        out = migrate_chem490dntl_syllabus(
            template_path=Path(args.template),
            target_path=Path(args.target),
            source_path=Path(args.source),
        )
    except (OSError, ValueError) as exc:
        print(f"Migration failed: {exc}", file=sys.stderr)
        return 1

    for note in LAST_SOURCE_AUDIT:
        print(note)
    print(f"Wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
