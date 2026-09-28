r"""Gaps, human referral, validated answers, follow-up questions.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_knowledge.py -q

The whole chat pipeline runs, offline: the embedding model is replaced by a
bag-of-words hash (texts sharing words land close together, deterministically) and
the chat model by a fake that returns a scripted reply and records what it was sent.
What is under test is everything around the models — routing, perimeter, recording,
referral, precedence — which is where these features can go wrong.
"""

import json
import math
import types
import uuid
import zlib
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app import ingestion, main
from app.auth import password_hash
from app.database import (
    AnswerFeedback,
    AuditEvent,
    ChatMessage,
    Conversation,
    DocumentChunk,
    DocumentRecord,
    ServiceContact,
    SessionLocal,
    UnansweredQuestion,
    User,
    ValidatedAnswer,
    initialise_database,
)
from app.knowledge import (
    content_words,
    contact_for,
    find_validated_answer,
    is_follow_up,
    normalise,
    previous_turn,
    referral_sentence,
)
from app.refusals import cluster_gaps, looks_like_refusal


PASSWORD = "KnowledgeTestPassword-2026"
REFUSAL_REPLY = "L'information n'est pas présente dans les extraits fournis."


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

def bag_of_words(text: str) -> list[float]:
    vector = [0.0] * 768
    for word in normalise(text).split():
        vector[zlib.crc32(word.encode()) % 768] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


async def fake_embed(texts):
    return [bag_of_words(text) for text in texts]


class FakeModel:
    """Stands in for Ollama's /api/chat, both plain and streamed."""

    reply = "Le délai est de 5 jours ouvrables [S1]."
    sent: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        FakeModel.sent.append(json)
        return types.SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"message": {"content": FakeModel.reply}},
        )

    def stream(self, method, url, json=None):
        FakeModel.sent.append(json)
        return FakeStream(FakeModel.reply)


class FakeStream:
    def __init__(self, content):
        self.content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        yield json.dumps({"message": {"content": self.content}})


@pytest.fixture(autouse=True)
def offline_models(monkeypatch):
    monkeypatch.setattr(main, "embed_texts", fake_embed)
    monkeypatch.setattr(ingestion, "embed_texts", fake_embed)
    monkeypatch.setattr(main, "httpx", types.SimpleNamespace(AsyncClient=FakeModel, HTTPError=httpx.HTTPError))
    # Every retrieval counts as relevant: what decides refusal here is the model's reply.
    monkeypatch.setattr(main, "RELEVANCE_THRESHOLD", -1.0)
    FakeModel.reply = "Le délai est de 5 jours ouvrables [S1]."
    FakeModel.sent = []
    main._rate_buckets.clear()


@pytest.fixture
def world():
    """Accounts in three services, a finance document, and the contact table as it
    was — restored afterwards, since it is shared with whatever the operator set."""
    initialise_database()
    run = uuid.uuid4().hex[:8]
    people = {
        "admin": ("admin", "technique"),
        "rh": ("user", "rh"),
        "finance": ("user", "finance"),
    }
    with SessionLocal() as db:
        saved_contacts = [
            {c: getattr(row, c) for c in ("department", "name", "email", "phone", "note")}
            for row in db.scalars(select(ServiceContact)).all()
        ]
        for key, (role, department) in people.items():
            db.add(User(username=f"kn-{key}-{run}", email=f"kn.{key}.{run}@ansi.ne",
                        password_hash=password_hash.hash(PASSWORD),
                        role=role, department=department, status="active"))
        db.commit()
        ids = {key: db.scalar(select(User.id).where(User.username == f"kn-{key}-{run}")) for key in people}

        document = DocumentRecord(
            title=f"Note finances {run}", original_filename=f"finances-{run}.txt",
            stored_filename=f"kn-{run}.txt", content_type="text/plain", classification="interne",
            allowed_roles="admin,document_manager,user", created_by=ids["admin"], department="finance",
        )
        db.add(document)
        db.flush()
        text = f"Remboursement des frais de mission {run} : delai de traitement de cinq jours ouvrables."
        db.add(DocumentChunk(document_id=document.id, ordinal=0, page_number=1, content=text,
                             embedding=bag_of_words(text)))
        db.commit()
        document_id = document.id
    try:
        yield {"run": run, "ids": ids, "document_id": document_id,
               "email": {key: f"kn.{key}.{run}@ansi.ne" for key in people}}
    finally:
        with SessionLocal() as db:
            all_ids = list(ids.values())
            conversations = [c.id for c in db.scalars(select(Conversation).where(Conversation.user_id.in_(all_ids))).all()]
            if conversations:
                messages = [m.id for m in db.scalars(select(ChatMessage).where(ChatMessage.conversation_id.in_(conversations))).all()]
                if messages:
                    db.execute(delete(AnswerFeedback).where(AnswerFeedback.message_id.in_(messages)))
                db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversations)))
                db.execute(delete(Conversation).where(Conversation.id.in_(conversations)))
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
            db.execute(delete(AuditEvent).where(AuditEvent.document_id == document_id))
            db.execute(delete(DocumentRecord).where(DocumentRecord.id == document_id))
            db.execute(delete(ValidatedAnswer).where(ValidatedAnswer.validated_by.in_(all_ids)))
            db.execute(delete(UnansweredQuestion).where(UnansweredQuestion.user_id.in_(all_ids)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(all_ids)))
            db.execute(delete(User).where(User.id.in_(all_ids)))
            db.execute(delete(ServiceContact))
            for row in saved_contacts:
                db.add(ServiceContact(**row))
            db.commit()


