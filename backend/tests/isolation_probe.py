r"""« Rien ne sort de la machine », verifie plutot que declare.

    .\.venv\Scripts\python.exe -m tests.isolation_probe

Requires Ollama. Creates its own account and documents, and removes them all.

C'est la contrainte absolue du projet, et jusqu'ici elle n'avait jamais ete verifiee
autrement qu'en relisant la configuration. Relire un fichier de configuration prouve ce
qui est ecrit, pas ce qui se passe.

Deux controles du §35, jamais realises :

**Reseau sortant.** Tout appel sortant de l'application passe par `httpx`. La sonde
intercepte `httpx.AsyncClient.send` et enregistre l'hote de *chaque* requete emise
pendant un parcours complet — import, indexation, recherche, question, streaming. Chaque
hote doit etre une adresse de bouclage. Le controle porte sur le trafic reellement emis,
pas sur l'intention.

**Fuite dans les journaux.** Un canari est place dans le contenu d'un document, un autre
sert de mot de passe. Tout ce qui est journalise pendant le parcours est capture, a la
racine et au niveau DEBUG, puis fouille : ni le contenu du document, ni le mot de passe,
ni le jeton de session ne doivent s'y trouver.

S'y ajoutent deux controles statiques : aucune URL non locale dans le code de
l'application, et aucune ressource distante dans le frontend — le POC interdit tout CDN
au moment de l'execution.
"""

import json
import logging
import re
import sys
import uuid
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import password_hash
from app.config import BACKEND_DIR, PROJECT_DIR
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
from app.rag import DOCUMENT_STORAGE_DIR


RUN = uuid.uuid4().hex[:6]
SECRET = f"CANARI-CONTENU-{RUN.upper()}"
PASSWORD = f"CanariMotDePasse-{RUN.upper()}"

DOCUMENT = (
    "Note interne de test d isolation.\n"
    f"Le code de verification confidentiel est {SECRET}.\n"
    "Le delai de traitement d une demande est de 5 jours ouvrables.\n"
)

LOOPBACK = {"127.0.0.1", "localhost", "::1", "testserver"}

results: list[tuple[bool, str]] = []


def check(passed: bool, label: str, detail: str = "") -> None:
    results.append((passed, label))
    print(f"[{'OK ' if passed else 'ECHEC'}] {label}", flush=True)
    if detail and not passed:
        print(f"         {detail}", flush=True)


def is_loopback(host: str) -> bool:
    return host in LOOPBACK or host.startswith("127.")


# ---------------------------------------------------------------------------
# Interception du trafic sortant, posee avant toute requete
# ---------------------------------------------------------------------------
CONTACTED: list[str] = []
_original_send = httpx.AsyncClient.send


async def recording_send(self, request, **kwargs):
    CONTACTED.append(request.url.host)
    return await _original_send(self, request, **kwargs)


httpx.AsyncClient.send = recording_send


# ---------------------------------------------------------------------------
# Capture de tout ce qui est journalise
# ---------------------------------------------------------------------------
class Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:  # noqa: BLE001 - a broken record must not stop the probe
            self.lines.append(str(record.msg))


recorder = Recorder()
root = logging.getLogger()
root.addHandler(recorder)
previous_level = root.level
root.setLevel(logging.DEBUG)

# app.main is imported after the hooks so nothing escapes them.
from app.main import app  # noqa: E402

initialise_database()

username = f"isolation-{RUN}"
with SessionLocal() as db:
    db.add(User(username=username, password_hash=password_hash.hash(PASSWORD),
                role="admin", department="technique", status="active"))
    db.commit()
    account_id = db.scalar(select(User.id).where(User.username == username))

