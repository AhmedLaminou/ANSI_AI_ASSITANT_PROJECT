"""Perimeter tests: role and department, and the boundary between departments.

These are the tests that decide whether "an HR user cannot see Finance documents"
is true or merely intended. They use no database and no Ollama.

Run with:  .\\.venv\\Scripts\\python.exe -m pytest tests/test_access.py -q
"""

import itertools

import pytest

from app.access import (
    DEPARTMENTS,
    DOCUMENT_DEPARTMENTS,
    TRANSVERSE,
    can_access_document,
    department_label,
    sees_every_department,
)
from app.database import DocumentRecord, User


ALL_ROLES = "admin,document_manager,user"


def make_user(role: str = "user", department: str | None = "rh") -> User:
    return User(username="x", password_hash="x", role=role, department=department)


def make_document(department: str = "rh", allowed: str = ALL_ROLES) -> DocumentRecord:
    return DocumentRecord(
        title="Doc",
        original_filename="doc.txt",
        stored_filename="stored.txt",
        content_type="text/plain",
        classification="interne",
        allowed_roles=allowed,
        created_by=1,
        department=department,
    )


# --------------------------------------------------------------------------
# The boundary between departments
# --------------------------------------------------------------------------

@pytest.mark.parametrize(("mine", "other"), [
    pair for pair in itertools.permutations(sorted(DEPARTMENTS), 2)
])
def test_no_department_can_read_another(mine, other):
    """Every ordered pair of departments, both directions. The core guarantee."""
    user = make_user(role="user", department=mine)
    assert can_access_document(user, make_document(department=other)) is False


@pytest.mark.parametrize("department", sorted(DEPARTMENTS))
def test_a_department_reads_its_own(department):
    user = make_user(role="user", department=department)
    assert can_access_document(user, make_document(department=department)) is True


@pytest.mark.parametrize("department", sorted(DEPARTMENTS))
def test_every_department_reads_transverse(department):
    user = make_user(role="user", department=department)
    assert can_access_document(user, make_document(department=TRANSVERSE)) is True


@pytest.mark.parametrize("department", sorted(DEPARTMENTS))
def test_document_manager_is_still_confined_to_its_department(department):
    """A higher role does not cross the perimeter — only the administrator does."""
    other = next(item for item in sorted(DEPARTMENTS) if item != department)
    user = make_user(role="document_manager", department=department)
    assert can_access_document(user, make_document(department=other)) is False


# --------------------------------------------------------------------------
# Department restricts, it never widens
# --------------------------------------------------------------------------

def test_department_does_not_grant_what_the_role_refuses():
    """The decisive property: matching departments cannot bypass the role check."""
    user = make_user(role="user", department="rh")
    document = make_document(department="rh", allowed="admin,document_manager")
    assert can_access_document(user, document) is False


def test_transverse_does_not_grant_what_the_role_refuses():
    user = make_user(role="user", department="rh")
    assert can_access_document(user, make_document(department=TRANSVERSE, allowed="admin")) is False


def test_admin_crossing_departments_still_obeys_allowed_roles():
    """Even the administrator exception stops at the role list."""
    admin = make_user(role="admin", department="technique")
    document = make_document(department="finance", allowed="document_manager,user")
    assert can_access_document(admin, document) is False


# --------------------------------------------------------------------------
# The administrator exception, deliberate and singular
# --------------------------------------------------------------------------

@pytest.mark.parametrize("department", sorted(DEPARTMENTS))
def test_admin_reads_every_department(department):
    admin = make_user(role="admin", department="technique")
    assert can_access_document(admin, make_document(department=department)) is True


def test_only_admin_crosses_perimeters():
    assert sees_every_department(make_user(role="admin")) is True
    assert sees_every_department(make_user(role="document_manager")) is False
    assert sees_every_department(make_user(role="user")) is False


# --------------------------------------------------------------------------
# Accounts without a perimeter (pending, or not yet assigned)
# --------------------------------------------------------------------------

@pytest.mark.parametrize("department", sorted(DEPARTMENTS))
def test_user_without_department_reads_no_department(department):
    """A pending account has no perimeter and must read nothing of a department."""
    user = make_user(role="user", department=None)
    assert can_access_document(user, make_document(department=department)) is False


def test_user_without_department_still_reads_transverse():
    """Deliberate: onboarding material is meant to be reachable before assignment."""
    user = make_user(role="user", department=None)
    assert can_access_document(user, make_document(department=TRANSVERSE)) is True


# --------------------------------------------------------------------------
# Configuration sanity
# --------------------------------------------------------------------------

def test_transverse_is_not_a_real_department():
    assert TRANSVERSE not in DEPARTMENTS
    assert TRANSVERSE in DOCUMENT_DEPARTMENTS


def test_four_departments_are_declared():
    assert DEPARTMENTS == frozenset({"technique", "finance", "logistique", "rh"})


def test_every_department_has_a_human_label():
    for department in DOCUMENT_DEPARTMENTS:
        assert department_label(department) != "Non rattaché"
    assert department_label(None) == "Non rattaché"
