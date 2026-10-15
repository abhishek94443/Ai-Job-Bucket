import hashlib
import io
import logging
import os
import xml.etree.ElementTree as ET
import zipfile
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pypdf import PdfReader
from pypdf.errors import PdfReadError

logger = logging.getLogger(__name__)

# Constants
MAX_RESUME_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB limit
SUPPORTED_EXTENSIONS = {".pdf", ".docx"}

# Magic byte signatures
PDF_MAGIC_BYTES = b"%PDF"
ZIP_MAGIC_BYTES = b"PK\x03\x04"


class ResumeParseError(Exception):
    """Base exception for resume parsing errors."""
    pass


class EmptyFileError(ResumeParseError):
    """Raised when the uploaded file contains 0 bytes."""
    pass


class FileTooLargeError(ResumeParseError):
    """Raised when the uploaded file exceeds size limits."""
    pass


class UnsupportedFileFormatError(ResumeParseError):
    """Raised when file extension or format is unsupported."""
    pass


class MalformedFileError(ResumeParseError):
    """Raised when file binary cannot be read, unpacked, or fails magic byte validation."""
    pass


@dataclass
class ResumeParseResult:
    """Parsed resume payload and metadata (never logs or exposes text without explicit caller request)."""
    file_name: str
    file_type: str  # 'pdf' or 'docx'
    file_size_bytes: int
    content_hash: str  # SHA-256 hex digest
    file_bytes: bytes  # Retained raw binary for mandatory persistence
    extracted_text: str
    char_count: int
    word_count: int
    page_count: Optional[int] = None


