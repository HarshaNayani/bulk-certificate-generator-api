# Bulk Certificate Generator API

FastAPI + SQLAlchemy (SQLite) + ReportLab. Submit many recipients in one request, get PDF certificates back.

## Setup
```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000/docs for interactive Swagger UI.

Env vars (optional): `DATABASE_URL` (default `sqlite:///./certs.db`; any SQLAlchemy URL such as PostgreSQL works), `CERT_DIR` (default `./generated`).

## Run tests
```bash
pytest -v
```

## Submit a generation request
```bash
curl -X POST http://127.0.0.1:8000/jobs -H "Content-Type: application/json" -d '{
  "event_name": "Python Workshop",
  "issuer": "Aereo",
  "recipients": [
    {"name": "Asha Rao", "email": "asha@example.com"},
    {"name": "Ravi Kumar", "email": "ravi@example.com"}
  ]
}'
# -> 202 {"job_id": "abc123...", "status": "pending", "total": 2}
```

## Check status / progress
```bash
curl http://127.0.0.1:8000/jobs/<job_id>
```
Returns `status` (`pending | processing | completed | completed_with_errors | failed`), counts (`succeeded`, `failed`, `pending`), `progress_percent`, and a per-certificate list with `status`, `error` and `download_url`.

## Retrieve certificates
- One: `GET /jobs/<job_id>/certificates/<cert_id>` (PDF)
- All successful ones: `GET /jobs/<job_id>/download` (ZIP; returns 409 until the job finishes)

## Design decisions
- **Background processing.** `POST /jobs` returns `202` immediately and generation runs in a FastAPI `BackgroundTask`. A request with many recipients would otherwise hold the connection open and risk timeouts. The client polls `GET /jobs/{id}`. For production scale I would swap this for a task queue (Celery/RQ) so jobs survive restarts and can run on separate workers.
- **Per-recipient validation, not request-level.** `recipients` is accepted as raw objects so one bad row doesn't reject the whole request. Invalid rows (missing name, bad email) are stored as `failed` with a reason; valid rows are still generated. Structural errors (no recipients, missing event_name, >1000 recipients) return `422`.
- **Failure isolation.** Each certificate is generated inside its own try/except and committed individually, so one failure never blocks the others and progress is visible mid-job.
- **Job status** is derived from results: all ok -> `completed`, some failed -> `completed_with_errors`, all failed -> `failed`.
- **Relational DB.** Two tables, `jobs` and `certificates` (one-to-many). SQLite by default, switchable via `DATABASE_URL`.
- **Single template.** Certificate layout lives in `app/generator.py` (ReportLab, landscape A4).

## Project structure
```
app/main.py        endpoints + background processing
app/models.py      Job, Certificate tables
app/generator.py   PDF template
app/database.py    engine/session
tests/             pytest suite (12 tests)
```
