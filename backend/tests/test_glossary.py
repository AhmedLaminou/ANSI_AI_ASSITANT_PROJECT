r"""Per-department glossaries (phase D).

The point of splitting the glossary is that the same acronym means different things in
different services. « CP » is *congés payés* to an HR officer, *crédits de paiement* to
an accountant and *chef de projet* in the technical service. A flat glossary has to pick
one, and picks wrong for two services out of three.

These tests hold that behaviour, and the operator-file contract around it. No database
and no Ollama.

Run with:  .\.venv\Scripts\python.exe -m pytest tests/test_glossary.py -q
"""

import json

import pytest

from app import glossary as glossary_module
from app.access import DEPARTMENTS
from app.database import User
from app.glossary import (
    COMMON,
    DEFAULT_GLOSSARY,
    SECTIONS,
    expand_acronyms,
    expand_for,
    load_glossary,
)


def make_user(role: str = "user", department: str | None = "rh") -> User:
    return User(username="x", password_hash="x", role=role, department=department)


@pytest.fixture
def operator_file(tmp_path, monkeypatch):
    """Points the module at a throwaway glossary file and returns a writer for it."""
    path = tmp_path / "glossary.json"
    monkeypatch.setattr(glossary_module, "GLOSSARY_PATH", path)

    def write(payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    return write


# --------------------------------------------------------------------------
# The collision that justifies the split
# --------------------------------------------------------------------------

def test_cp_means_something_different_in_each_service():
    assert load_glossary("rh")["CP"] == "congés payés"
    assert load_glossary("finance")["CP"] == "crédits de paiement"
    assert load_glossary("technique")["CP"] == "chef de projet"


def test_the_expansion_follows_the_service():
    question = "Combien de jours de CP reste-t-il ?"
    assert "congés payés" in expand_acronyms(question, department="rh")
    assert "crédits de paiement" in expand_acronyms(question, department="finance")
    assert "congés payés" not in expand_acronyms(question, department="finance")


def test_a_service_does_not_read_another_services_acronyms():
    """SIRH belongs to HR; an accountant cannot read HR documents, so expanding it there
    would only drag the search towards documents the account will never be shown."""
    assert "SIRH" in load_glossary("rh")
    assert "SIRH" not in load_glossary("finance")
    assert "AE" in load_glossary("finance")
    assert "AE" not in load_glossary("rh")


# --------------------------------------------------------------------------
# The shared section reaches everyone
# --------------------------------------------------------------------------

@pytest.mark.parametrize("department", sorted(DEPARTMENTS | {None}, key=str))
def test_the_shared_section_is_read_by_every_service(department):
    assert load_glossary(department)["ANSI"] == "Agence Nationale pour la Société de l'Information"


def test_no_department_is_missing_from_the_defaults():
    assert DEPARTMENTS <= set(DEFAULT_GLOSSARY)
    assert COMMON in DEFAULT_GLOSSARY


def test_an_account_without_a_service_reads_the_shared_section_only():
    entries = load_glossary(None)
    assert "ANSI" in entries
    assert "CP" not in entries


# --------------------------------------------------------------------------
# An administrator reads every perimeter, so ambiguity broadens
# --------------------------------------------------------------------------

def test_an_administrator_keeps_every_reading_of_an_ambiguous_acronym():
    both = load_glossary(every_department=True)["CP"]
    for reading in ("congés payés", "crédits de paiement", "chef de projet"):
        assert reading in both


def test_an_administrator_does_not_repeat_an_identical_reading():
    """BC is a bon de commande in both logistics and finance: said once, not twice."""
    assert load_glossary(every_department=True)["BC"] == "bon de commande"


def test_expand_for_derives_the_glossary_from_the_account():
    question = "Quel est le solde de CP ?"
    assert "congés payés" in expand_for(make_user(department="rh"), question)
    assert "crédits de paiement" in expand_for(make_user(department="finance"), question)

    administrator = expand_for(make_user(role="admin", department="technique"), question)
    assert "congés payés" in administrator and "crédits de paiement" in administrator


# --------------------------------------------------------------------------
# The operator file
# --------------------------------------------------------------------------

def test_a_sectioned_operator_file_is_merged_per_service(operator_file):
    operator_file({"rh": {"ANCV": "agence nationale des chèques vacances"},
                   "commun": {"ODD": "objectifs de développement durable"}})
    assert load_glossary("rh")["ANCV"] == "agence nationale des chèques vacances"
    assert "ANCV" not in load_glossary("finance")
    assert load_glossary("finance")["ODD"] == "objectifs de développement durable"


def test_a_legacy_flat_file_still_loads_as_shared(operator_file):
    """The file used to be a flat mapping. An existing one must keep working."""
    operator_file({"PNUD": "programme des Nations unies pour le développement"})
    for department in sorted(DEPARTMENTS):
        assert load_glossary(department)["PNUD"] == "programme des Nations unies pour le développement"


def test_the_two_shapes_may_be_mixed(operator_file):
    operator_file({"PNUD": "programme des Nations unies", "rh": {"ANCV": "chèques vacances"}})
    assert "PNUD" in load_glossary("finance")
    assert "ANCV" in load_glossary("rh")


def test_the_operator_file_overrides_a_default(operator_file):
    operator_file({"rh": {"CP": "congé principal"}})
    assert load_glossary("rh")["CP"] == "congé principal"


def test_an_unknown_section_is_ignored_and_reported(operator_file, caplog):
    """Silent failure is the trap: a section named 'informatique' would load and never
    be selected by anyone."""
    operator_file({"informatique": {"XYZ": "quelque chose"}})
    with caplog.at_level("WARNING"):
        entries = load_glossary("technique")
    assert "XYZ" not in entries
    assert any("informatique" in record.getMessage() for record in caplog.records)


def test_a_section_name_is_accepted_whatever_its_case(operator_file):
    operator_file({"RH": {"ANCV": "chèques vacances"}})
    assert "ANCV" in load_glossary("rh")


def test_a_malformed_file_falls_back_to_the_defaults(operator_file, tmp_path):
    glossary_module.GLOSSARY_PATH.write_text("{ ceci n'est pas du JSON", encoding="utf-8")
    assert load_glossary("rh")["CP"] == "congés payés"


def test_a_file_that_is_not_a_mapping_is_ignored(operator_file):
    operator_file(["ANSI", "DSI"])
    assert load_glossary("rh")["ANSI"].startswith("Agence Nationale")


def test_every_section_name_is_a_real_department_or_the_shared_one():
    assert SECTIONS == DEPARTMENTS | {COMMON}


# --------------------------------------------------------------------------
# The two-letter trap still holds, now per department
# --------------------------------------------------------------------------

def test_a_two_letter_acronym_still_does_not_fire_on_a_french_word():
    """'SI' must not fire on 'ainsi' or 'si', in any service."""
    question = "Ainsi, que faut-il faire si le poste est perdu ?"
    for department in sorted(DEPARTMENTS):
        assert expand_acronyms(question, department=department) == question


def test_ae_does_not_fire_on_lowercase_text():
    question = "Faut-il ae rediger la note ?"
    assert expand_acronyms(question, department="finance") == question
