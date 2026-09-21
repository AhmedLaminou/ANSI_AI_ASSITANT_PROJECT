import json
import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .auth import create_access_token, get_current_user, password_hash, require_roles, verify_password
from .access import (
    DEPARTMENT_LABELS,
    DEPARTMENTS,
    DOCUMENT_DEPARTMENTS,
    TRANSVERSE,
    can_access_document,
    department_label,
)
from .config import get_settings
from .glossary import expand_for
from .graph import build_assistant_graph
from .prompts import assistant_description, system_message
from .tools import find_tool
from .database import (
    AnswerFeedback,
    AuditEvent,
    ChatMessage,
    Conversation,
    DocumentChunk,
    DocumentRecord,
    SessionLocal,
    User,
    engine,
    get_db,
    initialise_database,
)
from .rag import (
    DOCUMENT_STORAGE_DIR,
    SUPPORTED_EXTENSIONS,
    DocumentError,
    ModelUnavailableError,
    RagError,
    chunk_pages,
    embed_texts,
    extract_pages,
    ocr_available,
    parse_allowed_roles,
    search_similar_chunks,
)


logger = logging.getLogger("ansi.assistant")

ROLES = {"admin", "document_manager", "user"}
DEFAULT_ALLOWED_ROLES = "admin,document_manager,user"

TOP_K = 5
RELEVANCE_THRESHOLD = 0.18
HISTORY_WINDOW = 6
REASONING_MARKER = "</think>"
MODEL_UNAVAILABLE = "Le modèle conversationnel local est indisponible"
NO_DOCUMENTS_ANSWER = "Aucun document autorisé n’est encore disponible pour votre compte."


def document_titles(documents: list[DocumentRecord], limit: int = 5) -> str:
    titles = [f"« {document.title} »" for document in documents[:limit]]
    remaining = len(documents) - len(titles)
    listed = ", ".join(titles)
    return f"{listed} et {remaining} autre(s)" if remaining > 0 else listed


def social_answer(user: User, documents: list[DocumentRecord]) -> str:
    """A greeting deserves an answer, not a failed document search."""
    return (
        f"Bonjour. Je suis {assistant_description(user)} : je réponds à partir des "
        "documents auxquels votre compte a accès, en citant le document et la page utilisés.\n\n"
        f"Vous pouvez m’interroger sur : {document_titles(documents)}.\n\n"
        "Posez-moi une question portant sur leur contenu."
    )


def no_match_answer(documents: list[DocumentRecord]) -> str:
    """A refusal is more useful when it says what *can* be answered."""
    return (
        "Je ne trouve pas d’information suffisamment pertinente dans les documents auxquels vous "
        "avez accès. Je ne réponds qu’à partir de ces documents et je n’invente pas de réponse.\n\n"
        f"Documents actuellement interrogeables : {document_titles(documents)}."
    )


@dataclass
class ChatContext:
    """Either a canned refusal, or the body to send to Ollama."""

    refusal: str | None
    sources: list[dict[str, object]] = field(default_factory=list)
    request_body: dict[str, object] | None = None
    rewritten: bool = False  # the graph had to reformulate the question to find sources


_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _within_limit(key: str, limit: int, window_seconds: int) -> bool:
    """Sliding window counter. In-process only — see ARCHITECTURE_TECHNIQUE §7."""
    now = time.monotonic()
    hits = _rate_buckets[key]
    while hits and now - hits[0] > window_seconds:
        hits.popleft()
    if len(hits) >= limit:
        return False
    hits.append(now)
    return True


def enforce_chat_rate_limit(user_id: int) -> None:
    """Ollama answers sequentially, so a burst only builds a queue."""
    limit = get_settings().chat_rate_limit_per_minute
    if limit <= 0:
        return
    if not _within_limit(f"chat:{user_id}", limit, 60):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Trop de questions en peu de temps (maximum {limit} par minute). Réessayez dans un instant.",
        )


def enforce_login_rate_limit(username: str, client_ip: str) -> None:
    """Throttles credential stuffing on one account and spraying from one address.

    Only failed attempts are counted (see record_failed_login), so a legitimate user
    is never locked out by their own successful sign-ins.
    """
    settings_ = get_settings()
    limit = settings_.login_rate_limit_per_minute
    window = settings_.login_rate_limit_window_seconds
    if limit <= 0:
        return
    now = time.monotonic()
    for key in (f"login-fail:user:{username.lower()}", f"login-fail:ip:{client_ip}"):
        hits = _rate_buckets[key]
        while hits and now - hits[0] > window:
            hits.popleft()
        if len(hits) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Trop de tentatives de connexion échouées. Réessayez dans quelques minutes.",
            )


def record_failed_login(username: str, client_ip: str) -> None:
    window = get_settings().login_rate_limit_window_seconds
    now = time.monotonic()
    for key in (f"login-fail:user:{username.lower()}", f"login-fail:ip:{client_ip}"):
        _rate_buckets[key].append(now)
        while _rate_buckets[key] and now - _rate_buckets[key][0] > window:
            _rate_buckets[key].popleft()


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: int | None = None


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)
    role: str = Field(default="user")
    department: str | None = None


class UpdateUserRequest(BaseModel):
    role: str | None = None
    is_active: bool | None = None
    department: str | None = None


class ResetPasswordRequest(BaseModel):
    password: str = Field(min_length=12, max_length=128)


class RegistrationRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=128)
    requested_department: str
    reason: str = Field(default="", max_length=500)


