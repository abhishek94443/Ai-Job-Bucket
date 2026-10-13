"""
Job Bucket — Interview Prep View Component (Phase 4)
Renders clean, executive UI drawer for the Gap-Bridging Interview Preparation Pack:
- 5 Toughest Technical Probes with Honest Bridge Answers
- 2 Project-Based STAR Stories
- 3 Strategic Reverse Questions
"""

from typing import Any, Dict, Optional
import streamlit as st

from src.candidate_profile import CandidateProfile
from src.interview_prep import InterviewPrepEngine, InterviewPrepPack
from src.resume_service import ResumeService


def render_interview_prep_drawer(
    job: Dict[str, Any],
    profile: Optional[CandidateProfile] = None,
):
    """
    Renders the Interview Preparation Pack drawer inside a job card expander.
    """
    job_id = job.get("id", "temp")
    state_key = f"prep_pack_{job_id}"

    if profile is None:
        try:
            resume_svc = ResumeService()
            profile = resume_svc.get_active_profile()
        except Exception:
            from src.candidate_profile import DEFAULT_CANDIDATE_PROFILE
            profile = DEFAULT_CANDIDATE_PROFILE

    st.markdown("#### Technical Interview Preparation Pack")
    st.caption("Gap-bridging probe questions, honest transition talking points, STAR stories, and reverse questions.")

    has_pack = state_key in st.session_state
    pack: Optional[InterviewPrepPack] = st.session_state.get(state_key)

    col_btn, col_info = st.columns([1.5, 3.5])
    with col_btn:
        btn_label = "Regenerate Prep Pack" if has_pack else "Generate Interview Pack"
        if st.button(btn_label, key=f"gen_prep_btn_{job_id}", use_container_width=True):
            with st.spinner("Synthesizing gap-bridging interview pack..."):
                try:
                    engine = InterviewPrepEngine()
                    pack = engine.generate_pack(job, profile=profile, prefer_ai=False)
                    st.session_state[state_key] = pack
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to generate interview materials: {e}")

    with col_info:
        if has_pack and pack:
            gaps_str = ", ".join(pack.analyzed_gaps) if pack.analyzed_gaps else "Distributed Systems"
            st.markdown(
                f'<span class="status-badge badge-shortlisted">[Tested Gaps: {gaps_str}]</span> '
                f'<span class="status-badge badge-stale">[{pack.generation_source}]</span>',
                unsafe_allow_html=True
            )

    if has_pack and pack:
        tab_probes, tab_star, tab_reverse = st.tabs([
            f"Technical Probes ({len(pack.technical_probes)})",
            f"STAR Stories ({len(pack.star_stories)})",
            f"Reverse Questions ({len(pack.reverse_questions)})"
        ])

        with tab_probes:
            st.caption("Anticipate probing questions on candidate gaps and system complexity with honest bridge answers:")
            for i, p in enumerate(pack.technical_probes, 1):
                with st.container():
                    st.markdown(f"**Probe {i}: {p.question}**")
                    st.markdown(f'<span class="status-badge badge-score">[Focus: {p.target_skill_or_gap}]</span>', unsafe_allow_html=True)
                    st.markdown("**Honest Bridge Answer:**")
                    st.info(p.honest_bridge_answer)
                    if p.key_talking_points:
                        st.markdown("**Key Talking Points to Hit:**")
                        for pt in p.key_talking_points:
                            st.write(f"• {pt}")
                    st.markdown("---")

        with tab_star:
            st.caption("Concrete project achievements structured as Situation, Task, Action, and Result:")
            for s in pack.star_stories:
                with st.container():
                    st.markdown(f"**{s.title}**")
                    comp_tags = " ".join(f'<span class="status-badge badge-applied">[{c}]</span>' for c in s.demonstrated_competencies)
                    st.markdown(comp_tags, unsafe_allow_html=True)
                    st.write(f"**Situation:** {s.situation}")
                    st.write(f"**Task:** {s.task}")
                    st.write(f"**Action:** {s.action}")
                    st.write(f"**Result:** {s.result}")
                    st.markdown("---")

        with tab_reverse:
            st.caption("High-signal questions to ask interviewers that demonstrate senior systems maturity:")
            for i, r in enumerate(pack.reverse_questions, 1):
                with st.container():
                    st.markdown(f"**Question {i}:** \"{r.question}\"")
                    st.caption(f"Strategic Intent: {r.strategic_intent}")
                    st.markdown("---")
