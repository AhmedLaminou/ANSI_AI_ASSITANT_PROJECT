"""Acronym expansion before embedding, per department (phase D).

Administrative French runs on acronyms. An agent asks about « la DSI » while the
document spells out « direction des systèmes d'information » — the two embed to
different places and the right document is never retrieved.

Expanding the question (not the document) keeps the index untouched: the glossary
can change without re-indexing anything. The original wording is preserved and the
expansion appended, so a question that was already explicit is not harmed.

**Why the glossary is split by department.** The same acronym does not mean the same
thing in two services. In French public administration « CP » is *congés payés* to an
HR officer and *crédits de paiement* to an accountant; « BC » is a *bon de commande* in
logistics. A single flat glossary has to pick one, and picks wrong half the time. Each
service therefore reads the shared section plus its own, its own winning on collision.

An administrator reads every perimeter, so for them an ambiguous acronym expands to
**both** readings joined by « ou » — ambiguity should broaden the search, not resolve
itself silently in favour of one service.

The operator file `backend/data/glossary.json` (not versioned — it may itself reveal
internal organisation) may use either shape:

    {"ANSI": "Agence Nationale ..."}                      legacy, treated as shared
    {"commun": {...}, "rh": {...}, "finance": {...}}      per department

Both may be mixed: a string value goes to the shared section, a dictionary value is a
section. Its absence is normal, and a malformed file must never break ingestion.
"""

import json
import logging
import re
import unicodedata

from .access import DEPARTMENTS, sees_every_department
from .config import BACKEND_DIR
from .database import User


logger = logging.getLogger("ansi.assistant")

GLOSSARY_PATH = BACKEND_DIR / "data" / "glossary.json"

# The section every account reads, whatever its department.
COMMON = "commun"
SECTIONS: frozenset[str] = DEPARTMENTS | {COMMON}

# Deliberately small and generic. Real ANSI terminology belongs in glossary.json.
# The shared section holds what every agent meets regardless of service; anything a
# single service owns belongs to that service, so it cannot shadow another's meaning.
DEFAULT_GLOSSARY: dict[str, dict[str, str]] = {
    COMMON: {
        "ANSI": "Agence Nationale pour la Société de l'Information",
        "DSI": "direction des systèmes d'information",
        "SI": "système d'information",
        "RH": "ressources humaines",
        "DG": "direction générale",
        "PV": "procès-verbal",
        "RGPD": "règlement général sur la protection des données",
        "VPN": "réseau privé virtuel",
        "MFA": "authentification à double facteur",
        "2FA": "authentification à double facteur",
    },
    "technique": {
        "SSI": "sécurité des systèmes d'information",
        "CERT": "centre de réponse aux incidents de sécurité",
        "PCA": "plan de continuité d'activité",
        "PRA": "plan de reprise d'activité",
        "SLA": "niveau de service contractuel",
        "MCO": "maintien en condition opérationnelle",
        "CP": "chef de projet",
    },
    "finance": {
        # The collision that justifies the whole split: to an accountant, CP is a
        # payment credit; to an HR officer, paid leave.
        "CP": "crédits de paiement",
        "AE": "autorisation d'engagement",
        "TVA": "taxe sur la valeur ajoutée",
        "DAF": "direction administrative et financière",
        "BC": "bon de commande",
    },
    "logistique": {
        "BC": "bon de commande",
        "BL": "bon de livraison",
        "TDR": "termes de référence",
        "DAO": "dossier d'appel d'offres",
        "MAD": "mise à disposition",
    },
    "rh": {
        "CP": "congés payés",
        "SIRH": "système d'information des ressources humaines",
        "CDD": "contrat à durée déterminée",
        "CDI": "contrat à durée indéterminée",
        "DPAE": "déclaration préalable à l'embauche",
    },
}


def _read_custom_sections() -> dict[str, dict[str, str]]:
    """The operator file, normalised to the sectioned shape. Never raises."""
    if not GLOSSARY_PATH.exists():
        return {}
    try:
        raw = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        logger.warning("Glossaire illisible, valeurs par défaut utilisées : %s", GLOSSARY_PATH)
        return {}
    if not isinstance(raw, dict):
        return {}

    sections: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            name = str(key).strip().lower()
            if name not in SECTIONS:
                # Silent failure is the trap here: a section named "informatique"
                # would load and never be selected by anyone.
                logger.warning("Section de glossaire inconnue, ignorée : %r (attendu : %s)",
                               key, ", ".join(sorted(SECTIONS)))
                continue
            sections.setdefault(name, {}).update({str(k): str(v) for k, v in value.items()})
        else:
            # Legacy flat entry: shared by everyone, as it used to be.
            sections.setdefault(COMMON, {})[str(key)] = str(value)
    return sections


def _merge(into: dict[str, str], entries: dict[str, str], *, keep_both: bool) -> None:
    for acronym, expansion in entries.items():
        previous = into.get(acronym)
        if previous is None or not keep_both or previous == expansion:
            into[acronym] = expansion
        elif expansion not in previous.split(" ou "):
            into[acronym] = f"{previous} ou {expansion}"


def load_glossary(department: str | None = None, *, every_department: bool = False) -> dict[str, str]:
    """Shared section plus the department's own, the department winning on collision.

    With `every_department`, all sections are merged and a collision keeps both
    readings — the account reads every perimeter, so neither meaning can be ruled out.
    """
    sections: dict[str, dict[str, str]] = {name: dict(entries) for name, entries in DEFAULT_GLOSSARY.items()}
    for name, entries in _read_custom_sections().items():
        sections.setdefault(name, {}).update(entries)

    glossary: dict[str, str] = dict(sections.get(COMMON, {}))
    if every_department:
        for name in sorted(DEPARTMENTS):
            _merge(glossary, sections.get(name, {}), keep_both=True)
    elif department:
        _merge(glossary, sections.get(department, {}), keep_both=False)
    return glossary


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# Two-letter acronyms collide with ordinary French words — "SI" against "si",
# "RH" against nothing but "OK" against plenty. Below this length the acronym must
# actually be written in capitals to count; above it, any case is accepted.
CASE_SENSITIVE_BELOW = 3


def expand_acronyms(
    question: str,
    glossary: dict[str, str] | None = None,
    department: str | None = None,
    *,
    every_department: bool = False,
) -> str:
    """Appends the expansion of every acronym found, leaving the question intact."""
    entries = glossary if glossary is not None else load_glossary(department, every_department=every_department)
    if not entries:
        return question

    words = re.findall(r"[A-Za-zÀ-ÿ0-9]+", question)
    upper_words = {strip_accents(word).upper() for word in words}
    exact_words = {strip_accents(word) for word in words}
    already_said = strip_accents(question).lower()

    additions = []
    for acronym, expansion in entries.items():
        key = strip_accents(acronym)
        present = key in exact_words if len(key) < CASE_SENSITIVE_BELOW else key.upper() in upper_words
        if present and strip_accents(expansion).lower() not in already_said:
            additions.append(expansion)
    return f"{question} ({', '.join(additions)})" if additions else question


def expand_for(user: User, question: str) -> str:
    """The one place that maps an account to the glossary it reads.

    Same principle as `access.can_access_document`: derived from the database row, in a
    single function, so a caller cannot get the mapping subtly wrong.
    """
    return expand_acronyms(
        question,
        department=user.department,
        every_department=sees_every_department(user),
    )
