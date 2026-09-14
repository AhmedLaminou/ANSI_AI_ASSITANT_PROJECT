"""End-to-end local smoke test. It creates and removes its own test accounts and documents.

Requires Ollama running with the configured chat and embedding models.
Run with:  python -m tests.smoke_rag
"""

import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import password_hash
from app.database import (
    AuditEvent,
    ChatMessage,
    Conversation,
    DocumentChunk,
    DocumentRecord,
    SessionLocal,
    User,
    engine,
    initialise_database,
)
from app.main import app
from app.rag import DOCUMENT_STORAGE_DIR


initialise_database()  # a fresh PostgreSQL instance has no tables yet
print(f"Base de données : {engine.dialect.name}")

admin_name = f"smoke-admin-{uuid.uuid4().hex[:8]}"
reader_name = f"smoke-reader-{uuid.uuid4().hex[:8]}"
password = "SmokeTestPassword-2026"
admin_id: int | None = None

with SessionLocal() as db:
    admin = User(username=admin_name, password_hash=password_hash.hash(password), role="admin")
    db.add_all([admin, User(username=reader_name, password_hash=password_hash.hash(password), role="user")])
    db.commit()
    admin_id = admin.id

try:
    with TestClient(app) as client:
        login = client.post("/auth/login", json={"username": admin_name, "password": password})
        assert login.status_code == 204, login.text

        uploaded = client.post(
            "/documents/upload",
            data={"title": "Note de démonstration", "classification": "interne", "allowed_roles": "admin"},
            files={"file": ("demonstration.txt", b"Le projet pilote ANSI commence le 15 octobre 2026. Le responsable est Amina.", "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text
        assert uploaded.json()["chunks_indexed"] > 0
        assert uploaded.json()["version"] == 1
        assert uploaded.json()["is_current"] is True

        # --- non-streaming answer -------------------------------------------------
        answer = client.post("/chat", json={"message": "Quelle est la date de début du projet pilote ANSI ?"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["sources"], answer.text
        assert "</think>" not in answer.json()["answer"], "reasoning leaked into the answer"
        conversation_id = answer.json()["conversation"]["id"]
        assert conversation_id

        renamed = client.patch(f"/conversations/{conversation_id}", json={"title": "Projet pilote"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["title"] == "Projet pilote"

        # --- streaming answer -----------------------------------------------------
        streamed = client.post("/chat/stream", json={"message": "Qui est responsable du projet pilote ?"})
        assert streamed.status_code == 200, streamed.text
        events = [json.loads(line) for line in streamed.text.splitlines() if line.strip()]
        kinds = [event["type"] for event in events]
        assert kinds[0] == "meta", kinds[:3]
        assert "answer_start" in kinds, kinds
        assert kinds[-1] == "done", kinds[-3:]
        assert not any(event["type"] == "error" for event in events), events
        assert events[0]["sources"], "streamed answer carried no sources"
        tokens = "".join(event["value"] for event in events if event["type"] == "token")
        assert tokens.strip(), "no answer tokens were streamed"
        assert "</think>" not in tokens, "reasoning leaked into the stream"

        # --- versioning -----------------------------------------------------------
        second = client.post(
            "/documents/upload",
            data={"title": "Note de démonstration (corrigée)", "classification": "interne", "allowed_roles": "admin"},
            files={"file": ("demonstration.txt", b"Le projet pilote ANSI commence le 20 octobre 2026. Le responsable est Amina.", "text/plain")},
        )
        assert second.status_code == 201, second.text
        assert second.json()["version"] == 2, second.json()
        current = client.get("/documents").json()
        assert len(current) == 1, "superseded version still listed as current"
        assert current[0]["version"] == 2
        assert len(client.get("/documents", params={"include_superseded": True}).json()) == 2

        # --- account lifecycle ----------------------------------------------------
        reader = next(item for item in client.get("/admin/users").json() if item["username"] == reader_name)
        promoted = client.patch(f"/admin/users/{reader['id']}", json={"role": "document_manager"})
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["role"] == "document_manager"

        reset = client.post(f"/admin/users/{reader['id']}/password", json={"password": "RotatedPassword-2026"})
        assert reset.status_code == 204, reset.text

        deactivated = client.patch(f"/admin/users/{reader['id']}", json={"is_active": False})
        assert deactivated.status_code == 200, deactivated.text
        assert deactivated.json()["is_active"] is False

        # an admin must not be able to lock themselves out
        self_lock = client.patch(f"/admin/users/{admin_id}", json={"is_active": False})
        assert self_lock.status_code == 422, self_lock.text

        # --- access control -------------------------------------------------------
        client.post("/auth/logout")
        blocked = client.post("/auth/login", json={"username": reader_name, "password": "RotatedPassword-2026"})
        assert blocked.status_code == 401, "a deactivated account was able to log in"

        with SessionLocal() as db:
            account = db.scalar(select(User).where(User.username == reader_name))
            account.is_active = True
            db.commit()

        reader_login = client.post("/auth/login", json={"username": reader_name, "password": "RotatedPassword-2026"})
        assert reader_login.status_code == 204, reader_login.text
        assert client.get("/documents").json() == [], "reader saw an admin-only document"

    print("smoke_rag_ok")
finally:
    with SessionLocal() as db:
        documents = db.scalars(select(DocumentRecord).where(DocumentRecord.created_by == admin_id)).all()
        for document in documents:
            (DOCUMENT_STORAGE_DIR / document.stored_filename).unlink(missing_ok=True)
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
            db.execute(delete(AuditEvent).where(AuditEvent.document_id == document.id))
            db.execute(delete(DocumentRecord).where(DocumentRecord.id == document.id))
        user_ids = [user.id for user in db.query(User).filter(User.username.in_([admin_name, reader_name])).all()]
        if user_ids:
            conversation_ids = [row.id for row in db.query(Conversation).filter(Conversation.user_id.in_(user_ids)).all()]
            if conversation_ids:
                db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversation_ids)))
                db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(user_ids)))
            db.execute(delete(User).where(User.id.in_(user_ids)))
        db.commit()
