from getpass import getpass

from sqlalchemy import select

from .auth import password_hash
from .database import Base, SessionLocal, User, engine


def main() -> None:
    Base.metadata.create_all(bind=engine)
    username = input("Nom d'utilisateur administrateur : ").strip()
    if len(username) < 3:
        raise SystemExit("Le nom d'utilisateur doit contenir au moins 3 caractères.")

    password = getpass("Mot de passe : ")
    confirmation = getpass("Confirmer le mot de passe : ")
    if len(password) < 12:
        raise SystemExit("Utilisez au moins 12 caractères.")
    if password != confirmation:
        raise SystemExit("Les mots de passe ne correspondent pas.")

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == username)):
            raise SystemExit("Cet utilisateur existe déjà.")
        db.add(User(username=username, password_hash=password_hash.hash(password), role="admin"))
        db.commit()
    print("Administrateur créé.")


if __name__ == "__main__":
    main()
