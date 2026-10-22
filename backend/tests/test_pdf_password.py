"""decrypt_pdf — password-protected PDFs opened locally for the AI import (§3.1)."""

import io

import pypdfium2 as pdfium

from app.helpers.pdf_password import decrypt_pdf


def _blank_pdf() -> bytes:
    doc = pdfium.PdfDocument.new()
    doc.new_page(200, 200)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_an_unencrypted_pdf_passes_through_unchanged():
    pdf = _blank_pdf()
    assert decrypt_pdf(pdf, None) == pdf
    assert decrypt_pdf(pdf, "ignored") == pdf


def test_non_pdf_bytes_are_left_for_the_normal_path_to_report():
    assert decrypt_pdf(b"not a pdf", None) == b"not a pdf"
