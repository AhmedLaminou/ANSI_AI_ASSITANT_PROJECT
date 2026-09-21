r"""Defects found by using the application, and the checks that keep them fixed.

Each of these came from a real session rather than from reading the code, which is
why they are grouped: reading had already missed them once.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_regressions.py -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.access import DEPARTMENTS
from app.auth import password_hash
from app.database import AnswerFeedback, AuditEvent, ChatMessage, Conversation, SessionLocal, User, initialise_database
from app.main import app
from app.tools import find_tool


PASSWORD = "RegressionTestPassword-2026"


# --------------------------------------------------------------------------
# « À quoi ai-je accès ? » is the commonest question a new account has
# --------------------------------------------------------------------------
# Observed: "dis moi les trucs sur lesquels j'ai accès en tant que user" matched no
# tool, fell through to documentary search, retrieved unrelated extracts and was
# refused — on an account that could in fact read three documents.

ACCESS_TOOLS = {"list_documents", "my_access"}


@pytest.mark.parametrize("question", [
    "Salut , dis moi les trucs sur lesquels j'ai accés en tant que \"user \" ?",
    "A quoi ai-je accès ?",
    "À quoi j'ai accès exactement ?",
    "Que puis-je consulter ?",
    "Que puis-je lire avec ce compte ?",
    "Quels sont mes documents accessibles ?",
    "Quels documents puis-je consulter ?",
    "Liste des documents s'il te plaît",
    "Quels sont les documents auxquels j'ai accès ?",
])
def test_asking_what_i_may_read_reaches_a_tool(question):
    """Never the documentary search, which is what produced the original refusal.

    Which of the two tools answers is a later refinement: « à quoi ai-je accès »
    went to `my_access`, which also states the role, the service and what the
    account *cannot* see. `list_documents` keeps the questions that really ask for
    a list. Both are correct answers to the defect; neither is a semantic search.
    """
    tool = find_tool(question)
    assert tool is not None and tool.name in ACCESS_TOOLS, question


@pytest.mark.parametrize("question", [
    "Quelle est la durée des congés annuels ?",
    "Parle-moi de la charte de télétravail",
    "Que dit le document sur les mots de passe ?",
    "Résume la procédure d'incident",
])
def test_a_real_documentary_question_still_goes_to_the_documents(question):
    """The new patterns must not swallow ordinary questions that mention documents."""
    assert find_tool(question) is None, question


def test_counting_and_listing_stay_distinct():
    assert find_tool("Combien de documents ?").name == "count_documents"
    assert find_tool("Quels documents ?").name == "list_documents"


# --------------------------------------------------------------------------
# An account created by an administrator must be able to carry a department
# --------------------------------------------------------------------------
# Observed: the admin form sent only {username, password, role}, so every account
# created through the interface was born « Non rattaché » and could read nothing
# but transverse documents. The API accepted a department all along — the form
# simply never offered one, and there was no way to set one afterwards either.

@pytest.fixture
def administrator():
    initialise_database()
    run = uuid.uuid4().hex[:8]
    name = f"reg-admin-{run}"
    created: list[str] = [name]
    with SessionLocal() as db:
        db.add(User(username=name, password_hash=password_hash.hash(PASSWORD),
                    role="admin", department="technique", status="active"))
        db.commit()
    try:
        yield name, run, created
    finally:
        with SessionLocal() as db:
            accounts = db.scalars(select(User).where(User.username.in_(created))).all()
            identifiers = [account.id for account in accounts]
            if identifiers:
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


def test_an_administrator_can_create_an_account_with_a_department(administrator):
    name, run, created = administrator
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": name, "password": PASSWORD}).status_code == 204
        new_name = f"reg-user-{run}"
        created.append(new_name)
        response = client.post("/admin/users", json={
            "username": new_name, "password": PASSWORD, "role": "user", "department": "rh",
        })
        assert response.status_code == 201, response.text
        assert response.json()["department"] == "rh"
        assert response.json()["department_label"] == "Ressources humaines"


def test_an_unattached_account_can_be_attached_afterwards(administrator):
    """The fix for accounts already created without one — no need to recreate them."""
    name, run, created = administrator
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": name, "password": PASSWORD}).status_code == 204
        orphan = f"reg-orphan-{run}"
        created.append(orphan)
        made = client.post("/admin/users", json={
            "username": orphan, "password": PASSWORD, "role": "user",
        })
        assert made.status_code == 201
        assert made.json()["department"] is None
        assert made.json()["department_label"] == "Non rattaché"

        patched = client.patch(f"/admin/users/{made.json()['id']}", json={"department": "logistique"})
        assert patched.status_code == 200
        assert patched.json()["department"] == "logistique"


@pytest.mark.parametrize("department", sorted(DEPARTMENTS))
def test_every_department_is_accepted_at_creation(administrator, department):
    name, run, created = administrator
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": name, "password": PASSWORD})
        account = f"reg-{department}-{run}"
        created.append(account)
        response = client.post("/admin/users", json={
            "username": account, "password": PASSWORD, "role": "user", "department": department,
        })
        assert response.status_code == 201, response.text


def test_an_invented_department_is_still_refused(administrator):
    name, run, created = administrator
    with TestClient(app) as client:
        client.post("/auth/login", json={"username": name, "password": PASSWORD})
        response = client.post("/admin/users", json={
            "username": f"reg-bad-{run}", "password": PASSWORD, "role": "user",
            "department": "direction-generale",
        })
        assert response.status_code == 422


# --------------------------------------------------------------------------
# One verdict per answer and per account
# --------------------------------------------------------------------------
# Observed in the database: the same answer carried both "wrong" and "useful",
# because the endpoint always inserted. Whatever evaluation set is built from
# these rows would then contain a contradiction.

def test_changing_your_mind_replaces_the_verdict(administrator):
    name, _, _ = administrator
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": name, "password": PASSWORD}).status_code == 204
        with SessionLocal() as db:
            account_id = db.scalar(select(User.id).where(User.username == name))
            conversation = Conversation(user_id=account_id, title="Test")
            db.add(conversation)
            db.flush()
            db.add(ChatMessage(conversation_id=conversation.id, role="user", content="Une question ?"))
            db.flush()
            answer = ChatMessage(conversation_id=conversation.id, role="assistant", content="Une réponse.")
            db.add(answer)
            db.commit()
            message_id = answer.id

        assert client.post("/feedback", json={"message_id": message_id, "verdict": "wrong"}).status_code == 204
        assert client.post("/feedback", json={"message_id": message_id, "verdict": "useful"}).status_code == 204

        with SessionLocal() as db:
            rows = db.scalars(select(AnswerFeedback).where(AnswerFeedback.message_id == message_id)).all()
            assert len(rows) == 1, f"{len(rows)} avis enregistrés pour une seule réponse"
            assert rows[0].verdict == "useful"
            assert rows[0].question == "Une question ?", "la question doit rester attachée à l'avis"
