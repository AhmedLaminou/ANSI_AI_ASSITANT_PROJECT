"""The single definition of who may read what.

This rule lived in two places — `main.py` and `tools.py` — each reimplementing it.
That is precisely how a perimeter leaks: one is updated, the other is forgotten.
Every caller now goes through `can_access_document()`.

Two independent axes (see docs/FUTURE_IDEAS.md §3):

- **role** — what you may do: read, manage documents, administer.
- **department** — which perimeter you belong to.

Access requires *both*. The department **restricts, it never widens**: belonging to
HR grants nothing over an HR document whose allowed roles exclude you.
"""

from .database import DocumentRecord, User
from .rag import parse_allowed_roles


# The four ANSI departments, plus the perimeter for documents that concern everyone
# (règlement intérieur, charte informatique, onboarding material).
DEPARTMENTS: frozenset[str] = frozenset({"technique", "finance", "logistique", "rh"})
TRANSVERSE = "transverse"
DOCUMENT_DEPARTMENTS: frozenset[str] = DEPARTMENTS | {TRANSVERSE}

DEPARTMENT_LABELS: dict[str, str] = {
    "technique": "Technique / informatique",
    "finance": "Finance / comptabilité",
    "logistique": "Logistique",
    "rh": "Ressources humaines",
    TRANSVERSE: "Transverse",
}


def department_label(department: str | None) -> str:
    return DEPARTMENT_LABELS.get(department or "", "Non rattaché")


def sees_every_department(user: User) -> bool:
    """The central administrator manages the whole corpus, so reads across perimeters.

    This is a deliberate exception and the only one. It is what makes a single
    administrator able to approve requests and curate documents for every department.
    If ANSI decides administrators must also be confined to a perimeter, this is the
    one function to change.
    """
    return user.role == "admin"


def can_access_document(user: User, document: DocumentRecord) -> bool:
    if user.role not in parse_allowed_roles(document.allowed_roles):
        return False
    if document.department == TRANSVERSE or sees_every_department(user):
        return True
    return document.department == user.department
