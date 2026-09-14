r"""Unit tests for the pure logic: chunking, similarity, access control, reasoning strip.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_units.py -q
These never call Ollama and never touch the database.
"""

import pytest
from fastapi import HTTPException

from app.database import DocumentRecord, User
from app.main import (
    RELEVANCE_THRESHOLD,
    can_access_document,
    enforce_chat_rate_limit,
    extract_answer,
    safe_roles,
    _chat_calls,
)
from app.rag import chunk_pages, cosine_similarity, parse_allowed_roles


# --------------------------------------------------------------------------
# Reasoning strip
# --------------------------------------------------------------------------

def test_extract_answer_removes_reasoning_block():
    raw = "Okay, let me think about this.\nThe answer is in [S1].</think>Le projet démarre le 15 octobre [S1]."
    assert extract_answer({"content": raw}) == "Le projet démarre le 15 octobre [S1]."


def test_extract_answer_keeps_plain_content():
    assert extract_answer({"content": "  Réponse directe.  "}) == "Réponse directe."


def test_extract_answer_uses_last_marker():
    raw = "premier</think>deuxième</think>réponse finale"
    assert extract_answer({"content": raw}) == "réponse finale"


def test_extract_answer_handles_missing_content():
    assert extract_answer({}) == ""


# --------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------

def test_chunk_pages_skips_blank_pages():
    assert chunk_pages([(1, "   "), (2, "")]) == []


def test_chunk_pages_keeps_page_numbers():
    chunks = chunk_pages([(1, "Texte de la première page."), (7, "Texte de la septième page.")])
    assert [chunk.page_number for chunk in chunks] == [1, 7]


def test_chunk_pages_splits_long_text_with_overlap():
    text = " ".join(f"mot{index}" for index in range(600))
    chunks = chunk_pages([(1, text)])
    assert len(chunks) > 1
    assert all(chunk.page_number == 1 for chunk in chunks)
    # Consecutive chunks must share text, otherwise a sentence cut in half is lost.
    assert chunks[0].content[-40:] in chunks[0].content
    assert any(word in chunks[1].content for word in chunks[0].content.split()[-3:])


def test_chunk_pages_normalises_whitespace():
    chunk = chunk_pages([(1, "un   deux\n\n\ttrois")])[0]
    assert chunk.content == "un deux trois"


# --------------------------------------------------------------------------
# Similarity
# --------------------------------------------------------------------------

def test_cosine_similarity_identical_vectors():
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_zero_vector_does_not_divide_by_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_relevance_threshold_rejects_unrelated_vectors():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) < RELEVANCE_THRESHOLD


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------

def build_document(allowed: str) -> DocumentRecord:
    return DocumentRecord(
        title="Doc",
        original_filename="doc.txt",
        stored_filename="stored.txt",
        content_type="text/plain",
        classification="interne",
        allowed_roles=allowed,
        created_by=1,
    )


@pytest.mark.parametrize(
    ("role", "allowed", "expected"),
    [
        ("user", "admin,document_manager,user", True),
        ("user", "admin", False),
        ("user", "admin,document_manager", False),
        ("admin", "admin", True),
        ("document_manager", "admin,document_manager", True),
    ],
)
def test_can_access_document(role, allowed, expected):
    assert can_access_document(User(username="x", password_hash="x", role=role), build_document(allowed)) is expected


def test_parse_allowed_roles_ignores_blanks():
    assert parse_allowed_roles("admin, ,user ") == {"admin", "user"}


def test_safe_roles_sorts_and_deduplicates():
    assert safe_roles("user,admin,user") == "admin,user"


def test_safe_roles_rejects_unknown_role():
    with pytest.raises(HTTPException) as error:
        safe_roles("admin,root")
    assert error.value.status_code == 422


def test_safe_roles_rejects_empty():
    with pytest.raises(HTTPException):
        safe_roles("  ")


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

def test_rate_limit_allows_then_blocks(monkeypatch):
    from app import main

    monkeypatch.setattr(main.get_settings(), "chat_rate_limit_per_minute", 3, raising=False)
    _chat_calls.pop(999, None)
    for _ in range(3):
        enforce_chat_rate_limit(999)
    with pytest.raises(HTTPException) as error:
        enforce_chat_rate_limit(999)
    assert error.value.status_code == 429
    _chat_calls.pop(999, None)


def test_rate_limit_is_per_user(monkeypatch):
    from app import main

    monkeypatch.setattr(main.get_settings(), "chat_rate_limit_per_minute", 1, raising=False)
    _chat_calls.pop(1001, None)
    _chat_calls.pop(1002, None)
    enforce_chat_rate_limit(1001)
    enforce_chat_rate_limit(1002)  # different user must not be blocked
    with pytest.raises(HTTPException):
        enforce_chat_rate_limit(1001)
    _chat_calls.pop(1001, None)
    _chat_calls.pop(1002, None)
