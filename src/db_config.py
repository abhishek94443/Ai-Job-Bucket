import os
import sys
import urllib.parse
from typing import Optional, Dict, Any, Union
import psycopg2
from dotenv import load_dotenv

# Load .env if present
load_dotenv()


def get_streamlit_secret(key: str) -> Optional[str]:
    """
    Safely retrieves a secret from Streamlit's secrets if Streamlit is imported
    and secrets are available, without importing Streamlit in non-Streamlit environments.
    """
    if "streamlit" not in sys.modules:
        return None
    try:
        st = sys.modules["streamlit"]
        if hasattr(st, "secrets") and key in st.secrets:
            val = st.secrets[key]
            return str(val) if val is not None else None
    except Exception:
        return None
    return None


def get_config_value(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Fetches a configuration value respecting strict precedence:
    1. Environment variable (os.environ / .env)
    2. Streamlit secrets (if Streamlit is active)
    3. default value
    """
    env_val = os.getenv(key)
    if env_val is not None and env_val.strip() != "":
        return env_val.strip()

    st_val = get_streamlit_secret(key)
    if st_val is not None and st_val.strip() != "":
        return st_val.strip()

    return default


class DatabaseConfig:
    """
    Database configuration supporting both Neon/Cloud PostgreSQL (via DATABASE_URL)
    and legacy local PostgreSQL split variables (DB_HOST, DB_PORT, etc.).
    Prioritizes DATABASE_URL when present.
    """

    @classmethod
    def get_database_url(cls) -> Optional[str]:
        return get_config_value("DATABASE_URL")

    @classmethod
    def get_connection_string(cls) -> Optional[str]:
        """
        Returns connection DSN string if DATABASE_URL is present,
        or constructs a connection URI string from split configuration.
        """
        db_url = cls.get_database_url()
        if db_url:
            return db_url

        user = cls.get_user()
        password = cls.get_password()
        host = cls.get_host()
        port = cls.get_port()
        dbname = cls.get_name()
        sslmode = get_config_value("DB_SSLMODE")

        auth = f"{user}:{password}@" if password else f"{user}@"
        base = f"postgresql://{auth}{host}:{port}/{dbname}"
        if sslmode:
            base += f"?sslmode={sslmode}"
        return base

    @classmethod
    def get_host(cls) -> str:
        val = get_config_value("DB_HOST")
        if val:
            return val
        db_url = cls.get_database_url()
        if db_url:
            try:
                parsed = urllib.parse.urlparse(db_url)
                if parsed.hostname:
                    return parsed.hostname
            except Exception:
                pass
        return "localhost"

    @classmethod
    def get_port(cls) -> int:
        val = get_config_value("DB_PORT")
        if val:
            try:
                return int(val)
            except ValueError:
                return 5432
        db_url = cls.get_database_url()
        if db_url:
            try:
                parsed = urllib.parse.urlparse(db_url)
                if parsed.port:
                    return parsed.port
            except Exception:
                pass
        return 5432

    @classmethod
    def get_name(cls) -> str:
        val = get_config_value("DB_NAME")
        if val:
            return val
        db_url = cls.get_database_url()
        if db_url:
            try:
                parsed = urllib.parse.urlparse(db_url)
                path = parsed.path.lstrip("/")
                if path:
                    return path
            except Exception:
                pass
        return "job_bucket"

    @classmethod
    def get_user(cls) -> str:
        val = get_config_value("DB_USER")
        if val:
            return val
        db_url = cls.get_database_url()
        if db_url:
            try:
                parsed = urllib.parse.urlparse(db_url)
                if parsed.username:
                    return parsed.username
            except Exception:
                pass
        return "postgres"

    @classmethod
    def get_password(cls) -> Optional[str]:
        val = get_config_value("DB_PASSWORD")
        if val is not None:
            return val
        db_url = cls.get_database_url()
        if db_url:
            try:
                parsed = urllib.parse.urlparse(db_url)
                if parsed.password:
                    return parsed.password
            except Exception:
                pass
        return None

    @classmethod
    def get_connection_dict(cls) -> Dict[str, Any]:
        """
        Returns connection arguments for psycopg2.
        If DATABASE_URL is present, returns {'dsn': <database_url>},
        preserving any query parameters (such as sslmode=require).
        Otherwise, returns keyword arguments dict for local PostgreSQL.
        """
        db_url = cls.get_database_url()
        if db_url:
            return {"dsn": db_url}

        cfg: Dict[str, Any] = {
            "host": cls.get_host(),
            "port": cls.get_port(),
            "dbname": cls.get_name(),
            "user": cls.get_user(),
        }
        password = cls.get_password()
        if password is not None:
            cfg["password"] = password

        sslmode = get_config_value("DB_SSLMODE")
        if sslmode:
            cfg["sslmode"] = sslmode

        return cfg

    @classmethod
    def get_masked_connection_info(cls) -> Dict[str, Any]:
        """
        Returns safe diagnostic metadata for logging and health checks.
        Never reveals connection strings, user credentials, or passwords.
        """
        db_url = cls.get_database_url()
        if db_url:
            try:
                parsed = urllib.parse.urlparse(db_url)
                query_params = urllib.parse.parse_qs(parsed.query)
                sslmode = query_params.get("sslmode", ["default"])[0]
                return {
                    "source": "DATABASE_URL",
                    "host": parsed.hostname or "unknown",
                    "port": parsed.port or 5432,
                    "dbname": (parsed.path or "").lstrip("/") or "unknown",
                    "sslmode": sslmode,
                    "authenticated": bool(parsed.username or parsed.password),
                }
            except Exception:
                return {"source": "DATABASE_URL", "configured": True}

        return {
            "source": "LEGACY_CONFIG",
            "host": cls.get_host(),
            "port": cls.get_port(),
            "dbname": cls.get_name(),
            "user": cls.get_user(),
            "password": "***" if cls.get_password() else None,
        }


def connect_database(config: Optional[Union[str, Dict[str, Any]]] = None):
    """
    Connects to PostgreSQL using either:
    - A connection string (DSN)
    - A dictionary with 'dsn' key
    - A dictionary of connection keyword arguments (host, port, dbname, etc.)
    If config is None, loads active configuration via DatabaseConfig.get_connection_dict().
    """
    if config is None:
        target = DatabaseConfig.get_connection_dict()
    else:
        target = config

    if isinstance(target, str):
        return psycopg2.connect(dsn=target)
    elif isinstance(target, dict):
        if "dsn" in target and len(target) == 1:
            return psycopg2.connect(dsn=target["dsn"])
        return psycopg2.connect(**target)
    else:
        raise TypeError(f"Database configuration must be str or dict, got {type(target).__name__}")

