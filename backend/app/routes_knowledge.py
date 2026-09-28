"""Endpoints for what the corpus lacks, who to ask, and what was validated.

A router of its own rather than more lines in main.py, which had passed 1 800. It
depends only on the database, the auth dependencies and the knowledge modules —
never on main — so there is no import cycle to manage.
"""

import json
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .access import DOCUMENT_DEPARTMENTS, department_label
from .auth import get_current_user, require_roles
from .database import AuditEvent, ServiceContact, UnansweredQuestion, User, ValidatedAnswer, get_db
from .knowledge import contacts_visible_to, content_words, phrasings_of
from .refusals import REASONS, cluster_gaps


router = APIRouter()


# ---------------------------------------------------------------------------
# Gaps: unanswered questions, grouped
# ---------------------------------------------------------------------------

class ResolveGapsRequest(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=500)


@router.get("/admin/gaps")
def list_gaps(
    include_resolved: bool = False,
    _: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """What people asked and the corpus could not answer, most-asked first.

    Each group is one gap: a missing document, or a vocabulary the corpus does not
    use. The count is what makes it actionable — one question is anecdote, fourteen
    is a document to write.
    """
    query = select(UnansweredQuestion).order_by(UnansweredQuestion.created_at.desc()).limit(1000)
    if not include_resolved:
        query = query.where(UnansweredQuestion.resolved.is_(False))
    rows = db.scalars(query).all()
    clusters = cluster_gaps(list(rows))

    def when(row: UnansweredQuestion) -> str:
        moment = row.created_at
        return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).isoformat()

    return {
        "total_questions": len(rows),
        "gaps": [
            {
                # The most recent phrasing is the one an administrator recognises.
                "representative": max(members, key=lambda r: r.id).question,
                "count": len(members),
                "questions": [row.question for row in sorted(members, key=lambda r: -r.id)[:8]],
                "departments": {
                    department_label(name): count
                    for name, count in Counter(row.department for row in members).most_common()
                },
                "reasons": {
                    REASONS.get(name, name): count
                    for name, count in Counter(row.reason for row in members).most_common()
                },
                "first_seen": min(when(row) for row in members),
                "last_seen": max(when(row) for row in members),
                "ids": [row.id for row in members],
            }
            for members in clusters
        ],
    }


@router.post("/admin/gaps/resolve", status_code=status.HTTP_204_NO_CONTENT)
def resolve_gaps(
    payload: ResolveGapsRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> None:
    """Once a document covers the gap. Resolved questions stop appearing but are kept:
    if the same questions keep arriving afterwards, the new document did not help."""
    rows = db.scalars(select(UnansweredQuestion).where(UnansweredQuestion.id.in_(payload.ids))).all()
    for row in rows:
        row.resolved = True
    db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="gap_resolved"))
    db.commit()


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

class ContactRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: str = Field(default="", max_length=160)
    phone: str = Field(default="", max_length=40)
    note: str = Field(default="", max_length=500)


def contact_summary(contact: ServiceContact) -> dict[str, object]:
    return {
        "department": contact.department,
        "department_label": department_label(contact.department),
        "name": contact.name,
        "email": contact.email,
        "phone": contact.phone,
        "note": contact.note,
    }


@router.get("/contacts")
def my_contacts(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, object]]:
    """Who to ask, for the agent's own perimeter: their service and the general contact."""
    return [contact_summary(contact) for contact in contacts_visible_to(db, user)]


@router.get("/admin/contacts")
def all_contacts(_: User = Depends(require_roles("admin")), db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = {contact.department: contact for contact in db.scalars(select(ServiceContact)).all()}
    # Every perimeter is listed, filled or not: an empty row is what prompts filling it.
    return [
        contact_summary(rows[name]) if name in rows else {
            "department": name, "department_label": department_label(name),
            "name": "", "email": "", "phone": "", "note": "",
        }
        for name in sorted(DOCUMENT_DEPARTMENTS)
    ]


@router.put("/admin/contacts/{department}")
def set_contact(
    department: str,
    payload: ContactRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if department not in DOCUMENT_DEPARTMENTS:
        raise HTTPException(status_code=422, detail="Service invalide.")
    contact = db.get(ServiceContact, department) or ServiceContact(department=department)
    contact.name = payload.name.strip()
    contact.email = payload.email.strip()
    contact.phone = payload.phone.strip()
    contact.note = payload.note.strip()
    contact.updated_at = datetime.now(timezone.utc)
    db.add(contact)
    db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="contact_updated"))
    db.commit()
    return contact_summary(contact)


@router.delete("/admin/contacts/{department}", status_code=status.HTTP_204_NO_CONTENT)
def clear_contact(
    department: str,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> None:
    contact = db.get(ServiceContact, department)
    if contact is not None:
        db.delete(contact)
        db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="contact_updated"))
        db.commit()


