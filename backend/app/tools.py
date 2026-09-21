"""Business tools: questions answered from the database, not from documents.

« Combien d'utilisateurs sont enregistrés ? » is a query, not a semantic search
(§9 du document de conception). Sending it through the RAG pipeline is both slow
and wrong: the answer is not in any document.

Design constraints, following §18 and §20:

- **The model never decides to call a tool.** A deterministic pattern match does.
  An assistant that lets a language model choose when to touch the database is
  exactly what §18 warns against; an explicit allowlist is auditable.
- **Each tool declares the roles allowed to run it.** `count_users` is admin-only.
- **Each tool sees only what its caller may see.** Document counts are computed
  from the caller's own authorised set, never the whole corpus.
- **Each invocation is journalised** by the caller (`tool_invoked`).
- **No free-form SQL.** Every tool is a fixed function with no parameters taken
  from the question, so a crafted question cannot alter the query.
"""

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import DocumentChunk, DocumentRecord, User
from .access import can_access_document


def normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in without_accents)
    return " ".join(cleaned.split())


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    patterns: tuple[str, ...]
    roles: frozenset[str]
    run: Callable[[Session, User], str]


def visible_documents(db: Session, user: User) -> list[DocumentRecord]:
    """Uses the shared access rule: role *and* department, never a local copy."""
    return [
        document
        for document in db.scalars(select(DocumentRecord).where(DocumentRecord.is_current.is_(True))).all()
        if can_access_document(user, document)
    ]


def count_documents(db: Session, user: User) -> str:
    documents = visible_documents(db, user)
    if not documents:
        return "Aucun document n'est actuellement accessible à votre compte."
    by_classification: dict[str, int] = {}
    for document in documents:
        by_classification[document.classification] = by_classification.get(document.classification, 0) + 1
    detail = ", ".join(f"{count} en « {name} »" for name, count in sorted(by_classification.items()))
    return f"Votre compte a accès à {len(documents)} document(s) : {detail}."


def list_documents(db: Session, user: User) -> str:
    documents = visible_documents(db, user)
    if not documents:
        return "Aucun document n'est actuellement accessible à votre compte."
    lines = [
        f"- {document.title} ({document.classification}, version {document.version})"
        for document in sorted(documents, key=lambda item: item.title.lower())
    ]
    return "Documents accessibles à votre compte :\n" + "\n".join(lines)


def count_users(db: Session, user: User) -> str:
    accounts = db.scalars(select(User)).all()
    active = [account for account in accounts if account.is_active]
    by_role: dict[str, int] = {}
    for account in active:
        by_role[account.role] = by_role.get(account.role, 0) + 1
    detail = ", ".join(f"{count} {name}" for name, count in sorted(by_role.items()))
    inactive = len(accounts) - len(active)
    answer = f"{len(active)} compte(s) actif(s) : {detail}."
    if inactive:
        answer += f" {inactive} compte(s) désactivé(s)."
    return answer


def corpus_statistics(db: Session, user: User) -> str:
    documents = visible_documents(db, user)
    if not documents:
        return "Aucun document n'est actuellement accessible à votre compte."
    identifiers = [document.id for document in documents]
    total_chunks = len(db.scalars(select(DocumentChunk.id).where(DocumentChunk.document_id.in_(identifiers))).all())
    latest = max(document.created_at for document in documents)
    return (
        f"{len(documents)} document(s) accessible(s), découpés en {total_chunks} extrait(s) indexés. "
        f"Import le plus récent : {latest.strftime('%d/%m/%Y')}."
    )


EVERYONE = frozenset({"admin", "document_manager", "user"})

TOOLS: tuple[Tool, ...] = (
    Tool(
        name="count_users",
        description="Nombre de comptes enregistrés, par rôle",
        patterns=(
            "combien d utilisateurs",
            "combien de utilisateurs",
            "combien d usagers",
            "combien de comptes",
            "nombre d utilisateurs",
            "nombre de comptes",
            "combien d agents sont enregistres",
        ),
        roles=frozenset({"admin"}),
        run=count_users,
    ),
    Tool(
        name="count_documents",
        description="Nombre de documents accessibles au compte",
        patterns=(
            "combien de documents",
            "nombre de documents",
            "combien de fichiers",
            "combien y a t il de documents",
        ),
        roles=EVERYONE,
        run=count_documents,
    ),
    Tool(
        name="list_documents",
        description="Liste des documents accessibles au compte",
        patterns=(
            "quels documents",
            "quels sont les documents",
            "liste des documents",
            "liste les documents",
            "quels fichiers",
            "a quels documents",
            "a quoi ai je acces",
            "a quoi j ai acces",
            "sur lesquels j ai acces",
            "auxquels j ai acces",
            "auquel j ai acces",
            "ce a quoi j ai acces",
            "que puis je consulter",
            "que puis je lire",
            "qu est ce que je peux consulter",
            "qu est ce que je peux lire",
            "mes documents",
            "documents accessibles",
            "documents auxquels",
        ),
        roles=EVERYONE,
        run=list_documents,
    ),
    Tool(
        name="corpus_statistics",
        description="Volume indexé et date du dernier import",
        patterns=(
            "statistiques",
            "etat du corpus",
            "combien d extraits",
            "taille du corpus",
        ),
        roles=EVERYONE,
        run=corpus_statistics,
    ),
)


def find_tool(question: str) -> Tool | None:
    """Deterministic match. Returns the tool a question asks for, or None."""
    text = normalise(question)
    for tool in TOOLS:
        if any(pattern in text for pattern in tool.patterns):
            return tool
    return None
