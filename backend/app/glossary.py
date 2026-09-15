"""Acronym expansion before embedding.

Administrative French runs on acronyms. An agent asks about « la DSI » while the
document spells out « direction des systèmes d'information » — the two embed to
different places and the right document is never retrieved.

Expanding the question (not the document) keeps the index untouched: the glossary
can change without re-indexing anything. The original wording is preserved and the
expansion appended, so a question that was already explicit is not harmed.

The glossary lives in `backend/data/glossary.json` when present, so an operator can
extend it without touching the code. Its absence is normal.
"""

import json
import re
import unicodedata

from .config import BACKEND_DIR


GLOSSARY_PATH = BACKEND_DIR / "data" / "glossary.json"

# Deliberately small and generic. Real ANSI terminology belongs in glossary.json,
# which is not versioned: it may itself reveal internal organisation.
DEFAULT_GLOSSARY: dict[str, str] = {
    "ANSI": "Agence Nationale pour la Sécurité des Systèmes d'Information",
    "DSI": "direction des systèmes d'information",
    "RH": "ressources humaines",
    "SI": "système d'information",
    "CERT": "centre de réponse aux incidents de sécurité",
    "PCA": "plan de continuité d'activité",
    "PRA": "plan de reprise d'activité",
    "RGPD": "règlement général sur la protection des données",
    "SSI": "sécurité des systèmes d'information",
    "VPN": "réseau privé virtuel",
    "MFA": "authentification à double facteur",
    "2FA": "authentification à double facteur",
}


def load_glossary() -> dict[str, str]:
    glossary = dict(DEFAULT_GLOSSARY)
    if GLOSSARY_PATH.exists():
        try:
            custom = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return glossary  # a malformed glossary must not break ingestion
        if isinstance(custom, dict):
            glossary.update({str(k): str(v) for k, v in custom.items()})
    return glossary


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


# Two-letter acronyms collide with ordinary French words — "SI" against "si",
# "RH" against nothing but "OK" against plenty. Below this length the acronym must
# actually be written in capitals to count; above it, any case is accepted.
CASE_SENSITIVE_BELOW = 3


def expand_acronyms(question: str, glossary: dict[str, str] | None = None) -> str:
    """Appends the expansion of every acronym found, leaving the question intact."""
    entries = glossary if glossary is not None else load_glossary()
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
