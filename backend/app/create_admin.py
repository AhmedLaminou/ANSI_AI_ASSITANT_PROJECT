r"""Create the first administrator, from the machine hosting the database.

    .\.venv\Scripts\python.exe -m app.create_admin

The professional address is the sign-in identifier; a readable username is derived
from it for the audit trail. An administrator reads every department (see
access.sees_every_department), so the department asked for here is the one they
belong to, not the one they can read.
"""

import re
from getpass import getpass

from sqlalchemy import select

from .access import DEPARTMENTS
from .auth import password_hash
from .database import SessionLocal, User, initialise_database

EMAIL_PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def main() -> None:
    initialise_database()
    email = input("Adresse professionnelle de l'administrateur : ").strip().lower()
    if not EMAIL_PATTERN.match(email):
        raise SystemExit("Adresse invalide — exemple : prenom.nom@ansi.ne")

    services = sorted(DEPARTMENTS)
    department = input(f"Service de rattachement ({', '.join(services)}) : ").strip().lower()
    if department not in DEPARTMENTS:
        raise SystemExit(f"Service inconnu. Valeurs acceptées : {', '.join(services)}.")

    password = getpass("Mot de passe : ")
    confirmation = getpass("Confirmer le mot de passe : ")
    if len(password) < 12:
        raise SystemExit("Utilisez au moins 12 caractères.")
    if password != confirmation:
        raise SystemExit("Les mots de passe ne correspondent pas.")

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email)):
            raise SystemExit("Un compte utilise déjà cette adresse.")
        base = re.sub(r"[^A-Za-z0-9_.-]", "-", email.split("@", 1)[0]).strip("-.") or "admin"
        username, suffix = base[:56], 2
        while db.scalar(select(User).where(User.username == username)):
            username = f"{base[:56]}-{suffix}"
            suffix += 1
        db.add(User(
            username=username,
            email=email,
            password_hash=password_hash.hash(password),
            role="admin",
            department=department,
            status="active",
        ))
        db.commit()
    print(f"Administrateur créé. Connexion avec : {email}")


if __name__ == "__main__":
    main()
