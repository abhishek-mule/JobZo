from fpdf import FPDF
import json, os

FONT_DIR = "/usr/share/fonts/truetype/dejavu"

class ResumePDF(FPDF):
    def __init__(self, target_role, emphasis_skills, deemphasis_skills=None):
        super().__init__()
        self.target_role = target_role
        self.emphasis_skills = emphasis_skills
        self.deemphasis_skills = deemphasis_skills or []
        self.set_auto_page_break(auto=True, margin=20)
        self.add_font("DejaVu", "", os.path.join(FONT_DIR, "DejaVuSans.ttf"), uni=True)
        self.add_font("DejaVu", "B", os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf"), uni=True)
        self.add_font("DejaVu", "I", os.path.join(FONT_DIR, "DejaVuSans-Oblique.ttf"), uni=True)
        self.add_font("DejaVu", "BI", os.path.join(FONT_DIR, "DejaVuSans-BoldOblique.ttf"), uni=True)

    def header_block(self):
        self.set_font("DejaVu", "B", 16)
        self.cell(0, 8, "ABHISHEK MULE", align="L", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "I", 9)
        self.cell(0, 5, self.target_role, align="L", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "", 8)
        contact = "Phone: 9371343891 | Email: abhimule2709@gmail.com"
        self.cell(0, 5, contact, align="L", new_x="LMARGIN", new_y="NEXT")
        links = "GitHub: https://github.com/abhishek-mule | LinkedIn: https://www.linkedin.com/in/abhishek-mule-4706b9292/"
        self.cell(0, 5, links, align="L", new_x="LMARGIN", new_y="NEXT")
        self.line(10, self.get_y() + 2, 200, self.get_y() + 2)
        self.ln(4)

    def section(self, title):
        self.set_font("DejaVu", "B", 10)
        self.set_text_color(0, 0, 0)
        self.cell(0, 6, title.upper(), align="L", new_x="LMARGIN", new_y="NEXT")
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(2)

    def bullet(self, text):
        self.set_font("DejaVu", "", 8.5)
        self.set_text_color(40, 40, 40)
        self.cell(5, 4.5, "\u2022", align="L")
        self.multi_cell(0, 4.5, text, align="L")
        self.ln(0.5)

    def entry(self, title, subtitle, date, bullets):
        self.set_font("DejaVu", "B", 9)
        self.set_text_color(0, 0, 0)
        self.cell(0, 5.5, title, align="L", new_x="LMARGIN", new_y="NEXT")
        self.set_font("DejaVu", "I", 8)
        self.set_text_color(80, 80, 80)
        self.cell(0, 4.5, f"{subtitle} | {date}", align="L", new_x="LMARGIN", new_y="NEXT")
        for b in bullets:
            self.bullet(b)

    def skill_row(self, label, items):
        self.set_font("DejaVu", "B", 8.5)
        self.set_text_color(0, 0, 0)
        w = self.get_string_width(label) + 3
        self.cell(w, 4.5, label, align="L")
        self.set_font("DejaVu", "", 8.5)
        self.set_text_color(40, 40, 40)
        self.multi_cell(self.w - self.l_margin - self.r_margin - w, 4.5, items, align="L")


def backend_resume():
    pdf = ResumePDF(
        "BACKEND ENGINEER | JAVA \u2022 SPRING BOOT \u2022 DISTRIBUTED SYSTEMS",
        emphasis_skills=["Java", "Spring Boot", "PostgreSQL", "Docker", "REST APIs", "Git",
                         "Linux", "Microservices", "Kafka", "Redis", "Distributed Systems"],
    )
    pdf.add_page()
    pdf.header_block()
    pdf.section("Profile")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 5, (
        "Backend-focused Software Engineer experienced in JVM-based systems, real-time APIs, "
        "and performance-critical backend services. Strong in Java, Spring Boot, Python, FastAPI, "
        "and distributed systems, with hands-on experience in tracing, low-latency systems, "
        "and production debugging."
    ))
    pdf.ln(3)

    pdf.section("Experience")
    pdf.entry(
        "Vahanfin Solutions Pvt. Ltd.", "Full Stack Intern",
        "Remote (Mumbai, India) | Feb 2025 - May 2025",
        [
            "Engineered a modular AI video generation platform enabling natural language to animation using Python, FastAPI, Remotion, FFmpeg, and Manim.",
            "Designed an extensible architecture supporting plug-and-play model upgrades (OpenAI, Mistral, custom LLMs).",
        ]
    )
    pdf.ln(2)

    pdf.section("Key Projects")
    pdf.entry(
        "Real-Time Trading Platform \u2014 Spring Boot Backend System",
        "Personal Project",
        "Sep 2025 - Oct 2025",
        [
            "Developed a low-latency trading backend using Java and Spring Boot, supporting real-time market data updates and concurrent order handling.",
            "Optimized database access using PostgreSQL with Redis caching, significantly reducing query latency and improving response times for live price feeds.",
        ]
    )
    pdf.ln(1)
    pdf.entry(
        "VTracer (Java Agent \u2013 JVM Performance Tracing)",
        "Personal Project",
        "Nov 2025 - Dec 2025",
        [
            "Built a JVM bytecode instrumentation agent using java.lang.instrument.Instrumentation and ClassFileTransformer to trace method latency, call stacks, and thread context with <2% overhead in live Spring Boot services.",
            "Used the tool to debug real production bottlenecks (blocking I/O, thread contention, deep call chains) without adding logs or modifying application code.",
        ]
    )
    pdf.ln(1)
    pdf.entry(
        "OmniVid Lite \u2014 AI-Powered Text-to-Video Generation Engine",
        "Personal Project",
        "Oct 2025 - Present",
        [
            "Engineered a modular AI video generation platform enabling natural language to animation using Python, FastAPI, Remotion, FFmpeg, and Manim.",
            "Designed an extensible architecture supporting plug-and-play model upgrades (OpenAI, Mistral, custom LLMs).",
        ]
    )
    pdf.ln(2)

    pdf.section("Education")
    pdf.entry(
        "Priyadarshini College Of Engineering, Hingna, Nagpur",
        "B.Tech in Computer Technology",
        "C.G.P.A : 8.88",
        []
    )
    pdf.ln(2)

    pdf.section("Technical Skills")
    pdf.skill_row("Languages:", "Python, Java, JavaScript/TypeScript")
    pdf.skill_row("Backend & Frameworks:", "Spring Boot, FastAPI, Node.js, Next.js")
    pdf.skill_row("Databases & Caching:", "PostgreSQL, Redis")
    pdf.skill_row("Distributed Systems & Messaging:", "WebSockets, Kafka, Celery, RabbitMQ")
    pdf.skill_row("Cloud & DevOps:", "Docker, Kubernetes, Linux, Git, AWS, Google Cloud")

    return pdf