class ApproveRegistrationRequest(BaseModel):
    role: str = Field(default="user")
    department: str


class RefuseRegistrationRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=8, ge=1, le=25)


class FeedbackRequest(BaseModel):
    message_id: int
    verdict: str = Field(pattern="^(useful|wrong)$")
    comment: str = Field(default="", max_length=1000)


def bootstrap_admin() -> None:
    """Optional first-run path; normal account provisioning uses /admin/users."""
    settings = get_settings()
    if not settings.bootstrap_admin_username or not settings.bootstrap_admin_password:
        return
    with Session(engine) as db:
        existing = db.scalar(select(User).where(User.username == settings.bootstrap_admin_username))
        if not existing:
            db.add(User(
                username=settings.bootstrap_admin_username,
                password_hash=password_hash.hash(settings.bootstrap_admin_password),
                role="admin",
            ))
            db.commit()


def purge_expired_conversations() -> int:
    """Retention is disabled by default: the duration is a governance decision, not a default."""
    days = get_settings().conversation_retention_days
    if days <= 0:
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with Session(engine) as db:
        expired = db.scalars(select(Conversation).where(Conversation.updated_at < cutoff)).all()
        if not expired:
            return 0
        identifiers = [conversation.id for conversation in expired]
        db.execute(delete(ChatMessage).where(ChatMessage.conversation_id.in_(identifiers)))
        db.execute(delete(Conversation).where(Conversation.id.in_(identifiers)))
        db.commit()
        return len(identifiers)


@asynccontextmanager
async def lifespan(_: FastAPI):
    DOCUMENT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    initialise_database()
    bootstrap_admin()
    purge_expired_conversations()
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)

LOCALHOST_ORIGIN_PATTERN = r"http://(localhost|127\.0\.0\.1)(:\d+)?"


def cors_policy(app_env: str, frontend_origin: str) -> dict[str, object]:
    """Production allows exactly one origin. Development allows any local port.

    Vite falls back to 5174, 5175… when a port is taken, and http://127.0.0.1 is a
    different origin from http://localhost. Without this, the preflight is rejected
    and the browser only reports an opaque "Failed to fetch".
    """
    if app_env == "production":
        return {"allow_origins": [frontend_origin]}
    return {"allow_origin_regex": LOCALHOST_ORIGIN_PATTERN}


app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
    **cors_policy(settings.app_env, settings.frontend_origin),
)


# Field names as an agent would name them, not as the schema spells them.
FIELD_LABELS: dict[str, str] = {
    "username": "L'identifiant",
    "password": "Le mot de passe",
    "requested_department": "Le service demandé",
    "department": "Le service",
    "role": "Le rôle",
    "reason": "Le motif",
    "title": "Le titre",
    "message": "La question",
    "query": "La recherche",
    "verdict": "L'avis",
    "comment": "Le commentaire",
    "classification": "La classification",
    "allowed_roles": "Les rôles autorisés",
}


def explain_validation_error(error: dict[str, object]) -> str:
    """One Pydantic error, in a sentence an agent can act on."""
    location = [part for part in error.get("loc", ()) if part != "body"]
    field = FIELD_LABELS.get(str(location[-1]) if location else "", "Un champ obligatoire")
    kind = error.get("type", "")
    context = error.get("ctx") or {}

    if kind == "missing":
        return f"{field} est obligatoire."
    if kind == "string_too_short":
        return f"{field} doit contenir au moins {context.get('min_length', '?')} caractères."
    if kind == "string_too_long":
        return f"{field} ne doit pas dépasser {context.get('max_length', '?')} caractères."
    if kind == "string_pattern_mismatch":
        if location and location[-1] == "username":
            # The commonest failure by far: a space or an accent in the username.
            return ("L'identifiant ne peut contenir que des lettres non accentuées, des chiffres, "
                    "et les signes « . » « - » « _ » — ni espace, ni accent.")
        return f"{field} contient des caractères non autorisés."
    return f"{field} est invalide."


