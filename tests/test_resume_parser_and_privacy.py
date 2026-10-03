import io
import logging
import zipfile
import pytest
from pypdf import PdfWriter

from src.resume_parser import (
    ResumeParser,
    EmptyFileError,
    FileTooLargeError,
    UnsupportedFileFormatError,
    MalformedFileError,
    MAX_RESUME_FILE_SIZE_BYTES,
)
from src.privacy_scrubber import PrivacyScrubber


def create_minimal_pdf_bytes(text: str = "Hello World PDF Content") -> bytes:
    """Creates a valid PDF binary in-memory with text content."""
    from pypdf import PageObject
    # We can write minimal PDF using PdfWriter
    writer = PdfWriter()
    # Create a blank page
    page = PageObject.create_blank_page(width=200, height=200)
    writer.add_page(page)
    # Note: blank page has no text, so to test text extraction with pypdf without external fonts,
    # we can inject a mock page or basic text stream.
    # Alternatively, PdfWriter allows adding annotation or text:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def create_minimal_docx_bytes(text: str) -> bytes:
    """Creates a valid in-memory DOCX zip package with word/document.xml containing given text."""
    xml_content = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
    <w:body>
        <w:p>
            <w:r>
                <w:t>{text}</w:t>
            </w:r>
        </w:p>
    </w:body>
</w:document>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", xml_content.encode("utf-8"))
    return buf.getvalue()