def fullstack_resume():
    pdf = ResumePDF(
        "FULL STACK DEVELOPER | REACT \u2022 SPRING BOOT \u2022 TYPESCRIPT",
        emphasis_skills=["Spring Boot", "React", "TypeScript", "PostgreSQL", "JavaScript",
                         "Docker", "REST APIs", "Git", "Next.js", "Node.js", "FastAPI"],
    )
    pdf.add_page()
    pdf.header_block()
    pdf.section("Profile")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 5, (
        "Full Stack Software Engineer experienced in building end-to-end web applications "
        "with React, TypeScript, Next.js, and Spring Boot. Adept at designing scalable APIs, "
        "optimizing database performance, and delivering production-ready features in remote team environments."
    ))
    pdf.ln(3)

    pdf.section("Experience")
    pdf.entry(
        "Vahanfin Solutions Pvt. Ltd.", "Full Stack Intern",
        "Remote (Mumbai, India) | Feb 2025 - May 2025",
        [
            "Engineered a modular AI video generation platform enabling natural language to animation using Python, FastAPI, Remotion, FFmpeg, and Manim.",
            "Designed an extensible architecture supporting plug-and-play model upgrades (OpenAI, Mistral, custom LLMs).",
        ]
    )
    pdf.ln(2)

    pdf.section("Key Projects")
    pdf.entry(
        "Fleet Management System",
        "Personal Project",
        "Ongoing",
        [
            "Developed a production-ready Fleet Management system using Next.js, enabling vehicle tracking, service logging, and operational workflow management for FRC services.",
        ]
    )
    pdf.ln(1)
    pdf.entry(
        "OmniVid Lite \u2014 AI-Powered Text-to-Video Generation Engine",
        "Personal Project",
        "Oct 2025 - Present",
        [
            "Built a full-stack AI video generation platform with Python/FastAPI backend and React frontend, integrating Remotion for programmatic video rendering.",
            "Designed a plug-and-play architecture supporting multiple LLM providers (OpenAI, Mistral, custom).",
        ]
    )
    pdf.ln(1)
    pdf.entry(
        "Real-Time Trading Platform \u2014 Spring Boot Backend System",
        "Personal Project",
        "Sep 2025 - Oct 2025",
        [
            "Developed a low-latency trading backend using Java and Spring Boot, supporting real-time market data updates and concurrent order handling.",
            "Optimized database access using PostgreSQL with Redis caching.",
        ]
    )
    pdf.ln(2)

    pdf.section("Education")
    pdf.entry(
        "Priyadarshini College Of Engineering, Hingna, Nagpur",
        "B.Tech in Computer Technology",
        "C.G.P.A : 8.88",
        []
    )
    pdf.ln(2)

    pdf.section("Technical Skills")
    pdf.skill_row("Languages:", "Python, Java, JavaScript/TypeScript")
    pdf.skill_row("Frontend:", "React, Next.js, TypeScript, JavaScript, HTML/CSS")
    pdf.skill_row("Backend & Frameworks:", "Spring Boot, FastAPI, Node.js")
    pdf.skill_row("Databases & Caching:", "PostgreSQL, Redis")
    pdf.skill_row("Cloud & DevOps:", "Docker, Kubernetes, Linux, Git, AWS, Google Cloud")

    return pdf


