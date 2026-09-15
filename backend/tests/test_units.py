r"""Unit tests for the pure logic: chunking, similarity, access control, reasoning strip.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_units.py -q
These never call Ollama and never touch the database.
"""

import asyncio
import io
import re
import unicodedata

import pytest
from fastapi import HTTPException
from pypdf import PdfReader

import app.graph
from app.database import DocumentRecord, User
from app.graph import build_assistant_graph, classify_intent
from app.glossary import expand_acronyms
from app.tools import find_tool
from app.main import (
    RELEVANCE_THRESHOLD,
    can_access_document,
    cors_policy,
    enforce_chat_rate_limit,
    enforce_login_rate_limit,
    extract_answer,
    record_failed_login,
    safe_roles,
    _rate_buckets,
)
from app.rag import chunk_pages, cosine_similarity, extract_pages, ocr_available, parse_allowed_roles


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
# OCR fallback for scanned PDFs
# --------------------------------------------------------------------------

def build_scanned_pdf(lines: list[str]) -> bytes:
    """An image-only PDF: what a scanner produces, with no embedded text layer."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    offset = 200
    for line in lines:
        draw.text((150, offset), line, fill="black", font=font)
        offset += 90
    buffer = io.BytesIO()
    image.save(buffer, format="PDF", resolution=200)
    return buffer.getvalue()


@pytest.mark.skipif(not ocr_available(), reason="Tesseract n'est pas installé")
def test_scanned_pdf_has_no_text_layer_but_ocr_recovers_it():
    pdf = build_scanned_pdf(["Note de service numero 47", "Reunion le 12 decembre 2026"])

    # Confirm the fixture really is a scan: pypdf alone finds nothing.
    assert (PdfReader(io.BytesIO(pdf)).pages[0].extract_text() or "").strip() == ""

    recovered = " ".join(text for _, text in extract_pages("scan.pdf", pdf))
    assert "47" in recovered
    assert "2026" in recovered
    assert normalise_accents("decembre") in normalise_accents(recovered)


def normalise_accents(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text.lower())
    return "".join(character for character in stripped if unicodedata.category(character) != "Mn")


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

@pytest.fixture(autouse=True)
def clean_rate_buckets():
    _rate_buckets.clear()
    yield
    _rate_buckets.clear()


def test_rate_limit_allows_then_blocks(monkeypatch):
    from app import main

    monkeypatch.setattr(main.get_settings(), "chat_rate_limit_per_minute", 3, raising=False)
    for _ in range(3):
        enforce_chat_rate_limit(999)
    with pytest.raises(HTTPException) as error:
        enforce_chat_rate_limit(999)
    assert error.value.status_code == 429


def test_rate_limit_is_per_user(monkeypatch):
    from app import main

    monkeypatch.setattr(main.get_settings(), "chat_rate_limit_per_minute", 1, raising=False)
    enforce_chat_rate_limit(1001)
    enforce_chat_rate_limit(1002)  # different user must not be blocked
    with pytest.raises(HTTPException):
        enforce_chat_rate_limit(1001)


# --------------------------------------------------------------------------
# CORS policy
# --------------------------------------------------------------------------

def test_production_allows_only_the_configured_origin():
    policy = cors_policy("production", "https://assistant.ansi.ne")
    assert policy == {"allow_origins": ["https://assistant.ansi.ne"]}
    assert "allow_origin_regex" not in policy, "production must never accept a pattern of origins"


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:5173", "http://localhost:5174", "http://127.0.0.1:5173", "http://localhost"],
)
def test_development_accepts_any_local_port(origin):
    pattern = cors_policy("development", "http://localhost:5173")["allow_origin_regex"]
    assert re.fullmatch(pattern, origin), f"{origin} should be usable in development"


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example.com",
        "http://localhost.evil.com",
        "http://127.0.0.1.evil.com",
        "https://localhost:5173",
        "http://notlocalhost:5173",
    ],
)
def test_development_still_rejects_foreign_origins(origin):
    """The development convenience must not become an open door."""
    pattern = cors_policy("development", "http://localhost:5173")["allow_origin_regex"]
    assert not re.fullmatch(pattern, origin), f"{origin} must never be accepted"


# --------------------------------------------------------------------------
# Intent routing: a greeting must not be treated as a document search
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message",
    ["salut", "Bonjour !", "bonsoir", "Coucou", "Merci beaucoup", "ça va ?", "Qui es-tu ?",
     "Que peux-tu faire ?", "au revoir", "ok", "   ", "hello"],
)
def test_greetings_are_social(message):
    assert classify_intent(message) == "social"


@pytest.mark.parametrize(
    "message",
    [
        "Bonjour, quel est le budget du projet pilote ?",
        "salut quelle est la politique de mot de passe",
        "Quelle est la durée de rétention des sauvegardes ?",
        "Merci de résumer la procédure de gestion des accès",
        "aide-moi à retrouver la note de cadrage du projet",
    ],
)
def test_real_questions_stay_documentary(message):
    """A courtesy prefix must not disguise a genuine question as small talk."""
    assert classify_intent(message) == "documentary"


def test_social_path_never_touches_retrieval():
    """A greeting must cost nothing: no embedding, no search, no generation."""
    calls = []

    async def retrieve(question):
        calls.append(question)
        return [(0.9, "pertinent")]

    graph = build_assistant_graph(retrieve, relevance_threshold=0.18, max_attempts=2)
    state = asyncio.run(graph.ainvoke({"question": "salut", "search_question": "salut", "attempts": 0}))

    assert state["outcome"] == "social"
    assert calls == [], "a greeting triggered a document search"


# --------------------------------------------------------------------------
# Acronym expansion
# --------------------------------------------------------------------------

def test_acronym_is_expanded():
    expanded = expand_acronyms("Quelles sont les obligations de la DSI ?")
    assert "direction des systèmes d'information" in expanded
    assert "DSI" in expanded, "the original wording must be preserved"


def test_expansion_is_skipped_when_already_explicit():
    question = "Quelles sont les obligations de la direction des systèmes d'information ?"
    assert expand_acronyms(question) == question


def test_unknown_acronym_leaves_the_question_untouched():
    question = "Quelles sont les règles du XYZQ ?"
    assert expand_acronyms(question) == question


def test_acronym_matching_ignores_case_and_accents():
    assert "réseau privé virtuel" in expand_acronyms("le vpn est-il obligatoire ?")


def test_acronym_does_not_match_inside_a_word():
    """'SI' must not fire on 'ainsi' or 'si'."""
    question = "Ainsi, que faut-il faire si le poste est perdu ?"
    assert expand_acronyms(question) == question


# --------------------------------------------------------------------------
# Business tools: matched deterministically, never chosen by the model
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Combien d'utilisateurs sont enregistrés ?", "count_users"),
        ("Quel est le nombre de comptes ?", "count_users"),
        ("Combien de documents sont accessibles ?", "count_documents"),
        ("Quels documents puis-je consulter ?", "list_documents"),
        ("Donne-moi les statistiques du corpus", "corpus_statistics"),
    ],
)
def test_tool_questions_are_matched(question, expected):
    tool = find_tool(question)
    assert tool is not None and tool.name == expected


@pytest.mark.parametrize(
    "question",
    [
        "Combien de jours de télétravail par semaine ?",
        "Quelle est la durée de rétention des sauvegardes ?",
        "Qui est le responsable du projet pilote ?",
        "Combien de temps faut-il pour signaler un incident ?",
    ],
)
def test_documentary_questions_do_not_trigger_a_tool(question):
    """A question about document *content* must not be answered from the database."""
    assert find_tool(question) is None


def test_count_users_is_admin_only():
    tool = find_tool("Combien d'utilisateurs sont enregistrés ?")
    assert tool.roles == frozenset({"admin"})


def test_other_tools_are_open_to_every_role():
    for question in ("Combien de documents ?", "Quels documents ?", "statistiques"):
        assert find_tool(question).roles == frozenset({"admin", "document_manager", "user"})


def test_tool_branch_short_circuits_retrieval():
    """A database question must not embed, search or generate."""
    searched = []

    async def retrieve(question):
        searched.append(question)
        return [(0.9, "pertinent")]

    graph = build_assistant_graph(
        retrieve, relevance_threshold=0.18, max_attempts=2, run_tool=lambda q: "42 comptes actifs."
    )
    state = asyncio.run(
        graph.ainvoke({"question": "Combien d'utilisateurs ?", "search_question": "x", "attempts": 0})
    )
    assert state["outcome"] == "tool"
    assert state["tool_answer"] == "42 comptes actifs."
    assert searched == [], "a database question triggered a document search"


def test_unmatched_tool_falls_back_to_documents():
    """If the tool declines, the question must still reach the documents."""
    searched = []

    async def retrieve(question):
        searched.append(question)
        return [(0.9, "pertinent")]

    graph = build_assistant_graph(
        retrieve, relevance_threshold=0.18, max_attempts=2, run_tool=lambda q: None
    )
    state = asyncio.run(
        graph.ainvoke({"question": "Combien de documents ?", "search_question": "Combien de documents ?", "attempts": 0})
    )
    assert state["outcome"] == "answer"
    assert searched, "the fallback never reached retrieval"


# --------------------------------------------------------------------------
# Decision graph: conditional branch and bounded cycle
# --------------------------------------------------------------------------

@pytest.fixture
def stub_rewrite(monkeypatch):
    async def fake(question):
        return f"reformulation de {question}"

    monkeypatch.setattr(app.graph, "rewrite_question", fake)


def test_weak_retrieval_is_rescued_by_rewriting(stub_rewrite):
    """A question worded unlike the document must not fail on the first attempt."""
    seen = []

    async def retrieve(question):
        seen.append(question)
        return [(0.05, "faible")] if len(seen) == 1 else [(0.71, "pertinent")]

    graph = build_assistant_graph(retrieve, relevance_threshold=0.18, max_attempts=2)
    state = asyncio.run(graph.ainvoke({"question": "q", "search_question": "q", "attempts": 0}))

    assert len(seen) == 2, "the graph did not retry after a weak result"
    assert seen[1] != seen[0], "the retry reused the original wording"
    assert state["outcome"] == "answer"
    assert state["rewritten"] is True


def test_cycle_is_bounded_and_ends_in_refusal(stub_rewrite):
    """Nothing relevant must end in a refusal, never an endless rewrite loop."""
    attempts = []

    async def always_weak(question):
        attempts.append(question)
        return [(0.01, "rien")]

    graph = build_assistant_graph(always_weak, relevance_threshold=0.18, max_attempts=2)
    state = asyncio.run(graph.ainvoke({"question": "q", "search_question": "q", "attempts": 0}))

    assert len(attempts) == 2
    assert state["outcome"] == "refuse"


def test_strong_first_result_skips_the_rewrite_branch(stub_rewrite):
    """The extra latency of a rewrite must only be paid when it is needed."""
    attempts = []

    async def strong(question):
        attempts.append(question)
        return [(0.83, "pertinent")]

    graph = build_assistant_graph(strong, relevance_threshold=0.18, max_attempts=2)
    state = asyncio.run(graph.ainvoke({"question": "q", "search_question": "q", "attempts": 0}))

    assert len(attempts) == 1
    assert state["outcome"] == "answer"
    assert not state.get("rewritten")


def test_empty_retrieval_still_refuses(stub_rewrite):
    async def nothing(question):
        return []

    graph = build_assistant_graph(nothing, relevance_threshold=0.18, max_attempts=2)
    state = asyncio.run(graph.ainvoke({"question": "q", "search_question": "q", "attempts": 0}))
    assert state["outcome"] == "refuse"


def test_retry_can_be_disabled(stub_rewrite):
    attempts = []

    async def weak(question):
        attempts.append(question)
        return [(0.02, "faible")]

    graph = build_assistant_graph(weak, relevance_threshold=0.18, max_attempts=1)
    state = asyncio.run(graph.ainvoke({"question": "q", "search_question": "q", "attempts": 0}))
    assert len(attempts) == 1
    assert state["outcome"] == "refuse"


# --------------------------------------------------------------------------
# Login brute-force protection
# --------------------------------------------------------------------------

def test_login_limit_blocks_after_repeated_failures(monkeypatch):
    from app import main

    monkeypatch.setattr(main.get_settings(), "login_rate_limit_per_minute", 3, raising=False)
    for _ in range(3):
        enforce_login_rate_limit("victim", "10.0.0.1")
        record_failed_login("victim", "10.0.0.1")
    with pytest.raises(HTTPException) as error:
        enforce_login_rate_limit("victim", "10.0.0.1")
    assert error.value.status_code == 429


def test_login_limit_blocks_same_account_from_another_address(monkeypatch):
    """Credential stuffing rotating source addresses must still hit the account budget."""
    from app import main

    monkeypatch.setattr(main.get_settings(), "login_rate_limit_per_minute", 3, raising=False)
    for index in range(3):
        enforce_login_rate_limit("victim", f"10.0.0.{index}")
        record_failed_login("victim", f"10.0.0.{index}")
    with pytest.raises(HTTPException):
        enforce_login_rate_limit("victim", "10.0.0.99")


def test_login_limit_blocks_spraying_many_accounts_from_one_address(monkeypatch):
    """Password spraying across accounts must still hit the address budget."""
    from app import main

    monkeypatch.setattr(main.get_settings(), "login_rate_limit_per_minute", 3, raising=False)
    for index in range(3):
        enforce_login_rate_limit(f"account{index}", "10.0.0.7")
        record_failed_login(f"account{index}", "10.0.0.7")
    with pytest.raises(HTTPException):
        enforce_login_rate_limit("another-account", "10.0.0.7")


def test_login_limit_is_case_insensitive_on_username(monkeypatch):
    from app import main

    monkeypatch.setattr(main.get_settings(), "login_rate_limit_per_minute", 2, raising=False)
    for name in ("Victim", "VICTIM"):
        enforce_login_rate_limit(name, "10.0.0.5")
        record_failed_login(name, "10.0.0.5")
    with pytest.raises(HTTPException):
        enforce_login_rate_limit("victim", "10.0.0.5")


def test_successful_logins_do_not_consume_the_budget(monkeypatch):
    """Only failures are recorded, so normal use never locks anyone out."""
    from app import main

    monkeypatch.setattr(main.get_settings(), "login_rate_limit_per_minute", 2, raising=False)
    for _ in range(20):
        enforce_login_rate_limit("busy-user", "10.0.0.8")  # no record_failed_login


def test_login_limit_disabled_when_zero(monkeypatch):
    from app import main

    monkeypatch.setattr(main.get_settings(), "login_rate_limit_per_minute", 0, raising=False)
    for _ in range(50):
        record_failed_login("victim", "10.0.0.1")
    enforce_login_rate_limit("victim", "10.0.0.1")  # must not raise
