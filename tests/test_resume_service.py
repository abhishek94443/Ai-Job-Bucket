from unittest.mock import MagicMock, patch
import pytest
import psycopg2

from src.resume_service import ResumeService
from src.resume_parser import EmptyFileError, MalformedFileError
from src.ai_service import AIServiceError, AIMatchEvaluation, CURRENT_PROMPT_VERSION
from src.candidate_profile import (
    CandidateProfile,
    DEFAULT_CANDIDATE_PROFILE,
    extract_deterministic_profile,
)


class TestResumeService:
    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_failed_parse_never_touches_database(self, mock_parse):
        mock_parse.side_effect = EmptyFileError("Empty file")
        mock_db = MagicMock()

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_db)

        with pytest.raises(EmptyFileError):
            service.process_and_activate_resume(b"", "empty.pdf")

        # Database connection was NEVER opened or touched
        service._get_connection.assert_not_called()

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_failed_ai_extraction_never_commits_to_database(self, mock_parse):
        mock_parse.return_value = MagicMock(
            file_name="resume.docx",
            file_type="docx",
            file_size_bytes=100,
            content_hash="abc123hash",
            file_bytes=b"valid_docx_bytes",
            extracted_text="Some valid resume text",
        )
        mock_ai = MagicMock()
        mock_ai.is_configured = True
        mock_ai.extract_candidate_profile.side_effect = AIServiceError("LLM failed")

        mock_db = MagicMock()
        mock_db.__enter__.return_value = mock_db
        mock_cur = MagicMock()
        mock_db.cursor.return_value.__enter__.return_value = mock_cur

        service = ResumeService(ai_service=mock_ai)
        service._get_connection = MagicMock(return_value=mock_db)

        with pytest.raises(AIServiceError):
            service.process_and_activate_resume(b"valid_docx_bytes", "resume.docx")

        # Database commit was NEVER called, and no activation or insertion was committed
        mock_db.commit.assert_not_called()
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        assert not any("INSERT INTO resume_versions" in sql for sql in executed_sqls)
        assert not any("INSERT INTO candidate_profiles" in sql for sql in executed_sqls)

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_transactional_activation_with_advisory_lock_and_foreign_key(self, mock_parse):
        mock_parse.return_value = MagicMock(
            file_name="abhishek_resume.pdf",
            file_type="pdf",
            file_size_bytes=1024,
            content_hash="hash123456",
            file_bytes=b"binary_pdf_data",
            extracted_text="Java Developer with Spring Boot experience",
        )
        mock_ai = MagicMock()
        mock_ai.is_configured = False  # Deterministic fallback for test

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_conn.__enter__.return_value = mock_conn
        mock_cur.fetchone.return_value = (42,)  # Returned resume_version_id

        service = ResumeService(ai_service=mock_ai)
        service._get_connection = MagicMock(return_value=mock_conn)

        resume_id, profile = service.process_and_activate_resume(
            b"binary_pdf_data",
            "abhishek_resume.pdf",
            candidate_name="Abhishek Dwivedi",
        )

        assert resume_id == 42
        assert profile.name == "Abhishek Dwivedi"

        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]

        # 1. Advisory lock acquired for concurrency serialization
        assert any("pg_advisory_xact_lock" in sql for sql in executed_sqls)
        # 2. Previous resume versions deactivated
        assert any("UPDATE resume_versions SET is_active = FALSE" in sql for sql in executed_sqls)
        # 3. New resume version inserted with is_active = TRUE and mandatory file_content
        assert any("INSERT INTO resume_versions" in sql and "file_content" in sql for sql in executed_sqls)
        # 4. Previous candidate profiles deactivated
        assert any("UPDATE candidate_profiles SET is_active = FALSE" in sql for sql in executed_sqls)
        # 5. New candidate profile inserted with is_active = TRUE and referencing resume_version_id = 42
        assert any("INSERT INTO candidate_profiles" in sql for sql in executed_sqls)

        # Inspect the parameters passed to candidate profile insertion
        insert_profile_call = [call for call in mock_cur.execute.call_args_list if "INSERT INTO candidate_profiles" in call[0][0]][0]
        params = insert_profile_call[0][1]
        assert params[0] == 42  # resume_version_id foreign key

    def test_get_active_profile_fallback_when_db_empty(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = None  # No active profile found

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        profile = service.get_active_profile()
        assert profile.name == DEFAULT_CANDIDATE_PROFILE.name
        assert profile.languages == DEFAULT_CANDIDATE_PROFILE.languages

    def test_cache_save_stores_no_raw_response(self):
        """Verifies that cached matches store only validated fields and never retain raw LLM payloads."""
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        eval_data = AIMatchEvaluation(
            match_score=85,
            fit_verdict="HIGH_FIT",
            matching_evidence=["Java", "Spring Boot", "contact candidate at test@example.com"],
            missing_requirements=["Docker"],
            experience_assessment="2 YOE matches well. Phone: +91-9876543210",
            reasoning="Strong technical alignment for abhishek.dev@example.com",
            confidence_score=0.9,
            prompt_version=CURRENT_PROMPT_VERSION,
        )

        service.save_cached_match(
            profile_hash="hash_p",
            platform="linkedin",
            platform_job_id="job_123",
            job_hash="hash_j",
            model_version="gemini-3.5-flash-lite",
            evaluation=eval_data,
        )

        executed_sql = mock_cur.execute.call_args[0][0]
        params = mock_cur.execute.call_args[0][1]

        # 1. SQL column list must NOT include raw_response
        assert "raw_response" not in executed_sql
        assert "INSERT INTO ai_match_cache" in executed_sql

        # 2. PII must be scrubbed from persisted fields
        persisted_evidence = params[8]  # json string of matching_evidence
        persisted_assessment = params[10]  # experience_assessment
        persisted_explanation = params[11]  # explanation

        assert "test@example.com" not in persisted_evidence
        assert "[REDACTED_EMAIL]" in persisted_evidence
        assert "9876543210" not in persisted_assessment
        assert "[REDACTED_PHONE]" in persisted_assessment
        assert "abhishek.dev@example.com" not in persisted_explanation
        assert "[REDACTED_EMAIL]" in persisted_explanation

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_activation_stores_null_when_experience_unknown(self, mock_parse):
        """Proves that when experience is unknown, NULL is passed to DB and no default (2.0) is invented."""
        mock_parse.return_value = MagicMock(
            file_name="grad.pdf",
            file_type="pdf",
            file_size_bytes=500,
            content_hash="hash999",
            file_bytes=b"bytes",
            extracted_text="Python Django developer with unknown experience",
        )
        mock_ai = MagicMock()
        mock_ai.is_configured = True
        # AI returns profile with None experience
        mock_ai.extract_candidate_profile.return_value = CandidateProfile(
            name="New Candidate",
            total_experience_years=None,
            experience_confirmed=False,
            languages=["Python"],
            frameworks=["Django"],
        )

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (10,)

        service = ResumeService(ai_service=mock_ai)
        service._get_connection = MagicMock(return_value=mock_conn)

        resume_id, profile = service.process_and_activate_resume(b"bytes", "grad.pdf")

        assert profile.total_experience_years is None
        assert profile.is_experience_known is False

        # Find the INSERT INTO candidate_profiles call
        insert_profile_calls = [c for c in mock_cur.execute.call_args_list if "INSERT INTO candidate_profiles" in c[0][0]]
        assert len(insert_profile_calls) == 1
        params = insert_profile_calls[0][0][1]

        # total_experience_years is the 4th parameter (index 3)
        assert params[3] is None  # Must be None, NEVER defaulted to 2.0!
        # experience_confirmed is the 5th parameter (index 4)
        assert params[4] is False

    def test_update_active_profile_preferences(self):
        """Proves that user can update search preferences without modifying resume facts."""
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        # Mock get_active_profile return after update
        service.get_active_profile = MagicMock(return_value=CandidateProfile(
            name="Abhishek",
            total_experience_years=2.0,
            experience_confirmed=True,
            target_title="Senior Java Microservices Specialist",
            hard_limit_years=5.0,
            target_cities=["bengaluru", "hyderabad"],
        ))

        updated_profile = service.update_active_profile_preferences({
            "target_title": "Senior Java Microservices Specialist",
            "hard_limit_years": 5.0,
            "target_cities": ["bengaluru", "hyderabad"],
        })

        assert updated_profile.target_title == "Senior Java Microservices Specialist"
        assert updated_profile.hard_limit_years == 5.0
        assert "bengaluru" in updated_profile.target_cities

        # Verify UPDATE SQL executed on candidate_profiles
        executed_sql = mock_cur.execute.call_args[0][0]
        assert "UPDATE candidate_profiles" in executed_sql
        assert "WHERE is_active = TRUE" in executed_sql

    def test_deterministic_extraction_missing_experience_is_none(self):
        """Proves that deterministic extraction represents missing/uncertain experience as None, never 2.0."""
        text = """
        John Smith
        Software Engineer
        Technical Skills: Python, Django, PostgreSQL, Docker
        Projects: Built RESTful API service with Docker containerization.
        """
        profile = extract_deterministic_profile(text, candidate_name="John Smith")

        assert profile.name == "John Smith"
        assert profile.total_experience_years is None
        assert profile.is_experience_known is False
        assert profile.experience_confirmed is False
        assert "Python" in profile.languages
        assert "Django" in profile.frameworks
        assert "PostgreSQL" in profile.databases
        assert "Docker" in profile.devops_and_tools
        # Preferences are explicitly marked as editable defaults
        assert profile.preferences_source == "editable_default"
        assert profile.target_title == "Python Developer"
        assert profile.experience_range_min == 0.0
        assert profile.experience_range_max == 3.0
        assert profile.hard_limit_years == 4.0

    def test_deterministic_extraction_explicit_experience_pattern(self):
        """Proves that explicit experience in text is detected but still marked unconfirmed until user verifies."""
        text = """
        Backend Developer with 3.5 years of experience in Java microservices.
        Skills: Java, Spring Boot, Kafka, Microservices, PostgreSQL, Docker, Kubernetes
        """
        profile = extract_deterministic_profile(text)

        assert profile.total_experience_years == 3.5
        assert profile.is_experience_known is True
        assert profile.experience_confirmed is False  # Must require user confirmation
        assert "Java" in profile.languages
        assert "Spring Boot" in profile.frameworks
        assert "Kafka" in profile.messaging_and_cache
        assert "Event-driven microservices architecture" in profile.inferred_capabilities
        # Preferences initialized relative to detected experience, but marked as editable defaults
        assert profile.preferences_source == "editable_default"
        assert profile.experience_range_min == 2.5
        assert profile.experience_range_max == 4.5
        assert profile.hard_limit_years == 5.5

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_offline_incomplete_extraction_does_not_invent_abhishek_facts(self, mock_parse):
        """Proves that offline processing of a third-party resume never silently invents Abhishek's facts or 2.0 YOE."""
        mock_parse.return_value = MagicMock(
            file_name="jane_golang.pdf",
            file_type="pdf",
            file_size_bytes=800,
            content_hash="hash_jane",
            file_bytes=b"raw_pdf_jane",
            extracted_text="Jane Doe\nBackend Developer with Python and PostgreSQL knowledge.",
        )
        mock_ai = MagicMock()
        mock_ai.is_configured = False  # No AI API key

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (88,)

        service = ResumeService(ai_service=mock_ai)
        service._get_connection = MagicMock(return_value=mock_conn)

        resume_id, profile = service.process_and_activate_resume(
            b"raw_pdf_jane",
            "jane_golang.pdf",
            candidate_name="Jane Doe",
        )

        assert profile.name == "Jane Doe"
        # Must NOT invent Abhishek's 2.0 years
        assert profile.total_experience_years is None
        assert profile.is_experience_known is False
        assert profile.experience_confirmed is False
        # Must NOT invent Abhishek's Java skills
        assert "Java" not in profile.languages
        assert "Spring Boot" not in profile.frameworks
        assert "Python" in profile.languages
        assert "PostgreSQL" in profile.databases

        # Verify DB insert payload has None for experience
        insert_calls = [c for c in mock_cur.execute.call_args_list if "INSERT INTO candidate_profiles" in c[0][0]]
        assert len(insert_calls) == 1
        params = insert_calls[0][0][1]
        assert params[2] == "Jane Doe"
        assert params[3] is None  # total_experience_years is NULL
        assert params[4] is False  # experience_confirmed is FALSE

    def test_unmocked_connection_delegates_to_connect_database_for_all_operations(self):
        """
        Verifies that ResumeService._get_connection() delegates directly to connect_database(self.config),
        allowing active profile checks, resume metadata retrieval, and profile loading without
        attempting to call any nonexistent get_connection_string method.
        """
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Sample row for candidate_profiles
        mock_cur.fetchone.side_effect = [
            # 1. has_active_profile fetchone -> (1,)
            (1,),
            # 2. get_active_resume_metadata fetchone -> row dict
            {
                "id": 10,
                "file_name": "resume.pdf",
                "file_type": "pdf",
                "file_size_bytes": 1024,
                "content_hash": "hash123",
                "extraction_status": "EXTRACTED",
                "is_active": True,
                "created_at": "2026-10-11",
                "updated_at": "2026-10-11",
            },
            # 3. get_active_profile fetchone -> row dict
            {
                "id": 1,
                "candidate_name": "Jane Doe",
                "target_title": "Backend Engineer",
                "total_experience_years": 3.0,
                "experience_confirmed": True,
                "experience_range_min": 2.0,
                "experience_range_max": 5.0,
                "hard_limit_years": 6.0,
                "languages": ["Python"],
                "frameworks": ["FastAPI"],
                "databases": ["PostgreSQL"],
                "messaging_cache": ["Redis"],
                "devops_cloud": ["Docker"],
                "inferred_capabilities": ["REST APIs"],
                "target_locations": ["Bangalore"],
                "remote_preference": "flexible",
                "raw_profile_json": {},
            },
        ]

        with patch("src.resume_service.connect_database", return_value=mock_conn) as mock_connect_helper:
            # Instantiate with explicit config dict, testing unmocked _get_connection
            service = ResumeService(config={"dsn": "postgresql://test:test@localhost:5432/testdb"})

            # 1. has_active_profile
            has_active = service.has_active_profile()
            assert has_active is True

            # 2. get_active_resume_metadata
            meta = service.get_active_resume_metadata()
            assert meta is not None
            assert meta["file_name"] == "resume.pdf"

            # 3. get_active_profile
            prof = service.get_active_profile()
            assert prof.name == "Jane Doe"
            assert prof.total_experience_years == 3.0

            # Verify connect_database was called for each operation
            assert mock_connect_helper.call_count == 3
            for c in mock_connect_helper.call_args_list:
                assert c[0][0] == {"dsn": "postgresql://test:test@localhost:5432/testdb"}

    def test_no_code_path_calls_nonexistent_get_connection_string(self):
        """
        Verifies that even if db_config strictly forbids get_connection_string (e.g. lacks it),
        activation and reading succeed cleanly without AttributeError.
        """
        class StrictConfigWithoutGetConnectionString:
            @classmethod
            def get_connection_dict(cls):
                return {"dsn": "postgresql://mock:mock@localhost:5432/mockdb"}

        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (42,)

        with patch("src.resume_service.connect_database", return_value=mock_conn):
            service = ResumeService(db_config=StrictConfigWithoutGetConnectionString)

            # Test that calling _get_connection does not attempt get_connection_string
            conn = service._get_connection()
            assert conn == mock_conn

            # Test activation flow does not call get_connection_string
            mock_parse = MagicMock(
                file_name="resume.pdf",
                file_type="pdf",
                file_size_bytes=500,
                content_hash="h123",
                file_bytes=b"sample",
                extracted_text="Sample text",
            )
            mock_profile = CandidateProfile(
                name="Test Candidate",
                total_experience_years=2.0,
                experience_confirmed=True,
            )
            resume_id, activated_profile = service.activate_reviewed_profile(mock_parse, mock_profile)
            assert resume_id == 42
            assert activated_profile.name == "Test Candidate"

    def test_activation_rollback_on_failure_preserves_database(self):
        """
        Verifies that when database error occurs during activation,
        conn.rollback() is strictly invoked and the exception propagates.
        """
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Make the insertion fail
        mock_cur.execute.side_effect = [
            None,  # advisory lock
            None,  # update resume_versions
            psycopg2.DatabaseError("Disk full or connection aborted"),
        ]

        with patch("src.resume_service.connect_database", return_value=mock_conn):
            service = ResumeService()
            mock_parse = MagicMock(
                file_name="test.pdf",
                file_type="pdf",
                file_size_bytes=100,
                content_hash="h",
                file_bytes=b"bytes",
                extracted_text="text",
            )
            mock_profile = CandidateProfile(name="Test")

            with pytest.raises(psycopg2.DatabaseError, match="Disk full or connection aborted"):
                service.activate_reviewed_profile(mock_parse, mock_profile)

            # Rollback must be explicitly executed
            mock_conn.rollback.assert_called_once()
            mock_conn.close.assert_called_once()



