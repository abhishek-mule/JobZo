#!/usr/bin/env python3
"""Resume Generator — reads master YAML data and generates ATS-optimized PDF."""

import os
import sys
import yaml
from pathlib import Path

from fpdf import FPDF

MASTER_DIR = Path(__file__).parent / "master"
TEMPLATES_DIR = Path(__file__).parent / "templates"
OUTPUT_DIR = Path(__file__).parent / "generated"
ROOT = Path(__file__).parent.parent

os.makedirs(OUTPUT_DIR, exist_ok=True)


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def load_master(name):
    return load_yaml(MASTER_DIR / name)


def load_projects():
    projects = []
    proj_dir = MASTER_DIR / "projects"
    for f in sorted(os.listdir(proj_dir)):
        if f.endswith(".yaml") or f.endswith(".yml"):
            projects.append(load_yaml(proj_dir / f))
    return projects


FONT_DIR = "/usr/share/fonts/truetype/noto"


class ResumePDF(FPDF):
    def __init__(self):
        super().__init__("P", "mm", "A4")
        self.set_auto_page_break(auto=True, margin=15)
        self.add_font("Noto", "", FONT_DIR + "/NotoSans-Regular.ttf", uni=True)
        self.add_font("Noto", "B", FONT_DIR + "/NotoSans-Bold.ttf", uni=True)
        self.add_font("Noto", "I", FONT_DIR + "/NotoSans-Italic.ttf", uni=True)
        self.add_font("Noto", "BI", FONT_DIR + "/NotoSans-BoldItalic.ttf", uni=True)
        self.add_page()

    def header_block(self, profile):
        self.set_font("Noto", "B", 22)
        self.cell(0, 8, profile["name"].upper(), new_x="LMARGIN", new_y="NEXT")
        self.set_font("Noto", "", 11)
        self.set_text_color(80, 80, 80)
        self.cell(0, 5, profile["headline"], new_x="LMARGIN", new_y="NEXT")
        self.ln(2)
        self.set_font("Noto", "", 9)
        self.set_text_color(60, 60, 60)
        contact = f'{profile["email"]}  |  {profile["phone"]}  |  {profile["location"]}'
        self.cell(0, 4, contact, new_x="LMARGIN", new_y="NEXT")
        links = profile.get("linkedin", "") + "  |  " + profile.get("github", "")
        if profile.get("portfolio"):
            links += "  |  " + profile["portfolio"]
        self.cell(0, 4, links, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)

    def section_heading(self, title):
        self.ln(4)
        self.set_font("Noto", "B", 11)
        self.set_fill_color(230, 230, 230)
        self.cell(0, 6, "  " + title.upper(), new_x="LMARGIN", new_y="NEXT", fill=True)
        self.ln(2)

    def summary_block(self, text):
        self.set_font("Noto", "", 9.5)
        self.multi_cell(0, 4.5, text)
        self.ln(1)

    def experience_block(self, exp):
        self.set_font("Noto", "B", 10)
        self.cell(0, 5, exp["role"] + " - " + exp["company"], new_x="LMARGIN", new_y="NEXT")
        self.set_font("Noto", "I", 9)
        self.set_text_color(100, 100, 100)
        dates = f"{exp['start_date']} - {exp['end_date']}"
        self.cell(0, 4, dates, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)
        self.set_font("Noto", "", 9.5)
        for h in exp["highlights"]:
            self.cell(4)
            self.cell(0, 4.5, "- " + h, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def project_block(self, proj):
        self.set_font("Noto", "B", 10)
        self.cell(0, 5, proj["title"], new_x="LMARGIN", new_y="NEXT")
        self.set_font("Noto", "I", 9)
        self.set_text_color(100, 100, 100)
        self.cell(0, 4, proj["description"], new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)
        self.set_font("Noto", "", 9.5)
        tech_str = ", ".join(proj["technologies"])
        self.cell(4)
        self.set_font("Noto", "B", 9)
        self.cell(0, 4.5, "Tech: ", new_x="LMARGIN", new_y="NEXT")
        self.set_x(self.get_x() + 4)
        self.set_font("Noto", "", 9)
        self.cell(0, 4.5, tech_str, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)
        self.set_font("Noto", "", 9.5)
        for pt in proj["resume_points"]:
            self.cell(4)
            self.cell(0, 4.5, "- " + pt, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def skills_block(self, skills):
        groups = [
            ("Languages", [s["name"] for s in skills["languages"]]),
            ("Backend", skills["backend"]),
            ("Frontend", skills["frontend"]),
            ("Databases", skills["databases"]),
            ("Tools", skills["tools"]),
        ]
        col_w = 90
        start_x = self.get_x()
        for i, (label, items) in enumerate(groups):
            if i % 2 == 0:
                x = start_x
                y_before = self.get_y()
                self.set_xy(x, y_before)
            else:
                x = start_x + col_w + 5
                y_after = self.get_y()
                self.set_xy(x, max(y_before, y_after))

            self.set_font("Noto", "B", 9)
            self.cell(col_w, 4.5, label, new_x="LMARGIN", new_y="NEXT")
            self.set_x(self.get_x() if i % 2 == 0 else x)
            self.set_font("Noto", "", 9)
            line = ", ".join(items)
            self.multi_cell(col_w, 4, line)
            self.ln(1)

    def achievements_block(self, achievements):
        col_w = 90
        start_x = self.get_x()
        for i, ach in enumerate(achievements):
            if i % 2 == 0:
                x = start_x
                y_before = self.get_y()
                self.set_xy(x, y_before)
            else:
                x = start_x + col_w + 5
                y_after = self.get_y()
                self.set_xy(x, max(y_before, y_after))

            self.set_font("Noto", "B", 9)
            self.cell(col_w, 4.5, ach["title"], new_x="LMARGIN", new_y="NEXT")
            self.set_x(self.get_x() if i % 2 == 0 else x)
            self.set_font("Noto", "", 8.5)
            self.multi_cell(col_w, 3.5, ach["detail"])
            self.ln(0.5)

    def education_block(self, edu):
        self.set_font("Noto", "B", 10)
        self.cell(0, 5, edu["degree"], new_x="LMARGIN", new_y="NEXT")
        self.set_font("Noto", "I", 9)
        self.set_text_color(100, 100, 100)
        line = edu["institution"]
        if edu.get("expected_graduation"):
            line += "  |  Expected " + edu["expected_graduation"]
        self.cell(0, 4, line, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(1)
        if edu.get("coursework"):
            self.set_font("Noto", "B", 9)
            self.cell(4)
            self.cell(0, 4, "Relevant Coursework: ", new_x="LMARGIN", new_y="NEXT")
            self.set_x(self.get_x() + 4)
            self.set_font("Noto", "", 9)
            self.cell(0, 4, ", ".join(edu["coursework"]), new_x="LMARGIN", new_y="NEXT")
        self.ln(2)


def generate_resume(variant="backend", output_path=None):
    profile = load_master("profile.yaml")
    education = load_master("education.yaml")
    experience = load_master("experience.yaml")
    skills = load_master("skills.yaml")
    achievements = load_master("achievements.yaml")
    projects = load_projects()

    if output_path is None:
        output_path = OUTPUT_DIR / f"{variant}.pdf"

    pdf = ResumePDF()

    # Header
    pdf.header_block(profile)

    # Summary
    pdf.section_heading("Summary")
    pdf.summary_block(profile["summary"])

    # Experience
    pdf.section_heading("Experience")
    for exp in experience:
        pdf.experience_block(exp)

    # Projects
    pdf.section_heading("Projects")
    for proj in projects:
        pdf.project_block(proj)

    # Education
    pdf.section_heading("Education")
    for edu in education:
        pdf.education_block(edu)

    # Skills & Achievements side by side
    pdf.ln(2)
    y_skills = pdf.get_y()

    pdf.set_xy(10, y_skills)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(90, 6, "  TECHNICAL SKILLS", fill=True)

    pdf.set_xy(105, y_skills)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_fill_color(230, 230, 230)
    pdf.cell(95, 6, "  ACHIEVEMENTS", fill=True)

    pdf.ln(6)
    pdf.set_xy(10, pdf.get_y())
    pdf.skills_block(skills)

    # achievements go on the right
    ach_x = 105
    ach_y = y_skills + 6
    pdf.set_xy(ach_x, ach_y)
    pdf.achievements_block(achievements)

    pdf.output(str(output_path))
    return output_path


if __name__ == "__main__":
    variant = sys.argv[1] if len(sys.argv) > 1 else "backend"
    out = sys.argv[2] if len(sys.argv) > 2 else None
    path = generate_resume(variant, out)
    print(f"Generated: {path}")
