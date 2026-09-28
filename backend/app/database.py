import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import BACKEND_DIR, get_settings


# embeddinggemma produces 768 floats. Changing the embedding model changes this
# number *and* the vector space: every document must then be re-indexed.
EMBEDDING_DIMENSIONS = 768

DATABASE_PATH = BACKEND_DIR / "data" / "ansi_ai.db"


def build_engine():
    """SQLite for the POC, PostgreSQL + pgvector when DATABASE_URL points at one."""
    url = get_settings().database_url or f"sqlite:///{DATABASE_PATH.as_posix()}"
    if url.startswith("sqlite"):
        DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url, pool_pre_ping=True)


engine = build_engine()
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def is_postgres() -> bool:
    return engine.dialect.name == "postgresql"


class Embedding(TypeDecorator):
    """Stored as a real `vector` on PostgreSQL, as JSON text on SQLite.

    Python code always sees a plain list of floats, whichever engine is in use.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from pgvector.sqlalchemy import Vector

            return dialect.type_descriptor(Vector(EMBEDDING_DIMENSIONS))
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        if value is None or dialect.name == "postgresql":
            return value
        return json.dumps(value, separators=(",", ":"))

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return list(value)
        return json.loads(value)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Professional address. Nullable because accounts created before it existed have
    # none; new registrations always carry one, and sign-in accepts either.
    email: Mapped[str | None] = mapped_column(String(160), unique=True, nullable=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Perimeter, independent of the role. NULL means not yet assigned (pending account).
    department: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # pending | active | refused | suspended. Existing rows default to active.
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active", index=True)
    requested_department: Mapped[str | None] = mapped_column(String(32), nullable=True)
    request_reason: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Carried in every session token and compared on every request. Incrementing it
    # revokes every session issued before - the only way a stateless token can be
    # withdrawn before it expires.
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Set when an administrator chooses the password: the agent must replace it
    # before doing anything else, so the administrator never knows a live password.
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class DocumentRecord(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255), unique=True)
    content_type: Mapped[str] = mapped_column(String(100))
    classification: Mapped[str] = mapped_column(String(64), default="interne")
    allowed_roles: Mapped[str] = mapped_column(String(255), default="admin,document_manager,user")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    # An administrative procedure expires. Answering confidently from a document
    # that lapsed last year is a correctness problem, not a cosmetic one.
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Which perimeter the document belongs to; "transverse" means every department.
    department: Mapped[str] = mapped_column(
        String(32), default="transverse", server_default="transverse", index=True
    )
    # A corpus without owners rots: nobody notices a procedure has gone stale until
    # an agent acts on it. The owner is who gets asked; the review date is when.
    # Distinct from valid_until, which is when the text stops being true -
    # review_due is when someone must check whether it still is.
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    review_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "ordinal", name="uq_document_chunk_ordinal"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    page_number: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Embedding)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AnswerFeedback(Base):
    """One click on an answer. Turns real usage into evaluation cases.

    Stores the question and the verdict, not the answer: the question is what a
    future evaluation set needs, and keeping less content is the safer default
    while the retention policy is unsettled.
    """

    __tablename__ = "answer_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("chat_messages.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    verdict: Mapped[str] = mapped_column(String(16))  # "useful" | "wrong"
    question: Mapped[str] = mapped_column(Text)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True, index=True)
    event_type: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class PasswordResetRequest(Base):
    """An agent who forgot their password asks the administrator.

    Offline, there is no mail relay to send a reset link through, and an
    unauthenticated endpoint that resets a password would be a second way in. So the
    endpoint only *files a request*; the administrator acts on it and hands over a
    temporary password by another channel, which the agent must then replace.
    """

    __tablename__ = "password_reset_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)  # pending|resolved|dismissed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UnansweredQuestion(Base):
    """A question the assistant could not answer - the most useful signal it produces.

    Every refusal is either a missing document or a vocabulary gap. Aggregated,
    they say what the corpus lacks; left in the audit trail, they say nothing.

    The department is copied at the time of the question rather than joined from the
    account, so a later transfer does not rewrite history - the lesson of the audit
    journal, which shows current attributes (ARCHITECTURE_TECHNIQUE 7.12).

    Holds question text, so it falls under the same retention rule as conversations.
    """

    __tablename__ = "unanswered_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    department: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text)
    # "no_match": nothing retrieved was close enough. "model_refusal": extracts were
    # found but the model judged them insufficient - by far the commoner case, since
    # the similarity threshold separates almost nothing (5.1).
    reason: Mapped[str] = mapped_column(String(24))
    embedding: Mapped[list[float] | None] = mapped_column(Embedding, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )
    # An administrator marks a gap as addressed once a document covers it.
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class ServiceContact(Base):
    """Who to ask when the assistant cannot answer, per perimeter.

    "Je ne trouve pas" is honest and unhelpful. For an internal assistant the right
    answer is often a person. One row per department, plus "transverse" for the
    general contact.
    """

    __tablename__ = "service_contacts"

    department: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(160), default="")
    phone: Mapped[str] = mapped_column(String(40), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ValidatedAnswer(Base):
    """An answer a person checked once, served instantly thereafter.

    The frequent questions are regenerated at thirty seconds each, forever, and each
    regeneration is a fresh chance to be wrong. A validated answer is faster and more
    reliable, because a human read it.

    It carries a department and obeys the same perimeter rule as a document: a finance
    answer validated by an administrator is not served to an HR agent.
    """

    __tablename__ = "validated_answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question: Mapped[str] = mapped_column(Text)
    # JSON list of alternative phrasings, matched after normalisation.
    phrasings: Mapped[str] = mapped_column(Text, default="[]")
    answer: Mapped[str] = mapped_column(Text)
    department: Mapped[str] = mapped_column(String(32), default="transverse", index=True)
    validated_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    validated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1", index=True)
    times_served: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


def ensure_extensions() -> None:
    """pgvector must exist before create_all() can build a `vector` column."""
    if not is_postgres():
        return
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")


def ensure_schema() -> None:
    """Additive migration for databases created before a column existed.

    Proper migrations (Alembic) are the next step; until then this keeps an existing
    database usable instead of forcing operators to delete it.
    """
    expected = {
        "documents": {
            "version": "INTEGER NOT NULL DEFAULT 1",
            "is_current": "BOOLEAN NOT NULL DEFAULT TRUE" if is_postgres() else "BOOLEAN NOT NULL DEFAULT 1",
            "valid_until": "TIMESTAMP NULL" if is_postgres() else "DATETIME NULL",
            "department": "VARCHAR(32) NOT NULL DEFAULT 'transverse'",
            "owner_id": "INTEGER NULL",
            "review_due": "TIMESTAMP NULL" if is_postgres() else "DATETIME NULL",
        },
        "users": {
            "email": "VARCHAR(160) NULL",
            "department": "VARCHAR(32) NULL",
            "status": "VARCHAR(16) NOT NULL DEFAULT 'active'",
            "requested_department": "VARCHAR(32) NULL",
            "request_reason": "TEXT NOT NULL DEFAULT ''",
            "token_version": "INTEGER NOT NULL DEFAULT 0",
            "must_change_password": (
                "BOOLEAN NOT NULL DEFAULT FALSE" if is_postgres() else "BOOLEAN NOT NULL DEFAULT 0"
            ),
        },
    }
    with engine.begin() as connection:
        for table, columns in expected.items():
            if is_postgres():
                present = {
                    row[0]
                    for row in connection.exec_driver_sql(
                        "SELECT column_name FROM information_schema.columns WHERE table_name = %s", (table,)
                    )
                }
            else:
                present = {row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table})")}
            if not present:
                continue
            for column, definition in columns.items():
                if column not in present:
                    connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        if is_postgres():
            # Approximate nearest-neighbour index; without it pgvector scans every row.
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS document_chunks_embedding_hnsw "
                "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
            )


def initialise_database() -> None:
    """Bring an empty or older database up to the current schema.

    Single entry point used by the API lifespan and by the test suites, so a fresh
    PostgreSQL instance is provisioned exactly like SQLite.
    """
    ensure_extensions()
    Base.metadata.create_all(bind=engine)
    ensure_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