@app.exception_handler(RequestValidationError)
async def readable_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Turns Pydantic's error list into a single readable sentence.

    Without this, `detail` is a list of objects. The interface displays
    `detail` directly, so an agent whose password was too short saw either nothing
    or « [object Object] » — and kept resubmitting the same form. Observed in
    production of the POC: seven consecutive 422 on /auth/register.

    Making `detail` always a string is also a contract worth having: every caller
    can render an error the same way.
    """
    messages: list[str] = []
    for error in exc.errors():
        sentence = explain_validation_error(error)
        if sentence not in messages:
            messages.append(sentence)
    return JSONResponse(status_code=422, content={"detail": " ".join(messages)})


def document_summary(document: DocumentRecord) -> dict[str, object]:
    return {
        "id": document.id,
        "title": document.title,
        "filename": document.original_filename,
        "classification": document.classification,
        "allowed_roles": sorted(parse_allowed_roles(document.allowed_roles)),
        "created_at": document.created_at.isoformat(),
        "version": document.version,
        "is_current": document.is_current,
        "department": document.department,
        "department_label": department_label(document.department),
        "valid_until": document.valid_until.isoformat() if document.valid_until else None,
        "is_expired": is_expired(document),
    }


def is_expired(document: DocumentRecord) -> bool:
    return bool(document.valid_until and document.valid_until < datetime.now(timezone.utc))


def parse_valid_until(raw: str) -> datetime | None:
    """Accepts an empty value (no expiry) or YYYY-MM-DD from the date field."""
    if not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip()).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Date de validité invalide (format attendu : AAAA-MM-JJ)") from exc


def account_summary(account: User) -> dict[str, object]:
    return {
        "id": account.id,
        "username": account.username,
        "role": account.role,
        "is_active": account.is_active,
        "department": account.department,
        "department_label": department_label(account.department),
        "status": account.status,
    }


def count_active_admins(db: Session) -> int:
    return len(db.scalars(select(User).where(User.role == "admin").where(User.is_active.is_(True))).all())


def conversation_summary(conversation: Conversation) -> dict[str, object]:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "updated_at": conversation.updated_at.isoformat(),
    }


def get_owned_conversation(db: Session, conversation_id: int, user: User) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    return conversation


def safe_roles(raw_roles: str) -> str:
    requested = {role.strip() for role in raw_roles.split(",") if role.strip()}
    if not requested or not requested.issubset(ROLES):
        raise HTTPException(status_code=422, detail="Rôles documentaires invalides")
    return ",".join(sorted(requested))


def safe_department(raw: str) -> str:
    """A department is chosen from a closed set, never free text from the client."""
    candidate = (raw or TRANSVERSE).strip().lower()
    if candidate not in DOCUMENT_DEPARTMENTS:
        raise HTTPException(
            status_code=422,
            detail=f"Département invalide. Valeurs acceptées : {', '.join(sorted(DOCUMENT_DEPARTMENTS))}.",
        )
    return candidate


def model_is_available(model: str, installed_models: list[str]) -> bool:
    """Ollama lists an untagged model as name:latest, while its API accepts name."""
    return model in installed_models or f"{model}:latest" in installed_models


def extract_answer(message: dict[str, object]) -> str:
    """qwen3 still emits its reasoning inline despite think=false; keep only what follows it."""
    content = str(message.get("content", ""))
    if "</think>" in content:
        content = content.rsplit("</think>", 1)[-1]
    return content.strip()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.app_env}


@app.get("/system/status")
async def system_status(user: User = Depends(get_current_user)) -> dict[str, object]:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(f"{settings.ollama_base_url}/api/tags")
            response.raise_for_status()
            models = [model["name"] for model in response.json().get("models", [])]
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Ollama local est indisponible") from exc
    return {
        "user": user.username,
        "ollama_available": True,
        "chat_model": settings.ollama_chat_model,
        "embedding_model": settings.ollama_embedding_model,
        "chat_model_ready": model_is_available(settings.ollama_chat_model, models),
        "embedding_model_ready": model_is_available(settings.ollama_embedding_model, models),
    }


@app.post("/auth/login", status_code=status.HTTP_204_NO_CONTENT)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    client_ip = request.client.host if request.client else "unknown"
    enforce_login_rate_limit(payload.username, client_ip)
    user = db.scalar(select(User).where(User.username == payload.username))
    if user and user.status == "pending" and verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Votre demande d’accès est en attente de validation par l’administrateur.",
        )
    if user and user.status == "refused" and verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Votre demande d’accès a été refusée.")
    if not user or not user.is_active or user.status != "active" or not verify_password(
        payload.password, user.password_hash
    ):
        record_failed_login(payload.username, client_ip)
        if user:
            db.add(AuditEvent(actor_id=user.id, document_id=None, event_type="login_failed"))
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Identifiants invalides")
    response.set_cookie(
        key="ansi_session",
        value=create_access_token(user),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=8 * 60 * 60,
        path="/",
    )


@app.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie("ansi_session", path="/")


@app.get("/auth/me")
def current_user(user: User = Depends(get_current_user)) -> dict[str, object]:
    # id lets the interface disable actions an admin must not apply to their own account
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "department": user.department,
        "department_label": department_label(user.department),
        "sees_every_department": user.role == "admin",
    }


@app.get("/documents")
def list_documents(
    include_superseded: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    query = select(DocumentRecord).order_by(DocumentRecord.created_at.desc())
    if not include_superseded:
        query = query.where(DocumentRecord.is_current.is_(True))
    documents = db.scalars(query).all()
    return [document_summary(document) for document in documents if can_access_document(user, document)]


@app.get("/documents/{document_id}/preview")
def preview_document(
    document_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    document = db.get(DocumentRecord, document_id)
    if not document or not can_access_document(user, document):
        raise HTTPException(status_code=404, detail="Document introuvable")
    chunks = db.scalars(
        select(DocumentChunk).where(DocumentChunk.document_id == document.id).order_by(DocumentChunk.ordinal).limit(8)
    ).all()
    return {
        "document": document_summary(document),
        "chunks": [{"page": chunk.page_number, "content": chunk.content} for chunk in chunks],
    }


@app.post("/documents/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    title: str = Form(..., min_length=3, max_length=255),
    classification: str = Form("interne", min_length=2, max_length=64),
    allowed_roles: str = Form(DEFAULT_ALLOWED_ROLES),
    valid_until: str = Form(""),
    department: str = Form(TRANSVERSE),
    user: User = Depends(require_roles("admin", "document_manager")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    original_filename = Path(file.filename or "").name
    extension = Path(original_filename).suffix.lower()
    if not original_filename or extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Formats acceptés : PDF, DOCX, TXT et Markdown")

    content = await file.read()
    max_bytes = settings.document_max_upload_mb * 1024 * 1024
    if not content:
        raise HTTPException(status_code=422, detail="Le document est vide")
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Document trop volumineux (maximum {settings.document_max_upload_mb} Mo)")

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
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ModelUnavailableError as exc:
        # Not the document's fault: say so, and with a status code that says so.
        logger.error("Import impossible (%s) : %s", original_filename, exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    stored_filename = f"{uuid.uuid4().hex}{extension}"
    storage_path = DOCUMENT_STORAGE_DIR / stored_filename
    storage_path.write_bytes(content)
    try:
        # Re-importing the same filename supersedes the previous version rather than
        # leaving two copies that both answer questions.
        previous = db.scalars(
            select(DocumentRecord)
            .where(DocumentRecord.original_filename == original_filename)
            .where(DocumentRecord.is_current.is_(True))
        ).all()
        for superseded in previous:
            superseded.is_current = False
        document = DocumentRecord(
            title=title.strip(),
            original_filename=original_filename,
            stored_filename=stored_filename,
            content_type=file.content_type or "application/octet-stream",
            classification=classification.strip().lower(),
            allowed_roles=safe_roles(allowed_roles),
            created_by=user.id,
            version=max((item.version for item in previous), default=0) + 1,
            is_current=True,
            valid_until=parse_valid_until(valid_until),
            department=safe_department(department),
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
        db.add(AuditEvent(actor_id=user.id, document_id=document.id, event_type="document_uploaded"))
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        storage_path.unlink(missing_ok=True)
        raise

    return {**document_summary(document), "chunks_indexed": len(chunks)}


@app.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> None:
    document = db.get(DocumentRecord, document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document introuvable")
    storage_path = DOCUMENT_STORAGE_DIR / document.stored_filename
    db.add(AuditEvent(actor_id=user.id, document_id=document.id, event_type="document_deleted"))
    db.delete(document)
    db.commit()
    storage_path.unlink(missing_ok=True)


@app.get("/conversations")
def list_conversations(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, object]]:
    conversations = db.scalars(
        select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc())
    ).all()
    return [conversation_summary(conversation) for conversation in conversations]


@app.post("/conversations", status_code=status.HTTP_201_CREATED)
def create_conversation(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, object]:
    conversation = Conversation(user_id=user.id, title="Nouvelle conversation")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation_summary(conversation)


@app.get("/conversations/{conversation_id}/messages")
def get_messages(
    conversation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, object]:
    conversation = get_owned_conversation(db, conversation_id, user)
    messages = db.scalars(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation.id).order_by(ChatMessage.id)
    ).all()
    return {
        "conversation": conversation_summary(conversation),
        "messages": [
            {"id": message.id, "role": message.role, "content": message.content, "sources": json.loads(message.sources)}
            for message in messages
        ],
    }


@app.patch("/conversations/{conversation_id}")
def rename_conversation(
    conversation_id: int,
    payload: RenameConversationRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    conversation = get_owned_conversation(db, conversation_id, user)
    conversation.title = payload.title.strip()
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conversation)
    return conversation_summary(conversation)


@app.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> None:
    conversation = get_owned_conversation(db, conversation_id, user)
    db.execute(delete(ChatMessage).where(ChatMessage.conversation_id == conversation.id))
    db.delete(conversation)
    db.commit()


def save_assistant_exchange(
    db: Session,
    conversation: Conversation,
    question: str,
    answer: str,
    sources: list[dict[str, object]],
    user_id: int,
) -> dict[str, object]:
    if conversation.title == "Nouvelle conversation":
        conversation.title = question[:157] + ("…" if len(question) > 157 else "")
    conversation.updated_at = datetime.now(timezone.utc)
    reply = ChatMessage(
        conversation_id=conversation.id, role="assistant", content=answer, sources=json.dumps(sources)
    )
    db.add_all([
        ChatMessage(conversation_id=conversation.id, role="user", content=question),
        reply,
        AuditEvent(actor_id=user_id, document_id=None, event_type="document_question_answered"),
    ])
    db.commit()
    db.refresh(conversation)
    db.refresh(reply)
    # The identifier lets the interface attach feedback to this precise answer.
    return {
        "answer": answer,
        "sources": sources,
        "conversation": conversation_summary(conversation),
        "message_id": reply.id,
    }


def resolve_conversation(db: Session, conversation_id: int | None, user: User) -> Conversation:
    """A new conversation is committed immediately so the streaming generator can reload it."""
    if conversation_id is not None:
        return get_owned_conversation(db, conversation_id, user)
    conversation = Conversation(user_id=user.id, title="Nouvelle conversation")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation


async def build_chat_context(
    db: Session, conversation: Conversation, question: str, user: User, stream: bool
) -> ChatContext:
    visible_documents = {
        document.id: document
        for document in db.scalars(select(DocumentRecord).where(DocumentRecord.is_current.is_(True))).all()
        if can_access_document(user, document)
    }
    if not visible_documents:
        return ChatContext(refusal=NO_DOCUMENTS_ANSWER, sources=[], request_body=None)

    async def retrieve(search_question: str):
        """ACL is applied here, inside the injected retriever: the graph never sees
        a document the user is not allowed to read, even after a rewrite."""
        try:
            # Acronyms are expanded on the question only: the index stays untouched,
            # so the glossary can grow without re-indexing anything.
            embedding = (await embed_texts([expand_for(user, search_question)]))[0]
        except RagError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return search_similar_chunks(db, embedding, list(visible_documents), TOP_K)

    def run_tool(tool_question: str) -> str | None:
        """Executes a business tool as the caller. Returns None to fall back to documents."""
        tool = find_tool(tool_question)
        if tool is None:
            return None
        if user.role not in tool.roles:
            logger.info("Outil %s refusé pour le rôle %s", tool.name, user.role)
            return (
                f"Cette information ({tool.description.lower()}) est réservée aux administrateurs. "
                "Votre compte n'y a pas accès."
            )
        answer = tool.run(db, user)
        db.add(AuditEvent(actor_id=user.id, document_id=None, event_type=f"tool_invoked:{tool.name}"))
        db.commit()
        return answer

    assistant_graph = build_assistant_graph(
        retrieve, RELEVANCE_THRESHOLD, settings.max_retrieval_attempts, run_tool=run_tool
    )
    final_state = await assistant_graph.ainvoke({"question": question, "search_question": question, "attempts": 0})
    outcome = final_state.get("outcome")

    if outcome == "tool":
        return ChatContext(refusal=final_state["tool_answer"], sources=[], request_body=None)
    if outcome == "social":
        return ChatContext(refusal=social_answer(user, list(visible_documents.values())), sources=[], request_body=None)
    if outcome != "answer":
        return ChatContext(refusal=no_match_answer(list(visible_documents.values())), sources=[], request_body=None)

    selected_chunks = final_state["chunks"]
    rewritten = bool(final_state.get("rewritten"))

    source_blocks = []
    source_metadata = []
    for index, (_, chunk) in enumerate(selected_chunks, start=1):
        document = visible_documents[chunk.document_id]
        source_id = f"S{index}"
        expired = is_expired(document)
        # The model is told an extract is out of date so it can say so; the interface
        # marks it too. Silence here would mean confidently citing a lapsed procedure.
        validity = " — DOCUMENT PÉRIMÉ" if expired else ""
        source_blocks.append(
            f"[{source_id}] Document : {document.title} — page {chunk.page_number}{validity}\n{chunk.content}"
        )
        source_metadata.append({
            "id": source_id,
            "title": document.title,
            "filename": document.original_filename,
            "page": chunk.page_number,
            "is_expired": expired,
        })

    prior_messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.id.desc())
        .limit(HISTORY_WINDOW)
    ).all()
    recent_history = [
        {"role": message.role, "content": message.content}
        for message in reversed(prior_messages)
        if message.role in {"user", "assistant"}
    ]
    request_body = {
        "model": settings.ollama_chat_model,
        "stream": stream,
        "think": False,
        "messages": [{"role": "system", "content": system_message(user)}, *recent_history, {
            "role": "user",
            "content": f"Question : {question}\n\nExtraits autorisés :\n\n" + "\n\n".join(source_blocks),
        }],
        "options": {"num_ctx": 4096, "temperature": 0.15},
        "keep_alive": "10m",
    }
    return ChatContext(refusal=None, sources=source_metadata, request_body=request_body, rewritten=rewritten)


@app.post("/search")
async def search_documents(
    payload: SearchRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Document search without generation.

    Retrieval costs a few seconds; generation costs most of a minute. An agent who
    only needs to *find* the right document should not pay for a synthesis.
    """
    enforce_chat_rate_limit(user.id)
    visible = {
        document.id: document
        for document in db.scalars(select(DocumentRecord).where(DocumentRecord.is_current.is_(True))).all()
        if can_access_document(user, document)
    }
    if not visible:
        return {"results": [], "detail": NO_DOCUMENTS_ANSWER}

    try:
        embedding = (await embed_texts([expand_for(user, payload.query)]))[0]
    except ModelUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    matches = search_similar_chunks(db, embedding, list(visible), payload.limit)
    return {
        "results": [
            {
                "score": round(score, 3),
                "document_id": chunk.document_id,
                "title": visible[chunk.document_id].title,
                "filename": visible[chunk.document_id].original_filename,
                "classification": visible[chunk.document_id].classification,
                "page": chunk.page_number,
                "excerpt": chunk.content[:400],
            }
            for score, chunk in matches
        ]
    }


