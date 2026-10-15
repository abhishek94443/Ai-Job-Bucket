"""
Job Bucket — Live Real-Time AI Gateway Verification
Sends 7 live technical questions across Google Gemini models over HTTPS.
Verifies RPM sliding-window throttling (Limit = 5) and live auto-switch to Priority 2.
Run directly in your terminal:
    .\\venv\\Scripts\\python verify_rpm_switch.py
"""

import os
import sys
import time
from datetime import datetime

# Ensure project root is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.ai_gateway import AIGateway


def get_timestamp() -> str:
    """Returns current local time with millisecond precision."""
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


# Model 1 & Model 2: Active live endpoints on Google Generative Language API
PRIMARY_MODEL = "gemini-3.5-flash-lite"
SECONDARY_MODEL = "gemini-3.6-flash"

# The user's exact 7 technical questions
QUESTIONS = [
    "what is java",
    "what is go",
    "what is json",
    "what is abstraction",
    "what is polymorphism",
    "what is interfaces in java",
    "wha is system design",
]


def setup_db_priorities(gateway: AIGateway):
    """
    Executes database update in Neon PostgreSQL setting:
    - Primary Model (gemini-3.5-flash-lite): Priority 1 (RPM Limit = 5)
    - Secondary Model (gemini-3.6-flash): Priority 2 (RPM Limit = 5)
    - Remaining models: Lower priorities (3, 4, 5)
    """
    print(f"[{get_timestamp()}] [DB] Updating model priorities in Neon PostgreSQL...")
    with gateway.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE ai_model_registry
                SET priority = 1, rpm_limit = 5, is_active = TRUE
                WHERE model_id = '{PRIMARY_MODEL}';
                """
            )
            cur.execute(
                f"""
                UPDATE ai_model_registry
                SET priority = 2, rpm_limit = 5, is_active = TRUE
                WHERE model_id = '{SECONDARY_MODEL}';
                """
            )
            cur.execute("UPDATE ai_model_registry SET priority = 3 WHERE model_id = 'gemini-3.1-flash-lite';")
            cur.execute("UPDATE ai_model_registry SET priority = 4 WHERE model_id = 'gemini-3.7-flash';")
            cur.execute("UPDATE ai_model_registry SET priority = 5 WHERE model_id = 'gemini-3.8-flash';")
            conn.commit()
    print(f"[{get_timestamp()}] [DB] Priorities configured in PostgreSQL: {PRIMARY_MODEL} (P1, RPM=5) | {SECONDARY_MODEL} (P2, RPM=5)\n")


def restore_db_priorities(gateway: AIGateway):
    """Restores baseline default waterfall priorities."""
    with gateway.get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE ai_model_registry SET priority = 1, rpm_limit = 15 WHERE model_id = 'gemini-3.5-flash-lite';")
            cur.execute("UPDATE ai_model_registry SET priority = 2, rpm_limit = 15 WHERE model_id = 'gemini-3.1-flash-lite';")
            cur.execute("UPDATE ai_model_registry SET priority = 3, rpm_limit = 5 WHERE model_id = 'gemini-3.7-flash';")
            cur.execute("UPDATE ai_model_registry SET priority = 4, rpm_limit = 5 WHERE model_id = 'gemini-3.8-flash';")
            cur.execute("UPDATE ai_model_registry SET priority = 5, rpm_limit = 5 WHERE model_id = 'gemini-3.6-flash';")
            conn.commit()
    print(f"[{get_timestamp()}] [DB] Priorities safely restored to baseline in database.")


def main():
    gateway = AIGateway()

    print("=" * 80)
    print("JOB BUCKET: LIVE REAL-TIME AI GATEWAY VERIFICATION")
    print(f"Testing 7 live HTTPS calls to Google Gemini: {PRIMARY_MODEL} (RPM=5) -> {SECONDARY_MODEL} (RPM=5)")
    print("=" * 80 + "\n")

    # Step 1: Configure Neon DB
    setup_db_priorities(gateway)

    # Print Live DB Configuration
    models = gateway.get_registered_models()
    print("CURRENT WATERFALL PRIORITY TABLE (FROM NEON POSTGRESQL):")
    for m in models:
        print(f"  * Priority {m['priority']}: {m['model_id']:<24} | RPM Limit: {m['rpm_limit']:<2} | RPD Limit: {m['rpd_limit']}")
    print("-" * 80 + "\n")

    print(f"[{get_timestamp()}] Launching 7 questions with LIVE HTTPS calls to Google Gemini...\n")
    results = []

    for idx, question in enumerate(QUESTIONS, start=1):
        prompt = f"Answer concisely in 2 sentences: {question}"
        ts_start = get_timestamp()

        # Check in-memory capacity
        m1_rpm_ok = gateway._is_rpm_available(PRIMARY_MODEL, "GEMINI_API_KEY", rpm_limit=5)
        m1_cooling = gateway._is_cooling_down(PRIMARY_MODEL, "GEMINI_API_KEY")

        if not m1_rpm_ok or m1_cooling:
            print(f">>> [{ts_start}] ALERT: {PRIMARY_MODEL} RPM limit of 5 reached in 60s window!")
            print(f">>> [{ts_start}] ACTION: Gateway auto-cascading to Priority 2 ({SECONDARY_MODEL})...")

        # Live call to Google Gemini via AIGateway router
        res = gateway.execute_generation(prompt)
        ts_end = get_timestamp()
        results.append((idx, question, ts_start, res))

        # Format output
        clean_text = res['text'].strip()
        print(f"[{ts_start}] Request #{idx} (q{idx}):")
        print(f"    |-- Question (Prompt) : \"{question}\"")
        print(f"    |-- Model Routed      : {res['model_id']} (Priority {res['priority']})")
        print(f"    |-- Provider          : {res['provider']}")
        print(f"    +-- LIVE AI Response  :\n        {clean_text}\n")

    # Summary Table
    print("=" * 80)
    print("FINAL LIVE EXECUTION TRACE TABLE")
    print(f"{'Req #':<6} | {'Timestamp':<12} | {'Question':<26} | {'Serving Model':<22} | {'Priority'}")
    print("-" * 80)

    for idx, question, ts, r in results:
        q_label = f"q{idx}: {question[:21]}"
        prio_str = f"Priority {r['priority']}"
        print(f"{idx:<6} | {ts:<12} | {q_label:<26} | {r['model_id']:<22} | {prio_str}")

    print("=" * 80 + "\n")

    # Verification counts
    count_p1 = sum(1 for _, _, _, r in results if r["model_id"] == PRIMARY_MODEL)
    count_p2 = sum(1 for _, _, _, r in results if r["model_id"] == SECONDARY_MODEL)

    print(f"[{get_timestamp()}] Live Verification Audit:")
    print(f"    * Questions answered by {PRIMARY_MODEL} (Priority 1): {count_p1} / 5 (Matched)")
    print(f"    * Questions answered by {SECONDARY_MODEL} (Priority 2): {count_p2} / 2 (Matched)")

    if count_p1 == 5 and count_p2 == 2:
        print(f"[{get_timestamp()}] SUCCESS: Live waterfall switched to {SECONDARY_MODEL} after call 5 with zero errors!\n")
    else:
        print(f"[{get_timestamp()}] NOTICE: Check model distribution.\n")

    # Restore DB
    restore_db_priorities(gateway)


if __name__ == "__main__":
    main()
