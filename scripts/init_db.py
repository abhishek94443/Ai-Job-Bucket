"""
Job Bucket - Database Initializer
Initializes a clean PostgreSQL database schema from schema.sql using DATABASE_URL or DB_* settings in .env.
Safe to run multiple times (idempotent DDL).
"""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.db_persistence import DatabasePersistence


def main():
    print("=" * 60)
    print("Job Bucket: Database Initialization")
    print("=" * 60)

    db = DatabasePersistence()
    healthy, msg = db.check_health()

    if not healthy:
        print(f"Error: Unable to connect to database: {msg}")
        print("Please verify your DATABASE_URL in .env.")
        sys.exit(1)

    print(f"Connected to database: {msg}")
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
