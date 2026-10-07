import io
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .database import Base, SessionLocal, engine, get_db
from .generator import generate_certificate
from .models import Certificate, Job

CERT_DIR = Path(os.getenv("CERT_DIR", "generated"))
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
FINISHED = {"completed", "completed_with_errors", "failed"}

Base.metadata.create_all(engine)
app = FastAPI(title="Bulk Certificate Generator API")

@app.get("/")
def root():
    return {"message": "Bulk Certificate Generator API", "docs": "/docs"}

class JobCreate(BaseModel):
    event_name: str = Field(min_length=1, max_length=200)
    issuer: str = Field(default="", max_length=200)
    # Kept as raw dicts on purpose: one bad recipient must NOT reject the whole
    # request. Each recipient is validated individually below.
    recipients: list[dict[str, Any]] = Field(min_length=1, max_length=1000)


def validate_recipient(r: dict) -> str | None:
    """Return an error message, or None if the recipient is valid."""
    name, email = r.get("name"), r.get("email")
    if not isinstance(name, str) or not name.strip():
        return "name is required"
    if len(name.strip()) > 100:
        return "name too long (max 100 chars)"
    if not isinstance(email, str) or not EMAIL_RE.match(email.strip()):
        return "valid email is required"
    return None


def finalize_job(db: Session, job: Job) -> None:
    failed = db.scalar(
        select(func.count()).where(Certificate.job_id == job.id, Certificate.status == "failed")
    )
    if failed == job.total:
        job.status = "failed"
    elif failed:
        job.status = "completed_with_errors"
    else:
        job.status = "completed"
    job.completed_at = datetime.now(timezone.utc)
    db.commit()


def process_job(job_id: str) -> None:
    """Background task: generate every pending certificate, isolating failures."""
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        job.status = "processing"
        db.commit()
        pending = db.scalars(
            select(Certificate).where(Certificate.job_id == job_id, Certificate.status == "pending")
        ).all()
        for cert in pending:
            try:
                path = CERT_DIR / job_id / f"{cert.id}.pdf"
                generate_certificate(cert.recipient_name, job.event_name, job.issuer, path)
                cert.status, cert.file_path = "success", str(path)
            except Exception as exc:  # one failure must not stop the others
                cert.status, cert.error = "failed", f"generation error: {exc}"
            db.commit()
        finalize_job(db, job)
    finally:
        db.close()


def get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.post("/jobs", status_code=202)
def create_job(body: JobCreate, bg: BackgroundTasks, db: Session = Depends(get_db)):
    job = Job(event_name=body.event_name.strip(), issuer=body.issuer.strip(), total=len(body.recipients))
    db.add(job)
    db.flush()
    for r in body.recipients:
        err = validate_recipient(r)
        db.add(
            Certificate(
                job_id=job.id,
                recipient_name=str(r.get("name") or "")[:200],
                recipient_email=str(r.get("email") or "")[:200],
                status="failed" if err else "pending",
                error=f"validation error: {err}" if err else None,
            )
        )
    db.commit()
    bg.add_task(process_job, job.id)
    return {"job_id": job.id, "status": job.status, "total": job.total}


@app.get("/jobs/{job_id}")
def job_status(job_id: str, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    counts = dict(
        db.execute(
            select(Certificate.status, func.count()).where(Certificate.job_id == job.id).group_by(Certificate.status)
        ).all()
    )
    ok, failed, pending = counts.get("success", 0), counts.get("failed", 0), counts.get("pending", 0)
    certs = db.scalars(select(Certificate).where(Certificate.job_id == job.id)).all()
    return {
        "job_id": job.id,
        "event_name": job.event_name,
        "status": job.status,
        "total": job.total,
        "succeeded": ok,
        "failed": failed,
        "pending": pending,
        "progress_percent": round((ok + failed) / job.total * 100, 1) if job.total else 100,
        "certificates": [
            {
                "id": c.id,
                "name": c.recipient_name,
                "email": c.recipient_email,
                "status": c.status,
                "error": c.error,
                "download_url": f"/jobs/{job.id}/certificates/{c.id}" if c.status == "success" else None,
            }
            for c in certs
        ],
    }


@app.get("/jobs/{job_id}/certificates/{cert_id}")
def download_certificate(job_id: str, cert_id: str, db: Session = Depends(get_db)):
    get_job_or_404(db, job_id)
    cert = db.get(Certificate, cert_id)
    if not cert or cert.job_id != job_id:
        raise HTTPException(404, "Certificate not found")
    if cert.status != "success" or not cert.file_path or not Path(cert.file_path).exists():
        raise HTTPException(404, f"Certificate not available (status: {cert.status})")
    return FileResponse(cert.file_path, media_type="application/pdf", filename=f"{cert.recipient_name}.pdf")


@app.get("/jobs/{job_id}/download")
def download_all(job_id: str, db: Session = Depends(get_db)):
    job = get_job_or_404(db, job_id)
    if job.status not in FINISHED:
        raise HTTPException(409, f"Job not finished yet (status: {job.status})")
    certs = db.scalars(
        select(Certificate).where(Certificate.job_id == job_id, Certificate.status == "success")
    ).all()
    if not certs:
        raise HTTPException(404, "No certificates were generated for this job")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for c in certs:
            safe = re.sub(r"[^\w\-]+", "_", c.recipient_name)
            z.write(c.file_path, arcname=f"{safe}_{c.id[:6]}.pdf")
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{job_id}.zip"'}
    )
