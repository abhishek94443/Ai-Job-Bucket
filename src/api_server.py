"""
Job Bucket - Headless REST & Decoupled Backend API Server
Provides high-performance, asynchronous REST endpoints for:
1. Job Discovery & Tracking (Filtering, search, status updates)
2. 2-Agent Tailoring Engine (Drafter + Reviewer Grounding Audit)
3. Candidate Master Profile & PII Masking
4. Direct LaTeX-to-PDF Compilation & ATS Audit
5. AI Gateway Model Registry & Quotas
6. Static Mount for Next.js Frontend (Single Container Monolith)

Fully decoupled: The Next.js frontend can run on localhost:3000, Vercel, or be detached completely.
"""

import base64
import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, status, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.db_config import DatabaseConfig, connect_database
from src.dashboard_service import DashboardQueryService
from src.candidate_profile_service import CandidateMasterProfileService
from src.agentic_tailoring_service import AgenticTailoringEngine, load_prompt_file, PROMPTS_DIR
from src.pdf_compiler import PDFCompilationEngine
from src.ai_gateway import AIGateway
from src.env_manager import EnvKeyManager
from src.resume_parser import ResumeParser, ResumeParseError

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("job_bucket_api")

app = FastAPI(
    title="Job Bucket API",
    description="Executive Software Engineering Discovery & Agentic Tailoring Backend",
    version="2.0.0",
)

# Enable CORS for Next.js frontend (localhost:3000 or any production origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Services
profile_svc = CandidateMasterProfileService()
tailoring_engine = AgenticTailoringEngine()
pdf_engine = PDFCompilationEngine()
query_svc = DashboardQueryService()
ai_gateway = AIGateway()
env_manager = EnvKeyManager()


# -------------------------------------------------------------------------
# Pydantic Schemas
# -------------------------------------------------------------------------

class UpdateProfileRequest(BaseModel):
    candidate_name: str
    phone: str
    email: str
    location: str
    linkedin_url: str
    github_url: str
    status: str = "Actively Looking"
    constraints: str = "Open to Remote / Bangalore"
    technical_skills: Dict[str, Any] = Field(default_factory=dict)
    work_history: List[Dict[str, Any]] = Field(default_factory=list)
    projects: List[Dict[str, Any]] = Field(default_factory=list)
    education: List[Dict[str, Any]] = Field(default_factory=list)
    languages: List[Dict[str, Any]] = Field(default_factory=list)
    master_latex_source: str = ""
    behavioral_profile_md: str = ""


class CompilePdfRequest(BaseModel):
    latex_code: str


class TrackJobRequest(BaseModel):
    status: str
    notes: Optional[str] = None


class TailorJobRequest(BaseModel):
    prefer_ai: bool = True
    sync: bool = False


class EnrichBatchRequest(BaseModel):
    batch_size: Optional[int] = None
    jobs_per_call: int = 2
    concurrency: int = 2
    job_ids: Optional[List[int]] = None


class AddModelRequest(BaseModel):
    provider: str
    model_id: str
    api_key_alias: str = "GEMINI_API_KEY"
    rpm_limit: int = 15
    tpm_limit: int = 250000
    rpd_limit: int = 500
    priority: int = 1


class SaveKeyRequest(BaseModel):
    alias: str
    key_value: str


class ReorderModelsRequest(BaseModel):
    model_ids: List[int]


# -------------------------------------------------------------------------
# Health & Diagnostics
# -------------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "engine": "Job Bucket Headless API",
        "version": "2.0.0",
    }


# -------------------------------------------------------------------------
# Jobs & Discovery Endpoints
# -------------------------------------------------------------------------

