import logging
import json
from typing import List, Optional, Tuple, Dict, Any, Union
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from .models import JobRecord
from .db_config import DatabaseConfig, connect_database

logger = logging.getLogger(__name__)

class DatabasePersistence:
    """
    Manages PostgreSQL persistence for discovered jobs and validation history.
    Enforces idempotent writes and validation audit tracking.
    """
    def __init__(self, config: Optional[Union[dict, str]] = None):
        self.config = config if config is not None else DatabaseConfig.get_connection_dict()
        self._use_ai_columns: bool = True

    def get_connection(self):
        return connect_database(self.config)

    def check_health(self) -> Tuple[bool, str]:
        """
        Tests database reachability and executes a lightweight query.
        Returns (is_healthy, status_message).
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT version();")
                    ver = cur.fetchone()
                    return True, f"Connected successfully: {ver[0]}"
        except Exception as e:
            return False, f"Database connection failed: {e}"

    def initialize_schema(self, schema_sql_path: str = "schema.sql") -> None:
        """Executes the DDL schema file to ensure tables and types exist."""
        with open(schema_sql_path, "r", encoding="utf-8") as f:
            ddl = f.read()

        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(ddl)
            conn.commit()
        logger.info("Database schema initialized successfully.")

    def run_migrations(self, migrations_dir: str = "migrations") -> List[str]:
        """Runs any pending migration SQL scripts in alphabetical order."""
        import os
        from pathlib import Path
        mig_path = Path(migrations_dir)
        applied = []
        if not mig_path.exists():
            return applied

        sql_files = sorted(list(mig_path.glob("*.sql")))
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                for sql_file in sql_files:
                    with open(sql_file, "r", encoding="utf-8") as f:
                        ddl = f.read()
                    cur.execute(ddl)
                    applied.append(sql_file.name)
            conn.commit()
        logger.info(f"Applied migrations: {applied}")
        return applied

    UPSERT_JOB_SQL = """
        INSERT INTO jobs (
            platform, platform_job_id, title, company, location,
            description, posted_date, url, normalized_url,
            application_url, job_status, application_type,
            experience_level, relevance_score, score_breakdown, score_reasons, matched_skills,
            confidence_level, confidence_score, eligibility, eligibility_reason,
            is_provisional, unverified_components, available_fields,
            evidence_source, evidence_notes, raw_metadata,
            discovered_at, last_checked_at, updated_at,
            enrichment_status, char_count, estimated_tokens, canonical_hash
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, NOW(),
            %s, %s, %s, %s
        )
        ON CONFLICT (platform, platform_job_id) DO UPDATE SET
            title = COALESCE(NULLIF(TRIM(EXCLUDED.title), ''), jobs.title),
            company = COALESCE(NULLIF(TRIM(EXCLUDED.company), ''), jobs.company),
            location = COALESCE(NULLIF(TRIM(EXCLUDED.location), ''), jobs.location),
            description = COALESCE(NULLIF(TRIM(EXCLUDED.description), ''), jobs.description),
            posted_date = COALESCE(NULLIF(TRIM(EXCLUDED.posted_date), ''), jobs.posted_date),
            url = EXCLUDED.url,
            normalized_url = EXCLUDED.normalized_url,
            application_url = COALESCE(NULLIF(TRIM(EXCLUDED.application_url), ''), jobs.application_url),
            job_status = EXCLUDED.job_status,
            application_type = EXCLUDED.application_type,
            experience_level = CASE 
                WHEN EXCLUDED.experience_level::text != 'UNKNOWN' 
                THEN EXCLUDED.experience_level 
                ELSE jobs.experience_level 
            END,
            relevance_score = COALESCE(EXCLUDED.relevance_score, jobs.relevance_score),
            score_breakdown = CASE 
                WHEN EXCLUDED.score_breakdown IS NOT NULL AND EXCLUDED.score_breakdown != '{}'::jsonb 
                THEN EXCLUDED.score_breakdown 
                ELSE jobs.score_breakdown 
            END,
            score_reasons = CASE 
                WHEN EXCLUDED.score_reasons IS NOT NULL AND EXCLUDED.score_reasons != '[]'::jsonb 
                THEN EXCLUDED.score_reasons 
                ELSE jobs.score_reasons 
            END,
            matched_skills = CASE 
                WHEN EXCLUDED.matched_skills IS NOT NULL AND EXCLUDED.matched_skills != '[]'::jsonb 
                THEN EXCLUDED.matched_skills 
                ELSE jobs.matched_skills 
            END,
            confidence_level = COALESCE(EXCLUDED.confidence_level, jobs.confidence_level),
            confidence_score = COALESCE(EXCLUDED.confidence_score, jobs.confidence_score),
            eligibility = COALESCE(EXCLUDED.eligibility, jobs.eligibility),
            eligibility_reason = COALESCE(NULLIF(TRIM(EXCLUDED.eligibility_reason), ''), jobs.eligibility_reason),
            is_provisional = COALESCE(EXCLUDED.is_provisional, jobs.is_provisional),
            unverified_components = CASE 
                WHEN EXCLUDED.unverified_components IS NOT NULL AND EXCLUDED.unverified_components != '[]'::jsonb 
                THEN EXCLUDED.unverified_components 
                ELSE jobs.unverified_components 
            END,
            available_fields = CASE 
                WHEN EXCLUDED.available_fields IS NOT NULL AND EXCLUDED.available_fields != '[]'::jsonb 
                THEN EXCLUDED.available_fields 
                ELSE jobs.available_fields 
            END,
            evidence_source = EXCLUDED.evidence_source,
            evidence_notes = COALESCE(NULLIF(TRIM(EXCLUDED.evidence_notes), ''), jobs.evidence_notes),
            raw_metadata = COALESCE(jobs.raw_metadata, '{}'::jsonb) || COALESCE(EXCLUDED.raw_metadata, '{}'::jsonb),
            last_checked_at = EXCLUDED.last_checked_at,
            enrichment_status = CASE 
                WHEN jobs.enrichment_status = 'AI_ENRICHED' AND EXCLUDED.enrichment_status != 'AI_ENRICHED'
                THEN jobs.enrichment_status
                ELSE EXCLUDED.enrichment_status
            END,
            char_count = COALESCE(NULLIF(EXCLUDED.char_count, 0), jobs.char_count),
            estimated_tokens = COALESCE(NULLIF(EXCLUDED.estimated_tokens, 0), jobs.estimated_tokens),
            canonical_hash = COALESCE(EXCLUDED.canonical_hash, jobs.canonical_hash),
            updated_at = NOW()
        RETURNING id;
    """

    UPSERT_JOB_WITH_AI_SQL = """
        INSERT INTO jobs (
            platform, platform_job_id, title, company, location,
            description, posted_date, url, normalized_url,
            application_url, job_status, application_type,
            experience_level, relevance_score, score_breakdown, score_reasons, matched_skills,
            confidence_level, confidence_score, eligibility, eligibility_reason,
            is_provisional, unverified_components, available_fields,
            evidence_source, evidence_notes, raw_metadata,
            discovered_at, last_checked_at, updated_at,
            enrichment_status, char_count, estimated_tokens, canonical_hash,
            ai_score, ai_verdict, evaluation_source, ai_evaluation
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, NOW(),
            %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (platform, platform_job_id) DO UPDATE SET
            title = COALESCE(NULLIF(TRIM(EXCLUDED.title), ''), jobs.title),
            company = COALESCE(NULLIF(TRIM(EXCLUDED.company), ''), jobs.company),
            location = COALESCE(NULLIF(TRIM(EXCLUDED.location), ''), jobs.location),
            description = COALESCE(NULLIF(TRIM(EXCLUDED.description), ''), jobs.description),
            posted_date = COALESCE(NULLIF(TRIM(EXCLUDED.posted_date), ''), jobs.posted_date),
            url = EXCLUDED.url,
            normalized_url = EXCLUDED.normalized_url,
            application_url = COALESCE(NULLIF(TRIM(EXCLUDED.application_url), ''), jobs.application_url),
            job_status = EXCLUDED.job_status,
            application_type = EXCLUDED.application_type,
            experience_level = CASE 
                WHEN EXCLUDED.experience_level::text != 'UNKNOWN' 
                THEN EXCLUDED.experience_level 
                ELSE jobs.experience_level 
            END,
            relevance_score = COALESCE(EXCLUDED.relevance_score, jobs.relevance_score),
            score_breakdown = CASE 
                WHEN EXCLUDED.score_breakdown IS NOT NULL AND EXCLUDED.score_breakdown != '{}'::jsonb 
                THEN EXCLUDED.score_breakdown 
                ELSE jobs.score_breakdown 
            END,
            score_reasons = CASE 
                WHEN EXCLUDED.score_reasons IS NOT NULL AND EXCLUDED.score_reasons != '[]'::jsonb 
                THEN EXCLUDED.score_reasons 
                ELSE jobs.score_reasons 
            END,
            matched_skills = CASE 
                WHEN EXCLUDED.matched_skills IS NOT NULL AND EXCLUDED.matched_skills != '[]'::jsonb 
                THEN EXCLUDED.matched_skills 
                ELSE jobs.matched_skills 
            END,
            confidence_level = COALESCE(EXCLUDED.confidence_level, jobs.confidence_level),
            confidence_score = COALESCE(EXCLUDED.confidence_score, jobs.confidence_score),
            eligibility = COALESCE(EXCLUDED.eligibility, jobs.eligibility),
            eligibility_reason = COALESCE(NULLIF(TRIM(EXCLUDED.eligibility_reason), ''), jobs.eligibility_reason),
            is_provisional = COALESCE(EXCLUDED.is_provisional, jobs.is_provisional),
            unverified_components = CASE 
                WHEN EXCLUDED.unverified_components IS NOT NULL AND EXCLUDED.unverified_components != '[]'::jsonb 
                THEN EXCLUDED.unverified_components 
                ELSE jobs.unverified_components 
            END,
            available_fields = CASE 
                WHEN EXCLUDED.available_fields IS NOT NULL AND EXCLUDED.available_fields != '[]'::jsonb 
                THEN EXCLUDED.available_fields 
                ELSE jobs.available_fields 
            END,
            evidence_source = EXCLUDED.evidence_source,
            evidence_notes = COALESCE(NULLIF(TRIM(EXCLUDED.evidence_notes), ''), jobs.evidence_notes),
            raw_metadata = COALESCE(jobs.raw_metadata, '{}'::jsonb) || COALESCE(EXCLUDED.raw_metadata, '{}'::jsonb),
            last_checked_at = EXCLUDED.last_checked_at,
            enrichment_status = CASE 
                WHEN jobs.enrichment_status = 'AI_ENRICHED' AND EXCLUDED.enrichment_status != 'AI_ENRICHED'
                THEN jobs.enrichment_status
                ELSE EXCLUDED.enrichment_status
            END,
            char_count = COALESCE(NULLIF(EXCLUDED.char_count, 0), jobs.char_count),
            estimated_tokens = COALESCE(NULLIF(EXCLUDED.estimated_tokens, 0), jobs.estimated_tokens),
            canonical_hash = COALESCE(EXCLUDED.canonical_hash, jobs.canonical_hash),
            updated_at = NOW(),
            ai_score = CASE 
                WHEN jobs.evaluation_source IN ('AI_LIVE', 'AI_CACHED') AND (EXCLUDED.evaluation_source IS NULL OR EXCLUDED.evaluation_source NOT IN ('AI_LIVE', 'AI_CACHED'))
                THEN jobs.ai_score
                ELSE COALESCE(EXCLUDED.ai_score, jobs.ai_score)
            END,
            ai_verdict = CASE 
                WHEN jobs.evaluation_source IN ('AI_LIVE', 'AI_CACHED') AND (EXCLUDED.evaluation_source IS NULL OR EXCLUDED.evaluation_source NOT IN ('AI_LIVE', 'AI_CACHED'))
                THEN jobs.ai_verdict
                ELSE COALESCE(EXCLUDED.ai_verdict, jobs.ai_verdict)
            END,
            evaluation_source = CASE 
                WHEN jobs.evaluation_source IN ('AI_LIVE', 'AI_CACHED') AND (EXCLUDED.evaluation_source IS NULL OR EXCLUDED.evaluation_source NOT IN ('AI_LIVE', 'AI_CACHED'))
                THEN jobs.evaluation_source
                ELSE COALESCE(EXCLUDED.evaluation_source, jobs.evaluation_source)
            END,
            ai_evaluation = CASE 
                WHEN jobs.evaluation_source IN ('AI_LIVE', 'AI_CACHED') AND (EXCLUDED.evaluation_source IS NULL OR EXCLUDED.evaluation_source NOT IN ('AI_LIVE', 'AI_CACHED'))
                THEN jobs.ai_evaluation
                ELSE COALESCE(EXCLUDED.ai_evaluation, jobs.ai_evaluation)
            END
        RETURNING id;
    """

    INSERT_HISTORY_SQL = """
        INSERT INTO job_validation_history (
            job_id, job_status, application_type, evidence_source,
            evidence_notes, application_url, checked_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s);
    """

    @staticmethod
    def extract_job_raw_metadata(job: JobRecord, raw_metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Extracts and normalizes metadata for the raw_metadata JSONB column.
        Preserves source-provided experience requirements, structured requirements,
        deterministic eligibility results, freshness status, work mode, and any additional metadata.
        """
        meta: Dict[str, Any] = dict(raw_metadata or {})

        if job.source_experience_years_min is not None:
            meta["source_experience_years_min"] = job.source_experience_years_min
        if job.structured_requirements is not None:
            meta["structured_requirements"] = job.structured_requirements
        if job.work_mode is not None:
            meta["work_mode"] = job.work_mode
        if job.freshness_status is not None:
            meta["freshness_status"] = job.freshness_status.value
        if job.updated_date is not None:
            meta["updated_date"] = job.updated_date
        if job.deterministic_eligibility is not None:
            meta["deterministic_eligibility"] = {
                "decision": job.deterministic_eligibility.decision.value,
                "is_eligible": job.deterministic_eligibility.is_eligible,
                "reasons": job.deterministic_eligibility.reasons,
                "rejection_reasons": job.deterministic_eligibility.rejection_reasons,
                "review_reasons": job.deterministic_eligibility.review_reasons,
                "criteria_breakdown": job.deterministic_eligibility.criteria_breakdown,
            }
        if job.ai_score is not None:
            meta["ai_score"] = job.ai_score
        if job.ai_verdict is not None:
            meta["ai_verdict"] = job.ai_verdict
        if job.evaluation_source is not None:
            meta["evaluation_source"] = job.evaluation_source
        if job.ai_evaluation is not None:
            meta["ai_evaluation"] = job.ai_evaluation
        if getattr(job, "is_partial_description", False):
            meta["is_partial_description"] = True
        return meta

    @classmethod
    def _prepare_upsert_params(
        cls,
        job: JobRecord,
        raw_metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Tuple[Any, ...], Tuple[Any, ...]]:
        exp_level = job.relevance.experience_level.value if job.relevance else "UNKNOWN"
        rel_score = job.relevance.total_score if job.relevance else None
        breakdown_json = Json(job.relevance.score_breakdown) if job.relevance and job.relevance.score_breakdown else Json({})
        reasons_json = Json(job.relevance.reasons) if job.relevance and job.relevance.reasons else Json([])
        skills_json = Json(job.relevance.matched_skills) if job.relevance and job.relevance.matched_skills else Json([])
        conf_level = job.relevance.confidence_level.value if job.relevance else None
        conf_score = job.relevance.confidence_score if job.relevance else None
        elig = job.relevance.eligibility.value if job.relevance else None
        elig_reason = job.relevance.eligibility_reason if job.relevance else None
        is_prov = job.relevance.is_provisional if job.relevance else None
        unver_json = Json(job.relevance.unverified_components) if job.relevance and job.relevance.unverified_components else Json([])
        avail_json = Json(job.relevance.available_fields) if job.relevance and job.relevance.available_fields else Json([])

        desc = job.description_snippet or ""
        char_count = getattr(job, "char_count", 0) or len(desc)
        est_tokens = getattr(job, "estimated_tokens", 0) or max(1, (char_count + 3) // 4)
        canonical_h = getattr(job, "canonical_hash", None)
        if not canonical_h:
            import hashlib
            norm_key = f"{(job.company or '').lower().strip()}:{(job.title or '').lower().strip()}"
            canonical_h = hashlib.md5(norm_key.encode("utf-8")).hexdigest()
        enrichment_stat = getattr(job, "enrichment_status", None) or "STAGED_DETERMINISTIC"
        if hasattr(enrichment_stat, "value"):
            enrichment_stat = enrichment_stat.value

        meta_dict = cls.extract_job_raw_metadata(job, raw_metadata)
        meta_json = Json(meta_dict)

        job_params = (
            job.platform,
            job.platform_job_id,
            job.title,
            job.company,
            job.location,
            job.description_snippet,
            job.posted_date,
            job.url,
            job.normalized_url,
            job.validation.application_url,
            job.validation.job_status.value,
            job.validation.application_type.value,
            exp_level,
            rel_score,
            breakdown_json,
            reasons_json,
            skills_json,
            conf_level,
            conf_score,
            elig,
            elig_reason,
            is_prov,
            unver_json,
            avail_json,
            job.validation.evidence_source,
            job.validation.evidence_notes,
            meta_json,
            job.discovered_at,
            job.validation.last_checked_at,
            enrichment_stat,
            char_count,
            est_tokens,
            canonical_h,
        )

        history_params = (
            job.validation.job_status.value,
            job.validation.application_type.value,
            job.validation.evidence_source,
            job.validation.evidence_notes,
            job.validation.application_url,
            job.validation.last_checked_at,
        )

        return job_params, history_params

    def save_job(self, job: JobRecord, raw_metadata: Optional[Dict[str, Any]] = None) -> int:
        """
        Idempotently inserts or updates a job record and appends a validation history entry.
        Supports dedicated AI columns (ai_score, ai_verdict, evaluation_source, ai_evaluation)
        when migrated, and safely falls back if the database schema is not yet upgraded.
        Returns the job id.
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                job_params, hist_params = self._prepare_upsert_params(job, raw_metadata)
                if self._use_ai_columns:
                    ai_params = (
                        job.ai_score,
                        job.ai_verdict,
                        job.evaluation_source,
                        Json(job.ai_evaluation) if job.ai_evaluation else None,
                    )
                    try:
                        cur.execute(self.UPSERT_JOB_WITH_AI_SQL, job_params + ai_params)
                    except psycopg2.ProgrammingError as e:
                        if "does not exist" in str(e).lower() or "ai_score" in str(e).lower():
                            self._use_ai_columns = False
                            conn.rollback()
                            cur.execute(self.UPSERT_JOB_SQL, job_params)
                        else:
                            raise
                else:
                    cur.execute(self.UPSERT_JOB_SQL, job_params)

                job_id = cur.fetchone()[0]
                cur.execute(self.INSERT_HISTORY_SQL, (job_id,) + hist_params)
            conn.commit()
            return job_id

    def save_jobs(self, jobs: List[JobRecord]) -> List[int]:
        """Persists a batch of jobs inside an atomic transaction."""
        job_ids = []
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                for idx, job in enumerate(jobs):
                    job_params, hist_params = self._prepare_upsert_params(job)
                    if self._use_ai_columns:
                        ai_params = (
                            job.ai_score,
                            job.ai_verdict,
                            job.evaluation_source,
                            Json(job.ai_evaluation) if job.ai_evaluation else None,
                        )
                        try:
                            cur.execute(self.UPSERT_JOB_WITH_AI_SQL, job_params + ai_params)
                        except psycopg2.ProgrammingError as e:
                            if "does not exist" in str(e).lower() or "ai_score" in str(e).lower():
                                self._use_ai_columns = False
                                conn.rollback()
                                cur.execute(self.UPSERT_JOB_SQL, job_params)
                            else:
                                raise
                    else:
                        cur.execute(self.UPSERT_JOB_SQL, job_params)

                    job_id = cur.fetchone()[0]
                    job_ids.append(job_id)
                    cur.execute(self.INSERT_HISTORY_SQL, (job_id,) + hist_params)
            conn.commit()
        return job_ids

    def get_job_by_platform_id(self, platform: str, platform_job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves a single job row by (platform, platform_job_id)."""
        sql = "SELECT * FROM jobs WHERE platform = %s AND platform_job_id = %s;"
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (platform, platform_job_id))
                res = cur.fetchone()
                return dict(res) if res else None

    def get_validation_history(self, job_id: int) -> List[Dict[str, Any]]:
        """Retrieves audit validation history for a job."""
        sql = "SELECT * FROM job_validation_history WHERE job_id = %s ORDER BY checked_at DESC;"
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (job_id,))
                return [dict(r) for r in cur.fetchall()]

    def update_job_user_status(
        self,
        job_id: int,
        user_status: str,
        user_notes: Optional[str] = None,
        contact_person: Optional[str] = None,
        applied_at: Optional[Any] = None,
    ) -> bool:
        """
        Updates the candidate's lifecycle status for a job (DISCOVERED, SHORTLISTED, APPLIED, INTERVIEWING, OFFER, REJECTED, WITHDRAWN, STALE).
        If status changes to APPLIED and applied_at is not provided, sets applied_at = NOW().
        Updates last_status_change = NOW().
        """
        valid_statuses = {
            "DISCOVERED", "SHORTLISTED", "APPLIED", "INTERVIEWING",
            "OFFER", "REJECTED", "WITHDRAWN", "STALE"
        }
        status_clean = user_status.upper().strip()
        if status_clean not in valid_statuses:
            raise ValueError(f"Invalid user_status '{user_status}'. Must be one of: {sorted(list(valid_statuses))}")

        sql = """
            UPDATE jobs
            SET user_status = %s::user_application_status_enum,
                user_notes = COALESCE(%s, user_notes),
                contact_person = COALESCE(%s, contact_person),
                applied_at = CASE
                    WHEN %s::timestamptz IS NOT NULL THEN %s::timestamptz
                    WHEN %s = 'APPLIED' AND applied_at IS NULL THEN NOW()
                    ELSE applied_at
                END,
                last_status_change = NOW(),
                updated_at = NOW()
            WHERE id = %s
            RETURNING id;
        """
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (
                    status_clean,
                    user_notes,
                    contact_person,
                    applied_at,
                    applied_at,
                    status_clean,
                    job_id,
                ))
                row = cur.fetchone()
                conn.commit()
                return row is not None

    def get_tracked_applications(
        self,
        status_filter: Optional[List[str]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Retrieves all jobs tracked by the user (by default user_status != 'DISCOVERED', or matching status_filter).
        Ordered by last_status_change DESC.
        """
        conditions = []
        params: List[Any] = []

        if status_filter:
            clean_statuses = [s.upper().strip() for s in status_filter if s]
            if clean_statuses:
                placeholders = ", ".join(["%s::user_application_status_enum"] * len(clean_statuses))
                conditions.append(f"user_status IN ({placeholders})")
                params.extend(clean_statuses)
        else:
            conditions.append("user_status != 'DISCOVERED'::user_application_status_enum")

        where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"""
            SELECT id, platform, platform_job_id, title, company, location,
                   url, normalized_url, application_url, posted_date,
                   job_status, application_type, experience_level,
                   relevance_score, ai_score, ai_verdict, evaluation_source, ai_evaluation,
                   user_status, applied_at, user_notes, contact_person,
                   last_status_change, discovered_at, updated_at
            FROM jobs
            {where_clause}
            ORDER BY last_status_change DESC
            LIMIT %s OFFSET %s;
        """
        params.extend([limit, offset])

        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                return [dict(r) for r in cur.fetchall()]

    def get_quiet_applications(self, days_threshold: int = 10) -> List[Dict[str, Any]]:
        """
        Retrieves applications in 'APPLIED' status where applied_at is older than days_threshold days
        and no follow-up/stage progression has occurred.
        """
        sql = """
            SELECT id, platform, platform_job_id, title, company, location,
                   url, application_url, applied_at, user_notes, contact_person,
                   ai_score, ai_verdict, last_status_change,
                   ROUND(EXTRACT(EPOCH FROM (NOW() - applied_at)) / 86400) AS days_silent
            FROM jobs
            WHERE user_status = 'APPLIED'::user_application_status_enum
              AND applied_at IS NOT NULL
              AND applied_at <= NOW() - (%s || ' days')::INTERVAL
            ORDER BY applied_at ASC;
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (days_threshold,))
                return [dict(r) for r in cur.fetchall()]

    def get_crm_metrics(self) -> Dict[str, int]:
        """
        Returns counts for each stage in the user's application pipeline.
        """
        metrics: Dict[str, int] = {
            "DISCOVERED": 0,
            "SHORTLISTED": 0,
            "APPLIED": 0,
            "INTERVIEWING": 0,
            "OFFER": 0,
            "REJECTED": 0,
            "WITHDRAWN": 0,
            "STALE": 0,
            "TOTAL_TRACKED": 0,
            "QUIET_COUNT": 0,
        }
        with self.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_status::text AS status, COUNT(*) AS count FROM jobs GROUP BY user_status;")
                for row in cur.fetchall():
                    st_val = str(row[0]).upper()
                    cnt = int(row[1])
                    if st_val in metrics:
                        metrics[st_val] = cnt
                    if st_val != "DISCOVERED":
                        metrics["TOTAL_TRACKED"] += cnt

                cur.execute("""
                    SELECT COUNT(*) FROM jobs
                    WHERE user_status = 'APPLIED'::user_application_status_enum
                      AND applied_at IS NOT NULL
                      AND applied_at <= NOW() - INTERVAL '10 days';
                """)
                q_row = cur.fetchone()
                if q_row:
                    metrics["QUIET_COUNT"] = int(q_row[0])

        return metrics

    def save_tailored_pack(self, job_id: int, pack_data: Dict[str, Any]) -> bool:
        """
        Persists AI-generated tailoring pack into jobs.raw_metadata['tailored_pack'].
        Enables instant reloading without redundant LLM calls.
        """
        sql = """
            UPDATE jobs
            SET raw_metadata = COALESCE(raw_metadata, '{}'::jsonb) || jsonb_build_object('tailored_pack', %s::jsonb),
                updated_at = NOW()
            WHERE id = %s;
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (json.dumps(pack_data), job_id))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save tailored pack for job {job_id}: {e}")
            return False

    def get_tailored_pack(self, job_id: int) -> Optional[Dict[str, Any]]:
        """
        Retrieves existing tailored pack from jobs.raw_metadata['tailored_pack'].
        """
        sql = "SELECT raw_metadata->'tailored_pack' FROM jobs WHERE id = %s;"
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (job_id,))
                    row = cur.fetchone()
                    if row and row[0]:
                        val = row[0]
                        if isinstance(val, str):
                            return json.loads(val)
                        return val
            return None
        except Exception as e:
            logger.error(f"Failed to get tailored pack for job {job_id}: {e}")
            return None

    def get_staged_jobs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves jobs awaiting AI enrichment, ordered by deterministic relevance score descending."""
        sql = """
            SELECT * FROM jobs 
            WHERE enrichment_status IN ('STAGED_DETERMINISTIC', 'AI_QUEUED', 'AI_QUOTA_HELD')
            ORDER BY COALESCE(relevance_score, 0) DESC, id ASC
            LIMIT %s;
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, (limit,))
                return [dict(r) for r in cur.fetchall()]

    def get_enrichment_metrics(self) -> Dict[str, Any]:
        """Returns pipeline staging and enrichment counts."""
        sql = """
            SELECT 
                COUNT(*) AS total_jobs,
                COUNT(*) FILTER (WHERE enrichment_status = 'AI_ENRICHED') AS ai_enriched_count,
                COUNT(*) FILTER (WHERE enrichment_status IN ('STAGED_DETERMINISTIC', 'AI_QUEUED')) AS staged_count,
                COUNT(*) FILTER (WHERE enrichment_status = 'AI_IN_FLIGHT') AS in_flight_count,
                COUNT(*) FILTER (WHERE enrichment_status = 'AI_QUOTA_HELD') AS quota_held_count
            FROM jobs;
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql)
                row = cur.fetchone()
                return dict(row) if row else {
                    "total_jobs": 0, "ai_enriched_count": 0, "staged_count": 0, "in_flight_count": 0, "quota_held_count": 0
                }

    def claim_staged_jobs_for_enrichment(self, limit: int = 5, job_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
        """
        Atomically claims up to `limit` staged jobs by updating their status to AI_IN_FLIGHT.
        Uses FOR UPDATE SKIP LOCKED to prevent duplicate processing across concurrent workers.
        """
        where_clause = "enrichment_status IN ('STAGED_DETERMINISTIC', 'AI_QUEUED', 'AI_QUOTA_HELD')"
        params: List[Any] = []
        if job_ids:
            where_clause += " AND id = ANY(%s)"
            params.append(job_ids)
        params.append(limit)

        sql = f"""
            UPDATE jobs
            SET enrichment_status = 'AI_IN_FLIGHT',
                updated_at = NOW()
            WHERE id IN (
                SELECT id FROM jobs
                WHERE {where_clause}
                ORDER BY COALESCE(relevance_score, 0) DESC, id ASC
                LIMIT %s
                FOR UPDATE SKIP LOCKED
            )
            RETURNING *;
        """
        with self.get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, tuple(params))
                rows = cur.fetchall()
            conn.commit()
            return [dict(r) for r in rows]

    def save_job_ai_enrichment(self, job_id: int, ai_score: int, fit_analysis: Dict[str, Any]) -> bool:
        """
        Persists AI evaluation results and marks job as AI_ENRICHED.
        Updates relevance_score, score_reasons, matched_skills, eligibility_reason, and raw_metadata['ai_enrichment'].
        """
        sql = """
            UPDATE jobs
            SET relevance_score = %s,
                score_reasons = %s::jsonb,
                matched_skills = %s::jsonb,
                eligibility_reason = %s,
                raw_metadata = COALESCE(raw_metadata, '{}'::jsonb) || jsonb_build_object('ai_enrichment', %s::jsonb),
                enrichment_status = 'AI_ENRICHED',
                updated_at = NOW()
            WHERE id = %s;
        """
        score_reasons = fit_analysis.get("why_good") or []
        matched_skills = fit_analysis.get("matched_skills") or []
        eligibility_reason = fit_analysis.get("fit_verdict") or ""

        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        sql,
                        (
                            ai_score,
                            json.dumps(score_reasons),
                            json.dumps(matched_skills),
                            eligibility_reason,
                            json.dumps(fit_analysis),
                            job_id,
                        ),
                    )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to save AI enrichment for job {job_id}: {e}")
            return False

    def mark_jobs_quota_held(self, job_ids: List[int], reason: Optional[str] = None) -> int:
        """
        Reverts in-flight jobs back to AI_QUOTA_HELD when rate limits or quota boundaries are met.
        """
        if not job_ids:
            return 0
        sql = """
            UPDATE jobs
            SET enrichment_status = 'AI_QUOTA_HELD',
                updated_at = NOW()
            WHERE id = ANY(%s) AND enrichment_status = 'AI_IN_FLIGHT';
        """
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (job_ids,))
                    count = cur.rowcount
                conn.commit()
            return count
        except Exception as e:
            logger.error(f"Failed to mark jobs quota held: {e}")
            return 0

    def update_enrichment_status(self, job_id: int, status: str) -> bool:
        """Updates the enrichment status of a specific job."""
        sql = "UPDATE jobs SET enrichment_status = %s, updated_at = NOW() WHERE id = %s;"
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, (status, job_id))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to update enrichment status for job {job_id}: {e}")
            return False


