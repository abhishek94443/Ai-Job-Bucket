import json
from typing import Optional, Tuple
import pytest
from unittest.mock import MagicMock, patch
import requests

from src.ai_service import (
    GeminiAIService,
    AIServiceError,
    AIConfigurationError,
    AIQuotaExceededError,
    AIDailyQuotaExhaustedError,
    AIDatabaseConnectivityError,
    compute_profile_hash,
)
from src.candidate_profile import (
    CandidateProfile,
    DEFAULT_CANDIDATE_PROFILE,
)
from src.resume_analysis_schema import (
    ResumeAnalysisResult,
    WorkExperienceItem,
    ExperienceCalculation,
    TechnicalSkillsBreakdown,
    analysis_result_to_candidate_profile,
)
from src.resume_parser import ResumeParseResult
from src.resume_service import ResumeService


def validate_experience_input(
    entered_exp: Optional[float],
    confirm_exp: bool,
) -> tuple[Optional[float], bool, Optional[str]]:
    if entered_exp is not None and float(entered_exp) > 0.0 and not confirm_exp:
        return (
            None,
            False,
            f"You entered {float(entered_exp):g} years for actual professional experience, but did not check the confirmation checkbox ('✅ I explicitly confirm this actual experience figure is accurate'). Please check the confirmation box to verify your experience, or clear the field if unknown.",
        )
    if entered_exp is not None and confirm_exp:
        return (float(entered_exp), True, None)
    return (None, False, None)


def verify_profile_persistence(
    submitted: CandidateProfile,
    persisted: CandidateProfile,
) -> tuple[bool, Optional[str]]:
    if submitted.total_experience_years is not None:
        if persisted.total_experience_years != submitted.total_experience_years:
            return (
                False,
                f"Persisted total_experience_years ({persisted.total_experience_years}) does not match submitted ({submitted.total_experience_years}).",
            )
        if persisted.experience_confirmed != submitted.experience_confirmed:
            return (
                False,
                f"Persisted experience_confirmed ({persisted.experience_confirmed}) does not match submitted ({submitted.experience_confirmed}).",
            )
    else:
        if persisted.total_experience_years is not None:
            return (
                False,
                f"Expected experience to be None, but persisted as {persisted.total_experience_years}.",
            )
        if persisted.experience_confirmed is not False:
            return (
                False,
                f"Expected experience_confirmed to be False for unspecified experience, but persisted as {persisted.experience_confirmed}.",
            )
    return (True, None)


# =====================================================================
# Fixtures & Mock Helpers
# =====================================================================

def make_valid_analysis_dict(
    candidate_name: str = "Jane Doe",
    exp_years: float = 3.5,
    is_ambiguous: bool = False,
    gaps: list = None,
) -> dict:
    return {
        "schema_version": "v2",
        "provenance": "AI_LIVE",
        "candidate_name": candidate_name,
        "professional_summary": "Experienced Cloud and Backend Engineer with focus on distributed systems.",
        "work_history": [
            {
                "employer": "TechCorp Inc.",
                "job_title": "Senior Backend Engineer",
                "start_date": "2023-01",
                "end_date": "2026-06",
                "duration_years": 3.5,
                "responsibilities": ["Designed event-driven services", "Optimized PostgreSQL indexes"],
                "technologies_used": ["Python", "FastAPI", "PostgreSQL", "Kafka"],
                "evidence": "Led microservices migration at TechCorp Inc.",
            }
        ],
        "experience_calculation": {
            "total_experience_years": exp_years,
            "calculation_method": "Calculated from non-overlapping employment periods (Jan 2023 - Jun 2026)",
            "assumptions": ["Assumed full-time employment for Senior role"],
            "is_ambiguous": is_ambiguous,
            "conflicts_or_gaps": gaps or [],
        },
        "skills": {
            "languages": ["Python", "SQL"],
            "frameworks": ["FastAPI", "Django"],
            "databases": ["PostgreSQL", "Redis"],
            "cloud_and_devops": ["Docker", "Kubernetes", "AWS"],
            "messaging_and_cache": ["Kafka"],
            "apis_and_architecture": ["REST", "Microservices"],
            "testing_and_tools": ["pytest", "Git"],
            "other_technologies": ["Linux"],
        },
        "demonstrated_capabilities": ["Distributed Systems Design", "Database Query Optimization"],
        "projects": [
            {
                "title": "Data Pipeline",
                "description": "High-throughput streaming ingestion pipeline",
                "technologies_used": ["Python", "Kafka"],
                "demonstrated_capabilities": ["Stream processing"],
            }
        ],
        "education": [
            {
                "degree": "B.S. in Computer Science",
                "institution": "State University",
                "graduation_year": "2022",
                "field_of_study": "Computer Science",
            }
        ],
        "certifications": [
            {
                "name": "AWS Certified Solutions Architect",
                "issuing_organization": "Amazon Web Services",
                "year": "2024",
            }
        ],
        "suggested_role_titles": ["Senior Backend Engineer", "Cloud Infrastructure Engineer"],
        "suggested_role_families": ["Backend Engineering", "Platform Engineering"],
        "uncertainties": [] if not is_ambiguous else ["Unclear end date for earlier internship"],
    }


