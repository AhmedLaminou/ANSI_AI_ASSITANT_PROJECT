r"""Mesure la qualite du RAG sur un jeu de questions a reponses connues, service par service.

Sans ce harnais, tout changement de modele, de decoupage ou de seuil releve de l'intuition.
Le script cree ses propres comptes et ses propres documents, puis les supprime.

    .\.venv\Scripts\python.exe -m tests.evaluate
    .\.venv\Scripts\python.exe -m tests.evaluate --model qwen3:0.6b
    .\.venv\Scripts\python.exe -m tests.evaluate --department rh    # un seul service, rapide

Pourquoi un jeu **par service** (phase G) : ameliorer les reponses techniques peut degrader les
reponses RH sans que rien ne le signale. Une moyenne globale masque exactement ce genre de
regression, puisqu'elle compense un service par un autre.

Chaque question porte le service qui la pose (`asked_by`). En son absence, elle est posee par le
compte administrateur, qui lit tous les perimetres : c'est le comportement historique du harnais.

Mesures produites :
  - Exactitude        : la reponse contient les elements attendus
  - Sources correctes : le document attendu figure parmi les sources citees
  - Refus corrects    : sur les questions sans reponse, l'assistant refuse au lieu d'inventer
  - Refus hors perimetre : un agent pose une question legitime dont la reponse appartient a un
                        autre service. Ce n'est pas le cas adversarial (voir security_probe.py),
                        c'est le cas ordinaire, et ce qui se mesure ici est la tenue du refus.
  - Latence           : secondes par question
"""

import argparse
import json
import time
import unicodedata
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.access import DEPARTMENTS, TRANSVERSE, department_label
from app.auth import password_hash
from app.config import get_settings
from app.database import AuditEvent, ChatMessage, Conversation, DocumentChunk, DocumentRecord, SessionLocal, User
from app.main import app
from app.rag import DOCUMENT_STORAGE_DIR


DATASET = Path(__file__).parent / "evaluation" / "dataset.json"
ADMIN = "admin"  # the key used for questions that carry no service
PASSWORD = "EvaluationPassword-2026"

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