def java_resume():
    pdf = ResumePDF(
        "JAVA DEVELOPER | SPRING BOOT \u2022 JVM \u2022 MICROSERVICES",
        emphasis_skills=["Java", "Spring Boot", "Spring", "Hibernate", "PostgreSQL",
                         "Microservices", "Docker", "Kafka", "Redis", "JVM", "Distributed Systems"],
    )
    pdf.add_page()
    pdf.header_block()
    pdf.section("Profile")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 5, (
        "Java-focused Software Engineer with deep expertise in Spring Boot, JVM internals, "
        "and microservices architecture. Experienced in building low-latency systems, "
        "performance profiling, and distributed systems design. Strong understanding of "
        "concurrency, JVM bytecode, and production debugging."
    ))
    pdf.ln(3)

    pdf.section("Experience")
    pdf.entry(
        "Vahanfin Solutions Pvt. Ltd.", "Full Stack Intern",
        "Remote (Mumbai, India) | Feb 2025 - May 2025",
        [
            "Engineered a modular AI video generation platform using Python, FastAPI, Remotion, FFmpeg, and Manim.",
            "Designed extensible architecture supporting plug-and-play model upgrades across multiple LLM providers.",
        ]
    )
    pdf.ln(2)

    pdf.section("Key Projects")
    pdf.entry(
        "VTracer (Java Agent \u2013 JVM Performance Tracing)",
        "Personal Project",
        "Nov 2025 - Dec 2025",
        [
            "Built a JVM bytecode instrumentation agent using java.lang.instrument.Instrumentation and ClassFileTransformer to trace method latency, call stacks, and thread context with <2% overhead in live Spring Boot services.",
            "Debugged real production bottlenecks (blocking I/O, thread contention, deep call chains) without modifying application code.",
        ]
    )
    pdf.ln(1)
    pdf.entry(
        "Real-Time Trading Platform \u2014 Spring Boot Backend System",
        "Personal Project",
        "Sep 2025 - Oct 2025",
        [
            "Developed a low-latency trading backend using Java and Spring Boot with real-time market data updates and concurrent order handling.",
            "Optimized database access using PostgreSQL with Redis caching, reducing query latency significantly.",
        ]
    )
    pdf.ln(1)
    pdf.entry(
        "OmniVid Lite \u2014 AI-Powered Text-to-Video Generation Engine",
        "Personal Project",
        "Oct 2025 - Present",
        [
            "Engineered AI video generation platform with Python, FastAPI, Remotion, FFmpeg, and Manim.",
            "Designed plug-and-play architecture supporting OpenAI, Mistral, and custom LLMs.",
        ]
    )
    pdf.ln(2)

    pdf.section("Education")
    pdf.entry(
        "Priyadarshini College Of Engineering, Hingna, Nagpur",
        "B.Tech in Computer Technology",
        "C.G.P.A : 8.88",
        []
    )
    pdf.ln(2)

    pdf.section("Technical Skills")
    pdf.skill_row("Languages:", "Java, Python, JavaScript")
    pdf.skill_row("Java Ecosystem:", "Spring Boot, Spring, Hibernate, JPA, JVM Internals, Bytecode Instrumentation")
    pdf.skill_row("Databases & Caching:", "PostgreSQL, Redis")
    pdf.skill_row("Distributed Systems & Messaging:", "WebSockets, Kafka, RabbitMQ")
    pdf.skill_row("Cloud & DevOps:", "Docker, Kubernetes, Linux, Git, AWS, Google Cloud")

    return pdf


