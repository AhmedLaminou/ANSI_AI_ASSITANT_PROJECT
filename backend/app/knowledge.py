"""Three things the assistant knows besides its documents.

**Who to ask** when it cannot answer. « Je ne trouve pas » is honest and unhelpful;
for an internal assistant the right answer is often a person. One contact per
service, plus a general one under `transverse`.

**What a person already validated.** The frequent questions were regenerated at
thirty seconds each, forever, each time a fresh chance to be wrong. A validated
answer is served instantly and carries who checked it and when.

**What the previous turn was about.** « Et pour un temps partiel ? » means nothing on
its own; it means a great deal after a question on leave. A follow-up is searched
together with the question it follows, and the documents the last answer cited get
precedence.

All three follow the project's rule for anything near a decision: deterministic,
readable, testable. No model decides whether a question is a follow-up or whether a
validated answer applies.
"""

import json
import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import TRANSVERSE, department_label, sees_every_department
from .database import ChatMessage, Conversation, ServiceContact, User, ValidatedAnswer


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def contact_for(db: Session, user: User) -> ServiceContact | None:
    """The agent's own service first — the person who knows their procedures — then
    the general contact. An administrator reads everything, so gets the general one."""
    candidates = [] if sees_every_department(user) or not user.department else [user.department]
    candidates.append(TRANSVERSE)
    for department in candidates:
        contact = db.get(ServiceContact, department)
        if contact is not None and contact.name.strip():
            return contact
    return None


def referral_sentence(contact: ServiceContact | None) -> str:
    if contact is None:
        return ""
    reach = " · ".join(part for part in (contact.email.strip(), contact.phone.strip()) if part)
    who = f"**{contact.name.strip()}**"
    where = "" if contact.department == TRANSVERSE else f" ({department_label(contact.department)})"
    line = f"\n\nPour cette question, vous pouvez vous adresser à {who}{where}"
    line += f" — {reach}." if reach else "."
    if contact.note.strip():
        line += f" {contact.note.strip()}"
    return line


def contacts_visible_to(db: Session, user: User) -> list[ServiceContact]:
    """An agent sees their own service's contact and the general one; an
    administrator sees all. The directory of who handles what in another service is
    not the agent's to browse — the same reasoning as the profile, which is self-only."""
    rows = db.scalars(select(ServiceContact).order_by(ServiceContact.department)).all()
    if sees_every_department(user):
        return list(rows)
    allowed = {TRANSVERSE, user.department}
    return [row for row in rows if row.department in allowed]


# ---------------------------------------------------------------------------
# Validated answers
# ---------------------------------------------------------------------------

# Words that carry no meaning of their own in a French question. What remains is
# what the question is *about*, and two phrasings with the same remainder ask the
# same thing: « Quelle est la durée des congés annuels ? » and « Durée des congés
# annuels ? » both reduce to {duree, conges, annuels}.
STOPWORDS = frozenset("""
a ai as au aux avec c ca ce ces cet cette comment combien d dans de des du elle en est et
etre il ils j je l la le les leur lui m ma me mes moi mon n ne nos notre nous on ou par pas
pour qu quand que quel quelle quelles quels qui quoi s sa se ses si son sont sur t ta te tes
toi ton tu un une vos votre vous y svp stp plait merci bonjour salut
""".split())


def normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", without_accents).split())


def content_words(text: str) -> frozenset[str]:
    return frozenset(word for word in normalise(text).split() if word not in STOPWORDS and len(word) > 1)


def phrasings_of(answer: ValidatedAnswer) -> list[str]:
    try:
        extra = json.loads(answer.phrasings or "[]")
    except ValueError:
        extra = []
    return [answer.question, *[str(item) for item in extra if str(item).strip()]]


def can_read_perimeter(user: User, department: str) -> bool:
    """The document rule, applied to a validated answer: same service, transverse, or
    an administrator. A finance answer is not served to an HR agent."""
    return department == TRANSVERSE or sees_every_department(user) or department == user.department


def find_validated_answer(db: Session, user: User, question: str) -> ValidatedAnswer | None:
    """Matches when the question means the same words as a validated phrasing.

    Deliberately exact on meaning-bearing words rather than semantic: a validated
    answer is served *as is*, with a person's name under it, so a near miss would be
    a confident wrong answer signed by someone who never saw the question. An
    administrator who wants a phrasing covered adds it.
    """
    asked = content_words(question)
    if len(asked) < 2:
        # One meaningful word is not a question a validated answer can safely own.
        return None
    for answer in db.scalars(select(ValidatedAnswer).where(ValidatedAnswer.active.is_(True))).all():
        if not can_read_perimeter(user, answer.department):
            continue
        if any(content_words(phrasing) == asked for phrasing in phrasings_of(answer)):
            return answer
    return None


# ---------------------------------------------------------------------------
# Follow-up questions
# ---------------------------------------------------------------------------

# How a French follow-up opens. Matched on the start of the normalised question.
FOLLOW_UP_OPENINGS = (
    "et ", "et pour", "et si", "et en cas", "et dans", "et quand", "et combien", "et pour les",
    "pareil", "meme chose", "idem", "dans ce cas", "sinon", "et sinon", "aussi pour", "et le ",
    "et la ", "et les ", "et un ", "et une ", "et a ", "et au ", "et aux ", "et si je",
)
# Past this many meaning-bearing words, a question stands on its own even if it
# opens with « et ».
FOLLOW_UP_MAX_WORDS = 6


def is_follow_up(question: str) -> bool:
    text = normalise(question)
    if not text:
        return False
    opens_like_one = any(text == opening.strip() or text.startswith(opening) for opening in FOLLOW_UP_OPENINGS)
    return opens_like_one and len(content_words(question)) <= FOLLOW_UP_MAX_WORDS


def previous_turn(db: Session, conversation: Conversation) -> tuple[str | None, list[int], list[str]]:
    """The last question asked in this conversation and what its answer cited.

    Returns (question, document ids, filenames). Older answers stored sources without
    a document id; their filenames are returned so the caller can resolve them.
    """
    last_answer = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .where(ChatMessage.role == "assistant")
        .order_by(ChatMessage.id.desc())
        .limit(1)
    ).first()
    if last_answer is None:
        return None, [], []
    last_question = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .where(ChatMessage.role == "user")
        .where(ChatMessage.id < last_answer.id)
        .order_by(ChatMessage.id.desc())
        .limit(1)
    ).first()
    try:
        sources = json.loads(last_answer.sources or "[]")
    except ValueError:
        sources = []
    ids = [int(item["document_id"]) for item in sources if isinstance(item, dict) and item.get("document_id")]
    names = [str(item["filename"]) for item in sources
             if isinstance(item, dict) and not item.get("document_id") and item.get("filename")]
    return (last_question.content if last_question else None), ids, names
