"""Unanswered questions: recognised, recorded, grouped into gaps.

Every refusal is either a missing document or a vocabulary gap. Left in the audit
trail as a bare event, it says nothing; recorded with its question and grouped with
similar ones, it says « fourteen people asked about part-time telework and the corpus
has nothing on it » — the single most actionable fact this system produces.

**Most refusals come from the model, not from the graph.** The similarity threshold
separates almost nothing (ARCHITECTURE_TECHNIQUE §5.1): extracts are nearly always
found and handed to the model, which then says the information is not there. So
detection has to read the *answer*, not only the graph's outcome — recording graph
refusals alone would capture almost none of them.

`looks_like_refusal` used to live only in the evaluation harness. It is the same
judgement in both places, so it has one definition, here.
"""

import re
import unicodedata
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import UnansweredQuestion, User
from .rag import cosine_similarity


# Phrases by which the assistant — or the model under its instructions — says the
# answer is not in the sources. Matched after lowercasing and accent removal.
REFUSAL_HINTS = (
    "ne trouve pas",
    "pas présente",
    "pas presente",
    "pas disponible",
    "aucun document",
    "pas d'information",
    "ne contiennent pas",
    "ne permettent pas",
    "ne figure pas",
    "n'est pas mentionné",
    "n'est pas mentionne",
    "n'apparait pas",
    "aucune information",
)

REASONS = {
    "no_documents": "aucun document accessible",
    "no_match": "rien d'assez proche dans le corpus",
    "model_refusal": "extraits jugés insuffisants par le modèle",
}


def normalise(text: str) -> str:
    stripped = unicodedata.normalize("NFD", text.lower())
    return "".join(character for character in stripped if unicodedata.category(character) != "Mn")


def looks_like_refusal(answer: str) -> bool:
    lowered = normalise(answer)
    return any(normalise(hint) in lowered for hint in REFUSAL_HINTS)


def record_unanswered(
    db: Session,
    user: User,
    question: str,
    reason: str,
    embedding: list[float] | None,
) -> None:
    """Called after the answer is decided; commits on its own.

    The department is copied now rather than joined later, so a transfer does not
    rewrite what a service was asking. An administrator's questions are kept too:
    they read every perimeter, so what *they* cannot find is missing for everyone.
    """
    db.add(UnansweredQuestion(
        user_id=user.id,
        department=user.department,
        question=question.strip()[:2000],
        reason=reason if reason in REASONS else "model_refusal",
        embedding=embedding,
    ))
    db.commit()


def cluster_gaps(rows: list[UnansweredQuestion], threshold: float | None = None) -> list[list[UnansweredQuestion]]:
    """Groups questions that ask the same thing, most-asked first.

    Greedy assignment to the closest running centroid. The threshold was *measured*
    on embeddinggemma rather than guessed (28/09/2026, 17 questions, 5 topics):

        0.45 -> 6 groups, 1 mixing two topics
        0.50 -> 7 groups, none mixed          <- default
        0.55 -> 9 groups, none mixed, more questions left alone
        0.80 -> nothing groups at all

    Paraphrases of one question score 0.31-0.65 against each other; unrelated
    questions reach 0.50. The margin is narrow, and the sample small: re-measure on
    real questions once there are some, and adjust GAP_SIMILARITY_THRESHOLD.

    Questions without an embedding (the model was down when they were asked) are
    grouped by identical normalised text only.
    """
    limit = threshold if threshold is not None else get_settings().gap_similarity_threshold
    groups: list[tuple[list[float] | None, list[UnansweredQuestion]]] = []
    by_text: dict[str, list[UnansweredQuestion]] = {}

    for row in sorted(rows, key=lambda item: item.created_at or datetime.min.replace(tzinfo=timezone.utc)):
        if not row.embedding:
            # Punctuation and spacing are noise: « Wifi invités ? » and « wifi invites »
            # are one question.
            key = " ".join(re.sub(r"[^a-z0-9]+", " ", normalise(row.question)).split())
            by_text.setdefault(key, []).append(row)
            continue
        best, best_score = None, -1.0
        for index, (centroid, _) in enumerate(groups):
            score = cosine_similarity(row.embedding, centroid)
            if score > best_score:
                best, best_score = index, score
        if best is not None and best_score >= limit:
            centroid, members = groups[best]
            members.append(row)
            count = len(members)
            groups[best] = ([(c * (count - 1) + v) / count for c, v in zip(centroid, row.embedding)], members)
        else:
            groups.append((list(row.embedding), [row]))

    clusters = [members for _, members in groups] + list(by_text.values())
    return sorted(clusters, key=lambda members: (-len(members), members[0].id))
