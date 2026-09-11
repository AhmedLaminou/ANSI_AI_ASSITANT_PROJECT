"""End-to-end local smoke test. It creates and removes its own test accounts and document."""

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.auth import password_hash
from app.database import AuditEvent, ChatMessage, Conversation, DocumentChunk, DocumentRecord, SessionLocal, User
from app.main import app
from app.rag import DOCUMENT_STORAGE_DIR


admin_name = f"smoke-admin-{uuid.uuid4().hex[:8]}"
reader_name = f"smoke-reader-{uuid.uuid4().hex[:8]}"
password = "SmokeTestPassword-2026"
document_id = None

with SessionLocal() as db:
    db.add_all([
        User(username=admin_name, password_hash=password_hash.hash(password), role="admin"),
        User(username=reader_name, password_hash=password_hash.hash(password), role="user"),
    ])
    db.commit()

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
        document_id = uploaded.json()["id"]
        assert uploaded.json()["chunks_indexed"] > 0

        answer = client.post("/chat", json={"message": "Quelle est la date de début du projet pilote ANSI ?"})
        assert answer.status_code == 200, answer.text
        assert answer.json()["sources"], answer.text
        conversation_id = answer.json()["conversation"]["id"]
        assert conversation_id

        renamed = client.patch(f"/conversations/{conversation_id}", json={"title": "Projet pilote"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["title"] == "Projet pilote"

        client.post("/auth/logout")
        reader_login = client.post("/auth/login", json={"username": reader_name, "password": password})
        assert reader_login.status_code == 204, reader_login.text
        assert client.get("/documents").json() == []

    print("smoke_rag_ok")
finally:
    with SessionLocal() as db:
        if document_id:
            stored = db.get(DocumentRecord, document_id)
            if stored:
                (DOCUMENT_STORAGE_DIR / stored.stored_filename).unlink(missing_ok=True)
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
            db.execute(delete(AuditEvent).where(AuditEvent.document_id == document_id))
            db.execute(delete(DocumentRecord).where(DocumentRecord.id == document_id))
        user_ids = [user.id for user in db.query(User).filter(User.username.in_([admin_name, reader_name])).all()]
        if user_ids:
            conversation_ids = [conversation.id for conversation in db.query(Conversation).filter(Conversation.user_id.in_(user_ids)).all()]
            if conversation_ids:
                db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversation_ids)))
                db.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(user_ids)))
            db.execute(delete(User).where(User.id.in_(user_ids)))
        db.commit()
