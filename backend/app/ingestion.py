"""Turning a file into indexed, owned, reviewable knowledge.

One function, three callers: the upload form, the batch endpoint, and the folder
importer run on the server. Keeping one path matters for the same reason the access
rule lives in one function: three copies of "how a document enters the corpus"
would drift, and the drift would show up as documents that were silently indexed
differently depending on how they arrived.

Nothing here knows about HTTP. Failures are `IngestionRejected`, carrying the status
code the web layer should use and a sentence an agent can act on; the batch endpoint
and the command-line importer turn the same exception into a report line instead.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import DOCUMENT_DEPARTMENTS, TRANSVERSE
from .config import get_settings
from .database import AuditEvent, DocumentChunk, DocumentRecord, User
from .rag import (
    DOCUMENT_STORAGE_DIR,
    SUPPORTED_EXTENSIONS,
    DocumentError,
    ModelUnavailableError,
    chunk_pages,
    embed_texts,
    extract_pages,
    ocr_available,
    parse_allowed_roles,
)


logger = logging.getLogger("ansi.assistant")

ROLES = frozenset({"admin", "document_manager", "user"})
DEFAULT_ALLOWED_ROLES = "admin,document_manager,user"


class IngestionRejected(Exception):
    """A document that cannot enter the corpus, and why, in a sentence."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class DocumentMetadata:
    title: str
    classification: str = "interne"
    allowed_roles: str = DEFAULT_ALLOWED_ROLES
    department: str = TRANSVERSE
    valid_until: datetime | None = None
    # None means "apply the default review interval"; use `no_review=True` to opt out.
    review_due: datetime | None = None
    no_review: bool = False
    owner_id: int | None = None


def as_utc(moment: datetime | None) -> datetime | None:
    """SQLite hands datetimes back without their timezone; comparing a naive value
    with an aware one raises. Every comparison goes through this."""
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def parse_date(raw: str, field_label: str) -> datetime | None:
    """Empty means "none". Otherwise YYYY-MM-DD, as the date field sends it."""
    if not raw or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip()).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise IngestionRejected(422, f"{field_label} invalide (format attendu : AAAA-MM-JJ).") from exc


def safe_roles(raw: str) -> str:
    """Rejects an unknown role outright rather than dropping it: a typo in a
    permission list should fail loudly, not silently narrow who may read."""
    requested = parse_allowed_roles(raw or "")
    if not requested or not requested <= ROLES:
        raise IngestionRejected(422, "Rôles documentaires invalides.")
    # The administrator is always allowed - the same rule the scope editor enforces.
    # Otherwise a document could enter the corpus that nobody can ever read, correct
    # or delete through the interface.
    return ",".join(sorted(requested | {"admin"}))


def safe_department(raw: str) -> str:
    value = (raw or TRANSVERSE).strip().lower()
    if value not in DOCUMENT_DEPARTMENTS:
        raise IngestionRejected(422, "Service invalide.")
    return value


def title_from_filename(name: str) -> str:
    """The title a batch or folder import gives a document: its filename, readable.

    The three-character minimum exists for the manual form, where it catches a
    careless title. Applied to filenames it would reject « cv.pdf » or « 01.txt »
    for no reason, so a short stem falls back to the full filename.
    """
    stem = Path(name).stem.replace("_", " ").replace("-", " ").strip()
    return stem if len(stem) >= 3 else Path(name).name


def default_review_due() -> datetime | None:
    months = get_settings().document_review_months
    if months <= 0:
        return None
    return datetime.now(timezone.utc) + timedelta(days=round(months * 30.44))


def review_status(document: DocumentRecord) -> str:
    """none | ok | due_soon | overdue — what the supervision screen sorts on."""
    due = as_utc(document.review_due)
    if due is None:
        return "none"
    now = datetime.now(timezone.utc)
    if due < now:
        return "overdue"
    if due < now + timedelta(days=30):
        return "due_soon"
    return "ok"