def client_for(world, key: str) -> TestClient:
    client = TestClient(app_instance())
    client.__enter__()
    assert client.post("/auth/login", json={"username": world["email"][key], "password": PASSWORD}).status_code == 204
    return client


def app_instance():
    return main.app


# --------------------------------------------------------------------------
# Refusal detection and gap grouping
# --------------------------------------------------------------------------

@pytest.mark.parametrize("answer,expected", [
    ("L'information n'est pas présente dans les extraits fournis.", True),
    ("Je ne trouve pas d'information suffisamment pertinente.", True),
    ("Les extraits ne contiennent pas cette donnée.", True),
    ("Le délai est de 5 jours ouvrables [S1].", False),
])
def test_refusal_detection(answer, expected):
    assert looks_like_refusal(answer) is expected


def gap(question, embedding, identifier):
    return UnansweredQuestion(id=identifier, user_id=1, department="rh", question=question,
                              reason="model_refusal", embedding=embedding,
                              created_at=datetime.now(timezone.utc) + timedelta(seconds=identifier))


def test_similar_questions_form_one_gap_and_the_largest_comes_first():
    telework = bag_of_words("teletravail jours semaine autorise")
    rows = [
        gap("Combien de jours de télétravail ?", telework, 1),
        gap("Le télétravail est-il autorisé ?", bag_of_words("teletravail autorise jours semaine agents"), 2),
        gap("Jours de télétravail par semaine", bag_of_words("teletravail jours semaine"), 3),
        gap("Où est la cantine ?", bag_of_words("cantine batiment horaires repas"), 4),
    ]
    clusters = cluster_gaps(rows, threshold=0.50)
    assert [len(c) for c in clusters] == [3, 1]


def test_questions_without_embedding_group_by_identical_text():
    rows = [gap("Wifi invités ?", None, 1), gap("wifi  invites", None, 2), gap("Autre chose", None, 3)]
    assert sorted(len(c) for c in cluster_gaps(rows)) == [1, 2]


# --------------------------------------------------------------------------
# Gaps are recorded where refusals actually happen
# --------------------------------------------------------------------------

def test_a_model_refusal_is_recorded_as_a_gap(world):
    """Most refusals come from the model, not the graph: the threshold separates almost
    nothing, so extracts are found and the model says they do not answer."""
    FakeModel.reply = REFUSAL_REPLY
    client = client_for(world, "finance")
    answer = client.post("/chat", json={"message": f"Quelle est la politique de covoiturage {world['run']} ?"})
    client.__exit__(None, None, None)
    assert answer.status_code == 200
    with SessionLocal() as db:
        rows = db.scalars(select(UnansweredQuestion).where(UnansweredQuestion.user_id == world["ids"]["finance"])).all()
    assert len(rows) == 1
    assert rows[0].reason == "model_refusal"
    assert rows[0].department == "finance", "the department is frozen at the time of the question"
    assert rows[0].embedding, "kept for grouping"


def test_a_streamed_refusal_is_recorded_too(world):
    FakeModel.reply = REFUSAL_REPLY
    client = client_for(world, "finance")
    streamed = client.post("/chat/stream", json={"message": f"Horaires de la cantine {world['run']} ?"})
    client.__exit__(None, None, None)
    assert streamed.status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(UnansweredQuestion).where(UnansweredQuestion.user_id == world["ids"]["finance"]))


def test_an_answered_question_is_not_a_gap(world):
    client = client_for(world, "finance")
    client.post("/chat", json={"message": f"Frais de mission {world['run']} : quel délai ?"})
    client.__exit__(None, None, None)
    with SessionLocal() as db:
        assert db.scalar(select(UnansweredQuestion).where(UnansweredQuestion.user_id == world["ids"]["finance"])) is None


