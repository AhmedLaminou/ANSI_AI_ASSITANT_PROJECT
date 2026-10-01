r"""The original file of a document: the whole document, not its first excerpts.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_document_file.py -q

No Ollama: the files are written straight into the storage directory.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import AuditEvent, DocumentRecord, SessionLocal
from app.main import app
from app.rag import DOCUMENT_STORAGE_DIR

# The fixture removes the two accounts, their documents and their audit events.
from tests.test_profile_and_scope import PASSWORD, people  # noqa: F401


@pytest.fixture
def stored():
    """Writes files into the real storage directory, and removes them afterwards."""
    written = []

    def write(owner_id: int, filename: str, content: bytes, **fields) -> int:
        DOCUMENT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        stored_filename = f"test-{uuid.uuid4().hex}{filename[filename.rfind('.'):]}"
        (DOCUMENT_STORAGE_DIR / stored_filename).write_bytes(content)
        written.append(DOCUMENT_STORAGE_DIR / stored_filename)
        with SessionLocal() as db:
            document = DocumentRecord(
                title=fields.get("title", "Document complet"),
                original_filename=filename,
                stored_filename=stored_filename,
                content_type=fields.get("content_type", "application/octet-stream"),
                classification="interne",
                allowed_roles=fields.get("allowed_roles", "admin,document_manager,user"),
                created_by=owner_id,
                department=fields.get("department", "transverse"),
            )
            db.add(document)
            db.commit()
            return document.id

    yield write
    for path in written:
        path.unlink(missing_ok=True)


def signed_in(client: TestClient, username: str) -> TestClient:
    assert client.post("/auth/login", json={"username": username, "password": PASSWORD}).status_code == 204
    return client


def test_the_whole_document_is_returned_not_its_excerpts(people, stored):
    names, ids = people
    # Far longer than the eight excerpts of the preview would ever show.
    content = ("Article 1. " + "Le télétravail est limité à deux jours. " * 400 + "Fin du règlement.").encode()
    document_id = stored(ids[0], "reglement.txt", content)
    with TestClient(app) as client:
        response = signed_in(client, names[1]).get(f"/documents/{document_id}/file")
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "no-store" in response.headers["cache-control"]


def test_a_pdf_opens_in_the_browser(people, stored):
    names, ids = people
    document_id = stored(ids[0], "procedure.pdf", b"%PDF-1.4 test")
    with TestClient(app) as client:
        response = signed_in(client, names[1]).get(f"/documents/{document_id}/file")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")


def test_a_docx_is_always_a_download(people, stored):
    names, ids = people
    document_id = stored(ids[0], "note.docx", b"PK fake docx")
    with TestClient(app) as client:
        response = signed_in(client, names[1]).get(f"/documents/{document_id}/file")
    assert response.headers["content-disposition"].startswith("attachment")


def test_any_document_can_be_downloaded_on_request(people, stored):
    names, ids = people
    document_id = stored(ids[0], "procedure.pdf", b"%PDF-1.4 test")
    with TestClient(app) as client:
        response = signed_in(client, names[1]).get(f"/documents/{document_id}/file?download=true")
    assert response.headers["content-disposition"].startswith("attachment")


def test_the_type_comes_from_the_extension_not_from_the_upload(people, stored):
    """A Markdown file declared as HTML at upload must not render as a web page."""
    names, ids = people
    document_id = stored(ids[0], "piege.md", b"<script>alert(1)</script>", content_type="text/html")
    with TestClient(app) as client:
        response = signed_in(client, names[1]).get(f"/documents/{document_id}/file")
    assert response.headers["content-type"].startswith("text/plain")


def test_another_service_cannot_open_the_file(people, stored):
    names, ids = people
    document_id = stored(ids[0], "budget.pdf", b"%PDF-1.4 secret", department="finance")
    with TestClient(app) as client:
        response = signed_in(client, names[1]).get(f"/documents/{document_id}/file")
    # Same answer as a document that does not exist: no confirmation that it does.
    assert response.status_code == 404
    assert b"secret" not in response.content


def test_a_role_excluded_from_the_document_cannot_open_it(people, stored):
    names, ids = people
    document_id = stored(ids[0], "consignes.pdf", b"%PDF-1.4 admin", allowed_roles="admin")
    with TestClient(app) as client:
        assert signed_in(client, names[1]).get(f"/documents/{document_id}/file").status_code == 404


def test_the_file_needs_a_session(people, stored):
    _, ids = people
    document_id = stored(ids[0], "reglement.txt", b"contenu")
    with TestClient(app) as client:
        assert client.get(f"/documents/{document_id}/file").status_code == 401


def test_a_missing_file_says_so(people, stored):
    names, ids = people
    document_id = stored(ids[0], "perdu.txt", b"contenu")
    with SessionLocal() as db:
        (DOCUMENT_STORAGE_DIR / db.get(DocumentRecord, document_id).stored_filename).unlink()
    with TestClient(app) as client:
        response = signed_in(client, names[1]).get(f"/documents/{document_id}/file")
    assert response.status_code == 404
    assert "plus disponible" in response.json()["detail"]


def test_opening_a_document_is_journalled(people, stored):
    names, ids = people
    document_id = stored(ids[0], "reglement.txt", b"contenu")
    with TestClient(app) as client:
        signed_in(client, names[1]).get(f"/documents/{document_id}/file")
    with SessionLocal() as db:
        events = db.scalars(select(AuditEvent).where(
            AuditEvent.document_id == document_id, AuditEvent.event_type == "document_opened",
        )).all()
    assert [event.actor_id for event in events] == [ids[1]]