@app.get("/api/jobs")
def get_jobs(
    search: Optional[str] = Query(None, description="Search keyword in title, company, or description"),
    min_score: Optional[int] = Query(None, description="Minimum match relevance score"),
    status: Optional[str] = Query(None, description="Application tracking status filter"),
    portal: Optional[str] = Query(None, description="Platform source: freehire, linkedin, indeed (comma-separated supported)"),
    posted_within: Optional[str] = Query(None, description="Date posted window: 'last 24 hours', 'last 3 days', 'last 7 days', 'last 14 days', 'last 30 days'"),
    fetched_within: Optional[str] = Query(None, description="Date fetched window: 'today', 'last 24 hours', 'last 3 days', 'last 7 days', 'last 14 days', 'last 30 days'"),
    sort_by: Optional[str] = Query("ai_score", description="Sort by: 'ai_score', 'newest', 'fetched', 'company'"),
    sort_order: Optional[str] = Query("DESC", description="Sort direction: 'DESC' or 'ASC'"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=500, description="Jobs per page (default 20)"),
    limit: Optional[int] = Query(None, description="Legacy limit override"),
):
    try:
        # If legacy limit provided without explicit page_size, adapt page_size
        effective_page_size = limit if (limit and limit > 0 and page_size == 20) else page_size

        rows, total_count = query_svc.search_jobs(
            search_term=search,
            platform=portal,
            min_score=min_score,
            user_status=status if (status and status != "ALL") else None,
            posted_within=posted_within,
            fetched_within=fetched_within,
            sort_by=sort_by or "ai_score",
            sort_order=sort_order or "DESC",
            page=page,
            page_size=effective_page_size,
        )

        total_pages = max(1, math.ceil(total_count / effective_page_size)) if total_count > 0 else 1

        return {
            "total": total_count,
            "page": page,
            "page_size": effective_page_size,
            "total_pages": total_pages,
            "jobs": rows,
        }
    except Exception as e:
        logger.error(f"Failed to fetch jobs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/jobs/{job_id}")
def get_job_detail(job_id: int):
    job = query_svc.get_job_by_id(job_id)
    if job:
        return job
    raise HTTPException(status_code=404, detail="Job not found")


@app.post("/api/jobs/{job_id}/enrich")
async def enrich_single_job(job_id: int):
    """
    On-demand AI enrichment for a single selected job.
    """
    job = query_svc.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    try:
        from src.ai_enrichment_worker import AIEnrichmentWorker
        worker = AIEnrichmentWorker()
        result = await worker.run_enrichment_batch(
            batch_size=1,
            concurrency=1,
            job_ids=[job_id],
            inter_request_delay_seconds=0.0,
        )
        updated_job = query_svc.get_job_by_id(job_id)
        return {
            "status": "success" if result.get("enriched_count", 0) > 0 else "held",
            "batch_result": result,
            "job": updated_job,
        }
    except Exception as e:
        logger.error(f"Single job enrichment failed for job {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/jobs/{job_id}/track")
def track_job(job_id: int, req: TrackJobRequest):
    try:
        from src.db_persistence import DatabasePersistence
        db_p = DatabasePersistence()
        success = db_p.update_job_status(job_id, req.status, notes=req.notes)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to update job tracking status")
        return {"status": "success", "job_id": job_id, "new_status": req.status}
    except Exception as e:
        logger.error(f"Track job failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/jobs/discover")
async def trigger_discovery_pipeline(background_tasks: BackgroundTasks):
    from src.pipeline import run_pipeline
    
    async def _run():
        logger.info("Executing background discovery pipeline without artificial job caps...")
        await run_pipeline(limit_per_source=None, persist_to_db=True)
        logger.info("Discovery complete. Auto-chaining AI enrichment for all newly staged jobs...")
        try:
            from src.ai_enrichment_worker import AIEnrichmentWorker
            worker = AIEnrichmentWorker()
            while True:
                res = await worker.run_enrichment_batch(jobs_per_call=2, concurrency=2)
                logger.info(f"Auto-chained enrichment batch: {res.get('message')}")
                if res.get("claimed_count", 0) == 0 or res.get("held_count", 0) > 0:
                    break
            logger.info("Auto-chained enrichment fully finished.")
        except Exception as e:
            logger.error(f"Auto-chained enrichment failed: {e}")

    background_tasks.add_task(_run)
    return {"status": "accepted", "message": "Job discovery pipeline running in background with auto-enrichment."}