# =====================================================================
# 1. Structured AI Responses (Valid & Malformed)
# =====================================================================

class TestStructuredAIResponses:
    def test_valid_structured_ai_response_validation(self):
        """Proves valid structured AI response validates into versioned Pydantic model."""
        payload = make_valid_analysis_dict()
        result = ResumeAnalysisResult.model_validate(payload)
        assert result.candidate_name == "Jane Doe"
        assert result.experience_calculation.total_experience_years == 3.5
        assert result.skills.languages == ["Python", "SQL"]
        assert "FastAPI" in result.skills.frameworks
        assert result.provenance == "AI_LIVE"
        assert "Senior Backend Engineer" in result.suggested_role_titles

    def test_malformed_ai_response_raises_service_error(self):
        """Proves schema-violating payload raises AIServiceError and never silently passes invalid data."""
        service = GeminiAIService(api_key="test-key", require_persistent_quota=False)
        # Type violation: skills is string instead of dictionary structure
        malformed_json = {"skills": "not_a_dict_of_skills", "work_history": "not_a_list"}
        with patch.object(service, "generate_json", return_value=malformed_json):
            with pytest.raises(AIServiceError) as exc:
                service.analyze_resume_semantic("Some resume text")
            assert "AI response schema validation failed" in str(exc.value)

    @patch("src.ai_service.requests.post")
    def test_pii_scrubbing_before_semantic_analysis(self, mock_post):
        """Verifies candidate email and phone numbers are scrubbed before reaching the prompt."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": json.dumps(make_valid_analysis_dict())}]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        service = GeminiAIService(api_key="test-key", require_persistent_quota=False)
        raw_resume = (
            "Alice Candidate\nEmail: alice.private@example.com\nPhone: +1-555-123-4567\n"
            "Backend Engineer with Python and AWS experience."
        )

        service.analyze_resume_semantic(raw_resume, candidate_name="Alice Candidate")

        # Verify sent payload contains no raw email or phone
        call_args = mock_post.call_args
        body_text = call_args[1]["json"]["contents"][0]["parts"][0]["text"]
        assert "alice.private@example.com" not in body_text
        assert "555-123-4567" not in body_text
        assert "[REDACTED_EMAIL]" in body_text
        assert "[REDACTED_PHONE]" in body_text


# =====================================================================
# 2. Ambiguous Dates & Experience Calculations
# =====================================================================

class TestAmbiguousDatesAndExperience:
    def test_ambiguous_dates_flagged_with_uncertainties(self):
        """Proves ambiguous timelines preserve is_ambiguous=True and capture gaps/conflicts."""
        payload = make_valid_analysis_dict(
            exp_years=2.0,
            is_ambiguous=True,
            gaps=["Employment gap between 2023 and 2024", "Overlap between full-time role and consulting"],
        )
        result = ResumeAnalysisResult.model_validate(payload)
        assert result.experience_calculation.is_ambiguous is True
        assert len(result.experience_calculation.conflicts_or_gaps) == 2
        assert "Employment gap" in result.experience_calculation.conflicts_or_gaps[0]

    def test_unstated_experience_leaves_years_none(self):
        """Proves missing experience in resume leaves total_experience_years as None and does not invent years."""
        payload = make_valid_analysis_dict(exp_years=None, is_ambiguous=True)
        payload["experience_calculation"]["total_experience_years"] = None
        result = ResumeAnalysisResult.model_validate(payload)
        assert result.experience_calculation.total_experience_years is None

        profile = analysis_result_to_candidate_profile(result)
        assert profile.total_experience_years is None
        assert profile.experience_confirmed is False


# =====================================================================
# 3. AI-First Execution & Strict Fallback Policy
# =====================================================================

class TestAIFirstExecutionAndFallback:
    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_ai_first_is_default_path(self, mock_parse):
        """Proves AI semantic analysis is the primary/default execution path."""
        mock_parse.return_value = ResumeParseResult(
            file_name="resume.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            content_hash="hash_123",
            file_bytes=b"resume_bytes",
            extracted_text="Python FastAPI engineer with 3.5 years experience.",
            char_count=50,
            word_count=8,
        )
        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.is_configured = True
        mock_analysis = ResumeAnalysisResult.model_validate(make_valid_analysis_dict())
        mock_ai.analyze_resume_semantic.return_value = mock_analysis

        service = ResumeService(ai_service=mock_ai)
        service._get_connection = MagicMock()
        service.has_active_profile = MagicMock(return_value=False)

        parsed, profile = service.parse_and_extract_for_review(
            file_bytes=b"resume_bytes",
            file_name="resume.pdf",
            # use_ai defaults to True!
        )

        # AI semantic analysis was called
        mock_ai.analyze_resume_semantic.assert_called_once()
        assert profile.analysis_provenance == "AI_LIVE"
        assert profile.total_experience_years == 3.5
        assert profile.experience_confirmed is False

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_automatic_fallback_strictly_on_quota_exhausted(self, mock_parse):
        """Proves automatic deterministic fallback occurs ONLY on AIDailyQuotaExhaustedError."""
        mock_parse.return_value = ResumeParseResult(
            file_name="resume.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            content_hash="hash_123",
            file_bytes=b"resume_bytes",
            extracted_text="Python Django developer with 2.5 years of experience in AWS.",
            char_count=60,
            word_count=10,
        )
        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.is_configured = True
        mock_ai.analyze_resume_semantic.side_effect = AIDailyQuotaExhaustedError("Daily quota exhausted: 50/50")

        service = ResumeService(ai_service=mock_ai)
        service._get_connection = MagicMock()
        service.has_active_profile = MagicMock(return_value=False)

        parsed, profile = service.parse_and_extract_for_review(
            file_bytes=b"resume_bytes",
            file_name="resume.pdf",
        )

        # Automatically fell back to deterministic parser and set explicit provenance
        assert profile.analysis_provenance == "DETERMINISTIC_FALLBACK"
        assert "Python" in profile.languages
        assert "Django" in profile.frameworks
        assert profile.experience_confirmed is False

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_no_fallback_on_missing_api_key(self, mock_parse):
        """Proves missing API key raises AIConfigurationError and does NOT silently trigger fallback."""
        mock_parse.return_value = ResumeParseResult(
            file_name="resume.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            content_hash="hash_123",
            file_bytes=b"resume_bytes",
            extracted_text="Python engineer",
            char_count=15,
            word_count=2,
        )
        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.is_configured = False  # Missing key!

        service = ResumeService(ai_service=mock_ai)

        with pytest.raises(AIConfigurationError) as exc:
            service.parse_and_extract_for_review(
                file_bytes=b"resume_bytes",
                file_name="resume.pdf",
            )
        assert "GEMINI_API_KEY is not configured" in str(exc.value)

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_no_fallback_on_timeout(self, mock_parse):
        """Proves HTTP timeout raises and does NOT silently trigger fallback."""
        mock_parse.return_value = ResumeParseResult(
            file_name="resume.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            content_hash="hash_123",
            file_bytes=b"resume_bytes",
            extracted_text="Python engineer",
            char_count=15,
            word_count=2,
        )
        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.is_configured = True
        mock_ai.analyze_resume_semantic.side_effect = requests.exceptions.Timeout("Read timeout")

        service = ResumeService(ai_service=mock_ai)
        service._get_connection = MagicMock()

        with pytest.raises(requests.exceptions.Timeout):
            service.parse_and_extract_for_review(
                file_bytes=b"resume_bytes",
                file_name="resume.pdf",
            )

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_no_fallback_on_provider_or_db_error(self, mock_parse):
        """Proves provider 500 error or DB error raises AIServiceError/AIDatabaseConnectivityError without fallback."""
        mock_parse.return_value = ResumeParseResult(
            file_name="resume.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            content_hash="hash_123",
            file_bytes=b"resume_bytes",
            extracted_text="Python engineer",
            char_count=15,
            word_count=2,
        )
        mock_ai = MagicMock(spec=GeminiAIService)
        mock_ai.is_configured = True
        mock_ai.analyze_resume_semantic.side_effect = AIDatabaseConnectivityError("PostgreSQL unreachable")

        service = ResumeService(ai_service=mock_ai)
        service._get_connection = MagicMock()

        with pytest.raises(AIDatabaseConnectivityError):
            service.parse_and_extract_for_review(
                file_bytes=b"resume_bytes",
                file_name="resume.pdf",
            )

    @patch("src.resume_service.ResumeParser.parse_bytes")
    def test_explicit_offline_mode_provenance(self, mock_parse):
        """Proves explicit user offline selection runs deterministic extraction with correct provenance."""
        mock_parse.return_value = ResumeParseResult(
            file_name="resume.pdf",
            file_type="pdf",
            file_size_bytes=1000,
            content_hash="hash_123",
            file_bytes=b"resume_bytes",
            extracted_text="Go Kubernetes developer with 4 years experience.",
            char_count=50,
            word_count=7,
        )
        service = ResumeService()
        service.has_active_profile = MagicMock(return_value=False)

        parsed, profile = service.parse_and_extract_for_review(
            file_bytes=b"resume_bytes",
            file_name="resume.pdf",
            explicit_fallback=True,
        )

        assert profile.analysis_provenance == "DETERMINISTIC_FALLBACK"
        assert "Go" in profile.languages
        assert "Kubernetes" in profile.devops_and_tools
        assert profile.experience_confirmed is False


# =====================================================================
# 4. Separation of Facts vs Preferences & Confirmation
# =====================================================================

class TestSeparationOfFactsAndPreferences:
    def test_search_preferences_not_overwritten_by_ai_suggestions(self):
        """Proves AI-suggested titles and role families do NOT overwrite candidate's configured search preferences."""
        analysis = ResumeAnalysisResult.model_validate(make_valid_analysis_dict())
        # AI suggested "Senior Backend Engineer"
        assert "Senior Backend Engineer" in analysis.suggested_role_titles

        # User previously configured a custom target role and cities
        existing_profile = CandidateProfile(
            name="Alice",
            target_title="Staff Platform Architect",
            experience_range_min=4.0,
            experience_range_max=7.0,
            hard_limit_years=8.0,
            target_cities=["delhi", "noida"],
            remote_preference="remote_only",
            preferences_source="user_configured",
        )

        converted_profile = analysis_result_to_candidate_profile(
            analysis,
            candidate_name="Alice",
            existing_profile=existing_profile,
        )

        # Career facts extracted from resume
        assert "FastAPI" in converted_profile.frameworks
        assert "Python" in converted_profile.languages

        # Search preferences strictly preserved from existing user-configured profile!
        assert converted_profile.target_title == "Staff Platform Architect"
        assert converted_profile.experience_range_min == 4.0
        assert converted_profile.experience_range_max == 7.0
        assert converted_profile.hard_limit_years == 8.0
        assert converted_profile.target_cities == ["delhi", "noida"]
        assert converted_profile.remote_preference == "remote_only"
        assert converted_profile.preferences_source == "user_configured"

    def test_experience_requires_explicit_confirmation(self):
        """Proves experience validation requires explicit checkbox confirmation and returns error if unconfirmed."""
        # User entered 3.5 years but unchecked confirmation box
        val, confirmed, err = validate_experience_input(3.5, False)
        assert confirmed is False
        assert val is None
        assert err is not None
        assert "did not check the confirmation checkbox" in err

        # User entered 3.5 years and checked confirmation box
        val, confirmed, err = validate_experience_input(3.5, True)
        assert confirmed is True
        assert val == 3.5
        assert err is None

        # User specified unknown (None)
        val, confirmed, err = validate_experience_input(None, False)
        assert confirmed is False
        assert val is None
        assert err is None


