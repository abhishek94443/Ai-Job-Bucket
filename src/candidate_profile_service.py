"""
Job Bucket Candidate Master Profile Service
Manages candidate ground truth facts and LaTeX templates.

Ensures:
1. Candidate facts in Neon PostgreSQL are the single source of truth (01-candidate-profile.md format).
2. Direct candidate facts are supplied to AI models for strict factual grounding.
3. Master LaTeX Template management: Stores and retrieves the user-editable LaTeX template.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from src.db_config import DatabaseConfig, connect_database

logger = logging.getLogger(__name__)

PHONE_PLACEHOLDER = "{{CANDIDATE_PHONE}}"
EMAIL_PLACEHOLDER = "{{CANDIDATE_EMAIL}}"


class CandidateMasterProfileService:
    """
    CRUD and formatting service for candidate master profile and LaTeX templates.
    """

    def __init__(self, db_config: Optional[Any] = None):
        self.db_config = db_config or DatabaseConfig

    def get_connection(self):
        """Creates psycopg2 connection to PostgreSQL."""
        if hasattr(self.db_config, "get_connection_dict"):
            cfg = self.db_config.get_connection_dict()
        elif isinstance(self.db_config, dict):
            cfg = self.db_config
        else:
            cfg = DatabaseConfig.get_connection_dict()
        return connect_database(cfg)

    def get_active_profile(self) -> Dict[str, Any]:
        """
        Retrieves the active candidate master profile from PostgreSQL.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT * FROM candidate_master_profile
                    WHERE is_active = TRUE
                    ORDER BY id ASC LIMIT 1;
                    """
                )
                row = cur.fetchone()
                if row:
                    return dict(row)
                return {}
        except Exception as e:
            logger.error(f"Failed to fetch active candidate profile: {e}")
            return {}
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass

    def update_profile(self, profile_data: Dict[str, Any]) -> bool:
        """
        Updates the active candidate master profile.
        """
        conn = None
        try:
            conn = self.get_connection()
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE candidate_master_profile
                        SET candidate_name = %s,
                            location = %s,
                            phone = %s,
                            email = %s,
                            linkedin_url = %s,
                            github_url = %s,
                            status = %s,
                            constraints = %s,
                            languages = %s,
                            education = %s,
                            work_history = %s,
                            projects = %s,
                            technical_skills = %s,
                            master_latex_source = %s,
                            behavioral_profile_md = %s,
                            updated_at = NOW()
                        WHERE is_active = TRUE;
                        """,
                        (
                            profile_data.get("candidate_name", ""),
                            profile_data.get("location", ""),
                            profile_data.get("phone", ""),
                            profile_data.get("email", ""),
                            profile_data.get("linkedin_url", ""),
                            profile_data.get("github_url", ""),
                            profile_data.get("status", "Actively Looking"),
                            profile_data.get("constraints", ""),
                            json.dumps(profile_data.get("languages", [])),
                            json.dumps(profile_data.get("education", [])),
                            json.dumps(profile_data.get("work_history", [])),
                            json.dumps(profile_data.get("projects", [])),
                            json.dumps(profile_data.get("technical_skills", {})),
                            profile_data.get("master_latex_source", ""),
                            profile_data.get("behavioral_profile_md", ""),
                        )
                    )
                    conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to update candidate profile: {e}")
            return False
        finally:
            if conn and not getattr(conn, "closed", True):
                try:
                    conn.close()
                except Exception:
                    pass

    def export_profile_to_markdown(self, profile: Optional[Dict[str, Any]] = None, masked: bool = False) -> str:
        """
        Formats candidate profile strictly in the format of 01-candidate-profile.md.
        Always outputs real contact information (phone and email) directly.
        """
        p = profile or self.get_active_profile()
        if not p:
            return "# Candidate Profile\n\nNo profile found in database."

        phone_val = p.get("phone", "")
        email_val = p.get("email", "")

        md_lines = [
            "# Candidate Profile",
            "",
            "## Identity",
            f"- **Name:** {p.get('candidate_name', 'Abhishek Dwivedi')}",
            f"- **Location:** {p.get('location', 'Bangalore, India')}",
            f"- **Phone:** {phone_val}",
            f"- **Email:** {email_val}",
            f"- **LinkedIn:** {p.get('linkedin_url', '')}",
            f"- **GitHub:** {p.get('github_url', '')}",
            f"- **Status:** {p.get('status', 'Actively Looking')}",
            f"- **Constraints:** {p.get('constraints', 'Open to Remote / Bangalore')}",
            "",
            "### Languages",
            "| Language | Level | Notes |",
            "|---|---|---|",
        ]

        langs = p.get("languages", [])
        if isinstance(langs, str):
            langs = json.loads(langs)
        for l in langs:
            md_lines.append(f"| {l.get('language', '')} | {l.get('level', '')} | {l.get('notes', '')} |")

        md_lines.extend([
            "",
            "## Education",
            "| Degree | Period | Institution | Key Topics |",
            "|---|---|---|---|",
        ])

        edus = p.get("education", [])
        if isinstance(edus, str):
            edus = json.loads(edus)
        for ed in edus:
            md_lines.append(f"| {ed.get('degree', '')} | {ed.get('period', '')} | {ed.get('institution', '')} | {ed.get('topics', '')} |")

        md_lines.extend(["", "## Professional Experience"])
        work = p.get("work_history", [])
        if isinstance(work, str):
            work = json.loads(work)
        for w in work:
            client_note = f" | Client: {w.get('client')}" if w.get("client") else ""
            md_lines.append(f"\n### {w.get('role', 'Software Engineer')} - {w.get('company', '')}{client_note} ({w.get('period', '')})")
            md_lines.append(f"{w.get('location', '')}")
            for b in w.get("bullets", []):
                md_lines.append(f"- {b}")

        md_lines.extend(["", "## Independent Projects"])
        projs = p.get("projects", [])
        if isinstance(projs, str):
            projs = json.loads(projs)
        for pr in projs:
            md_lines.append(f"\n### {pr.get('name', 'Project')}")
            if pr.get("tech_stack"):
                md_lines.append(f"**Tech Stack:** {pr.get('tech_stack')}")
            if pr.get("github_url"):
                md_lines.append(f"**URL:** {pr.get('github_url')}")
            for b in pr.get("bullets", []):
                md_lines.append(f"- {b}")

        md_lines.extend(["", "## Technical Skills"])
        skills = p.get("technical_skills", {})
        if isinstance(skills, str):
            skills = json.loads(skills)

        for category, skill_list in skills.items():
            cat_title = category.replace("_", " ").title()
            if isinstance(skill_list, list):
                md_lines.append(f"- **{cat_title}:** {', '.join(skill_list)}")
            else:
                md_lines.append(f"- **{cat_title}:** {skill_list}")

        return "\n".join(md_lines)

    def get_masked_context_for_ai(self) -> Tuple[str, Dict[str, str], str]:
        """
        Returns (profile_md, unmask_map, master_latex).
        Context contains actual candidate details directly without masking.
        """
        p = self.get_active_profile()
        profile_md = self.export_profile_to_markdown(p, masked=False)
        unmask_map: Dict[str, str] = {}
        master_latex = p.get("master_latex_source", "")
        return profile_md, unmask_map, master_latex

    def restore_unmasked_text(self, text: str, unmask_map: Optional[Dict[str, str]] = None) -> str:
        """
        Pass-through function maintaining backwards compatibility.
        No substitution required as text already contains actual details.
        """
        if not unmask_map:
            return text
        result = text
        for placeholder, real_val in unmask_map.items():
            result = result.replace(placeholder, real_val)
        return result