@app.post("/chat")
async def chat(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    enforce_chat_rate_limit(user.id)
    conversation = resolve_conversation(db, payload.conversation_id, user)
    context = await build_chat_context(db, conversation, payload.message, user, stream=False)
    if context.refusal is not None:
        return save_assistant_exchange(db, conversation, payload.message, context.refusal, [], user.id)

    try:
        async with httpx.AsyncClient(timeout=settings.chat_timeout_seconds) as client:
            response = await client.post(f"{settings.ollama_base_url}/api/chat", json=context.request_body)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=MODEL_UNAVAILABLE) from exc

    answer = extract_answer(response.json().get("message", {}))
    return save_assistant_exchange(db, conversation, payload.message, answer, context.sources, user.id)


@app.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    enforce_chat_rate_limit(user.id)
    conversation = resolve_conversation(db, payload.conversation_id, user)
    context = await build_chat_context(db, conversation, payload.message, user, stream=True)
    conversation_id, user_id, question = conversation.id, user.id, payload.message

    async def emit():
        def event(body: dict[str, object]) -> str:
            return json.dumps(body, ensure_ascii=False) + "\n"

        def persist(answer: str, sources: list[dict[str, object]]) -> dict[str, object]:
            # The request session is closed once the endpoint returns, so the
            # generator commits through its own session.
            with SessionLocal() as stream_db:
                stored = stream_db.get(Conversation, conversation_id)
                return save_assistant_exchange(stream_db, stored, question, answer, sources, user_id)

        if context.refusal is not None:
            saved = persist(context.refusal, [])
            yield event({"type": "meta", "sources": [], "reasoning_expected": False})
            yield event({"type": "answer_start"})
            yield event({"type": "token", "value": context.refusal})
            yield event({"type": "done", "conversation": saved["conversation"], "message_id": saved["message_id"]})
            return

        yield event({
            "type": "meta",
            "sources": context.sources,
            "reasoning_expected": settings.ollama_chat_reasoning,
        })

        answer_started = not settings.ollama_chat_reasoning
        reasoning_buffer = ""
        answer_parts: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=settings.chat_timeout_seconds) as client:
                async with client.stream("POST", f"{settings.ollama_base_url}/api/chat", json=context.request_body) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        piece = json.loads(line).get("message", {}).get("content", "")
                        if not piece:
                            continue
                        if answer_started:
                            answer_parts.append(piece)
                            yield event({"type": "token", "value": piece})
                            continue
                        reasoning_buffer += piece
                        if REASONING_MARKER in reasoning_buffer:
                            remainder = reasoning_buffer.split(REASONING_MARKER, 1)[1]
                            answer_started = True
                            reasoning_buffer = ""
                            yield event({"type": "answer_start"})
                            if remainder:
                                answer_parts.append(remainder)
                                yield event({"type": "token", "value": remainder})
                        else:
                            yield event({"type": "thinking", "chars": len(reasoning_buffer)})
        except (httpx.HTTPError, json.JSONDecodeError):
            yield event({"type": "error", "detail": MODEL_UNAVAILABLE})
            return

        if not answer_started and reasoning_buffer:
            # The model never closed a reasoning block: treat everything as the answer.
            answer_parts.append(reasoning_buffer)
            yield event({"type": "answer_start"})
            yield event({"type": "token", "value": reasoning_buffer})

        answer = extract_answer({"content": "".join(answer_parts)})
        saved = persist(answer, context.sources)
        yield event({"type": "done", "conversation": saved["conversation"], "message_id": saved["message_id"]})

    return StreamingResponse(emit(), media_type="application/x-ndjson")


