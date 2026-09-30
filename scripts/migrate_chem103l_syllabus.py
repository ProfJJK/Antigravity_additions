"""Automated migration script for CHEM 103L syllabus to Cumberland University template format.

This module migrates the CHEM 103L (Fundamentals of Chemistry Lab) syllabus into the
official Cumberland University institutional LaTeX template format, integrating Fall 2026
academic dates, exam schedules, ADA/Title II accessibility compliance, and dynamic Canvas notices.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional, Sequence

DEFAULT_TEMPLATE_PATH = Path("D:/Gdrive/__agentic/.sources/CU_sources/CUSyllabusTemplate.tex")
DEFAULT_TARGET_PATH = Path(
    "D:/Gdrive/_CU Teaching/2026FA CHEM103L Fundamentals of Chemistry/Syllabus/CHEM103L Syllabus.tex"
)
DEFAULT_SOURCE_PATH = DEFAULT_TARGET_PATH

PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

# Authentic CHEM 103L laboratory course data mapping dictionary
CHEM103L_DATA: dict[str, str] = {
    "COURSE_CODE": "CHEM 103L",
    "COURSE_NAME": "Fundamentals of Chemistry Lab",
    "TERM": "Fall 2026",
    "INSTRUCTOR_NAME": "Dr. Joshua Klaassen",
    "INSTRUCTOR_OFFICE": "Memorial Hall, Room 301B",
    "OFFICE_HOURS_M": "8:00--8:30 AM GHHS, 12:15--1:00 PM, 3:15--4:00 PM",
    "OFFICE_HOURS_T": "10:00--11:00 AM, 12:15--1:00 PM, 5:00--5:30 PM",
    "OFFICE_HOURS_W": "8:00--8:30 AM GHHS, 12:15--1:00 PM, 3:15--4:00 PM",
    "OFFICE_HOURS_R": "10:00--11:00 AM, 5:00--5:30 PM",
    "OFFICE_HOURS_F": "7:30--8:00 AM, 12:00--12:30 PM",
    "INSTRUCTOR_PHONE": "615-547-1247",
    "INSTRUCTOR_EMAIL": "jklaassen@cumberland.edu",
    "COURSE_DESCRIPTION": (
        "This course teaches the fundamentals of inorganic chemistry, organic chemistry, "
        "and biochemistry. It is intended for non-science majors and nursing students. "
        "Topics include measurement, matter, energy, atomic theory, ionic and covalent "
        "compounds, mole-gram conversions, chemical reactions and equations, states of "
        "matter, solutions and their properties, acids, bases, pH, organic compounds, and "
        "biological applications. \\textbf{Three hours of laboratory.} Students must take "
        "MATH 110 or MATH 111 as a co-requisite for this course if they have not already "
        "successfully completed it."
    ),
    "CREDITS": "1 Credit",
    "PREREQUISITES": "None",
    "COREQUISITES": "CHEM 103 (Fundamentals of Chemistry lecture) and MATH 110 or MATH 111.",
    "REQUIRED_MATERIALS": (
        "\\item ANSI Z87 Splash Goggles (Bookstore has the correct goggles)\n"
        "\t\t\\item Student Research Notebook\n"
        "\t\t\\item Scientific Calculator -- You must have a scientific calculator with the following functions: LOG and LN.\n"
        "\t\t\\item Laptop"
    ),
    "LEARNING_OUTCOMES": (
        "\\item Understand and solve problems requiring knowledge and application of: measurement, matter, energy, atomic theory, states of matter, ionic and covalent compounds, and mole-gram conversions.\n"
        "\t\t\\item Understand and solve problems requiring knowledge and application of: organic compounds, chemical reactions and equations.\n"
        "\t\t\\item Understand and solve problems requiring knowledge and application of: solutions and their properties, acids, bases, and pH.\n"
        "\t\t\\item Understand and solve problems requiring knowledge and application of: biological applications.\n"
        "\t\t\\item Apply these concepts in a laboratory environment.\n"
        "\t\t\\item \\textbf{General Education Student Learning Outcome:} The student will examine the world by interpreting data, applying mathematical reasoning and methods to solve problems, and connecting to principles of the natural sciences."
    ),
    "EVALUATION_DESCRIPTION": (
        "You will be evaluated by means of quizzes, laboratory activities and worksheets, "
        "and lab notebooks. They will be weighted as follows for your final grade in the course:"
    ),
    "GRADE_BREAKDOWN_ROWS": (
        "Lab Safety & 10\\% \\\\\n"
        "\t\t\tCheck-in/out & 10\\% \\\\\n"
        "\t\t\tAttendance & 10\\% \\\\\n"
        "\t\t\tLab Worksheets and Active Participation & 60\\% \\\\\n"
        "\t\t\tLab Notebook & 10\\% \\\\"
    ),
    "GRADE_TOTAL": "100",
    "GRADE_SCALE_ROWS": (
        "A  & $\\ge$ 90\\% \\\\\n"
        "\t\t\tB+ & 87--89\\% \\\\\n"
        "\t\t\tB  & 80--86\\% \\\\\n"
        "\t\t\tC+ & 77--79\\% \\\\\n"
        "\t\t\tC  & 70--76\\% \\\\\n"
        "\t\t\tD  & 60--69\\% \\\\\n"
        "\t\t\tF  & $<$ 60\\% \\\\"
    ),
    "ASSESSMENT_DESCRIPTIONS": (
        "\\noindent \\textbf{Lab Safety (10\\%):} There are a series of modules on Canvas which you must complete the first week before you will be allowed to take part in experiments.\n\n"
        "\t\\vspace{0.5em}\n"
        "\t\\noindent \\textbf{Check-in/out (10\\%):} During the beginning and end of lab these are quizzes on Canvas that guide you through how to properly check in and then check out of the laboratory. This process requires your physical presence in the lab.\n\n"
        "\t\\vspace{0.5em}\n"
        "\t\\noindent \\textbf{Attendance (10\\%):} Attending the lab. Not attending the lab during an in-person lab will mean you cannot complete the check in/out and worksheet. With proper documentation submitted in place of the lab notebook: 1 lab will be dropped and 2 of the labs can have their points replaced with make-up quizzes. This will require proof of a university approved absence submitted in place of the lab notebook assignment for that week. Please note that any labs that do not have a lab notebook assignment can in fact be completed online or from home and so do not need this process. Additionally, the excusal for this lab should be submitted by the regular due date of the lab notebooks (typically the day before the next lab session). Failure to submit the excuse may constitute the inability to be excused from the lab. (University policy is to notify the professor BEFORE the excused absence).\n\n"
        "\t\\vspace{0.5em}\n"
        "\t\\noindent \\textbf{Lab Worksheets and Active Participation in Lab (60\\%):} There will be an assignment which can be found on Canvas detailing the activities we will do during lab for each week. This can include some combination of Canvas quiz and/or PDF. If there is a PDF for that week you will need to print, read, and complete any activities listed as pre-lab or needed before lab. Come with this set of activities completed before the lab time. During the lab time your professor will have a short explanation of safety concerns, tips, and modifications that will need to be recorded in your lab notebook. You will then actively participate in the experiment being performed that week. After you are done you should have your ``Report Sheets'' filled out. Once completed you should then hand in the sheet at the end of the lab time. If the assignment is a quiz these are meant to be completed in groups during the lab time with aid from the Professor and sometimes items in the lab. They are, however, often possible to complete outside the lab and thus will be allowed to complete outside of lab if there are compelling reasons.\n\n"
        "\t\\vspace{0.5em}\n"
        "\t\\noindent \\textbf{Lab Notebook (10\\%):} Lab notebooks will be submitted through Canvas and checked throughout the semester. This is an iterative learning process, please don't be stressed as primarily an honest effort is what is desired. These will be submitted on Canvas as PDF scans using the CamScanner app."
    ),
    "COURSE_POLICIES": (
        "\\subsection*{General Attendance Policy}\n"
        "\tAttendance at labs and lectures is vital for your ability to interact with this subject matter. Search engines and AI will have little to no help to offer students if they are not interacting with this course. Your record for attendance will be kept using the check-in/out process.\n\n"
        "\tA student may not be penalized for absences resulting from required participation in University activities such as, but not limited to, athletic competition, band and choir performances, field trips, and conferences if the instructor is made aware prior to the absence. No excused absence will be accepted after the event. Practice associated with University activities is not included. Consideration of absences for illness, emergencies, and other non-sanctioned activities will be at the discretion of the instructor. Exams missed for excused absences can be taken at a time convenient to the instructor.\n\n"
        "\t\\subsection*{Statement on Health Concerns}\n"
        "\tAs we are all aware, the COVID-19 virus continues to circulate in our communities. At the same time, other communicable illnesses are also present. Members of the Cumberland University community are asked to help maintain a campus that is safe for all by undertaking appropriate health precautions. If you experience symptoms of COVID-19, the flu, or other ailments, I encourage you to seek medical attention through the University Health Services (\\href{mailto:healthservices@cumberland.edu}{\\nolinkurl{healthservices@cumberland.edu}}) or your personal health provider.\n\n"
        "\tStudents may be excused from class attendance with a note from University Health Services or a private health provider, though anyone receiving an excused absence will continue to be responsible for completing all required course assignments as outlined in the attendance policy above.\n\n"
        "\t\\subsection*{Academic Integrity}\n"
        "\tPlease note that the basis for academic integrity is that assignments, exams, papers, etc. are all used to measure the student's progress in understanding and practicing concepts they should be improving through the course. A subversion of this process through not submitting work that was done through the student's own abilities negates the ability of the professor to measure progress in the course. The basis of academic integrity is displaying the current state of your own abilities. Therefore, on assignments the work must be done by the student. Students can and should discuss problems and help each other better understand problems but the student should always submit work they themselves have done even though they may have had help noticing issues.\n\n"
        "\tExams will be clearly outlined in the limits of what students can use to help themselves but unless expressly allowed the student and the equipment necessary to answer the exam are the only things implicitly allowed.\n\n"
        "\tFor written responses like essays, lab notebooks, scientific papers/reports, or posters: These will require the use of references and quotes of various types. The references used should be clearly indicated both in their use and where they are specifically applied. The work submitted should be the student's work in structure and conception. The references should be used to supplement and inform the student's own work.\n\n"
        "\tPlease check the Cumberland University Academic Integrity Policy at the following link:\\\\\n"
        "\t\\url{https://cumberland.smartcatalogiq.com/en/current-catalog/current-catalog/academic-affairs/academic-integrity-policy/}\n\n"
        "\t\\subsection*{Artificial Intelligence Policy}\n"
        "\tThe use of AI of any type is allowed but should be treated as material generated by someone else. That means the AI should be referenced as a source and quoted where appropriate. If using Copilot or similar integrated AI in a word processor or other such programs (like Grammarly, etc.), then the use of such a program should be indicated somewhere in the beginning of the assignment. The submitted work should also still be structured according to the student's intent and understanding. Don't let the AI guide you, you should be guiding the AI which helps flesh out what you are writing. Also please be extremely careful in the use of AI which still tends to hallucinate in a convincing (to most students) way topics which even a cursory search would have disproven.\n\n"
        "\t\\subsection*{Translation Devices}\n"
        "\tLearning science in a non-native language presents challenges that can be mitigated through the use of translation devices, facilitating the translation of English terms into a student's native language. Students who require translation support during an exam are permitted to use a paper-based, ``word-for-word'' dictionary. This dictionary should only offer direct translations of words without providing definitions. It must be submitted to the instructor for approval before every exam, and the instructor has the final say on whether it is appropriate for use. Students are advised to consult with their instructors well before the exam date to ensure their dictionary complies with departmental standards. The use of Google Translate or any other online translation tools is strictly prohibited during in-person exams, as instructors cannot effectively oversee the use of such digital resources. For exclusively online courses, students are allowed to use a designated word-to-word translation website, as approved by the instructor, for proctored assessments.\n\n"
        "\t\\subsection*{Student Conduct}\n"
        "\tIt is expected that students will conduct themselves in a manner that promotes learning by themselves and others in the class. Respect for the instructor and fellow classmates is paramount to creating a productive learning environment. Class participation is desired even under unusual circumstances! Students are encouraged to ask any questions at any time during class, as long as they are relevant to the topic of lecture or previous lectures.\n\n"
        "\tIf for any reason a student is dismissed due to conduct, dismissal from class will result in a 10\\% penalty on the final course grade. If a student is dismissed twice for any reason, i.e. disruptive behavior, disrespect for instructor or classmates, etc., there will not be a third. The student will not be allowed to rejoin the class either in person or online.\n\n"
        "\t\\subsection*{Policy on Electronic Devices}\n"
        "\tCell phones will be turned off or placed on silent (not vibrate) at the beginning of the class. Cell phones should not be used during class, and use of a phone during Plicker questions will result in a zero for the daily assignment. The use of earbuds is strictly prohibited. If your phone rings, dings, beeps, boops during class, you will be awkwardly looked at by everyone in the class and slightly embarrassed.\n\n"
        "\tIf there is a situation that requires a phone to be on during class, please report the matter to the instructor. Repeated failure to silence phones or answering calls and text during lecture will result in dismissal from lecture for the day. The instructor may ask students at any time to turn off and store devices during lecture. Failure to comply will result in dismissal from lecture for the day."
    ),
    "DYNAMIC_SCHEDULE_REFERENCE": (
        "\\vspace{0.8em}\n"
        "\t\\noindent \\textbf{Dynamic Course Schedule Notice:} Please note that while a tentative schedule is outlined below, the authoritative dynamic schedule is maintained in the Canvas course shell. Students are expected to consult Canvas regularly for real-time announcements, assignment due dates, pacing adjustments, and supplementary learning modules."
    ),
    "COURSE_SCHEDULE_ROWS": (
        "1  & 00 & Intro and Safety, Online \\\\\n"
        "\t\t\t2  & 01 & Math and Science Review, MH302 in Lab Quiz \\\\\n"
        "\t\t\t3  & 02 & Making Measurements, MH302 in Lab Experiment \\\\\n"
        "\t\t\t4  & 03 & Physical Properties of Element and Flame Test, MH302 in Lab Experiment \\\\\n"
        "\t\t\t5  & 04 & Limiting Reactant--Making Sidewalk chalk, MH302 in Lab Experiment \\\\\n"
        "\t\t\t6  & 05 & Models, Lewis Structures, and Organic Structures, MH302 in Lab Quiz + Model Kits \\\\\n"
        "\t\t\t7  & 06 & Molecular Architecture, MH302 in Lab Quiz + Model Kits \\\\\n"
        "\t\t\t8  & 07 & Part a, Density -- Determining the Sugar Content of Beverages, MH302 in Lab Exp \\\\\n"
        "\t\t\t9  & 07 & Part b, Density -- Determining the Sugar Content of Beverages, MH302 in Lab Exp + Laptop \\\\\n"
        "\t\t\t10 & 08 & Sampling, Demo and Gathering Samples, Outside Lab \\\\\n"
        "\t\t\t11 & 09 & pH Testing Samples, Outside Lab \\\\\n"
        "\t\t\t12 & -- & \\textbf{\\textcolor{CURed}{Colloquium, Attend and Fill in Online Quiz}} \\\\\n"
        "\t\t\t13 & 10 & Scientific Method Exploration -- Fireworks, MH302 in Lab Exp \\\\\n"
        "\t\t\t14 & -- & \\textbf{\\textcolor{CURed}{Thanksgiving (No Lab)}} \\\\\n"
        "\t\t\t15 & -- & \\textbf{\\textcolor{CURed}{Make-up Quizzes, Online}} \\\\\n"
        "\t\t\t16 & -- & \\textbf{\\textcolor{CURed}{No Lab, Finals Week}} \\\\"
    ),
    "IMPORTANT_DATES": (
        "\\item \\textbf{August 31}: Last day to register or add/drop classes with no entry on your record.\n"
        "\t\t\\item \\textbf{September 7}: Labor Day Holiday (No Classes).\n"
        "\t\t\\item \\textbf{September 18}: Last day to drop a class with a grade of W.\n"
        "\t\t\\item \\textbf{October 9}: Midterm grades are due to be submitted in the Student First portal.\n"
        "\t\t\\item \\textbf{October 30}: Last day to drop a class (with a grade of WP or WF).\n"
        "\t\t\\item \\textbf{November 23--27}: Thanksgiving Holidays (No Classes; University Offices closed November 26--27).\n"
        "\t\t\\item \\textbf{December 4}: Last day of classes.\n"
        "\t\t\\item \\textbf{December 7--11}: Final examination week (see detailed schedule below).\n"
        "\t\t\\item \\textbf{December 14}: Final grades are due to be submitted in the Student First portal."
    ),
    "EXAM_PERIOD_DATES": "DECEMBER 7--11, 2026",
    "EXAM_SCHEDULE_BLOCKS": (
        "\\examday{Saturday, December 5, 2026}\n"
        "\t\\begin{center}\n"
        "\t\t\\textit{*** Any lab or class that meets exclusively on Saturday will hold the final exam at the time normally scheduled for this class.}\n"
        "\t\\end{center}\n\n"
        "\t\\examday{Monday, December 7, 2026}\n"
        "\t\\begin{center}\n"
        "\t\t\\renewcommand{\\arraystretch}{1.3}\n"
        "\t\t\\begin{tabular}{@{}ll@{}}\n"
        "\t\t\t8:00--10:00 & Classes meeting at 8:00 MW \\\\\n"
        "\t\t\t10:30--12:30 & Classes meeting at 11:00 MW \\\\\n"
        "\t\t\t1:30--3:30 & Classes meeting at 2:00 MW \\\\\n"
        "\t\t\t6:30--8:30 & Classes meeting Monday evening \\\\\n"
        "\t\t\\end{tabular}\n"
        "\t\\end{center}\n\n"
        "\t\\examday{Tuesday, December 8, 2026}\n"
        "\t\\begin{center}\n"
        "\t\t\\renewcommand{\\arraystretch}{1.3}\n"
        "\t\t\\begin{tabular}{@{}ll@{}}\n"
        "\t\t\t8:00--10:00 & Classes meeting at 8:00 TR \\\\\n"
        "\t\t\t10:30--12:30 & Classes meeting at 11:00 TR \\\\\n"
        "\t\t\t1:30--3:30 & Classes meeting at 2:00 TR \\\\\n"
        "\t\t\t6:30--8:30 & Classes meeting Tuesday evening \\\\\n"
        "\t\t\\end{tabular}\n"
        "\t\\end{center}\n\n"
        "\t\\examday{Wednesday, December 9, 2026}\n"
        "\t\\begin{center}\n"
        "\t\t\\renewcommand{\\arraystretch}{1.3}\n"
        "\t\t\\begin{tabular}{@{}ll@{}}\n"
        "\t\t\t8:00--10:00 & Classes meeting at 9:30 MW \\\\\n"
        "\t\t\t10:30--12:30 & Classes meeting at 12:30 MW \\\\\n"
        "\t\t\t1:30--3:30 & Classes meeting at 3:30 or 4:00 MW \\\\\n"
        "\t\t\t4:00--6:00 & Classes meeting at 5:00 or 5:30 MW \\\\\n"
        "\t\t\t6:30--8:30 & Classes meeting Wednesday evening \\\\\n"
        "\t\t\\end{tabular}\n"
        "\t\\end{center}\n\n"
        "\t\\examday{Thursday, December 10, 2026}\n"
        "\t\\begin{center}\n"
        "\t\t\\renewcommand{\\arraystretch}{1.3}\n"
        "\t\t\\begin{tabular}{@{}ll@{}}\n"
        "\t\t\t8:00--10:00 & Classes meeting at 9:30 TR \\\\\n"
        "\t\t\t10:30--12:30 & Classes meeting at 12:30 TR \\\\\n"
        "\t\t\t1:30--3:30 & Classes meeting at 3:30 or 4:00 TR \\\\\n"
        "\t\t\t4:00--6:00 & Classes meeting at 5:00 or 5:30 TR \\\\\n"
        "\t\t\t6:30--8:30 & Classes meeting Thursday evening \\\\\n"
        "\t\t\\end{tabular}\n"
        "\t\\end{center}\n\n"
        "\t\\examday{Friday, December 11, 2026}\n"
        "\t\\begin{center}\n"
        "\t\t\\textit{*** Any lab or class that meets exclusively on Friday will hold the final exam at the time normally scheduled for this class.}\n"
        "\t\\end{center}"
    ),
}


def build_syllabus_content(
    template_path: Optional[str | Path] = None,
    source_path: Optional[str | Path] = None,
) -> str:
    """Read Cumberland University template and populate placeholders with CHEM 103L data.

    Parameters
    ----------
    template_path : Optional[str | Path]
        Path to the CUSyllabusTemplate.tex file. Defaults to DEFAULT_TEMPLATE_PATH.
    source_path : Optional[str | Path]
        Optional path to the source syllabus. Defaults to DEFAULT_SOURCE_PATH.

    Returns
    -------
    str
        Fully rendered LaTeX text with all placeholders replaced and zero stubs.
    """
    resolved_template = Path(template_path) if template_path is not None else DEFAULT_TEMPLATE_PATH
    if not resolved_template.is_file():
        raise FileNotFoundError(f"CU template not found at {resolved_template}")

    content = resolved_template.read_text(encoding="utf-8")

    # If source path is provided and exists, verify readability
    if source_path is not None:
        src_path = Path(source_path)
        if src_path.is_file():
            _ = src_path.read_text(encoding="utf-8")

    for key, value in CHEM103L_DATA.items():
        tag = f"{{{{{key}}}}}"
        content = content.replace(tag, value)

    unexpanded = PLACEHOLDER_RE.findall(content)
    if unexpanded:
        raise ValueError(f"Unexpanded placeholders remain in rendered syllabus: {set(unexpanded)}")

    return content


def migrate_chem103l_syllabus(
    template_path: Optional[str | Path] = None,
    target_path: Optional[str | Path] = None,
    source_path: Optional[str | Path] = None,
) -> Path:
    """Render the migrated syllabus and update the target file in place.

    Parameters
    ----------
    template_path : Optional[str | Path]
        Path to the template LaTeX file. Defaults to DEFAULT_TEMPLATE_PATH.
    target_path : Optional[str | Path]
        Path to the target output LaTeX file. Defaults to DEFAULT_TARGET_PATH.
    source_path : Optional[str | Path]
        Optional path to the source syllabus. Defaults to DEFAULT_SOURCE_PATH.

    Returns
    -------
    Path
        Path to the written target syllabus file.
    """
    resolved_target = Path(target_path) if target_path is not None else DEFAULT_TARGET_PATH
    rendered_text = build_syllabus_content(template_path=template_path, source_path=source_path)

    resolved_target.parent.mkdir(parents=True, exist_ok=True)
    resolved_target.write_text(rendered_text, encoding="utf-8")
    return resolved_target


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint for migrating the CHEM 103L syllabus.

    Parameters
    ----------
    argv : Optional[Sequence[str]]
        Command-line arguments. If None, uses sys.argv[1:].

    Returns
    -------
    int
        0 on success, non-zero on failure.
    """
    parser = argparse.ArgumentParser(
        description="Migrate CHEM 103L syllabus to Cumberland University template format."
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
        default=str(DEFAULT_TARGET_PATH),
        help="Path to output CHEM103L Syllabus.tex",
    )
    parser.add_argument(
        "--source",
        dest="source_path",
        default=str(DEFAULT_SOURCE_PATH),
        help="Path to source syllabus",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    migrate_chem103l_syllabus(
        template_path=args.template_path,
        target_path=args.target_path,
        source_path=args.source_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
