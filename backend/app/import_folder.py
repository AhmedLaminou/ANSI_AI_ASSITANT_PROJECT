r"""Import a whole folder of documents, from the machine hosting the application.

    .\.venv\Scripts\python.exe -m app.import_folder D:\corpus\rh --service rh --as admin@ansi.ne
    .\.venv\Scripts\python.exe -m app.import_folder D:\corpus\rh --service rh --as admin@ansi.ne --dry-run

This is how an agency's existing corpus enters the assistant. The web form handles
one document, the batch endpoint fifty; the first real load at ANSI will be
hundreds of files sitting on a shared drive, and a browser is the wrong tool for it.

It goes through exactly the same ingestion function as the web interface
(`app.ingestion.ingest`): same validation, same versioning, same owner and review
date, same audit entry. A document imported here is indistinguishable from one
imported by hand — which is the point.

Each file is independent: an unreadable PDF is reported and the next one proceeds.
The report at the end is what an operator keeps.

`--dry-run` lists what would be imported, and why the rest would be skipped, without
reading a single file into the model — worth running first on an unfamiliar folder.
"""

import argparse
import asyncio
import mimetypes
import sys
import time
from pathlib import Path

from sqlalchemy import select

from .access import DOCUMENT_DEPARTMENTS
from .database import SessionLocal, User, initialise_database
from .ingestion import (
    DEFAULT_ALLOWED_ROLES,
    DocumentMetadata,
    IngestionRejected,
    ingest,
    parse_date,
    title_from_filename,
)
from .rag import SUPPORTED_EXTENSIONS


def title_from(path: Path) -> str:
    return title_from_filename(path.name)


def find_account(db, identifier: str) -> User | None:
    if "@" in identifier:
        return db.scalar(select(User).where(User.email == identifier.strip().lower()))
    return db.scalar(select(User).where(User.username == identifier.strip()))


async def run(arguments: argparse.Namespace) -> int:
    root = Path(arguments.folder)
    if not root.is_dir():
        print(f"Dossier introuvable : {root}")
        return 2

    pattern = "**/*" if arguments.recursive else "*"
    candidates = sorted(path for path in root.glob(pattern) if path.is_file())
    eligible = [path for path in candidates if path.suffix.lower() in SUPPORTED_EXTENSIONS]
    skipped = [path for path in candidates if path.suffix.lower() not in SUPPORTED_EXTENSIONS]

    print(f"{len(eligible)} fichier(s) à importer, {len(skipped)} ignoré(s) (format non pris en charge).")
    for path in skipped:
        print(f"  ignoré   {path.relative_to(root)}")
    if arguments.dry_run:
        for path in eligible:
            print(f"  prévu    {path.relative_to(root)}  →  « {title_from(path)} »")
        print("Simulation : aucun fichier n'a été lu ni indexé.")
        return 0

    initialise_database()
    with SessionLocal() as db:
        actor = find_account(db, arguments.actor)
        if actor is None or actor.role not in {"admin", "document_manager"}:
            print("Le compte indiqué par --as doit exister et avoir le rôle admin ou document_manager.")
            return 2
        owner_id = None
        if arguments.owner:
            owner = find_account(db, arguments.owner)
            if owner is None:
                print("Le responsable indiqué par --owner est introuvable.")
                return 2
            owner_id = owner.id

        try:
            valid_until = parse_date(arguments.valid_until or "", "Date de validité")
            review_due = parse_date(arguments.review_due or "", "Date de révision")
        except IngestionRejected as exc:
            print(exc.detail)
            return 2

        imported = failed = 0
        started = time.monotonic()
        for index, path in enumerate(eligible, start=1):
            metadata = DocumentMetadata(
                title=title_from(path),
                classification=arguments.classification,
                allowed_roles=arguments.roles,
                department=arguments.service,
                valid_until=valid_until,
                review_due=review_due,
                owner_id=owner_id,
            )
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            label = f"[{index}/{len(eligible)}] {path.relative_to(root)}"
            try:
                document, chunks = await ingest(db, actor, path.name, path.read_bytes(), metadata, content_type)
                imported += 1
                version = f", version {document.version}" if document.version > 1 else ""
                print(f"  importé  {label} — {chunks} extrait(s){version}")
            except IngestionRejected as exc:
                failed += 1
                print(f"  ÉCHEC    {label} — {exc.detail}")
                if exc.status_code == 503:
                    # The embedding model is down: every following file would fail the
                    # same way. Stop, rather than print the same error a hundred times.
                    print("Le modèle d'embeddings est indisponible : import interrompu.")
                    break

    elapsed = time.monotonic() - started
    print(f"\n{imported} importé(s), {failed} échec(s), en {elapsed:.0f} s.")
    return 0 if failed == 0 else 1


def main() -> None:
    # A Windows console encodes in cp1252, which has no "→": a report line would
    # raise mid-import and stop the loop with half a folder indexed. The report must
    # never be the thing that fails.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Importe un dossier de documents dans l'assistant.")
    parser.add_argument("folder", help="Dossier contenant les documents")
    parser.add_argument("--service", required=True, choices=sorted(DOCUMENT_DEPARTMENTS),
                        help="Service propriétaire des documents (ou transverse)")
    parser.add_argument("--as", dest="actor", required=True,
                        help="Adresse ou identifiant du compte qui importe (admin ou document_manager)")
    parser.add_argument("--owner", help="Responsable des documents, si différent du compte qui importe")
    parser.add_argument("--roles", default=DEFAULT_ALLOWED_ROLES,
                        help=f"Rôles autorisés, séparés par des virgules (défaut : {DEFAULT_ALLOWED_ROLES})")
    parser.add_argument("--classification", default="interne")
    parser.add_argument("--valid-until", help="Date de validité commune, AAAA-MM-JJ")
    parser.add_argument("--review-due", help="Date de révision commune, AAAA-MM-JJ (sinon : défaut configuré)")
    parser.add_argument("--recursive", action="store_true", help="Inclure les sous-dossiers")
    parser.add_argument("--dry-run", action="store_true", help="Lister sans importer")
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