def test_a_tool_answer_is_not_a_gap(world):
    """« Quels droits ai-je ? » has no document answer by design; it is not a gap."""
    client = client_for(world, "rh")
    client.post("/chat", json={"message": "Quels sont mes droits ?"})
    client.__exit__(None, None, None)
    with SessionLocal() as db:
        assert db.scalar(select(UnansweredQuestion).where(UnansweredQuestion.user_id == world["ids"]["rh"])) is None


def test_the_administrator_sees_gaps_grouped_and_can_resolve_them(world):
    FakeModel.reply = REFUSAL_REPLY
    finance = client_for(world, "finance")
    for phrasing in ("Politique de covoiturage", "Politique covoiturage agents", "Covoiturage politique"):
        finance.post("/chat", json={"message": f"{phrasing} {world['run']} ?"})
    finance.__exit__(None, None, None)

    admin = client_for(world, "admin")
    body = admin.get("/admin/gaps").json()
    ours = [g for g in body["gaps"] if any(world["run"] in q for q in g["questions"])]
    assert ours and ours[0]["count"] == 3, [g["count"] for g in ours]
    assert "Finance / comptabilité" in ours[0]["departments"]

    assert admin.post("/admin/gaps/resolve", json={"ids": ours[0]["ids"]}).status_code == 204
    after = admin.get("/admin/gaps").json()
    admin.__exit__(None, None, None)
    assert not any(any(world["run"] in q for q in g["questions"]) for g in after["gaps"])


def test_an_agent_cannot_read_the_gaps(world):
    client = client_for(world, "rh")
    assert client.get("/admin/gaps").status_code == 403
    client.__exit__(None, None, None)


# --------------------------------------------------------------------------
# A refusal points to a person
# --------------------------------------------------------------------------

def test_a_refusal_names_the_service_contact(world):
    with SessionLocal() as db:
        db.merge(ServiceContact(department="finance", name="Moussa Issa", email="m.issa@ansi.ne", phone="1234"))
        db.commit()
    FakeModel.reply = REFUSAL_REPLY
    client = client_for(world, "finance")
    answer = client.post("/chat", json={"message": f"Question sans reponse {world['run']} ?"}).json()["answer"]
    client.__exit__(None, None, None)
    assert "Moussa Issa" in answer and "m.issa@ansi.ne" in answer


def test_the_general_contact_is_the_fallback(world):
    with SessionLocal() as db:
        db.execute(delete(ServiceContact))
        db.add(ServiceContact(department="transverse", name="Accueil DSI", email="accueil@ansi.ne"))
        db.commit()
        agent = db.get(User, world["ids"]["rh"])
        assert contact_for(db, agent).name == "Accueil DSI"


def test_an_answered_question_carries_no_referral(world):
    with SessionLocal() as db:
        db.merge(ServiceContact(department="finance", name="Moussa Issa"))
        db.commit()
    client = client_for(world, "finance")
    answer = client.post("/chat", json={"message": f"Frais de mission {world['run']} ?"}).json()["answer"]
    client.__exit__(None, None, None)
    assert "Moussa Issa" not in answer


def test_an_agent_sees_only_their_own_and_the_general_contact(world):
    with SessionLocal() as db:
        db.execute(delete(ServiceContact))
        for department, name in (("finance", "Contact Finances"), ("rh", "Contact RH"), ("transverse", "Accueil")):
            db.add(ServiceContact(department=department, name=name))
        db.commit()
    client = client_for(world, "rh")
    names = {row["name"] for row in client.get("/contacts").json()}
    client.__exit__(None, None, None)
    assert names == {"Contact RH", "Accueil"}


def test_referral_sentence_is_empty_without_a_contact():
    assert referral_sentence(None) == ""


# --------------------------------------------------------------------------
# Validated answers
# --------------------------------------------------------------------------

def test_content_words_ignore_what_carries_no_meaning():
    assert content_words("Quelle est la durée des congés annuels ?") == content_words("Durée des congés annuels")


def validate(world, question, answer, department="transverse", phrasings=()):
    admin = client_for(world, "admin")
    response = admin.post("/admin/validated-answers", json={
        "question": question, "answer": answer, "department": department, "phrasings": list(phrasings),
    })
    admin.__exit__(None, None, None)
    return response


