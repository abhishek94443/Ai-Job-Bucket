import logging
import re
import json
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple, Union
import psycopg2
from psycopg2.extras import RealDictCursor
from .db_config import DatabaseConfig, connect_database

logger = logging.getLogger(__name__)

def format_freshness(posted_date_str: Optional[str], reference_date: Optional[datetime] = None) -> Tuple[str, str]:
    """
    Parses a posted_date string and formats a human-friendly relative label.
    Returns (display_date, relative_label).
    If missing or invalid, returns ("N/A", "Posting date unknown").
    """
    if not posted_date_str or not isinstance(posted_date_str, str):
        return ("N/A", "Posting date unknown")
    
    clean_str = posted_date_str.strip()
    if not clean_str:
        return ("N/A", "Posting date unknown")

    # Match YYYY-MM-DD prefix
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", clean_str)
    if not match:
        return (clean_str, "Posting date unknown")

    try:
        year, month, day = map(int, match.groups())
        p_date = datetime(year, month, day).date()
    except ValueError:
        return (clean_str, "Posting date unknown")

    ref_date = reference_date.date() if reference_date else datetime.now().date()
    delta_days = (ref_date - p_date).days

    if delta_days == 0:
        label = "Today"
    elif delta_days == 1:
        label = "1 day ago"
    elif delta_days > 1:
        label = f"{delta_days} days ago"
    elif delta_days < 0:
        # Date is in the future relative to ref_date
        label = f"in {abs(delta_days)} days"
    else:
        label = "Posting date unknown"

    return (p_date.strftime("%Y-%m-%d"), label)


