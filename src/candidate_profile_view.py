"""
Job Bucket — Candidate Profile & LaTeX Workbench View
Streamlit interface for managing candidate ground-truth facts, PII masking, and LaTeX CV templates.
"""

import json
import logging
from typing import Any, Dict, Optional

import streamlit as st

from src.candidate_profile_service import CandidateMasterProfileService, PHONE_PLACEHOLDER, EMAIL_PLACEHOLDER
from src.pdf_compiler import PDFCompilationEngine, CandidateContactInfo

logger = logging.getLogger(__name__)


def render_candidate_profile_workbench_page():
    """Renders the Candidate Profile & LaTeX Workbench page."""
    st.title("Candidate Profile & LaTeX Workbench")
    st.caption("Ground-truth candidate facts, PII masking guardrails, and master LaTeX CV template.")

    profile_svc = CandidateMasterProfileService()
    pdf_engine = PDFCompilationEngine()

    profile = profile_svc.get_active_profile()
    if not profile:
        st.error("No active candidate profile found in database. Please check migrations.")
        return

    tab_facts, tab_latex, tab_ai_md, tab_prompts = st.tabs([
        "Profile Facts & PII",
        "Master LaTeX CV",
        "AI Context (01-candidate-profile.md)",
        "Prompt Guidelines & Rules",
    ])

    # -------------------------------------------------------------------------
    # TAB 1: Profile Facts & PII
    # -------------------------------------------------------------------------
    with tab_facts:
        st.markdown("### Candidate Identity & Private PII")
        st.info(
            "**Privacy Guarantee:** Your **Phone** and **Email** are strictly masked with "
            f"`{PHONE_PLACEHOLDER}` and `{EMAIL_PLACEHOLDER}` before any prompt is transmitted to AI. "
            "Your Name, LinkedIn, and GitHub are kept intact as public engineering assets. "
            "Real contact details are restored locally on your machine after AI generation."
        )

        with st.form("edit_candidate_facts_form"):
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("Full Name", value=profile.get("candidate_name", ""))
                phone = st.text_input("Phone Number (Masked in AI)", value=profile.get("phone", ""))
                email = st.text_input("Email Address (Masked in AI)", value=profile.get("email", ""))
                location = st.text_input("Location", value=profile.get("location", ""))

            with col2:
                linkedin = st.text_input("LinkedIn Profile URL", value=profile.get("linkedin_url", ""))
                github = st.text_input("GitHub Profile URL", value=profile.get("github_url", ""))
                status = st.text_input("Employment Status", value=profile.get("status", "Actively Looking"))
                constraints = st.text_input("Location / Commute Constraints", value=profile.get("constraints", ""))

            st.markdown("#### Technical Skills (JSON)")
            skills_val = profile.get("technical_skills", {})
            skills_str = json.dumps(skills_val, indent=2) if isinstance(skills_val, (dict, list)) else str(skills_val)
            skills_input = st.text_area("Technical Skills Map", value=skills_str, height=180)

            st.markdown("#### Professional Experience (JSON)")
            work_val = profile.get("work_history", [])
            work_str = json.dumps(work_val, indent=2) if isinstance(work_val, (dict, list)) else str(work_val)
            work_input = st.text_area("Work History Entries", value=work_str, height=220)

            st.markdown("#### Independent Projects (JSON)")
            proj_val = profile.get("projects", [])
            proj_str = json.dumps(proj_val, indent=2) if isinstance(proj_val, (dict, list)) else str(proj_val)
            proj_input = st.text_area("Projects", value=proj_str, height=180)

            submit_facts = st.form_submit_button("Save Profile Facts to Neon DB", type="primary")
            if submit_facts:
                try:
                    updated_data = {
                        "candidate_name": name.strip(),
                        "phone": phone.strip(),
                        "email": email.strip(),
                        "location": location.strip(),
                        "linkedin_url": linkedin.strip(),
                        "github_url": github.strip(),
                        "status": status.strip(),
                        "constraints": constraints.strip(),
                        "technical_skills": json.loads(skills_input),
                        "work_history": json.loads(work_input),
                        "projects": json.loads(proj_input),
                        "education": profile.get("education", []),
                        "languages": profile.get("languages", []),
                        "master_latex_source": profile.get("master_latex_source", ""),
                        "behavioral_profile_md": profile.get("behavioral_profile_md", ""),
                    }
                    if profile_svc.update_profile(updated_data):
                        st.success("Successfully updated candidate master profile in Neon PostgreSQL!")
                        st.rerun()
                    else:
                        st.error("Failed to update profile in database.")
                except json.JSONDecodeError as je:
                    st.error(f"Invalid JSON in Skills, Work, or Projects: {je}")
                except Exception as e:
                    st.error(f"Error saving profile: {e}")

    # -------------------------------------------------------------------------
    # TAB 2: Master LaTeX CV Workbench
    # -------------------------------------------------------------------------
    with tab_latex:
        st.markdown("### Master LaTeX Template (ModernCV Banking Style)")
        st.caption(
            "This template is used as the structural baseline for tailored CV generation. "
            f"Use `{PHONE_PLACEHOLDER}` and `{EMAIL_PLACEHOLDER}` placeholders for contact information."
        )

        current_latex = profile.get("master_latex_source", "")
        latex_editor = st.text_area("LaTeX Source Code", value=current_latex, height=450, key="latex_source_area")

        col_l1, col_l2, col_l3 = st.columns([1.5, 1.5, 1.5])
        with col_l1:
            if st.button("Save LaTeX to Database", use_container_width=True, type="primary"):
                updated_profile = dict(profile)
                updated_profile["master_latex_source"] = latex_editor
                if profile_svc.update_profile(updated_profile):
                    st.success("Master LaTeX template saved successfully!")
                    st.rerun()
                else:
                    st.error("Failed to save LaTeX template.")

        with col_l2:
            test_compile = st.button("Test Compile ATS PDF", use_container_width=True)

        with col_l3:
            unmasked_download = profile_svc.restore_unmasked_text(
                latex_editor,
                {
                    PHONE_PLACEHOLDER: profile.get("phone", ""),
                    EMAIL_PLACEHOLDER: profile.get("email", ""),
                }
            )
            st.download_button(
                "Download Master .tex",
                data=unmasked_download,
                file_name="Abhishek_Dwivedi_Master_CV.tex",
                mime="text/plain",
                use_container_width=True,
            )

        if test_compile:
            with st.spinner("Compiling exact LaTeX editor source into ATS-compliant PDF..."):
                try:
                    unmasked_latex = profile_svc.restore_unmasked_text(
                        latex_editor,
                        {
                            PHONE_PLACEHOLDER: profile.get("phone", ""),
                            EMAIL_PLACEHOLDER: profile.get("email", ""),
                        }
                    )
                    pdf_bytes = pdf_engine.compile_latex_to_pdf(unmasked_latex)
                    audit = pdf_engine.verify_pdf_ats_compliance(pdf_bytes, expected_max_pages=2)

                    st.success(f"Compilation Successful! ATS Score: {audit.ats_score}/100 | Pages: {audit.page_count}")
                    st.download_button(
                        label="Download Compiled PDF",
                        data=pdf_bytes,
                        file_name="Master_Resume_Compiled.pdf",
                        mime="application/pdf",
                        key="dl_compiled_master_pdf",
                    )
                except Exception as ce:
                    st.error(f"LaTeX Compilation failed: {ce}")

    # -------------------------------------------------------------------------
    # TAB 3: AI Context (01-candidate-profile.md)
    # -------------------------------------------------------------------------
    with tab_ai_md:
        st.markdown("### Ground-Truth Context Preview (`01-candidate-profile.md`)")
        st.caption("This is the exact markdown representation generated from PostgreSQL and injected into Drafter & Reviewer prompts.")

        view_mode = st.radio("Context Mode", ["PII-Masked (Sent to AI)", "Unmasked (Local Machine View)"], horizontal=True)
        masked_mode = (view_mode == "PII-Masked (Sent to AI)")

        generated_md = profile_svc.export_profile_to_markdown(profile, masked=masked_mode)
        st.code(generated_md, language="markdown")

    # -------------------------------------------------------------------------
    # TAB 4: Prompt Guidelines & Rules
    # -------------------------------------------------------------------------
    with tab_prompts:
        st.markdown("### Authentic `ai-job-search` Prompt Guidelines")
        st.caption("Active prompt files stored in `src/prompts/` (zero modifications, zero fabrications).")

        from src.agentic_tailoring_service import load_prompt_file

        prompt_sub = st.selectbox(
            "Select Guideline Document",
            [
                "03-writing-style.md (Negative Style Constraints)",
                "04-job-evaluation.md (5-D Fit Scoring)",
                "05-cv-templates.md (LaTeX Standards)",
                "08-application-forms.md (Form Field Rules)",
                "apply.md (2-Agent Drafter + Reviewer Workflow)",
            ]
        )

        filename_map = {
            "03-writing-style.md (Negative Style Constraints)": "03-writing-style.md",
            "04-job-evaluation.md (5-D Fit Scoring)": "04-job-evaluation.md",
            "05-cv-templates.md (LaTeX Standards)": "05-cv-templates.md",
            "08-application-forms.md (Form Field Rules)": "08-application-forms.md",
            "apply.md (2-Agent Drafter + Reviewer Workflow)": "apply.md",
        }

        chosen_file = filename_map.get(prompt_sub, "03-writing-style.md")
        content = load_prompt_file(chosen_file)
        if content:
            st.code(content, language="markdown")
        else:
            st.warning(f"File {chosen_file} not found in `src/prompts/`.")