# =====================================================================
# 5. Transactional Activation & Profile Hash
# =====================================================================

class TestTransactionalActivationAndProfileHash:
    def test_profile_hash_changes_on_content_change(self):
        """Proves compute_profile_hash produces different hashes when experience or skills change."""
        p1 = CandidateProfile(
            name="Bob",
            total_experience_years=2.0,
            languages=["Python"],
            frameworks=["Django"],
        )
        p2 = CandidateProfile(
            name="Bob",
            total_experience_years=3.0,  # Changed experience
            languages=["Python"],
            frameworks=["Django"],
        )
        p3 = CandidateProfile(
            name="Bob",
            total_experience_years=2.0,
            languages=["Python", "Go"],  # Changed skills
            frameworks=["Django"],
        )

        h1 = compute_profile_hash(p1)
        h2 = compute_profile_hash(p2)
        h3 = compute_profile_hash(p3)

        assert h1 != h2
        assert h1 != h3
        assert h2 != h3

    def test_transactional_activation_rolls_back_on_failure(self):
        """Proves database rollback occurs on activation failure, leaving previous profile untouched."""
        service = ResumeService()
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        service._get_connection = MagicMock(return_value=mock_conn)

        # Mock failure during resume_versions insert
        mock_cur.execute.side_effect = Exception("DB Disk Full")

        parsed = ResumeParseResult(
            file_name="res.pdf",
            file_type="pdf",
            file_size_bytes=100,
            content_hash="h1",
            file_bytes=b"bytes",
            extracted_text="text",
            char_count=4,
            word_count=1,
        )
        profile = CandidateProfile(name="Bob")

        with pytest.raises(Exception) as exc:
            service.activate_reviewed_profile(parsed, profile)
        assert "DB Disk Full" in str(exc.value)

        # Transaction rollback was explicitly invoked
        mock_conn.rollback.assert_called_once()


