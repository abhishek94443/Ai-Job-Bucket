-- Migration 008: Add Candidate Master Profile & LaTeX Template Registry
-- Stores verified candidate facts, PII (phone/email), public URLs, and the master LaTeX template.
-- Enforces:
-- 1. At most one active master profile (partial unique index).
-- 2. Complete factual grounding fields (identity, languages, education, work history, projects, skills).
-- 3. Dedicated master_latex_source for user-customizable LaTeX resume templates.

BEGIN;

CREATE TABLE IF NOT EXISTS candidate_master_profile (
    id SERIAL PRIMARY KEY,
    candidate_name VARCHAR(255) NOT NULL DEFAULT 'Candidate Name',
    location VARCHAR(255) DEFAULT 'City, Country',
    phone VARCHAR(50) DEFAULT '+1 555-0100',
    email VARCHAR(255) DEFAULT 'candidate@example.com',
    linkedin_url VARCHAR(255) DEFAULT 'https://linkedin.com/in/candidate',
    github_url VARCHAR(255) DEFAULT 'https://github.com/candidate',
    status VARCHAR(100) DEFAULT 'Actively Looking',
    constraints TEXT DEFAULT 'Open to Remote / Relocation',
    languages JSONB DEFAULT '[{"language": "English", "level": "Fluent / Professional", "notes": "Working proficiency"}]'::jsonb,
    education JSONB DEFAULT '[{"degree": "Bachelor of Science in Computer Science", "period": "2020 - 2024", "institution": "University Name", "topics": "Computer Systems and Software Architecture"}]'::jsonb,
    work_history JSONB DEFAULT '[
        {
            "company": "Enterprise Tech Solutions",
            "client": "",
            "role": "Software Engineer",
            "period": "2023 - Present",
            "location": "City, Country",
            "bullets": [
                "Engineered distributed backend microservices and REST APIs handling asynchronous message queuing.",
                "Containerized legacy service components using Docker and deployed on Kubernetes clusters.",
                "Optimized database queries and transaction throughput on relational PostgreSQL databases."
            ]
        }
    ]'::jsonb,
    projects JSONB DEFAULT '[
        {
            "name": "CloudContext - Developer Observability Platform",
            "tech_stack": "Python, FastAPI, Docker, PostgreSQL",
            "github_url": "https://github.com/candidate/project",
            "bullets": [
                "Built asynchronous data ingestion pipelines with automated validation and structured metric logs.",
                "Implemented JWT authentication and role-based access control across multi-tenant endpoints."
            ]
        }
    ]'::jsonb,
    technical_skills JSONB DEFAULT '{
        "languages": ["Java", "Python", "SQL", "TypeScript"],
        "frameworks": ["Spring Boot", "FastAPI", "React", "Next.js"],
        "databases": ["PostgreSQL", "MySQL", "Redis"],
        "devops_cloud": ["Docker", "Kubernetes", "Git", "CI/CD", "AWS"],
        "apis_messaging": ["REST", "Kafka", "ActiveMQ", "Microservices"]
    }'::jsonb,
    master_latex_source TEXT DEFAULT '\documentclass[11pt,a4paper,sans]{moderncv}
\moderncvstyle{banking}
\moderncvcolor{blue}

\renewcommand*{\namefont}{\fontsize{32}{34}\bfseries\upshape}
\colorlet{firstnamecolor}{color1}
\colorlet{lastnamecolor}{color1}
\colorlet{namecolor}{color1}
\renewcommand*{\sectionstyle}[1]{{\sectionfont\color{color1}#1}}

\usepackage[utf8]{inputenc}
\usepackage[scale=0.78]{geometry}
\usepackage{import}

\name{Candidate}{Name}
\address{City, Country}{}{}
\phone[mobile]{{{CANDIDATE_PHONE}}}
\email{{{CANDIDATE_EMAIL}}}
\extrainfo{\href{https://linkedin.com/in/candidate}{LinkedIn} \textbar\ \href{https://github.com/candidate}{GitHub}}

\begin{document}
\makecvtitle

\section{Summary}
Software Engineer specializing in scalable distributed backend systems, database architecture, and microservices.

\section{Technical Skills}
\cvitem{Languages}{Java, Python, SQL, TypeScript}
\cvitem{Frameworks}{Spring Boot, FastAPI, React, Next.js}
\cvitem{Databases}{PostgreSQL, MySQL, Redis}
\cvitem{DevOps \& Cloud}{Docker, Kubernetes, Git, CI/CD, AWS}

\section{Professional Experience}
\cventry{2023 -- Present}{Software Engineer}{Enterprise Tech Solutions}{City, Country}{}{
\begin{itemize}
\item Engineered distributed backend microservices and REST APIs handling asynchronous message queuing.
\item Containerized legacy service components using Docker and deployed on Kubernetes clusters.
\item Optimized database queries and transaction throughput on relational PostgreSQL databases.
\end{itemize}}

\section{Projects}
\cventry{2024}{Developer Observability Platform}{CloudContext}{\href{https://github.com/candidate/project}{github.com/candidate/project}}{}{
\begin{itemize}
\item Built asynchronous data ingestion pipelines with automated validation and structured metric logs.
\item Implemented JWT authentication and role-based access control across multi-tenant endpoints.
\end{itemize}}

\section{Education}
\cventry{2020 -- 2024}{Bachelor of Science in Computer Science}{University Name}{City, Country}{}{}

\end{document}',
    behavioral_profile_md TEXT DEFAULT '## Behavioral & Work Style
- **Collaborative Problem Solver:** Values clear system boundaries, high code quality, and peer review.
- **Engineering Autonomy:** Strong ownership of backend services from design to production observability.
- **Direct & Transparent:** Prefers concise, factual communication over buzzwords and corporate jargon.',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partial unique index: ensures only one profile is active at a time
CREATE UNIQUE INDEX IF NOT EXISTS uq_candidate_master_profile_active 
ON candidate_master_profile (is_active) 
WHERE is_active = TRUE;

-- Insert default active record if table is empty
INSERT INTO candidate_master_profile (candidate_name, is_active)
SELECT 'Candidate Name', TRUE
WHERE NOT EXISTS (SELECT 1 FROM candidate_master_profile);

COMMIT;
