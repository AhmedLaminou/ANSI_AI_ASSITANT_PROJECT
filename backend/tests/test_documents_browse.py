r"""Browsing the library: pages, search, filters, facets, sort — and the perimeter.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_documents_browse.py -q

`GET /documents/page` exists because a library of a few thousand documents cannot be
sent, or rendered, in one piece. What matters most here is not the arithmetic of
pages but that nothing leaks: a page, a search and a facet count are all computed
over what the account may read, never over the corpus.

The tests share the application's database with whatever it already holds, so every
document they create carries a run token and every query searches for it.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth import password_hash
from app.browse import fold
from app.database import AuditEvent, DocumentChunk, DocumentRecord, SessionLocal, User, initialise_database
from app.main import app

PASSWORD = "BrowseTestPassword-2026"
NOW = datetime.now(timezone.utc)
FILLERS = 23  # transverse notes, so the admin's results span several pages


@pytest.fixture(scope="module")
def library():
    """Three accounts and a small library, all removed afterwards."""
    initialise_database()
    run = f"zz{uuid.uuid4().hex[:6]}"
    people = {
        "admin": (f"brw-adm-{run}", "admin", "technique"),
        "rh": (f"brw-rh-{run}", "user", "rh"),
        "finance": (f"brw-fin-{run}", "user", "finance"),
    }
    with SessionLocal() as db:
        for username, role, department in people.values():
            db.add(User(username=username, email=f"{username}@ansi.ne", password_hash=password_hash.hash(PASSWORD),
                        role=role, department=department, status="active"))
        db.commit()
        ids = {key: db.scalar(select(User.id).where(User.username == name)) for key, (name, _, _) in people.items()}

        def add(title, department, classification="interne", extension="pdf", roles="admin,document_manager,user",
                owner=None, review_due=None, valid_until=None, current=True, version=1, age_days=0, filename=None):
            document = DocumentRecord(
                title=f"{run} {title}", original_filename=filename or f"{run}-{uuid.uuid4().hex[:6]}.{extension}",
                stored_filename=f"{uuid.uuid4().hex}.{extension}", content_type="application/octet-stream",
                classification=classification, allowed_roles=roles, created_by=ids["admin"],
                department=department, owner_id=owner, review_due=review_due, valid_until=valid_until,
                is_current=current, version=version, created_at=NOW - timedelta(days=age_days))
            db.add(document)
            db.flush()
            return document.id

        docs = {
            "conges": add("Procédure des congés", "rh", owner=ids["admin"], review_due=NOW - timedelta(days=3),
                          age_days=1),
            "fiche": add("Fiche individuelle", "rh", classification="confidentiel", extension="docx",
                         roles="admin,document_manager", age_days=2),
            "budget": add("Note budgétaire", "finance", owner=ids["admin"], review_due=NOW + timedelta(days=10),
                          age_days=3),
            "bareme": add("Barème des missions", "finance", classification="direction", extension="md",
                          valid_until=NOW - timedelta(days=30), age_days=4),
            "reglement": add("Règlement intérieur", "transverse", extension="txt", owner=ids["admin"],
                             review_due=NOW + timedelta(days=300), age_days=5),
            "charte_v1": add("Charte informatique", "transverse", current=False, age_days=40,
                             filename=f"{run}-charte.pdf"),
            "charte_v2": add("Charte informatique", "transverse", version=2, age_days=6,
                             filename=f"{run}-charte.pdf"),
        }
        for number in range(1, FILLERS + 1):
            add(f"Note de service {number:02d}", "transverse", age_days=10 + number)
        db.add_all([DocumentChunk(document_id=docs["reglement"], ordinal=index, page_number=index + 1,
                                  content=f"Article {index + 1}.", embedding=[0.0] * 768) for index in range(3)])
        db.commit()
    try:
        yield {"run": run, "ids": ids, "docs": docs, "emails": {key: f"{name}@ansi.ne" for key, (name, _, _) in people.items()}}
    finally:
        with SessionLocal() as db:
            mine = select(DocumentRecord.id).where(DocumentRecord.title.startswith(run))
            db.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(mine)))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_id.in_(ids.values())))
            db.execute(delete(DocumentRecord).where(DocumentRecord.title.startswith(run)))
            db.execute(delete(User).where(User.id.in_(ids.values())))
            db.commit()


def client_for(library, who: str) -> TestClient:
    client = TestClient(app)
    client.__enter__()
    assert client.post("/auth/login", json={"username": library["emails"][who],
                                            "password": PASSWORD}).status_code == 204
    return client


def browse(library, who: str, **params):
    client = client_for(library, who)
    try:
        response = client.get("/documents/page", params={"q": library["run"], **params})
    finally:
        client.__exit__(None, None, None)
    assert response.status_code == 200, response.text
    return response.json()


def titles(page) -> list[str]:
    return [item["title"].split(" ", 1)[1] for item in page["items"]]


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------

def test_pages_split_the_results(library):
    total = 6 + FILLERS  # current documents only: the old charter is excluded by default
    first = browse(library, "admin", size=10)
    assert first["total"] == total and first["pages"] == 3
    assert len(first["items"]) == 10 and (first["first"], first["last"]) == (1, 10)
    second = browse(library, "admin", size=10, page=2)
    assert (second["first"], second["last"]) == (11, 20)
    assert not set(titles(first)) & set(titles(second)), "no document appears on two pages"


def test_a_page_past_the_end_shows_the_last_one(library):
    # A deletion can empty the last page: the interface must not land on nothing.
    page = browse(library, "admin", size=10, page=99)
    assert page["page"] == page["pages"] == 3 and page["items"]


def test_the_newest_documents_come_first_by_default(library):
    assert titles(browse(library, "admin", size=3)) == [
        "Procédure des congés", "Fiche individuelle", "Note budgétaire"]


# --------------------------------------------------------------------------
# The perimeter
# --------------------------------------------------------------------------

def test_a_reader_never_sees_nor_counts_what_they_cannot_read(library):
    page = browse(library, "rh", size=100)
    listed = set(titles(page))
    assert "Note budgétaire" not in listed and "Barème des missions" not in listed, "another service's documents"
    assert "Fiche individuelle" not in listed, "a document whose roles exclude the reader"
    assert "finance" not in page["facets"]["department"], "a count would reveal the finance corpus exists"
    assert page["facets"]["classification"].get("confidentiel") is None
    assert page["total"] == 1 + 2 + FILLERS  # congés, règlement, charte v2 and the notes


def test_favourites_are_restricted_to_what_is_readable(library):
    docs = library["docs"]
    page = browse(library, "rh", ids=f"{docs['conges']},{docs['budget']}")
    assert titles(page) == ["Procédure des congés"], "a favourite id cannot open another perimeter"


# --------------------------------------------------------------------------
# Search, filters, facets
# --------------------------------------------------------------------------

def test_search_ignores_accents_and_case(library):
    page = browse(library, "admin", q=f"{library['run']} PROCEDURE conges")
    assert titles(page) == ["Procédure des congés"]


def test_search_finds_a_document_by_its_owner(library):
    owner = library["emails"]["admin"].split("@")[0]
    page = browse(library, "admin", q=f"{library['run']} {owner}", size=100)
    assert set(titles(page)) == {"Procédure des congés", "Note budgétaire", "Règlement intérieur"}


def test_filters_combine(library):
    page = browse(library, "admin", department="rh", classification="interne")
    assert titles(page) == ["Procédure des congés"]
    assert titles(browse(library, "admin", format="md")) == ["Barème des missions"]


def test_a_facet_ignores_its_own_filter(library):
    """Choosing a service must still show what the others hold, or nobody could switch."""
    page = browse(library, "admin", department="rh")
    assert page["total"] == 2
    assert page["facets"]["department"] == {"rh": 2, "finance": 2, "transverse": 2 + FILLERS}
    assert page["facets"]["classification"] == {"interne": 1, "confidentiel": 1}


@pytest.mark.parametrize("status, expected", [
    ("overdue", {"Procédure des congés"}),
    ("due_soon", {"Note budgétaire"}),
    ("expired", {"Barème des missions"}),
    ("mine", {"Procédure des congés", "Note budgétaire", "Règlement intérieur"}),
])
def test_status_filters(library, status, expected):
    assert set(titles(browse(library, "admin", status=status, size=100))) == expected


def test_status_counts_follow_the_other_filters(library):
    counts = browse(library, "admin", department="finance")["facets"]["status"]
    assert counts["due_soon"] == 1 and counts["expired"] == 1 and counts["overdue"] == 0
    assert counts["unowned"] == 1  # the barème


# --------------------------------------------------------------------------
# Sort, versions
# --------------------------------------------------------------------------

def test_sort_by_title_ignores_accents(library):
    listed = titles(browse(library, "admin", sort="title", size=100))
    assert listed == sorted(listed, key=fold)
    assert listed.index("Barème des missions") < listed.index("Charte informatique")


def test_sort_by_review_puts_undated_documents_last(library):
    listed = titles(browse(library, "admin", sort="review", size=100))
    assert listed[:3] == ["Procédure des congés", "Note budgétaire", "Règlement intérieur"]


def test_replaced_versions_appear_only_on_request(library):
    assert titles(browse(library, "admin", q=f"{library['run']} charte")) == ["Charte informatique"]
    both = browse(library, "admin", q=f"{library['run']} charte", superseded="true")
    assert len(both["items"]) == 2
    assert sorted(item["is_current"] for item in both["items"]) == [False, True]


# --------------------------------------------------------------------------
# Refusals, preview, audit
# --------------------------------------------------------------------------

@pytest.mark.parametrize("params, fragment", [
    ({"sort": "au-hasard"}, "Tri inconnu"),
    ({"status": "perdu"}, "Statut inconnu"),
    ({"format": "exe"}, "Format inconnu"),
    ({"size": 500}, "ne peut pas dépasser 100"),
    ({"page": 0}, "doit valoir au moins 1"),
    ({"ids": "1,deux"}, "numéros"),
])
def test_unknown_values_are_refused_in_a_sentence(library, params, fragment):
    client = client_for(library, "admin")
    try:
        response = client.get("/documents/page", params=params)
    finally:
        client.__exit__(None, None, None)
    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_the_preview_names_the_owner_and_counts_the_passages(library):
    client = client_for(library, "admin")
    try:
        preview = client.get(f"/documents/{library['docs']['reglement']}/preview").json()
    finally:
        client.__exit__(None, None, None)
    assert preview["document"]["owner"] == library["emails"]["admin"].split("@")[0]
    assert preview["chunks_total"] == 3 and len(preview["chunks"]) == 3


def test_the_audit_journal_pages_with_an_offset(library):
    client = client_for(library, "admin")  # signing in writes nothing, so seed three events
    try:
        with SessionLocal() as db:
            db.add_all([AuditEvent(actor_id=library["ids"]["admin"], document_id=None, event_type="browse_test")
                        for _ in range(3)])
            db.commit()
        first = client.get("/admin/audit", params={"limit": 2, "event_type": "browse_test"}).json()
        second = client.get("/admin/audit", params={"limit": 2, "offset": 2, "event_type": "browse_test"}).json()
    finally:
        client.__exit__(None, None, None)
    assert first["matching"] >= 3 and first["matching"] <= first["total"]
    assert len(first["events"]) == 2 and second["offset"] == 2
    assert not {event["id"] for event in first["events"]} & {event["id"] for event in second["events"]}
