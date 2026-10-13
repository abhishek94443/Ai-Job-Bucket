import logging
import streamlit as st
from typing import Optional, List, Dict, Any

from src.ai_service import (
    AIConfigurationError,
    AIDailyQuotaExhaustedError,
    AIServiceError,
)
from src.candidate_profile import (
    CandidateProfile,
    DEFAULT_CANDIDATE_PROFILE,
    DEFAULT_TARGET_CITIES,
)
from src.resume_parser import ResumeParser, ResumeParseResult, ResumeParseError
from src.resume_service import ResumeService

logger = logging.getLogger(__name__)


def _parse_comma_list(val: str) -> List[str]:
    """Helper to parse and deduplicate comma-separated strings."""
    if not val:
        return []
    items = [x.strip() for x in val.split(",") if x.strip()]
    seen = set()
    result = []
    for item in items:
        if item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result


def validate_experience_input(
    entered_exp: Optional[float],
    confirm_exp: bool,
) -> tuple[Optional[float], bool, Optional[str]]:
    """
    Validates experience inputs submitted via candidate profile forms.
    Returns:
        (final_exp, final_confirmed, error_message)
    Rules:
    - If user enters a positive experience value without confirming, returns a validation error.
    - If user enters experience and explicitly confirms, returns (float(entered_exp), True, None).
    - If user explicitly enters 0.0 with confirmation, returns (0.0, True, None).
    - If experience is omitted/None, returns (None, False, None).
    - Never silently discards user-entered experience.
    """
    if entered_exp is not None and float(entered_exp) > 0.0 and not confirm_exp:
        return (
            None,
            False,
            f"You entered {float(entered_exp):g} years for actual professional experience, but did not check the confirmation checkbox ('✅ I explicitly confirm this actual experience figure is accurate'). Please check the confirmation box to verify your experience, or clear the field if unknown.",
        )
    if entered_exp is not None and confirm_exp:
        return (float(entered_exp), True, None)
    return (None, False, None)


def verify_profile_persistence(
    submitted: CandidateProfile,
    persisted: CandidateProfile,
) -> tuple[bool, Optional[str]]:
    """
    Verifies that the persisted CandidateProfile record accurately reflects submitted intent.
    Returns (True, None) if verified, or (False, failure_reason) on mismatch.
    """
    if submitted.total_experience_years is not None:
        if persisted.total_experience_years != submitted.total_experience_years:
            return (
                False,
                f"Persisted total_experience_years ({persisted.total_experience_years}) does not match submitted ({submitted.total_experience_years}).",
            )
        if persisted.experience_confirmed != submitted.experience_confirmed:
            return (
                False,
                f"Persisted experience_confirmed ({persisted.experience_confirmed}) does not match submitted ({submitted.experience_confirmed}).",
            )
    else:
        if persisted.total_experience_years is not None:
            return (
                False,
                f"Expected experience to be None, but persisted as {persisted.total_experience_years}.",
            )
        if persisted.experience_confirmed is not False:
            return (
                False,
                f"Expected experience_confirmed to be False for unspecified experience, but persisted as {persisted.experience_confirmed}.",
            )
    return (True, None)


