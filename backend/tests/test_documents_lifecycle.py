r"""Documents: batch import, folder import, owners and review dates.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_documents_lifecycle.py -q

The embedding model is replaced by a fake that returns fixed vectors, which makes
the ingestion path testable offline for the first time — until now only the probes
that need Ollama ever exercised it. What is under test is the pipeline around the
model: validation, versioning, ownership, review dates, permissions, reporting.
"""

import argparse
import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import ingestion
from app.auth import password_hash
from app.database import AuditEvent, DocumentChunk, DocumentRecord, SessionLocal, User, initialise_database
from app.ingestion import DocumentMetadata, IngestionRejected, review_status, safe_roles
from app.main import app
from app.rag import DOCUMENT_STORAGE_DIR


PASSWORD = "LifecycleTestPassword-2026"
TEXT = b"Procedure interne.\nLe delai de traitement est de 5 jours ouvrables.\n"


@pytest.fixture(autouse=True)
def fake_embeddings(monkeypatch):
    async def embed(texts):
        return [[0.1] * 768 for _ in texts]

    monkeypatch.setattr(ingestion, "embed_texts", embed)


@pytest.fixture
def team():
    """An administrator, a document manager and a reader, all removed afterwards
    together with every document they imported."""
    initialise_database()
    run = uuid.uuid4().hex[:8]
    people = {
        "admin": (f"cyc-adm-{run}", "admin", "technique"),
        "manager": (f"cyc-mgr-{run}", "document_manager", "rh"),
        "reader": (f"cyc-lec-{run}", "user", "rh"),
    }
    with SessionLocal() as db:
        for username, role, department in people.values():
            db.add(User(username=username, email=f"{username}@ansi.ne",
                        password_hash=password_hash.hash(PASSWORD),
                        role=role, department=department, status="active"))
        db.commit()
        ids = {key: db.scalar(select(User.id).where(User.username == username))
               for key, (username, _, _) in people.items()}
    try:
        yield {key: {"email": f"{username}@ansi.ne", "id": ids[key]}
               for key, (username, _, _) in people.items()}
    finally:
        with SessionLocal() as db:
            for document in db.scalars(
                select(DocumentRecord).where(DocumentRecord.created_by.in_(ids.values()))
            ).all():
                (DOCUMENT_STORAGE_DIR / document.stored_filename).unlink(missing_ok=True)
                db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
                db.execute(delete(AuditEvent).where(AuditEvent.document_id == document.id))
                db.execute(delete(DocumentRecord).where(DocumentRecord.id == document.id))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(ids.values())))
            db.execute(delete(User).where(User.id.in_(ids.values())))
            db.commit()


def signed_in(email: str) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    assert client.post("/auth/login", json={"username": email, "password": PASSWORD}).status_code == 204
    return client


def unique(name: str) -> str:
    return f"{uuid.uuid4().hex[:6]}-{name}"


# --------------------------------------------------------------------------
# Batch import
# --------------------------------------------------------------------------