class DashboardQueryService:
    """
    Read-only query service for dashboard analytics and filtered job browsing.
    Strictly performs SELECT queries using parameterized SQL.
    Does NOT modify records or trigger discovery runs.
    """
    def __init__(self, config: Optional[Union[dict, str]] = None):
        self.config = config if config is not None else DatabaseConfig.get_connection_dict()

    def get_connection(self):
        return connect_database(self.config)

    def check_health(self) -> Tuple[bool, str]:
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1;")
                    return True, "Database reachable"
        except Exception as e:
            return False, f"Database unreachable: {e}"

    def get_overview_metrics(self) -> Dict[str, Any]:
        """
        Calculates aggregate summary statistics:
        - Total stored jobs
        - By source platform
        - By listing status
        - By eligibility
        - By confidence level
        """
        metrics: Dict[str, Any] = {
            "total_jobs": 0,
            "by_platform": {},
            "by_status": {},
            "by_eligibility": {},
            "by_confidence": {},
        }
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                # Total
                cur.execute("SELECT COUNT(*) FROM jobs;")
                metrics["total_jobs"] = cur.fetchone()[0]

                # By Platform
                cur.execute("SELECT platform, COUNT(*) FROM jobs GROUP BY platform;")
                metrics["by_platform"] = {r[0]: r[1] for r in cur.fetchall()}

                # By Listing Status
                cur.execute("SELECT job_status, COUNT(*) FROM jobs GROUP BY job_status;")
                metrics["by_status"] = {r[0]: r[1] for r in cur.fetchall()}

                # By Eligibility
                cur.execute("SELECT eligibility, COUNT(*) FROM jobs GROUP BY eligibility;")
                metrics["by_eligibility"] = {r[0]: r[1] for r in cur.fetchall()}

                # By Confidence Level
                cur.execute("SELECT confidence_level, COUNT(*) FROM jobs GROUP BY confidence_level;")
                metrics["by_confidence"] = {r[0]: r[1] for r in cur.fetchall()}

                # AI Evaluation Metrics (safely checking migrated columns or raw_metadata)
                try:
                    if self._has_ai_columns(conn):
                        cur.execute("SELECT evaluation_source, COUNT(*) FROM jobs WHERE evaluation_source IS NOT NULL GROUP BY evaluation_source;")
                        sources = {r[0]: r[1] for r in cur.fetchall()}
                        cur.execute("SELECT ai_verdict, COUNT(*) FROM jobs WHERE ai_verdict IS NOT NULL GROUP BY ai_verdict;")
                        verdicts = {r[0]: r[1] for r in cur.fetchall()}
                    else:
                        cur.execute("SELECT raw_metadata->>'evaluation_source', COUNT(*) FROM jobs WHERE raw_metadata->>'evaluation_source' IS NOT NULL GROUP BY 1;")
                        sources = {r[0]: r[1] for r in cur.fetchall()}
                        cur.execute("SELECT raw_metadata->>'ai_verdict', COUNT(*) FROM jobs WHERE raw_metadata->>'ai_verdict' IS NOT NULL GROUP BY 1;")
                        verdicts = {r[0]: r[1] for r in cur.fetchall()}

                    metrics["ai_live_count"] = sources.get("AI_LIVE", 0)
                    metrics["ai_cached_count"] = sources.get("AI_CACHED", 0)
                    metrics["deterministic_fallback_count"] = sources.get("DETERMINISTIC_FALLBACK", 0)
                    metrics["by_ai_verdict"] = verdicts
                except Exception:
                    metrics["ai_live_count"] = 0
                    metrics["ai_cached_count"] = 0
                    metrics["deterministic_fallback_count"] = 0
                    metrics["by_ai_verdict"] = {}

        return metrics

    def _has_ai_columns(self, conn) -> bool:
        """Checks if migration 005 has added ai_score to jobs table."""
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM information_schema.columns WHERE table_name = 'jobs' AND column_name = 'ai_score';"
                )
                return cur.fetchone() is not None
        except Exception:
            return False

    def search_jobs(
        self,
        search_term: Optional[str] = None,
        platform: Optional[str] = None,
        eligibility: Optional[str] = None,
        listing_status: Optional[str] = None,
        confidence_level: Optional[str] = None,
        experience_level: Optional[str] = None,
        min_score: Optional[int] = None,
        posted_within: Optional[str] = None,  # "All time", "Last 24 hours", "Last 3 days", "Last 7 days", "Last 14 days", "Last 30 days"
        fetched_within: Optional[str] = None,  # "today", "last 24 hours", "last 3 days", "last 7 days", "last 14 days", "last 30 days"
        include_unknown_dates: bool = False,
        ai_verdict: Optional[str] = None,
        evaluation_source: Optional[str] = None,
        user_status: Optional[str] = None,
        exclude_applied: bool = False,
        sort_by: str = "score",  # "ai_score", "score", "date", "company", "newest", "fetched"
        sort_order: str = "DESC",
        page: int = 1,
        page_size: int = 10
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Executes a safe parameterized read-only query with filtering, sorting, and pagination.
        Returns (list_of_job_dicts, total_matching_records).
        """
        conditions: List[str] = []
        params: List[Any] = []

        if search_term and search_term.strip():
            term = f"%{search_term.strip().lower()}%"
            conditions.append("""
                (LOWER(title) LIKE %s OR LOWER(company) LIKE %s OR LOWER(COALESCE(description, '')) LIKE %s)
            """)
            params.extend([term, term, term])

        if platform and platform != "ALL":
            plats = [p.strip().lower() for p in platform.split(",") if p.strip()]
            if len(plats) == 1:
                conditions.append("LOWER(platform) = LOWER(%s)")
                params.append(plats[0])
            elif len(plats) > 1:
                placeholders = ", ".join(["LOWER(%s)"] * len(plats))
                conditions.append(f"LOWER(platform) IN ({placeholders})")
                params.extend(plats)

        if eligibility and eligibility != "ALL":
            conditions.append("eligibility = %s")
            params.append(eligibility)

        if listing_status and listing_status != "ALL":
            conditions.append("job_status = %s")
            params.append(listing_status)

        if confidence_level and confidence_level != "ALL":
            conditions.append("confidence_level = %s")
            params.append(confidence_level)

        if experience_level and experience_level != "ALL":
            conditions.append("experience_level = %s")
            params.append(experience_level)

        if min_score is not None and min_score > 0:
            # Score filtering prioritizes AI match score when available, falling back to deterministic relevance_score
            conditions.append("COALESCE(ai_score, relevance_score, 0) >= %s")
            params.append(min_score)

        if ai_verdict and ai_verdict != "ALL":
            conditions.append("(ai_verdict = %s OR raw_metadata->>'ai_verdict' = %s)")
            params.extend([ai_verdict, ai_verdict])

        if evaluation_source and evaluation_source != "ALL":
            conditions.append("(evaluation_source = %s OR raw_metadata->>'evaluation_source' = %s)")
            params.extend([evaluation_source, evaluation_source])

        if exclude_applied:
            conditions.append("user_status != 'APPLIED'::user_application_status_enum")

        if user_status and user_status != "ALL":
            conditions.append("user_status = %s::user_application_status_enum")
            params.append(user_status.upper().strip())

        # Posted Within filter (by company / job board)
        posted_window_days_map = {
            "last 24 hours": 1,
            "today": 1,
            "last 3 days": 3,
            "last 7 days": 7,
            "last 14 days": 14,
            "last 30 days": 30,
        }
        if posted_within and posted_within.strip().lower() in posted_window_days_map:
            days = posted_window_days_map[posted_within.strip().lower()]
            if include_unknown_dates:
                conditions.append("""
                    (
                        (posted_date IS NOT NULL 
                         AND posted_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' 
                         AND TO_DATE(SUBSTRING(posted_date FROM 1 FOR 10), 'YYYY-MM-DD') >= CURRENT_DATE - (%s * INTERVAL '1 day'))
                        OR (posted_date IS NULL OR NOT (posted_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'))
                    )
                """)
            else:
                conditions.append("""
                    posted_date IS NOT NULL 
                    AND posted_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' 
                    AND TO_DATE(SUBSTRING(posted_date FROM 1 FOR 10), 'YYYY-MM-DD') >= CURRENT_DATE - (%s * INTERVAL '1 day')
                """)
            params.append(days)

        # Fetched / Discovered Within filter (by our ingestion pipeline)
        fetched_window_days_map = {
            "today": 1,
            "last 24 hours": 1,
            "last 3 days": 3,
            "last 7 days": 7,
            "last 14 days": 14,
            "last 30 days": 30,
        }
        if fetched_within and fetched_within.strip().lower() in fetched_window_days_map:
            f_days = fetched_window_days_map[fetched_within.strip().lower()]
            conditions.append("discovered_at >= NOW() - (%s * INTERVAL '1 day')")
            params.append(f_days)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        # Safe sort mapping to prevent SQL injection
        direction = "ASC" if sort_order.upper() == "ASC" else "DESC"
        if sort_by == "newest":
            date_expr = "(CASE WHEN posted_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}' THEN TO_DATE(SUBSTRING(posted_date FROM 1 FOR 10), 'YYYY-MM-DD') ELSE NULL END)"
            order_clause = f"{date_expr} {direction} NULLS LAST, id ASC"
        elif sort_by == "ai_score":
            order_clause = f"ai_score {direction} NULLS LAST, relevance_score {direction} NULLS LAST, id ASC"
        elif sort_by in ("fetched", "date_fetched"):
            order_clause = f"discovered_at {direction} NULLS LAST, id ASC"
        else:
            sort_map = {
                "score": "relevance_score",
                "date": "discovered_at",
                "company": "company"
            }
            order_col = sort_map.get(sort_by, "relevance_score")
            order_clause = f"{order_col} {direction} NULLS LAST, id ASC"

        # Count total matching rows
        count_sql = f"SELECT COUNT(*) FROM jobs {where_clause};"

        # Fetch paginated rows with dedicated AI columns
        offset = max(0, (page - 1) * page_size)
        query_sql = f"""
            SELECT 
                id, platform, platform_job_id, title, company, location,
                description, posted_date, url, normalized_url, application_url,
                job_status, application_type, experience_level, relevance_score,
                confidence_level, confidence_score, eligibility, eligibility_reason,
                is_provisional, score_breakdown, score_reasons, matched_skills,
                unverified_components, available_fields, evidence_source, evidence_notes,
                last_checked_at, discovered_at, raw_metadata,
                ai_score, ai_verdict, evaluation_source, ai_evaluation,
                enrichment_status, char_count, estimated_tokens, canonical_hash
            FROM jobs
            {where_clause}
            ORDER BY {order_clause}
            LIMIT %s OFFSET %s;
        """

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(count_sql, params)
                res = cur.fetchone()
                if isinstance(res, dict):
                    total_count = res.get("count", 0)
                elif res:
                    total_count = res[0]
                else:
                    total_count = 0

                fetch_params = list(params) + [page_size, offset]
                cur.execute(query_sql, fetch_params)
                rows = [dict(row) for row in cur.fetchall()]

                # Unpack AI fields from dedicated columns, with fallback to raw_metadata if null
                for r in rows:
                    meta = r.get("raw_metadata") or {}
                    if not isinstance(meta, dict):
                        try:
                            meta = json.loads(meta)
                        except Exception:
                            meta = {}
                    if r.get("ai_score") is None:
                        raw_s = meta.get("ai_score")
                        r["ai_score"] = int(raw_s) if raw_s is not None else None
                    if r.get("ai_verdict") is None:
                        r["ai_verdict"] = meta.get("ai_verdict")
                    if r.get("evaluation_source") is None:
                        r["evaluation_source"] = meta.get("evaluation_source")
                    if r.get("ai_evaluation") is None:
                        r["ai_evaluation"] = meta.get("ai_evaluation")

        return rows, total_count

    def get_job_by_id(self, job_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves full record for a specific job."""
        sql = "SELECT * FROM jobs WHERE id = %s;"
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (job_id,))
                res = cur.fetchone()
                return dict(res) if res else None

    def get_all_jobs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Retrieves jobs sorted by score."""
        rows, _ = self.search_jobs(page=1, page_size=limit)
        return rows

    def get_tracked_applications(
        self,
        status_filter: Optional[List[str]] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retrieves all tracked applications (shortlisted, applied, interviewing, etc.)."""
        from .db_persistence import DatabasePersistence
        return DatabasePersistence(config=self.config).get_tracked_applications(status_filter, limit, offset)

    def get_quiet_applications(self, days_threshold: int = 10) -> List[Dict[str, Any]]:
        """Retrieves applications in APPLIED status with no update for >= days_threshold days."""
        from .db_persistence import DatabasePersistence
        return DatabasePersistence(config=self.config).get_quiet_applications(days_threshold)

    def get_crm_metrics(self) -> Dict[str, int]:
        """Returns application pipeline breakdown metrics."""
        from .db_persistence import DatabasePersistence
        return DatabasePersistence(config=self.config).get_crm_metrics()

    def update_job_user_status(
        self,
        job_id: int,
        user_status: str,
        user_notes: Optional[str] = None,
        contact_person: Optional[str] = None,
        applied_at: Optional[Any] = None,
    ) -> bool:
        """Updates user application status and notes for a job."""
        from .db_persistence import DatabasePersistence
        return DatabasePersistence(config=self.config).update_job_user_status(
            job_id=job_id,
            user_status=user_status,
            user_notes=user_notes,
            contact_person=contact_person,
            applied_at=applied_at,
        )

    def get_tailored_pack(self, job_id: int) -> Optional[Dict[str, Any]]:
        """Retrieves existing tailored pack for a job."""
        from .db_persistence import DatabasePersistence
        return DatabasePersistence(config=self.config).get_tailored_pack(job_id)

    def save_tailored_pack(self, job_id: int, pack_data: Dict[str, Any]) -> bool:
        """Persists tailored pack for a job."""
        from .db_persistence import DatabasePersistence
        return DatabasePersistence(config=self.config).save_tailored_pack(job_id, pack_data)
