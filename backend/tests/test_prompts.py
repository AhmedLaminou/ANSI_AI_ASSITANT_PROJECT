r"""Per-department prompts: the specialisation must add, never weaken.

A department block exists to make an answer better for that service — quote figures
exactly, keep a procedure's step order, answer on the rule rather than on a person.
It must not touch what the account may read: that is decided in `access.py`, from the
database, before the model sees anything.

These tests are the guard that a later edit cannot quietly break that separation.
No database and no Ollama.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_prompts.py -q
"""

import pytest

from app.access import DEPARTMENTS, TRANSVERSE
from app.database import User
from app.prompts import (
    CROSS_DEPARTMENT,
    INVARIANT_HEAD,
    INVARIANT_TAIL,
    SPECIALISATIONS,
    UNASSIGNED,
    assistant_description,
    specialisation_for,
    system_message,
)


def make_user(role: str = "user", department: str | None = "rh") -> User:
    return User(username="x", password_hash="x", role=role, department=department)


ALL_BLOCKS = [*SPECIALISATIONS.values(), CROSS_DEPARTMENT, UNASSIGNED]


# --------------------------------------------------------------------------
# Every department is covered
# --------------------------------------------------------------------------

def test_every_department_has_its_own_block():
    """A department added to access.py without a prompt would silently fall back."""
    assert DEPARTMENTS <= set(SPECIALISATIONS)


def test_transverse_is_covered_too():
    assert TRANSVERSE in SPECIALISATIONS


def test_the_blocks_are_all_distinct():
    assert len(set(SPECIALISATIONS.values())) == len(SPECIALISATIONS)


# --------------------------------------------------------------------------
# The invariants survive composition — this is the point of the module
# --------------------------------------------------------------------------

@pytest.mark.parametrize("department", sorted(DEPARTMENTS | {TRANSVERSE, None}, key=str))
def test_both_invariants_are_present_for_every_department(department):
    message = system_message(make_user(department=department))
    assert INVARIANT_HEAD in message
    assert INVARIANT_TAIL in message


def test_invariants_are_present_for_an_administrator():
    message = system_message(make_user(role="admin", department="technique"))
    assert INVARIANT_HEAD in message
    assert INVARIANT_TAIL in message


def test_the_invariants_bracket_the_specialisation():
    """Read first and read last: the rules are not buried in the middle."""
    user = make_user(department="finance")
    message = system_message(user)
    assert message.index(INVARIANT_HEAD) < message.index(SPECIALISATIONS["finance"])
    assert message.index(SPECIALISATIONS["finance"]) < message.index(INVARIANT_TAIL)


def test_the_untrusted_extract_rule_is_in_the_invariant_not_the_department():
    """Moving it into a department block would make it droppable one service at a time."""
    assert "non fiables" in INVARIANT_HEAD
    for block in ALL_BLOCKS:
        assert "non fiables" not in block


# --------------------------------------------------------------------------
# A department block never grants anything
# --------------------------------------------------------------------------

# Unambiguous granting phrases. The realistic future mistake is not malice, it is
# someone writing "tu peux consulter tous les documents" into a department block
# while trying to make one answer more helpful.
GRANTING_PHRASES = [
    "tu peux consulter",
    "tu as acc\u00e8s",
    "autoris\u00e9 \u00e0 lire",
    "tous les documents",
    "sans restriction",
    "ignore",
]


@pytest.mark.parametrize("block", ALL_BLOCKS)
def test_no_block_contains_granting_language(block):
    lowered = block.lower()
    for phrase in GRANTING_PHRASES:
        assert phrase not in lowered, f"formulation d'octroi dans un bloc de service : {phrase}"


# --------------------------------------------------------------------------
# No cross-contamination between services
# --------------------------------------------------------------------------

@pytest.mark.parametrize("department", sorted(DEPARTMENTS))
def test_a_department_receives_only_its_own_block(department):
    message = system_message(make_user(department=department))
    assert SPECIALISATIONS[department] in message
    for other, block in SPECIALISATIONS.items():
        if other != department:
            assert block not in message


def test_selection_follows_the_database_field():
    assert specialisation_for(make_user(department="rh")) == SPECIALISATIONS["rh"]
    assert specialisation_for(make_user(department="finance")) == SPECIALISATIONS["finance"]


def test_an_unknown_department_falls_back_to_the_unassigned_block():
    """Defensive: a value written directly into the database must not select nothing."""
    assert specialisation_for(make_user(department="direction-generale")) == UNASSIGNED


# --------------------------------------------------------------------------
# The administrator exception is the same one access.py documents
# --------------------------------------------------------------------------

def test_an_administrator_gets_the_cross_department_block_not_their_own():
    user = make_user(role="admin", department="technique")
    assert specialisation_for(user) == CROSS_DEPARTMENT
    assert SPECIALISATIONS["technique"] not in system_message(user)


def test_an_administrator_is_told_to_attribute_each_source_to_its_service():
    assert "service" in CROSS_DEPARTMENT and "contredisent" in CROSS_DEPARTMENT


# --------------------------------------------------------------------------
# A pending account
# --------------------------------------------------------------------------

def test_a_pending_account_gets_the_unassigned_block():
    """No department yet: it reads transverse documents only, and is not told otherwise."""
    assert specialisation_for(make_user(department=None)) == UNASSIGNED


def test_the_unassigned_block_does_not_hint_at_other_documents():
    assert "transverse" in UNASSIGNED.lower()


# --------------------------------------------------------------------------
# How the assistant introduces itself
# --------------------------------------------------------------------------

def test_the_greeting_names_the_service():
    assert "ressources humaines" in assistant_description(make_user(department="rh"))
    assert "finance" in assistant_description(make_user(department="finance")).lower()


def test_the_greeting_for_an_administrator_says_every_service():
    assert "ensemble des services" in assistant_description(make_user(role="admin", department="rh"))


def test_the_greeting_for_an_unassigned_account_names_no_service():
    description = assistant_description(make_user(department=None))
    assert "service" not in description
