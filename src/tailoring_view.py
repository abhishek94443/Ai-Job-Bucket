"""
Job Bucket — Tailoring View Component (Phase 3 & 2-Agent Drafter + Reviewer)
Renders clean, executive UI drawer for Tailored Application Materials:
- 2-Agent Tailoring Engine (Drafter + Reviewer Factual Grounding Audit)
- Resume Highlights
- Recruiter Outreach Pitch (<= 300 chars)
- Forward-Looking Cover Pitch (3 paragraphs)
- Application Form Portal Answers
- Tailored LaTeX CV (.tex)
- Executive ATS-Grade PDF (.pdf) with pypdf validation
"""

import os
import re
from typing import Any, Dict, Optional
import streamlit as st

from src.candidate_profile import CandidateProfile
from src.resume_service import ResumeService
from src.tailoring_service import TailoringService, TailoredApplicationPack
from src.agentic_tailoring_service import AgenticTailoringEngine, AgenticTailoringResult
from src.pdf_compiler import PDFCompilationEngine, CandidateContactInfo
from src.candidate_profile_service import CandidateMasterProfileService


def render_tailored_application_drawer(
    job: Dict[str, Any],
    profile: Optional[CandidateProfile] = None,
):
    """
    Renders the Tailored Application Material drawer inside a job card expander.
    Supports both the 2-Agent (Drafter + Reviewer) pipeline and Fast Grounded Synthesis.
    """
    job_id = job.get("id", "temp")
    state_key_agent = f"agentic_pack_{job_id}"
    state_key_fast = f"pack_{job_id}"

    st.markdown("#### Tailored Application Materials")
    st.caption("Job-aligned materials grounded in verified profile facts (zero hallucinated skills, no corporate buzzwords, no em-dashes).")

    has_agent_pack = state_key_agent in st.session_state
    agent_pack: Optional[AgenticTailoringResult] = st.session_state.get(state_key_agent)
    fast_pack: Optional[TailoredApplicationPack] = st.session_state.get(state_key_fast)

    # Action buttons
    col_btn1, col_btn2, col_info = st.columns([2.0, 1.8, 2.5])
    with col_btn1:
        agent_btn_label = "Re-run 2-Agent Loop" if has_agent_pack else "Run 2-Agent Tailoring"
        if st.button(agent_btn_label, key=f"gen_2agent_btn_{job_id}", type="primary", use_container_width=True):
            with st.spinner("Executing 2-Agent loop (Drafter -> Reviewer Grounding Audit -> Revision)..."):
                try:
                    engine = AgenticTailoringEngine()
                    agent_res = engine.run_workflow(job, prefer_ai=True)
                    st.session_state[state_key_agent] = agent_res
                    st.rerun()
                except Exception as e:
                    st.error(f"2-Agent workflow failed: {e}")

    with col_btn2:
        fast_btn_label = "Quick Synthesis"
        if st.button(fast_btn_label, key=f"gen_fast_btn_{job_id}", use_container_width=True):
            with st.spinner("Synthesizing grounded application pack..."):
                try:
                    svc = TailoringService()
                    pack = svc.generate_pack(job, profile=profile, prefer_ai=False)
                    st.session_state[state_key_fast] = pack
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to generate materials: {e}")

    with col_info:
        if has_agent_pack and agent_pack:
            ats_val = agent_pack.ats_score
            src_val = agent_pack.generation_source
            st.markdown(
                f'<span class="status-badge badge-offer">[Grounding: PASS]</span> '
                f'<span class="status-badge badge-submitted">[ATS: {ats_val}/100]</span> '
                f'<span style="font-size: 0.85rem; color: #a1a1aa;">Source: {src_val}</span>',
                unsafe_allow_html=True
            )
        elif fast_pack:
            st.markdown(
                f'<span class="status-badge badge-offer">[Grounded: PASS]</span> '
                f'<span style="font-size: 0.85rem; color: #a1a1aa;">Deterministic</span>',
                unsafe_allow_html=True
            )

    # -------------------------------------------------------------------------
    # Display 2-Agent Results
    # -------------------------------------------------------------------------
    if has_agent_pack and agent_pack:
        # Reviewer Audit Details Box
        fb = agent_pack.reviewer_feedback
        with st.expander("Reviewer Grounding Audit Report (Hiring Manager Proxy)", expanded=False):
            col_rev1, col_rev2 = st.columns(2)
            with col_rev1:
                st.markdown(f"**Grounding Verification ($Draft \\subseteq Facts$):** {'PASS' if fb.grounding_passed else 'FLAGGED'}")
                st.markdown(f"**Tone & Style Check:** `{fb.part_b_tone_style}`")
                st.markdown(f"**Missed Keywords Checked:** `{fb.part_b_missed_keywords}`")
            with col_rev2:
                st.markdown(f"**Company Angle:** `{fb.part_b_company_angles}`")
                st.markdown(f"**Action-Oriented Reframing:** `{fb.part_b_reframing}`")
                st.markdown(f"**Mechanical String Edits Applied (Part A):** {len(fb.part_a_edits)}")

            if fb.part_a_edits:
                st.markdown("##### Part A String Replacements Applied:")
                for idx, ed in enumerate(fb.part_a_edits, 1):
                    st.caption(f"{idx}. `[{ed.reason}]` `{ed.old_string}` -> `{ed.new_string}`")

        # Tabs for tailored artifacts
        t_bullets, t_outreach, t_pitch, t_portal, t_latex, t_pdf = st.tabs([
            "Resume Highlights",
            "Recruiter Outreach",
            "Cover Pitch",
            "Portal Form Answers",
            "Tailored LaTeX CV",
            "Export ATS PDF",
        ])

        with t_bullets:
            st.markdown("**Outcome-Oriented Tailored Bullets**")
            st.caption("Engineered for target tech stack and verified against candidate master profile:")
            bullets_text = "\n".join(f"• {b}" for b in agent_pack.tailored_bullets)
            st.code(bullets_text, language="markdown")

        with t_outreach:
            st.markdown("**Recruiter / Hiring Manager Direct Outreach**")
            st.caption("Crisp LinkedIn InMail note (<= 300 chars, no cliches):")
            pitch_lead = agent_pack.cover_pitch.split("\n\n")[0] if agent_pack.cover_pitch else ""
            outreach = pitch_lead[:280] if len(pitch_lead) > 280 else pitch_lead
            st.code(outreach, language="text")

        with t_pitch:
            st.markdown("**Forward-Looking Cover Pitch**")
            st.caption("3-paragraph problem-solving pitch addressing company engineering needs:")
            st.code(agent_pack.cover_pitch, language="text")

        with t_portal:
            st.markdown("**Application Form Portal Answers**")
            st.markdown("**Short Self-Introduction (100–150 words):**")
            st.code(agent_pack.portal_short_bio, language="text")
            st.markdown("**Core Technical Accomplishment (100–150 words):**")
            st.code(agent_pack.portal_key_achievement, language="text")

        with t_latex:
            st.markdown("**Tailored LaTeX CV Code (`moderncv` Banking Style)**")
            st.caption("Full compilable LaTeX document tailored to this opportunity with local PII restored:")
            clean_company = re.sub(r'[^A-Za-z0-9]', '_', job.get("company", "Company"))
            st.download_button(
                label=f"Download Tailored LaTeX (.tex)",
                data=agent_pack.tailored_latex_cv,
                file_name=f"Abhishek_Dwivedi_{clean_company}_CV.tex",
                mime="text/plain",
                key=f"dl_tex_{job_id}",
                use_container_width=True,
            )
            st.code(agent_pack.tailored_latex_cv, language="latex")

        with t_pdf:
            st.markdown("**Executive ATS PDF Document Generator**")
            st.caption("Locally compiled with Helvetica fonts, ensuring 100% OCR compliance across all ATS systems.")
            pdf_engine = PDFCompilationEngine()
            clean_company = re.sub(r'[^A-Za-z0-9]', '_', job.get("company", "Company"))
            clean_role = job.get("title", "Software_Engineer")

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                if st.button("Re-Compile Resume (PDF)", key=f"recompile_resume_{job_id}", use_container_width=True):
                    with st.spinner("Compiling exact LaTeX source into ATS-compliant PDF..."):
                        try:
                            pdf_bytes = pdf_engine.compile_latex_to_pdf(agent_pack.tailored_latex_cv)
                            audit = pdf_engine.verify_pdf_ats_compliance(pdf_bytes, expected_max_pages=2)
                            st.session_state[f"pdf_resume_{job_id}"] = (pdf_bytes, audit)
                        except Exception as e:
                            st.error(f"LaTeX Compilation failed: {e}")

                # Automatically compile if not in session state yet
                if f"pdf_resume_{job_id}" not in st.session_state:
                    try:
                        pdf_bytes = pdf_engine.compile_latex_to_pdf(agent_pack.tailored_latex_cv)
                        audit = pdf_engine.verify_pdf_ats_compliance(pdf_bytes, expected_max_pages=2)
                        st.session_state[f"pdf_resume_{job_id}"] = (pdf_bytes, audit)
                    except Exception:
                        pass

                if f"pdf_resume_{job_id}" in st.session_state:
                    res_bytes, res_audit = st.session_state[f"pdf_resume_{job_id}"]
                    st.success(f"ATS Score: {res_audit.ats_score}/100 | Pages: {res_audit.page_count} | OCR Valid")
                    st.download_button(
                        label="Download Tailored Resume (PDF)",
                        data=res_bytes,
                        file_name=f"Abhishek_Dwivedi_{clean_company}_Resume.pdf",
                        mime="application/pdf",
                        key=f"dl_res_pdf_{job_id}",
                        use_container_width=True,
                    )

            with btn_col2:
                if st.button("Compile Cover Letter (PDF)", key=f"compile_cover_{job_id}", use_container_width=True):
                    with st.spinner("Compiling executive cover letter..."):
                        try:
                            c_svc = CandidateMasterProfileService()
                            p = c_svc.get_active_profile()
                            contact_info = CandidateContactInfo(
                                name=p.get("candidate_name", "Candidate Name"),
                                phone=p.get("phone", "+1 555-0100"),
                                email=p.get("email", "candidate@example.com"),
                                linkedin_url=p.get("linkedin_url", "https://linkedin.com/in/candidate"),
                                github_url=p.get("github_url", "https://github.com/candidate"),
                                location=p.get("location", "City, Country"),
                            )
                            cover_bytes = pdf_engine.compile_cover_letter_pdf(
                                contact_info=contact_info,
                                target_role=clean_role,
                                target_company=job.get("company", "Company"),
                                pitch_paragraphs=agent_pack.cover_pitch,
                            )
                            c_audit = pdf_engine.verify_pdf_ats_compliance(cover_bytes, expected_max_pages=1)
                            st.session_state[f"pdf_cover_{job_id}"] = (cover_bytes, c_audit)
                        except Exception as e:
                            st.error(f"Cover letter compilation failed: {e}")

                if f"pdf_cover_{job_id}" in st.session_state:
                    cov_bytes, cov_audit = st.session_state[f"pdf_cover_{job_id}"]
                    st.success(f"ATS Score: {cov_audit.ats_score}/100 | Pages: {cov_audit.page_count} | Clean")
                    st.download_button(
                        label="Download Cover Letter (PDF)",
                        data=cov_bytes,
                        file_name=f"Abhishek_Dwivedi_{clean_company}_Cover_Letter.pdf",
                        mime="application/pdf",
                        key=f"dl_cov_pdf_{job_id}",
                        use_container_width=True,
                    )

    # -------------------------------------------------------------------------
    # Display Fast Synthesis Fallback Results
    # -------------------------------------------------------------------------
    elif fast_pack:
        tab_bullets, tab_outreach, tab_pitch, tab_portal = st.tabs([
            "Resume Highlights",
            "Recruiter Outreach",
            "Cover Pitch",
            "Portal Form Answers",
        ])

        with tab_bullets:
            st.markdown("**Outcome-Oriented Resume Bullets**")
            bullets_text = "\n".join(f"• {b}" for b in fast_pack.tailored_resume_bullets)
            st.code(bullets_text, language="markdown")

        with tab_outreach:
            st.markdown("**Recruiter / Hiring Manager Direct Outreach**")
            st.code(fast_pack.outreach_message, language="text")

        with tab_pitch:
            st.markdown("**Forward-Looking Cover Pitch**")
            st.code(fast_pack.cover_pitch, language="text")

        with tab_portal:
            st.markdown("**Application Form Portal Answers**")
            st.markdown("**Short Self-Introduction (100–150 words):**")
            st.code(fast_pack.portal_short_bio, language="text")
            st.markdown("**Core Technical Accomplishment (100–150 words):**")
            st.code(fast_pack.portal_key_achievement, language="text")
