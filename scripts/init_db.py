"""
Job Bucket - Database Initializer
Initializes a clean PostgreSQL database schema from schema.sql using DATABASE_URL or DB_* settings in .env.
Safe to run multiple times (idempotent DDL).
"""

import sys
import time
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db_persistence import DatabasePersistence


def main():
    print("=" * 60)
    print("Job Bucket: Database Initialization & Health Verification")
    print("=" * 60)

    db = DatabasePersistence()
    max_attempts = 10
    delay_seconds = 5
    connected = False
    last_error = ""

    print(f"Connecting to database (polling up to {max_attempts} attempts, {delay_seconds}s intervals for cold starts)...")
    for attempt in range(1, max_attempts + 1):
        healthy, msg = db.check_health()
        if healthy:
            print(f"Connected to database on attempt {attempt}/{max_attempts}: {msg}")
            connected = True
            break
        last_error = msg
        print(f"Attempt {attempt}/{max_attempts} failed: {msg}. Retrying in {delay_seconds}s...")
        if attempt < max_attempts:
            time.sleep(delay_seconds)

    if not connected:
        print(f"Error: Unable to connect to database after {max_attempts * delay_seconds}s: {last_error}")
        print("Please verify your DATABASE_URL in .env.")
        sys.exit(1)
    print("Executing schema.sql...")

    schema_file = PROJECT_ROOT / "schema.sql"
    if not schema_file.is_file():
        print(f"Error: Schema file not found at {schema_file}")
        sys.exit(1)

    try:
        db.initialize_schema(str(schema_file))
        print("Success: All tables, types, indexes, and initial AI models initialized successfully.")
    except Exception as e:
        print(f"Error during schema initialization: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
