import hashlib
import io
import logging
import os
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Optional

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