# =====================================================================
# 6. Profile Persistence Verification & Duplicate Protection
# =====================================================================

class TestPersistenceVerificationAndUI:
    def test_persistence_verification_detects_mismatch(self):
        """Proves verify_profile_persistence detects accidental mismatches in experience or confirmation."""
        submitted = CandidateProfile(
            name="Alice",
            total_experience_years=2.5,
            experience_confirmed=True,
        )
        persisted_bad = CandidateProfile(
            name="Alice",
            total_experience_years=None,  # Accidentally lost
            experience_confirmed=False,
        )
        ok, reason = verify_profile_persistence(submitted, persisted_bad)
        assert ok is False
        assert "does not match submitted" in reason

        persisted_good = CandidateProfile(
            name="Alice",
            total_experience_years=2.5,
            experience_confirmed=True,
        )
        ok, reason = verify_profile_persistence(submitted, persisted_good)
        assert ok is True
        assert reason is None

    def test_get_active_resume_content_retrieves_stored_bytes(self):
        """Proves get_active_resume_content retrieves stored binary file bytes for re-analysis."""
        service = ResumeService()
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        service._get_connection = MagicMock(return_value=mock_conn)

        mock_cur.fetchone.return_value = {
            "id": 1,
            "file_name": "resume.pdf",
            "file_type": "pdf",
            "file_size_bytes": 1024,
            "content_hash": "hash_abc",
            "file_content": b"%PDF-mock-bytes",
            "extracted_text": "Python engineer text",
            "extraction_status": "EXTRACTED",
        }

        res = service.get_active_resume_content()
        assert res is not None
        assert res["id"] == 1
        assert res["file_name"] == "resume.pdf"
        assert res["file_bytes"] == b"%PDF-mock-bytes"
        assert res["extracted_text"] == "Python engineer text"