def test_a_validated_answer_is_served_without_the_model(world):
    """Instant and signed: the model is never called."""
    assert validate(world, f"Quel est le code wifi invités {world['run']} ?", "Le code est affiché à l'accueil.").status_code == 201
    client = client_for(world, "rh")
    answer = client.post("/chat", json={"message": f"code wifi invités {world['run']}"}).json()["answer"]
    client.__exit__(None, None, None)
    assert answer.startswith("Le code est affiché à l'accueil.")
    assert "Réponse validée par" in answer
    assert FakeModel.sent == [], "a validated answer must not go through the model"


def test_a_validated_answer_obeys_the_perimeter(world):
    """A finance answer is not served to an HR agent — the document rule, applied."""
    validate(world, f"Plafond des engagements {world['run']} ?", "Réponse finances.", department="finance")
    rh = client_for(world, "rh")
    rh.post("/chat", json={"message": f"Plafond des engagements {world['run']}"})
    rh.__exit__(None, None, None)
    assert FakeModel.sent, "the RH agent must fall through to the documents, not get the finance answer"
    with SessionLocal() as db:
        finance_agent = db.get(User, world["ids"]["finance"])
        assert find_validated_answer(db, finance_agent, f"Plafond des engagements {world['run']}") is not None


def test_a_deactivated_validated_answer_is_no_longer_served(world):
    created = validate(world, f"Horaires accueil {world['run']} ?", "8h-16h.").json()
    admin = client_for(world, "admin")
    admin.patch(f"/admin/validated-answers/{created['id']}", json={"active": False})
    admin.__exit__(None, None, None)
    with SessionLocal() as db:
        assert find_validated_answer(db, db.get(User, world["ids"]["rh"]), f"Horaires accueil {world['run']}") is None


def test_a_vague_phrasing_is_refused(world):
    """One meaningful word would let the answer be served to questions it never covered."""
    response = validate(world, f"Horaires accueil {world['run']} ?", "8h-16h.", phrasings=["Horaires ?"])
    assert response.status_code == 422


def test_editing_the_text_re_signs_it(world):
    created = validate(world, f"Adresse du siège {world['run']} ?", "Avenue de la République.").json()
    admin = client_for(world, "admin")
    updated = admin.patch(f"/admin/validated-answers/{created['id']}",
                          json={"answer": "Avenue de la République, bâtiment B."}).json()
    admin.__exit__(None, None, None)
    assert updated["answer"].endswith("bâtiment B.")
    assert updated["validated_by"].startswith("kn-admin-")


def test_a_tool_question_is_never_shadowed_by_a_validated_answer(world):
    """Tools state facts about the account; no curated text may override them."""
    validate(world, "Quels sont mes droits ?", "Texte validé qui ne devrait jamais être servi.")
    client = client_for(world, "rh")
    answer = client.post("/chat", json={"message": "Quels sont mes droits ?"}).json()["answer"]
    client.__exit__(None, None, None)
    assert "ne devrait jamais" not in answer


# --------------------------------------------------------------------------
# Follow-up questions
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("Et pour un temps partiel ?", True),
    ("et si je suis stagiaire ?", True),
    ("Pareil pour les contractuels ?", True),
    ("Quelle est la durée des congés ?", False),
    ("Et quelle est la procédure complète de demande de congés annuels pour un agent titulaire détaché ?", False),
])
def test_follow_up_detection(question, expected):
    assert is_follow_up(question) is expected


def test_sources_now_carry_the_document_id(world):
    """Needed for a follow-up to give the cited document precedence."""
    client = client_for(world, "finance")
    body = client.post("/chat", json={"message": f"Frais de mission {world['run']} ?"}).json()
    client.__exit__(None, None, None)
    assert any(source.get("document_id") == world["document_id"] for source in body["sources"])


def test_a_follow_up_is_searched_with_the_question_it_follows(world):
    client = client_for(world, "finance")
    first = client.post("/chat", json={"message": f"Remboursement des frais de mission {world['run']} ?"}).json()
    FakeModel.sent = []
    client.post("/chat", json={"message": "Et pour un stagiaire ?", "conversation_id": first["conversation"]["id"]})
    client.__exit__(None, None, None)
    extracts = FakeModel.sent[-1]["messages"][-1]["content"]
    # « Et pour un stagiaire ? » shares no word with the note; it is found only because
    # the search carried the previous question and the cited document had precedence.
    assert world["run"] in extracts


def test_previous_turn_reads_the_last_exchange(world):
    client = client_for(world, "finance")
    first = client.post("/chat", json={"message": f"Frais de mission {world['run']} ?"}).json()
    client.__exit__(None, None, None)
    with SessionLocal() as db:
        conversation = db.get(Conversation, first["conversation"]["id"])
        question, ids, _ = previous_turn(db, conversation)
    assert question == f"Frais de mission {world['run']} ?"
    assert world["document_id"] in ids