@app.post("/feedback", status_code=status.HTTP_204_NO_CONTENT)
def submit_feedback(
    payload: FeedbackRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Marks an answer useful or wrong. Each 'wrong' is a future evaluation case."""
    message = db.get(ChatMessage, payload.message_id)
    if not message:
        raise HTTPException(status_code=404, detail="Réponse introuvable")
    conversation = db.get(Conversation, message.conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="Réponse introuvable")

    question = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .where(ChatMessage.id < message.id)
        .where(ChatMessage.role == "user")
        .order_by(ChatMessage.id.desc())
        .limit(1)
    ).first()

    # One verdict per account and per answer: changing your mind replaces it.
    # Appending instead produced rows saying the same answer was both wrong and
    # useful, which then poisons whatever evaluation set is built from them.
    existing = db.scalars(
        select(AnswerFeedback)
        .where(AnswerFeedback.message_id == message.id)
        .where(AnswerFeedback.user_id == user.id)
    ).first()
    if existing is not None:
        existing.verdict = payload.verdict
        existing.comment = payload.comment.strip()
        existing.created_at = datetime.now(timezone.utc)
    else:
        db.add(AnswerFeedback(
            message_id=message.id,
            user_id=user.id,
            verdict=payload.verdict,
            question=question.content if question else "",
            comment=payload.comment.strip(),
        ))
    db.add(AuditEvent(actor_id=user.id, document_id=None, event_type=f"feedback:{payload.verdict}"))
    db.commit()


@app.get("/admin/feedback")
def list_feedback(
    _: User = Depends(require_roles("admin")), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    """Collected verdicts, newest first — the raw material for a real evaluation set."""
    entries = db.scalars(select(AnswerFeedback).order_by(AnswerFeedback.created_at.desc()).limit(200)).all()
    authors = {
        account.id: account.username
        for account in db.scalars(select(User).where(User.id.in_({entry.user_id for entry in entries}))).all()
    } if entries else {}
    return [
        {
            "id": entry.id,
            "verdict": entry.verdict,
            "question": entry.question,
            "comment": entry.comment,
            "author": authors.get(entry.user_id, "compte supprimé"),
            "created_at": entry.created_at.isoformat(),
        }
        for entry in entries
    ]


# Audit events are written by a dozen call sites and, until now, read by none.
# An audit trail nobody can consult is not an audit trail.
AUDIT_LABELS: dict[str, str] = {
    "document_uploaded": "Document importé",
    "document_deleted": "Document supprimé",
    "document_question_answered": "Question répondue sur document",
    "login_failed": "Tentative de connexion échouée",
    "user_created": "Compte créé",
    "user_updated": "Compte modifié",
    "user_password_reset": "Mot de passe réinitialisé",
    "registration_approved": "Demande d'accès approuvée",
    "registration_refused": "Demande d'accès refusée",
}


def audit_label(event_type: str) -> str:
    if event_type.startswith("tool_invoked:"):
        return f"Outil exécuté ({event_type.split(':', 1)[1]})"
    if event_type.startswith("feedback:"):
        verdict = event_type.split(":", 1)[1]
        return "Réponse marquée utile" if verdict == "useful" else "Réponse signalée incorrecte"
    return AUDIT_LABELS.get(event_type, event_type)


@app.get("/admin/audit")
def list_audit_events(
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
    limit: int = 100,
    event_type: str | None = None,
    actor_id: int | None = None,
) -> dict[str, object]:
    """The audit trail, newest first, with the actor and document resolved.

    Filters are applied in SQL rather than after the fact, so a narrow filter over a
    long history stays cheap.
    """
    limit = max(1, min(limit, 500))
    query = select(AuditEvent).order_by(AuditEvent.id.desc())
    if event_type:
        # Prefix match so "tool_invoked" catches "tool_invoked:count_users".
        query = query.where(AuditEvent.event_type.startswith(event_type))
    if actor_id is not None:
        query = query.where(AuditEvent.actor_id == actor_id)
    events = db.scalars(query.limit(limit)).all()

    actors = {
        account.id: account
        for account in db.scalars(select(User).where(User.id.in_({event.actor_id for event in events}))).all()
    } if events else {}
    document_ids = {event.document_id for event in events if event.document_id}
    documents = {
        document.id: document.title
        for document in db.scalars(select(DocumentRecord).where(DocumentRecord.id.in_(document_ids))).all()
    } if document_ids else {}

    return {
        "total": db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
        "kinds": sorted({event.event_type.split(":", 1)[0] for event in events}),
        "events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "label": audit_label(event.event_type),
                "actor": actors[event.actor_id].username if event.actor_id in actors else "compte supprimé",
                "actor_id": event.actor_id,
                "actor_department": department_label(actors[event.actor_id].department)
                if event.actor_id in actors else None,
                "document": documents.get(event.document_id) if event.document_id else None,
                "created_at": event.created_at.isoformat(),
            }
            for event in events
        ],
    }


@app.get("/admin/overview")
def administration_overview(
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Everything an administrator needs on one screen, service by service.

    The per-department breakdown is the point: a service with accounts but no
    document, or documents but no account, is an operational problem that a global
    total hides completely.
    """
    documents = db.scalars(select(DocumentRecord).where(DocumentRecord.is_current.is_(True))).all()
    accounts = db.scalars(select(User)).all()
    chunk_counts = dict(
        db.execute(
            select(DocumentChunk.document_id, func.count(DocumentChunk.id)).group_by(DocumentChunk.document_id)
        ).all()
    )

    per_department: list[dict[str, object]] = []
    for name in sorted(DOCUMENT_DEPARTMENTS):
        owned = [document for document in documents if document.department == name]
        members = [account for account in accounts if account.department == name]
        per_department.append({
            "value": name,
            "label": department_label(name),
            "documents": len(owned),
            "chunks": sum(chunk_counts.get(document.id, 0) for document in owned),
            # "transverse" is a document perimeter, not a service: nobody belongs to it.
            "accounts": None if name == TRANSVERSE else len(members),
            "last_import": max((d.created_at for d in owned), default=None).isoformat() if owned else None,
        })

    by_role: dict[str, int] = {}
    for account in accounts:
        if account.is_active and account.status == "active":
            by_role[account.role] = by_role.get(account.role, 0) + 1
    by_classification: dict[str, int] = {}
    for document in documents:
        by_classification[document.classification] = by_classification.get(document.classification, 0) + 1

    since = datetime.now(timezone.utc) - timedelta(days=7)
    recent = db.scalars(select(AuditEvent).where(AuditEvent.created_at >= since)).all()
    activity: dict[str, int] = {}
    for event in recent:
        key = event.event_type.split(":", 1)[0]
        activity[key] = activity.get(key, 0) + 1

    verdicts = db.scalars(select(AnswerFeedback)).all()
    expired = [document for document in documents if is_expired(document)]

    return {
        "documents": {
            "total": len(documents),
            "chunks": sum(chunk_counts.get(document.id, 0) for document in documents),
            "by_classification": by_classification,
            "expired": len(expired),
            "last_import": max((d.created_at for d in documents), default=None).isoformat() if documents else None,
        },
        "accounts": {
            "total": len(accounts),
            "active": len([a for a in accounts if a.is_active and a.status == "active"]),
            "pending": len([a for a in accounts if a.status == "pending"]),
            "refused": len([a for a in accounts if a.status == "refused"]),
            "suspended": len([a for a in accounts if not a.is_active]),
            # An account with no department reads only transverse documents. Almost
            # always an oversight, so it is surfaced rather than merely stored.
            "unattached": len([a for a in accounts if a.department is None and a.status == "active"]),
            "by_role": by_role,
        },
        "departments": per_department,
        "feedback": {
            "useful": len([v for v in verdicts if v.verdict == "useful"]),
            "wrong": len([v for v in verdicts if v.verdict == "wrong"]),
        },
        "activity_7d": activity,
        "model": {
            "chat": get_settings().ollama_chat_model,
            "embedding": get_settings().ollama_embedding_model,
            "ocr": ocr_available(),
            "database": "PostgreSQL + pgvector" if get_settings().database_url else "SQLite",
            "retention_days": get_settings().conversation_retention_days,
        },
    }


@app.post("/auth/register", status_code=status.HTTP_202_ACCEPTED)
def register(
    payload: RegistrationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """The only unauthenticated write endpoint in the application.

    Three rules follow from that:

    - **Identical response whether or not the username exists.** Otherwise
      registration becomes an oracle for enumerating who works at ANSI.
    - **Rate limited per source address**, so it cannot be used to flood the table.
    - **The account is created with no role and no department**, so it can read
      nothing until an administrator decides otherwise. The requested department is
      recorded as a hint, never applied.
    """
    client_ip = request.client.host if request.client else "unknown"
    limit = get_settings().registration_rate_limit_per_hour
    if limit > 0 and not _within_limit(f"register:ip:{client_ip}", limit, 3600):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de demandes depuis cette adresse. Réessayez plus tard.",
        )

    acknowledgement = {
        "detail": "Votre demande a été enregistrée. Un administrateur la validera avant votre premier accès."
    }
    if payload.requested_department not in DEPARTMENTS:
        raise HTTPException(status_code=422, detail="Service demandé invalide")

    if db.scalar(select(User).where(User.username == payload.username)):
        # Same body, same status code: an existing username is indistinguishable.
        logger.info("Demande d'inscription sur un identifiant existant depuis %s", client_ip)
        return acknowledgement

    db.add(User(
        username=payload.username,
        password_hash=password_hash.hash(payload.password),
        role="user",
        department=None,
        status="pending",
        requested_department=payload.requested_department,
        request_reason=payload.reason.strip(),
        is_active=True,
    ))
    db.commit()
    logger.info("Nouvelle demande d'inscription depuis %s", client_ip)
    return acknowledgement


@app.get("/admin/registrations")
def list_registrations(
    _: User = Depends(require_roles("admin")), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    pending = db.scalars(select(User).where(User.status == "pending").order_by(User.id)).all()
    return [
        {
            "id": account.id,
            "username": account.username,
            "requested_department": account.requested_department,
            "requested_department_label": department_label(account.requested_department),
            "reason": account.request_reason,
        }
        for account in pending
    ]


@app.post("/admin/registrations/{user_id}/approve")
def approve_registration(
    user_id: int,
    payload: ApproveRegistrationRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """The administrator chooses role and department; the request is only a hint."""
    account = db.get(User, user_id)
    if not account or account.status != "pending":
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if payload.role not in ROLES:
        raise HTTPException(status_code=422, detail="Rôle invalide")
    if payload.department not in DEPARTMENTS:
        raise HTTPException(status_code=422, detail="Service invalide")

    account.role = payload.role
    account.department = payload.department
    account.status = "active"
    account.is_active = True
    db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="registration_approved"))
    db.commit()
    db.refresh(account)
    return account_summary(account)


@app.post("/admin/registrations/{user_id}/refuse", status_code=status.HTTP_204_NO_CONTENT)
def refuse_registration(
    user_id: int,
    payload: RefuseRegistrationRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> None:
    account = db.get(User, user_id)
    if not account or account.status != "pending":
        raise HTTPException(status_code=404, detail="Demande introuvable")
    account.status = "refused"
    account.is_active = False
    account.request_reason = payload.reason.strip() or account.request_reason
    db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="registration_refused"))
    db.commit()


