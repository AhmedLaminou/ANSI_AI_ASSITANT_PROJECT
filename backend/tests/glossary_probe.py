r"""Does the per-department glossary change what is retrieved? (phase D)

    .\.venv\Scripts\python.exe -m tests.glossary_probe

Needs Ollama for embeddings, but never generates: it goes through /search, so it runs in
seconds rather than minutes.

`test_glossary.py` proves that « CP » expands differently per service. That is a string
result, not a retrieval result. This measures the thing that actually matters: whether the
right document comes back.

**The experiment is built so the perimeter cannot explain the outcome.** Both documents are
filed as `transverse`, so every account may read both. Neither spells out « CP ». The only
difference between the two accounts is the glossary their question is expanded with, so a
difference in ranking can come from nothing else.
"""

import sys
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import password_hash
from app.database import (
    AuditEvent,
    DocumentChunk,
    DocumentRecord,
    SessionLocal,
    User,
    initialise_database,
)
from app.main import app
from app.rag import DOCUMENT_STORAGE_DIR


RUN = uuid.uuid4().hex[:6]
PASSWORD = "GlossaryProbePassword-2026"

QUESTION = "Quelles sont les regles applicables aux CP ?"

# Neither text contains the letters "CP": only the expansion can connect the question to it.
LEAVE_DOCUMENT = """Procedure interne - conges payes.

Les conges payes annuels sont de 30 jours ouvrables par annee de service. La demande se
depose 15 jours a l avance aupres du responsable hierarchique. Le report d un reliquat sur
l annee suivante est limite a 10 jours ouvrables.
"""

BUDGET_DOCUMENT = """Note interne - credits de paiement, exercice 2026.

Les credits de paiement ouverts au titre de l exercice 2026 couvrent les engagements
anterieurs. Une autorisation d engagement non couverte par un credit de paiement ne peut
donner lieu a aucun decaissement avant l exercice suivant.
"""

results: list[tuple[bool, str]] = []


def check(passed: bool, label: str, detail: str = "") -> None:
    results.append((passed, label))
    print(f"[{'OK ' if passed else 'ECHEC'}] {label}", flush=True)
    if detail and not passed:
        print(f"         {detail}", flush=True)


def titles_ranked(client: TestClient) -> list[str]:
    response = client.post("/search", json={"query": QUESTION, "limit": 5})
    assert response.status_code == 200, response.text
    ordered: list[str] = []
    for hit in response.json()["results"]:
        if hit["title"] not in ordered:
            ordered.append(hit["title"])
    return ordered


initialise_database()

leave_title = f"Conges payes {RUN}"
budget_title = f"Credits de paiement {RUN}"

names = [f"gloss-rh-{RUN}", f"gloss-fin-{RUN}", f"gloss-adm-{RUN}"]
rh_name, fin_name, adm_name = names

with SessionLocal() as db:
    db.add_all([
        User(username=rh_name, password_hash=password_hash.hash(PASSWORD), role="user",
             department="rh", status="active"),
        User(username=fin_name, password_hash=password_hash.hash(PASSWORD), role="user",
             department="finance", status="active"),
        User(username=adm_name, password_hash=password_hash.hash(PASSWORD), role="admin",
             department="technique", status="active"),
    ])
    db.commit()
    account_ids = [a.id for a in db.scalars(select(User).where(User.username.in_(names))).all()]

try:
    with TestClient(app) as manager:
        assert manager.post("/auth/login", json={"username": adm_name, "password": PASSWORD}).status_code == 204
        for title, body in [(leave_title, LEAVE_DOCUMENT), (budget_title, BUDGET_DOCUMENT)]:
            uploaded = manager.post(
                "/documents/upload",
                # transverse on purpose: both accounts may read both documents
                data={"title": title, "classification": "interne",
                      "allowed_roles": "admin,document_manager,user", "department": "transverse"},
                files={"file": (f"{title}.txt", body.encode("utf-8"), "text/plain")},
            )
            assert uploaded.status_code == 201, uploaded.text

    print(f"\nquestion posee par les trois comptes : {QUESTION}\n")

    with TestClient(app) as rh:
        assert rh.post("/auth/login", json={"username": rh_name, "password": PASSWORD}).status_code == 204
        ranked = titles_ranked(rh)
        print(f"  RH       -> {ranked}")
        check(bool(ranked) and ranked[0] == leave_title,
              "le service RH remonte les conges payes en premier", f"classement : {ranked}")

    with TestClient(app) as finance:
        assert finance.post("/auth/login", json={"username": fin_name, "password": PASSWORD}).status_code == 204
        ranked = titles_ranked(finance)
        print(f"  finances -> {ranked}")
        check(bool(ranked) and ranked[0] == budget_title,
              "le service finances remonte les credits de paiement en premier", f"classement : {ranked}")

    with TestClient(app) as admin:
        assert admin.post("/auth/login", json={"username": adm_name, "password": PASSWORD}).status_code == 204
        ranked = titles_ranked(admin)
        print(f"  admin    -> {ranked}")
        check(leave_title in ranked and budget_title in ranked,
              "l administrateur voit les deux lectures de l acronyme", f"classement : {ranked}")

finally:
    with SessionLocal() as db:
        for document in db.scalars(select(DocumentRecord).where(DocumentRecord.created_by.in_(account_ids))).all():
            (DOCUMENT_STORAGE_DIR / document.stored_filename).unlink(missing_ok=True)
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
            db.execute(delete(AuditEvent).where(AuditEvent.document_id == document.id))
            db.execute(delete(DocumentRecord).where(DocumentRecord.id == document.id))
        db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(account_ids)))
        db.execute(delete(User).where(User.id.in_(account_ids)))
        db.commit()

failures = [label for passed, label in results if not passed]
print("\n" + "=" * 66)
print(f"{len(results) - len(failures)}/{len(results)} controles passes")
if failures:
    print("\nECHECS :")
    for label in failures:
        print(f"  - {label}")
print("=" * 66)
sys.exit(1 if failures else 0)