def score(rows: list[dict], key: str) -> str:
    if not rows:
        return "  -  "
    return f"{sum(row[key] for row in rows)}/{len(rows)}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Evalue la qualite des reponses du POC ANSI.")
    parser.add_argument("--model", help="Modele de chat a tester (par defaut celui du .env)")
    parser.add_argument("--verbose", action="store_true", help="Affiche chaque reponse complete")
    parser.add_argument(
        "--attempts",
        type=int,
        help="Tentatives de recherche. 1 desactive la reformulation, 2 l'active.",
    )
    parser.add_argument(
        "--department",
        help=f"N'evalue qu'un service : {', '.join(sorted(DEPARTMENTS))}, ou {ADMIN}. "
             "Utile pour une mesure rapide apres un changement cible.",
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

    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in dataset["questions"]:
        grouped[item.get("asked_by") or ADMIN].append(item)

    if arguments.department:
        wanted = arguments.department.strip().lower()
        if wanted not in grouped:
            raise SystemExit(f"Aucune question pour « {wanted} ». Services disponibles : "
                             f"{', '.join(sorted(grouped))}.")
        grouped = {wanted: grouped[wanted]}

    run = uuid.uuid4().hex[:8]
    # The administrator uploads everything: an upload is not what is being measured, and
    # confining it to one account keeps the cleanup to a single owner.
    accounts = {ADMIN: (f"eval-admin-{run}", "admin", "technique")}
    for department in sorted(DEPARTMENTS):
        accounts[department] = (f"eval-{department}-{run}", "user", department)

    with SessionLocal() as db:
        db.add_all([
            User(username=username, password_hash=password_hash.hash(PASSWORD),
                 role=role, department=department, status="active")
            for username, role, department in accounts.values()
        ])
        db.commit()
        usernames = [username for username, _, _ in accounts.values()]
        account_ids = [row.id for row in db.scalars(select(User).where(User.username.in_(usernames))).all()]
        uploader_id = db.scalar(select(User.id).where(User.username == accounts[ADMIN][0]))

    results: list[dict] = []
    try:
        with TestClient(app) as uploader:
            assert uploader.post(
                "/auth/login", json={"username": accounts[ADMIN][0], "password": PASSWORD}
            ).status_code == 204

            print(f"Modele : {settings.ollama_chat_model}  |  Embeddings : {settings.ollama_embedding_model}")
            print(f"Indexation de {len(dataset['documents'])} documents…")
            for document in dataset["documents"]:
                response = uploader.post(
                    "/documents/upload",
                    data={
                        "title": document["title"],
                        "classification": document["classification"],
                        "allowed_roles": document["allowed_roles"],
                        "department": document.get("department", TRANSVERSE),
                    },
                    files={"file": (document["filename"], document["content"].encode("utf-8"), "text/plain")},
                )
                assert response.status_code == 201, response.text

        total = sum(len(items) for items in grouped.values())
        print(f"{total} questions, {len(grouped)} perimetre(s)…\n")

        for group in sorted(grouped):
            username = accounts[group][0]
            label = "administrateur (tous services)" if group == ADMIN else department_label(group)
            print(f"--- {label} : {len(grouped[group])} question(s) " + "-" * max(0, 30 - len(label)))

            with TestClient(app) as client:
                assert client.post(
                    "/auth/login", json={"username": username, "password": PASSWORD}
                ).status_code == 204

                for item in grouped[group]:
                    started = time.monotonic()
                    error = ""
                    try:
                        response = client.post("/chat", json={"message": item["question"]})
                    except Exception as failure:  # noqa: BLE001 - a harness must survive any single question
                        response = None
                        error = str(failure)
                    elapsed = time.monotonic() - started

                    row = {
                        "question": item["question"],
                        "group": group,
                        "refusal": bool(item.get("refusal")),
                        "out_of_perimeter": bool(item.get("out_of_perimeter")),
                        "hard": bool(item.get("hard")),
                        "seconds": elapsed,
                    }

                    if response is None or response.status_code != 200:
                        # Usually a local model timeout under memory pressure. Record and continue:
                        # losing the whole run to one slow question wastes twenty minutes of work.
                        detail = error if response is None else response.text[:120]
                        results.append({**row, "correct": False, "sourced": False,
                                        "answer": f"[ECHEC] {detail}"})
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

                    results.append({**row, "correct": correct, "sourced": sourced, "answer": answer})
                    marker = "OK " if correct else "NON"
                    # flush so progress is visible when the output is redirected to a file
                    print(f"[{marker}] {elapsed:6.1f}s  {item['question']}", flush=True)
                    if arguments.verbose or not correct:
                        print(f"        -> {answer.strip()[:300]}", flush=True)
            print()

        factual = [row for row in results if not row["refusal"]]
        plain = [row for row in factual if not row["hard"]]
        hard = [row for row in factual if row["hard"]]
        absent = [row for row in results if row["refusal"] and not row["out_of_perimeter"]]
        outside = [row for row in results if row["out_of_perimeter"]]
        latencies = sorted(row["seconds"] for row in results)

        print("=" * 68)
        print(f"Modele             : {settings.ollama_chat_model}")
        print(f"Tentatives         : {settings.max_retrieval_attempts} "
              f"({'reformulation active' if settings.max_retrieval_attempts > 1 else 'reformulation desactivee'})")
        print("-" * 68)
        print(f"Exactitude         : {score(factual, 'correct')}")
        print(f"  formulation directe  : {score(plain, 'correct')}")
        print(f"  formulation eloignee : {score(hard, 'correct')}")
        print(f"Sources correctes  : {score(factual, 'sourced')}")
        print(f"Refus corrects     : {score(absent, 'correct')}   (information absente du corpus)")
        print(f"Refus hors perimetre : {score(outside, 'correct')}   "
              f"(reponse dans un autre service)")
        print(f"Latence mediane    : {latencies[len(latencies) // 2]:.1f}s")
        print(f"Latence maximale   : {latencies[-1]:.1f}s")

        # The point of phase G: a global average hides a service that has regressed, because
        # another service compensates for it.
        print("-" * 68)
        print(f"{'Perimetre':<24} {'Exactitude':>11} {'Sources':>9} {'Refus':>7} {'Latence':>9}")
        for group in sorted({row['group'] for row in results}):
            rows = [row for row in results if row["group"] == group]
            group_factual = [row for row in rows if not row["refusal"]]
            group_refusals = [row for row in rows if row["refusal"]]
            median = sorted(row["seconds"] for row in rows)[len(rows) // 2]
            label = "administrateur" if group == ADMIN else department_label(group)
            print(f"{label:<24} {score(group_factual, 'correct'):>11} "
                  f"{score(group_factual, 'sourced'):>9} {score(group_refusals, 'correct'):>7} "
                  f"{median:>8.1f}s")
        print("=" * 68)
    finally:
        with SessionLocal() as db:
            documents = db.scalars(select(DocumentRecord).where(DocumentRecord.created_by == uploader_id)).all()
            for document in documents:
                (DOCUMENT_STORAGE_DIR / document.stored_filename).unlink(missing_ok=True)
                db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document.id))
                db.execute(delete(AuditEvent).where(AuditEvent.document_id == document.id))
                db.execute(delete(DocumentRecord).where(DocumentRecord.id == document.id))
            conversations = [
                row.id for row in
                db.scalars(select(Conversation).where(Conversation.user_id.in_(account_ids))).all()
            ]
            if conversations:
                db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversations)))
                db.execute(delete(Conversation).where(Conversation.id.in_(conversations)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(account_ids)))
            db.execute(delete(User).where(User.id.in_(account_ids)))
            db.commit()


if __name__ == "__main__":
    main()