def sde_resume():
    pdf = ResumePDF(
        "SOFTWARE ENGINEER | BACKEND \u2022 DISTRIBUTED SYSTEMS \u2022 JAVA",
        emphasis_skills=["Java", "Spring Boot", "Python", "FastAPI", "PostgreSQL",
                         "Docker", "REST APIs", "Git", "Linux", "Microservices",
                         "TypeScript", "Next.js", "React"],
    )
    pdf.add_page()
    pdf.header_block()
    pdf.section("Profile")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(40, 40, 40)
    pdf.multi_cell(0, 5, (
        "Software Engineer with strong foundations in backend systems, API design, and distributed "
        "computing. Proficient across Java, Python, and TypeScript ecosystems. Experienced in building "
        "production-ready applications, optimizing performance, and working effectively in remote teams."
    ))
    pdf.ln(3)

    pdf.section("Experience")
    pdf.entry(
        "Vahanfin Solutions Pvt. Ltd.", "Full Stack Intern",
        "Remote (Mumbai, India) | Feb 2025 - May 2025",
        [
            "Engineered a modular AI video generation platform using Python, FastAPI, Remotion, FFmpeg, and Manim.",
            "Designed extensible architecture supporting plug-and-play model upgrades across multiple LLM providers.",
        ]
    )
    pdf.ln(2)

    pdf.section("Key Projects")
    pdf.entry(
        "Real-Time Trading Platform \u2014 Spring Boot Backend System",
        "Personal Project",
        "Sep 2025 - Oct 2025",
        [
            "Developed a low-latency trading backend using Java and Spring Boot with real-time market data updates and concurrent order handling.",
            "Optimized database access using PostgreSQL with Redis caching, reducing query latency significantly.",
        ]
    )
    pdf.ln(1)
    pdf.entry(
        "VTracer (Java Agent \u2013 JVM Performance Tracing)",
        "Personal Project",
        "Nov 2025 - Dec 2025",
        [
            "Built a JVM bytecode instrumentation agent using java.lang.instrument.Instrumentation and ClassFileTransformer for method latency tracing with <2% overhead.",
            "Debugged production bottlenecks (blocking I/O, thread contention, deep call chains) without modifying application code.",
        ]
    )
    pdf.ln(1)
    pdf.entry(
        "OmniVid Lite \u2014 AI-Powered Text-to-Video Generation Engine",
        "Personal Project",
        "Oct 2025 - Present",
        [
            "Built a full-stack AI video generation platform with Python/FastAPI backend and frontend integration with Remotion and Manim.",
            "Designed plug-and-play architecture supporting OpenAI, Mistral, and custom LLMs.",
        ]
    )
    pdf.ln(1)
    pdf.entry(
        "Fleet Management System",
        "Personal Project",
        "Ongoing",
        [
            "Developed a production-ready Fleet Management system using Next.js, enabling vehicle tracking, service logging, and operational workflow management.",
        ]
    )
    pdf.ln(2)

    pdf.section("Education")
    pdf.entry(
        "Priyadarshini College Of Engineering, Hingna, Nagpur",
        "B.Tech in Computer Technology",
        "C.G.P.A : 8.88",
        []
    )
    pdf.ln(2)

    pdf.section("Technical Skills")
    pdf.skill_row("Languages:", "Python, Java, JavaScript/TypeScript")
    pdf.skill_row("Backend & Frameworks:", "Spring Boot, FastAPI, Node.js, Next.js")
    pdf.skill_row("Databases & Caching:", "PostgreSQL, Redis")
    pdf.skill_row("Distributed Systems & Messaging:", "WebSockets, Kafka, Celery, RabbitMQ")
    pdf.skill_row("Cloud & DevOps:", "Docker, Kubernetes, Linux, Git, AWS, Google Cloud")

    return pdf


VARIANTS = {
    "backend_v3": (backend_resume, ["Spring Boot", "Java", "PostgreSQL", "Docker", "REST APIs", "Git", "Linux", "Microservices"]),
    "fullstack_v4": (fullstack_resume, ["Spring Boot", "React", "TypeScript", "PostgreSQL", "JavaScript", "Docker", "REST APIs", "Git"]),
    "java_v1": (java_resume, ["Java", "Spring Boot", "Spring", "Hibernate", "PostgreSQL", "Microservices", "Docker", "Kafka"]),
    "sde_v1": (sde_resume, ["Java", "Spring Boot", "Python", "FastAPI", "PostgreSQL", "Docker", "REST APIs", "Git", "Linux", "Microservices", "TypeScript", "Next.js", "React"]),
}

RESUMES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "resumes")

for name, (fn, skills) in VARIANTS.items():
    pdf = fn()
    pdf_path = os.path.join(RESUMES_DIR, f"{name}.pdf")
    pdf.output(pdf_path)
    print(f"Created {pdf_path}")

    json_path = os.path.join(RESUMES_DIR, f"{name}.json")
    if not os.path.exists(json_path):
        label = name.replace("_v1", "").replace("_v3", "").replace("_v4", "")
        meta = {
            "name": label,
            "version": 1,
            "skills": skills,
            "experience": label,
            "ats_score": None,
            "ats_analysis": ""
        }
        with open(json_path, "w") as f:
            json.dump(meta, f, indent=2)
        print(f"Created {json_path}")
    else:
        print(f"Skipped existing {json_path}")

print("\nDone! 4 resumes generated.")
