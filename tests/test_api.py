import io
import zipfile

import app.main as main

GOOD = [
    {"name": "Asha Rao", "email": "asha@example.com"},
    {"name": "Ravi Kumar", "email": "ravi@example.com"},
]


def make_job(client, recipients, event="Python Workshop"):
    r = client.post("/jobs", json={"event_name": event, "issuer": "Aereo", "recipients": recipients})
    assert r.status_code == 202
    return r.json()["job_id"]


# ---- creating a job ----
def test_create_job_and_complete(client):
    job_id = make_job(client, GOOD)
    s = client.get(f"/jobs/{job_id}").json()
    assert s["status"] == "completed"
    assert s["total"] == 2 and s["succeeded"] == 2 and s["failed"] == 0


# ---- input validation ----
def test_rejects_empty_recipients(client):
    assert client.post("/jobs", json={"event_name": "X", "recipients": []}).status_code == 422


def test_rejects_missing_event_name(client):
    assert client.post("/jobs", json={"recipients": GOOD}).status_code == 422


def test_invalid_recipients_marked_failed_but_valid_still_generated(client):
    recips = GOOD + [{"name": "", "email": "a@b.com"}, {"name": "No Mail", "email": "not-an-email"}, {"foo": "bar"}]
    s = client.get(f"/jobs/{make_job(client, recips)}").json()
    assert s["status"] == "completed_with_errors"
    assert s["succeeded"] == 2 and s["failed"] == 3
    assert all("validation error" in c["error"] for c in s["certificates"] if c["status"] == "failed")


def test_all_invalid_job_is_failed(client):
    s = client.get(f"/jobs/{make_job(client, [{'name': '', 'email': 'x'}])}").json()
    assert s["status"] == "failed"


# ---- certificate generation ----
def test_generator_creates_pdf(tmp_path):
    out = tmp_path / "c.pdf"
    main.generate_certificate("Test User", "Event", "Org", out)
    assert out.read_bytes().startswith(b"%PDF")


# ---- status / progress ----
def test_status_shape_and_progress(client):
    s = client.get(f"/jobs/{make_job(client, GOOD)}").json()
    assert s["progress_percent"] == 100
    assert {"job_id", "status", "total", "succeeded", "failed", "pending", "certificates"} <= s.keys()


def test_unknown_job_404(client):
    assert client.get("/jobs/doesnotexist").status_code == 404


# ---- individual failure isolation ----
def test_single_generation_failure_does_not_block_others(client, monkeypatch):
    real = main.generate_certificate

    def flaky(name, event, issuer, path):
        if name == "Ravi Kumar":
            raise RuntimeError("boom")
        real(name, event, issuer, path)

    monkeypatch.setattr(main, "generate_certificate", flaky)
    s = client.get(f"/jobs/{make_job(client, GOOD)}").json()
    assert s["status"] == "completed_with_errors"
    assert s["succeeded"] == 1 and s["failed"] == 1
    bad = next(c for c in s["certificates"] if c["status"] == "failed")
    assert "boom" in bad["error"] and bad["download_url"] is None


# ---- retrieving certificates ----
def test_download_single_certificate(client):
    job_id = make_job(client, GOOD)
    cert = client.get(f"/jobs/{job_id}").json()["certificates"][0]
    r = client.get(cert["download_url"])
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


def test_download_failed_certificate_404(client):
    job_id = make_job(client, [{"name": "", "email": "x"}] + GOOD)
    bad = next(c for c in client.get(f"/jobs/{job_id}").json()["certificates"] if c["status"] == "failed")
    assert client.get(f"/jobs/{job_id}/certificates/{bad['id']}").status_code == 404


def test_download_zip(client):
    job_id = make_job(client, GOOD)
    r = client.get(f"/jobs/{job_id}/download")
    assert r.status_code == 200
    assert len(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == 2