async def ingest(
    db: Session,
    actor: User,
    filename: str,
    content: bytes,
    metadata: DocumentMetadata,
    content_type: str = "application/octet-stream",
) -> tuple[DocumentRecord, int]:
    """Validates, extracts, embeds and stores one document. Returns it with its chunk count.

    Re-importing the same filename supersedes the previous version rather than leaving
    two copies that both answer questions. The owner defaults to whoever imported it:
    a document nobody is responsible for is a document nobody will review.
    """
    settings = get_settings()
    original_filename = Path(filename or "").name
    extension = Path(original_filename).suffix.lower()
    if not original_filename or extension not in SUPPORTED_EXTENSIONS:
        raise IngestionRejected(415, "Formats acceptés : PDF, DOCX, TXT et Markdown.")
    if not content:
        raise IngestionRejected(422, "Le document est vide.")
    max_bytes = settings.document_max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise IngestionRejected(413, f"Document trop volumineux (maximum {settings.document_max_upload_mb} Mo).")

    title = (metadata.title or Path(original_filename).stem).strip()
    if len(title) < 3:
        raise IngestionRejected(422, "Le titre doit contenir au moins 3 caractères.")

    owner_id = metadata.owner_id if metadata.owner_id is not None else actor.id
    if db.get(User, owner_id) is None:
        raise IngestionRejected(422, "Le responsable désigné n'existe pas.")

    try:
        pages = extract_pages(original_filename, content)
        chunks = chunk_pages(pages)
        if not chunks:
            extracted = sum(len(text.strip()) for _, text in pages)
            raise DocumentError(
                f"Aucun texte exploitable trouvé ({len(pages)} page(s) lue(s), {extracted} caractères extraits). "
                + (
                    "Si ce document est scanné, vérifiez que l'OCR reconnaît sa langue."
                    if ocr_available()
                    else "Ce document semble scanné et l'OCR local n'est pas configuré."
                )
            )
        embeddings = await embed_texts([chunk.content for chunk in chunks])
    except DocumentError as exc:
        logger.warning("Import refusé (%s) : %s", original_filename, exc)
        raise IngestionRejected(422, str(exc)) from exc
    except ModelUnavailableError as exc:
        # Not the document's fault: say so, with a status code that says so.
        logger.error("Import impossible (%s) : %s", original_filename, exc)
        raise IngestionRejected(503, str(exc)) from exc

    review_due = None if metadata.no_review else (metadata.review_due or default_review_due())

    stored_filename = f"{uuid.uuid4().hex}{extension}"
    storage_path = DOCUMENT_STORAGE_DIR / stored_filename
    storage_path.write_bytes(content)
    try:
        previous = db.scalars(
            select(DocumentRecord)
            .where(DocumentRecord.original_filename == original_filename)
            .where(DocumentRecord.is_current.is_(True))
        ).all()
        for superseded in previous:
            superseded.is_current = False
        document = DocumentRecord(
            title=title,
            original_filename=original_filename,
            stored_filename=stored_filename,
            content_type=content_type or "application/octet-stream",
            classification=(metadata.classification or "interne").strip().lower(),
            allowed_roles=safe_roles(metadata.allowed_roles),
            created_by=actor.id,
            version=max((item.version for item in previous), default=0) + 1,
            is_current=True,
            valid_until=metadata.valid_until,
            department=safe_department(metadata.department),
            owner_id=owner_id,
            review_due=review_due,
        )
        db.add(document)
        db.flush()
        db.add_all([
            DocumentChunk(
                document_id=document.id,
                ordinal=index,
                page_number=chunk.page_number,
                content=chunk.content,
                embedding=embeddings[index],
            )
            for index, chunk in enumerate(chunks)
        ])
        db.add(AuditEvent(actor_id=actor.id, document_id=document.id, event_type="document_uploaded"))
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        storage_path.unlink(missing_ok=True)
        raise
    return document, len(chunks)
