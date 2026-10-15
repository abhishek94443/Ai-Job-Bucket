import sys
from src.db_persistence import DatabasePersistence
from src.db_config import DatabaseConfig

def main():
    print("=== PostgreSQL Health Check ===")
    info = DatabaseConfig.get_masked_connection_info()
    print(f"Target connection parameters: {info}")

    db = DatabasePersistence()
    healthy, msg = db.check_health()
    if healthy:
        print(f"[STATUS: OK] {msg}")
        sys.exit(0)
    else:
        print(f"[STATUS: FAILED] {msg}")
        print("\nTroubleshooting tips:")
        print("1. If using Neon / Cloud: verify DATABASE_URL is set with valid host, credentials and ?sslmode=require.")
        print("2. If using local PostgreSQL: ensure service is running and DB_USER/DB_PASSWORD/DB_NAME are valid.")
        sys.exit(1)

if __name__ == "__main__":
    main()