@app.get("/admin/users")
def list_users(
    _: User = Depends(require_roles("admin")), db: Session = Depends(get_db)
) -> list[dict[str, object]]:
    return [account_summary(account) for account in db.scalars(select(User).order_by(User.username)).all()]


@app.post("/admin/users", status_code=status.HTTP_201_CREATED)
def create_user(
    payload: CreateUserRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if payload.role not in ROLES:
        raise HTTPException(status_code=422, detail="Rôle invalide")
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=409, detail="Cet identifiant existe déjà")
    if payload.department is not None and payload.department not in DEPARTMENTS:
        raise HTTPException(status_code=422, detail="Département invalide")
    account = User(
        username=payload.username,
        password_hash=password_hash.hash(payload.password),
        role=payload.role,
        department=payload.department,
        status="active",
    )
    db.add(account)
    db.flush()
    db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="user_created"))
    db.commit()
    db.refresh(account)
    return account_summary(account)


@app.patch("/admin/users/{user_id}")
def update_user(
    user_id: int,
    payload: UpdateUserRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    account = db.get(User, user_id)
    if not account:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    if payload.role is not None and payload.role not in ROLES:
        raise HTTPException(status_code=422, detail="Rôle invalide")
    if account.id == admin.id and (payload.is_active is False or (payload.role or "admin") != "admin"):
        raise HTTPException(status_code=422, detail="Vous ne pouvez pas retirer vos propres accès administrateur")

    next_role = payload.role or account.role
    next_active = account.is_active if payload.is_active is None else payload.is_active
    losing_admin = account.role == "admin" and (next_role != "admin" or not next_active)
    if losing_admin and count_active_admins(db) <= 1:
        raise HTTPException(status_code=422, detail="Au moins un administrateur actif doit subsister")

    if payload.department is not None and payload.department not in DEPARTMENTS:
        raise HTTPException(status_code=422, detail="Département invalide")
    account.role = next_role
    account.is_active = next_active
    if payload.department is not None:
        # Takes effect immediately: authorisation reads the database on every
        # request, never a claim carried in the token.
        account.department = payload.department
    db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="user_updated"))
    db.commit()
    db.refresh(account)
    return account_summary(account)


@app.post("/admin/users/{user_id}/password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(
    user_id: int,
    payload: ResetPasswordRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> None:
    account = db.get(User, user_id)
    if not account:
        raise HTTPException(status_code=404, detail="Compte introuvable")
    account.password_hash = password_hash.hash(payload.password)
    db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="user_password_reset"))
    db.commit()
