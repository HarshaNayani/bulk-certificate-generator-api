from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas


def generate_certificate(name: str, event_name: str, issuer: str, out_path: Path) -> None:
    """Render the single predefined certificate template to a PDF file."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    w, h = landscape(A4)
    c = canvas.Canvas(str(out_path), pagesize=(w, h))
    c.setTitle(f"Certificate - {name}")

    # border
    c.setStrokeColor(colors.HexColor("#1F3A5F"))
    c.setLineWidth(6)
    c.rect(25, 25, w - 50, h - 50)
    c.setLineWidth(1.5)
    c.rect(38, 38, w - 76, h - 76)

    c.setFillColor(colors.HexColor("#1F3A5F"))
    c.setFont("Helvetica-Bold", 38)
    c.drawCentredString(w / 2, h - 130, "CERTIFICATE OF COMPLETION")

    c.setFillColor(colors.black)
    c.setFont("Helvetica", 16)
    c.drawCentredString(w / 2, h - 190, "This is proudly presented to")

    c.setFont("Helvetica-Bold", 34)
    c.drawCentredString(w / 2, h - 250, name)

    c.setFont("Helvetica", 16)
    c.drawCentredString(w / 2, h - 300, "for successfully completing")

    c.setFont("Helvetica-Bold", 24)
    c.drawCentredString(w / 2, h - 340, event_name)

    c.setFont("Helvetica", 13)
    c.drawString(80, 80, f"Date: {date.today():%d %b %Y}")
    if issuer:
        c.drawRightString(w - 80, 80, f"Issued by: {issuer}")

    c.showPage()
    c.save()