class ResumeParser:
    """
    Production-grade resume parser supporting PDF and DOCX formats.
    - PDF extraction powered by pypdf
    - DOCX extraction using Python standard-library zipfile and xml.etree (zero third-party dependency)
    - Validates file extensions AND file magic bytes (%PDF, PK\x03\x04).
    - Enforces size boundaries (<= 5MB), non-empty files, and strict privacy logging.
    """

    @classmethod
    def parse_bytes(cls, file_bytes: bytes, file_name: str) -> ResumeParseResult:
        """
        Parses in-memory binary resume file.
        Does NOT log resume content.
        """
        if not file_bytes or len(file_bytes) == 0:
            raise EmptyFileError(f"File '{file_name}' is empty (0 bytes).")

        size_bytes = len(file_bytes)
        if size_bytes > MAX_RESUME_FILE_SIZE_BYTES:
            raise FileTooLargeError(
                f"File '{file_name}' ({size_bytes / (1024 * 1024):.2f} MB) exceeds maximum allowed limit of 5 MB."
            )

        ext = os.path.splitext(file_name.lower())[1]
        if ext not in SUPPORTED_EXTENSIONS:
            raise UnsupportedFileFormatError(
                f"Unsupported format '{ext}'. Only .pdf and .docx documents are supported."
            )

        content_hash = hashlib.sha256(file_bytes).hexdigest()

        # Validate magic bytes
        if ext == ".pdf":
            if not file_bytes.startswith(PDF_MAGIC_BYTES):
                raise MalformedFileError(
                    f"File '{file_name}' has .pdf extension but lacks %PDF header signature."
                )
            extracted_text, page_count = cls._extract_pdf(file_bytes, file_name)
            file_type = "pdf"
        elif ext == ".docx":
            if not file_bytes.startswith(ZIP_MAGIC_BYTES):
                raise MalformedFileError(
                    f"File '{file_name}' has .docx extension but lacks valid PK ZIP header signature."
                )
            extracted_text = cls._extract_docx(file_bytes, file_name)
            page_count = None
            file_type = "docx"
        else:
            raise UnsupportedFileFormatError(f"Unhandled file extension: {ext}")

        cleaned_text = cls._clean_text(extracted_text)
        if not cleaned_text:
            raise MalformedFileError(
                f"File '{file_name}' contains no readable textual content (e.g., scanned image or empty document)."
            )

        char_count = len(cleaned_text)
        word_count = len(cleaned_text.split())

        logger.info(
            "Successfully parsed resume metadata: file_name=%s, type=%s, size_bytes=%d, hash=%s, chars=%d, words=%d",
            file_name,
            file_type,
            size_bytes,
            content_hash[:12],
            char_count,
            word_count,
        )

        return ResumeParseResult(
            file_name=file_name,
            file_type=file_type,
            file_size_bytes=size_bytes,
            content_hash=content_hash,
            file_bytes=file_bytes,
            extracted_text=cleaned_text,
            char_count=char_count,
            word_count=word_count,
            page_count=page_count,
        )

    @classmethod
    def parse_file(cls, file_path: str) -> ResumeParseResult:
        """Reads file from disk and parses it."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Resume file not found at: {file_path}")

        file_name = os.path.basename(file_path)
        with open(file_path, "rb") as f:
            file_bytes = f.read()

        return cls.parse_bytes(file_bytes, file_name)

    @staticmethod
    def _extract_pdf(file_bytes: bytes, file_name: str) -> tuple[str, int]:
        """Extracts text from PDF binary using pypdf."""
        try:
            stream = io.BytesIO(file_bytes)
            reader = PdfReader(stream)
            page_count = len(reader.pages)
            if page_count == 0:
                raise MalformedFileError(f"PDF file '{file_name}' contains zero pages.")

            pages_text = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                pages_text.append(text)

            combined_text = "\n\n".join(pages_text)
            return combined_text, page_count
        except (PdfReadError, Exception) as exc:
            if isinstance(exc, ResumeParseError):
                raise
            raise MalformedFileError(f"Failed to read PDF '{file_name}': {str(exc)}") from exc

    @staticmethod
    def _extract_docx(file_bytes: bytes, file_name: str) -> str:
        """
        Extracts text from DOCX binary using Python standard library (zipfile + xml.etree).
        A DOCX file is an Open Packaging Conventions (OPC) zip container.
        Document body is in 'word/document.xml'.
        """
        try:
            stream = io.BytesIO(file_bytes)
            with zipfile.ZipFile(stream) as docx_zip:
                namelist = docx_zip.namelist()
                if "word/document.xml" not in namelist:
                    raise MalformedFileError(
                        f"DOCX '{file_name}' is missing word/document.xml. File may be corrupted or not a valid Word document."
                    )

                xml_content = docx_zip.read("word/document.xml")

            tree = ET.fromstring(xml_content)
            namespaces = {
                "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
            }

            paragraphs = []
            for p in tree.iterfind(".//w:p", namespaces):
                texts = [node.text for node in p.iterfind(".//w:t", namespaces) if node.text]
                if texts:
                    paragraphs.append("".join(texts))

            return "\n\n".join(paragraphs)
        except zipfile.BadZipFile as exc:
            raise MalformedFileError(f"File '{file_name}' is not a valid zip/docx archive: {str(exc)}") from exc
        except ET.ParseError as exc:
            raise MalformedFileError(f"Malformed XML in DOCX '{file_name}': {str(exc)}") from exc
        except Exception as exc:
            if isinstance(exc, ResumeParseError):
                raise
            raise MalformedFileError(f"Failed to extract DOCX '{file_name}': {str(exc)}") from exc

    @staticmethod
    def _clean_text(text: str) -> str:
        """Normalizes unicode whitespace and removes control characters while preserving lines."""
        if not text:
            return ""

        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = [line.strip() for line in text.split("\n")]
        cleaned_lines = []
        blank_count = 0
        for line in lines:
            if not line:
                blank_count += 1
                if blank_count <= 2:
                    cleaned_lines.append("")
            else:
                blank_count = 0
                cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip()

    @classmethod
    def extract_structured_profile(cls, raw_text: str, ai_gateway: Optional[Any] = None) -> Dict[str, Any]:
        """
        Transforms raw resume text into the canonical candidate_master_profile schema.
        First attempts AI extraction via ai_gateway if provided;
        falls back to deterministic regex and section heuristic parsing.
        """
        contacts = cls._extract_contacts(raw_text)

        if ai_gateway:
            try:
                ai_profile = cls._extract_with_ai(raw_text, ai_gateway)
                if ai_profile and isinstance(ai_profile, dict):
                    for k, v in contacts.items():
                        if not ai_profile.get(k) and v:
                            ai_profile[k] = v
                    return ai_profile
            except Exception as e:
                logger.warning(f"AI resume structuring failed, using deterministic parser: {e}")

        return cls._extract_deterministic(raw_text, contacts)

    @classmethod
    def _extract_contacts(cls, text: str) -> Dict[str, str]:
        """Extracts contact coordinates deterministically from text."""
        result = {
            "candidate_name": "",
            "email": "",
            "phone": "",
            "linkedin_url": "",
            "github_url": "",
            "location": "",
        }

        # Email
        email_m = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", text)
        if email_m:
            result["email"] = email_m.group(0)

        # Phone
        phone_m = re.search(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3,5}\)?[-.\s]?\d{3,5}[-.\s]?\d{3,5}\b", text)
        if phone_m:
            val = phone_m.group(0).strip()
            if sum(c.isdigit() for c in val) >= 10:
                result["phone"] = val

        # LinkedIn
        li_m = re.search(r"(?:https?://)?(?:www\.)?linkedin\.com/in/([A-Za-z0-9_-]+)", text, re.IGNORECASE)
        if li_m:
            result["linkedin_url"] = f"https://linkedin.com/in/{li_m.group(1)}"

        # GitHub
        gh_m = re.search(r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9_-]+)", text, re.IGNORECASE)
        if gh_m:
            result["github_url"] = f"https://github.com/{gh_m.group(1)}"

        # Name: Scan first 5 lines
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in lines[:5]:
            # Skip noise lines
            if any(term in line.lower() for term in ["resume", "curriculum", "vitae", "page ", "@", "http", "phone"]):
                continue
            # If line is short and alphabetic, treat as name
            clean_line = re.sub(r"[^A-Za-z\s.]", "", line).strip()
            if 2 <= len(clean_line.split()) <= 4 and len(clean_line) <= 40:
                result["candidate_name"] = clean_line
                break

        return result

    @classmethod
    def _extract_with_ai(cls, text: str, ai_gateway: Any) -> Optional[Dict[str, Any]]:
        """Invokes AI Gateway to extract structured profile JSON from resume text."""
        prompt = f"""You are a professional resume parser for an engineering career platform.
