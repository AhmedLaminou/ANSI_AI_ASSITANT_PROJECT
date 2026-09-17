r"""Create the first administrator, from the machine hosting the database.

    .\.venv\Scripts\python.exe -m app.create_admin

An administrator reads every department (see access.sees_every_department), so the
department asked for here is the one they belong to, not the one they can read.
"""

from getpass import getpass

from sqlalchemy import select

from .access import DEPARTMENTS
from .auth import password_hash
from .database import SessionLocal, User, initialise_database


def main() -> None:
    initialise_database()
    username = input("Nom d'utilisateur administrateur : ").strip()
    if len(username) < 3:
        raise SystemExit("Le nom d'utilisateur doit contenir au moins 3 caractères.")

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
        if db.scalar(select(User).where(User.username == username)):
            raise SystemExit("Cet utilisateur existe déjà.")
        db.add(User(
            username=username,
            password_hash=password_hash.hash(password),
            role="admin",
            department=department,
            status="active",
        ))
        db.commit()
    print("Administrateur créé.")


if __name__ == "__main__":
    main()
