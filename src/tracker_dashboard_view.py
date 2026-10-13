"""
Job Bucket — Application Lifecycle Tracker & CRM View
Provides an interactive interface to manage:
SHORTLISTED -> APPLIED -> INTERVIEWING -> OFFER / REJECTED / WITHDRAWN / STALE
Includes quiet application detection (>= 10 days silent) and automated follow-up drafting.
Refactored for clean, minimal, executive UI without emoji clutter.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import streamlit as st

from src.dashboard_service import DashboardQueryService, format_freshness
from src.tailoring_view import render_tailored_application_drawer
from src.interview_prep_view import render_interview_prep_drawer
from src.ui_theme import inject_clean_theme, format_status_badge, format_score_badge


def render_followup_drawer(job: Dict[str, Any], days_silent: int):
    """Generates a polite, context-aware follow-up email template."""
    company = job.get("company", "the team")
    title = job.get("title", "the role")
    contact = job.get("contact_person") or "Hiring Team"

    email_subject = f"Following up on application: {title} - Abhishek Dwivedi"
    email_body = f"""Dear {contact},

I hope this message finds you well.

I am writing to follow up on my application for the {title} position at {company}, submitted approximately {days_silent} days ago.

I remain very interested in the opportunity to contribute to {company}, particularly with my background in Java, Spring Boot microservices, and distributed backend architectures.

Please let me know if there are any additional materials, references, or details I can provide to support my application. I look forward to hearing about the next steps.

