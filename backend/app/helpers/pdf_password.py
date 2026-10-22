"""Open password-protected PDFs locally (dev-docs/cas-import.md §3.1).

Indian bank, card and mutual fund statements are almost always password-protected.
``decrypt_pdf`` removes the encryption in memory with pypdfium2 (already a dependency),
so the AI import can extract text or send a readable PDF to a native-PDF provider. The
password is used here and dropped; it is never stored or logged.
"""

from __future__ import annotations

import io
from typing import Optional

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from app import error_codes as ec
from app.exceptions import APIError


def decrypt_pdf(content: bytes, password: Optional[str]) -> bytes:
    """Return ``content`` unchanged when it isn't encrypted, else a decrypted copy.
    Raises ``APIError`` ``PDF_PASSWORD_REQUIRED`` / ``PDF_WRONG_PASSWORD`` (422)."""
    try:
        doc = pdfium.PdfDocument(content)
        doc.close()
        return content
    except pdfium.PdfiumError as e:
        if "password" not in str(e).lower():
            return content  # not an encryption problem; let the normal path report it
    if not password:
        raise APIError(
            "This PDF is password-protected. Enter its password and try again.",
            code=ec.PDF_PASSWORD_REQUIRED, status_code=422,
        )
    try:
        doc = pdfium.PdfDocument(content, password=password)
    except pdfium.PdfiumError:
        raise APIError("Wrong PDF password.", code=ec.PDF_WRONG_PASSWORD, status_code=422)
    try:
        out = io.BytesIO()
        doc.save(out, flags=pdfium_c.FPDF_REMOVE_SECURITY)
        return out.getvalue()
    finally:
        doc.close()
