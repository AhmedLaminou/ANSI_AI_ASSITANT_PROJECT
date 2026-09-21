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
        },
        "users": {
            "email": "VARCHAR(160) NULL",
            "department": "VARCHAR(32) NULL",
            "status": "VARCHAR(16) NOT NULL DEFAULT 'active'",
            "requested_department": "VARCHAR(32) NULL",
            "request_reason": "TEXT NOT NULL DEFAULT ''",
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
