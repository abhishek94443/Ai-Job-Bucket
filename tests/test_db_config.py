import sys
from types import ModuleType
from unittest.mock import MagicMock, patch
import pytest

from src.db_config import (
    DatabaseConfig,
    connect_database,
    get_config_value,
    get_streamlit_secret,
)
from src.db_persistence import DatabasePersistence
from src.dashboard_service import DashboardQueryService
from src.resume_service import ResumeService


SYNTHETIC_NEON_DSN = "postgresql://synth_user:synth_secret@ep-cool-project.ap-southeast-1.aws.neon.tech/neondb?sslmode=require"
SYNTHETIC_LOCAL_DSN = "postgresql://postgres:localpass@localhost:5432/job_bucket"


def test_database_url_precedence_over_legacy_settings(monkeypatch):
    """Verifies DATABASE_URL takes strict precedence over DB_* split variables."""
    monkeypatch.setenv("DATABASE_URL", SYNTHETIC_NEON_DSN)
    monkeypatch.setenv("DB_HOST", "legacy-localhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "legacy_db")
    monkeypatch.setenv("DB_USER", "legacy_user")
    monkeypatch.setenv("DB_PASSWORD", "legacy_pass")

    cfg = DatabaseConfig.get_connection_dict()
    assert "dsn" in cfg
    assert cfg["dsn"] == SYNTHETIC_NEON_DSN

    # Legacy getters parse from DATABASE_URL
    assert DatabaseConfig.get_database_url() == SYNTHETIC_NEON_DSN


def test_connect_database_passes_valid_dsn_to_psycopg2():
    """Verifies that connect_database passes a PostgreSQL DSN to psycopg2.connect correctly."""
    with patch("psycopg2.connect") as mock_connect:
        mock_connect.return_value = MagicMock()

        # 1. Passed as string
        conn1 = connect_database(SYNTHETIC_NEON_DSN)
        mock_connect.assert_called_with(dsn=SYNTHETIC_NEON_DSN)

        # 2. Passed as dict with 'dsn'
        conn2 = connect_database({"dsn": SYNTHETIC_NEON_DSN})
        mock_connect.assert_called_with(dsn=SYNTHETIC_NEON_DSN)


def test_legacy_configuration_works_when_database_url_absent(monkeypatch):
    """Verifies split DB_* parameters are used when DATABASE_URL is not provided."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "local-test-db")
    monkeypatch.setenv("DB_PORT", "5433")
    monkeypatch.setenv("DB_NAME", "custom_job_bucket")
    monkeypatch.setenv("DB_USER", "test_dev")
    monkeypatch.setenv("DB_PASSWORD", "dev_secret")

    cfg = DatabaseConfig.get_connection_dict()
    assert "dsn" not in cfg
    assert cfg["host"] == "local-test-db"
    assert cfg["port"] == 5433
    assert cfg["dbname"] == "custom_job_bucket"
    assert cfg["user"] == "test_dev"
    assert cfg["password"] == "dev_secret"

    with patch("psycopg2.connect") as mock_connect:
        connect_database(cfg)
        mock_connect.assert_called_with(
            host="local-test-db",
            port=5433,
            dbname="custom_job_bucket",
            user="test_dev",
            password="dev_secret",
        )


def test_streamlit_secrets_handled_safely_when_available(monkeypatch):
    """Verifies Streamlit secrets are read when present and env var is not set."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    fake_st = ModuleType("streamlit")
    fake_st.secrets = {"DATABASE_URL": SYNTHETIC_NEON_DSN}
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    assert get_streamlit_secret("DATABASE_URL") == SYNTHETIC_NEON_DSN
    assert DatabaseConfig.get_database_url() == SYNTHETIC_NEON_DSN

    # But environment variable still takes precedence over Streamlit secrets
    monkeypatch.setenv("DATABASE_URL", SYNTHETIC_LOCAL_DSN)
    assert DatabaseConfig.get_database_url() == SYNTHETIC_LOCAL_DSN


def test_streamlit_secrets_ignored_when_unavailable(monkeypatch):
    """Verifies get_streamlit_secret gracefully returns None when streamlit is not loaded."""
    monkeypatch.delitem(sys.modules, "streamlit", raising=False)
    assert get_streamlit_secret("DATABASE_URL") is None


def test_credentials_never_exposed_in_masked_info(monkeypatch):
    """Verifies secrets and raw connection strings are never exposed in masked metadata."""
    secret_pass = "super_secret_neon_password_123"
    dsn = f"postgresql://synth_user:{secret_pass}@ep-example.aws.neon.tech/neondb?sslmode=require"
    monkeypatch.setenv("DATABASE_URL", dsn)

    masked = DatabaseConfig.get_masked_connection_info()
    masked_str = str(masked)

    assert secret_pass not in masked_str
    assert "synth_user" not in masked_str
    assert masked["source"] == "DATABASE_URL"
    assert masked["host"] == "ep-example.aws.neon.tech"
    assert masked["dbname"] == "neondb"
    assert masked["sslmode"] == "require"
    assert masked["authenticated"] is True


def test_ssl_parameters_preserved_in_dsn(monkeypatch):
    """Verifies SSL mode in connection string is preserved when passing to connect_database."""
    ssl_dsn = "postgresql://user:pass@ep-project.aws.neon.tech/dbname?sslmode=require&connect_timeout=10"
    monkeypatch.setenv("DATABASE_URL", ssl_dsn)

    cfg = DatabaseConfig.get_connection_dict()
    assert cfg["dsn"] == ssl_dsn

    with patch("psycopg2.connect") as mock_connect:
        connect_database()
        mock_connect.assert_called_with(dsn=ssl_dsn)


def test_persistence_service_uses_connect_database():
    """Verifies DatabasePersistence delegates connection establishment to connect_database."""
    mock_conn = MagicMock()
    with patch("src.db_persistence.connect_database", return_value=mock_conn) as mock_connect_helper:
        db = DatabasePersistence(config={"dsn": SYNTHETIC_NEON_DSN})
        conn = db.get_connection()

        assert conn == mock_conn
        mock_connect_helper.assert_called_once_with({"dsn": SYNTHETIC_NEON_DSN})


def test_dashboard_service_uses_connect_database():
    """Verifies DashboardQueryService delegates connection establishment to connect_database."""
    mock_conn = MagicMock()
    with patch("src.dashboard_service.connect_database", return_value=mock_conn) as mock_connect_helper:
        service = DashboardQueryService(config={"dsn": SYNTHETIC_NEON_DSN})
        conn = service.get_connection()

        assert conn == mock_conn
        mock_connect_helper.assert_called_once_with({"dsn": SYNTHETIC_NEON_DSN})


def test_resume_service_uses_connect_database():
    """Verifies ResumeService delegates connection establishment to connect_database."""
    mock_conn = MagicMock()
    with patch("src.resume_service.connect_database", return_value=mock_conn) as mock_connect_helper:
        service = ResumeService(config={"dsn": SYNTHETIC_NEON_DSN})
        conn = service.get_connection()

        assert conn == mock_conn
        mock_connect_helper.assert_called_once_with({"dsn": SYNTHETIC_NEON_DSN})


def test_resume_service_default_init_uses_connect_database(monkeypatch):
    """Verifies default ResumeService() correctly delegates to connect_database with active config dict."""
    monkeypatch.setenv("DATABASE_URL", SYNTHETIC_NEON_DSN)
    mock_conn = MagicMock()
    with patch("src.resume_service.connect_database", return_value=mock_conn) as mock_connect_helper:
        service = ResumeService()
        conn = service._get_connection()

        assert conn == mock_conn
        mock_connect_helper.assert_called_once_with({"dsn": SYNTHETIC_NEON_DSN})


def test_database_config_get_connection_string_helper(monkeypatch):
    """Verifies DatabaseConfig.get_connection_string() provides expected DSN."""
    monkeypatch.setenv("DATABASE_URL", SYNTHETIC_NEON_DSN)
    assert DatabaseConfig.get_connection_string() == SYNTHETIC_NEON_DSN

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DB_HOST", "myhost")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_NAME", "mydb")
    monkeypatch.setenv("DB_USER", "myuser")
    monkeypatch.setenv("DB_PASSWORD", "mypass")
    monkeypatch.setenv("DB_SSLMODE", "require")
    dsn = DatabaseConfig.get_connection_string()
    assert dsn == "postgresql://myuser:mypass@myhost:5432/mydb?sslmode=require"
