r"""Administration surface: readable errors, the access tool, audit trail, overview.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_administration.py -q

No Ollama: none of this reaches the model.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.access import DEPARTMENTS, TRANSVERSE
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
from app.main import app, audit_label
from app.tools import find_tool, my_access


PASSWORD = "AdministrationTestPassword-2026"


@pytest.fixture
def people():
    """One administrator and one HR agent, removed afterwards."""
    initialise_database()
    run = uuid.uuid4().hex[:8]
    names = [f"adm-{run}", f"rh-{run}"]
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


# --------------------------------------------------------------------------
# Validation errors an agent can act on
# --------------------------------------------------------------------------
# Observed: seven consecutive 422 on /auth/register. The endpoint was right; the
# error was a list of Pydantic objects, which the interface rendered as nothing
# useful, so the same invalid form was resubmitted over and over.

@pytest.mark.parametrize("payload,expected", [
    ({"username": "essai-court", "password": "court", "requested_department": "rh"},
     "12 caractères"),
    ({"username": "Ahmed Laminou", "password": "MotDePasseValide2026", "requested_department": "rh"},
     "ni espace, ni accent"),
    ({"username": "essai-sans", "password": "MotDePasseValide2026"},
     "obligatoire"),
])
def test_a_validation_error_is_a_readable_sentence(payload, expected):
    with TestClient(app) as client:
        response = client.post("/auth/register", json=payload)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, str), "detail doit toujours être une chaîne, jamais une liste"
    assert expected in detail


def test_several_invalid_fields_are_all_reported():
    with TestClient(app) as client:
        response = client.post("/auth/register", json={"username": "a b", "password": "x"})
    detail = response.json()["detail"]
    assert "identifiant" in detail.lower() and "mot de passe" in detail.lower()


def test_a_valid_request_is_still_accepted():
    run = uuid.uuid4().hex[:8]
    name = f"valide-{run}"
    try:
        with TestClient(app) as client:
            response = client.post("/auth/register", json={
                "username": name, "password": "MotDePasseValide2026", "requested_department": "rh",
            })
        assert response.status_code == 202
    finally:
        with SessionLocal() as db:
            db.execute(delete(User).where(User.username == name))
            db.commit()


# --------------------------------------------------------------------------
# « Quels droits ai-je ? »
# --------------------------------------------------------------------------
# Observed: an administrator asked this and was told the information was not in
# the extracts, cited from a book on the history of mathematics.

@pytest.mark.parametrize("question", [
    "Quels droits ai-je accés ?",
    "Quels sont mes droits ?",
    "À quoi ai-je accès ?",
    "Que puis-je faire avec ce compte ?",
    "Quel est mon rôle ?",
    "Quel est mon service ?",
    "Quel est mon périmètre ?",
])
def test_asking_about_my_own_rights_reaches_the_tool(question):
    tool = find_tool(question)
    assert tool is not None and tool.name == "my_access", question


def test_the_answer_states_the_role_the_service_and_what_is_not_visible(people):
    _, identifiers = people
    with SessionLocal() as db:
        agent = db.get(User, identifiers[1])
        answer = my_access(db, agent)
    assert "user" in answer
    assert "Ressources humaines" in answer
    # The useful half: what the account cannot see.
    assert "ne voyez pas" in answer
    for other in ("Finance", "Logistique", "Technique"):
        assert other in answer


def test_an_administrator_is_told_the_exception_is_not_transitive(people):
    _, identifiers = people
    with SessionLocal() as db:
        administrator = db.get(User, identifiers[0])
        answer = my_access(db, administrator)
    assert "tous les services" in answer
    assert "pas transitive" in answer


def test_the_tool_is_open_to_every_role():
    tool = find_tool("Quels sont mes droits ?")
    assert {"admin", "document_manager", "user"} <= tool.roles


# --------------------------------------------------------------------------
# Audit trail
# --------------------------------------------------------------------------

def test_the_audit_trail_is_readable_by_an_administrator(people):
    names, _ = people
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": names[0], "password": PASSWORD}).status_code == 204
        body = client.get("/admin/audit?limit=20").json()
    assert "events" in body and isinstance(body["total"], int)


def test_the_audit_trail_is_closed_to_an_ordinary_account(people):
    names, _ = people
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[1], "password": PASSWORD})
        assert client.get("/admin/audit").status_code == 403
        assert client.get("/admin/overview").status_code == 403


def test_an_event_carries_its_actor_and_a_readable_label(people):
    names, identifiers = people
    with SessionLocal() as db:
        db.add(AuditEvent(actor_id=identifiers[0], document_id=None, event_type="user_created"))
        db.commit()
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        events = client.get("/admin/audit?event_type=user_created&limit=5").json()["events"]
    assert events and events[0]["label"] == "Compte créé"
    assert any(event["actor"] == names[0] for event in events)


def test_the_filter_narrows_by_prefix(people):
    names, identifiers = people
    with SessionLocal() as db:
        db.add_all([
            AuditEvent(actor_id=identifiers[0], document_id=None, event_type="tool_invoked:count_users"),
            AuditEvent(actor_id=identifiers[0], document_id=None, event_type="login_failed"),
        ])
        db.commit()
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        events = client.get("/admin/audit?event_type=tool_invoked").json()["events"]
    assert events
    assert all(event["event_type"].startswith("tool_invoked") for event in events)


@pytest.mark.parametrize("event_type,expected", [
    ("document_uploaded", "Document importé"),
    ("login_failed", "Tentative de connexion échouée"),
    ("tool_invoked:count_users", "Outil exécuté (count_users)"),
    ("feedback:wrong", "Réponse signalée incorrecte"),
    ("feedback:useful", "Réponse marquée utile"),
])
def test_every_event_type_written_by_the_code_has_a_label(event_type, expected):
    assert audit_label(event_type) == expected


# --------------------------------------------------------------------------
# Overview
# --------------------------------------------------------------------------

def test_the_overview_reports_every_perimeter(people):
    names, _ = people
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        body = client.get("/admin/overview").json()
    reported = {item["value"] for item in body["departments"]}
    assert DEPARTMENTS | {TRANSVERSE} <= reported


def test_transverse_reports_no_account_because_nobody_belongs_to_it(people):
    names, _ = people
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        body = client.get("/admin/overview").json()
    transverse = next(item for item in body["departments"] if item["value"] == TRANSVERSE)
    assert transverse["accounts"] is None


def test_the_overview_counts_unattached_accounts(people):
    """The figure exists so the oversight is visible, not merely stored."""
    names, _ = people
    orphan = f"orphelin-{uuid.uuid4().hex[:8]}"
    with SessionLocal() as db:
        db.add(User(username=orphan, password_hash=password_hash.hash(PASSWORD),
                    role="user", department=None, status="active"))
        db.commit()
    try:
        with TestClient(app) as client:
            client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
            body = client.get("/admin/overview").json()
        assert body["accounts"]["unattached"] >= 1
    finally:
        with SessionLocal() as db:
            db.execute(delete(User).where(User.username == orphan))
            db.commit()


def test_the_overview_states_the_retention_setting(people):
    """It is the blocking decision before real data, so it belongs on the screen."""
    names, _ = people
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": names[0], "password": PASSWORD})
        body = client.get("/admin/overview").json()
    assert "retention_days" in body["model"]
    assert body["model"]["database"] in {"SQLite", "PostgreSQL + pgvector"}
