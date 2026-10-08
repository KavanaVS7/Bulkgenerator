"""PDF certificate generation using ReportLab.

There is exactly ONE predefined template, defined by the drawing code below.
`CertificateGenerator` is just a callable type so tests can swap in a failing one.
"""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Callable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

# (recipient_name, course_title, issuer_name, issue_date, output_path) -> None
CertificateGenerator = Callable[[str, str, str, date, Path], None]

PAGE_W, PAGE_H = landscape(A4)
NAVY = colors.HexColor("#1f3a5f")
GOLD = colors.HexColor("#b8892b")


def _fit_font_size(text: str, font: str, max_size: float, max_width: float) -> float:
    """Shrink the font until `text` fits `max_width` so long names never overflow the page."""
    size = max_size
    while size > 12 and stringWidth(text, font, size) > max_width:
        size -= 1
    return size


def generate_certificate_pdf(
    recipient_name: str,
    course_title: str,
    issuer_name: str,
    issue_date: date,
    out_path: Path,
) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to a temp file then atomically rename: a crash mid-write can never leave a
    # half-written PDF at the final path that the API would then happily serve.
    tmp_path = out_path.with_suffix(".pdf.tmp")
    c = canvas.Canvas(str(tmp_path), pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Certificate - {recipient_name}")
    c.setAuthor(issuer_name)

    cx = PAGE_W / 2
    text_w = PAGE_W - 160

    # Double border
    c.setStrokeColor(GOLD)
    c.setLineWidth(6)
    c.rect(24, 24, PAGE_W - 48, PAGE_H - 48)
    c.setStrokeColor(NAVY)
    c.setLineWidth(1.5)
    c.rect(38, 38, PAGE_W - 76, PAGE_H - 76)

    c.setFillColor(NAVY)
    c.setFont("Times-Bold", 40)
    c.drawCentredString(cx, PAGE_H - 120, "CERTIFICATE OF COMPLETION")

    c.setFillColor(colors.HexColor("#444444"))
    c.setFont("Times-Italic", 16)
    c.drawCentredString(cx, PAGE_H - 175, "This is to certify that")

    name_size = _fit_font_size(recipient_name, "Times-BoldItalic", 38, text_w)
    c.setFillColor(GOLD)
    c.setFont("Times-BoldItalic", name_size)
    c.drawCentredString(cx, PAGE_H - 235, recipient_name)
    c.setStrokeColor(GOLD)
    c.setLineWidth(1)
    c.line(cx - 220, PAGE_H - 248, cx + 220, PAGE_H - 248)

    c.setFillColor(colors.HexColor("#444444"))
    c.setFont("Times-Italic", 16)
    c.drawCentredString(cx, PAGE_H - 285, "has successfully completed")

    course_size = _fit_font_size(course_title, "Times-Bold", 26, text_w)
    c.setFillColor(NAVY)
    c.setFont("Times-Bold", course_size)
    c.drawCentredString(cx, PAGE_H - 330, course_title)

    # Footer: date on the left, issuer on the right
    c.setFillColor(colors.HexColor("#333333"))
    c.setFont("Times-Roman", 14)
    c.drawString(90, 90, f"Date: {issue_date.strftime('%d %B %Y')}")
    c.drawRightString(PAGE_W - 90, 90, issuer_name)
    c.setStrokeColor(colors.HexColor("#333333"))
    c.line(PAGE_W - 290, 108, PAGE_W - 90, 108)
    c.setFont("Times-Italic", 10)
    c.drawRightString(PAGE_W - 90, 74, "Issued by")

    c.showPage()
    c.save()
    os.replace(tmp_path, out_path)  # atomic on the same filesystem