token = ""
try:
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": username, "password": PASSWORD}).status_code == 204
        token = client.cookies.get("access_token") or ""

        # a failed sign-in too: the wrong password must not be written down either
        client.post("/auth/login", json={"username": username, "password": "MauvaisMotDePasse-2026"})

        uploaded = client.post(
            "/documents/upload",
            data={"title": f"Note isolation {RUN}", "classification": "confidentiel",
                  "allowed_roles": "admin,document_manager,user", "department": "technique"},
            files={"file": (f"isolation-{RUN}.txt", DOCUMENT.encode("utf-8"), "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text

        client.post("/search", json={"query": "code de verification confidentiel", "limit": 5})
        answer = client.post("/chat", json={"message": "Quel est le delai de traitement d une demande ?"})
        assert answer.status_code == 200, answer.text

        streamed = client.post("/chat/stream", json={"message": "Rappelle le delai de traitement."})
        assert streamed.status_code == 200

    # ---------------- reseau sortant ------------------------------------
    hosts = sorted(set(CONTACTED))
    print(f"\nhotes contactes pendant le parcours : {hosts}\n")
    check(bool(CONTACTED), "du trafic sortant a bien ete observe (sinon le controle ne prouve rien)")
    escaping = [host for host in hosts if not is_loopback(host)]
    check(not escaping, "aucune requete ne quitte la machine", f"hotes non locaux : {escaping}")

    # ---------------- fuite dans les journaux ---------------------------
    journal = "\n".join(recorder.lines)
    check(SECRET not in journal, "le contenu du document n'apparait pas dans les journaux")
    check(PASSWORD not in journal, "le mot de passe n'apparait pas dans les journaux")
    check("MauvaisMotDePasse-2026" not in journal,
          "un mot de passe errone n'apparait pas non plus dans les journaux")
    check(not token or token not in journal, "le jeton de session n'apparait pas dans les journaux")

finally:
    httpx.AsyncClient.send = _original_send
    root.removeHandler(recorder)
    root.setLevel(previous_level)
    with SessionLocal() as db:
        for document in db.scalars(select(DocumentRecord).where(DocumentRecord.created_by == account_id)).all():
            (DOCUMENT_STORAGE_DIR / document.stored_filename).unlink(missing_ok=True)
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
            db.execute(delete(AuditEvent).where(AuditEvent.document_id == document.id))
            db.execute(delete(DocumentRecord).where(DocumentRecord.id == document.id))
        conversations = [
            row.id for row in db.scalars(select(Conversation).where(Conversation.user_id == account_id)).all()
        ]
        if conversations:
            messages = [
                row.id for row in
                db.scalars(select(ChatMessage).where(ChatMessage.conversation_id.in_(conversations))).all()
            ]
            if messages:
                db.execute(delete(AnswerFeedback).where(AnswerFeedback.message_id.in_(messages)))
            db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversations)))
            db.execute(delete(Conversation).where(Conversation.id.in_(conversations)))
        db.execute(delete(AnswerFeedback).where(AnswerFeedback.user_id == account_id))
        db.execute(delete(AuditEvent).where(AuditEvent.actor_id == account_id))
        db.execute(delete(User).where(User.id == account_id))
        db.commit()

# ---------------------------------------------------------------------------
# Controles statiques : ce que le code pourrait contacter, meme sans le faire
# ---------------------------------------------------------------------------
URL = re.compile(r"https?://([A-Za-z0-9._:\[\]-]+)")

offending: list[str] = []
for source in sorted((BACKEND_DIR / "app").glob("*.py")):
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        for host in URL.findall(line):
            # A host may end a sentence in a comment ("...from http://localhost."),
            # and the dot belongs to the prose, not to the name.
            host = host.rstrip(".").split(":")[0]
            if host and not is_loopback(host):
                offending.append(f"{source.name}:{number} -> {host}")
check(not offending, "aucune URL non locale dans le code de l'application", "; ".join(offending))

remote_assets: list[str] = []
frontend = PROJECT_DIR / "frontend"
candidates = [frontend / "index.html"] + sorted((frontend / "src").rglob("*.jsx")) \
    + sorted((frontend / "src").rglob("*.css")) + sorted((frontend / "src").rglob("*.js"))
for asset in candidates:
    if not asset.exists():
        continue
    for number, line in enumerate(asset.read_text(encoding="utf-8").splitlines(), start=1):
        if re.search(r'(?:src|href)\s*=\s*["\']https?://', line) or "@import url(http" in line:
            remote_assets.append(f"{asset.relative_to(PROJECT_DIR)}:{number}")
check(not remote_assets, "aucune ressource distante dans le frontend (pas de CDN a l'execution)",
      "; ".join(remote_assets))

failures = [label for passed, label in results if not passed]
print("\n" + "=" * 66)
print(f"{len(results) - len(failures)}/{len(results)} controles passes")
if failures:
    print("\nECHECS :")
    for label in failures:
        print(f"  - {label}")
print("=" * 66)
sys.exit(1 if failures else 0)