@app.get("/api/pipeline/status")
def get_pipeline_status():
    """Returns staging queue, window capacity, and AI enrichment telemetry."""
    try:
        from src.db_persistence import DatabasePersistence
        from src.batch_window_optimizer import calculate_window_metrics
        db_p = DatabasePersistence()
        metrics = db_p.get_enrichment_metrics()
        staged_jobs = db_p.get_staged_jobs(limit=50)
        window_meta = calculate_window_metrics(staged_jobs, target_batch_size=5)
        
        from src.ai_gateway import AIGateway
        gw = AIGateway()
        models = gw.get_registered_models(active_only=True)
        active_model = models[0] if models else None
        
        return {
            "total_jobs": metrics.get("total_jobs", 0),
            "ai_enriched_count": metrics.get("ai_enriched_count", 0),
            "staged_count": metrics.get("staged_count", 0),
            "in_flight_count": metrics.get("in_flight_count", 0),
            "quota_held_count": metrics.get("quota_held_count", 0),
            "active_model": active_model.get("model_id") if active_model else "None",
            "active_provider": active_model.get("provider") if active_model else "None",
            "window_metrics": window_meta,
        }
    except Exception as e:
        logger.error(f"Failed to fetch pipeline status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/pipeline/enrich-batch")
async def enrich_pipeline_batch(req: EnrichBatchRequest):
    """
    Triggers concurrent AI enrichment for staged jobs under bounded semaphore concurrency
    and multi-job prompt packing.
    """
    try:
        from src.ai_enrichment_worker import AIEnrichmentWorker
        worker = AIEnrichmentWorker()
        result = await worker.run_enrichment_batch(
            batch_size=req.batch_size,
            jobs_per_call=req.jobs_per_call,
            concurrency=req.concurrency,
            job_ids=req.job_ids,
        )
        return result
    except Exception as e:
        logger.error(f"Batch enrichment failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# -------------------------------------------------------------------------
# 2-Agent Tailoring Engine Endpoints
# -------------------------------------------------------------------------

@app.get("/api/jobs/{job_id}/tailor")
def get_job_tailoring(job_id: int):
    """Retrieves previously synthesized tailored pack for this job if present."""
    pack = query_svc.get_tailored_pack(job_id)
    if not pack:
        raise HTTPException(status_code=404, detail=f"No tailoring pack found for job {job_id}")
    return pack


@app.post("/api/jobs/{job_id}/tailor")
def tailor_job(job_id: int, req: TailorJobRequest, background_tasks: BackgroundTasks):
    job = query_svc.get_job_by_id(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    if req.sync:
        try:
            result = tailoring_engine.run_workflow(job, prefer_ai=req.prefer_ai)
            result_dict = result.model_dump()
            result_dict["status"] = "completed"
            query_svc.save_tailored_pack(job_id, result_dict)
            return result_dict
        except Exception as e:
            logger.error(f"2-Agent tailoring failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Production-ready asynchronous execution pattern:
    # Immediately store in-progress marker so UI and status checks know work is underway
    in_progress_state = {
        "status": "in_progress",
        "job_id": job_id,
        "job_title": job.get("title", ""),
        "company": job.get("company", ""),
    }
    query_svc.save_tailored_pack(job_id, in_progress_state)

    def _execute():
        try:
            logger.info(f"Background 2-agent tailoring started for job {job_id} ({job.get('title')})")
            result = tailoring_engine.run_workflow(job, prefer_ai=req.prefer_ai)
            result_dict = result.model_dump()
            result_dict["status"] = "completed"
            query_svc.save_tailored_pack(job_id, result_dict)
            logger.info(f"Background 2-agent tailoring successfully completed and persisted for job {job_id}")
        except Exception as e:
            logger.error(f"Background 2-agent tailoring failed for job {job_id}: {e}")
            query_svc.save_tailored_pack(job_id, {
                "status": "failed",
                "job_id": job_id,
                "error": str(e),
            })

    background_tasks.add_task(_execute)
    return in_progress_state


# -------------------------------------------------------------------------
# Candidate Profile & PII Endpoints
# -------------------------------------------------------------------------

@app.get("/api/profile")
def get_candidate_profile():
    p = profile_svc.get_active_profile()
    if not p:
        raise HTTPException(status_code=404, detail="Active profile not found")
    return p


@app.put("/api/profile")
def update_candidate_profile(req: UpdateProfileRequest):
    data = req.model_dump()
    success = profile_svc.update_profile(data)
    if not success:
        raise HTTPException(status_code=400, detail="Failed to update candidate profile in database")
    return {"status": "success", "message": "Profile updated successfully"}


@app.get("/api/profile/markdown")
def get_candidate_profile_markdown(masked: Optional[bool] = Query(None)):
    p = profile_svc.get_active_profile()
    md = profile_svc.export_profile_to_markdown(p)
    return {"markdown": md}


@app.post("/api/profile/upload-resume")
async def upload_candidate_resume(file: UploadFile = File(...)):
    """
    Ingests PDF, DOCX, or text resume file, extracts structured profile facts,
    and updates the active candidate master profile.
    """
    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty (0 bytes).")

        ext = os.path.splitext(file.filename or "")[1].lower()
        if ext not in [".pdf", ".docx", ".txt"]:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file format '{ext}'. Please upload a PDF (.pdf), Word document (.docx), or plain text resume.",
            )

        if ext == ".txt":
            raw_text = content.decode("utf-8", errors="ignore")
        else:
            parse_result = ResumeParser.parse_bytes(content, file.filename or "resume.pdf")
            raw_text = parse_result.extracted_text

        if not raw_text.strip():
            raise HTTPException(status_code=400, detail="Could not extract readable text from resume.")

        # Structured extraction (AI + heuristic fallback)
        extracted = ResumeParser.extract_structured_profile(raw_text, ai_gateway=ai_gateway)

        # Merge with existing master_latex_source if already present
        existing_profile = profile_svc.get_active_profile()
        if existing_profile and existing_profile.get("master_latex_source"):
            extracted["master_latex_source"] = existing_profile["master_latex_source"]

        # Persist to database
        success = profile_svc.update_profile(extracted)
        if not success:
            raise HTTPException(status_code=500, detail="Failed to save extracted profile to database.")

        return {
            "status": "success",
            "message": f"Successfully parsed {len(extracted.get('projects', []))} projects and {len(extracted.get('work_history', []))} experiences.",
            "profile": extracted,
        }
    except ResumeParseError as e:
        logger.error(f"Resume parsing validation failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to process resume upload: {e}")
        raise HTTPException(status_code=500, detail=f"Resume processing error: {str(e)}")


# -------------------------------------------------------------------------
# LaTeX PDF Compilation Endpoints
# -------------------------------------------------------------------------

@app.post("/api/compile-pdf")
def compile_latex_to_pdf(req: CompilePdfRequest):
    """
    Directly compiles exact LaTeX source into an ATS-compliant PDF.
    Returns base64 encoded PDF binary and full ATS audit breakdown.
    """
    try:
        pdf_bytes = pdf_engine.compile_latex_to_pdf(req.latex_code)
        audit = pdf_engine.verify_pdf_ats_compliance(pdf_bytes, expected_max_pages=2)
        
        b64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")
        
        return {
            "status": "success",
            "ats_score": audit.ats_score,
            "passed": audit.passed,
            "page_count": audit.page_count,
            "warnings": audit.warnings,
            "extracted_contacts": audit.extracted_contacts,
            "pdf_base64": b64_pdf,
        }
    except Exception as e:
        logger.error(f"LaTeX PDF compilation failed: {e}")
        raise HTTPException(status_code=400, detail=f"Compilation error: {str(e)}")


@app.post("/api/download-pdf")
def download_pdf(req: CompilePdfRequest):
    """Returns raw application/pdf binary for direct browser download."""
    pdf_bytes = pdf_engine.compile_latex_to_pdf(req.latex_code)
    
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=Tailored_Resume.pdf"},
    )


# -------------------------------------------------------------------------
# AI Model Registry & Quotas Endpoints
# -------------------------------------------------------------------------

@app.get("/api/models")
def get_registered_models():
    try:
        models = ai_gateway.get_registered_models(active_only=False)
        return {"models": models}
    except Exception as e:
        logger.error(f"Failed to fetch models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/models/reorder")
def reorder_models(req: ReorderModelsRequest):
    try:
        success = ai_gateway.reorder_models(req.model_ids)
        if not success:
            raise HTTPException(status_code=400, detail="Failed to reorder models")
        return {"status": "success", "message": "Models reordered successfully"}
    except Exception as e:
        logger.error(f"Failed to reorder models: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/models/{model_id}")
def update_model_settings(model_id: int, settings: Dict[str, Any]):
    try:
        conn = ai_gateway.get_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE ai_model_registry
                    SET is_active = COALESCE(%s, is_active),
                        priority = COALESCE(%s, priority),
                        rpd_limit = COALESCE(%s, rpd_limit),
                        rpm_limit = COALESCE(%s, rpm_limit),
                        updated_at = NOW()
                    WHERE id = %s;
                    """,
                    (
                        settings.get("is_active"),
                        settings.get("priority"),
                        settings.get("rpd_limit"),
                        settings.get("rpm_limit"),
                        model_id,
                    )
                )
        return {"status": "success", "model_id": model_id}
    except Exception as e:
        logger.error(f"Failed to update model {model_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/models")
def add_new_model(req: AddModelRequest):
    try:
        success = ai_gateway.add_model(
            provider=req.provider,
            model_id=req.model_id,
            api_key_alias=req.api_key_alias,
            rpm_limit=req.rpm_limit,
            tpm_limit=req.tpm_limit,
            rpd_limit=req.rpd_limit,
            priority=req.priority,
        )
        if not success:
            raise HTTPException(status_code=400, detail="Failed to add model to registry")
        return {"status": "success", "message": f"Model {req.model_id} added successfully"}
    except Exception as e:
        logger.error(f"Failed to add model: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/models/{model_id}")
def delete_model(model_id: int):
    try:
        success = ai_gateway.delete_model(model_id)
        if not success:
            raise HTTPException(status_code=400, detail=f"Failed to delete model {model_id}")
        return {"status": "success", "model_id": model_id}
    except Exception as e:
        logger.error(f"Failed to delete model {model_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# API Keys & Provider Secrets Endpoints
# -------------------------------------------------------------------------

@app.get("/api/keys")
def get_api_keys():
    try:
        return {"keys": env_manager.get_keys_status()}
    except Exception as e:
        logger.error(f"Failed to fetch keys: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/keys")
def save_api_key(req: SaveKeyRequest):
    try:
        env_manager.save_key(req.alias, req.key_value)
        return {"status": "success", "alias": req.alias, "masked": env_manager.mask_key(req.key_value)}
    except Exception as e:
        logger.error(f"Failed to save key {req.alias}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# -------------------------------------------------------------------------
# Prompts & Rules Catalog Endpoints
# -------------------------------------------------------------------------

@app.get("/api/prompts")
def list_prompts():
    prompt_files = [
        "01-candidate-profile.md",
        "02-behavioral-profile.md",
        "03-writing-style.md",
        "04-job-evaluation.md",
        "05-cv-templates.md",
        "06-cover-letter-templates.md",
        "07-interview-prep.md",
        "08-application-forms.md",
        "09-web-research.md",
        "apply.md",
    ]
    catalog = []
    for f in prompt_files:
        catalog.append({
            "filename": f,
            "content": load_prompt_file(f),
        })
    return {"prompts": catalog}


# -------------------------------------------------------------------------
# Production Next.js Static Export Mounting
# -------------------------------------------------------------------------

out_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "out")
if os.path.exists(out_dir):
    logger.info(f"Mounting production Next.js build from {out_dir}")
    app.mount("/", StaticFiles(directory=out_dir, html=True), name="static")
