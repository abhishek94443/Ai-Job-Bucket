import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Dict, Any, List

from src.models import (
    JobRecord,
    ValidationEvidence,
    JobStatus,
    ApplicationType,
    FreshnessStatus,
)
from src.candidate_profile import DEFAULT_CANDIDATE_PROFILE
from src.discovery import JobDiscoveryService
from src.pipeline import run_pipeline


class TestPaginatedDiscovery:
    # ------------------------------------------------------------------
    # 1. Freehire Geography Argument Construction
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_freehire_city_provided_omits_country(self):
        """When cities is provided, payload must contain cities and MUST omit countries."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"data": []}

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        await discovery.search_freehire(
            query="Java",
            country_code="in",
            cities="Bengaluru",
            limit=5,
            max_pages=1,
        )

        mock_mcp.call_tool.assert_called_once()
        payload = mock_mcp.call_tool.call_args[0][1]
        assert payload["cities"] == "Bengaluru"
        assert "countries" not in payload

    @pytest.mark.asyncio
    async def test_freehire_city_omitted_preserves_country(self):
        """When cities is None or whitespace, payload must include countries and omit cities."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"data": []}

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        # Case A: cities is None
        await discovery.search_freehire(
            query="Java",
            country_code="in",
            cities=None,
            limit=5,
            max_pages=1,
        )
        payload = mock_mcp.call_tool.call_args[0][1]
        assert payload["countries"] == "in"
        assert "cities" not in payload

        # Case B: cities is empty string or spaces
        mock_mcp.reset_mock()
        await discovery.search_freehire(
            query="Java",
            country_code="in",
            cities="   ",
            limit=5,
            max_pages=1,
        )
        payload = mock_mcp.call_tool.call_args[0][1]
        assert payload["countries"] == "in"
        assert "cities" not in payload

    # ------------------------------------------------------------------
    # 2. Structured Experience and Requirements Mapping
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_structured_experience_and_requirements_mapping_present(self):
        """Maps enrichment.requirements and enrichment.experience_years_min without inventing values."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "data": [
                {
                    "public_slug": "lead-java-dev-1",
                    "title": "Lead Java Developer",
                    "company": "Capco",
                    "url": "https://capco.com/jobs/1",
                    "enrichment": {
                        "experience_years_min": 7,
                        "requirements": [
                            {"priority": "required", "text": "7+ years of backend Java experience"},
                            {"priority": "preferred", "text": "Kafka streaming knowledge"},
                        ],
                    },
                }
            ]
        }

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        jobs = await discovery.search_freehire(query="Java", limit=5, max_pages=1)

        assert len(jobs) == 1
        job = jobs[0]
        assert job.source_experience_years_min == 7
        assert job.structured_requirements == [
            {"priority": "required", "text": "7+ years of backend Java experience"},
            {"priority": "preferred", "text": "Kafka streaming knowledge"},
        ]

    @pytest.mark.asyncio
    async def test_structured_experience_and_requirements_mapping_absent(self):
        """Preserves absent or null requirements and experience as None."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "data": [
                {
                    "public_slug": "java-dev-no-enrichment",
                    "title": "Java Developer",
                    "company": "Generic Corp",
                    "url": "https://generic.com/jobs/1",
                    # No enrichment field
                },
                {
                    "public_slug": "java-dev-empty-enrichment",
                    "title": "Java Developer",
                    "company": "Empty Corp",
                    "url": "https://empty.com/jobs/2",
                    "enrichment": {
                        "experience_years_min": None,
                        "requirements": [],
                    },
                },
            ]
        }

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        jobs = await discovery.search_freehire(query="Java", limit=5, max_pages=1)

        assert len(jobs) == 2
        assert jobs[0].source_experience_years_min is None
        assert jobs[0].structured_requirements is None
        assert jobs[1].source_experience_years_min is None
        assert jobs[1].structured_requirements is None

    # ------------------------------------------------------------------
    # 3. Freehire Offset Advancement
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_freehire_offset_advancement_across_pages(self):
        """Verifies Freehire increments offset by page size across requests."""
        mock_mcp = AsyncMock()

        def make_items(page_num, count=10):
            return [
                {
                    "public_slug": f"freehire-job-p{page_num}-{i}",
                    "title": f"Java Dev {page_num}-{i}",
                    "company": "Company",
                    "url": f"https://company.com/job/p{page_num}/{i}",
                }
                for i in range(count)
            ]

        # 3 pages: 10 items, 10 items, 5 items (final page)
        mock_mcp.call_tool.side_effect = [
            {"data": make_items(1, 10)},
            {"data": make_items(2, 10)},
            {"data": make_items(3, 5)},
        ]

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        jobs = await discovery.search_freehire(
            query="Java",
            limit=10,
            max_pages=5,
            offset=0,
        )

        assert len(jobs) == 25
        assert mock_mcp.call_tool.call_count == 3

        calls = mock_mcp.call_tool.call_args_list
        assert calls[0][0][1]["offset"] == 0
        assert calls[1][0][1]["offset"] == 10
        assert calls[2][0][1]["offset"] == 20

    # ------------------------------------------------------------------
    # 4. LinkedIn Start Advancement
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_linkedin_start_advancement_across_pages(self):
        """Verifies LinkedIn increments start offset by 10 each page."""
        mock_mcp = AsyncMock()

        def make_li_items(page_num, count=10):
            return [
                {
                    "id": f"li-{page_num}-{i}",
                    "title": f"Java Dev {page_num}-{i}",
                    "company": "Company",
                    "url": f"https://linkedin.com/jobs/view/li-{page_num}-{i}",
                }
                for i in range(count)
            ]

        # 2 full pages of 10, then a final partial page of 4 items
        mock_mcp.call_tool.side_effect = [
            {"data": make_li_items(1, 10)},
            {"data": make_li_items(2, 10)},
            {"data": make_li_items(3, 4)},
        ]

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        jobs = await discovery.search_linkedin(
            keyword="Java",
            location="Bengaluru",
            max_pages=5,
            limit=10,
            fetch_details=False,
        )

        assert len(jobs) == 24
        assert mock_mcp.call_tool.call_count == 3

        calls = mock_mcp.call_tool.call_args_list
        assert calls[0][0][1]["start"] == 0
        assert calls[1][0][1]["start"] == 10
        assert calls[2][0][1]["start"] == 20

    # ------------------------------------------------------------------
    # 5. Indeed Cursor Forwarding and Termination
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_indeed_cursor_forwarding_and_termination(self):
        """Verifies Indeed passes cursor from next_cursor and terminates when next_cursor is absent."""
        mock_mcp = AsyncMock()

        def make_ind_items(prefix, count=5):
            return [
                {
                    "key": f"{prefix}_{i}",
                    "title": f"Java Dev {prefix}_{i}",
                    "company": "Company",
                    "url": f"https://in.indeed.com/viewjob?jk={prefix}_{i}",
                }
                for i in range(count)
            ]

        mock_mcp.call_tool.side_effect = [
            {"data": make_ind_items("page1", 5), "next_cursor": "cursor_token_alpha"},
            {"data": make_ind_items("page2", 5), "next_cursor": "cursor_token_beta"},
            {"data": make_ind_items("page3", 3), "next_cursor": None},  # No more pages
        ]

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        jobs = await discovery.search_indeed(
            keyword="Java",
            location="Bengaluru",
            max_pages=5,
            limit=5,
            fetch_details=False,
        )

        assert len(jobs) == 13
        assert mock_mcp.call_tool.call_count == 3

        calls = mock_mcp.call_tool.call_args_list
        # Page 0 has no cursor in payload
        assert "cursor" not in calls[0][0][1]
        # Page 1 forwarded next_cursor from Page 0
        assert calls[1][0][1]["cursor"] == "cursor_token_alpha"
        # Page 2 forwarded next_cursor from Page 1
        assert calls[2][0][1]["cursor"] == "cursor_token_beta"

    # ------------------------------------------------------------------
    # 6. Duplicate Records Across Pages Stop Pagination
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_duplicate_records_across_pages_stops_pagination(self):
        """If a subsequent page returns only duplicate records, pagination stops immediately."""
        mock_mcp = AsyncMock()

        identical_items = [
            {
                "public_slug": f"dup-job-{i}",
                "title": f"Java Dev {i}",
                "company": "Same Company",
                "url": f"https://company.com/job/{i}",
            }
            for i in range(5)
        ]

        # First call returns 5 items; second call returns the exact same 5 items
        mock_mcp.call_tool.side_effect = [
            {"data": identical_items},
            {"data": identical_items},
            {"data": identical_items},  # Should never be reached
        ]

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        jobs = await discovery.search_freehire(
            query="Java",
            limit=5,
            max_pages=5,
        )

        # Only 5 unique jobs returned; stopped after second page returned 0 new items
        assert len(jobs) == 5
        assert mock_mcp.call_tool.call_count == 2

    # ------------------------------------------------------------------
    # 7. Repeated Cursors Stop Pagination
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_indeed_repeated_cursor_stops_pagination(self):
        """If Indeed returns a repeated cursor already seen in the query run, loop terminates."""
        mock_mcp = AsyncMock()

        def make_ind_items(prefix):
            return [
                {
                    "key": f"{prefix}_1",
                    "title": "Java Dev",
                    "company": "Company",
                    "url": f"https://in.indeed.com/viewjob?jk={prefix}_1",
                }
            ]

        # Next cursor is identical to first page's cursor
        mock_mcp.call_tool.side_effect = [
            {"data": make_ind_items("a"), "next_cursor": "stuck_cursor_123"},
            {"data": make_ind_items("b"), "next_cursor": "stuck_cursor_123"},  # Repeated cursor!
            {"data": make_ind_items("c"), "next_cursor": "other_cursor"},      # Should not run
        ]

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        jobs = await discovery.search_indeed(
            keyword="Java",
            max_pages=5,
            limit=5,
            fetch_details=False,
        )

        assert len(jobs) == 2
        assert mock_mcp.call_tool.call_count == 2

    # ------------------------------------------------------------------
    # 8. Empty Pages and Timeouts Handled Safely
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_empty_page_terminates_pagination(self):
        """Empty data array terminates pagination without error."""
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {"data": []}

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        jobs = await discovery.search_freehire(query="Java", max_pages=3)
        assert jobs == []
        assert mock_mcp.call_tool.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_on_page_preserves_earlier_results(self):
        """A timeout on page 2 preserves results collected from page 1."""
        mock_mcp = AsyncMock()

        page1_items = [
            {
                "public_slug": "job-p1",
                "title": "Java Dev Page 1",
                "company": "Good Company",
                "url": "https://good.com/1",
            }
        ]

        mock_mcp.call_tool.side_effect = [
            {"data": page1_items},
            TimeoutError("Call timed out after 30 seconds"),
        ]

        discovery = JobDiscoveryService(mcp_client=mock_mcp)
        jobs = await discovery.search_freehire(query="Java", limit=1, max_pages=3)

        assert len(jobs) == 1
        assert jobs[0].platform_job_id == "job-p1"
        assert mock_mcp.call_tool.call_count == 2

    # ------------------------------------------------------------------
    # 9. Maximum Page and Result Limits
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_max_results_and_max_pages_caps_respected(self):
        """Confirms discovery stops when max_results or max_pages limit is reached."""
        mock_mcp = AsyncMock()

        def make_items(page_num, count=10):
            return [
                {
                    "id": f"li-{page_num}-{i}",
                    "title": f"Java Dev {page_num}-{i}",
                    "company": "Company",
                    "url": f"https://linkedin.com/jobs/view/li-{page_num}-{i}",
                }
                for i in range(count)
            ]

        mock_mcp.call_tool.side_effect = [
            {"data": make_items(1, 10)},
            {"data": make_items(2, 10)},
            {"data": make_items(3, 10)},
        ]

        discovery = JobDiscoveryService(mcp_client=mock_mcp)

        # Test A: max_pages=2 caps at 2 calls
        jobs_a = await discovery.search_linkedin(keyword="Java", max_pages=2, fetch_details=False)
        assert len(jobs_a) == 20
        assert mock_mcp.call_tool.call_count == 2

        # Test B: max_results=15 caps result count exactly at 15
        mock_mcp.reset_mock()
        mock_mcp.call_tool.side_effect = [
            {"data": make_items(1, 10)},
            {"data": make_items(2, 10)},
        ]
        jobs_b = await discovery.search_linkedin(keyword="Java", max_pages=3, max_results=15, fetch_details=False)
        assert len(jobs_b) == 15

    # ------------------------------------------------------------------
    # 10. Pipeline Compatibility and Database Unmodified
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_pipeline_integration_compatibility(self):
        """
        Runs the discovery pipeline with mocked discovery results to confirm
        that pipeline execution, scoring, deduplication, and export work smoothly
        with the new structured requirements fields without database writes.
        """
        mock_mcp = AsyncMock()
        mock_mcp.call_tool.return_value = {
            "data": [
                {
                    "public_slug": "pipeline-java-1",
                    "external_id": "pip-101",
                    "title": "Java Backend Engineer",
                    "company": "TCS",
                    "cities": ["Bengaluru"],
                    "url": "https://tcs.com/jobs/101",
                    "posted_at": "2026-10-11",
                    "description": "Java, Spring Boot, Microservices, 2 years experience.",
                    "enrichment": {
                        "experience_years_min": 2,
                        "requirements": [{"priority": "required", "text": "2 years Java experience"}],
                    },
                    "reality": {"class": "fresh"},
                }
            ]
        }

        with patch("src.pipeline.JobDiscoveryService") as MockService:
            mock_service_instance = JobDiscoveryService(mcp_client=mock_mcp)
            MockService.return_value = mock_service_instance

            discovered = await run_pipeline(
                skill_queries=["Java Spring Boot"],
                location="Bengaluru",
                country="India",
                limit_per_source=2,
                persist_to_db=False,  # Safety constraint: offline, no Neon writes
            )

            assert len(discovered) >= 1
            job = discovered[0]
            assert job.source_experience_years_min == 2
            assert job.structured_requirements is not None
            assert job.relevance is not None
