r"""Mesure la qualite du RAG sur un jeu de questions a reponses connues.

Sans ce harnais, tout changement de modele, de decoupage ou de seuil releve de l'intuition.
Le script cree son propre compte et ses propres documents, puis les supprime.

    .\.venv\Scripts\python.exe -m tests.evaluate
    .\.venv\Scripts\python.exe -m tests.evaluate --model qwen3:0.6b

Mesures produites :
  - Exactitude        : la reponse contient les elements attendus
  - Sources correctes : le document attendu figure parmi les sources citees
  - Refus corrects    : sur les questions sans reponse, l'assistant refuse au lieu d'inventer
  - Latence           : secondes par question
"""

import argparse
import json
import time
import unicodedata
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import password_hash
from app.config import get_settings
from app.database import AuditEvent, ChatMessage, Conversation, DocumentChunk, DocumentRecord, SessionLocal, User
from app.main import app
from app.rag import DOCUMENT_STORAGE_DIR


DATASET = Path(__file__).parent / "evaluation" / "dataset.json"
REFUSAL_HINTS = (
    "ne trouve pas",
    "pas présente",
    "pas disponible",
    "aucun document",
    "pas d'information",
    "ne contiennent pas",
    "ne permettent pas",
)


def normalise(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text.lower())
    return "".join(character for character in stripped if unicodedata.category(character) != "Mn")


def looks_like_refusal(answer: str) -> bool:
    lowered = normalise(answer)
    return any(normalise(hint) in lowered for hint in REFUSAL_HINTS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evalue la qualite des reponses du POC ANSI.")
    parser.add_argument("--model", help="Modele de chat a tester (par defaut celui du .env)")
    parser.add_argument("--verbose", action="store_true", help="Affiche chaque reponse complete")
    parser.add_argument(
        "--attempts",
        type=int,
        help="Tentatives de recherche. 1 desactive la reformulation, 2 l'active.",
    )
    arguments = parser.parse_args()

    settings = get_settings()
    if arguments.model:
        settings.ollama_chat_model = arguments.model
    if arguments.attempts:
        settings.max_retrieval_attempts = arguments.attempts
    # The rate limit guards interactive users; a batch harness would trip it on a fast model.
    settings.chat_rate_limit_per_minute = 0

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    account = f"eval-{uuid.uuid4().hex[:8]}"
    password = "EvaluationPassword-2026"

    with SessionLocal() as db:
        operator = User(username=account, password_hash=password_hash.hash(password), role="admin")
        db.add(operator)
        db.commit()
        operator_id = operator.id

    results = []
    try:
        with TestClient(app) as client:
            assert client.post("/auth/login", json={"username": account, "password": password}).status_code == 204

            print(f"Modele : {settings.ollama_chat_model}  |  Embeddings : {settings.ollama_embedding_model}")
            print(f"Indexation de {len(dataset['documents'])} documents…")
            for document in dataset["documents"]:
                response = client.post(
                    "/documents/upload",
                    data={
                        "title": document["title"],
                        "classification": document["classification"],
                        "allowed_roles": document["allowed_roles"],
                    },
                    files={"file": (document["filename"], document["content"].encode("utf-8"), "text/plain")},
                )
                assert response.status_code == 201, response.text

            print(f"{len(dataset['questions'])} questions…\n")
            for item in dataset["questions"]:
                started = time.monotonic()
                try:
                    response = client.post("/chat", json={"message": item["question"]})
                except Exception as failure:  # noqa: BLE001 - a harness must survive any single question
                    response = None
                    error = str(failure)
                elapsed = time.monotonic() - started

                if response is None or response.status_code != 200:
                    # Usually a local model timeout under memory pressure. Record and continue:
                    # losing the whole run to one slow question wastes ten minutes of work.
                    detail = error if response is None else response.text[:120]
                    results.append({
                        "question": item["question"],
                        "refusal": bool(item.get("refusal")),
                        "hard": bool(item.get("hard")),
                        "correct": False,
                        "sourced": False,
                        "seconds": elapsed,
                        "answer": f"[ECHEC] {detail}",
                    })
                    print(f"[ERR] {elapsed:6.1f}s  {item['question']}", flush=True)
                    print(f"        -> {detail}", flush=True)
                    continue

                body = response.json()
                answer = body["answer"]
                cited = {source["filename"] for source in body["sources"]}

                if item.get("refusal"):
                    correct = looks_like_refusal(answer)
                    sourced = True  # not applicable
                else:
                    correct = all(normalise(token) in normalise(answer) for token in item["expected"])
                    sourced = item["source"] in cited

                results.append({
                    "question": item["question"],
                    "refusal": bool(item.get("refusal")),
                    "hard": bool(item.get("hard")),
                    "correct": correct,
                    "sourced": sourced,
                    "seconds": elapsed,
                    "answer": answer,
                })
                marker = "OK " if correct else "NON"
                # flush so progress is visible when the output is redirected to a file
                print(f"[{marker}] {elapsed:6.1f}s  {item['question']}", flush=True)
                if arguments.verbose or not correct:
                    print(f"        -> {answer.strip()[:300]}", flush=True)

            factual = [row for row in results if not row["refusal"]]
            plain = [row for row in factual if not row["hard"]]
            hard = [row for row in factual if row["hard"]]
            refusals = [row for row in results if row["refusal"]]
            latencies = sorted(row["seconds"] for row in results)

            print("\n" + "=" * 62)
            print(f"Modele            : {settings.ollama_chat_model}")
            print(f"Tentatives         : {settings.max_retrieval_attempts} "
                  f"({'reformulation active' if settings.max_retrieval_attempts > 1 else 'reformulation desactivee'})")
            print("-" * 62)
            print(f"Exactitude        : {sum(r['correct'] for r in factual)}/{len(factual)}")
            print(f"  dont formulation directe   : {sum(r['correct'] for r in plain)}/{len(plain)}")
            print(f"  dont formulation eloignee  : {sum(r['correct'] for r in hard)}/{len(hard)}")
            print(f"Sources correctes : {sum(r['sourced'] for r in factual)}/{len(factual)}")
            print(f"Refus corrects    : {sum(r['correct'] for r in refusals)}/{len(refusals)}")
            print(f"Latence mediane   : {latencies[len(latencies) // 2]:.1f}s")
            print(f"Latence maximale  : {latencies[-1]:.1f}s")
            print("=" * 62)
    finally:
        with SessionLocal() as db:
            documents = db.scalars(select(DocumentRecord).where(DocumentRecord.created_by == operator_id)).all()
            for document in documents:
                (DOCUMENT_STORAGE_DIR / document.stored_filename).unlink(missing_ok=True)
                db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
                db.execute(delete(AuditEvent).where(AuditEvent.document_id == document.id))
                db.execute(delete(DocumentRecord).where(DocumentRecord.id == document.id))
            conversations = [row.id for row in db.query(Conversation).filter(Conversation.user_id == operator_id).all()]
            if conversations:
                db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversations)))
                db.execute(delete(Conversation).where(Conversation.id.in_(conversations)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id == operator_id))
            db.execute(delete(User).where(User.id == operator_id))
            db.commit()


if __name__ == "__main__":
    main()
