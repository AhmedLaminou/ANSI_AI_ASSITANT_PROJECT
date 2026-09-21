r"""Profile, self-service password change, and the scope of an indexed document.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_profile_and_scope.py -q

No Ollama: none of this reaches the model.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import password_hash
from app.database import (
    AnswerFeedback,
    AuditEvent,
    ChatMessage,
    Conversation,
    DocumentChunk,
    DocumentRecord,
    SessionLocal,
    User,
    initialise_database,
)
from app.main import app


PASSWORD = "ProfileTestPassword-2026"


@pytest.fixture
def people():
    """One administrator and one HR agent, removed afterwards."""
    initialise_database()
    run = uuid.uuid4().hex[:8]
    names = [f"prof-adm-{run}", f"prof-rh-{run}"]
    with SessionLocal() as db:
        db.add_all([
            User(username=names[0], password_hash=password_hash.hash(PASSWORD),
                 role="admin", department="technique", status="active"),
            User(username=names[1], password_hash=password_hash.hash(PASSWORD),
                 role="user", department="rh", status="active"),
        ])
        db.commit()
        identifiers = [row.id for row in db.scalars(select(User).where(User.username.in_(names))).all()]
    try:
        yield names, identifiers
    finally:
        with SessionLocal() as db:
            for document in db.scalars(
                select(DocumentRecord).where(DocumentRecord.created_by.in_(identifiers))
            ).all():
                db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
                db.execute(delete(AuditEvent).where(AuditEvent.document_id == document.id))
                db.execute(delete(DocumentRecord).where(DocumentRecord.id == document.id))
            conversations = [
                row.id for row in
                db.scalars(select(Conversation).where(Conversation.user_id.in_(identifiers))).all()
            ]
            if conversations:
                db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversations)))
                db.execute(delete(Conversation).where(Conversation.id.in_(conversations)))
            db.execute(delete(AnswerFeedback).where(AnswerFeedback.user_id.in_(identifiers)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(identifiers)))
            db.execute(delete(User).where(User.id.in_(identifiers)))
            db.commit()


def make_document(db, owner_id: int, **overrides) -> DocumentRecord:
    document = DocumentRecord(
        title=overrides.get("title", "Note interne"),
        original_filename="note.txt",
        stored_filename=f"stored-{uuid.uuid4().hex}.txt",
        content_type="text/plain",
        classification=overrides.get("classification", "interne"),
        allowed_roles=overrides.get("allowed_roles", "admin,document_manager,user"),
        created_by=owner_id,
        department=overrides.get("department", "transverse"),
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    return document


# --------------------------------------------------------------------------
# Profil
# --------------------------------------------------------------------------

def test_an_agent_reads_their_own_profile(people):
    names, _ = people
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": names[1], "password": PASSWORD}).status_code == 204
        profile = client.get("/auth/profile").json()
    assert profile["account"]["username"] == names[1]
    assert profile["account"]["department_label"] == "Ressources humaines"
    assert profile["rights"]
    # The half that matters: the perimeters this account cannot reach.
    assert profile["readable"]["unreachable"]


def test_an_administrator_has_no_unreachable_perimeter(people):
    names, _ = people
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        profile = client.get("/auth/profile").json()
    assert profile["readable"]["unreachable"] == []


def test_the_profile_needs_a_session():
    with TestClient(app) as client:
        assert client.get("/auth/profile").status_code == 401


def test_there_is_no_profile_of_somebody_else():
    """Deliberate: an agent's perimeter is their own, and reading another account's
    profile would be a quiet way of learning the shape of the organisation."""
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert not any(path.startswith("/auth/profile/") for path in paths)


# --------------------------------------------------------------------------
# Changement de mot de passe par l'agent
# --------------------------------------------------------------------------

def test_an_agent_changes_their_own_password(people):
    names, _ = people
    new_password = "NouveauMotDePasse-2026"
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": names[1], "password": PASSWORD}).status_code == 204
        changed = client.post("/auth/password", json={
            "current_password": PASSWORD, "new_password": new_password,
        })
        assert changed.status_code == 204
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": names[1], "password": new_password}).status_code == 204
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": names[1], "password": PASSWORD}).status_code == 401


def test_the_current_password_is_required(people):
    """A session left open on an unlocked machine must not let a passer-by lock the
    owner out of their own account."""
    names, _ = people
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[1], "password": PASSWORD})
        refused = client.post("/auth/password", json={
            "current_password": "MauvaisMotDePasse-2026", "new_password": "AutreMotDePasse-2026",
        })
    assert refused.status_code == 403


def test_the_new_password_must_differ(people):
    names, _ = people
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[1], "password": PASSWORD})
        refused = client.post("/auth/password", json={
            "current_password": PASSWORD, "new_password": PASSWORD,
        })
    assert refused.status_code == 422


def test_a_short_new_password_is_refused(people):
    names, _ = people
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[1], "password": PASSWORD})
        refused = client.post("/auth/password", json={
            "current_password": PASSWORD, "new_password": "court",
        })
    assert refused.status_code == 422
    assert "12" in refused.json()["detail"]


# --------------------------------------------------------------------------
# Perimeter of an already-indexed document
# --------------------------------------------------------------------------

def test_an_administrator_changes_the_scope_without_reimporting(people):
    names, identifiers = people
    with SessionLocal() as db:
        document_id = make_document(db, identifiers[0]).id
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        updated = client.patch(f"/documents/{document_id}", json={
            "department": "finance", "classification": "confidentiel",
            "allowed_roles": "admin,document_manager",
        })
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["department"] == "finance"
    assert body["classification"] == "confidentiel"
    assert set(body["allowed_roles"]) == {"admin", "document_manager"}


def test_changing_the_scope_takes_effect_on_who_may_read(people):
    """The point of the feature: the perimeter really moves, and immediately."""
    names, identifiers = people
    with SessionLocal() as db:
        document_id = make_document(db, identifiers[0], department="transverse").id

    with TestClient(app) as agent:
        agent.post("/auth/login", json={"username": names[1], "password": PASSWORD})
        before = {item["id"] for item in agent.get("/documents").json()}
    assert document_id in before, "un document transverse est lisible par tous"

    with TestClient(app) as admin:
        admin.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        admin.patch(f"/documents/{document_id}", json={"department": "finance"})

    with TestClient(app) as agent:
        agent.post("/auth/login", json={"username": names[1], "password": PASSWORD})
        after = {item["id"] for item in agent.get("/documents").json()}
    assert document_id not in after, "deplace aux finances, il sort du perimetre d un agent RH"


def test_an_ordinary_account_cannot_change_a_scope(people):
    names, identifiers = people
    with SessionLocal() as db:
        document_id = make_document(db, identifiers[0]).id
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[1], "password": PASSWORD})
        assert client.patch(f"/documents/{document_id}", json={"department": "rh"}).status_code == 403


def test_an_administrator_cannot_lock_everyone_out_of_a_document(people):
    """Removing the admin role would leave the document unreadable and unfixable
    through the interface: the only way back would be the database itself."""
    names, identifiers = people
    with SessionLocal() as db:
        document_id = make_document(db, identifiers[0]).id
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        refused = client.patch(f"/documents/{document_id}", json={"allowed_roles": "user"})
    assert refused.status_code == 422


def test_an_invented_service_is_refused(people):
    names, identifiers = people
    with SessionLocal() as db:
        document_id = make_document(db, identifiers[0]).id
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        assert client.patch(f"/documents/{document_id}",
                            json={"department": "direction-generale"}).status_code == 422


def test_a_missing_document_is_a_404(people):
    names, _ = people
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        assert client.patch("/documents/999999", json={"department": "rh"}).status_code == 404


def test_a_scope_change_is_journalised(people):
    names, identifiers = people
    with SessionLocal() as db:
        document_id = make_document(db, identifiers[0]).id
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        client.patch(f"/documents/{document_id}", json={"department": "logistique"})
        events = client.get("/admin/audit?event_type=document_scope_updated").json()["events"]
    assert any(event["actor"] == names[0] for event in events)


def test_omitted_fields_are_left_alone(people):
    names, identifiers = people
    with SessionLocal() as db:
        document_id = make_document(db, identifiers[0], classification="direction").id
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        body = client.patch(f"/documents/{document_id}", json={"department": "rh"}).json()
    assert body["classification"] == "direction"


# --------------------------------------------------------------------------
# Sign-in by address or by username
# --------------------------------------------------------------------------

def test_sign_in_works_with_either_the_address_or_the_username():
    """Accounts predating the email column sign in by username; newer ones by
    address. Both must keep working without inventing an address for the old ones."""
    run = uuid.uuid4().hex[:8]
    username, email = f"double-{run}", f"double.{run}@ansi.ne"
    with SessionLocal() as db:
        db.add(User(username=username, email=email, password_hash=password_hash.hash(PASSWORD),
                    role="user", department="rh", status="active"))
        db.commit()
        account_id = db.scalar(select(User.id).where(User.username == username))
    try:
        for identifier in (username, email, email.upper()):
            with TestClient(app) as client:
                assert client.post(
                    "/auth/login", json={"username": identifier, "password": PASSWORD}
                ).status_code == 204, identifier
    finally:
        with SessionLocal() as db:
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id == account_id))
            db.execute(delete(User).where(User.id == account_id))
            db.commit()