# ---------------------------------------------------------------------------
# Validated answers
# ---------------------------------------------------------------------------

class ValidatedAnswerRequest(BaseModel):
    question: str = Field(min_length=5, max_length=500)
    answer: str = Field(min_length=5, max_length=8000)
    phrasings: list[str] = Field(default_factory=list, max_length=30)
    department: str = "transverse"


class UpdateValidatedAnswerRequest(BaseModel):
    question: str | None = Field(default=None, min_length=5, max_length=500)
    answer: str | None = Field(default=None, min_length=5, max_length=8000)
    phrasings: list[str] | None = Field(default=None, max_length=30)
    department: str | None = None
    active: bool | None = None


def validated_summary(answer: ValidatedAnswer, names: dict[int, str]) -> dict[str, object]:
    moment = answer.validated_at
    return {
        "id": answer.id,
        "question": answer.question,
        "phrasings": phrasings_of(answer)[1:],
        "answer": answer.answer,
        "department": answer.department,
        "department_label": department_label(answer.department),
        "validated_by": names.get(answer.validated_by, "compte supprimé"),
        "validated_at": (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).isoformat(),
        "active": answer.active,
        "times_served": answer.times_served,
    }


def check_phrasings(question: str, phrasings: list[str]) -> list[str]:
    cleaned = [p.strip() for p in phrasings if p and p.strip()]
    for candidate in [question, *cleaned]:
        if len(content_words(candidate)) < 2:
            raise HTTPException(
                status_code=422,
                detail=f"« {candidate} » est trop vague : une formulation doit contenir au moins deux mots porteurs "
                       "de sens, sinon la réponse validée serait servie à des questions qu'elle ne couvre pas.",
            )
    return cleaned


@router.get("/admin/validated-answers")
def list_validated(_: User = Depends(require_roles("admin")), db: Session = Depends(get_db)) -> list[dict[str, object]]:
    rows = db.scalars(select(ValidatedAnswer).order_by(ValidatedAnswer.times_served.desc(), ValidatedAnswer.id)).all()
    names = {u.id: u.username for u in db.scalars(select(User).where(User.id.in_({r.validated_by for r in rows}))).all()} if rows else {}
    return [validated_summary(row, names) for row in rows]


@router.post("/admin/validated-answers", status_code=status.HTTP_201_CREATED)
def create_validated(
    payload: ValidatedAnswerRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if payload.department not in DOCUMENT_DEPARTMENTS:
        raise HTTPException(status_code=422, detail="Service invalide.")
    phrasings = check_phrasings(payload.question, payload.phrasings)
    answer = ValidatedAnswer(
        question=payload.question.strip(),
        phrasings=json.dumps(phrasings, ensure_ascii=False),
        answer=payload.answer.strip(),
        department=payload.department,
        validated_by=admin.id,
    )
    db.add(answer)
    db.flush()
    db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="validated_answer_created"))
    db.commit()
    db.refresh(answer)
    return validated_summary(answer, {admin.id: admin.username})


@router.patch("/admin/validated-answers/{answer_id}")
def update_validated(
    answer_id: int,
    payload: UpdateValidatedAnswerRequest,
    admin: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    """Editing the text re-signs it: the validator becomes whoever changed it last,
    because that is the person who vouches for what is now served."""
    answer = db.get(ValidatedAnswer, answer_id)
    if answer is None:
        raise HTTPException(status_code=404, detail="Réponse validée introuvable.")
    if payload.department is not None:
        if payload.department not in DOCUMENT_DEPARTMENTS:
            raise HTTPException(status_code=422, detail="Service invalide.")
        answer.department = payload.department
    question = payload.question.strip() if payload.question is not None else answer.question
    if payload.phrasings is not None or payload.question is not None:
        phrasings = payload.phrasings if payload.phrasings is not None else phrasings_of(answer)[1:]
        answer.phrasings = json.dumps(check_phrasings(question, phrasings), ensure_ascii=False)
        answer.question = question
    content_changed = payload.answer is not None and payload.answer.strip() != answer.answer
    if payload.answer is not None:
        answer.answer = payload.answer.strip()
    if content_changed or payload.question is not None or payload.phrasings is not None:
        answer.validated_by = admin.id
        answer.validated_at = datetime.now(timezone.utc)
    if payload.active is not None:
        answer.active = payload.active
    db.add(AuditEvent(actor_id=admin.id, document_id=None, event_type="validated_answer_updated"))
    db.commit()
    db.refresh(answer)
    names = {u.id: u.username for u in db.scalars(select(User).where(User.id == answer.validated_by)).all()}
    return validated_summary(answer, names)
