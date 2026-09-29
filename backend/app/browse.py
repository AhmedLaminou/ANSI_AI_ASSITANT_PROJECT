"""Browsing the document library: search, filters, facets, sort and pages.

`GET /documents` returns every readable document at once, which is what the chat and
the probes want and what a library of a few thousand documents cannot afford to
render. This module answers one page at a time.

The access rule is applied before anything here runs: callers pass only documents
the account may read. Facet counts are therefore counts of *readable* documents —
a count must never reveal that a document exists outside the perimeter.

Everything happens in Python over the readable set rather than in SQL, because
readability itself is decided in Python (`access.can_access_document`). That is
linear in the size of the corpus and comfortable up to several thousand documents;
beyond that, readability would have to move into the query.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .access import department_label
from .database import DocumentRecord, User
from .ingestion import as_utc, review_status

SORTS = ("recent", "oldest", "title", "department", "review")
STATUSES = ("overdue", "due_soon", "expired", "unowned", "mine")
FORMATS = ("pdf", "docx", "txt", "md")
MAX_PAGE_SIZE = 100


def fold(text: str | None) -> str:
    """Lowercase, without accents: « conges » finds « Congés »."""
    decomposed = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(character for character in decomposed if unicodedata.category(character) != "Mn")


def document_format(document: DocumentRecord) -> str:
    return Path(document.original_filename or "").suffix.lower().lstrip(".")


def is_expired(document: DocumentRecord) -> bool:
    return bool(document.valid_until and as_utc(document.valid_until) < datetime.now(timezone.utc))


def has_status(document: DocumentRecord, status: str, user: User) -> bool:
    if status in ("overdue", "due_soon"):
        return review_status(document) == status
    if status == "expired":
        return is_expired(document)
    if status == "unowned":
        return document.owner_id is None
    if status == "mine":
        return document.owner_id == user.id
    return True


@dataclass
class BrowseQuery:
    page: int = 1
    size: int = 10
    q: str = ""
    department: str = ""
    classification: str = ""
    format: str = ""
    status: str = ""
    sort: str = "recent"
    ids: set[int] | None = None  # a favourites view: restrict to these documents
    owner_names: dict[int, str] = field(default_factory=dict)


def _haystack(document: DocumentRecord, owners: dict[int, str]) -> str:
    return fold(" ".join((
        document.title or "",
        document.original_filename or "",
        department_label(document.department),
        owners.get(document.owner_id, "") if document.owner_id else "",
    )))


def _sort_key(sort: str) -> Callable[[DocumentRecord], object]:
    if sort == "title":
        return lambda document: (fold(document.title), document.id)
    if sort == "department":
        return lambda document: (fold(department_label(document.department)), fold(document.title))
    if sort == "review":
        # Documents without a review date come last: nothing is due.
        return lambda document: (
            document.review_due is None,
            as_utc(document.review_due) if document.review_due else datetime.max.replace(tzinfo=timezone.utc),
            fold(document.title),
        )
    if sort == "oldest":
        return lambda document: (as_utc(document.created_at), document.id)
    return lambda document: (as_utc(document.created_at), document.id)  # "recent", reversed below


def browse(
    readable: list[DocumentRecord],
    query: BrowseQuery,
    user: User,
    serialise: Callable[[DocumentRecord], dict[str, object]],
) -> dict[str, object]:
    """One page of `readable`, with the counts that let a filter be chosen blind.

    Facets are disjunctive: the count shown for a service ignores the service
    filter itself but applies every other one, so picking a service still shows how
    many documents the other services would give.
    """
    terms = fold(query.q).split()
    base = [
        document for document in readable
        if (query.ids is None or document.id in query.ids)
        and all(term in _haystack(document, query.owner_names) for term in terms)
    ]

    predicates: dict[str, Callable[[DocumentRecord], bool]] = {}
    if query.department:
        predicates["department"] = lambda document: document.department == query.department
    if query.classification:
        predicates["classification"] = lambda document: document.classification == query.classification
    if query.format:
        predicates["format"] = lambda document: document_format(document) == query.format
    if query.status:
        predicates["status"] = lambda document: has_status(document, query.status, user)

    def matching(skip: str | None = None) -> list[DocumentRecord]:
        active = [check for name, check in predicates.items() if name != skip]
        return [document for document in base if all(check(document) for check in active)]

    selected = matching()
    selected.sort(key=_sort_key(query.sort), reverse=query.sort == "recent")

    total = len(selected)
    pages = max(1, math.ceil(total / query.size))
    page = min(query.page, pages)  # a deletion can empty the last page: show the new last one
    window = selected[(page - 1) * query.size:page * query.size]

    def count(documents: list[DocumentRecord], key: Callable[[DocumentRecord], str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for document in documents:
            value = key(document)
            counts[value] = counts.get(value, 0) + 1
        return counts

    status_pool = matching(skip="status")
    return {
        "items": [serialise(document) for document in window],
        "total": total,
        "page": page,
        "size": query.size,
        "pages": pages,
        "first": (page - 1) * query.size + 1 if total else 0,
        "last": (page - 1) * query.size + len(window),
        "readable_total": len(readable),
        "facets": {
            "department": count(matching(skip="department"), lambda document: document.department),
            "classification": count(matching(skip="classification"), lambda document: document.classification),
            "format": count(matching(skip="format"), document_format),
            "status": {status: sum(has_status(document, status, user) for document in status_pool)
                       for status in STATUSES},
        },
    }