def render_resume_profile_page():
    """
    Renders the dedicated Resume & Candidate Profile management page in Streamlit.
    Enforces:
    1. Zero silent factual invention: Missing experience is explicitly represented as unknown.
    2. Strict separation: Extracted facts vs. inferred capabilities vs. editable search preferences.
    3. Failure safety: Failed uploads or extraction never alter the active database profile.
    4. Explicit user confirmation: Review confirmation required before transactional activation in Neon.
    5. Privacy: No automatic LLM calls on file selection; clear free-tier privacy notice.
    6. Protected deletion: Destructive resume removal requires explicit confirmation.
    """
    from src.ui_theme import inject_clean_theme
    inject_clean_theme()

    st.title("Candidate Resume & Profile Management")
    st.caption("Upload, review, customize, and transactionally activate your profile stored in Neon PostgreSQL.")

    resume_service = ResumeService()

    # ---------------------------------------------------------
    # 1. Active Resume & Profile Status Card
    # ---------------------------------------------------------
    st.markdown("### Active Profile Status")
    if "profile_save_success" in st.session_state:
        st.success(st.session_state.pop("profile_save_success"))

    active_meta = resume_service.get_active_resume_metadata()
    has_active_db = resume_service.has_active_profile()
    active_profile = resume_service.get_active_profile()

    status_col1, status_col2 = st.columns([3, 2])
    with status_col1:
        if active_meta and has_active_db:
            st.success(f"**Active Resume:** `{active_meta.get('file_name')}` (Version #{active_meta.get('id')})")
            created_str = str(active_meta.get("created_at", ""))[:19]
            size_kb = active_meta.get("file_size_bytes", 0) / 1024
            st.markdown(
                f"**Format:** `{active_meta.get('file_type', '').upper()}` &nbsp;|&nbsp; "
                f"**Size:** `{size_kb:.1f} KB` &nbsp;|&nbsp; "
                f"**SHA-256:** `{active_meta.get('content_hash', '')[:16]}...` &nbsp;|&nbsp; "
                f"**Activated:** `{created_str}`"
            )
        else:
            st.info("**No active resume uploaded in Neon PostgreSQL yet.**")
            st.caption("Operating with baseline seed profile for Abhishek Dwivedi. Upload your resume below to personalize discovery.")

    with status_col2:
        # Summary metrics of the active profile
        c_exp, c_skills, c_title = st.columns(3)
        with c_exp:
            if active_profile.total_experience_years is not None:
                exp_label = f"{active_profile.total_experience_years:g} YOE"
                exp_help = "Verified" if active_profile.experience_confirmed else "Unconfirmed"
            else:
                exp_label = "Unknown"
                exp_help = "Experience not specified"
            st.metric("Experience", exp_label, help=exp_help)
        with c_skills:
            st.metric("Confirmed Skills", len(active_profile.all_confirmed_skills))
        with c_title:
            st.metric("Target Title", active_profile.target_title or "Software Engineer")

    # Expandable details of active profile
    with st.expander("View Active Profile Details & Verified Skills"):
        d1, d2 = st.columns(2)
        with d1:
            st.markdown("**Verified Resume Facts:**")
            st.write(f"- **Name:** {active_profile.name}")
            if active_profile.total_experience_years is not None:
                exp_status = "Verified" if active_profile.experience_confirmed else "Unconfirmed"
                st.write(f"- **Experience:** {active_profile.total_experience_years:g} YOE ({exp_status})")
            else:
                st.write("- **Experience:** Unknown / Unspecified")
            st.write(f"- **Languages:** {', '.join(active_profile.languages) or 'None'}")
            st.write(f"- **Frameworks:** {', '.join(active_profile.frameworks) or 'None'}")
            st.write(f"- **Databases:** {', '.join(active_profile.databases) or 'None'}")
            st.write(f"- **Messaging / Cache:** {', '.join(active_profile.messaging_and_cache) or 'None'}")
            st.write(f"- **DevOps / Cloud:** {', '.join(active_profile.devops_and_tools) or 'None'}")
        with d2:
            st.markdown("**Inferred Capabilities:**")
            if active_profile.inferred_capabilities:
                for cap in active_profile.inferred_capabilities:
                    st.caption(f"• {cap}")
            else:
                st.caption("_None recorded_")

            st.markdown("**Configured Search Preferences:**")
            st.write(f"- **Target Title:** {active_profile.target_title}")
            st.write(f"- **Experience Range:** {active_profile.experience_range_min or 0.0:g} - {active_profile.experience_range_max or 3.0:g} YOE")
            st.write(f"- **Hard Limit Cutoff:** {active_profile.hard_limit_years or 4.0:g} YOE")
            st.write(f"- **Remote Preference:** `{active_profile.remote_preference}`")
            st.write(f"- **Target Cities ({len(active_profile.target_cities)}):** {', '.join(active_profile.target_cities[:5])}...")

    # Edit Active Profile action button & form
    if active_meta and has_active_db:
        col_act1, col_act2, col_act3 = st.columns([1.5, 2.5, 2])
        with col_act1:
            if not st.session_state.get("edit_active_mode", False):
                if st.button("Edit Profile", type="secondary", use_container_width=True, key="btn_open_edit_profile"):
                    st.session_state["edit_active_mode"] = True
                    # Initialize session state variables once from database record
                    st.session_state["edit_active_entered_exp"] = (
                        float(active_profile.total_experience_years)
                        if active_profile.total_experience_years is not None
                        else None
                    )
                    st.session_state["edit_active_confirm_exp"] = bool(
                        active_profile.experience_confirmed and active_profile.total_experience_years is not None
                    )
                    st.session_state["edit_active_cand_name"] = active_profile.name or "Candidate"
                    st.session_state["edit_active_lang"] = ", ".join(active_profile.languages)
                    st.session_state["edit_active_fw"] = ", ".join(active_profile.frameworks)
                    st.session_state["edit_active_db"] = ", ".join(active_profile.databases)
                    st.session_state["edit_active_msg"] = ", ".join(active_profile.messaging_and_cache)
                    st.session_state["edit_active_devops"] = ", ".join(active_profile.devops_and_tools)
                    st.session_state["edit_active_api"] = ", ".join(active_profile.apis_and_auth)
                    st.session_state["edit_active_inferred"] = ", ".join(active_profile.inferred_capabilities)
                    st.session_state["edit_active_target_title"] = active_profile.target_title or "Software Engineer"
                    st.session_state["edit_active_range_min"] = float(active_profile.experience_range_min if active_profile.experience_range_min is not None else 0.0)
                    st.session_state["edit_active_range_max"] = float(active_profile.experience_range_max if active_profile.experience_range_max is not None else 3.0)
                    st.session_state["edit_active_hard_limit"] = float(active_profile.hard_limit_years if active_profile.hard_limit_years is not None else 4.0)
                    st.session_state["edit_active_remote_pref"] = active_profile.remote_preference if active_profile.remote_preference in ["flexible", "remote_only", "hybrid", "onsite"] else "flexible"
                    st.session_state["edit_active_cities"] = [c for c in active_profile.target_cities if c in DEFAULT_TARGET_CITIES] or DEFAULT_TARGET_CITIES[:6]
                    st.rerun()

        with col_act2:
            if not st.session_state.get("edit_active_mode", False):
                if st.button(
                    "Re-Analyze Stored Resume",
                    type="primary",
                    use_container_width=True,
                    key="btn_reanalyze_stored_resume",
                    help="Reprocesses the currently active resume through Gemini AI semantic analysis without needing to re-upload.",
                ):
                    if st.session_state.get("is_extracting_resume", False):
                        st.warning("Semantic analysis is already in progress. Please wait...")
                    else:
                        st.session_state["is_extracting_resume"] = True
                        try:
                            stored_resume = resume_service.get_active_resume_content()
                            if not stored_resume or not stored_resume.get("file_bytes"):
                                st.error("Stored resume document bytes not found in database. Please re-upload your resume.")
                            else:
                                with st.spinner("Analyzing stored resume semantically with Gemini AI..."):
                                    parsed_res, extracted_prof = resume_service.parse_and_extract_for_review(
                                        file_bytes=stored_resume["file_bytes"],
                                        file_name=stored_resume["file_name"],
                                        candidate_name=active_profile.name,
                                        use_ai=True,
                                    )
                                st.session_state["staged_parsed"] = parsed_res
                                st.session_state["staged_profile"] = extracted_prof
                                st.session_state["extraction_method"] = "Gemini AI (Semantic Re-Analysis)"
                                st.rerun()
                        except AIConfigurationError as cfg_err:
                            st.error(f"Gemini AI Configuration Error: {str(cfg_err)}")
                        except Exception as exc:
                            st.error(f"Failed to re-analyze resume: {str(exc)}")
                        finally:
                            st.session_state["is_extracting_resume"] = False

        # Render Edit Profile Form if edit mode is active
        if st.session_state.get("edit_active_mode", False):
            # Guard: ensure session state has defaults if user reloaded page directly while in edit mode
            if "edit_active_entered_exp" not in st.session_state:
                st.session_state["edit_active_entered_exp"] = (
                    float(active_profile.total_experience_years)
                    if active_profile.total_experience_years is not None
                    else None
                )
            if "edit_active_confirm_exp" not in st.session_state:
                st.session_state["edit_active_confirm_exp"] = bool(
                    active_profile.experience_confirmed and active_profile.total_experience_years is not None
                )
            if "edit_active_cand_name" not in st.session_state:
                st.session_state["edit_active_cand_name"] = active_profile.name or "Candidate"
            if "edit_active_lang" not in st.session_state:
                st.session_state["edit_active_lang"] = ", ".join(active_profile.languages)
            if "edit_active_fw" not in st.session_state:
                st.session_state["edit_active_fw"] = ", ".join(active_profile.frameworks)
            if "edit_active_db" not in st.session_state:
                st.session_state["edit_active_db"] = ", ".join(active_profile.databases)
            if "edit_active_msg" not in st.session_state:
                st.session_state["edit_active_msg"] = ", ".join(active_profile.messaging_and_cache)
            if "edit_active_devops" not in st.session_state:
                st.session_state["edit_active_devops"] = ", ".join(active_profile.devops_and_tools)
            if "edit_active_api" not in st.session_state:
                st.session_state["edit_active_api"] = ", ".join(active_profile.apis_and_auth)
            if "edit_active_inferred" not in st.session_state:
                st.session_state["edit_active_inferred"] = ", ".join(active_profile.inferred_capabilities)
            if "edit_active_target_title" not in st.session_state:
                st.session_state["edit_active_target_title"] = active_profile.target_title or "Software Engineer"
            if "edit_active_range_min" not in st.session_state:
                st.session_state["edit_active_range_min"] = float(active_profile.experience_range_min if active_profile.experience_range_min is not None else 0.0)
            if "edit_active_range_max" not in st.session_state:
                st.session_state["edit_active_range_max"] = float(active_profile.experience_range_max if active_profile.experience_range_max is not None else 3.0)
            if "edit_active_hard_limit" not in st.session_state:
                st.session_state["edit_active_hard_limit"] = float(active_profile.hard_limit_years if active_profile.hard_limit_years is not None else 4.0)
            if "edit_active_remote_pref" not in st.session_state:
                st.session_state["edit_active_remote_pref"] = active_profile.remote_preference if active_profile.remote_preference in ["flexible", "remote_only", "hybrid", "onsite"] else "flexible"
            if "edit_active_cities" not in st.session_state:
                st.session_state["edit_active_cities"] = [c for c in active_profile.target_cities if c in DEFAULT_TARGET_CITIES] or DEFAULT_TARGET_CITIES[:6]

            st.markdown("### ✏️ Edit Active Candidate Profile")
            st.caption(
                f"Editing active profile linked to Resume Version #{active_meta.get('id')} (`{active_meta.get('file_name')}`). "
                "Update verified skills, confirm experience, adjust inferred capabilities, and customize search preferences without re-uploading."
            )

            with st.form("edit_active_candidate_profile_form"):
                # Section A: Candidate Identity & Professional Experience
                st.markdown("#### 1. Candidate Identity & Actual Professional Experience (Career Fact)")
                st.caption(
                    "Your verified actual career experience. This fact evaluates job seniority fit and is strictly separate from search preferences."
                )
                
                # Visual indicator of experience verification state
                if active_profile.total_experience_years is None:
                    st.warning("⚠️ **Experience Status:** Currently unknown / not specified. Enter actual experience below and check the confirmation box to verify it.")
                elif not active_profile.experience_confirmed:
                    st.warning(f"⚠️ **Experience Status:** Currently unconfirmed ({active_profile.total_experience_years:g} YOE). Check the confirmation box below to verify it.")
                else:
                    st.success(f"✅ **Experience Status:** Confirmed & verified ({active_profile.total_experience_years:g} YOE).")

                cand_name = st.text_input(
                    "Candidate Name",
                    key="edit_active_cand_name",
                )

                col_exp1, col_exp2 = st.columns([3, 2])
                with col_exp1:
                    entered_exp = st.number_input(
                        "Actual Professional Experience (Years)",
                        min_value=0.0,
                        max_value=50.0,
                        step=0.25,
                        format="%.2f",
                        placeholder="Leave blank if unknown / enter e.g. 2.0",
                        help="Years of full-time professional engineering experience. Fractional values allowed (e.g. 1.5, 2.0, 2.25). Check the confirmation box to verify.",
                        key="edit_active_entered_exp",
                    )
                with col_exp2:
                    st.write("")
                    st.write("")
                    confirm_exp = st.checkbox(
                        "✅ I explicitly confirm this experience figure is accurate",
                        help="Experience is only treated as verified when explicitly confirmed by the candidate.",
                        key="edit_active_confirm_exp",
                    )

                st.markdown("---")

                # Section B: Technical Skills (Verified Resume Facts)
                st.markdown("#### 2. Technical Skills (Verified Resume Facts)")
                st.caption("Comma-separated technical skills evidenced in the candidate document.")

                col_s1, col_s2 = st.columns(2)
                with col_s1:
                    lang_str = st.text_input("Languages", key="edit_active_lang")
                    fw_str = st.text_input("Frameworks", key="edit_active_fw")
                    db_str = st.text_input("Databases", key="edit_active_db")
                with col_s2:
                    msg_str = st.text_input("Messaging & Cache", key="edit_active_msg")
                    devops_str = st.text_input("DevOps & Tools", key="edit_active_devops")
                    api_str = st.text_input("APIs & Architecture", key="edit_active_api")

                st.markdown("---")

                # Section C: Inferred Capabilities (Demonstrated Competencies)
                st.markdown("#### 3. Inferred Capabilities (Demonstrated Competencies)")
                st.caption("Architectural engineering capabilities inferred from demonstrated projects, visibly distinguished from raw keywords.")
                inferred_str = st.text_area(
                    "Inferred Capabilities (one per line or comma-separated)",
                    help="High-level engineering patterns (e.g., Event-driven microservices architecture, Relational data modeling).",
                    key="edit_active_inferred",
                )

                st.markdown("---")

                # Section D: User Search Preferences (Configured by user)
                st.markdown("#### 4. Job Search & Filtering Preferences (Discovery Parameters Only)")
                st.caption(
                    "Your search criteria. These are discovery filter preferences, NOT resume facts, and guide crawler discovery without altering actual career experience."
                )

                col_p1, col_p2 = st.columns(2)
                with col_p1:
                    target_title = st.text_input(
                        "Target Role Title",
                        help="Primary role title to query across job platforms.",
                        key="edit_active_target_title",
                    )
                    range_min = st.number_input(
                        "Search Experience Range Minimum (Years)",
                        min_value=0.0,
                        max_value=50.0,
                        step=0.25,
                        format="%.2f",
                        key="edit_active_range_min",
                    )
                    range_max = st.number_input(
                        "Search Experience Range Maximum (Years)",
                        min_value=0.0,
                        max_value=50.0,
                        step=0.25,
                        format="%.2f",
                        key="edit_active_range_max",
                    )
                with col_p2:
                    hard_limit = st.number_input(
                        "Hard Ceiling Cutoff (Years)",
                        min_value=0.0,
                        max_value=50.0,
                        step=0.25,
                        format="%.2f",
                        help="Jobs strictly demanding more than this threshold will be classified as OVER_LIMIT.",
                        key="edit_active_hard_limit",
                    )
                    remote_pref = st.selectbox(
                        "Remote Work Preference",
                        options=["flexible", "remote_only", "hybrid", "onsite"],
                        key="edit_active_remote_pref",
                    )
                    selected_cities = st.multiselect(
                        "Target Search Locations",
                        options=DEFAULT_TARGET_CITIES,
                        help="Cities included in geographic discovery filters.",
                        key="edit_active_cities",
                    )

                st.markdown("---")

                col_sub1, col_sub2 = st.columns([3, 1])
                with col_sub1:
                    save_submitted = st.form_submit_button(
                        "💾 Save Profile Changes in Neon",
                        type="primary",
                        use_container_width=True,
                    )
                with col_sub2:
                    cancel_edit_btn = st.form_submit_button("Cancel / Discard", use_container_width=True)

            if cancel_edit_btn:
                st.session_state["edit_active_mode"] = False
                for k in [
                    "edit_active_entered_exp",
                    "edit_active_confirm_exp",
                    "edit_active_cand_name",
                    "edit_active_lang",
                    "edit_active_fw",
                    "edit_active_db",
                    "edit_active_msg",
                    "edit_active_devops",
                    "edit_active_api",
                    "edit_active_inferred",
                    "edit_active_target_title",
                    "edit_active_range_min",
                    "edit_active_range_max",
                    "edit_active_hard_limit",
                    "edit_active_remote_pref",
                    "edit_active_cities",
                ]:
                    st.session_state.pop(k, None)
                st.rerun()

            if save_submitted:
                final_exp, final_confirmed, exp_err = validate_experience_input(entered_exp, confirm_exp)
                if exp_err:
                    st.error(f"⚠️ {exp_err}")
                else:
                    updated_profile = CandidateProfile(
                        name=cand_name.strip() or "Candidate",
                        total_experience_years=final_exp,
                        experience_confirmed=final_confirmed,
                        languages=_parse_comma_list(lang_str),
                        frameworks=_parse_comma_list(fw_str),
                        databases=_parse_comma_list(db_str),
                        messaging_and_cache=_parse_comma_list(msg_str),
                        devops_and_tools=_parse_comma_list(devops_str),
                        apis_and_auth=_parse_comma_list(api_str),
                        inferred_capabilities=_parse_comma_list(inferred_str),
                        target_title=target_title.strip() or "Software Engineer",
                        experience_range_min=float(range_min),
                        experience_range_max=float(range_max),
                        hard_limit_years=float(hard_limit),
                        target_cities=selected_cities if selected_cities else DEFAULT_TARGET_CITIES,
                        remote_preference=remote_pref,
                        preferences_source="user_configured",
                    )

                    try:
                        with st.spinner("Saving profile changes to Neon PostgreSQL..."):
                            resume_id, saved_prof = resume_service.update_active_profile(updated_profile)

                        verified, verify_err = verify_profile_persistence(updated_profile, saved_prof)
                        if not verified:
                            st.error(f"❌ Persistence verification failed: {verify_err}")
                            logger.error("Profile persistence verification failed: %s", verify_err)
                        else:
                            st.session_state["edit_active_mode"] = False
                            for k in [
                                "edit_active_entered_exp",
                                "edit_active_confirm_exp",
                                "edit_active_cand_name",
                                "edit_active_lang",
                                "edit_active_fw",
                                "edit_active_db",
                                "edit_active_msg",
                                "edit_active_devops",
                                "edit_active_api",
                                "edit_active_inferred",
                                "edit_active_target_title",
                                "edit_active_range_min",
                                "edit_active_range_max",
                                "edit_active_hard_limit",
                                "edit_active_remote_pref",
                                "edit_active_cities",
                            ]:
                                st.session_state.pop(k, None)

                            exp_info = (
                                f"{saved_prof.total_experience_years:g} YOE (Confirmed)"
                                if saved_prof.experience_confirmed and saved_prof.total_experience_years is not None
                                else "Unknown / Unconfirmed"
                            )
                            st.session_state["profile_save_success"] = (
                                f"🎉 Successfully saved profile changes in Neon PostgreSQL! (Linked to Resume Version #{resume_id}) — Experience: **{exp_info}**"
                            )
                            st.rerun()
                    except Exception as exc:
                        st.error(f"❌ Failed to save profile edits: {str(exc)}")
                        logger.error("Profile edit failure: %s", str(exc))

        # Destructive Resume Deletion Section (Requires explicit confirmation)
        with st.expander("⚠️ Danger Zone: Delete Active Resume"):
            st.warning("Deleting the active resume will permanently remove the active profile, extracted skills, and all AI job recommendations.")
            confirm_delete = st.checkbox(
                "I explicitly confirm that I want to delete the active resume and candidate profile from Neon PostgreSQL.",
                key="confirm_delete_resume_checkbox",
            )
            if st.button("🗑️ Permanently Delete Active Resume", type="secondary", disabled=not confirm_delete):
                try:
                    deleted = resume_service.delete_active_resume()
                    if deleted:
                        st.success("Active resume version and profile removed from database.")
                        if "staged_profile" in st.session_state:
                            del st.session_state["staged_profile"]
                        if "staged_parsed" in st.session_state:
                            del st.session_state["staged_parsed"]
                        st.rerun()
                    else:
                        st.info("No active resume found to delete.")
                except Exception as exc:
                    st.error(f"Failed to delete resume: {exc}")

    st.divider()

    # ---------------------------------------------------------
    # 2. Resume Upload Section
    # ---------------------------------------------------------
    st.markdown("### 📤 Upload or Replace Resume")
    st.caption("Upload a `.pdf` or `.docx` document (max 5 MB). File contents are validated before processing.")

    uploaded_file = st.file_uploader(
        "Select Resume Document",
        type=["pdf", "docx"],
        key="resume_file_uploader",
        help="Upload PDF or DOCX resume. Max 5 MB limit strictly enforced.",
    )

    if uploaded_file is not None:
        file_bytes = uploaded_file.getvalue()
        file_name = uploaded_file.name
        file_size_kb = len(file_bytes) / 1024

        st.info(f"📁 Selected file: **{file_name}** ({file_size_kb:.1f} KB)")

        # Validate file before presenting extraction options
        try:
            # Parsing validates size, magic bytes, structure, and extracts text without modifying DB
            pre_parsed = ResumeParser.parse_bytes(file_bytes, file_name)
            st.success(f"✅ Document validated: `{pre_parsed.file_type.upper()}` ({pre_parsed.char_count} characters, ~{pre_parsed.word_count} words).")
        except ResumeParseError as rpe:
            st.error(f"❌ Resume validation error: {str(rpe)}")
            return
        except Exception as exc:
            st.error(f"❌ Failed to parse resume document: {str(exc)}")
            return

        # Extraction Options (AI-First primary vs Explicit Offline Fallback)
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            ai_btn = st.button(
                "✨ Analyze Resume with Gemini AI (AI-First)",
                type="primary",
                use_container_width=True,
                help="Default semantic resume analysis using Google Gemini. Validated against versioned Pydantic models with grounded career facts.",
            )
        with col_btn2:
            deterministic_btn = st.button(
                "⚡ Offline / Deterministic Fallback",
                use_container_width=True,
                help="Explicit user action: parse offline using keyword/regex heuristics with zero API calls. Marked as fallback.",
            )

        st.caption(
            "🔒 **Privacy & AI Notice:** AI semantic analysis is the primary analyzer. Contact information (emails, phones, addresses) "
            "is scrubbed before transmission. Data is subject to Google Gemini Free Tier terms with zero paid overage. "
            "Offline extraction is 100% local."
        )

        # Trigger Extraction with duplicate-submission guard
        if ai_btn or deterministic_btn:
            if st.session_state.get("is_extracting_resume", False):
                st.warning("⏳ An extraction is already in progress. Please wait...")
            else:
                st.session_state["is_extracting_resume"] = True
                use_ai_flag = bool(ai_btn)
                explicit_fallback_flag = bool(deterministic_btn)
                try:
                    with st.spinner("Analyzing resume semantically with Gemini AI..." if use_ai_flag else "Extracting profile deterministically..."):
                        parsed_res, extracted_prof = resume_service.parse_and_extract_for_review(
                            file_bytes=file_bytes,
                            file_name=file_name,
                            use_ai=use_ai_flag,
                            explicit_fallback=explicit_fallback_flag,
                        )
                    st.session_state["staged_parsed"] = parsed_res
                    st.session_state["staged_profile"] = extracted_prof
                    if extracted_prof.analysis_provenance == "AI_LIVE":
                        st.session_state["extraction_method"] = "Gemini AI (Semantic)"
                    elif extracted_prof.analysis_provenance == "DETERMINISTIC_FALLBACK":
                        if use_ai_flag:
                            st.session_state["extraction_method"] = "Deterministic Fallback (Quota Exhausted)"
                        else:
                            st.session_state["extraction_method"] = "Deterministic Fallback (Offline Mode)"
                    else:
                        st.session_state["extraction_method"] = "Extracted"
                    st.rerun()
                except AIConfigurationError as cfg_err:
                    st.error(
                        f"❌ Gemini AI Configuration Error: {str(cfg_err)}\n\n"
                        "The current active profile has been preserved. You can configure `GEMINI_API_KEY` or "
                        "click 'Offline / Deterministic Fallback' to extract locally."
                    )
                except Exception as exc:
                    st.error(
                        f"❌ Resume Analysis Failed: {str(exc)}\n\n"
                        "The current active profile in Neon PostgreSQL remains untouched."
                    )
                finally:
                    st.session_state["is_extracting_resume"] = False

    # ---------------------------------------------------------
    # 3. Candidate Profile Review & Confirmation Form
    # ---------------------------------------------------------
    staged_profile: Optional[CandidateProfile] = st.session_state.get("staged_profile")
    staged_parsed: Optional[ResumeParseResult] = st.session_state.get("staged_parsed")

    if staged_profile and staged_parsed:
        st.divider()
        st.markdown(f"### ✏️ Candidate Profile Review ({st.session_state.get('extraction_method', 'Extracted')})")
        st.caption("Review extracted facts, verify experience years, and configure search preferences before activating in Neon PostgreSQL.")

        # Rich AI Semantic Analysis Grounding Cards
        if staged_profile.resume_analysis:
            analysis_dict = staged_profile.resume_analysis
            with st.expander("🔍 AI Semantic Analysis Insights & Grounding", expanded=True):
                if analysis_dict.get("professional_summary"):
                    st.markdown(f"**Professional Summary:** {analysis_dict['professional_summary']}")

                exp_calc = analysis_dict.get("experience_calculation") or {}
                calc_method = exp_calc.get("calculation_method", "Not specified")
                is_ambig = exp_calc.get("is_ambiguous", False)
                gaps = exp_calc.get("conflicts_or_gaps", [])
                assumptions = exp_calc.get("assumptions", [])

                st.markdown(f"**Experience Calculation Basis:** {calc_method}")
                if assumptions:
                    st.caption("Assumptions: " + "; ".join(assumptions))
                if is_ambig or gaps:
                    st.warning(f"⚠️ **Ambiguities / Conflicts in Dates:** {'; '.join(gaps) if gaps else 'Timeline has ambiguous or overlapping periods.'}")

                uncertainties = analysis_dict.get("uncertainties", [])
                if uncertainties:
                    st.warning(f"⚠️ **Reported Uncertainties:** {'; '.join(uncertainties)}")

                work_hist = analysis_dict.get("work_history", [])
                if work_hist:
                    st.markdown("**Evidenced Work History:**")
                    for item in work_hist:
                        dur = f" ({item.get('duration_years')} yrs)" if item.get('duration_years') else ""
                        dates = f"{item.get('start_date', '')} - {item.get('end_date', 'Present')}"
                        st.markdown(f"- **{item.get('job_title', 'Role')}** at **{item.get('employer', 'Company')}** ({dates}){dur}")
                        if item.get("technologies_used"):
                            st.caption(f"  Tech: {', '.join(item['technologies_used'])}")

                suggested_titles = analysis_dict.get("suggested_role_titles", [])
                suggested_families = analysis_dict.get("suggested_role_families", [])
                if suggested_titles or suggested_families:
                    st.info(
                        f"💡 **AI-Suggested Roles (Advisory Only):** {', '.join(suggested_titles)} "
                        f"({', '.join(suggested_families)}). "
                        "*Note: AI suggestions do not alter your job search preferences unless you choose to set them below.*"
                    )
        elif staged_profile.analysis_provenance == "DETERMINISTIC_FALLBACK":
            st.warning("⚠️ **Deterministic Fallback:** Profile was extracted using offline heuristics. Review all fields carefully before activation.")

        with st.form("candidate_profile_review_form"):
            # Section A: Candidate Identity & Experience
            st.markdown("#### 1. Candidate Identity & Professional Experience")
            col_id1, col_id2 = st.columns([2, 3])
            with col_id1:
                cand_name = st.text_input("Candidate Name", value=staged_profile.name or "Candidate")
            with col_id2:
                is_unknown = st.checkbox(
                    "Experience Unknown / Not explicitly stated in resume",
                    value=staged_profile.total_experience_years is None,
                    help="Check if resume does not state years of experience. Prevents assuming a default.",
                )

            col_exp1, col_exp2 = st.columns(2)
            with col_exp1:
                exp_val = staged_profile.total_experience_years if staged_profile.total_experience_years is not None else 0.0
                entered_exp = st.number_input(
                    "Total Professional Experience (Years)",
                    min_value=0.0,
                    max_value=40.0,
                    step=0.5,
                    value=float(exp_val),
                    disabled=is_unknown,
                    help="Years of full-time professional engineering experience.",
                )
            with col_exp2:
                st.write("")  # Vertical alignment spacer
                st.write("")
                confirm_exp = st.checkbox(
                    "✅ I explicitly confirm this experience figure is accurate",
                    value=staged_profile.experience_confirmed,
                    help="Experience is only treated as verified when explicitly confirmed by the candidate.",
                )

            st.markdown("---")

            # Section B: Factual Technical Skills (Resume-derived)
            st.markdown("#### 2. Technical Skills (Extracted Resume Facts)")
            st.caption("Comma-separated technical skills explicitly evidenced in the uploaded document.")

            col_s1, col_s2 = st.columns(2)
            with col_s1:
                lang_str = st.text_input("Languages", value=", ".join(staged_profile.languages))
                fw_str = st.text_input("Frameworks", value=", ".join(staged_profile.frameworks))
                db_str = st.text_input("Databases", value=", ".join(staged_profile.databases))
            with col_s2:
                msg_str = st.text_input("Messaging & Cache", value=", ".join(staged_profile.messaging_and_cache))
                devops_str = st.text_input("DevOps & Tools", value=", ".join(staged_profile.devops_and_tools))
                api_str = st.text_input("APIs & Architecture", value=", ".join(staged_profile.apis_and_auth))

            st.markdown("---")

            # Section C: Inferred Capabilities (Distinct from factual keywords)
            st.markdown("#### 3. Inferred Capabilities (Demonstrated Competencies)")
            st.caption("Architectural engineering capabilities inferred from demonstrated projects, visibly distinguished from raw keywords.")
            inferred_str = st.text_area(
                "Inferred Capabilities (one per line or comma-separated)",
                value=", ".join(staged_profile.inferred_capabilities),
                help="High-level engineering patterns (e.g., Event-driven microservices architecture, Relational data modeling).",
            )

            st.markdown("---")

            # Section D: User Search Preferences (Configured by user, preserved across uploads)
            st.markdown("#### 4. Job Search & Filtering Preferences (User-Configured)")
            st.caption("Your search criteria. These are editable preferences, NOT resume facts, and guide crawler discovery.")

            col_p1, col_p2 = st.columns(2)
            with col_p1:
                target_title = st.text_input(
                    "Target Role Title",
                    value=staged_profile.target_title or "Software Engineer",
                    help="Primary role title to query across job platforms.",
                )
                range_min = st.number_input(
                    "Search Experience Range Minimum (Years)",
                    min_value=0.0,
                    max_value=30.0,
                    step=0.5,
                    value=float(staged_profile.experience_range_min if staged_profile.experience_range_min is not None else 0.0),
                )
                range_max = st.number_input(
                    "Search Experience Range Maximum (Years)",
                    min_value=0.0,
                    max_value=30.0,
                    step=0.5,
                    value=float(staged_profile.experience_range_max if staged_profile.experience_range_max is not None else 3.0),
                )
            with col_p2:
                hard_limit = st.number_input(
                    "Hard Ceiling Cutoff (Years)",
                    min_value=0.0,
                    max_value=30.0,
                    step=0.5,
                    value=float(staged_profile.hard_limit_years if staged_profile.hard_limit_years is not None else 4.0),
                    help="Jobs strictly demanding more than this threshold will be classified as OVER_LIMIT.",
                )
                remote_pref = st.selectbox(
                    "Remote Work Preference",
                    options=["flexible", "remote_only", "hybrid", "onsite"],
                    index=["flexible", "remote_only", "hybrid", "onsite"].index(staged_profile.remote_preference)
                    if staged_profile.remote_preference in ["flexible", "remote_only", "hybrid", "onsite"] else 0,
                )
                # Target cities multiselect
                current_cities = [c for c in staged_profile.target_cities if c in DEFAULT_TARGET_CITIES] or DEFAULT_TARGET_CITIES[:6]
                selected_cities = st.multiselect(
                    "Target Search Locations",
                    options=DEFAULT_TARGET_CITIES,
                    default=current_cities,
                    help="Cities included in geographic discovery filters.",
                )

            st.markdown("---")

            # Form Action Buttons
            col_sub1, col_sub2 = st.columns([3, 1])
            with col_sub1:
                activate_submitted = st.form_submit_button(
                    "💾 Confirm Review & Activate Profile in Neon",
                    type="primary",
                    use_container_width=True,
                )
            with col_sub2:
                discard_btn = st.form_submit_button("Cancel / Discard", use_container_width=True)

        if discard_btn:
            del st.session_state["staged_profile"]
            del st.session_state["staged_parsed"]
            st.rerun()

        if activate_submitted:
            if st.session_state.get("is_activating_profile", False):
                st.warning("⏳ Profile activation is already in progress. Please wait...")
            else:
                # Experience validation with explicit confirmation enforcement
                exp_to_val = None if is_unknown else float(entered_exp)
                final_exp, final_confirmed, exp_err = validate_experience_input(exp_to_val, confirm_exp)

                if exp_err:
                    st.error(f"⚠️ {exp_err}")
                else:
                    st.session_state["is_activating_profile"] = True
                    try:
                        reviewed_profile = CandidateProfile(
                            name=cand_name.strip() or "Candidate",
                            total_experience_years=final_exp,
                            experience_confirmed=final_confirmed,
                            languages=_parse_comma_list(lang_str),
                            frameworks=_parse_comma_list(fw_str),
                            databases=_parse_comma_list(db_str),
                            messaging_and_cache=_parse_comma_list(msg_str),
                            devops_and_tools=_parse_comma_list(devops_str),
                            apis_and_auth=_parse_comma_list(api_str),
                            inferred_capabilities=_parse_comma_list(inferred_str),
                            target_title=target_title.strip() or "Software Engineer",
                            experience_range_min=float(range_min),
                            experience_range_max=float(range_max),
                            hard_limit_years=float(hard_limit),
                            target_cities=selected_cities if selected_cities else DEFAULT_TARGET_CITIES,
                            remote_preference=remote_pref,
                            preferences_source="user_configured",
                            analysis_provenance=staged_profile.analysis_provenance,
                            resume_analysis=staged_profile.resume_analysis,
                        )

                        with st.spinner("Activating profile in Neon PostgreSQL..."):
                            resume_id, active_res = resume_service.activate_reviewed_profile(
                                parsed=staged_parsed,
                                profile=reviewed_profile,
                            )

                        verified, verify_err = verify_profile_persistence(reviewed_profile, active_res)
                        if not verified:
                            st.error(f"❌ Persistence verification mismatch: {verify_err}")
                        else:
                            st.success(
                                f"🎉 Successfully activated Resume Version #{resume_id} and Candidate Profile in Neon PostgreSQL!"
                            )
                            del st.session_state["staged_profile"]
                            del st.session_state["staged_parsed"]
                            st.rerun()
                    except Exception as exc:
                        st.error(f"❌ Failed to activate profile in database: {str(exc)}")
                        logger.error("Activation failure: %s", str(exc))
                    finally:
                        st.session_state["is_activating_profile"] = False
