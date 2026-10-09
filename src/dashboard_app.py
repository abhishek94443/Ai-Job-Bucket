import streamlit as st
import pandas as pd
from datetime import datetime
import json
import asyncio

from src.dashboard_service import DashboardQueryService, format_freshness
from src.db_config import DatabaseConfig
from src.pipeline import run_pipeline
from src.resume_dashboard_view import render_resume_profile_page
from src.tracker_dashboard_view import render_tracker_page
from src.tailoring_view import render_tailored_application_drawer
from src.interview_prep_view import render_interview_prep_drawer
from src.ui_theme import inject_clean_theme

st.set_page_config(
    page_title="Job Discovery Workbench",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Apply executive minimalist styling
inject_clean_theme()

# Sidebar Navigation
st.sidebar.markdown("### Navigation")
nav_choice = st.sidebar.radio(
    "Navigation Mode",
    ["Job Discovery", "Tracked Applications", "Resume & Profile", "Profile & LaTeX Workbench", "AI Models & Quotas"],
    index=0,
    label_visibility="collapsed",
)
st.sidebar.markdown("---")

if nav_choice == "Tracked Applications":
    render_tracker_page()
    st.stop()

if nav_choice == "Resume & Profile":
    render_resume_profile_page()
    st.stop()

if nav_choice == "Profile & LaTeX Workbench":
    from src.candidate_profile_view import render_candidate_profile_workbench_page
    render_candidate_profile_workbench_page()
    st.stop()

if nav_choice == "AI Models & Quotas":
    from src.model_management_view import render_model_management_page
    render_model_management_page()
    st.stop()

# Header & Refresh
col_title, col_actions = st.columns([3, 2])
with col_title:
    st.title("Java Backend Job Discovery")
    st.caption("Live engineering opportunities evaluated against Abhishek Dwivedi's active profile.")
with col_actions:
    c_time, c_btn1, c_btn2 = st.columns([1.8, 1.2, 2.0])
    with c_time:
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.write("**Refreshed:**")
        st.code(current_time, language="text")
    with c_btn1:
        if st.button("Refresh", use_container_width=True):
            st.rerun()
    with c_btn2:
        confirm_run = st.checkbox(
            "Confirm operator run",
            value=False,
            key="operator_confirm_discovery",
            help="Check to enable discovery pipeline execution"
        )
        run_now = st.button(
            "Run Pipeline",
            type="primary",
            disabled=not confirm_run,
            use_container_width=True,
            help="Disabled until operator confirmation is checked"
        )

# Trigger Discovery Pipeline when button is clicked AND explicitly confirmed
if run_now and confirm_run:
    with st.status("Running Job Discovery Pipeline (Freehire, LinkedIn, Indeed)...", expanded=True) as status_box:
        st.write("1. Searching Freehire (past 24h)...")
        st.write("2. Searching LinkedIn (past day)...")
        st.write("3. Searching Indeed (past 24h)...")
        st.write("4. Deduplicating and evaluating against active candidate profile...")
        st.write("5. Persisting results to PostgreSQL...")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            discovered_jobs = loop.run_until_complete(
                run_pipeline(limit_per_source=3, persist_to_db=True)
            )
            loop.close()
            status_box.update(
                label=f"Discovery complete. Found {len(discovered_jobs)} unique jobs (persisted to database).",
                state="complete",
                expanded=False
            )
            st.success(f"Discovered and persisted {len(discovered_jobs)} jobs across Freehire, LinkedIn, and Indeed.")
            st.rerun()
        except Exception as e:
            status_box.update(label=f"Pipeline failed: {e}", state="error")
            st.error(f"Error during discovery run: {e}")

# Database Health Check
service = DashboardQueryService()
healthy, msg = service.check_health()
if not healthy:
    st.error(f"Database Connection Error: {msg}")
    st.info("Please verify that PostgreSQL is running and environment variables are set in `.env`.")
    st.stop()

# 1. Overview Section
try:
    metrics = service.get_overview_metrics()
except Exception as e:
    st.error(f"Failed to load overview metrics: {e}")
    st.stop()

total_jobs = metrics.get("total_jobs", 0)
st.markdown("### System Overview")

m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    st.metric("Total Jobs", total_jobs)
with m2:
    high_fit_count = metrics["by_eligibility"].get("HIGH_FIT", 0)
    st.metric("High Fit (Rule)", high_fit_count)
with m3:
    prov_count = metrics["by_eligibility"].get("PROVISIONAL_NEEDS_REVIEW", 0)
    st.metric("Provisional", prov_count)
with m4:
    active_count = metrics["by_status"].get("ACTIVE", 0)
    unknown_status = metrics["by_status"].get("UNKNOWN", 0)
    st.metric("Listing Status", f"{active_count} Active / {unknown_status} Unknown")
with m5:
    li_count = metrics["by_platform"].get("linkedin", 0)
    in_count = metrics["by_platform"].get("indeed", 0)
    fh_count = metrics["by_platform"].get("freehire", 0)
    st.metric("Platforms", f"{li_count} LI / {in_count} Indeed / {fh_count} Freehire")

# AI Evaluation Summary Row
ai_live = metrics.get("ai_live_count", 0)
ai_cached = metrics.get("ai_cached_count", 0)
fallback_count = metrics.get("deterministic_fallback_count", 0)
ai_verdicts = metrics.get("by_ai_verdict", {})

ai_c1, ai_c2, ai_c3, ai_c4 = st.columns(4)
with ai_c1:
    st.metric("Live AI Evaluated", ai_live)
with ai_c2:
    st.metric("AI Cache Hits", ai_cached)
with ai_c3:
    st.metric("Quota Fallback", fallback_count)
with ai_c4:
    strong = ai_verdicts.get("STRONG_MATCH", 0)
    good = ai_verdicts.get("GOOD_MATCH", 0)
    stretch = ai_verdicts.get("STRETCH_MATCH", 0)
    mismatch = ai_verdicts.get("MISMATCH", 0)
    st.metric("AI Verdicts", f"{strong} Strong / {good} Good / {stretch} Stretch / {mismatch} Mismatch")

st.divider()

# 2. Filters Sidebar
st.sidebar.header("Filters & Sorting")

search_term = st.sidebar.text_input("Search (Title, Company, Keyword)", placeholder="e.g. Spring Boot, SAP")

# Freshness filter
posted_within_options = [
    "All time",
    "Last 24 hours",
    "Last 3 days",
    "Last 7 days",
    "Last 14 days",
    "Last 30 days"
]
selected_posted_within = st.sidebar.selectbox("Posted Within", posted_within_options)
include_unknown_dates = False
if selected_posted_within != "All time":
    include_unknown_dates = st.sidebar.checkbox(
        "Include unknown posting dates",
        value=False,
        help="Keep listings without verified posting dates visible alongside recent listings"
    )

platform_options = ["ALL", "linkedin", "indeed", "freehire"]
selected_platform = st.sidebar.selectbox("Source Platform", platform_options)

# AI filters
ai_verdict_options = ["ALL", "STRONG_MATCH", "GOOD_MATCH", "STRETCH_MATCH", "MISMATCH"]
selected_ai_verdict = st.sidebar.selectbox("AI Fit Verdict", ai_verdict_options)

eval_source_options = ["ALL", "AI_LIVE", "AI_CACHED", "DETERMINISTIC_FALLBACK"]
selected_eval_source = st.sidebar.selectbox("Evaluation Provenance", eval_source_options)

eligibility_options = ["ALL", "HIGH_FIT", "MEDIUM_FIT", "PROVISIONAL_NEEDS_REVIEW", "LOW_FIT"]
selected_eligibility = st.sidebar.selectbox("Rule Eligibility", eligibility_options)

status_options = ["ALL", "ACTIVE", "EXPIRED", "UNKNOWN"]
selected_status = st.sidebar.selectbox("Listing Status", status_options)

conf_options = ["ALL", "HIGH", "MEDIUM", "LOW"]
selected_conf = st.sidebar.selectbox("Confidence Level", conf_options)

exp_options = ["ALL", "ENTRY_LEVEL", "JUNIOR", "MID_LEVEL", "SENIOR", "UNKNOWN"]
selected_exp = st.sidebar.selectbox("Experience Level", exp_options)

min_score = st.sidebar.slider(
    "Minimum Match Score",
    min_value=0,
    max_value=100,
    value=0,
    step=5,
    help="Filters by AI match score when available, falling back to rule relevance score"
)

# Sorting options
sort_labels = {
    "Highest AI match score": "ai_score",
    "Newest posted first": "newest",
    "Highest rule relevance score": "score",
    "Company name": "company"
}
selected_sort_label = st.sidebar.selectbox("Sort By", list(sort_labels.keys()))
sort_by = sort_labels[selected_sort_label]

sort_order = st.sidebar.radio("Sort Order", ["Descending", "Ascending"], horizontal=True)

# Application Tracking Filter
exclude_applied = st.sidebar.checkbox(
    "Hide jobs I already applied to",
    value=False,
    help="Filters out jobs marked as APPLIED so you can focus on new discovery"
)

# 3. Query Data with Pagination
PAGE_SIZE = 10
if "page" not in st.session_state:
    st.session_state["page"] = 1

# If any filter changes, reset to page 1
filter_signature = f"{search_term}-{selected_posted_within}-{include_unknown_dates}-{selected_platform}-{selected_ai_verdict}-{selected_eval_source}-{selected_eligibility}-{selected_status}-{selected_conf}-{selected_exp}-{min_score}-{sort_by}-{sort_order}-{exclude_applied}"
if st.session_state.get("last_filter_sig") != filter_signature:
    st.session_state["page"] = 1
    st.session_state["last_filter_sig"] = filter_signature

jobs, total_matching = service.search_jobs(
    search_term=search_term,
    platform=selected_platform,
    eligibility=selected_eligibility,
    listing_status=selected_status,
    confidence_level=selected_conf,
    experience_level=selected_exp,
    min_score=min_score,
    posted_within=selected_posted_within,
    include_unknown_dates=include_unknown_dates,
    ai_verdict=selected_ai_verdict,
    evaluation_source=selected_eval_source,
    exclude_applied=exclude_applied,
    sort_by=sort_by,
    sort_order="DESC" if sort_order == "Descending" else "ASC",
    page=st.session_state["page"],
    page_size=PAGE_SIZE
)

total_pages = max(1, (total_matching + PAGE_SIZE - 1) // PAGE_SIZE)

# Display Search Results Header
st.markdown(f"### Discovered Listings ({total_matching} matching)")

if selected_posted_within != "All time":
    if not include_unknown_dates:
        st.info(
            f"**Freshness Filter Active ({selected_posted_within})**: Only listings with verified dates within the window are shown. "
            f"Listings without verified posting dates are hidden. To view them, enable **'Include unknown posting dates'** in sidebar filters."
        )
    else:
        st.warning(
            f"**Freshness Filter Active ({selected_posted_within}) + Unknown Dates Included**: Showing verified posts from {selected_posted_within} "
            f"as well as listings with unparsed or missing posting dates."
        )

# Pagination controls
col_prev, col_page, col_next = st.columns([1, 2, 1])
with col_prev:
    if st.button("Previous", disabled=(st.session_state["page"] <= 1)):
        st.session_state["page"] = max(1, st.session_state["page"] - 1)
        st.rerun()
with col_page:
    st.write(f"Page **{st.session_state['page']}** of **{total_pages}**")
with col_next:
    if st.button("Next", disabled=(st.session_state["page"] >= total_pages)):
        st.session_state["page"] = min(total_pages, st.session_state["page"] + 1)
        st.rerun()

# 4. Job Table & Expandable Details
if not jobs:
    st.info("No job listings found matching the specified filters.")
else:
    for job in jobs:
        score = job.get("relevance_score") or 0
        conf = job.get("confidence_level") or "LOW"
        elig = job.get("eligibility") or "LOW_FIT"
        status = job.get("job_status") or "UNKNOWN"
        app_type = job.get("application_type") or "UNKNOWN"
        exp_lvl = job.get("experience_level") or "UNKNOWN"
        posted_date_raw = job.get("posted_date")
        display_date, relative_freshness = format_freshness(posted_date_raw)

        # AI Provenance & Score
        eval_source = job.get("evaluation_source")
        ai_score = job.get("ai_score")
        ai_verdict = job.get("ai_verdict")
        ai_eval_raw = job.get("ai_evaluation")
        if isinstance(ai_eval_raw, str):
            try:
                ai_eval = json.loads(ai_eval_raw)
            except Exception:
                ai_eval = None
        elif isinstance(ai_eval_raw, dict):
            ai_eval = ai_eval_raw
        else:
            ai_eval = None

        # Determine Card Badges and Score Presentation
        is_fallback = (eval_source == "DETERMINISTIC_FALLBACK")
        is_ai = eval_source in ("AI_LIVE", "AI_CACHED")

        card_col1, card_col2 = st.columns([3, 2])
        with card_col1:
            st.markdown(
                f"#### [{job.get('platform', '').upper()}] {job.get('title')} — **{job.get('company')}**"
            )
            freshness_badge = f"**Posted:** `{display_date}` ({relative_freshness})" if display_date != "N/A" else "**Posted:** `Posting date unknown`"

            if is_fallback:
                score_badge = f"**Rule Score: {score}/100** *(Fallback)*"
                prov_badge = "`DETERMINISTIC_FALLBACK`"
            elif is_ai:
                score_badge = f"**AI Score: {ai_score}/100** (`{ai_verdict}`)"
                source_tag = "Live LLM" if eval_source == "AI_LIVE" else "Cached LLM"
                prov_badge = f"`{eval_source}` ({source_tag})"
            else:
                score_badge = f"**Rule Score: {score}/100**"
                prov_badge = f"**{elig}**"

            user_status = job.get("user_status") or "DISCOVERED"
            status_pill = f" &nbsp;|&nbsp; `[{user_status}]`" if user_status != "DISCOVERED" else ""

            st.markdown(
                f"{freshness_badge} &nbsp;|&nbsp; {score_badge} &nbsp;|&nbsp; {prov_badge}{status_pill} &nbsp;|&nbsp; Location: `{job.get('location') or 'Bengaluru'}`"
            )
        with card_col2:
            listing_url = job.get("url") or "#"
            app_url = job.get("application_url") or listing_url

            btn_col1, btn_col2 = st.columns(2)
            with btn_col1:
                st.link_button("View Listing", listing_url, use_container_width=True)
            with btn_col2:
                if job.get("application_url") and job.get("application_url") != listing_url:
                    st.link_button("Direct Apply", app_url, use_container_width=True)
                else:
                    st.link_button("Apply Link", listing_url, use_container_width=True)

            # Quick CRM tracking actions
            crm_b1, crm_b2 = st.columns(2)
            with crm_b1:
                if user_status == "SHORTLISTED":
                    if st.button("Unstar", key=f"unstar_{job.get('id')}", use_container_width=True):
                        service.update_job_user_status(job['id'], "DISCOVERED")
                        st.toast("Removed from shortlist")
                        st.rerun()
                elif user_status != "APPLIED":
                    if st.button("Shortlist", key=f"star_{job.get('id')}", use_container_width=True):
                        service.update_job_user_status(job['id'], "SHORTLISTED")
                        st.toast("Added to Shortlist")
                        st.rerun()
                else:
                    st.caption("Application active")
            with crm_b2:
                if user_status == "APPLIED":
                    st.button("Applied", disabled=True, key=f"applied_lbl_{job.get('id')}", use_container_width=True)
                else:
                    if st.button("Mark Applied", key=f"apply_btn_{job.get('id')}", use_container_width=True):
                        service.update_job_user_status(job['id'], "APPLIED")
                        st.toast("Marked as Applied")
                        st.rerun()

        with st.expander(f"Details & Application Materials — {job.get('title')} ({job.get('company')})"):
            # 1-Click Tailored Application Material Generator (Phase 3)
            render_tailored_application_drawer(job)
            st.markdown("---")

            # Technical Interview Preparation Pack (Phase 4)
            render_interview_prep_drawer(job)
            st.markdown("---")

            if is_fallback:
                st.warning(
                    "Deterministic Fallback Evaluation: Daily AI quota was exhausted when this listing was evaluated. "
                    "The relevance score shown is rule-based and was NOT produced by AI. "
                    "This listing will be evaluated by Gemini once daily quota resets."
                )

            # Metadata Columns
            c1, c2, c3 = st.columns(3)
            with c1:
                if is_fallback:
                    st.write(f"**Rule-Based Score:** {score}/100")
                    st.write(f"**AI Score:** _None (Quota Exhausted)_")
                elif is_ai:
                    st.write(f"**AI Score:** {ai_score}/100 (`{ai_verdict}`)")
                    st.write(f"**Rule Fallback Score:** {score}/100")
                else:
                    st.write(f"**Relevance Score:** {score}/100")
                st.write(f"**Confidence Level:** {conf}")
                st.write(f"**Provisional:** {'Yes (Needs Review)' if job.get('is_provisional') else 'No'}")
            with c2:
                st.write(f"**Rule Eligibility:** {elig}")
                st.write(f"**Evaluation Provenance:** `{eval_source or 'RULE_BASED'}`")
                st.write(f"**Experience Level:** {exp_lvl}")
                st.write(f"**Location:** {job.get('location') or 'Not specified'}")
            with c3:
                st.write(f"**Listing Status:** `{status}`")
                st.write(f"**Application Type:** `{app_type}`")
                st.write(f"**Posted Date:** {display_date} ({relative_freshness})")
                st.caption(f"Last checked: {job.get('last_checked_at')}")

            # AI Evaluation Detailed Section (V2 Structured)
            if ai_eval and isinstance(ai_eval, dict):
                st.markdown("---")
                st.markdown("#### Structured Semantic Evaluation")

                # Extract schema fields with backward-compatible fallbacks
                v2_verdict = ai_eval.get("fit_verdict") or ai_eval.get("verdict") or ai_verdict or "UNSPECIFIED"
                v2_score = ai_eval.get("match_score") if ai_eval.get("match_score") is not None else (ai_eval.get("overall_fit_score") if ai_eval.get("overall_fit_score") is not None else ai_score)
                v2_conf = ai_eval.get("confidence_score") if ai_eval.get("confidence_score") is not None else ai_eval.get("fit_confidence")
                if isinstance(v2_conf, (int, float)):
                    conf_display = f"{int(v2_conf * 100)}%" if v2_conf <= 1.0 else f"{v2_conf}%"
                elif v2_conf:
                    conf_display = str(v2_conf)
                else:
                    conf_display = "N/A"

                v2_matching_skills = ai_eval.get("matching_skills") or ai_eval.get("matching_required_skills") or []
                v2_missing_skills = ai_eval.get("missing_skills") or ai_eval.get("missing_required_skills") or []
                v2_transferable_skills = ai_eval.get("transferable_skills") or []
                v2_evidence = ai_eval.get("evidence") or ai_eval.get("strengths") or []
                v2_recommendations = ai_eval.get("actionable_recommendations") or ([ai_eval.get("recommendation")] if ai_eval.get("recommendation") else [])
                v2_seniority = ai_eval.get("job_seniority_level")
                v2_exp_fit = ai_eval.get("experience_fit") or ai_eval.get("seniority_fit")
                v2_exp_required = ai_eval.get("job_experience_required")
                v2_exp_assessment = ai_eval.get("experience_assessment")
                v2_work_mode = ai_eval.get("work_mode") or ai_eval.get("work_mode_fit")
                v2_location_fit = ai_eval.get("location_fit")
                v2_responsibilities = ai_eval.get("role_responsibilities") or []
                v2_education = ai_eval.get("education_and_certifications")
                v2_uncertainties = ai_eval.get("uncertainties") or []
                v2_reasoning = ai_eval.get("reasoning")

                if v2_exp_fit == "OVER_QUALIFIED":
                    st.warning(
                        f"Candidate Over-Qualified: Experience may exceed target seniority of this role. "
                        f"{v2_exp_assessment or ''}"
                    )
                elif v2_exp_fit in ("SIGNIFICANT_OVERREACH", "OVER_LIMIT"):
                    st.warning(
                        f"Significant Experience Gap: Job explicitly requires `{v2_exp_required or 'higher experience'}`. "
                        f"{v2_exp_assessment or ''}"
                    )

                st.markdown(
                    f"**Verdict:** `{v2_verdict}` &nbsp;|&nbsp; "
                    f"**AI Match Score:** **{v2_score if v2_score is not None else 'N/A'}/100** &nbsp;|&nbsp; "
                    f"**Confidence:** **{conf_display}**"
                )

                ai_col_main, ai_col_meta = st.columns([3, 2])
                with ai_col_main:
                    if v2_reasoning:
                        st.markdown(f"**Evaluation Reasoning:** {v2_reasoning}")

                    if v2_exp_assessment:
                        st.markdown(f"**Experience Assessment:** {v2_exp_assessment}")

                    if v2_responsibilities and isinstance(v2_responsibilities, list):
                        st.markdown("**Key Role Responsibilities:**")
                        for r in v2_responsibilities:
                            st.write(f"• {r}")

                    if v2_evidence and isinstance(v2_evidence, list):
                        st.markdown("**Matching Evidence Citations:**")
                        for ev in v2_evidence:
                            st.write(f"• {ev}")

                    if v2_recommendations and isinstance(v2_recommendations, list):
                        st.markdown("**Actionable Recommendations:**")
                        for rec in v2_recommendations:
                            st.info(f"• {rec}")

                    if v2_uncertainties and isinstance(v2_uncertainties, list):
                        st.markdown("**Evaluation Uncertainties & Flags:**")
                        for unc in v2_uncertainties:
                            st.caption(f"• {unc}")

                with ai_col_meta:
                    st.markdown("**Role & Experience Metadata:**")
                    if v2_seniority and v2_seniority != "Unspecified":
                        st.write(f"• **Seniority Level:** `{v2_seniority}`")
                    if v2_exp_required and v2_exp_required != "Not specified":
                        st.write(f"• **Experience Required:** `{v2_exp_required}`")
                    if v2_exp_fit:
                        st.write(f"• **Experience Fit:** `{v2_exp_fit}`")
                    if v2_work_mode and v2_work_mode != "Unspecified":
                        st.write(f"• **Work Mode:** `{v2_work_mode}`")
                    if v2_location_fit and v2_location_fit != "Location flexible":
                        st.write(f"• **Location Fit:** `{v2_location_fit}`")
                    if v2_education and v2_education != "None specified":
                        st.write(f"• **Education / Certs:** `{v2_education}`")

                    st.markdown("---")
                    st.markdown("**Matching Skills:**")
                    if v2_matching_skills and isinstance(v2_matching_skills, list):
                        for ms in v2_matching_skills:
                            st.write(f"• {ms}")
                    else:
                        st.caption("_None directly matching_")

                    st.markdown("**Missing Skills:**")
                    if v2_missing_skills and isinstance(v2_missing_skills, list):
                        for mis in v2_missing_skills:
                            st.write(f"• {mis}")
                    else:
                        st.caption("_None identified_")

                    if v2_transferable_skills and isinstance(v2_transferable_skills, list):
                        st.markdown("**Transferable Skills:**")
                        for ts in v2_transferable_skills:
                            st.write(f"• {ts}")

            # Rule-based skill breakdown and reasons
            st.markdown("---")
            col_skills, col_reasons = st.columns(2)
            with col_skills:
                st.markdown("**Deterministic Skills:**")
                skills = job.get("matched_skills") or []
                if isinstance(skills, list) and skills:
                    st.write(", ".join(skills))
                else:
                    st.write("_None observed in search card metadata_")

                unverified = job.get("unverified_components") or []
                if isinstance(unverified, list) and unverified:
                    st.markdown("**Unverified Components:**")
                    st.write(", ".join(unverified))

            with col_reasons:
                st.markdown("**Rule Scoring Rationale:**")
                if job.get("eligibility_reason"):
                    st.info(job.get("eligibility_reason"))
                reasons = job.get("score_reasons") or []
                if isinstance(reasons, list) and reasons:
                    for r in reasons[:3]:
                        st.caption(f"• {r}")

            # Complete Job Description
            desc = job.get("description")
            if desc:
                st.markdown("**Job Description:**")
                with st.expander(f"Inspect Complete Description ({len(desc):,} characters)", expanded=False):
                    st.text(desc)
            else:
                st.caption("Full description was not retrieved from the search card.")

            # CRM Application Lifecycle & Notes Drawer
            st.markdown("---")
            st.markdown("#### Application Lifecycle & Notes")
            crm_c1, crm_c2 = st.columns([2, 3])
            with crm_c1:
                st_opts = ["DISCOVERED", "SHORTLISTED", "APPLIED", "INTERVIEWING", "OFFER", "REJECTED", "WITHDRAWN", "STALE"]
                curr_idx = st_opts.index(user_status) if user_status in st_opts else 0
                new_st = st.selectbox("Current Stage", st_opts, index=curr_idx, key=f"card_stage_{job.get('id')}")
                if new_st != user_status:
                    service.update_job_user_status(job['id'], new_st)
                    st.toast(f"Status updated to {new_st}")
                    st.rerun()
            with crm_c2:
                c_notes = job.get("user_notes") or ""
                n_input = st.text_input("Application Notes / Recruiter Contact", value=c_notes, key=f"quick_note_{job.get('id')}", placeholder="e.g. InMail sent to Sneha Sharma")
                if st.button("Save Note", key=f"save_qnote_{job.get('id')}"):
                    service.update_job_user_status(job['id'], user_status, user_notes=n_input)
                    st.toast("Note saved to database")
                    st.rerun()

        st.divider()