Extract all candidate details from the resume text below into a clean JSON object.

RESUME TEXT:
{text[:8000]}

SCHEMA REQUIREMENT (JSON only, no markdown, no em-dashes):
{{
  "candidate_name": "Full Name",
  "phone": "Phone number or empty string",
  "email": "Email address or empty string",
  "location": "City, Country or empty string",
  "linkedin_url": "LinkedIn URL or empty string",
  "github_url": "GitHub URL or empty string",
  "status": "Actively Looking",
  "constraints": "Open to Remote",
  "technical_skills": {{
    "languages": ["Java", "Python", ...],
    "frameworks": ["Spring Boot", "FastAPI", ...],
    "databases": ["PostgreSQL", ...],
    "devops_cloud": ["Docker", "Kubernetes", "AWS", ...],
    "tools": ["Git", "Linux", ...]
  }},
  "work_history": [
    {{
      "company": "Company Name",
      "role": "Job Title",
      "period": "Start - End Date",
      "location": "Location",
      "bullets": ["Bullet 1", "Bullet 2"]
    }}
  ],
  "projects": [
    {{
      "name": "Project Name",
      "tech_stack": "Technologies used",
      "github_url": "URL if present",
      "bullets": ["Bullet 1", "Bullet 2"]
    }}
  ],
  "education": [
    {{
      "degree": "Degree",
      "period": "Dates",
      "institution": "University / College",
      "topics": "Key Topics / Major"
    }}
  ],
  "languages": [
    {{
      "language": "English",
      "level": "Fluent",
      "notes": ""
    }}
  ]
}}
"""
        resp = ai_gateway.execute_generation(prompt, timeout_seconds=30.0)
        raw_out = resp.get("text", "")
        # Parse JSON
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_out, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        start = raw_out.find("{")
        end = raw_out.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw_out[start : end + 1])
        return None

    @classmethod
    def _extract_deterministic(cls, text: str, contacts: Dict[str, str]) -> Dict[str, Any]:
        """Heuristic section parser for offline or zero-AI mode."""
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        sections: Dict[str, List[str]] = {}
        current_section = "header"
        sections[current_section] = []

        section_triggers = {
            "skills": ["skills", "technical skills", "technologies", "core competencies"],
            "experience": ["experience", "work experience", "employment history", "professional experience"],
            "projects": ["projects", "personal projects", "academic projects", "key projects"],
            "education": ["education", "academic background", "academics"],
            "languages": ["languages", "spoken languages"],
        }

        for line in lines:
            lower = line.lower().strip(":").strip()
            matched = False
            for sec_name, triggers in section_triggers.items():
                if lower in triggers:
                    current_section = sec_name
                    if current_section not in sections:
                        sections[current_section] = []
                    matched = True
                    break
            if not matched:
                sections.setdefault(current_section, []).append(line)

        # Parse skills
        skills: Dict[str, List[str]] = {
            "languages": [],
            "frameworks": [],
            "databases": [],
            "devops_cloud": [],
            "tools": [],
        }
        for s_line in sections.get("skills", []):
            parts = re.split(r"[:,]\s*", s_line)
            cat = "tools"
            low_p0 = parts[0].lower()
            if "language" in low_p0:
                cat = "languages"
            elif "framework" in low_p0 or "library" in low_p0:
                cat = "frameworks"
            elif "database" in low_p0 or "data" in low_p0:
                cat = "databases"
            elif "cloud" in low_p0 or "devops" in low_p0 or "infra" in low_p0:
                cat = "devops_cloud"
            
            items = [item.strip() for item in parts[1:] if item.strip()]
            if not items and len(parts) > 1:
                items = [parts[1].strip()]
            if items:
                skills[cat].extend(items)
            else:
                # Add individual words or comma-separated tokens
                for tok in s_line.split(","):
                    t = tok.strip()
                    if t and len(t) < 30:
                        skills["tools"].append(t)

        # Parse projects
        projects: List[Dict[str, Any]] = []
        current_proj: Optional[Dict[str, Any]] = None
        for p_line in sections.get("projects", []):
            if p_line.startswith(("-", "*", "•")):
                bullet = p_line.lstrip("-*• ").strip()
                if current_proj:
                    current_proj["bullets"].append(bullet)
            else:
                if current_proj and current_proj.get("name"):
                    projects.append(current_proj)
                # New project header
                current_proj = {
                    "name": p_line,
                    "tech_stack": "",
                    "github_url": "",
                    "bullets": [],
                }
                # Check for tech stack in parentheses
                ts_m = re.search(r"\((.*?)\)", p_line)
                if ts_m:
                    current_proj["tech_stack"] = ts_m.group(1)
                    current_proj["name"] = p_line.replace(f"({ts_m.group(1)})", "").strip()

        if current_proj and current_proj.get("name"):
            projects.append(current_proj)

        # Parse experience
        work_history: List[Dict[str, Any]] = []
        current_work: Optional[Dict[str, Any]] = None
        for e_line in sections.get("experience", []):
            if e_line.startswith(("-", "*", "•")):
                bullet = e_line.lstrip("-*• ").strip()
                if current_work:
                    current_work["bullets"].append(bullet)
            else:
                # Possible role / company line
                if current_work and current_work.get("company"):
                    work_history.append(current_work)
                current_work = {
                    "company": e_line,
                    "role": "Software Engineer",
                    "period": "",
                    "location": "",
                    "bullets": [],
                }
                # Check for pipe or dash
                if " | " in e_line:
                    comp_parts = e_line.split(" | ")
                    current_work["company"] = comp_parts[0].strip()
                    if len(comp_parts) > 1:
                        current_work["role"] = comp_parts[1].strip()
                    if len(comp_parts) > 2:
                        current_work["period"] = comp_parts[2].strip()

        if current_work and current_work.get("company"):
            work_history.append(current_work)

        # Parse education
        education: List[Dict[str, Any]] = []
        for ed_line in sections.get("education", []):
            if ed_line.startswith(("-", "*", "•")):
                continue
            education.append({
                "degree": ed_line,
                "period": "",
                "institution": "",
                "topics": "",
            })

        return {
            "candidate_name": contacts.get("candidate_name") or "Candidate Name",
            "phone": contacts.get("phone") or "",
            "email": contacts.get("email") or "",
            "location": contacts.get("location") or "City, Country",
            "linkedin_url": contacts.get("linkedin_url") or "",
            "github_url": contacts.get("github_url") or "",
            "status": "Actively Looking",
            "constraints": "Open to Remote",
            "technical_skills": skills,
            "work_history": work_history,
            "projects": projects,
            "education": education,
            "languages": [{"language": "English", "level": "Fluent", "notes": ""}],
            "master_latex_source": "",
            "behavioral_profile_md": "",
        }
