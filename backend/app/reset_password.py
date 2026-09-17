r"""Reset the password of an existing account, from the machine hosting the database.

    .\.venv\Scripts\python.exe -m app.reset_password

There is deliberately no "forgot my password" endpoint: the assistant runs offline,
so there is no mail relay to send a reset link through, and an unauthenticated reset
endpoint would be a second way in. Recovery is therefore an operator action, performed
with access to the server — which is the right requirement for an internal tool.

The password is read with getpass: it is never passed as an argument, so it does not
land in the shell history nor in the process list.
"""

from getpass import getpass

from sqlalchemy import select

from .access import department_label
from .auth import password_hash
from .database import SessionLocal, User, initialise_database

MINIMUM_LENGTH = 12


def main() -> None:
    initialise_database()

    username = input("Nom d'utilisateur : ").strip()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            # Naming the account is fine here: the operator already has the database.
            raise SystemExit("Aucun compte ne porte cet identifiant.")

        print(f"Compte trouvé : rôle {user.role}, service {department_label(user.department)}, "
              f"état {user.status}.")
        if user.status != "active":
            print("Attention : ce compte n'est pas actif, il ne pourra pas se connecter "
                  "tant que son état n'aura pas été remis à « active ».")

        password = getpass("Nouveau mot de passe : ")
        if len(password) < MINIMUM_LENGTH:
            raise SystemExit(f"Utilisez au moins {MINIMUM_LENGTH} caractères.")
        if getpass("Confirmer le mot de passe : ") != password:
            raise SystemExit("Les mots de passe ne correspondent pas.")

        user.password_hash = password_hash.hash(password)
        db.commit()

    print("Mot de passe remplacé.")


if __name__ == "__main__":
    main()
