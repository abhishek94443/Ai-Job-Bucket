"""
Job Bucket — UI Design System & Minimalist Theme
Provides clean, professional CSS styling inspired by modern engineering tools (Linear, Raycast, GitHub).
Eliminates amateur emoji spam in favor of crisp typography, subtle borders, and semantic tags.
"""

import streamlit as st


def inject_clean_theme():
    """
    Injects a minimalist, executive CSS theme into the Streamlit app.
    Enforces clean typography, high information density, and subtle borders.
    """
    custom_css = """
    <style>
    /* Global Clean Typography */
    html, body, [class*="css"] {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
        letter-spacing: -0.01em;
    }

    /* Streamlit Main Container Spacing */
    .block-container {
        padding-top: 2rem !important;
        padding-bottom: 3rem !important;
        max-width: 1400px;
    }

    /* Streamlit Headers */
    h1, h2, h3, h4, h5, h6 {
        font-weight: 600 !important;
        letter-spacing: -0.02em !important;
        color: #f4f4f5 !important;
    }
    
    /* Subdued Captions and Secondary Text */
    .stCaption, p.caption {
        color: #a1a1aa !important;
        font-size: 0.875rem !important;
    }

    /* Metrics Styling */
    div[data-testid="stMetric"] {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        padding: 12px 16px;
        box-shadow: 0 1px 2px rgba(0, 0, 0, 0.1);
    }
    div[data-testid="stMetricLabel"] {
        font-size: 0.8rem !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #a1a1aa !important;
        font-weight: 500;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.5rem !important;
        font-weight: 600 !important;
        color: #fafafa !important;
    }

    /* Minimal Button Styling */
    .stButton > button {
        border-radius: 6px !important;
        font-weight: 500 !important;
        font-size: 0.85rem !important;
        padding: 0.4rem 0.85rem !important;
        border: 1px solid rgba(255, 255, 255, 0.12) !important;
        transition: all 0.15s ease-in-out !important;
    }
    .stButton > button:hover {
        border-color: rgba(255, 255, 255, 0.25) !important;
        background-color: rgba(255, 255, 255, 0.05) !important;
    }

    /* Primary Action Buttons */
    .stButton > button[kind="primary"] {
        background-color: #2563eb !important;
        border-color: #3b82f6 !important;
        color: #ffffff !important;
    }
    .stButton > button[kind="primary"]:hover {
        background-color: #1d4ed8 !important;
        border-color: #60a5fa !important;
    }

    /* Clean Card Container */
    .job-card {
        background: rgba(255, 255, 255, 0.02);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 12px;
    }

    /* Semantic Status Badges */
    .status-badge {
        display: inline-block;
        padding: 2px 8px;
        font-size: 0.75rem;
        font-weight: 600;
        border-radius: 4px;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        margin-right: 6px;
    }
    .badge-discovered {
        background: rgba(148, 163, 184, 0.15);
        color: #cbd5e1;
        border: 1px solid rgba(148, 163, 184, 0.3);
    }
    .badge-shortlisted {
        background: rgba(245, 158, 11, 0.15);
        color: #fbbf24;
        border: 1px solid rgba(245, 158, 11, 0.35);
    }
    .badge-applied {
        background: rgba(59, 130, 246, 0.15);
        color: #60a5fa;
        border: 1px solid rgba(59, 130, 246, 0.35);
    }
    .badge-interviewing {
        background: rgba(168, 85, 247, 0.15);
        color: #c084fc;
        border: 1px solid rgba(168, 85, 247, 0.35);
    }
    .badge-offer {
        background: rgba(34, 197, 94, 0.15);
        color: #4ade80;
        border: 1px solid rgba(34, 197, 94, 0.35);
    }
    .badge-rejected {
        background: rgba(239, 68, 68, 0.15);
        color: #f87171;
        border: 1px solid rgba(239, 68, 68, 0.35);
    }
    .badge-stale {
        background: rgba(100, 116, 139, 0.15);
        color: #94a3b8;
        border: 1px solid rgba(100, 116, 139, 0.25);
    }
    .badge-score {
        background: rgba(20, 184, 166, 0.12);
        color: #2dd4bf;
        border: 1px solid rgba(20, 184, 166, 0.3);
        font-family: monospace;
    }

    /* Expanders */
    div[data-testid="stExpander"] {
        border: 1px solid rgba(255, 255, 255, 0.08) !important;
        border-radius: 6px !important;
        background: transparent !important;
    }
    </style>
    """
    st.markdown(custom_css, unsafe_allow_html=True)


def format_status_badge(status: str) -> str:
    """Returns a clean HTML badge string for an application status."""
    st_upper = status.upper()
    css_class = f"badge-{st_upper.lower()}"
    return f'<span class="status-badge {css_class}">[{st_upper}]</span>'


def format_score_badge(score: int, label: str = "SCORE") -> str:
    """Returns a clean HTML badge string for a match score."""
    return f'<span class="status-badge badge-score">[{label}: {score}/100]</span>'
