r"""The evaluation dataset is a contract, so it is checked like one (phase G).

`tests/evaluate.py` takes twenty minutes and reports percentages. That makes a broken
dataset expensive and hard to spot: if a document's department changes, a question filed
as "out of perimeter" quietly becomes answerable, and the next run reports a failure that
looks exactly like a model regression.

These checks are static — no database, no Ollama, instant — and they hold the properties
the long run assumes:

- every question points at a document that exists;
- a question expected to be **answered** is asked by an account that may actually read its
  source, otherwise the expectation is impossible and the measurement meaningless;
- a question expected to be **refused for perimeter reasons** really is out of the asker's
  perimeter, so it measures the partitioning rather than a missing document.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_dataset.py -q
"""

import json
from pathlib import Path

import pytest

from app.access import DEPARTMENTS, TRANSVERSE, can_access_document
from app.database import DocumentRecord, User


DATASET = Path(__file__).parent / "evaluation" / "dataset.json"
ADMIN = "admin"

dataset = json.loads(DATASET.read_text(encoding="utf-8"))
DOCUMENTS = {document["filename"]: document for document in dataset["documents"]}
QUESTIONS = dataset["questions"]

ANSWERABLE = [q for q in QUESTIONS if not q.get("refusal")]
OUT_OF_PERIMETER = [q for q in QUESTIONS if q.get("out_of_perimeter")]


def asker(question: dict) -> User:
    """The account that will be created for this question by the harness."""
    group = question.get("asked_by") or ADMIN
    if group == ADMIN:
        return User(username="x", password_hash="x", role="admin", department="technique")
    return User(username="x", password_hash="x", role="user", department=group)


def as_record(document: dict) -> DocumentRecord:
    return DocumentRecord(
        title=document["title"],
        original_filename=document["filename"],
        stored_filename=document["filename"],
        content_type="text/plain",
        classification=document["classification"],
        allowed_roles=document["allowed_roles"],
        created_by=1,
        department=document.get("department", TRANSVERSE),
    )


# --------------------------------------------------------------------------
# Structure
# --------------------------------------------------------------------------

def test_the_dataset_has_documents_and_questions():
    assert DOCUMENTS and QUESTIONS


@pytest.mark.parametrize("filename", sorted(DOCUMENTS))
def test_every_document_declares_a_real_department(filename):
    assert DOCUMENTS[filename].get("department", TRANSVERSE) in DEPARTMENTS | {TRANSVERSE}


@pytest.mark.parametrize("filename", sorted(DOCUMENTS))
def test_every_document_has_content(filename):
    assert DOCUMENTS[filename]["content"].strip()


def test_every_asking_service_is_a_real_department():
    for question in QUESTIONS:
        group = question.get("asked_by")
        assert group is None or group in DEPARTMENTS, question["question"]


def test_an_answerable_question_names_a_document_that_exists():
    for question in ANSWERABLE:
        assert question["source"] in DOCUMENTS, question["question"]


def test_an_answerable_question_states_what_it_expects():
    for question in ANSWERABLE:
        assert question.get("expected"), question["question"]


# --------------------------------------------------------------------------
# The properties the twenty-minute run assumes
# --------------------------------------------------------------------------

def test_an_answerable_question_is_asked_by_someone_who_may_read_its_source():
    """Otherwise the expectation cannot be met and the run reports a false regression."""
    for question in ANSWERABLE:
        document = as_record(DOCUMENTS[question["source"]])
        assert can_access_document(asker(question), document), (
            f"« {question['question'][:60]} » est posee par "
            f"{question.get('asked_by') or ADMIN} mais sa source est hors de son perimetre"
        )


def test_an_out_of_perimeter_question_really_is_out_of_perimeter():
    """The whole point of these questions: they must fail on the partitioning, not on a
    document that simply is not there."""
    for question in OUT_OF_PERIMETER:
        group = question.get("asked_by")
        assert group in DEPARTMENTS, f"une question hors perimetre doit etre posee par un service : {question}"
        user = asker(question)
        readable = [name for name, document in DOCUMENTS.items() if can_access_document(user, as_record(document))]
        # The answer must exist somewhere in the corpus, and nowhere the asker can read it.
        elsewhere = [name for name in DOCUMENTS if name not in readable]
        assert elsewhere, question["question"]
        assert not any(question["question"] in DOCUMENTS[name]["content"] for name in readable)


def test_out_of_perimeter_questions_are_also_answerable_by_their_own_service():
    """Each one is the mirror of a question another service answers. If the pair ever drifts
    apart, the refusal stops proving anything — the answer might simply not exist."""
    answerable_texts = {question["question"] for question in ANSWERABLE}
    for question in OUT_OF_PERIMETER:
        assert question["question"] in answerable_texts, (
            f"aucun service ne repond a « {question['question'][:60]} » : "
            "le refus ne prouve donc pas le cloisonnement"
        )


def test_every_department_is_measured():
    """A service with no question of its own is a service whose quality nobody watches."""
    asked = {question.get("asked_by") for question in QUESTIONS} - {None}
    assert DEPARTMENTS <= asked, f"services sans question : {sorted(DEPARTMENTS - asked)}"


def test_every_department_owns_at_least_one_document():
    owners = {document.get("department", TRANSVERSE) for document in DOCUMENTS.values()}
    assert DEPARTMENTS <= owners, f"services sans document : {sorted(DEPARTMENTS - owners)}"


def test_transverse_documents_are_still_present():
    """The over-blocking control questions depend on them."""
    assert any(document.get("department", TRANSVERSE) == TRANSVERSE for document in DOCUMENTS.values())


def test_a_refusal_question_does_not_also_claim_an_expected_answer():
    for question in QUESTIONS:
        if question.get("refusal"):
            assert not question.get("expected"), question["question"]
