"""
Job Bucket — AI Model & Quota Management Dashboard View
Allows the operator to:
1. Monitor live AI model statuses, priorities, RPM/RPD limits, and remaining quota.
2. Manually adjust today's used RPD (if key was used outside job-bucket).
3. Reorder priorities and toggle models active/inactive.
4. Register new models from any provider (Google, Anthropic, OpenAI, etc.).
5. Safely manage API keys in .env with atomic file updates.
"""

from typing import Any, Dict, List
import streamlit as st

from src.ai_gateway import AIGateway
from src.env_manager import EnvKeyManager, SUPPORTED_KEY_ALIASES
from src.ui_theme import inject_clean_theme


def render_model_management_page():
    """
    Renders the AI Model & Quota Management page in Streamlit.
    """
    inject_clean_theme()
    gateway = AIGateway()
    env_mgr = EnvKeyManager()

    st.title("AI Models & Quota Management")
    st.caption("Monitor multi-model waterfall, adjust daily quotas, and configure provider API keys.")

    models = gateway.get_registered_models()
    keys_status = env_mgr.get_keys_status()

    # -------------------------------------------------------------------------
    # 1. Quota & Model Status Overview
    # -------------------------------------------------------------------------
    st.markdown("### Model Waterfall & Quota Status")

    if not models:
        st.warning("No models registered in database. Use the 'Add New Model' section below to configure models.")
    else:
        # Metrics summary
        total_models = len(models)
        active_models = sum(1 for m in models if m.get("is_active"))
        total_capacity_today = sum(m.get("rpd_limit", 0) for m in models if m.get("is_active"))
        total_calls_today = sum(m.get("calls_today", 0) for m in models if m.get("is_active"))
        remaining_today = max(0, total_capacity_today - total_calls_today)

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            st.metric("Total Free Capacity", f"{total_capacity_today} calls/day")
        with m2:
            st.metric("Used Today", f"{total_calls_today} calls")
        with m3:
            st.metric("Remaining Today", f"{remaining_today} calls")
        with m4:
            st.metric("Active Models", f"{active_models} / {total_models}")

        # Render Model Cards
        for m in models:
            m_id = m["model_id"]
            provider = m["provider"].upper()
            key_alias = m["api_key_alias"]
            priority = m["priority"]
            is_active = m["is_active"]
            rpm = m["rpm_limit"]
            rpd = m["rpd_limit"]
            used = m["calls_today"]
            remaining = max(0, rpd - used)
            pct = min(100.0, (used / rpd * 100.0) if rpd > 0 else 100.0)

            # Determine Status Badge
            if not is_active:
                status_html = '<span class="status-badge badge-stale">[DISABLED]</span>'
            elif used >= rpd:
                status_html = '<span class="status-badge badge-rejected">[RPD EXHAUSTED]</span>'
            elif gateway._is_cooling_down(m_id, key_alias):
                status_html = '<span class="status-badge badge-shortlisted">[COOLING DOWN (60s)]</span>'
            else:
                status_html = '<span class="status-badge badge-offer">[READY / ACTIVE]</span>'

            # Key config check
            key_configured = keys_status.get(key_alias, {}).get("is_configured", False)
            key_html = '<span class="status-badge badge-score">[KEY ACTIVE]</span>' if key_configured else '<span class="status-badge badge-rejected">[KEY MISSING]</span>'

            with st.container():
                c_head, c_stats = st.columns([3, 2])
                with c_head:
                    st.markdown(f"#### Priority {priority}: `{m_id}` ({provider})")
                    st.markdown(f"{status_html} {key_html} &nbsp;|&nbsp; Key Alias: `{key_alias}` &nbsp;|&nbsp; Limits: `{rpm} RPM` / `{rpd} RPD`", unsafe_allow_html=True)
                with c_stats:
                    st.write(f"**Used Today:** `{used} / {rpd}` ({remaining} remaining)")
                    st.progress(pct / 100.0)

                st.markdown("---")

    # -------------------------------------------------------------------------
    # 2. Manual Used RPD Adjustment (User Feature Request)
    # -------------------------------------------------------------------------
    st.markdown("### Synchronize External Quota (Manual RPD Override)")
    st.caption("If you used an API key outside of Job Bucket today, enter how many requests were consumed so the engine never hits unexpected 429 errors:")

    col_sync1, col_sync2, col_sync3 = st.columns([2, 1.5, 1])
    with col_sync1:
        model_options = [f"{m['model_id']} ({m['api_key_alias']})" for m in models]
        selected_sync = st.selectbox("Select Model to Synchronize", model_options, key="sync_model_select")
    with col_sync2:
        sync_target = next((m for m in models if f"{m['model_id']} ({m['api_key_alias']})" == selected_sync), None)
        curr_val = sync_target["calls_today"] if sync_target else 0
        new_used_count = st.number_input("Calls Used Today", min_value=0, max_value=5000, value=curr_val, step=1, key="sync_used_input")
    with col_sync3:
        st.write("")
        st.write("")
        if st.button("Update Quota", key="btn_update_rpd_sync", use_container_width=True):
            if sync_target:
                gateway.update_model_used_calls(sync_target["model_id"], sync_target["api_key_alias"], int(new_used_count))
                st.toast(f"Updated {sync_target['model_id']} calls today to {new_used_count}!")
                st.rerun()

    st.divider()

    # -------------------------------------------------------------------------
    # 3. Model Configuration & Priority Reordering
    # -------------------------------------------------------------------------
    st.markdown("### Edit Model Parameters & Priorities")
    if models:
        col_ed1, col_ed2, col_ed3, col_ed4, col_ed5 = st.columns([2, 1, 1, 1, 1])
        with col_ed1:
            edit_choice = st.selectbox("Model to Edit", [m["model_id"] for m in models], key="edit_model_select")
            target_edit = next((m for m in models if m["model_id"] == edit_choice), None)
        with col_ed2:
            edit_prio = st.number_input("Priority", min_value=1, max_value=20, value=target_edit["priority"] if target_edit else 1, key="edit_prio_input")
        with col_ed3:
            edit_rpm = st.number_input("RPM Limit", min_value=1, max_value=100, value=target_edit["rpm_limit"] if target_edit else 15, key="edit_rpm_input")
        with col_ed4:
            edit_rpd = st.number_input("RPD Limit", min_value=1, max_value=5000, value=target_edit["rpd_limit"] if target_edit else 500, key="edit_rpd_input")
        with col_ed5:
            edit_active = st.checkbox("Active", value=target_edit["is_active"] if target_edit else True, key="edit_active_checkbox")

        if st.button("Save Model Configuration", key="btn_save_model_edit"):
            if target_edit:
                gateway.update_model(
                    model_id=target_edit["model_id"],
                    api_key_alias=target_edit["api_key_alias"],
                    rpm_limit=int(edit_rpm),
                    rpd_limit=int(edit_rpd),
                    priority=int(edit_prio),
                    is_active=bool(edit_active),
                )
                st.toast(f"Configuration saved for {target_edit['model_id']}!")
                st.rerun()

    st.divider()

    # -------------------------------------------------------------------------
    # 4. Register New Model
    # -------------------------------------------------------------------------
    st.markdown("### Register New Model in Waterfall")
    st.caption("Add custom models from Google, Anthropic, OpenAI, or local Ollama:")

    c_add1, c_add2, c_add3 = st.columns([1.5, 2, 1.5])
    with c_add1:
        new_provider = st.selectbox("Provider", ["google", "anthropic", "openai", "groq", "ollama"], key="new_m_provider")
        new_key_alias = st.selectbox("API Key Alias", SUPPORTED_KEY_ALIASES, key="new_m_key_alias")
    with c_add2:
        new_m_id = st.text_input("Model ID", placeholder="e.g. gemini-2.5-flash or gpt-4o-mini", key="new_m_id")
        new_m_prio = st.number_input("Priority Order (1 = Top)", min_value=1, max_value=50, value=len(models) + 1, key="new_m_prio")
    with c_add3:
        new_rpm = st.number_input("RPM Limit", min_value=1, max_value=500, value=15, key="new_m_rpm")
        new_rpd = st.number_input("RPD Limit", min_value=1, max_value=10000, value=500, key="new_m_rpd")

    if st.button("Register Model", key="btn_register_new_model"):
        if not new_m_id:
            st.error("Model ID cannot be empty.")
        else:
            gateway.add_model(
                provider=new_provider,
                model_id=new_m_id,
                api_key_alias=new_key_alias,
                rpm_limit=int(new_rpm),
                tpm_limit=250000,
                rpd_limit=int(new_rpd),
                priority=int(new_m_prio),
            )
            st.toast(f"Successfully registered {new_m_id}!")
            st.rerun()

    st.divider()

    # -------------------------------------------------------------------------
    # 5. Safe .env API Key Management
    # -------------------------------------------------------------------------
    st.markdown("### API Key (.env) Credentials Manager")
    st.caption("View and atomically update provider API keys in your local .env without corrupting other settings:")

    key_cols = st.columns(2)
    with key_cols[0]:
        st.markdown("**Configured API Keys:**")
        for alias, info in keys_status.items():
            badge = '<span class="status-badge badge-offer">[CONFIGURED]</span>' if info["is_configured"] else '<span class="status-badge badge-stale">[NOT SET]</span>'
            st.markdown(f"• **`{alias}`**: {badge} `{info['masked']}`", unsafe_allow_html=True)

    with key_cols[1]:
        st.markdown("**Add or Update Key:**")
        selected_key_alias = st.selectbox("Select Key Alias to Update", SUPPORTED_KEY_ALIASES, key="key_update_select")
        new_key_secret = st.text_input("API Key Secret", type="password", placeholder="Paste API key here...", key="key_secret_input")
        if st.button("Save Key to .env", key="btn_save_key_secret"):
            if not new_key_secret:
                st.error("Key value cannot be empty.")
            else:
                try:
                    env_mgr.save_key(selected_key_alias, new_key_secret)
                    st.toast(f"Saved {selected_key_alias} to .env and activated in memory!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Failed to save key: {exc}")