class TestResumeParser:
    def test_empty_file_raises_empty_file_error(self):
        with pytest.raises(EmptyFileError) as exc_info:
            ResumeParser.parse_bytes(b"", "resume.pdf")
        assert "is empty" in str(exc_info.value)

    def test_oversized_file_raises_file_too_large_error(self):
        large_bytes = b"0" * (MAX_RESUME_FILE_SIZE_BYTES + 1024)
        with pytest.raises(FileTooLargeError) as exc_info:
            ResumeParser.parse_bytes(large_bytes, "huge_resume.pdf")
        assert "exceeds maximum allowed limit" in str(exc_info.value)

    def test_unsupported_extension_raises_error(self):
        with pytest.raises(UnsupportedFileFormatError) as exc_info:
            ResumeParser.parse_bytes(b"some content", "resume.txt")
        assert "Unsupported format '.txt'" in str(exc_info.value)

    def test_malformed_pdf_raises_malformed_file_error(self):
        corrupt_bytes = b"%PDF-1.4 CORRUPTED AND TRUNCATED CONTENT"
        with pytest.raises(MalformedFileError) as exc_info:
            ResumeParser.parse_bytes(corrupt_bytes, "corrupt.pdf")
        assert "Failed to read PDF" in str(exc_info.value)

    def test_malformed_docx_bad_zip_raises_malformed_file_error(self):
        corrupt_zip = b"PK\x03\x04This is not a valid zip archive"
        with pytest.raises(MalformedFileError) as exc_info:
            ResumeParser.parse_bytes(corrupt_zip, "corrupt.docx")
        assert "not a valid zip/docx archive" in str(exc_info.value)

    def test_docx_missing_document_xml_raises_malformed_file_error(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("some_other_file.txt", "unrelated data")
        with pytest.raises(MalformedFileError) as exc_info:
            ResumeParser.parse_bytes(buf.getvalue(), "missing_doc.docx")
        assert "missing word/document.xml" in str(exc_info.value)

    def test_valid_docx_extraction_success(self, caplog):
        sample_text = "Experienced Java Developer specializing in Spring Boot and Kafka."
        docx_bytes = create_minimal_docx_bytes(sample_text)

        with caplog.at_level(logging.INFO):
            result = ResumeParser.parse_bytes(docx_bytes, "abhishek_resume.docx")

        assert result.file_name == "abhishek_resume.docx"
        assert result.file_type == "docx"
        assert result.extracted_text == sample_text
        assert result.char_count == len(sample_text)
        assert result.word_count == 9
        assert len(result.content_hash) == 64

        # Privacy check: Verify logger NEVER logged the actual resume text
        for record in caplog.records:
            assert sample_text not in record.message
            assert "Spring Boot" not in record.message

    def test_pdf_magic_bytes_mismatch_raises_malformed_error(self):
        # A file with .pdf extension that doesn't start with %PDF
        fake_pdf = b"NOT_A_PDF_HEADER and some text"
        with pytest.raises(MalformedFileError) as exc_info:
            ResumeParser.parse_bytes(fake_pdf, "fake.pdf")
        assert "lacks %PDF header signature" in str(exc_info.value)

    def test_docx_magic_bytes_mismatch_raises_malformed_error(self):
        # A file with .docx extension that doesn't start with PK\x03\x04
        fake_docx = b"NOT_A_ZIP_HEADER and some text"
        with pytest.raises(MalformedFileError) as exc_info:
            ResumeParser.parse_bytes(fake_docx, "fake.docx")
        assert "lacks valid PK ZIP header signature" in str(exc_info.value)

    def test_empty_textual_content_docx_raises_malformed_error(self):
        empty_docx_bytes = create_minimal_docx_bytes("   ")
        with pytest.raises(MalformedFileError) as exc_info:
            ResumeParser.parse_bytes(empty_docx_bytes, "empty_text.docx")
        assert "contains no readable textual content" in str(exc_info.value)

    def test_raw_bytes_retained_in_parse_result(self):
        sample_text = "Java Developer Spring Boot AWS."
        docx_bytes = create_minimal_docx_bytes(sample_text)
        result = ResumeParser.parse_bytes(docx_bytes, "resume.docx")
        assert result.file_bytes == docx_bytes


class TestPrivacyScrubber:
    def test_redacts_email_and_phone(self):
        text = (
            "Contact me at abhishek.dwivedi99@example.com or +91-9876543210 for Java opportunities. "
            "Graduated in 2020-2024 with B.Tech IT."
        )
        scrubbed, counts = PrivacyScrubber.scrub_text(text)

        assert "[REDACTED_EMAIL]" in scrubbed
        assert "abhishek.dwivedi99@example.com" not in scrubbed
        assert "[REDACTED_PHONE]" in scrubbed
        assert "9876543210" not in scrubbed
        assert counts["emails"] == 1
        assert counts["phones"] == 1
        # Crucial: Year range 2020-2024 should NOT be redacted as a phone number
        assert "2020-2024" in scrubbed

    def test_redacts_indian_pin_codes(self):
        text = "Residing in Bengaluru, Karnataka 560100. Open to relocation to Pune 411057."
        scrubbed, counts = PrivacyScrubber.scrub_text(text)

        assert "560100" not in scrubbed
        assert "411057" not in scrubbed
        assert "[REDACTED_PIN]" in scrubbed
        assert counts["pins"] == 2
        assert "Bengaluru" in scrubbed

    def test_redacts_social_profile_links(self):
        text = (
            "Portfolio: https://github.com/abhishek94443 and LinkedIn: "
            "https://www.linkedin.com/in/abhishek-dwivedi-dev. Spring Boot expert."
        )
        scrubbed, counts = PrivacyScrubber.scrub_text(text)

        assert "abhishek94443" not in scrubbed
        assert "abhishek-dwivedi-dev" not in scrubbed
        assert "[REDACTED_PROFILE_URL]" in scrubbed
        assert counts["links"] == 2
        assert "Spring Boot expert." in scrubbed

    def test_redacts_candidate_name_when_provided(self):
        text = "Abhishek Dwivedi is an Associate Software Engineer with 2 years of experience."
        scrubbed, counts = PrivacyScrubber.scrub_text(text, candidate_name="Abhishek Dwivedi")

        assert "Abhishek Dwivedi" not in scrubbed
        assert "[CANDIDATE_NAME]" in scrubbed
        assert counts["name_redacted"] is True
        assert "Associate Software Engineer with 2 years of experience." in scrubbed

    def test_empty_text_handled_safely(self):
        scrubbed, counts = PrivacyScrubber.scrub_text("")
        assert scrubbed == ""
        assert counts["emails"] == 0
        assert counts["phones"] == 0