Best regards,
Abhishek Dwivedi
"""
    st.markdown("##### Contextual Follow-Up Draft")
    st.text_input("Subject:", value=email_subject, key=f"fu_subj_{job['id']}")
    st.text_area("Body:", value=email_body, height=180, key=f"fu_body_{job['id']}")
    st.caption("Tip: Copy and send via email or LinkedIn InMail to keep momentum going.")


def render_application_card(service: DashboardQueryService, job: Dict[str, Any], show_followup: bool = False):
    """Renders an interactive CRM card for a tracked job application."""
    job_id = job["id"]
    status = job.get("user_status", "DISCOVERED")
    title = job.get("title", "Unknown Role")
    company = job.get("company", "Unknown Company")
    platform = (job.get("platform") or "unknown").upper()
    location = job.get("location") or "Bengaluru"
    ai_score = job.get("ai_score")
    score = ai_score if ai_score is not None else job.get("relevance_score", 0)

    applied_at = job.get("applied_at")
    applied_display = applied_at.strftime("%Y-%m-%d") if applied_at else "Not recorded"

    # Calculate days silent if applied
    days_silent = 0
    if applied_at:
        days_silent = max(0, (datetime.now().date() - applied_at.date()).days)

    with st.container():
        c_title, c_links = st.columns([3, 2])
        with c_title:
            st.markdown(f"#### [{platform}] {title} — **{company}**")
            badge_score = f"**Match:** `{score}/100`"
            badge_plat = f"`{platform}`"
            badge_loc = f"Location: `{location}`"
            badge_status = f"`[{status}]`"
            st.markdown(f"{badge_plat} &nbsp;|&nbsp; {badge_status} &nbsp;|&nbsp; {badge_score} &nbsp;|&nbsp; {badge_loc}")

        with c_links:
            listing_url = job.get("url") or "#"
            app_url = job.get("application_url") or listing_url
            b1, b2 = st.columns(2)
            with b1:
                st.link_button("View Listing", listing_url, use_container_width=True)
            with b2:
                st.link_button("Direct Apply", app_url, use_container_width=True)

        # Stage updater & metadata columns
        m1, m2, m3 = st.columns([2, 2, 3])

        with m1:
            stage_options = ["SHORTLISTED", "APPLIED", "INTERVIEWING", "OFFER", "REJECTED", "WITHDRAWN", "STALE"]
            current_idx = stage_options.index(status) if status in stage_options else 0
            new_stage = st.selectbox(
                "Application Stage",
                options=stage_options,
                index=current_idx,
                key=f"stage_select_{job_id}",
                help="Update your stage in the hiring pipeline"
            )
            if new_stage != status:
                service.update_job_user_status(job_id=job_id, user_status=new_stage)
                st.toast(f"Moved {company} to {new_stage}.")
                st.rerun()

        with m2:
            st.write(f"**Applied Date:** `{applied_display}`")
            if status == "APPLIED" and days_silent >= 10:
                st.markdown(f"**Silent:** `{days_silent} days` *(Needs follow-up)*")
            elif status == "APPLIED":
                st.markdown(f"**Applied:** `{days_silent} days ago`")

        with m3:
            current_contact = job.get("contact_person") or ""
            new_contact = st.text_input(
                "Contact Person / Recruiter",
                value=current_contact,
                key=f"contact_{job_id}",
                placeholder="e.g. Sneha Sharma (HR Lead)"
            )
            if new_contact != current_contact:
                service.update_job_user_status(job_id=job_id, user_status=status, contact_person=new_contact)
                st.toast("Contact updated.")

        # Notes drawer
        current_notes = job.get("user_notes") or ""
        with st.expander(f"Outreach Notes & Activity Log ({len(current_notes)} chars)", expanded=bool(current_notes)):
            notes_input = st.text_area(
                "Application Notes",
                value=current_notes,
                height=90,
                key=f"notes_area_{job_id}",
                placeholder="Log interviews, salary discussed, recruiter interactions, or referral details..."
            )
            if st.button("Save Notes", key=f"save_notes_btn_{job_id}"):
                service.update_job_user_status(job_id=job_id, user_status=status, user_notes=notes_input)
                st.success("Notes saved to database.")
                st.rerun()

        # Follow-up drawer if flagged
        if status == "APPLIED" and (days_silent >= 10 or show_followup):
            with st.expander(f"Follow-Up Drafter ({days_silent} days since applied)", expanded=False):
                render_followup_drawer(job, days_silent)

        # Tailored Application Pack Drawer (Phase 3)
        with st.expander(f"Tailored Application Materials — {company}", expanded=False):
            render_tailored_application_drawer(job)

        # Technical Interview Preparation Pack (Phase 4)
        with st.expander(f"Technical Interview Preparation Pack — {company}", expanded=False):
            render_interview_prep_drawer(job)

        st.markdown("---")


def render_tracker_page():
    """Main rendering entrypoint for the Tracked Applications CRM tab."""
    inject_clean_theme()
    service = DashboardQueryService()

    st.title("Application Lifecycle Tracker")
    st.caption("Manage active pipeline: Shortlist, track applied roles, record interviews, and follow up.")

    # 1. Pipeline Metrics Banner
    metrics = service.get_crm_metrics()

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("Shortlisted", metrics.get("SHORTLISTED", 0))
    with c2:
        st.metric("Applied", metrics.get("APPLIED", 0))
    with c3:
        st.metric("Interviewing", metrics.get("INTERVIEWING", 0))
    with c4:
        st.metric("Offers", metrics.get("OFFER", 0))
    with c5:
        closed_count = metrics.get("REJECTED", 0) + metrics.get("WITHDRAWN", 0) + metrics.get("STALE", 0)
        st.metric("Closed / Other", closed_count)

    # 2. Quiet Applications Alert Banner
    quiet_jobs = service.get_quiet_applications(days_threshold=10)
    if quiet_jobs:
        st.warning(
            f"Attention Needed: You have {len(quiet_jobs)} applied role(s) quiet for 10 or more days. "
            "Sending a courteous follow-up inquiry increases interview conversion significantly."
        )

    # 3. Tabbed Filtering
    tabs = st.tabs([
        "All Active",
        f"Shortlisted ({metrics.get('SHORTLISTED', 0)})",
        f"Applied ({metrics.get('APPLIED', 0)})",
        f"Interviewing ({metrics.get('INTERVIEWING', 0)})",
        f"Offers ({metrics.get('OFFER', 0)})",
        f"Closed ({closed_count})",
    ])

    with tabs[0]:  # All Active
        active_jobs = service.get_tracked_applications(
            status_filter=["SHORTLISTED", "APPLIED", "INTERVIEWING", "OFFER"],
            limit=100
        )
        if not active_jobs:
            st.info("No active applications being tracked yet. Star or apply to jobs from the Job Discovery page.")
        else:
            for job in active_jobs:
                render_application_card(service, job)

    with tabs[1]:  # Shortlisted
        shortlisted = service.get_tracked_applications(status_filter=["SHORTLISTED"], limit=100)
        if not shortlisted:
            st.info("No shortlisted jobs. Click Shortlist on high-fit jobs to save them here.")
        else:
            for job in shortlisted:
                render_application_card(service, job)

    with tabs[2]:  # Applied
        applied = service.get_tracked_applications(status_filter=["APPLIED"], limit=100)
        if not applied:
            st.info("No jobs marked as applied yet.")
        else:
            for job in applied:
                render_application_card(service, job)

    with tabs[3]:  # Interviewing
        interviewing = service.get_tracked_applications(status_filter=["INTERVIEWING"], limit=100)
        if not interviewing:
            st.info("No roles currently in interviewing stage.")
        else:
            for job in interviewing:
                render_application_card(service, job)

    with tabs[4]:  # Offers
        offers = service.get_tracked_applications(status_filter=["OFFER"], limit=100)
        if not offers:
            st.info("No offers recorded yet.")
        else:
            for job in offers:
                render_application_card(service, job)

    with tabs[5]:  # Closed
        closed = service.get_tracked_applications(status_filter=["REJECTED", "WITHDRAWN", "STALE"], limit=100)
        if not closed:
            st.info("No closed applications.")
        else:
            for job in closed:
                render_application_card(service, job)
