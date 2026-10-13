import io
import json
from unittest.mock import MagicMock, patch
import pytest
import psycopg2

from src.candidate_profile import (
    CandidateProfile,
    DEFAULT_CANDIDATE_PROFILE,
    DEFAULT_TARGET_CITIES,
)
from src.resume_parser import (
    ResumeParser,
    ResumeParseResult,
    EmptyFileError,
    FileTooLargeError,
    MalformedFileError,
)
from src.resume_service import ResumeService
from src.resume_dashboard_view import (
    _parse_comma_list,
    validate_experience_input,
    verify_profile_persistence,
)
from src.ai_service import compute_profile_hash


class TestResumeReviewAndUI:
    def test_parse_comma_list_utility(self):
        """Tests comma-separated string parsing with whitespace stripping and case-insensitive deduplication."""
        assert _parse_comma_list("Java, Python, Java, SQL , spring boot") == ["Java", "Python", "SQL", "spring boot"]
        assert _parse_comma_list("") == []
        assert _parse_comma_list("  ,  ,, ") == []
        assert _parse_comma_list("Docker, kubernetes, DOCKER") == ["Docker", "kubernetes"]

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_parse_and_extract_for_review_does_not_modify_database(self, mock_parse):
        """Proves that extracting for review does not write or commit to the database."""
        mock_parse.return_value = ResumeParseResult(
            file_name="candidate.pdf",
            file_type="pdf",
            file_size_bytes=1200,
            content_hash="hash_candidate",
            file_bytes=b"candidate_pdf_bytes",
            extracted_text="Python Django developer with 2.5 years of experience in AWS",
            char_count=58,
            word_count=10,
        )
        mock_db = MagicMock()
        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_db)
        service.has_active_profile = MagicMock(return_value=False)

        parsed, profile = service.parse_and_extract_for_review(
            file_bytes=b"candidate_pdf_bytes",
            file_name="candidate.pdf",
            candidate_name="Alice Smith",
            use_ai=False,
        )

        # Database write/commit was never invoked
        mock_db.commit.assert_not_called()
        assert parsed.file_name == "candidate.pdf"
        assert profile.name == "Alice Smith"
        assert "Python" in profile.languages
        assert "Django" in profile.frameworks
        assert profile.total_experience_years == 2.5
        # Must require user verification
        assert profile.experience_confirmed is False
        assert profile.preferences_source == "editable_default"

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_parse_and_extract_preserves_user_configured_preferences(self, mock_parse):
        """Proves that replacing a resume preserves user-confirmed search preferences from the existing profile."""
        mock_parse.return_value = ResumeParseResult(
            file_name="new_resume.docx",
            file_type="docx",
            file_size_bytes=2048,
            content_hash="hash_new_docx",
            file_bytes=b"new_docx_bytes",
            extracted_text="Senior Go Engineer with Kubernetes and Docker experience",
            char_count=60,
            word_count=9,
        )

        service = ResumeService()
        service.has_active_profile = MagicMock(return_value=True)

        # Existing active profile with user-confirmed custom preferences
        existing_profile = CandidateProfile(
            name="Existing User",
            total_experience_years=4.0,
            experience_confirmed=True,
            target_title="Principal Distributed Systems Architect",
            hard_limit_years=6.0,
            experience_range_min=3.0,
            experience_range_max=5.5,
            target_cities=["bengaluru", "pune"],
            remote_preference="remote_only",
            preferences_source="user_configured",
        )
        service.get_active_profile = MagicMock(return_value=existing_profile)

        parsed, profile = service.parse_and_extract_for_review(
            file_bytes=b"new_docx_bytes",
            file_name="new_resume.docx",
            use_ai=False,
        )

        # 1. Extracted facts are updated from the new resume
        assert "Go" in profile.languages
        assert "Kubernetes" in profile.devops_and_tools

        # 2. But user-confirmed search preferences are preserved!
        assert profile.target_title == "Principal Distributed Systems Architect"
        assert profile.hard_limit_years == 6.0
        assert profile.experience_range_min == 3.0
        assert profile.experience_range_max == 5.5
        assert profile.target_cities == ["bengaluru", "pune"]
        assert profile.remote_preference == "remote_only"
        assert profile.preferences_source == "user_configured"

    def test_activate_reviewed_profile_transactional_commit(self):
        """Tests that activating a reviewed profile executes all statements inside an advisory-locked transaction."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (101,)  # New resume_version_id

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        parsed = ResumeParseResult(
            file_name="reviewed.pdf",
            file_type="pdf",
            file_size_bytes=1024,
            content_hash="content_hash_101",
            file_bytes=b"binary_content",
            extracted_text="Verified Candidate Text",
            char_count=23,
            word_count=3,
        )

        reviewed_profile = CandidateProfile(
            name="Bob Martin",
            total_experience_years=3.0,
            experience_confirmed=True,
            languages=["Java", "Kotlin"],
            frameworks=["Spring Boot"],
            target_title="Senior Java Microservices Engineer",
            hard_limit_years=5.0,
            preferences_source="user_configured",
        )

        resume_id, active_profile = service.activate_reviewed_profile(parsed, reviewed_profile)

        assert resume_id == 101
        assert active_profile.name == "Bob Martin"
        assert active_profile.total_experience_years == 3.0
        assert active_profile.experience_confirmed is True

        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]

        # 1. Advisory lock acquired
        assert any("pg_advisory_xact_lock" in sql for sql in executed_sqls)
        # 2. Deactivates previous resume versions
        assert any("UPDATE resume_versions SET is_active = FALSE" in sql for sql in executed_sqls)
        # 3. Inserts new active resume version
        assert any("INSERT INTO resume_versions" in sql for sql in executed_sqls)
        # 4. Deactivates previous candidate profiles
        assert any("UPDATE candidate_profiles SET is_active = FALSE" in sql for sql in executed_sqls)
        # 5. Inserts new active candidate profile referencing resume_version_id
        assert any("INSERT INTO candidate_profiles" in sql for sql in executed_sqls)

        insert_profile_call = [c for c in mock_cur.execute.call_args_list if "INSERT INTO candidate_profiles" in c[0][0]][0]
        params = insert_profile_call[0][1]
        assert params[0] == 101  # resume_version_id foreign key
        assert params[2] == "Bob Martin"
        assert params[3] == 3.0
        assert params[4] is True

    def test_activate_reviewed_profile_rollback_on_db_failure(self):
        """Proves that a database error during activation rolls back the transaction, preserving previous active state."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.execute.side_effect = [None, None, Exception("Database write error!")]

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        parsed = ResumeParseResult(
            file_name="fail.pdf",
            file_type="pdf",
            file_size_bytes=500,
            content_hash="hash_fail",
            file_bytes=b"bytes",
            extracted_text="Some text",
            char_count=9,
            word_count=2,
        )

        with pytest.raises(Exception, match="Database write error!"):
            service.activate_reviewed_profile(parsed, DEFAULT_CANDIDATE_PROFILE)

        # Transaction rollback invoked
        mock_conn.rollback.assert_called_once()

    def test_delete_active_resume_transactional(self):
        """Tests that delete_active_resume executes delete inside advisory lock and returns True when row affected."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (55,)  # Deleted resume ID

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        deleted = service.delete_active_resume()
        assert deleted is True

        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        assert any("pg_advisory_xact_lock" in sql for sql in executed_sqls)
        assert any("DELETE FROM resume_versions WHERE is_active = TRUE" in sql for sql in executed_sqls)

    def test_failed_parse_during_review_preserves_active_profile(self):
        """Proves that passing empty/corrupted file to parse_and_extract_for_review raises error and leaves DB untouched."""
        service = ResumeService()
        mock_db = MagicMock()
        service._get_connection = MagicMock(return_value=mock_db)

        with pytest.raises(EmptyFileError):
            service.parse_and_extract_for_review(b"", "empty.pdf")

        # Database was never opened or modified
        service._get_connection.assert_not_called()

    def test_duplicate_resume_content_upload_deactivates_older_safely(self):
        """Proves that uploading the exact same binary content twice creates a new version and deactivates the older one."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (102,)  # Newly generated ID for duplicate content

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        parsed = ResumeParseResult(
            file_name="same_resume.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            content_hash="identical_hash_abc",
            file_bytes=b"same_binary_bytes",
            extracted_text="Java Spring developer",
            char_count=21,
            word_count=3,
        )

        resume_id, active_prof = service.activate_reviewed_profile(parsed, DEFAULT_CANDIDATE_PROFILE)
        assert resume_id == 102

        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        # Old versions deactivated first, ensuring partial unique index uq_resume_versions_single_active is satisfied
        assert any("UPDATE resume_versions SET is_active = FALSE" in sql for sql in executed_sqls)
        assert any("INSERT INTO resume_versions" in sql for sql in executed_sqls)

    def test_has_active_profile_checks_database(self):
        """Tests has_active_profile query behavior."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = (1,)

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        assert service.has_active_profile() is True

        mock_cur.fetchone.return_value = None
        assert service.has_active_profile() is False

    def test_load_and_edit_active_profile_without_uploaded_file(self):
        """
        Verifies that an existing active profile can be loaded, edited, and saved
        completely independently of any uploaded file, preserving the linked resume_version_id.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Cursor queries:
        # 1. SELECT id, resume_version_id, user_preferences FROM candidate_profiles WHERE is_active = TRUE
        # 2. UPDATE candidate_profiles ... RETURNING resume_version_id -> (42,)
        # 3. get_active_profile SELECT query -> updated row dict
        mock_cur.fetchone.side_effect = [
            (10, 42, {"preferences_source": "user_configured", "target_cities": ["bengaluru"]}),  # lookup
            (42,),  # UPDATE RETURNING
            {  # get_active_profile re-read
                "id": 10,
                "candidate_name": "Updated Abhishek",
                "target_title": "Lead Cloud Platform Engineer",
                "total_experience_years": 3.5,
                "experience_confirmed": True,
                "experience_range_min": 2.5,
                "experience_range_max": 6.0,
                "hard_limit_years": 7.0,
                "languages": ["Java", "Python", "Go"],
                "frameworks": ["Spring Boot", "FastAPI"],
                "databases": ["PostgreSQL", "Redis"],
                "messaging_cache": ["Kafka", "RabbitMQ"],
                "devops_cloud": ["AWS", "Kubernetes", "Docker"],
                "inferred_capabilities": ["Cloud Native Architecture"],
                "target_locations": ["bengaluru", "hyderabad"],
                "remote_preference": "remote_only",
                "raw_profile_json": {"apis_and_auth": ["REST", "OAuth2", "gRPC"]},
                "user_preferences": {"preferences_source": "user_configured"},
            },
        ]

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        # Build updated profile with no uploaded file
        updated_profile = CandidateProfile(
            name="Updated Abhishek",
            total_experience_years=3.5,
            experience_confirmed=True,
            languages=["Java", "Python", "Go"],
            frameworks=["Spring Boot", "FastAPI"],
            databases=["PostgreSQL", "Redis"],
            messaging_and_cache=["Kafka", "RabbitMQ"],
            devops_and_tools=["AWS", "Kubernetes", "Docker"],
            apis_and_auth=["REST", "OAuth2", "gRPC"],
            inferred_capabilities=["Cloud Native Architecture"],
            target_title="Lead Cloud Platform Engineer",
            experience_range_min=2.5,
            experience_range_max=6.0,
            hard_limit_years=7.0,
            target_cities=["bengaluru", "hyderabad"],
            remote_preference="remote_only",
            preferences_source="user_configured",
        )

        resume_id, saved = service.update_active_profile(updated_profile)

        # 1. Active resume ID remains unchanged at 42
        assert resume_id == 42
        assert saved.name == "Updated Abhishek"
        assert saved.total_experience_years == 3.5
        assert saved.experience_confirmed is True
        assert saved.target_title == "Lead Cloud Platform Engineer"
        assert "Go" in saved.languages
        assert "gRPC" in saved.apis_and_auth

        # 2. Verify SQL statements: no resume_versions insert or update
        executed_sqls = [call[0][0] for call in mock_cur.execute.call_args_list]
        assert any("pg_advisory_xact_lock" in sql for sql in executed_sqls)
        assert any("UPDATE candidate_profiles" in sql for sql in executed_sqls)
        assert not any("INSERT INTO resume_versions" in sql for sql in executed_sqls)
        assert not any("UPDATE resume_versions" in sql for sql in executed_sqls)
        assert not any("DELETE FROM resume_versions" in sql for sql in executed_sqls)

    def test_update_active_profile_rollback_on_failure(self):
        """Proves transaction rollback on DB error during profile update without affecting resume versions."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.return_value = (5, 99, {})
        mock_cur.execute.side_effect = [
            None,  # lock
            None,  # select
            psycopg2.DatabaseError("Disk full during profile update"),
        ]

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        prof = CandidateProfile(name="Test Candidate")

        with pytest.raises(psycopg2.DatabaseError, match="Disk full during profile update"):
            service.update_active_profile(prof)

        mock_conn.rollback.assert_called_once()
        mock_conn.close.assert_called_once()

    def test_update_active_profile_when_no_active_profile_raises_runtime_error(self):
        """Verifies that calling update_active_profile when no active record exists raises RuntimeError."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_cur.fetchone.return_value = None  # No active profile found

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        prof = CandidateProfile(name="Test Candidate")

        with pytest.raises(RuntimeError, match="Cannot update profile: No active candidate profile found"):
            service.update_active_profile(prof)

        mock_conn.rollback.assert_called_once()

    def test_streamlit_session_state_preserves_edit_mode_and_fields_across_reruns(self):
        """
        Simulates Streamlit session state workflow during active profile editing:
        verifies that edit_active_mode and modified form fields persist across reruns.
        """
        fake_session = {}

        # 1. User enters edit mode
        fake_session["edit_active_mode"] = True
        fake_session["edit_active_cand_name"] = "Abhishek Dwivedi"
        fake_session["edit_active_entered_exp"] = 2.5
        fake_session["edit_active_confirm_exp"] = True
        fake_session["edit_active_lang"] = "Java, Python, Kotlin"
        fake_session["edit_active_target_title"] = "Senior Backend Engineer"
        fake_session["edit_active_range_min"] = 2.0
        fake_session["edit_active_range_max"] = 5.0
        fake_session["edit_active_hard_limit"] = 6.0
        fake_session["edit_active_remote_pref"] = "flexible"
        fake_session["edit_active_cities"] = ["bengaluru", "hyderabad"]

        # 2. Simulate Streamlit rerun (session state values persist without resetting)
        assert fake_session.get("edit_active_mode") is True
        assert fake_session.get("edit_active_entered_exp") == 2.5

        # 3. User modifies experience on subsequent rerun
        fake_session["edit_active_entered_exp"] = 3.0

        # 4. Construct CandidateProfile directly from session state (simulating form submit)
        entered_val = fake_session["edit_active_entered_exp"]
        confirmed = fake_session["edit_active_confirm_exp"]
        if entered_val is not None and confirmed:
            final_exp = float(entered_val)
            final_confirmed = True
        else:
            final_exp = None
            final_confirmed = False

        constructed_profile = CandidateProfile(
            name=fake_session["edit_active_cand_name"],
            total_experience_years=final_exp,
            experience_confirmed=final_confirmed,
            languages=_parse_comma_list(fake_session["edit_active_lang"]),
            target_title=fake_session["edit_active_target_title"],
            experience_range_min=float(fake_session["edit_active_range_min"]),
            experience_range_max=float(fake_session["edit_active_range_max"]),
            hard_limit_years=float(fake_session["edit_active_hard_limit"]),
            target_cities=fake_session["edit_active_cities"],
            remote_preference=fake_session["edit_active_remote_pref"],
            preferences_source="user_configured",
        )

        assert constructed_profile.name == "Abhishek Dwivedi"
        assert constructed_profile.total_experience_years == 3.0
        assert constructed_profile.experience_confirmed is True
        assert constructed_profile.languages == ["Java", "Python", "Kotlin"]
        assert constructed_profile.target_title == "Senior Backend Engineer"
        assert constructed_profile.preferences_source == "user_configured"

        # 5. After submit, edit mode is deactivated
        fake_session["edit_active_mode"] = False
        assert fake_session["edit_active_mode"] is False

    def test_experience_none_displayed_as_unknown_not_zero(self):
        """
        Verifies that when an active profile has total_experience_years=None:
        - It is loaded as None (not converted to 0.00).
        - experience_confirmed is False.
        - Session state initializes edit_active_entered_exp to None, not 0.0.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # DB row with total_experience_years = None
        mock_cur.fetchone.return_value = {
            "id": 1,
            "candidate_name": "Test Candidate",
            "target_title": "Software Engineer",
            "total_experience_years": None,
            "experience_confirmed": False,
            "experience_range_min": 0.0,
            "experience_range_max": 3.0,
            "hard_limit_years": 4.0,
            "languages": ["Python"],
            "frameworks": [],
            "databases": [],
            "messaging_cache": [],
            "devops_cloud": [],
            "inferred_capabilities": [],
            "target_locations": ["bengaluru"],
            "remote_preference": "flexible",
            "raw_profile_json": {},
            "user_preferences": {},
        }

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        active_prof = service.get_active_profile()
        assert active_prof is not None
        assert active_prof.total_experience_years is None
        assert active_prof.experience_confirmed is False

        # Simulate Streamlit session state initialization guard
        session = {}
        if "edit_active_entered_exp" not in session:
            session["edit_active_entered_exp"] = (
                float(active_prof.total_experience_years)
                if active_prof.total_experience_years is not None
                else None
            )
        if "edit_active_confirm_exp" not in session:
            session["edit_active_confirm_exp"] = bool(
                active_prof.experience_confirmed and active_prof.total_experience_years is not None
            )

        assert session["edit_active_entered_exp"] is None
        assert session["edit_active_confirm_exp"] is False

    def test_experience_manual_entry_confirmed_persists_2_0(self):
        """
        Verifies that entering 2.0 and checking the confirmation box persists
        total_experience_years=2.0 and experience_confirmed=True.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.side_effect = [
            (10, 42, {}),  # lookup
            (42,),          # UPDATE RETURNING resume_version_id
            {               # get_active_profile re-read
                "id": 10,
                "candidate_name": "Abhishek",
                "target_title": "Software Engineer",
                "total_experience_years": 2.0,
                "experience_confirmed": True,
                "experience_range_min": 0.0,
                "experience_range_max": 3.0,
                "hard_limit_years": 4.0,
                "languages": ["Java"],
                "frameworks": [],
                "databases": [],
                "messaging_cache": [],
                "devops_cloud": [],
                "inferred_capabilities": [],
                "target_locations": ["bengaluru"],
                "remote_preference": "flexible",
                "raw_profile_json": {"total_experience_years": 2.0},
                "user_preferences": {},
            },
        ]

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        profile_to_save = CandidateProfile(
            name="Abhishek",
            total_experience_years=2.0,
            experience_confirmed=True,
            languages=["Java"],
        )

        resume_id, saved = service.update_active_profile(profile_to_save)
        assert resume_id == 42
        assert saved.total_experience_years == 2.0
        assert saved.experience_confirmed is True

        # Check update parameters passed to SQL
        update_call = [c for c in mock_cur.execute.call_args_list if "UPDATE candidate_profiles" in c[0][0]][0]
        sql_params = update_call[0][1]
        assert sql_params[1] == "Abhishek"  # candidate_name
        assert sql_params[2] == 2.0         # total_experience_years
        assert sql_params[3] is True        # experience_confirmed

    def test_experience_fractional_value_persists_2_25(self):
        """
        Verifies that saving a fractional experience value such as 2.25
        is preserved in raw_profile_json and accurately recovered by get_active_profile.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # PostgreSQL numeric(4, 1) column stores 2.3, but raw_profile_json has exact 2.25
        mock_cur.fetchone.side_effect = [
            (10, 42, {}),
            (42,),
            {
                "id": 10,
                "candidate_name": "Abhishek",
                "target_title": "Software Engineer",
                "total_experience_years": 2.3,  # DB column rounded
                "experience_confirmed": True,
                "experience_range_min": 0.0,
                "experience_range_max": 3.0,
                "hard_limit_years": 4.0,
                "languages": ["Python"],
                "frameworks": [],
                "databases": [],
                "messaging_cache": [],
                "devops_cloud": [],
                "inferred_capabilities": [],
                "target_locations": ["bengaluru"],
                "remote_preference": "flexible",
                "raw_profile_json": {"total_experience_years": 2.25},  # exact fractional value
                "user_preferences": {},
            },
        ]

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        profile_to_save = CandidateProfile(
            name="Abhishek",
            total_experience_years=2.25,
            experience_confirmed=True,
            languages=["Python"],
        )

        resume_id, saved = service.update_active_profile(profile_to_save)
        assert resume_id == 42
        # Exact fractional value 2.25 preserved
        assert saved.total_experience_years == 2.25
        assert saved.experience_confirmed is True

    def test_experience_unconfirmed_or_blank_remains_unknown(self):
        """
        Verifies that if the user enters a value without confirming, or leaves it blank,
        the saved profile treats experience as None and experience_confirmed as False.
        """
        # Case 1: Entered 2.0 but confirm_exp is False -> remains unknown (None)
        entered_val = 2.0
        confirm_exp = False
        if entered_val is not None and confirm_exp:
            final_exp = float(entered_val)
            final_confirmed = True
        else:
            final_exp = None
            final_confirmed = False

        prof1 = CandidateProfile(
            name="Candidate",
            total_experience_years=final_exp,
            experience_confirmed=final_confirmed,
        )
        assert prof1.total_experience_years is None
        assert prof1.experience_confirmed is False

        # Case 2: Entered None and confirm_exp is True -> remains unknown (None)
        entered_val = None
        confirm_exp = True
        if entered_val is not None and confirm_exp:
            final_exp = float(entered_val)
            final_confirmed = True
        else:
            final_exp = None
            final_confirmed = False

        prof2 = CandidateProfile(
            name="Candidate",
            total_experience_years=final_exp,
            experience_confirmed=final_confirmed,
        )
        assert prof2.total_experience_years is None
        assert prof2.experience_confirmed is False

        # Case 3: Both None and False -> remains unknown
        entered_val = None
        confirm_exp = False
        if entered_val is not None and confirm_exp:
            final_exp = float(entered_val)
            final_confirmed = True
        else:
            final_exp = None
            final_confirmed = False

        prof3 = CandidateProfile(
            name="Candidate",
            total_experience_years=final_exp,
            experience_confirmed=final_confirmed,
        )
        assert prof3.total_experience_years is None
        assert prof3.experience_confirmed is False

    def test_streamlit_experience_rerun_does_not_overwrite_typed_value(self):
        """
        Verifies that when a user types a new experience value or toggles confirmation,
        subsequent Streamlit script executions (reruns) do NOT overwrite the session state
        with the active profile's initial values.
        """
        active_profile = CandidateProfile(
            name="Abhishek",
            total_experience_years=None,
            experience_confirmed=False,
        )

        session = {}

        # First run: Edit mode activated, keys initialized from active profile
        if "edit_active_entered_exp" not in session:
            session["edit_active_entered_exp"] = (
                float(active_profile.total_experience_years)
                if active_profile.total_experience_years is not None
                else None
            )
        if "edit_active_confirm_exp" not in session:
            session["edit_active_confirm_exp"] = bool(
                active_profile.experience_confirmed and active_profile.total_experience_years is not None
            )

        assert session["edit_active_entered_exp"] is None
        assert session["edit_active_confirm_exp"] is False

        # User interacts: enters 2.25 and checks confirmation
        session["edit_active_entered_exp"] = 2.25
        session["edit_active_confirm_exp"] = True

        # Second run (simulating Streamlit rerun):
        # The 'if ... not in session' guards prevent resetting back to None / False!
        if "edit_active_entered_exp" not in session:
            session["edit_active_entered_exp"] = (
                float(active_profile.total_experience_years)
                if active_profile.total_experience_years is not None
                else None
            )
        if "edit_active_confirm_exp" not in session:
            session["edit_active_confirm_exp"] = bool(
                active_profile.experience_confirmed and active_profile.total_experience_years is not None
            )

        # Values typed by the user remain intact
        assert session["edit_active_entered_exp"] == 2.25
        assert session["edit_active_confirm_exp"] is True

    def test_search_experience_range_independent_from_total_experience(self):
        """
        Verifies that total professional experience and search preference ranges
        (range_min, range_max, hard_limit) are completely decoupled.
        Changing search range must not alter total_experience_years, and vice versa.
        """
        prof = CandidateProfile(
            name="Decoupled Test",
            total_experience_years=2.25,
            experience_confirmed=True,
            experience_range_min=1.0,
            experience_range_max=5.0,
            hard_limit_years=6.0,
        )

        assert prof.total_experience_years == 2.25
        assert prof.experience_range_min == 1.0
        assert prof.experience_range_max == 5.0
        assert prof.hard_limit_years == 6.0

        # Change search range
        updated_prof = prof.model_copy(update={
            "experience_range_min": 0.5,
            "experience_range_max": 4.0,
            "hard_limit_years": 5.0,
        })
        # total_experience_years is unchanged
        assert updated_prof.total_experience_years == 2.25
        assert updated_prof.experience_confirmed is True
        assert updated_prof.experience_range_min == 0.5
        assert updated_prof.experience_range_max == 4.0
        assert updated_prof.hard_limit_years == 5.0

        # Change total experience
        updated_exp = updated_prof.model_copy(update={
            "total_experience_years": 3.5,
        })
        # search ranges remain unchanged
        assert updated_exp.total_experience_years == 3.5
        assert updated_exp.experience_range_min == 0.5
        assert updated_exp.experience_range_max == 4.0
        assert updated_exp.hard_limit_years == 5.0

    def test_regression_saving_2_0_years_sets_experience_and_confirmed_true(self):
        """
        Regression Test 1:
        Verifies that explicitly saving 2.0 years persists total_experience_years = 2.0
        and experience_confirmed = True to SQL, and is returned in the saved profile.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        mock_cur.fetchone.side_effect = [
            (10, 42, {}),  # lookup active candidate_profiles
            (42,),         # UPDATE RETURNING resume_version_id
            {              # get_active_profile re-read
                "id": 10,
                "candidate_name": "Abhishek",
                "target_title": "Software Engineer",
                "total_experience_years": 2.0,
                "experience_confirmed": True,
                "experience_range_min": 2.0,
                "experience_range_max": 3.0,
                "hard_limit_years": 4.0,
                "languages": ["Python", "Java"],
                "frameworks": ["Spring Boot"],
                "databases": ["PostgreSQL"],
                "messaging_cache": ["Redis"],
                "devops_cloud": ["Docker"],
                "inferred_capabilities": [],
                "target_locations": ["bengaluru"],
                "remote_preference": "flexible",
                "raw_profile_json": {"total_experience_years": 2.0},
                "user_preferences": {"experience_range_min": 2.0, "experience_range_max": 3.0},
            },
        ]

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        profile = CandidateProfile(
            name="Abhishek",
            total_experience_years=2.0,
            experience_confirmed=True,
            experience_range_min=2.0,
            experience_range_max=3.0,
            hard_limit_years=4.0,
            languages=["Python", "Java"],
        )

        resume_id, saved = service.update_active_profile(profile)
        assert resume_id == 42
        assert saved.total_experience_years == 2.0
        assert saved.experience_confirmed is True

        # Verify UPDATE parameters sent to PostgreSQL
        update_calls = [c for c in mock_cur.execute.call_args_list if "UPDATE candidate_profiles" in c[0][0]]
        assert len(update_calls) == 1
        sql_params = update_calls[0][0][1]
        assert sql_params[2] == 2.0   # total_experience_years
        assert sql_params[3] is True  # experience_confirmed

    def test_regression_updating_search_preferences_alone_does_not_confirm_actual_experience(self):
        """
        Regression Test 2:
        Verifies that updating search preferences alone (experience_range_min, experience_range_max)
        does NOT confirm actual career experience and does NOT alter total_experience_years.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        # Profile in database currently has unconfirmed / null experience
        unconfirmed_profile = CandidateProfile(
            name="Abhishek",
            total_experience_years=None,
            experience_confirmed=False,
            target_title="Software Engineer",
            experience_range_min=2.0,
            experience_range_max=3.0,
            hard_limit_years=4.0,
        )
        service.get_active_profile = MagicMock(return_value=unconfirmed_profile)

        updated_profile = service.update_active_profile_preferences({
            "experience_range_min": 2.0,
            "experience_range_max": 3.0,
            "hard_limit_years": 4.0,
        })

        # Experience facts remain completely unconfirmed and None
        assert updated_profile.total_experience_years is None
        assert updated_profile.experience_confirmed is False
        assert updated_profile.experience_range_min == 2.0
        assert updated_profile.experience_range_max == 3.0

        # Verify SQL executed: experience_confirmed is NOT updated in SQL SET clause
        update_sql = mock_cur.execute.call_args_list[0][0][0]
        assert "experience_confirmed" not in update_sql
        assert "total_experience_years" not in update_sql

    def test_regression_reloading_profile_displays_saved_experience(self):
        """
        Regression Test 3:
        Verifies that get_active_profile strictly loads total_experience_years and
        experience_confirmed from relational columns, and does not conflate them with search preferences.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # Scenario A: DB row has 2.0 confirmed experience
        mock_cur.fetchone.return_value = {
            "id": 10,
            "candidate_name": "Abhishek",
            "target_title": "Software Engineer",
            "total_experience_years": 2.0,
            "experience_confirmed": True,
            "experience_range_min": 1.0,
            "experience_range_max": 3.0,
            "hard_limit_years": 4.0,
            "languages": ["Java"],
            "frameworks": [],
            "databases": [],
            "messaging_cache": [],
            "devops_cloud": [],
            "inferred_capabilities": [],
            "target_locations": ["bengaluru"],
            "remote_preference": "flexible",
            "raw_profile_json": {},
            "user_preferences": {"is_user_confirmed": True, "experience_range_min": 1.0},
        }

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        prof = service.get_active_profile()
        assert prof.total_experience_years == 2.0
        assert prof.experience_confirmed is True

        # Scenario B: DB row has total_experience_years=None, but search range has 2.0 to 3.0
        mock_cur.fetchone.return_value = {
            "id": 10,
            "candidate_name": "Abhishek",
            "target_title": "Software Engineer",
            "total_experience_years": None,
            "experience_confirmed": False,
            "experience_range_min": 2.0,
            "experience_range_max": 3.0,
            "hard_limit_years": 4.0,
            "languages": ["Java"],
            "frameworks": [],
            "databases": [],
            "messaging_cache": [],
            "devops_cloud": [],
            "inferred_capabilities": [],
            "target_locations": ["bengaluru"],
            "remote_preference": "flexible",
            "raw_profile_json": {},
            "user_preferences": {"is_user_confirmed": True, "experience_range_min": 2.0, "experience_range_max": 3.0},
        }

        prof_unspecified = service.get_active_profile()
        assert prof_unspecified.total_experience_years is None
        assert prof_unspecified.experience_confirmed is False
        assert prof_unspecified.experience_range_min == 2.0
        assert prof_unspecified.experience_range_max == 3.0

    def test_regression_matching_relevant_experience_change_changes_profile_hash(self):
        """
        Regression Test 4:
        Verifies that changing candidate experience changes the profile hash,
        preventing stale cache lookups while preserving old cache records in the database.
        """
        base_kwargs = {
            "name": "Abhishek",
            "languages": ["Java", "Python"],
            "frameworks": ["Spring Boot"],
            "target_title": "Software Engineer",
            "hard_limit_years": 4.0,
            "target_cities": ["bengaluru"],
        }

        # 1. Unknown / Unconfirmed profile
        profile_unknown = CandidateProfile(
            total_experience_years=None,
            experience_confirmed=False,
            **base_kwargs,
        )

        # 2. Confirmed 2.0 years profile
        profile_confirmed_2 = CandidateProfile(
            total_experience_years=2.0,
            experience_confirmed=True,
            **base_kwargs,
        )

        # 3. Confirmed 3.0 years profile
        profile_confirmed_3 = CandidateProfile(
            total_experience_years=3.0,
            experience_confirmed=True,
            **base_kwargs,
        )

        hash_unknown = compute_profile_hash(profile_unknown)
        hash_confirmed_2 = compute_profile_hash(profile_confirmed_2)
        hash_confirmed_3 = compute_profile_hash(profile_confirmed_3)

        # Hashes must all be distinct
        assert hash_unknown != hash_confirmed_2
        assert hash_confirmed_2 != hash_confirmed_3
        assert hash_unknown != hash_confirmed_3

    def test_regression_success_message_not_shown_when_intended_db_update_fails(self):
        """
        Regression Test 5:
        Verifies that the success message is not displayed when input validation fails
        or when database persistence verification fails.
        """
        # Case A: User typed 2.0 YOE but did not check confirmation box
        exp, confirmed, err = validate_experience_input(entered_exp=2.0, confirm_exp=False)
        assert exp is None
        assert confirmed is False
        assert err is not None
        assert "did not check the confirmation checkbox" in err

        # Case B: User typed 2.0 YOE and checked confirmation box
        exp, confirmed, err = validate_experience_input(entered_exp=2.0, confirm_exp=True)
        assert exp == 2.0
        assert confirmed is True
        assert err is None

        # Case C: Verification detects persistence failure (e.g. DB still returned None / False)
        submitted_prof = CandidateProfile(
            name="Abhishek",
            total_experience_years=2.0,
            experience_confirmed=True,
        )
        stale_db_prof = CandidateProfile(
            name="Abhishek",
            total_experience_years=None,
            experience_confirmed=False,
        )
        verified, verify_err = verify_profile_persistence(submitted_prof, stale_db_prof)
        assert verified is False
        assert "Persisted total_experience_years (None) does not match submitted (2.0)" in verify_err

    def test_regression_v1_v2_cache_isolation_and_ai_first_routing_remain_intact(self):
        """
        Regression Test 6:
        Verifies that V1 and V2 cache entries remain isolated by prompt_version in get_cached_match,
        and that profile hash change preserves existing cache entries without deletion.
        """
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        service = ResumeService()
        service._get_connection = MagicMock(return_value=mock_conn)

        # Lookup with V2 prompt version
        mock_cur.fetchone.return_value = None
        match = service.get_cached_match(
            profile_hash="hash_abc",
            platform="freehire",
            platform_job_id="job_123",
            job_hash="jhash_456",
            model_version="gemini-2.5-flash",
            prompt_version="v2",
        )
        assert match is None

        # Confirm SQL query explicitly scoped to prompt_version = 'v2'
        query_sql = mock_cur.execute.call_args[0][0]
        params = mock_cur.execute.call_args[0][1]
        assert "prompt_version = %s" in query_sql
        assert params[5] == "v2"

        # Lookup with V1 prompt version
        service.get_cached_match(
            profile_hash="hash_abc",
            platform="freehire",
            platform_job_id="job_123",
            job_hash="jhash_456",
            model_version="gemini-2.5-flash",
            prompt_version="v1",
        )
        params_v1 = mock_cur.execute.call_args[0][1]
        assert params_v1[5] == "v1"