def test_a_batch_imports_what_it_can_and_reports_the_rest(team):
    client = signed_in(team["admin"]["email"])
    good_a, good_b = unique("procedure_conges.txt"), unique("note-de-service.md")
    response = client.post("/documents/upload-batch", data={"department": "rh"}, files=[
        ("files", (good_a, TEXT, "text/plain")),
        ("files", (good_b, TEXT, "text/markdown")),
        ("files", (unique("photo.png"), b"x", "image/png")),
        ("files", (unique("vide.txt"), b"", "text/plain")),
    ])
    client.__exit__(None, None, None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["imported"] == 2 and body["failed"] == 2
    by_name = {row["filename"]: row for row in body["results"]}
    assert by_name[good_a]["status"] == "ok"
    # Titled after the filename, separators read as spaces.
    assert "procedure conges" in by_name[good_a]["title"]
    failures = [row for row in body["results"] if row["status"] == "error"]
    assert all(row["detail"] for row in failures), "every failure says why"


def test_the_shared_metadata_is_checked_before_any_file_is_read(team):
    """A bad date must not cost fifty embeddings before being noticed."""
    client = signed_in(team["admin"]["email"])
    response = client.post("/documents/upload-batch", data={"review_due": "31/12/2026"}, files=[
        ("files", (unique("a.txt"), TEXT, "text/plain")),
    ])
    client.__exit__(None, None, None)
    assert response.status_code == 422
    assert "AAAA-MM-JJ" in response.json()["detail"]


def test_a_batch_over_the_limit_is_refused_and_points_to_the_folder_importer(team, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "document_batch_max_files", 2)
    client = signed_in(team["admin"]["email"])
    response = client.post("/documents/upload-batch", files=[
        ("files", (unique(f"f{i}.txt"), TEXT, "text/plain")) for i in range(3)
    ])
    client.__exit__(None, None, None)
    assert response.status_code == 413
    assert "import_folder" in response.json()["detail"]


def test_a_reader_cannot_import(team):
    client = signed_in(team["reader"]["email"])
    response = client.post("/documents/upload-batch", files=[("files", (unique("a.txt"), TEXT, "text/plain"))])
    client.__exit__(None, None, None)
    assert response.status_code == 403


def test_reimporting_a_filename_creates_a_new_version(team):
    name = unique("reglement.txt")
    client = signed_in(team["admin"]["email"])
    first = client.post("/documents/upload-batch", files=[("files", (name, TEXT, "text/plain"))]).json()
    second = client.post("/documents/upload-batch", files=[("files", (name, TEXT + b"Ajout.\n", "text/plain"))]).json()
    client.__exit__(None, None, None)
    assert first["results"][0]["version"] == 1
    assert second["results"][0]["version"] == 2
    with SessionLocal() as db:
        versions = db.scalars(select(DocumentRecord).where(DocumentRecord.original_filename == name)).all()
    assert sorted((v.version, v.is_current) for v in versions) == [(1, False), (2, True)]


# --------------------------------------------------------------------------
# Roles at import
# --------------------------------------------------------------------------

def test_the_administrator_is_always_among_the_allowed_roles():
    """Otherwise a document could enter the corpus that nobody can read or delete."""
    assert safe_roles("user") == "admin,user"
    assert safe_roles("admin,document_manager") == "admin,document_manager"


def test_an_unknown_role_is_rejected_rather_than_dropped():
    """A typo in a permission list should fail loudly, not silently narrow who may read."""
    with pytest.raises(IngestionRejected):
        safe_roles("admin,utilisateur")
    with pytest.raises(IngestionRejected):
        safe_roles("")


# --------------------------------------------------------------------------
# Owners and review dates
# --------------------------------------------------------------------------

def test_the_importer_is_the_default_owner_and_a_review_date_is_set(team):
    client = signed_in(team["manager"]["email"])
    body = client.post("/documents/upload", data={"title": "Procedure de test", "department": "rh"},
                       files={"file": (unique("p.txt"), TEXT, "text/plain")}).json()
    client.__exit__(None, None, None)
    assert body["owner_id"] == team["manager"]["id"]
    assert body["owner"].startswith("cyc-mgr-")
    due = datetime.fromisoformat(body["review_due"])
    assert timedelta(days=330) < due - datetime.now(timezone.utc) < timedelta(days=400)
    assert body["review_status"] == "ok"


@pytest.mark.parametrize("offset,expected", [
    (None, "none"),
    (-3, "overdue"),
    (10, "due_soon"),
    (120, "ok"),
])
def test_review_status(offset, expected):
    document = DocumentRecord(
        review_due=None if offset is None else datetime.now(timezone.utc) + timedelta(days=offset)
    )
    assert review_status(document) == expected


def test_a_naive_date_from_sqlite_is_compared_safely():
    """SQLite returns datetimes without timezone; comparing them with an aware one
    raises. The helper must absorb both."""
    naive = (datetime.now(timezone.utc) - timedelta(days=2)).replace(tzinfo=None)
    assert review_status(DocumentRecord(review_due=naive)) == "overdue"


def test_an_administrator_reassigns_the_owner_and_the_review_date(team):
    client = signed_in(team["admin"]["email"])
    body = client.post("/documents/upload", data={"title": "Note a reviser"},
                       files={"file": (unique("n.txt"), TEXT, "text/plain")}).json()
    updated = client.patch(f"/documents/{body['id']}", json={
        "owner_id": team["manager"]["id"], "review_due": "2020-01-15",
    })
    client.__exit__(None, None, None)
    assert updated.status_code == 200, updated.text
    assert updated.json()["owner_id"] == team["manager"]["id"]
    assert updated.json()["review_status"] == "overdue"


def test_a_reader_cannot_be_made_responsible_for_a_document(team):
    """Responsibility means being able to replace the document."""
    client = signed_in(team["admin"]["email"])
    body = client.post("/documents/upload", data={"title": "Note a reviser"},
                       files={"file": (unique("n.txt"), TEXT, "text/plain")}).json()
    refused = client.patch(f"/documents/{body['id']}", json={"owner_id": team["reader"]["id"]})
    client.__exit__(None, None, None)
    assert refused.status_code == 422


def test_the_review_date_can_be_cleared(team):
    client = signed_in(team["admin"]["email"])
    body = client.post("/documents/upload", data={"title": "Note permanente"},
                       files={"file": (unique("n.txt"), TEXT, "text/plain")}).json()
    cleared = client.patch(f"/documents/{body['id']}", json={"review_due": ""})
    client.__exit__(None, None, None)
    assert cleared.json()["review_due"] is None
    assert cleared.json()["review_status"] == "none"


# --------------------------------------------------------------------------
# Folder importer
# --------------------------------------------------------------------------

def folder_arguments(folder, actor, **extra) -> argparse.Namespace:
    values = {"folder": str(folder), "service": "rh", "actor": actor, "owner": None,
              "roles": "admin,document_manager,user", "classification": "interne",
              "valid_until": None, "review_due": None, "recursive": True, "dry_run": False}
    values.update(extra)
    return argparse.Namespace(**values)


def test_the_folder_importer_goes_through_the_same_path(team, tmp_path, capsys):
    from app import import_folder

    run = uuid.uuid4().hex[:6]
    (tmp_path / f"{run}-guide.txt").write_bytes(TEXT)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / f"{run}-annexe.md").write_bytes(TEXT)
    (tmp_path / f"{run}-image.png").write_bytes(b"x")

    code = asyncio.run(import_folder.run(folder_arguments(tmp_path, team["manager"]["email"])))
    report = capsys.readouterr().out
    assert code == 0, report
    assert "2 importé(s), 0 échec(s)" in report
    with SessionLocal() as db:
        imported = db.scalars(select(DocumentRecord).where(DocumentRecord.created_by == team["manager"]["id"])).all()
    assert {d.department for d in imported} == {"rh"}
    assert all(d.owner_id == team["manager"]["id"] for d in imported)


def test_the_folder_importer_refuses_a_reader_as_actor(team, tmp_path, capsys):
    from app import import_folder

    (tmp_path / "a.txt").write_bytes(TEXT)
    code = asyncio.run(import_folder.run(folder_arguments(tmp_path, team["reader"]["email"])))
    assert code == 2


def test_the_folder_importer_stops_when_the_model_is_down(team, tmp_path, capsys, monkeypatch):
    """Every following file would fail the same way: stop rather than print it a
    hundred times."""
    from app import import_folder
    from app.rag import ModelUnavailableError

    async def down(texts):
        raise ModelUnavailableError("Le modèle d'embeddings local est indisponible")

    monkeypatch.setattr(ingestion, "embed_texts", down)
    for i in range(3):
        (tmp_path / f"{i}.txt").write_bytes(TEXT)
    code = asyncio.run(import_folder.run(folder_arguments(tmp_path, team["manager"]["email"])))
    report = capsys.readouterr().out
    assert code == 1
    assert report.count("ÉCHEC") == 1
    assert "interrompu" in report


def test_a_dry_run_reads_nothing(team, tmp_path, capsys, monkeypatch):
    from app import import_folder

    async def must_not_run(texts):
        raise AssertionError("une simulation ne doit rien envoyer au modèle")

    monkeypatch.setattr(ingestion, "embed_texts", must_not_run)
    (tmp_path / "a.txt").write_bytes(TEXT)
    code = asyncio.run(import_folder.run(folder_arguments(tmp_path, team["manager"]["email"], dry_run=True)))
    assert code == 0
    assert "Simulation" in capsys.readouterr().out


@pytest.mark.parametrize("filename,expected", [
    ("procedure_conges.pdf", "procedure conges"),
    ("note-de-service.md", "note de service"),
    ("cv.pdf", "cv.pdf"),
    ("01.txt", "01.txt"),
])
def test_a_short_filename_still_gives_an_acceptable_title(filename, expected):
    """The three-character minimum guards the manual form; it must not reject
    « cv.pdf » in a folder import."""
    from app.ingestion import title_from_filename

    assert title_from_filename(filename) == expected
